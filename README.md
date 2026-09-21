# announcer-sdk

Python SDK for [Announcer](https://misralo.com) — send transactional email from
your own domain, DKIM-signed.

Sync and async clients, full type hints, one dependency (`httpx`).

```bash
pip install announcer-sdk
```

## Send an email

```python
from announcer import Announcer

announcer = Announcer()  # reads ANNOUNCER_API_KEY

announcer.send(
    from_="Acme <billing@acme.com>",
    to="customer@example.com",
    subject="Your receipt",
    text="Thanks for your order.",
    html="<p>Thanks for your order.</p>",
)
```

That's the whole integration. `from_` has a trailing underscore because `from`
is a Python keyword; if you already have a dict, splat it and use the plain
spelling:

```python
announcer.send(**{"from": "billing@acme.com", "to": "customer@example.com", "text": "Hi"})
```

`to`, `cc` and `bcc` each take one address or a list, and `reply_to` sets the
reply address — see [Several recipients](#several-recipients).

### Async

Same surface, awaited:

```python
from announcer import AsyncAnnouncer

async with AsyncAnnouncer() as announcer:
    await announcer.send(
        from_="billing@acme.com",
        to="customer@example.com",
        subject="Your receipt",
        text="Thanks!",
    )
```

## Before your first send

You need a registered, verified sending domain — Announcer will not let you send
`From:` a domain you have not proved you control.

```python
domain = announcer.domains.create("acme.com")

for record in domain.dns:
    print(record.type, record.name, record.value)
# TXT mail._domainkey.acme.com v=DKIM1; k=rsa; p=MIIBIjANBg...

# Publish that record, wait for DNS, then:
announcer.domains.verify(domain.id)
```

One TXT record is the entire ask. SPF and MX stay on Announcer's own bounce
domain, so your root domain's DNS is untouched.

## What the SDK does for you

**Retries are safe.** Every send carries an `Idempotency-Key`, generated per call
when you do not supply one. A timeout, a 500, or a 429 gets retried with
exponential backoff and jitter — and because the key travels with the retry, the
API recognises it as the same operation instead of sending twice.

Supply your own key to extend that guarantee across process restarts:

```python
announcer.send(
    from_="billing@acme.com",
    to="customer@example.com",
    subject="Your receipt",
    text="Thanks!",
    idempotency_key=f"receipt-{order.id}",  # this order mails exactly once, ever
)
```

A replay tells you so rather than pretending it sent again:

```python
result = announcer.send(..., idempotency_key="receipt-4711")
if result.idempotent_replay:
    ...  # already sent earlier; nothing went out a second time
```

**Errors are typed.** Catch the case you can actually handle:

```python
from announcer import SuppressedRecipientError, RateLimitError, PermissionDeniedError

try:
    announcer.send(from_=sender, to=recipient, subject=subject, text=body)
except SuppressedRecipientError:
    # They hard-bounced or complained before. Don't retry; mark them inactive.
    deactivate(recipient)
except RateLimitError as exc:
    print(f"Slow down for {exc.retry_after}s")
except PermissionDeniedError:
    # Domain not registered, not verified, or this key is send-scoped.
    ...
```

The full set: `ValidationError` (with a per-field `.errors` dict),
`AuthenticationError`, `PermissionDeniedError`, `NotFoundError`,
`ConflictError`, `UnprocessableEntityError`, `SuppressedRecipientError`,
`RateLimitError`, `InternalServerError`, `APIConnectionError`,
`APITimeoutError`. All subclass `AnnouncerError`.

**Results are dataclasses, not dicts.** Timestamps arrive as `datetime`
objects, `date` fields as `date`. The API mixes `camelCase` and `snake_case`
depending on the endpoint; the SDK normalises everything to Python's
convention and derives the booleans you actually want (`domain.verified`,
`key.revoked`, `endpoint.disabled`). Free-form `payload` and `detail` dicts
pass through untouched — those keys are your data.

## Several recipients

`to`, `cc` and `bcc` each take one address or a list. Everything in `to` and
`cc` is **one email** whose recipients see each other; `bcc` recipients see
nobody, not even each other:

```python
announcer.send(
    from_="billing@acme.com",
    to=["customer@example.com", "partner@example.com"],
    cc="accounting@acme.com",
    bcc="archive@acme.com",
    reply_to="support@acme.com",
    subject="Your receipt",
    text="Thanks!",
)
```

At most 50 addresses across the three. `reply_to` is a header only — it costs
nothing and cannot bounce.

**Recipients are the billable unit.** That call counts four against your quota,
not one. It is also what keeps `monthly_hard_cap` meaningful: otherwise a leaked
key could send fifty times your ceiling by padding the list.

### One email, or many?

For anything list-shaped — a newsletter, a digest, a fan-out — you want
`send_many`, not a list:

```python
results = announcer.emails.send_many(
    ["a@example.com", "b@example.com", "c@example.com"],
    from_="news@acme.com",
    subject="September update",
    html=body,
)

for r in results:
    if not r.ok:
        print(f"{r.to} failed: {r.error}")
```

|  | `send(to=[a, b])` | `send_many([a, b], …)` |
|---|---|---|
| Emails sent | one | two |
| Do they see each other? | yes, in `To:` | no |
| API requests | one | two |
| Idempotency key | one | one each, derived |
| One address fails | the send reports it | the others are unaffected |

### Suppressed recipients

A recipient on your suppression list is dropped and the rest still goes out:

```python
result = announcer.send(
    from_="billing@acme.com",
    to=["good@example.com", "bounced-before@example.com"],
    subject="Your receipt",
    text="Thanks!",
)

result.recipients  # 1 — what actually went out and what you were billed
result.suppressed  # ['bounced-before@example.com']
```

`SuppressedRecipientError` is raised only when *every* recipient is suppressed
(or every `to` recipient — a message with no visible primary recipient is
refused rather than sent). Its `.suppressed` list names them all.

## Webhooks

Register an endpoint, store the secret, verify every delivery:

```python
endpoint = announcer.webhooks.create("https://acme.com/hooks/announcer")
print(endpoint.secret)  # whsec_... — shown once, store it now
```

Flask:

```python
import os
from flask import Flask, request
from announcer import verify_webhook, SignatureVerificationError

app = Flask(__name__)

@app.post("/hooks/announcer")
def announcer_webhook():
    try:
        event = verify_webhook(
            request.get_data(),  # raw bytes, NOT request.get_json()
            request.headers.get("X-Announcer-Signature"),
            os.environ["ANNOUNCER_WEBHOOK_SECRET"],
        )
    except SignatureVerificationError:
        return "", 400

    if event.event == "delivered":
        mark_delivered(event.message.id)
    elif event.event in ("bounced", "complained"):
        deactivate(event.message.to)

    return "", 200
```

Django is the same with `request.body`.

Verification checks the HMAC **and** the timestamp, rejecting anything more than
five minutes old so a captured delivery cannot be replayed at you. Tune it with
`tolerance=`.

Events: `sent`, `delivered`, `bounced`, `complained`, `suppressed`.

## API reference

### Client

```python
Announcer(api_key=None, *, base_url=None, timeout=30.0, max_retries=2,
          user_agent=None, headers=None, http_client=None)
AsyncAnnouncer(...)  # same arguments
```

| Argument      | Default                                                          |
|---------------|------------------------------------------------------------------|
| `api_key`     | `ANNOUNCER_API_KEY`                                              |
| `base_url`    | `ANNOUNCER_BASE_URL`, then `https://mail.misralo.com`            |
| `timeout`     | `30.0` seconds, per attempt                                      |
| `max_retries` | `2` extra attempts after a failure                               |
| `user_agent`  | appended to the SDK's own — name your app                        |
| `headers`     | added to every request                                           |
| `http_client` | bring your own `httpx.Client` / `httpx.AsyncClient`              |

### Methods

| Call | Does |
|------|------|
| `announcer.send(**msg)` | Shorthand for `emails.send`. |
| `announcer.usage()` | Quota consumption plus a 14-day sending series. |
| `emails.send(**msg)` | Sends one email. `to`/`cc`/`bcc` take one address or many. |
| `emails.send_many(recipients, **msg)` | Separate emails, one per recipient. |
| `emails.list(limit=, status=, search=)` | Send history. |
| `emails.events(message_id)` | A message's audit trail. |
| `domains.create(domain)` | Registers a domain, returns the DNS record. |
| `domains.list()` | Every domain on the account. |
| `domains.dns(id)` | The records again, for a domain you already registered. |
| `domains.verify(id)` | Resolves DNS and checks the published key. |
| `domains.delete(id)` | Removes the domain and its signing key. |
| `api_keys.create(name, scope="full")` | Issues a key. Secret shown once. |
| `api_keys.list()` | Every key, without secrets. |
| `api_keys.revoke(id)` | Revokes a key; history survives. |
| `webhooks.create(url)` | Registers an endpoint. Max 2 active. |
| `webhooks.list()` | Every endpoint. |
| `webhooks.delete(id)` | Disables an endpoint. |
| `webhooks.verify(body, header, secret)` | Verifies a delivery. |
| `suppressions.list(limit=)` | Addresses that bounced or complained. |

`domains.*`, `api_keys.*` and `webhooks.*` need a `full`-scoped key. Everything
else works with a `send` key too — give integrations `send`.

## Scopes

Mint a `send`-scoped key for anything that only sends mail:

```python
key = announcer.api_keys.create("production-worker", "send")
```

A leaked send key cannot register domains, mint successor keys, or touch
billing. It is the difference between an incident and a catastrophe.

## Contributing

```bash
pip install -e ".[dev]"
pytest        # no network; everything runs against httpx.MockTransport
mypy
```

## License

MIT

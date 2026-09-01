"""Resource namespaces. Sync and async versions sit side by side so the two
clients stay literally identical in shape -- if a method exists on one, the
same-named method exists on the other."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ._http import AsyncTransport, SyncTransport
from .errors import AnnouncerError
from .types import (
    Addresses,
    ApiKey,
    BatchSendResult,
    CreatedApiKey,
    CreatedDomain,
    CreatedWebhookEndpoint,
    Domain,
    DomainDns,
    DomainVerification,
    Message,
    MessageEvent,
    SentEmail,
    Suppression,
    WebhookEndpoint,
    WebhookEvent,
)
from .webhooks import DEFAULT_TOLERANCE_SECONDS, verify_webhook


def _normalise(value: Addresses) -> Any:
    """A single address or a list of them, as the API wants it."""
    if isinstance(value, (list, tuple, set)):
        return [str(address) for address in value]
    return str(value)


def _first(value: Addresses) -> str:
    """The first address of a list, for error reporting."""
    if isinstance(value, (list, tuple, set)):
        return next((str(a) for a in value), "")
    return str(value)


def _build_send(
    from_: Optional[str],
    to: Optional[Addresses],
    cc: Optional[Addresses],
    bcc: Optional[Addresses],
    reply_to: Optional[Addresses],
    subject: Optional[str],
    text: Optional[str],
    html: Optional[str],
    idempotency_key: Optional[str],
    extra: Mapping[str, Any],
) -> Tuple[Dict[str, Any], str, str]:
    """Validate a send and return ``(body, idempotency_key, first_recipient)``.

    Shared by the sync and async paths so the two can never drift on what
    counts as a valid message.
    """
    unknown = dict(extra)
    # `from` is a Python keyword, so the kwarg is `from_`. Accept the raw
    # spelling too, for callers who splat a dict they got from elsewhere.
    sender = from_ if from_ is not None else unknown.pop("from", None)
    if reply_to is None:
        reply_to = unknown.pop("replyTo", None)
    if unknown:
        raise TypeError(
            f"Unexpected argument(s) to send(): {', '.join(sorted(unknown))}. "
            "Announcer accepts from_, to, cc, bcc, reply_to, subject, text, html, "
            "idempotency_key."
        )

    if not sender:
        raise TypeError("send() needs a `from_` address (or 'from' if you are splatting a dict).")
    if to is None:
        raise TypeError("send() needs a `to` address.")
    if isinstance(to, (list, tuple, set)) and not to:
        raise TypeError("send() needs at least one `to` recipient.")
    if not text and not html:
        raise TypeError("Provide `text`, `html`, or both -- an email needs a body.")

    body: Dict[str, Any] = {"from": sender, "to": _normalise(to)}
    if cc is not None:
        body["cc"] = _normalise(cc)
    if bcc is not None:
        body["bcc"] = _normalise(bcc)
    if reply_to is not None:
        # The API accepts `replyTo` too, but snake_case is its documented shape.
        body["reply_to"] = _normalise(reply_to)
    if subject is not None:
        body["subject"] = subject
    if text is not None:
        body["text"] = text
    if html is not None:
        body["html"] = html

    # Generated when absent so the SDK's own retries can never double-send.
    return body, idempotency_key or str(uuid.uuid4()), _first(to)


class Emails:
    """Sending mail and reading send history. Works with either key scope."""

    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def send(
        self,
        *,
        from_: Optional[str] = None,
        to: Optional[Addresses] = None,
        cc: Optional[Addresses] = None,
        bcc: Optional[Addresses] = None,
        reply_to: Optional[Addresses] = None,
        subject: Optional[str] = None,
        text: Optional[str] = None,
        html: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        **extra: Any,
    ) -> SentEmail:
        """Send one email.

        ``to``, ``cc`` and ``bcc`` each take one address or a list. Everything
        in ``to`` and ``cc`` is one email whose recipients see each other;
        ``bcc`` recipients see nobody. At most 50 addresses across the three.

        ::

            announcer.send(
                from_="Acme <billing@acme.com>",
                to=["customer@example.com", "partner@example.com"],
                cc="accounting@acme.com",
                reply_to="support@acme.com",
                subject="Your receipt",
                text="Thanks!",
            )

        An ``Idempotency-Key`` is generated when you do not supply one, so the
        SDK's automatic retries can never send twice. Supply your own -- an
        order id, a job id -- to extend that guarantee across process restarts.

        A recipient on your suppression list is dropped and reported in
        :attr:`SentEmail.suppressed`; the rest of the message still goes out.
        Only when every recipient is suppressed does this raise.

        :raises PermissionDeniedError: the ``from_`` domain is not registered, or
            not verified.
        :raises SuppressedRecipientError: every recipient bounced or complained
            before.
        :raises RateLimitError: a per-second limit or a daily/monthly quota.
            Quota counts recipients, so one call can consume several.
        """
        body, key, recipient = _build_send(
            from_, to, cc, bcc, reply_to, subject, text, html, idempotency_key, extra
        )
        data = self._t.request(
            "POST",
            "/v1/emails",
            json_body=body,
            headers={"Idempotency-Key": key},
            # Carrying a key makes a 409 mean "the original is still in flight",
            # so waiting and asking again is right.
            retry_on_409=True,
            recipient=recipient,
        )
        return SentEmail.from_api(data)

    def send_many(
        self,
        recipients: Sequence[str],
        *,
        from_: Optional[str] = None,
        cc: Optional[Addresses] = None,
        bcc: Optional[Addresses] = None,
        reply_to: Optional[Addresses] = None,
        subject: Optional[str] = None,
        text: Optional[str] = None,
        html: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        stop_on_error: bool = False,
        **extra: Any,
    ) -> List[BatchSendResult]:
        """Send the same message to several recipients as **separate emails**.

        One API call each, a result per recipient, and one failure does not stop
        the rest unless ``stop_on_error`` is set.

        Not the same as passing a list to :meth:`send`:

        * ``send(to=[a, b])`` is one email. A and B see each other in the
          ``To:`` header and it costs one request.
        * ``send_many([a, b], ...)`` is two emails. Neither knows the other
          exists, and each gets its own idempotency key and its own bounce.

        Use this for anything list-shaped -- a newsletter, a digest, a fan-out.

        A ``cc`` here is copied on *every* message, so a three-recipient batch
        sends the cc three copies. That is usually not what you want.
        """
        results: List[BatchSendResult] = []
        for index, recipient in enumerate(recipients):
            # Derived rather than shared: one key across the batch would make
            # every recipient after the first an idempotent replay of the first,
            # and only one person would get the mail.
            key = f"{idempotency_key}-{index}" if idempotency_key else None
            try:
                sent = self.send(
                    from_=from_,
                    to=recipient,
                    cc=cc,
                    bcc=bcc,
                    reply_to=reply_to,
                    subject=subject,
                    text=text,
                    html=html,
                    idempotency_key=key,
                    **extra,
                )
                results.append(BatchSendResult(to=recipient, ok=True, result=sent))
            except AnnouncerError as exc:
                results.append(BatchSendResult(to=recipient, ok=False, error=exc))
                if stop_on_error:
                    break
        return results

    def list(
        self,
        *,
        limit: Optional[int] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Message]:
        """Send history, newest first. ``search`` matches a substring of the recipient."""
        data = self._t.request(
            "GET",
            "/v1/messages",
            params={"limit": limit, "status": status, "search": search},
        )
        return [Message.from_api(row) for row in data or []]

    def events(self, message_id: str) -> List[MessageEvent]:
        """The audit trail for one message: every status transition, oldest first."""
        data = self._t.request("GET", f"/v1/messages/{message_id}/events")
        return [MessageEvent.from_api(row) for row in data or []]


class AsyncEmails:
    """Async twin of :class:`Emails`."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def send(
        self,
        *,
        from_: Optional[str] = None,
        to: Optional[Addresses] = None,
        cc: Optional[Addresses] = None,
        bcc: Optional[Addresses] = None,
        reply_to: Optional[Addresses] = None,
        subject: Optional[str] = None,
        text: Optional[str] = None,
        html: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        **extra: Any,
    ) -> SentEmail:
        """Send one email. See :meth:`Emails.send`."""
        body, key, recipient = _build_send(
            from_, to, cc, bcc, reply_to, subject, text, html, idempotency_key, extra
        )
        data = await self._t.request(
            "POST",
            "/v1/emails",
            json_body=body,
            headers={"Idempotency-Key": key},
            retry_on_409=True,
            recipient=recipient,
        )
        return SentEmail.from_api(data)

    async def send_many(
        self,
        recipients: Sequence[str],
        *,
        from_: Optional[str] = None,
        cc: Optional[Addresses] = None,
        bcc: Optional[Addresses] = None,
        reply_to: Optional[Addresses] = None,
        subject: Optional[str] = None,
        text: Optional[str] = None,
        html: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        stop_on_error: bool = False,
        **extra: Any,
    ) -> List[BatchSendResult]:
        """Send to several recipients. See :meth:`Emails.send_many`."""
        results: List[BatchSendResult] = []
        for index, recipient in enumerate(recipients):
            key = f"{idempotency_key}-{index}" if idempotency_key else None
            try:
                sent = await self.send(
                    from_=from_,
                    to=recipient,
                    cc=cc,
                    bcc=bcc,
                    reply_to=reply_to,
                    subject=subject,
                    text=text,
                    html=html,
                    idempotency_key=key,
                    **extra,
                )
                results.append(BatchSendResult(to=recipient, ok=True, result=sent))
            except AnnouncerError as exc:
                results.append(BatchSendResult(to=recipient, ok=False, error=exc))
                if stop_on_error:
                    break
        return results

    async def list(
        self,
        *,
        limit: Optional[int] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Message]:
        """Send history, newest first."""
        data = await self._t.request(
            "GET",
            "/v1/messages",
            params={"limit": limit, "status": status, "search": search},
        )
        return [Message.from_api(row) for row in data or []]

    async def events(self, message_id: str) -> List[MessageEvent]:
        """The audit trail for one message."""
        data = await self._t.request("GET", f"/v1/messages/{message_id}/events")
        return [MessageEvent.from_api(row) for row in data or []]


class Domains:
    """Registering and verifying sending domains. Needs a ``full``-scoped key."""

    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def create(self, domain: str) -> CreatedDomain:
        """Register a sending domain and get back the DNS record to publish.

        Registration generates an RSA keypair, so it is rate-limited hard --
        roughly one a minute. Publish every record in ``dns``, then call
        :meth:`verify`.
        """
        data = self._t.request("POST", "/v1/domains", json_body={"domain": domain})
        return CreatedDomain.from_api(data)

    def list(self) -> List[Domain]:
        """Every domain on the account, newest first."""
        data = self._t.request("GET", "/v1/domains")
        return [Domain.from_api(row) for row in data or []]

    def dns(self, domain_id: str) -> DomainDns:
        """The records for a domain, re-derived from the stored public key."""
        data = self._t.request("GET", f"/v1/domains/{domain_id}/dns")
        return DomainDns.from_api(data)

    def verify(self, domain_id: str) -> DomainVerification:
        """Resolve the DKIM record and compare it to the key we issued.

        A real DNS lookup, not a self-report. DNS propagation takes minutes to
        hours -- retry rather than re-registering.

        :raises UnprocessableEntityError: the record is missing or does not match.
        """
        data = self._t.request("POST", f"/v1/domains/{domain_id}/verify")
        return DomainVerification.from_api(data)

    def delete(self, domain_id: str) -> None:
        """Remove the domain and its signing key. Send history survives."""
        self._t.request("DELETE", f"/v1/domains/{domain_id}")


class AsyncDomains:
    """Async twin of :class:`Domains`."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def create(self, domain: str) -> CreatedDomain:
        """Register a sending domain. See :meth:`Domains.create`."""
        data = await self._t.request("POST", "/v1/domains", json_body={"domain": domain})
        return CreatedDomain.from_api(data)

    async def list(self) -> List[Domain]:
        """Every domain on the account."""
        data = await self._t.request("GET", "/v1/domains")
        return [Domain.from_api(row) for row in data or []]

    async def dns(self, domain_id: str) -> DomainDns:
        """The records for a domain."""
        data = await self._t.request("GET", f"/v1/domains/{domain_id}/dns")
        return DomainDns.from_api(data)

    async def verify(self, domain_id: str) -> DomainVerification:
        """Resolve and check the published DKIM record."""
        data = await self._t.request("POST", f"/v1/domains/{domain_id}/verify")
        return DomainVerification.from_api(data)

    async def delete(self, domain_id: str) -> None:
        """Remove the domain and its signing key."""
        await self._t.request("DELETE", f"/v1/domains/{domain_id}")


class ApiKeys:
    """Issuing and revoking API keys. Needs a ``full``-scoped key."""

    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def create(self, name: str, scope: str = "full") -> CreatedApiKey:
        """Issue a key. The secret is in the response and nowhere else.

        Prefer ``"send"`` for anything that only sends mail: a leaked send key
        cannot register domains, mint more keys, or reach billing.
        """
        data = self._t.request("POST", "/v1/keys", json_body={"name": name, "scope": scope})
        return CreatedApiKey.from_api(data)

    def list(self) -> List[ApiKey]:
        """Every key on the account. Secrets are never included."""
        data = self._t.request("GET", "/v1/keys")
        return [ApiKey.from_api(row) for row in data or []]

    def revoke(self, key_id: str) -> None:
        """Revoke a key. The row stays, so ``last_used_at`` remains auditable."""
        self._t.request("DELETE", f"/v1/keys/{key_id}")


class AsyncApiKeys:
    """Async twin of :class:`ApiKeys`."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def create(self, name: str, scope: str = "full") -> CreatedApiKey:
        """Issue a key. See :meth:`ApiKeys.create`."""
        data = await self._t.request("POST", "/v1/keys", json_body={"name": name, "scope": scope})
        return CreatedApiKey.from_api(data)

    async def list(self) -> List[ApiKey]:
        """Every key on the account."""
        data = await self._t.request("GET", "/v1/keys")
        return [ApiKey.from_api(row) for row in data or []]

    async def revoke(self, key_id: str) -> None:
        """Revoke a key."""
        await self._t.request("DELETE", f"/v1/keys/{key_id}")


class Webhooks:
    """Webhook endpoints, and verifying their deliveries."""

    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def create(self, url: str) -> CreatedWebhookEndpoint:
        """Register an endpoint. Maximum two active per account.

        The returned ``secret`` crosses the wire exactly once -- store it now
        and pass it to :meth:`verify` on every delivery.
        """
        data = self._t.request("POST", "/v1/webhooks", json_body={"url": url})
        return CreatedWebhookEndpoint.from_api(data)

    def list(self) -> List[WebhookEndpoint]:
        """Every endpoint on the account, including disabled ones."""
        data = self._t.request("GET", "/v1/webhooks")
        return [WebhookEndpoint.from_api(row) for row in data or []]

    def delete(self, endpoint_id: str) -> None:
        """Disable an endpoint. Pending deliveries stop; history stays."""
        self._t.request("DELETE", f"/v1/webhooks/{endpoint_id}")

    @staticmethod
    def verify(
        payload: Any,
        signature_header: Optional[str],
        secret: str,
        *,
        tolerance: int = DEFAULT_TOLERANCE_SECONDS,
        now: Optional[float] = None,
    ) -> WebhookEvent:
        """Verify a delivery and return the parsed event.

        Pass the raw request body, not a re-serialised object. See
        :func:`announcer.verify_webhook`.
        """
        return verify_webhook(payload, signature_header, secret, tolerance=tolerance, now=now)


class AsyncWebhooks:
    """Async twin of :class:`Webhooks`."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def create(self, url: str) -> CreatedWebhookEndpoint:
        """Register an endpoint. See :meth:`Webhooks.create`."""
        data = await self._t.request("POST", "/v1/webhooks", json_body={"url": url})
        return CreatedWebhookEndpoint.from_api(data)

    async def list(self) -> List[WebhookEndpoint]:
        """Every endpoint on the account."""
        data = await self._t.request("GET", "/v1/webhooks")
        return [WebhookEndpoint.from_api(row) for row in data or []]

    async def delete(self, endpoint_id: str) -> None:
        """Disable an endpoint."""
        await self._t.request("DELETE", f"/v1/webhooks/{endpoint_id}")

    #: Verification is pure computation -- no await needed, same function.
    verify = staticmethod(verify_webhook)


class Suppressions:
    """Addresses that hard-bounced or complained."""

    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def list(self, *, limit: Optional[int] = None) -> List[Suppression]:
        """Addresses Announcer refuses to send to, newest first."""
        data = self._t.request("GET", "/v1/suppressions", params={"limit": limit})
        return [Suppression.from_api(row) for row in data or []]


class AsyncSuppressions:
    """Async twin of :class:`Suppressions`."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def list(self, *, limit: Optional[int] = None) -> List[Suppression]:
        """Addresses Announcer refuses to send to."""
        data = await self._t.request("GET", "/v1/suppressions", params={"limit": limit})
        return [Suppression.from_api(row) for row in data or []]

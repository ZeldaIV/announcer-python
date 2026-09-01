from __future__ import annotations

import time

import httpx
import pytest

from announcer import (
    Announcer,
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
    ValidationError,
)
from conftest import make_client


# -- error mapping --------------------------------------------------------


def test_401_becomes_authentication_error():
    client, _ = make_client([{"status": 401, "json": {"status": 401, "detail": "Invalid API key."}}])

    with pytest.raises(AuthenticationError, match="Invalid API key."):
        client.usage()


def test_403_keeps_the_detail():
    client, _ = make_client(
        [
            {
                "status": 403,
                "json": {
                    "status": 403,
                    "detail": "Your account is not authorized to send from acme.test. "
                    "Register the domain first.",
                },
            }
        ]
    )

    with pytest.raises(PermissionDeniedError, match="Register the domain first"):
        client.send(from_="x@acme.test", to="y@example.com", text="hi")


def test_400_exposes_the_per_field_messages():
    client, _ = make_client(
        [
            {
                "status": 400,
                "json": {
                    "title": "One or more validation errors occurred.",
                    "status": 400,
                    "errors": {"from": ["Not a valid address."]},
                },
            }
        ]
    )

    with pytest.raises(ValidationError) as excinfo:
        client.send(from_="nonsense", to="y@example.com", text="hi")

    assert excinfo.value.errors == {"from": ["Not a valid address."]}
    assert str(excinfo.value) == "from: Not a valid address."


def test_bare_error_key_shape_is_understood():
    # Two 409 paths answer {"error": "..."} rather than problem+json.
    client, _ = make_client(
        [{"status": 409, "json": {"error": "Domain acme.test is already registered."}}]
    )

    with pytest.raises(ConflictError, match="already registered"):
        client.domains.create("acme.test")


def test_404_with_no_body_at_all():
    # Several handlers answer NOT_FOUND with nothing in the body.
    client, _ = make_client([{"status": 404, "content": b""}])

    with pytest.raises(NotFoundError, match="Not found."):
        client.domains.delete("missing")


def test_429_carries_retry_after():
    client, _ = make_client(
        [
            {
                "status": 429,
                "headers": {"retry-after": "3600"},
                "json": {"status": 429, "detail": "Daily send limit of 100 messages reached."},
            }
        ]
    )

    with pytest.raises(RateLimitError) as excinfo:
        client.send(from_="x@acme.test", to="y@example.com", text="hi")

    assert excinfo.value.retry_after == 3600


def test_422_outside_the_send_path_is_a_plain_unprocessable():
    client, _ = make_client(
        [{"status": 422, "json": {"status": 422, "detail": "No TXT record found."}}]
    )

    with pytest.raises(UnprocessableEntityError) as excinfo:
        client.domains.verify("d1")

    assert type(excinfo.value) is UnprocessableEntityError


def test_5xx_becomes_internal_server_error():
    client, _ = make_client(
        [{"status": 503, "json": {"status": 503, "detail": "Refusing to send unsigned."}}]
    )

    with pytest.raises(InternalServerError, match="Refusing to send unsigned"):
        client.send(from_="x@acme.test", to="y@example.com", text="hi")


def test_transport_failure_becomes_api_connection_error():
    client, _ = make_client([{"raises": httpx.ConnectError("connection refused")}])

    with pytest.raises(APIConnectionError, match="Could not reach the Announcer API"):
        client.usage()


def test_timeout_becomes_api_timeout_error():
    client, _ = make_client([{"raises": httpx.ReadTimeout("too slow")}])

    with pytest.raises(APITimeoutError):
        client.usage()


def test_request_id_is_surfaced():
    client, _ = make_client(
        [{"status": 500, "headers": {"x-request-id": "req_abc123"}, "json": {"status": 500}}]
    )

    with pytest.raises(InternalServerError) as excinfo:
        client.usage()

    assert excinfo.value.request_id == "req_abc123"


# -- retries --------------------------------------------------------------


def test_retries_a_500_and_returns_the_eventual_success():
    client, rec = make_client(
        [
            {"status": 500, "json": {"status": 500, "detail": "boom"}},
            {"json": {"id": "m", "messageId": None, "status": "sent"}},
        ],
        max_retries=2,
    )

    result = client.send(from_="a@acme.test", to="b@example.com", text="hi")

    assert len(rec.calls) == 2
    assert result.id == "m"


def test_retries_reuse_the_same_idempotency_key():
    client, rec = make_client(
        [
            {"status": 500, "json": {"status": 500}},
            {"json": {"id": "m", "messageId": None, "status": "sent"}},
        ],
        max_retries=2,
    )

    client.send(from_="a@acme.test", to="b@example.com", text="hi")

    # The whole point: the retry must be recognisable to the API as the same
    # operation, or a timeout on the first attempt would send twice.
    assert rec.header("idempotency-key", 0) == rec.header("idempotency-key", 1)


def test_409_on_the_send_path_is_retried():
    client, rec = make_client(
        [
            {"status": 409, "json": {"error": "A request with this Idempotency-Key is already in flight."}},
            {"json": {"id": "m", "messageId": None, "status": "sent", "idempotentReplay": True}},
        ],
        max_retries=2,
    )

    result = client.send(from_="a@acme.test", to="b@example.com", text="hi")

    assert len(rec.calls) == 2
    assert result.idempotent_replay is True


def test_409_gives_up_once_the_budget_is_spent():
    client, _ = make_client(
        [
            {"status": 409, "json": {"error": "already in flight"}},
            {"status": 409, "json": {"error": "already in flight"}},
        ],
        max_retries=1,
    )

    with pytest.raises(ConflictError):
        client.send(from_="a@acme.test", to="b@example.com", text="hi")


def test_409_outside_the_send_path_is_not_retried():
    client, rec = make_client(
        [{"status": 409, "json": {"error": "Domain acme.test is already registered."}}],
        max_retries=2,
    )

    with pytest.raises(ConflictError):
        client.domains.create("acme.test")

    assert len(rec.calls) == 1, "a duplicate domain is a real conflict, not a transient one"


def test_400_is_not_retried():
    client, rec = make_client(
        [{"status": 400, "json": {"status": 400, "errors": {"from": ["Not a valid address."]}}}],
        max_retries=2,
    )

    with pytest.raises(ValidationError):
        client.send(from_="junk", to="b@example.com", text="hi")

    assert len(rec.calls) == 1


def test_retry_after_beats_the_computed_backoff():
    client, rec = make_client(
        [
            {"status": 429, "headers": {"retry-after": "1"}, "json": {"status": 429}},
            {"json": {"sentToday": 1}},
        ],
        max_retries=1,
    )

    started = time.monotonic()
    client.usage()
    elapsed = time.monotonic() - started

    assert len(rec.calls) == 2
    # Slack below 1s for timer coarseness; the point is that it waited roughly
    # the second the server asked for rather than its own ~250ms guess.
    assert elapsed >= 0.9, f"expected to wait ~1s, waited {elapsed:.2f}s"


def test_transport_failures_are_retried():
    client, rec = make_client(
        [
            {"raises": httpx.ConnectError("boom")},
            {"raises": httpx.ConnectError("boom")},
            {"json": {"sentToday": 5}},
        ],
        max_retries=2,
    )

    usage = client.usage()

    assert len(rec.calls) == 3
    assert usage.sent_today == 5


# -- construction ---------------------------------------------------------


def test_api_key_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("ANNOUNCER_API_KEY", "ann_from_env")
    monkeypatch.setenv("ANNOUNCER_BASE_URL", "http://localhost:8080")

    client = Announcer()

    assert client.base_url == "http://localhost:8080"


def test_missing_key_explains_itself(monkeypatch):
    monkeypatch.delenv("ANNOUNCER_API_KEY", raising=False)

    with pytest.raises(ValueError, match="ANNOUNCER_API_KEY"):
        Announcer()


def test_trailing_slash_is_trimmed():
    client = Announcer("ann_k", base_url="https://api.example.test/")

    assert client.base_url == "https://api.example.test"


def test_user_agent_names_the_caller():
    client, rec = make_client([{"json": {"sentToday": 0}}], user_agent="acme-billing/2.1")

    client.usage()

    assert rec.header("user-agent").startswith("announcer-python/")
    assert rec.header("user-agent").endswith("acme-billing/2.1")

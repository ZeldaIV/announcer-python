"""The async client is the same surface, awaited. These tests exist to prove
the two halves have not drifted."""

from __future__ import annotations

import pytest

from announcer import AsyncAnnouncer, Announcer, ConflictError, SuppressedRecipientError
from conftest import make_async_client


@pytest.mark.asyncio
async def test_async_send():
    client, rec = make_async_client(
        [{"json": {"id": "msg-1", "messageId": "<abc@acme.test>", "status": "sent"}}]
    )

    result = await client.send(
        from_="billing@acme.test", to="customer@example.com", subject="Hi", text="Body"
    )

    assert rec.body() == {
        "from": "billing@acme.test",
        "to": "customer@example.com",
        "subject": "Hi",
        "text": "Body",
    }
    assert rec.header("idempotency-key") is not None
    assert result.id == "msg-1"


@pytest.mark.asyncio
async def test_async_send_raises_the_same_typed_errors():
    client, _ = make_async_client(
        [{"status": 422, "json": {"status": 422, "detail": "b@example.com is suppressed."}}]
    )

    with pytest.raises(SuppressedRecipientError):
        await client.send(from_="a@acme.test", to="b@example.com", text="hi")


@pytest.mark.asyncio
async def test_async_retries_a_500():
    client, rec = make_async_client(
        [
            {"status": 500, "json": {"status": 500}},
            {"json": {"id": "m", "messageId": None, "status": "sent"}},
        ],
        max_retries=2,
    )

    result = await client.send(from_="a@acme.test", to="b@example.com", text="hi")

    assert len(rec.calls) == 2
    assert rec.header("idempotency-key", 0) == rec.header("idempotency-key", 1)
    assert result.id == "m"


@pytest.mark.asyncio
async def test_async_409_outside_send_is_not_retried():
    client, rec = make_async_client(
        [{"status": 409, "json": {"error": "Domain acme.test is already registered."}}],
        max_retries=2,
    )

    with pytest.raises(ConflictError):
        await client.domains.create("acme.test")

    assert len(rec.calls) == 1


@pytest.mark.asyncio
async def test_async_send_many():
    client, rec = make_async_client(
        [
            {"json": {"id": "m1", "messageId": None, "status": "sent"}},
            {"json": {"id": "m2", "messageId": None, "status": "sent"}},
        ]
    )

    results = await client.emails.send_many(
        ["a@example.com", "b@example.com"],
        from_="billing@acme.test",
        text="hi",
        idempotency_key="batch",
    )

    assert [r.ok for r in results] == [True, True]
    assert rec.header("idempotency-key", 0) == "batch-0"
    assert rec.header("idempotency-key", 1) == "batch-1"


@pytest.mark.asyncio
async def test_async_list_and_usage():
    client, _ = make_async_client(
        [
            {"json": []},
            {"json": {"sentToday": 3, "dailySendLimit": 100, "domains": 1, "maxDomains": 3, "plan": "free"}},
        ]
    )

    assert await client.emails.list() == []
    usage = await client.usage()
    assert usage.sent_today == 3


@pytest.mark.asyncio
async def test_async_context_manager_closes():
    async with make_async_client([{"json": []}])[0] as client:
        assert await client.suppressions.list() == []


def test_both_clients_expose_the_same_resources():
    # If a method lands on one client and not the other, that is a bug in the
    # SDK rather than something a caller should discover at runtime.
    sync = Announcer("ann_k")
    unsync = AsyncAnnouncer("ann_k")

    for namespace in ("emails", "domains", "api_keys", "webhooks", "suppressions"):
        sync_methods = {m for m in dir(getattr(sync, namespace)) if not m.startswith("_")}
        async_methods = {m for m in dir(getattr(unsync, namespace)) if not m.startswith("_")}
        assert sync_methods == async_methods, f"{namespace} has drifted"

    sync.close()

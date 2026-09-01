from __future__ import annotations

import re

import pytest

from announcer import SuppressedRecipientError
from conftest import make_client


def test_send_posts_the_message(sent_ok):
    client, rec = make_client([sent_ok])

    result = client.send(
        from_="Acme <billing@acme.test>",
        to="customer@example.com",
        subject="Your receipt",
        text="Thanks!",
    )

    assert rec.request().method == "POST"
    assert str(rec.request().url) == "https://api.example.test/v1/emails"
    assert rec.header("authorization") == "Bearer ann_test_key"
    assert rec.body() == {
        "from": "Acme <billing@acme.test>",
        "to": "customer@example.com",
        "subject": "Your receipt",
        "text": "Thanks!",
    }
    assert result.id == "msg-1"
    assert result.message_id == "<abc@acme.test>"
    assert result.status == "sent"
    assert result.idempotent_replay is False


def test_send_accepts_the_raw_from_spelling_for_splatted_dicts(sent_ok):
    client, rec = make_client([sent_ok])

    payload = {"from": "billing@acme.test", "to": "c@example.com", "text": "hi"}
    client.send(**payload)

    assert rec.body()["from"] == "billing@acme.test"


def test_send_generates_an_idempotency_key(sent_ok):
    client, rec = make_client([sent_ok])

    client.send(from_="a@acme.test", to="b@example.com", text="hi")

    key = rec.header("idempotency-key")
    assert key is not None
    assert re.fullmatch(r"[0-9a-f-]{36}", key)


def test_send_passes_a_supplied_key_through(sent_ok):
    client, rec = make_client([sent_ok])

    client.send(from_="a@acme.test", to="b@example.com", text="hi", idempotency_key="order-4711")

    assert rec.header("idempotency-key") == "order-4711"


def test_send_reports_an_idempotent_replay():
    client, _ = make_client(
        [{"json": {"id": "m", "messageId": "<x@a.test>", "status": "sent", "idempotentReplay": True}}]
    )

    result = client.send(from_="a@acme.test", to="b@example.com", text="hi", idempotency_key="k")

    assert result.idempotent_replay is True


def test_send_accepts_a_list_of_recipients(sent_ok):
    client, rec = make_client([sent_ok])

    client.send(from_="a@acme.test", to=["b@example.com", "c@example.com"], text="hi")

    assert rec.body()["to"] == ["b@example.com", "c@example.com"]


def test_send_refuses_an_empty_recipient_list():
    client, rec = make_client([])

    with pytest.raises(TypeError, match="at least one"):
        client.send(from_="a@acme.test", to=[], text="hi")

    assert rec.calls == []


def test_send_carries_cc_bcc_and_reply_to(sent_ok):
    client, rec = make_client([sent_ok])

    client.send(
        from_="billing@acme.test",
        to="customer@example.com",
        cc="accounting@acme.test",
        bcc=["archive@acme.test", "audit@acme.test"],
        reply_to="support@acme.test",
        subject="Your receipt",
        text="Thanks!",
    )

    assert rec.body() == {
        "from": "billing@acme.test",
        "to": "customer@example.com",
        "cc": "accounting@acme.test",
        "bcc": ["archive@acme.test", "audit@acme.test"],
        # The API accepts replyTo too, but snake_case is its documented shape.
        "reply_to": "support@acme.test",
        "subject": "Your receipt",
        "text": "Thanks!",
    }


def test_send_accepts_the_camel_case_reply_to_for_splatted_dicts(sent_ok):
    client, rec = make_client([sent_ok])

    client.send(**{"from": "a@acme.test", "to": "b@example.com", "text": "hi",
                   "replyTo": "support@acme.test"})

    assert rec.body()["reply_to"] == "support@acme.test"


def test_send_omits_headers_left_unset(sent_ok):
    client, rec = make_client([sent_ok])

    client.send(from_="a@acme.test", to="b@example.com", text="hi")

    assert "cc" not in rec.body()
    assert "bcc" not in rec.body()
    assert "reply_to" not in rec.body()


def test_send_reports_partially_suppressed_recipients():
    client, _ = make_client(
        [
            {
                "json": {
                    "id": "m",
                    "messageId": "<x@a.test>",
                    "status": "sent",
                    "recipients": 2,
                    "suppressed": ["dead@example.com"],
                }
            }
        ]
    )

    result = client.send(
        from_="a@acme.test",
        to=["good@example.com", "dead@example.com"],
        cc="copied@example.com",
        text="hi",
    )

    # The message still went out; only the bad address was dropped.
    assert result.recipients == 2
    assert result.suppressed == ["dead@example.com"]


def test_send_refuses_a_message_with_no_body():
    client, rec = make_client([])

    with pytest.raises(TypeError, match="text.*html"):
        client.send(from_="a@acme.test", to="b@example.com", subject="empty")

    assert rec.calls == []


def test_send_rejects_unknown_arguments():
    client, _ = make_client([])

    # Announcer has no attachments, and a silently ignored kwarg is how a
    # caller ends up believing it sent one.
    with pytest.raises(TypeError, match="attachments"):
        client.send(from_="a@acme.test", to="b@example.com", text="hi", attachments=[])


def test_send_raises_suppressed_recipient_with_the_address():
    client, _ = make_client(
        [
            {
                "status": 422,
                "json": {
                    "title": "Unprocessable Entity",
                    "status": 422,
                    "detail": "bounced@example.com is on your suppression list.",
                },
            }
        ]
    )

    with pytest.raises(SuppressedRecipientError) as excinfo:
        client.send(from_="a@acme.test", to="bounced@example.com", text="hi")

    assert excinfo.value.recipient == "bounced@example.com"
    assert excinfo.value.status == 422
    assert "suppression list" in str(excinfo.value)


def test_a_fully_suppressed_send_lists_every_refused_address():
    client, _ = make_client(
        [
            {
                "status": 422,
                "json": {
                    "status": 422,
                    "detail": "All 2 recipients are on your suppression list.",
                    "suppressed": ["one@example.com", "two@example.com"],
                },
            }
        ]
    )

    with pytest.raises(SuppressedRecipientError) as excinfo:
        client.send(
            from_="a@acme.test", to=["one@example.com", "two@example.com"], text="hi"
        )

    # Read from the API's extension member, not parsed out of the prose.
    assert excinfo.value.suppressed == ["one@example.com", "two@example.com"]
    assert excinfo.value.recipient == "one@example.com"


def test_send_many_reports_each_outcome():
    client, rec = make_client(
        [
            {"json": {"id": "m1", "messageId": None, "status": "sent"}},
            {"status": 422, "json": {"status": 422, "detail": "b@example.com is suppressed."}},
            {"json": {"id": "m3", "messageId": None, "status": "sent"}},
        ]
    )

    results = client.emails.send_many(
        ["a@example.com", "b@example.com", "c@example.com"],
        from_="billing@acme.test",
        subject="Notice",
        text="hi",
    )

    assert len(rec.calls) == 3
    assert [(r.to, r.ok) for r in results] == [
        ("a@example.com", True),
        ("b@example.com", False),
        ("c@example.com", True),
    ]
    assert results[0].result.id == "m1"
    assert isinstance(results[1].error, SuppressedRecipientError)


def test_send_many_derives_a_distinct_key_per_recipient():
    client, rec = make_client(
        [
            {"json": {"id": "m1", "messageId": None, "status": "sent"}},
            {"json": {"id": "m2", "messageId": None, "status": "sent"}},
        ]
    )

    client.emails.send_many(
        ["a@example.com", "b@example.com"],
        from_="billing@acme.test",
        text="hi",
        idempotency_key="digest-2026-09-01",
    )

    # One key across the batch would make every recipient after the first an
    # idempotent replay of the first, and only one person gets the mail.
    assert rec.header("idempotency-key", 0) == "digest-2026-09-01-0"
    assert rec.header("idempotency-key", 1) == "digest-2026-09-01-1"


def test_send_many_can_stop_early():
    client, rec = make_client(
        [
            {"status": 500, "json": {"status": 500, "detail": "boom"}},
            {"json": {"id": "m2", "messageId": None, "status": "sent"}},
        ]
    )

    results = client.emails.send_many(
        ["a@example.com", "b@example.com"],
        from_="billing@acme.test",
        text="hi",
        stop_on_error=True,
    )

    assert len(results) == 1
    assert len(rec.calls) == 1


def test_list_maps_the_snake_case_row_onto_from_and_to():
    client, rec = make_client(
        [
            {
                "json": [
                    {
                        "id": "m1",
                        "message_id": "<x@acme.test>",
                        "header_from": "billing@acme.test",
                        "recipient": "customer@example.com",
                        "subject": "Receipt",
                        "status": "delivered",
                        "created_at": "2026-09-01T10:00:00Z",
                        "recipient_count": 3,
                        "reply_to": "support@acme.test",
                    }
                ]
            }
        ]
    )

    messages = client.emails.list(limit=10, status="delivered")

    assert rec.request().url.params["limit"] == "10"
    assert rec.request().url.params["status"] == "delivered"
    message = messages[0]
    assert message.from_ == "billing@acme.test"
    assert message.to == "customer@example.com"
    assert message.message_id == "<x@acme.test>"
    # Timestamps arrive as real datetimes, not strings.
    assert message.created_at.year == 2026
    assert message.created_at.tzinfo is not None
    # `to` is the primary; the total lives alongside it.
    assert message.recipient_count == 3
    assert message.reply_to == "support@acme.test"


def test_list_omits_unset_filters():
    client, rec = make_client([{"json": []}])

    client.emails.list()

    assert str(rec.request().url) == "https://api.example.test/v1/messages"


def test_events_leaves_the_free_form_payload_alone():
    client, _ = make_client(
        [
            {
                "json": [
                    {
                        "id": 2,
                        "event": "bounced",
                        # Tenant data. Rewriting these keys would corrupt real values.
                        "payload": {"dsn_status": "5.1.1", "Retry_Count": 3},
                        "created_at": "2026-09-01T10:00:00Z",
                    }
                ]
            }
        ]
    )

    events = client.emails.events("m1")

    assert events[0].payload == {"dsn_status": "5.1.1", "Retry_Count": 3}
    assert events[0].event == "bounced"

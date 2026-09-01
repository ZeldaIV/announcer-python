from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from announcer import SignatureVerificationError, verify_webhook
from conftest import make_client

SECRET = "whsec_0123456789abcdef"
NOW = 1_800_000_000

BODY = json.dumps(
    {
        "event": "bounced",
        "occurredAt": "2026-09-01T10:00:00Z",
        "message": {
            "id": "8f3a0000-0000-0000-0000-000000000001",
            "from": "billing@acme.test",
            "to": "customer@example.com",
            "subject": "Your receipt",
            "status": "bounced",
        },
        "detail": {"dsnStatus": "5.1.1 user unknown"},
    }
)


def sign(body: str, timestamp: int, secret: str = SECRET) -> str:
    """Build the header exactly the way the API's `signature_header` does."""
    v1 = hmac.new(
        secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={v1}"


# -- signature verification ----------------------------------------------


def test_accepts_a_good_signature():
    event = verify_webhook(BODY, sign(BODY, NOW), SECRET, now=NOW)

    assert event.event == "bounced"
    assert event.message.to == "customer@example.com"
    assert event.message.from_ == "billing@acme.test"
    assert event.detail == {"dsnStatus": "5.1.1 user unknown"}
    assert event.occurred_at.year == 2026


def test_accepts_bytes_which_is_what_raw_body_parsers_hand_over():
    event = verify_webhook(BODY.encode(), sign(BODY, NOW), SECRET, now=NOW)

    assert event.event == "bounced"


def test_matches_the_apis_documented_test_vector():
    # Pinned against announcer's own `signature_header` unit test, so a change
    # on either side shows up here rather than in production.
    expected = hmac.new(b"key", b"1700000000.{}", hashlib.sha256).hexdigest()
    assert sign("{}", 1_700_000_000, "key") == f"t=1700000000,v1={expected}"


def test_rejects_a_tampered_body():
    header = sign(BODY, NOW)
    tampered = BODY.replace("customer@example.com", "attacker@evil.test")

    with pytest.raises(SignatureVerificationError, match="does not match"):
        verify_webhook(tampered, header, SECRET, now=NOW)


def test_rejects_the_wrong_secret():
    with pytest.raises(SignatureVerificationError):
        verify_webhook(BODY, sign(BODY, NOW), "whsec_wrong", now=NOW)


def test_rejects_a_replay_outside_the_tolerance():
    with pytest.raises(SignatureVerificationError, match="tolerance"):
        verify_webhook(BODY, sign(BODY, NOW - 3600), SECRET, now=NOW)


def test_rejects_a_future_timestamp():
    with pytest.raises(SignatureVerificationError, match="tolerance"):
        verify_webhook(BODY, sign(BODY, NOW + 3600), SECRET, now=NOW)


def test_tolerance_can_be_disabled():
    event = verify_webhook(BODY, sign(BODY, NOW - 86_400), SECRET, now=NOW, tolerance=0)

    assert event.event == "bounced"


@pytest.mark.parametrize(
    "header", ["", "garbage", "t=123", "v1=deadbeef", "t=notanumber,v1=x"]
)
def test_rejects_a_malformed_header(header):
    with pytest.raises(SignatureVerificationError):
        verify_webhook(BODY, header, SECRET, now=NOW)


def test_ignores_unknown_schemes():
    header = sign(BODY, NOW) + ",v2=somethingelse"

    event = verify_webhook(BODY, header, SECRET, now=NOW)

    assert event.event == "bounced"


def test_missing_header_is_rejected():
    with pytest.raises(SignatureVerificationError, match="Missing X-Announcer-Signature"):
        verify_webhook(BODY, None, SECRET, now=NOW)


# -- resources -------------------------------------------------------------


def test_domains_create_returns_the_record_to_publish():
    client, rec = make_client(
        [
            {
                "status": 201,
                "json": {
                    "id": "d1",
                    "domain": "acme.test",
                    "selector": "mail",
                    "verified": False,
                    "dns": [
                        {
                            "type": "TXT",
                            "name": "mail._domainkey.acme.test",
                            "value": "v=DKIM1; k=rsa; p=MIIBIjANBg",
                            "purpose": "DKIM public key.",
                        }
                    ],
                },
            }
        ]
    )

    domain = client.domains.create("acme.test")

    assert rec.body() == {"domain": "acme.test"}
    assert domain.dns[0].name == "mail._domainkey.acme.test"
    assert domain.verified is False


def test_domains_list_derives_verified_from_the_timestamp():
    client, _ = make_client(
        [
            {
                "json": [
                    {
                        "id": "d1",
                        "domain": "acme.test",
                        "selector": "mail",
                        "verified_at": "2026-08-01T09:00:00Z",
                        "created_at": "2026-07-01T09:00:00Z",
                    },
                    {
                        "id": "d2",
                        "domain": "beta.test",
                        "selector": "mail",
                        "verified_at": None,
                        "created_at": "2026-07-02T09:00:00Z",
                    },
                ]
            }
        ]
    )

    domains = client.domains.list()

    assert domains[0].verified is True
    assert domains[1].verified is False
    assert domains[0].verified_at.month == 8


def test_domains_delete_tolerates_the_empty_204():
    client, rec = make_client([{"status": 204}])

    client.domains.delete("d1")

    assert rec.request().method == "DELETE"
    assert str(rec.request().url) == "https://api.example.test/v1/domains/d1"


def test_api_keys_create_defaults_to_full_scope():
    client, rec = make_client(
        [
            {
                "status": 201,
                "json": {
                    "id": "k1",
                    "name": "ci",
                    "prefix": "ann_abc123",
                    "scope": "full",
                    "key": "ann_secret",
                },
            }
        ]
    )

    key = client.api_keys.create("ci")

    assert rec.body() == {"name": "ci", "scope": "full"}
    assert key.key == "ann_secret"


def test_api_keys_list_derives_revoked():
    client, _ = make_client(
        [
            {
                "json": [
                    {
                        "id": "k1",
                        "name": "old",
                        "prefix": "ann_a",
                        "scope": "full",
                        "created_at": "2026-01-01T00:00:00Z",
                        "last_used_at": "2026-02-01T00:00:00Z",
                        "revoked_at": "2026-03-01T00:00:00Z",
                    }
                ]
            }
        ]
    )

    keys = client.api_keys.list()

    assert keys[0].revoked is True
    assert keys[0].last_used_at.month == 2


def test_webhooks_list_derives_disabled():
    client, _ = make_client(
        [
            {
                "json": [
                    {"id": "w1", "url": "https://a.test", "created_at": "2026-01-01T00:00:00Z", "disabled_at": None},
                    {
                        "id": "w2",
                        "url": "https://b.test",
                        "created_at": "2026-01-01T00:00:00Z",
                        "disabled_at": "2026-02-01T00:00:00Z",
                    },
                ]
            }
        ]
    )

    endpoints = client.webhooks.list()

    assert endpoints[0].disabled is False
    assert endpoints[1].disabled is True


def test_webhooks_verify_is_reachable_from_the_client():
    client, _ = make_client([])

    event = client.webhooks.verify(BODY, sign(BODY, NOW), SECRET, now=NOW)

    assert event.event == "bounced"


def test_usage_parses_the_camel_case_response():
    client, _ = make_client(
        [
            {
                "json": {
                    "sentToday": 12,
                    "dailySendLimit": 100,
                    "domains": 1,
                    "maxDomains": 3,
                    "plan": "free",
                    "sentThisPeriod": 40,
                    "periodStart": "2026-09-01T00:00:00Z",
                    "monthlyIncludedMessages": None,
                    "monthlyHardCap": None,
                    "overageMinorUnits": None,
                    "series": [{"date": "2026-09-01", "sent": 12, "delivered": 10, "failed": 1}],
                }
            }
        ]
    )

    usage = client.usage()

    assert usage.sent_today == 12
    assert usage.daily_send_limit == 100
    assert usage.monthly_hard_cap is None
    assert usage.series[0].date.isoformat() == "2026-09-01"


def test_suppressions_list():
    client, rec = make_client(
        [
            {
                "json": [
                    {
                        "id": "s1",
                        "email": "bounced@example.com",
                        "reason": "hard bounce (5.1.1 user unknown)",
                        "created_at": "2026-08-30T12:00:00Z",
                    }
                ]
            }
        ]
    )

    suppressions = client.suppressions.list(limit=25)

    assert rec.request().url.params["limit"] == "25"
    assert suppressions[0].email == "bounced@example.com"
    assert suppressions[0].created_at.day == 30

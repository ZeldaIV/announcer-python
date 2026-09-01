"""Typed results returned by the SDK.

Every dataclass here knows how to build itself from the API's JSON. That
mapping is explicit rather than a generic key-rewriter for a reason: the API
is inconsistent about casing -- hand-written responses are ``camelCase``
(``messageId``, ``sentToday``), while serde-derived list rows are ``snake_case``
(``message_id``, ``created_at``). Reading both here means callers never see the
difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

#: One address or several. Every address may carry a display name:
#: ``"Acme Billing <billing@acme.com>"``.
Addresses = Union[str, Sequence[str]]

__all__ = [
    "Addresses",
    "MESSAGE_STATUSES",
    "EVENT_TYPES",
    "KEY_SCOPES",
    "SentEmail",
    "Message",
    "MessageEvent",
    "DnsRecord",
    "Domain",
    "CreatedDomain",
    "DomainDns",
    "DomainVerification",
    "ApiKey",
    "CreatedApiKey",
    "WebhookEndpoint",
    "CreatedWebhookEndpoint",
    "Suppression",
    "UsagePoint",
    "Usage",
    "WebhookEventMessage",
    "WebhookEvent",
    "BatchSendResult",
]

#: Where a message can sit. ``bounced``, ``failed`` and ``suppressed`` are terminal.
MESSAGE_STATUSES = (
    "queued",
    "sent",
    "delivered",
    "bounced",
    "complained",
    "failed",
    "suppressed",
)

#: The transitions that produce a webhook delivery.
EVENT_TYPES = ("sent", "delivered", "bounced", "complained", "suppressed")

#: What an API key may do.
KEY_SCOPES = ("full", "send")


def _first(data: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    """Return the first key that is present, so both casings are accepted."""
    for name in names:
        if name in data:
            return data[name]
    return default


def _dt(value: Any) -> Optional[datetime]:
    """Parse an RFC 3339 timestamp. ``Z`` is spelled out for Python < 3.11."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        # Never lose a response over a timestamp we did not anticipate.
        return None


def _d(value: Any) -> Optional[_date]:
    """Parse a ``YYYY-MM-DD`` day."""
    if value in (None, ""):
        return None
    if isinstance(value, _date) and not isinstance(value, datetime):
        return value
    try:
        return _date.fromisoformat(str(value))
    except ValueError:
        return None


@dataclass(frozen=True)
class SentEmail:
    """The result of a successful send."""

    #: Announcer's id for the message. Use it with ``emails.events``.
    id: str
    #: The RFC 5322 ``Message-ID`` the MTA assigned.
    message_id: Optional[str]
    status: str
    #: True when this idempotency key had already been used. Nothing was sent
    #: a second time; these are the original send's details.
    idempotent_replay: bool = False
    #: How many addresses the message went to, across to, cc and bcc. This is
    #: the number billed and counted against quota.
    recipients: int = 1
    #: Addresses dropped because they are on your suppression list. Empty on a
    #: clean send -- the rest of the message still went out.
    suppressed: List[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "SentEmail":
        return cls(
            id=data["id"],
            message_id=_first(data, "messageId", "message_id"),
            status=data.get("status", "sent"),
            idempotent_replay=bool(_first(data, "idempotentReplay", "idempotent_replay", default=False)),
            # Older deployments predate both fields; a successful send is at
            # least one recipient and dropped nobody.
            recipients=int(data.get("recipients", 1)),
            suppressed=list(data.get("suppressed") or []),
        )


@dataclass(frozen=True)
class Message:
    """One row of send history."""

    id: str
    #: RFC 5322 ``Message-ID``, absent for messages that never reached the MTA.
    message_id: Optional[str]
    #: The ``From:`` header that went out. Trailing underscore because ``from``
    #: is a Python keyword.
    from_: str
    #: The primary recipient -- the first ``to`` address. A message with cc,
    #: bcc or several ``to`` addresses reports its first here and the total in
    #: :attr:`recipient_count`.
    to: str
    subject: Optional[str]
    #: The rolled-up status. One bounced recipient makes the whole message
    #: ``bounced`` -- it is the thing you have to act on.
    status: str
    created_at: Optional[datetime]
    #: How many addresses the message went to, across to, cc and bcc.
    recipient_count: int = 1
    #: The ``Reply-To:`` header that went out, if any.
    reply_to: Optional[str] = None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Message":
        return cls(
            id=data["id"],
            message_id=_first(data, "message_id", "messageId"),
            # `header_from` and `recipient` are the database's names. The
            # webhook payload already says from/to; one vocabulary is better.
            from_=_first(data, "header_from", "headerFrom", "from", default=""),
            to=_first(data, "recipient", "to", default=""),
            subject=data.get("subject"),
            status=data.get("status", ""),
            created_at=_dt(_first(data, "created_at", "createdAt")),
            recipient_count=int(_first(data, "recipient_count", "recipientCount", default=1)),
            reply_to=_first(data, "reply_to", "replyTo"),
        )


@dataclass(frozen=True)
class MessageEvent:
    """One entry in a message's audit trail."""

    id: int
    event: str
    #: Event-specific data, passed through exactly as the API sent it -- these
    #: keys are tenant data and must not be rewritten.
    payload: Dict[str, Any]
    created_at: Optional[datetime]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "MessageEvent":
        return cls(
            id=data["id"],
            event=data.get("event", ""),
            payload=dict(data.get("payload") or {}),
            created_at=_dt(_first(data, "created_at", "createdAt")),
        )


@dataclass(frozen=True)
class DnsRecord:
    """A DNS record the domain owner has to publish."""

    type: str
    #: e.g. ``mail._domainkey.acme.com``
    name: str
    #: e.g. ``v=DKIM1; k=rsa; p=MIIBIjANBg...``
    value: str
    #: Why this record exists, in a sentence you can show a user.
    purpose: str

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "DnsRecord":
        return cls(
            type=data.get("type", "TXT"),
            name=data.get("name", ""),
            value=data.get("value", ""),
            purpose=data.get("purpose", ""),
        )


@dataclass(frozen=True)
class Domain:
    """A sending domain."""

    id: str
    domain: str
    #: The DKIM selector. Always ``mail``; the API ignores client-supplied ones.
    selector: str
    #: Whether the DKIM record has been seen in DNS and matched.
    verified: bool
    verified_at: Optional[datetime]
    created_at: Optional[datetime]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Domain":
        verified_at = _dt(_first(data, "verified_at", "verifiedAt"))
        return cls(
            id=data["id"],
            domain=data.get("domain", ""),
            selector=data.get("selector", "mail"),
            # The list endpoint sends only the timestamp; derive the boolean
            # the caller actually wants.
            verified=bool(data.get("verified", verified_at is not None)),
            verified_at=verified_at,
            created_at=_dt(_first(data, "created_at", "createdAt")),
        )


@dataclass(frozen=True)
class CreatedDomain:
    """A freshly registered domain, including the record to publish."""

    id: str
    domain: str
    selector: str
    verified: bool
    #: Publish every record here, then call ``domains.verify(id)``.
    dns: List[DnsRecord] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "CreatedDomain":
        return cls(
            id=data["id"],
            domain=data.get("domain", ""),
            selector=data.get("selector", "mail"),
            verified=bool(data.get("verified", False)),
            dns=[DnsRecord.from_api(r) for r in data.get("dns", [])],
        )


@dataclass(frozen=True)
class DomainDns:
    """The records for an existing domain, re-derived from the stored key."""

    id: str
    domain: str
    verified: bool
    dns: List[DnsRecord] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "DomainDns":
        return cls(
            id=data["id"],
            domain=data.get("domain", ""),
            verified=bool(data.get("verified", False)),
            dns=[DnsRecord.from_api(r) for r in data.get("dns", [])],
        )


@dataclass(frozen=True)
class DomainVerification:
    """The outcome of a verification attempt."""

    id: str
    verified: bool

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "DomainVerification":
        return cls(id=data["id"], verified=bool(data.get("verified", False)))


@dataclass(frozen=True)
class ApiKey:
    """An API key, minus the secret."""

    id: str
    name: str
    #: The first few characters, for telling keys apart in a list.
    prefix: str
    scope: str
    created_at: Optional[datetime]
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]
    revoked: bool

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "ApiKey":
        revoked_at = _dt(_first(data, "revoked_at", "revokedAt"))
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            prefix=data.get("prefix", ""),
            scope=data.get("scope", "full"),
            created_at=_dt(_first(data, "created_at", "createdAt")),
            last_used_at=_dt(_first(data, "last_used_at", "lastUsedAt")),
            revoked_at=revoked_at,
            revoked=revoked_at is not None,
        )


@dataclass(frozen=True)
class CreatedApiKey:
    """A newly minted key. ``key`` is shown once and never again."""

    id: str
    name: str
    prefix: str
    scope: str
    #: The full secret. Only ever present here -- the API stores only its hash.
    key: str

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "CreatedApiKey":
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            prefix=data.get("prefix", ""),
            scope=data.get("scope", "full"),
            key=data.get("key", ""),
        )


@dataclass(frozen=True)
class WebhookEndpoint:
    """A registered webhook endpoint."""

    id: str
    url: str
    created_at: Optional[datetime]
    disabled_at: Optional[datetime]
    disabled: bool

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "WebhookEndpoint":
        disabled_at = _dt(_first(data, "disabled_at", "disabledAt"))
        return cls(
            id=data["id"],
            url=data.get("url", ""),
            created_at=_dt(_first(data, "created_at", "createdAt")),
            disabled_at=disabled_at,
            disabled=disabled_at is not None,
        )


@dataclass(frozen=True)
class CreatedWebhookEndpoint:
    """A newly registered endpoint. ``secret`` is shown once."""

    id: str
    url: str
    #: The ``whsec_...`` secret to pass to ``webhooks.verify``.
    secret: str

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "CreatedWebhookEndpoint":
        return cls(id=data["id"], url=data.get("url", ""), secret=data.get("secret", ""))


@dataclass(frozen=True)
class Suppression:
    """A suppressed address. Announcer refuses to send to these."""

    id: str
    email: str
    #: Why it was suppressed, e.g. ``hard bounce (5.1.1 user unknown)``.
    reason: str
    created_at: Optional[datetime]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Suppression":
        return cls(
            id=data["id"],
            email=data.get("email", ""),
            reason=data.get("reason", ""),
            created_at=_dt(_first(data, "created_at", "createdAt")),
        )


@dataclass(frozen=True)
class UsagePoint:
    """One day of the 14-day sending series."""

    date: Optional[_date]
    sent: int
    delivered: int
    #: Bounced + complained + failed. A subset of ``sent``, not additional to it.
    failed: int

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "UsagePoint":
        return cls(
            date=_d(data.get("date")),
            sent=int(data.get("sent", 0)),
            delivered=int(data.get("delivered", 0)),
            failed=int(data.get("failed", 0)),
        )


@dataclass(frozen=True)
class Usage:
    """Consumption against this account's limits."""

    sent_today: int
    daily_send_limit: int
    domains: int
    max_domains: int
    plan: str
    sent_this_period: int
    period_start: Optional[datetime]
    #: None on plans with no monthly accounting.
    monthly_included_messages: Optional[int]
    #: The spend ceiling. None when uncapped.
    monthly_hard_cap: Optional[int]
    #: What the current period's overage would cost, in minor units.
    overage_minor_units: Optional[int]
    #: The last 14 days, oldest first. Days with no sends are present as zeroes.
    series: List[UsagePoint] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Usage":
        return cls(
            sent_today=int(_first(data, "sentToday", "sent_today", default=0)),
            daily_send_limit=int(_first(data, "dailySendLimit", "daily_send_limit", default=0)),
            domains=int(data.get("domains", 0)),
            max_domains=int(_first(data, "maxDomains", "max_domains", default=0)),
            plan=data.get("plan", ""),
            sent_this_period=int(_first(data, "sentThisPeriod", "sent_this_period", default=0)),
            period_start=_dt(_first(data, "periodStart", "period_start")),
            monthly_included_messages=_first(data, "monthlyIncludedMessages", "monthly_included_messages"),
            monthly_hard_cap=_first(data, "monthlyHardCap", "monthly_hard_cap"),
            overage_minor_units=_first(data, "overageMinorUnits", "overage_minor_units"),
            series=[UsagePoint.from_api(p) for p in data.get("series", [])],
        )


@dataclass(frozen=True)
class WebhookEventMessage:
    """The message summary carried on a webhook delivery."""

    id: str
    from_: str
    to: str
    subject: Optional[str]
    status: str

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "WebhookEventMessage":
        return cls(
            id=data.get("id", ""),
            from_=data.get("from", ""),
            to=data.get("to", ""),
            subject=data.get("subject"),
            status=data.get("status", ""),
        )


@dataclass(frozen=True)
class WebhookEvent:
    """A verified webhook delivery."""

    #: One of ``sent``, ``delivered``, ``bounced``, ``complained``, ``suppressed``.
    event: str
    occurred_at: Optional[datetime]
    message: WebhookEventMessage
    #: Event-specific data, e.g. ``{"dsnStatus": "5.1.1 ..."}`` on a bounce.
    detail: Dict[str, Any] = field(default_factory=dict)
    #: The parsed body, in case you need a field this dataclass does not model.
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "WebhookEvent":
        return cls(
            event=data.get("event", ""),
            occurred_at=_dt(_first(data, "occurredAt", "occurred_at")),
            message=WebhookEventMessage.from_api(data.get("message") or {}),
            detail=dict(data.get("detail") or {}),
            raw=dict(data),
        )


@dataclass(frozen=True)
class BatchSendResult:
    """One recipient's outcome from ``emails.send_many``."""

    to: str
    ok: bool
    #: Present when ``ok``.
    result: Optional[SentEmail] = None
    #: Present when not ``ok``.
    error: Optional[Exception] = None

"""Python SDK for Announcer -- transactional email from your own domain.

::

    from announcer import Announcer

    announcer = Announcer()  # reads ANNOUNCER_API_KEY

    announcer.send(
        from_="Acme <billing@acme.com>",
        to="customer@example.com",
        subject="Your receipt",
        text="Thanks for your order.",
    )
"""

from ._client import Announcer, AsyncAnnouncer
from ._http import DEFAULT_BASE_URL
from ._resources import (
    ApiKeys,
    AsyncApiKeys,
    AsyncDomains,
    AsyncEmails,
    AsyncSuppressions,
    AsyncWebhooks,
    Domains,
    Emails,
    Suppressions,
    Webhooks,
)
from .errors import (
    AnnouncerError,
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    SignatureVerificationError,
    SuppressedRecipientError,
    UnprocessableEntityError,
    ValidationError,
)
from .types import (
    EVENT_TYPES,
    KEY_SCOPES,
    MESSAGE_STATUSES,
    Addresses,
    ApiKey,
    BatchSendResult,
    CreatedApiKey,
    CreatedDomain,
    CreatedWebhookEndpoint,
    DnsRecord,
    Domain,
    DomainDns,
    DomainVerification,
    Message,
    MessageEvent,
    SentEmail,
    Suppression,
    Usage,
    UsagePoint,
    WebhookEndpoint,
    WebhookEvent,
    WebhookEventMessage,
)
from .webhooks import DEFAULT_TOLERANCE_SECONDS, verify_webhook

__version__ = "0.1.1"

__all__ = [
    "__version__",
    "Announcer",
    "AsyncAnnouncer",
    "DEFAULT_BASE_URL",
    "DEFAULT_TOLERANCE_SECONDS",
    "verify_webhook",
    # Resources
    "Emails",
    "Domains",
    "ApiKeys",
    "Webhooks",
    "Suppressions",
    "AsyncEmails",
    "AsyncDomains",
    "AsyncApiKeys",
    "AsyncWebhooks",
    "AsyncSuppressions",
    # Errors
    "AnnouncerError",
    "APIConnectionError",
    "APITimeoutError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
    "UnprocessableEntityError",
    "SuppressedRecipientError",
    "RateLimitError",
    "InternalServerError",
    "SignatureVerificationError",
    # Types
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
    "WebhookEvent",
    "WebhookEventMessage",
    "BatchSendResult",
]

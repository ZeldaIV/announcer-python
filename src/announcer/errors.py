"""Exceptions raised by the Announcer SDK.

The API answers errors in four different shapes, and every one of them has to
land on a useful exception here:

* RFC 9457 problem+json -- ``{"type", "title", "status", "detail"}``
* validation            -- ``{"errors": {"field": ["message"]}}``
* two 409 paths         -- ``{"error": "..."}``
* several 404 handlers  -- no body at all

Names follow the convention set by other Python API clients
(``PermissionDeniedError`` rather than ``PermissionError``) so that
``from announcer import *`` cannot shadow a builtin.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

__all__ = [
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
]


class AnnouncerError(Exception):
    """Base class for everything this SDK raises. Catch this to catch them all."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        title: Optional[str] = None,
        detail: Optional[str] = None,
        request_id: Optional[str] = None,
        raw: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        #: HTTP status, or 0 when the request never reached the API.
        self.status = status
        #: The problem's ``title``, when the API sent one.
        self.title = title
        #: The problem's ``detail`` -- usually the most human-readable part.
        self.detail = detail
        #: The response's ``x-request-id``, when present. Quote this in support.
        self.request_id = request_id
        #: The parsed error body, exactly as the API sent it.
        self.raw = raw


class APIConnectionError(AnnouncerError):
    """The request never got an answer: DNS, TCP, TLS, or a timeout."""


class APITimeoutError(APIConnectionError):
    """The request exceeded the client-side timeout."""


class AuthenticationError(AnnouncerError):
    """401 -- the key is missing, malformed, or revoked."""


class PermissionDeniedError(AnnouncerError):
    """403 -- the key is valid but not allowed to do this.

    In practice one of: a ``send``-scoped key touching ``/v1/domains`` or
    ``/v1/keys``; sending from a domain this account has not registered; or
    sending from a registered-but-unverified domain while the server requires
    verification.
    """


class NotFoundError(AnnouncerError):
    """404 -- no such resource, or it belongs to another account."""


class ConflictError(AnnouncerError):
    """409 -- a duplicate domain, or a send with this key still in flight.

    The in-flight case is retried automatically first; seeing this means the
    retry budget ran out while the original was still running.
    """


class ValidationError(AnnouncerError):
    """400 -- one or more fields were rejected."""

    def __init__(
        self,
        message: str,
        *,
        errors: Optional[Mapping[str, List[str]]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        #: Field name -> the messages explaining why it was rejected.
        self.errors: Dict[str, List[str]] = dict(errors or {})


class UnprocessableEntityError(AnnouncerError):
    """422 -- the request was well-formed but cannot be carried out."""


class SuppressedRecipientError(UnprocessableEntityError):
    """422 from a send -- every recipient is on this account's suppression list.

    They hard-bounced or complained previously. The attempt is still recorded
    and still counts against quota. Do not retry: remove them from the
    suppression list first, or stop mailing them.

    A send where only *some* recipients are suppressed does not raise: the rest
    goes out and the dropped addresses come back in ``SentEmail.suppressed``.
    """

    def __init__(
        self,
        message: str,
        *,
        recipient: Optional[str] = None,
        suppressed: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        #: Every address that was refused, as reported by the API. Prefer this
        #: over parsing :attr:`AnnouncerError.detail`: the API sends it as an
        #: RFC 9457 extension member so clients need not read the prose.
        self.suppressed: List[str] = list(suppressed or [])
        #: The first refused address. Convenience for the single-recipient case;
        #: falls back to the address the SDK sent when the API named none.
        self.recipient = self.suppressed[0] if self.suppressed else recipient


class RateLimitError(AnnouncerError):
    """429 -- a per-second rate limit, or a daily/monthly quota."""

    def __init__(self, message: str, *, retry_after: Optional[float] = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        #: Seconds to wait, from the ``Retry-After`` header.
        self.retry_after = retry_after


class InternalServerError(AnnouncerError):
    """5xx -- the API failed. Retried automatically before you see this."""


class SignatureVerificationError(AnnouncerError):
    """A webhook's ``X-Announcer-Signature`` did not check out.

    Treat the delivery as hostile: do not act on its contents.
    """


#: Wording for statuses that arrive with no usable body -- the bare 404s.
_GENERIC = {
    400: "The request was rejected as invalid.",
    401: "Invalid or revoked API key.",
    403: "This API key is not permitted to perform that action.",
    404: "Not found.",
    409: "Conflict.",
    422: "The request could not be processed.",
    429: "Rate limit exceeded.",
}


def _message_for(status: int, body: Optional[Mapping[str, Any]]) -> str:
    """Pick the most useful sentence out of whichever error shape arrived.

    Order matters: ``detail`` is written for humans, ``errors`` is specific
    about which field is wrong, and ``title`` is generic boilerplate that only
    helps when nothing better exists.
    """
    if body:
        detail = body.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        error = body.get("error")
        if isinstance(error, str) and error:
            return error
        errors = body.get("errors")
        if isinstance(errors, dict) and errors:
            return "; ".join(
                f"{field}: {' '.join(messages)}" for field, messages in errors.items()
            )
        title = body.get("title")
        if isinstance(title, str) and title:
            return title
    return _GENERIC.get(status, f"Announcer API returned HTTP {status}.")


def _parse_retry_after(headers: Mapping[str, str]) -> Optional[float]:
    """Read ``Retry-After``, which the API sends as a whole number of seconds."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def error_from_response(
    status: int,
    body: Optional[Mapping[str, Any]],
    headers: Mapping[str, str],
    *,
    path: str = "",
    recipient: Optional[str] = None,
) -> AnnouncerError:
    """Map an unsuccessful response onto the right exception subclass."""
    message = _message_for(status, body)
    common: Dict[str, Any] = {
        "status": status,
        "title": (body or {}).get("title"),
        "detail": (body or {}).get("detail"),
        "request_id": headers.get("x-request-id"),
        "raw": body,
    }

    if status == 400:
        return ValidationError(message, errors=(body or {}).get("errors"), **common)
    if status == 401:
        return AuthenticationError(message, **common)
    if status == 403:
        return PermissionDeniedError(message, **common)
    if status == 404:
        return NotFoundError(message, **common)
    if status == 409:
        return ConflictError(message, **common)
    if status == 422:
        # Only the send path can produce a suppression refusal; anything else
        # 422 is a plain unprocessable (domain limit, failed DKIM verification).
        if path == "/v1/emails":
            return SuppressedRecipientError(
                message,
                recipient=recipient,
                suppressed=(body or {}).get("suppressed"),
                **common,
            )
        return UnprocessableEntityError(message, **common)
    if status == 429:
        return RateLimitError(message, retry_after=_parse_retry_after(headers), **common)
    if status >= 500:
        return InternalServerError(message, **common)
    return AnnouncerError(message, **common)

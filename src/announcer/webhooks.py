"""Webhook signature verification.

Announcer signs every delivery with ``X-Announcer-Signature: t=<unix>,v1=<hex>``,
where the MAC is HMAC-SHA256 over the literal string ``"<t>.<raw body>"``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional, Union

from .errors import SignatureVerificationError
from .types import WebhookEvent

__all__ = ["verify_webhook", "DEFAULT_TOLERANCE_SECONDS"]

#: How far apart the delivery's timestamp and our clock may be, in seconds.
DEFAULT_TOLERANCE_SECONDS = 300


def _parse_header(header: str) -> "tuple[int, str]":
    """Parse ``t=<unix>,v1=<hex>``, ignoring schemes we do not know about."""
    timestamp: Optional[int] = None
    v1: Optional[str] = None

    for part in header.split(","):
        key, sep, value = part.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                continue
        elif key == "v1":
            v1 = value

    if timestamp is None or v1 is None:
        raise SignatureVerificationError(
            'Malformed X-Announcer-Signature header: expected "t=<unix>,v1=<hex>", '
            f'got "{header}".'
        )
    return timestamp, v1


def verify_webhook(
    payload: Union[str, bytes, bytearray],
    signature_header: Optional[str],
    secret: str,
    *,
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
    now: Optional[float] = None,
) -> WebhookEvent:
    """Check a webhook delivery and return the parsed event.

    Pass the **raw** request body -- the exact bytes Announcer sent.
    Re-serialising a parsed object reorders keys and changes whitespace, and the
    signature will not match. In Flask that means ``request.get_data()``, not
    ``request.get_json()``; in Django, ``request.body``.

    :param tolerance: Reject deliveries older (or newer) than this many seconds.
        The timestamp is inside the MAC, so this check is what actually stops a
        captured delivery being replayed. Pass ``0`` to disable it.
    :raises SignatureVerificationError: on a malformed header, a MAC that does
        not match, or a delivery outside the tolerance.
    """
    if not signature_header:
        raise SignatureVerificationError("Missing X-Announcer-Signature header.")
    if not secret:
        raise SignatureVerificationError("Missing webhook signing secret.")

    body = payload if isinstance(payload, str) else bytes(payload).decode("utf-8")
    timestamp, v1 = _parse_header(signature_header)

    if tolerance > 0:
        current = time.time() if now is None else now
        if abs(current - timestamp) > tolerance:
            raise SignatureVerificationError(
                f"Webhook timestamp is outside the {tolerance}s tolerance "
                f"(signed at {timestamp}, now {int(current)}). Rejecting as a possible replay."
            )

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, v1):
        raise SignatureVerificationError(
            "Webhook signature does not match. Check that you are passing the raw request "
            "body and the secret returned by webhooks.create."
        )

    return WebhookEvent.from_api(json.loads(body))

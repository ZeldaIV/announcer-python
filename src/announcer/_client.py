"""The sync and async clients."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Mapping, Optional, Type

import httpx

from ._http import AsyncTransport, SyncTransport
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
from .types import SentEmail, Usage

__all__ = ["Announcer", "AsyncAnnouncer"]


class Announcer:
    """The Announcer client.

    ::

        from announcer import Announcer

        announcer = Announcer()  # reads ANNOUNCER_API_KEY

        announcer.send(
            from_="Acme <billing@acme.com>",
            to="customer@example.com",
            subject="Your receipt",
            text="Thanks for your order.",
        )

    Reuse one instance for the life of your process; it holds a connection pool
    and no per-request state. It is also a context manager, if you would rather
    close the pool deterministically.

    :param api_key: Your ``ann_...`` key. Falls back to ``ANNOUNCER_API_KEY``.
    :param base_url: API root. Falls back to ``ANNOUNCER_BASE_URL``, then the
        hosted API.
    :param timeout: Per-attempt timeout in seconds.
    :param max_retries: Extra attempts after a failure.
    :param user_agent: Appended to the SDK's own. Name your app here.
    :param headers: Added to every request.
    :param http_client: Bring your own ``httpx.Client`` (proxies, custom TLS).
        You are then responsible for closing it.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        user_agent: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._transport = SyncTransport(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            user_agent=user_agent,
            headers=headers,
            http_client=http_client,
        )
        #: Sending mail and reading send history. Works with either key scope.
        self.emails = Emails(self._transport)
        #: Registering and verifying sending domains. Needs a ``full`` key.
        self.domains = Domains(self._transport)
        #: Issuing and revoking API keys. Needs a ``full`` key.
        self.api_keys = ApiKeys(self._transport)
        #: Webhook endpoints, and verifying their deliveries. Needs a ``full`` key.
        self.webhooks = Webhooks(self._transport)
        #: Addresses that hard-bounced or complained.
        self.suppressions = Suppressions(self._transport)

    @property
    def base_url(self) -> str:
        """The API root this client talks to."""
        return self._transport.base_url

    def send(self, **kwargs: Any) -> SentEmail:
        """Shorthand for ``emails.send``. The one call most integrations make."""
        return self.emails.send(**kwargs)

    def usage(self) -> Usage:
        """Consumption against this account's limits, plus a 14-day series."""
        return Usage.from_api(self._transport.request("GET", "/v1/usage"))

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._transport.close()

    def __enter__(self) -> "Announcer":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()


class AsyncAnnouncer:
    """The async client. Same surface as :class:`Announcer`, awaited.

    ::

        from announcer import AsyncAnnouncer

        async with AsyncAnnouncer() as announcer:
            await announcer.send(
                from_="billing@acme.com",
                to="customer@example.com",
                subject="Your receipt",
                text="Thanks!",
            )
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        user_agent: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._transport = AsyncTransport(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            user_agent=user_agent,
            headers=headers,
            http_client=http_client,
        )
        self.emails = AsyncEmails(self._transport)
        self.domains = AsyncDomains(self._transport)
        self.api_keys = AsyncApiKeys(self._transport)
        self.webhooks = AsyncWebhooks(self._transport)
        self.suppressions = AsyncSuppressions(self._transport)

    @property
    def base_url(self) -> str:
        """The API root this client talks to."""
        return self._transport.base_url

    async def send(self, **kwargs: Any) -> SentEmail:
        """Shorthand for ``emails.send``."""
        return await self.emails.send(**kwargs)

    async def usage(self) -> Usage:
        """Consumption against this account's limits, plus a 14-day series."""
        return Usage.from_api(await self._transport.request("GET", "/v1/usage"))

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._transport.aclose()

    async def __aenter__(self) -> "AsyncAnnouncer":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.aclose()

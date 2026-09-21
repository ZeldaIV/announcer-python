"""Transport: auth, JSON, timeouts, retries, and error typing.

The sync and async halves keep their own retry loops rather than sharing one
through an abstraction. The loop is twenty lines; the abstraction that lets one
body serve both would be longer than the duplication and harder to read when
something goes wrong at three in the morning.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from typing import Any, Dict, Mapping, Optional

import httpx

from .errors import (
    APIConnectionError,
    APITimeoutError,
    AnnouncerError,
    error_from_response,
)

#: Where the hosted API lives.
DEFAULT_BASE_URL = "https://mail.misralo.com"

SDK_VERSION = "0.1.1"

#: Never wait longer than this between attempts, whatever the server suggests.
_MAX_BACKOFF = 8.0
_MAX_RETRY_AFTER = 60.0


def _should_retry(status: int, retry_on_409: bool) -> bool:
    """Statuses worth trying again. Everything else is the caller's problem."""
    if status in (408, 429):
        return True
    if status == 409 and retry_on_409:
        return True
    return status >= 500


def _backoff(attempt: int) -> float:
    """Exponential backoff with full jitter.

    The jitter is not decoration: without it, every client that hit the same
    rate limit retries in lockstep and hits it again together.
    """
    return random.random() * min(_MAX_BACKOFF, 0.5 * (2.0**attempt))


def _delay_for(response: httpx.Response, attempt: int) -> float:
    """The server's ``Retry-After`` beats our guess -- it knows when the
    per-tenant window actually rolls."""
    raw = response.headers.get("retry-after")
    if raw:
        try:
            seconds = float(raw)
            if seconds > 0:
                return min(seconds, _MAX_RETRY_AFTER)
        except ValueError:
            pass
    return _backoff(attempt)


def _parse_body(response: httpx.Response) -> Any:
    """204s and empty bodies become ``None``; everything else is parsed JSON."""
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def _parse_error_body(response: httpx.Response) -> Optional[Dict[str, Any]]:
    """Several 404 handlers answer with no body at all, so this tolerates junk."""
    if not response.content:
        return None
    try:
        parsed = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


class _BaseTransport:
    """Configuration shared by the sync and async clients."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        user_agent: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        key = api_key or os.environ.get("ANNOUNCER_API_KEY")
        if not key:
            raise ValueError(
                'No Announcer API key. Pass one to the constructor -- Announcer("ann_...") -- '
                "or set the ANNOUNCER_API_KEY environment variable."
            )
        self._api_key = key

        resolved = base_url or os.environ.get("ANNOUNCER_BASE_URL") or DEFAULT_BASE_URL
        self.base_url = resolved.rstrip("/")

        self.timeout = timeout
        self.max_retries = max_retries
        self._user_agent = (
            f"announcer-python/{SDK_VERSION} {user_agent}"
            if user_agent
            else f"announcer-python/{SDK_VERSION}"
        )
        self._extra_headers = dict(headers or {})

    def _headers(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        merged = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        merged.update(self._extra_headers)
        merged.update(extra or {})
        return merged

    @staticmethod
    def _clean(params: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        """Drop unset query parameters rather than sending ``None``."""
        return {k: v for k, v in (params or {}).items() if v is not None}

    def _error(
        self,
        response: httpx.Response,
        path: str,
        recipient: Optional[str],
    ) -> AnnouncerError:
        return error_from_response(
            response.status_code,
            _parse_error_body(response),
            {k.lower(): v for k, v in response.headers.items()},
            path=path,
            recipient=recipient,
        )


class SyncTransport(_BaseTransport):
    """Blocking HTTP, backed by an ``httpx.Client`` held for the client's life."""

    def __init__(self, *args: Any, http_client: Optional[httpx.Client] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=self.timeout)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        retry_on_409: bool = False,
        recipient: Optional[str] = None,
    ) -> Any:
        url = self.base_url + path
        request_headers = self._headers(headers)
        last_error: Optional[AnnouncerError] = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(
                    method,
                    url,
                    params=self._clean(params),
                    json=json_body,
                    headers=request_headers,
                    timeout=self.timeout,
                )
            except httpx.TimeoutException as exc:
                last_error = APITimeoutError(
                    f"Request to the Announcer API timed out after {self.timeout}s."
                )
                if attempt < self.max_retries:
                    time.sleep(_backoff(attempt))
                    continue
                raise last_error from exc
            except httpx.RequestError as exc:
                last_error = APIConnectionError(
                    f"Could not reach the Announcer API at {self.base_url}: {exc}"
                )
                if attempt < self.max_retries:
                    time.sleep(_backoff(attempt))
                    continue
                raise last_error from exc

            if response.is_success:
                return _parse_body(response)

            last_error = self._error(response, path, recipient)
            if _should_retry(response.status_code, retry_on_409) and attempt < self.max_retries:
                time.sleep(_delay_for(response, attempt))
                continue
            raise last_error

        raise last_error or APIConnectionError("Request failed for an unknown reason.")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class AsyncTransport(_BaseTransport):
    """Non-blocking HTTP, backed by an ``httpx.AsyncClient``."""

    def __init__(
        self, *args: Any, http_client: Optional[httpx.AsyncClient] = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=self.timeout)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        retry_on_409: bool = False,
        recipient: Optional[str] = None,
    ) -> Any:
        url = self.base_url + path
        request_headers = self._headers(headers)
        last_error: Optional[AnnouncerError] = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=self._clean(params),
                    json=json_body,
                    headers=request_headers,
                    timeout=self.timeout,
                )
            except httpx.TimeoutException as exc:
                last_error = APITimeoutError(
                    f"Request to the Announcer API timed out after {self.timeout}s."
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise last_error from exc
            except httpx.RequestError as exc:
                last_error = APIConnectionError(
                    f"Could not reach the Announcer API at {self.base_url}: {exc}"
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise last_error from exc

            if response.is_success:
                return _parse_body(response)

            last_error = self._error(response, path, recipient)
            if _should_retry(response.status_code, retry_on_409) and attempt < self.max_retries:
                await asyncio.sleep(_delay_for(response, attempt))
                continue
            raise last_error

        raise last_error or APIConnectionError("Request failed for an unknown reason.")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

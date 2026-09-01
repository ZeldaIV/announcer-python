"""Test fixtures.

Everything runs against ``httpx.MockTransport``, so the real client code --
retries, headers, error parsing -- is exercised without a network.
"""

from __future__ import annotations

import json as _json
from typing import Any, Dict, List, Optional

import httpx
import pytest

from announcer import Announcer, AsyncAnnouncer


class Recorder:
    """Plays back a queue of responses and records every request."""

    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self._queue = list(responses)
        self.calls: List[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if not self._queue:
            raise AssertionError(
                f"unexpected call number {len(self.calls)} to {request.method} {request.url}"
            )
        spec = self._queue.pop(0)

        if "raises" in spec:
            raise spec["raises"]

        status = spec.get("status", 200)
        headers = {"content-type": "application/json", **spec.get("headers", {})}
        if "content" in spec:
            return httpx.Response(status, content=spec["content"], headers=headers)
        if "json" in spec:
            return httpx.Response(status, content=_json.dumps(spec["json"]), headers=headers)
        return httpx.Response(status, content=b"", headers=headers)

    # -- assertions the tests reach for repeatedly ------------------------

    def request(self, index: int = 0) -> httpx.Request:
        return self.calls[index]

    def body(self, index: int = 0) -> Any:
        return _json.loads(self.calls[index].content)

    def header(self, name: str, index: int = 0) -> Optional[str]:
        return self.calls[index].headers.get(name)


def make_client(responses: List[Dict[str, Any]], **kwargs: Any) -> "tuple[Announcer, Recorder]":
    """A sync client wired to a stub, retries off unless a test asks for them."""
    recorder = Recorder(responses)
    kwargs.setdefault("max_retries", 0)
    client = Announcer(
        "ann_test_key",
        base_url="https://api.example.test",
        http_client=httpx.Client(transport=httpx.MockTransport(recorder.handle)),
        **kwargs,
    )
    return client, recorder


def make_async_client(
    responses: List[Dict[str, Any]], **kwargs: Any
) -> "tuple[AsyncAnnouncer, Recorder]":
    """An async client wired to a stub."""
    recorder = Recorder(responses)
    kwargs.setdefault("max_retries", 0)
    client = AsyncAnnouncer(
        "ann_test_key",
        base_url="https://api.example.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handle)),
        **kwargs,
    )
    return client, recorder


@pytest.fixture
def sent_ok() -> Dict[str, Any]:
    """A minimal successful send response."""
    return {"json": {"id": "msg-1", "messageId": "<abc@acme.test>", "status": "sent"}}

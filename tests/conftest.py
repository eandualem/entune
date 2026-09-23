from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from dictum.providers.contracts import Clip
from dictum.store import Store

WEBM_HEADER = b"\x1a\x45\xdf\xa3" + b"\x00" * 12


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path)


@pytest.fixture
def clip() -> Clip:
    return Clip(WEBM_HEADER, "audio/webm")


def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """An httpx client whose every request goes to `handler` instead of the network."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def wait_for_build(client: TestClient) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = client.get("/api/dictionary/build").json()
        if state["phase"] in ("ready", "failed", "cancelled"):
            detail: dict[str, Any] = client.get(f"/api/dictionary/build/{state['id']}").json()
            return detail
        time.sleep(0.01)
    pytest.fail("Dictionary build did not finish")

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

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

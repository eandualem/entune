"""What a suggestion call takes and returns, and the call itself.

Pydantic AI and the provider SDKs are large; they load on the first call (call.py),
not when Entune starts.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from pydantic_ai.models import Model

MAX_FIXES = 2  # corrected replies the model may send after breaking a rule


@dataclass(frozen=True)
class Request:
    provider: str
    api_key: str
    model: str  # provider:model
    system: str
    user: str
    shape: type[BaseModel]  # the reply's structure
    check: Callable[[str], object]  # raises ValueError naming the rule a reply breaks
    retrying: Callable[[int, str], None]  # (attempt about to start, the rule broken)


Caller = Callable[[Request], Coroutine[Any, Any, str]]
"""Returns the reply as JSON text that passed `check`."""


class BrokenReply(ValueError):
    """Every reply, including the corrected ones, broke a rule of the dictionary."""


async def call_model(request: Request, model: Model | None = None) -> str:
    """One request through Pydantic AI; see call.py."""
    from entune.learning.suggestion_model import call

    return await call.run(request, model)

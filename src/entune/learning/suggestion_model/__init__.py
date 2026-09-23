"""The suggestion model: the language-model providers, their models, and one call.

The model never touches a transcript on its way to the user; it is only asked for
dictionary suggestions.
"""

from entune.learning.suggestion_model.call import (
    MAX_FIXES,
    MAX_OUTPUT_TOKENS,
    BrokenReply,
    Caller,
    Request,
    call_model,
)
from entune.learning.suggestion_model.catalog import LLM_PROVIDERS, ModelChoice, catalog

__all__ = [
    "LLM_PROVIDERS",
    "MAX_FIXES",
    "MAX_OUTPUT_TOKENS",
    "BrokenReply",
    "Caller",
    "ModelChoice",
    "Request",
    "call_model",
    "catalog",
]

"""The suggestion model: the language-model providers, their models, and one call.

The model never touches a transcript on its way to the user; it is only asked for
dictionary suggestions.
"""

from entune.learning.suggestion_model.catalog import CHATGPT, LLM_PROVIDERS, ModelChoice, catalog
from entune.learning.suggestion_model.request import (
    MAX_FIXES,
    BrokenReply,
    Caller,
    ReplyStopped,
    ReplyTimedOut,
    ReplyTooLong,
    Request,
    call_model,
    passing,
)

__all__ = [
    "CHATGPT",
    "LLM_PROVIDERS",
    "MAX_FIXES",
    "BrokenReply",
    "Caller",
    "ModelChoice",
    "ReplyStopped",
    "ReplyTimedOut",
    "ReplyTooLong",
    "Request",
    "call_model",
    "catalog",
    "passing",
]

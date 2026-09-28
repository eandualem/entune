"""A Pydantic AI model for each provider, built per call with its own client.

Each client gets the key it is given and the provider's official endpoint, never one
from the environment, and makes one attempt per request: the SDKs' own retries are off.
The client is closed when the call ends.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
from pydantic_ai.models import Model

from entune.learning.suggestion_model import chatgpt
from entune.learning.suggestion_model.catalog import CHATGPT

TIMEOUT = 1200.0  # seconds; a reply at high reasoning effort takes minutes
CONNECT = 5.0  # seconds to open a connection


def _credential(header: str, value: str) -> dict[str, list[Callable[[Any], Awaitable[None]]]]:
    """Set the saved key on every request. The SDKs merge headers from the environment
    (ANTHROPIC_CUSTOM_HEADERS and the like) over their own, so only the last step before
    sending can guarantee which key goes out."""

    async def set_credential(request: Any) -> None:
        request.headers[header] = value

    return {"request": [set_credential]}


async def _connect_limit(request: Any) -> None:
    """Connecting gets CONNECT seconds on every request. Each SDK sends its own timeout
    with the request, over its HTTP client's, and some send one number for the whole
    request (Google's always does), which would let a stalled connection wait TIMEOUT."""
    request.extensions["timeout"] = {**request.extensions.get("timeout", {}), "connect": CONNECT}


def _hooks(header: str, value: str) -> dict[str, list[Callable[[Any], Awaitable[None]]]]:
    return {"request": [*_credential(header, value)["request"], _connect_limit]}


def _http2(header: str, value: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        timeout=httpx2.Timeout(TIMEOUT, connect=CONNECT),
        trust_env=False,
        event_hooks=_hooks(header, value),
    )


def _http(header: str, value: str) -> httpx.AsyncClient:
    """For the SDKs still on httpx (Groq, Mistral)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(TIMEOUT, connect=CONNECT),
        trust_env=False,
        event_hooks=_hooks(header, value),
    )


@asynccontextmanager
async def provider_model(provider: str, api_key: str, name: str) -> AsyncIterator[Model]:
    if provider == "anthropic":
        from anthropic import AsyncAnthropic
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        async with AsyncAnthropic(
            api_key=api_key,
            base_url="https://api.anthropic.com",
            max_retries=0,
            timeout=httpx2.Timeout(TIMEOUT, connect=CONNECT),
            http_client=_http2("x-api-key", api_key),
        ) as anthropic:
            yield AnthropicModel(name, provider=AnthropicProvider(anthropic_client=anthropic))
        return
    if provider in ("openai", CHATGPT):
        from openai import AsyncOpenAI
        from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
        from pydantic_ai.providers.openai import OpenAIProvider

        # A ChatGPT plan answers at its own endpoint, for the account its sign-in names;
        # `api_key` is then the sign-in's access token.
        plan = provider == CHATGPT
        account = chatgpt.account_id(api_key) if plan else None
        async with AsyncOpenAI(
            api_key=api_key,
            base_url=chatgpt.BACKEND if plan else "https://api.openai.com/v1",
            max_retries=0,
            timeout=httpx2.Timeout(TIMEOUT, connect=CONNECT),
            default_headers={"ChatGPT-Account-Id": account} if account else None,
            http_client=_http2("authorization", f"Bearer {api_key}"),
        ) as openai:
            yield OpenAIResponsesModel(
                name,
                provider=OpenAIProvider(openai_client=openai),
                settings=OpenAIResponsesModelSettings(openai_store=False),
            )
        return
    if provider == "google":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        # Our own client, so the limits are ours and the client is closed when the call ends.
        async with _http2("x-goog-api-key", api_key) as http:
            google = GoogleProvider(
                api_key=api_key,
                base_url="https://generativelanguage.googleapis.com/",
                http_client=http,
            )
            yield GoogleModel(name, provider=google)
        return
    if provider == "groq":
        from groq import AsyncGroq
        from pydantic_ai.models.groq import GroqModel
        from pydantic_ai.providers.groq import GroqProvider

        async with AsyncGroq(
            api_key=api_key,
            base_url="https://api.groq.com",
            max_retries=0,
            timeout=httpx.Timeout(TIMEOUT, connect=CONNECT),
            http_client=_http("authorization", f"Bearer {api_key}"),
        ) as groq:
            yield GroqModel(name, provider=GroqProvider(groq_client=groq))
        return
    if provider == "mistral":
        from mistralai.client import Mistral
        from pydantic_ai.models.mistral import MistralModel
        from pydantic_ai.providers.mistral import MistralProvider

        async with (
            _http("authorization", f"Bearer {api_key}") as mistral_http,
            Mistral(
                api_key=api_key,
                server_url="https://api.mistral.ai",
                timeout_ms=int(TIMEOUT * 1000),
                async_client=mistral_http,
            ) as mistral,
        ):
            yield MistralModel(name, provider=MistralProvider(mistral_client=mistral))
        return
    raise ValueError(f"Unknown dictionary provider: {provider}")

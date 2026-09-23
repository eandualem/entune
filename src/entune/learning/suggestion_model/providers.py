"""A Pydantic AI model for each provider, built per call with its own client.

Each client gets the key it is given and the provider's official endpoint, never one
from the environment, and makes one attempt per request: the SDKs' own retries are off.
The client is closed when the call ends.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic_ai.models import Model

TIMEOUT = 1200.0  # seconds; a reply at high reasoning effort takes minutes


@asynccontextmanager
async def provider_model(provider: str, api_key: str, name: str) -> AsyncIterator[Model]:
    if provider == "anthropic":
        from anthropic import AsyncAnthropic
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        async with AsyncAnthropic(
            api_key=api_key, base_url="https://api.anthropic.com", max_retries=0, timeout=TIMEOUT
        ) as anthropic:
            yield AnthropicModel(name, provider=AnthropicProvider(anthropic_client=anthropic))
        return
    if provider == "openai":
        from openai import AsyncOpenAI
        from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
        from pydantic_ai.providers.openai import OpenAIProvider

        async with AsyncOpenAI(
            api_key=api_key, base_url="https://api.openai.com/v1", max_retries=0, timeout=TIMEOUT
        ) as openai:
            yield OpenAIResponsesModel(
                name,
                provider=OpenAIProvider(openai_client=openai),
                settings=OpenAIResponsesModelSettings(openai_store=False),
            )
        return
    if provider == "google":
        import httpx2
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        # Our own client, so the timeout is ours and the client is closed when the call ends.
        async with httpx2.AsyncClient(timeout=httpx2.Timeout(TIMEOUT, connect=5)) as http:
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
            api_key=api_key, base_url="https://api.groq.com", max_retries=0, timeout=TIMEOUT
        ) as groq:
            yield GroqModel(name, provider=GroqProvider(groq_client=groq))
        return
    if provider == "mistral":
        from mistralai.client import Mistral
        from pydantic_ai.models.mistral import MistralModel
        from pydantic_ai.providers.mistral import MistralProvider

        async with Mistral(
            api_key=api_key, server_url="https://api.mistral.ai", timeout_ms=int(TIMEOUT * 1000)
        ) as mistral:
            yield MistralModel(name, provider=MistralProvider(mistral_client=mistral))
        return
    raise ValueError(f"Unknown dictionary provider: {provider}")

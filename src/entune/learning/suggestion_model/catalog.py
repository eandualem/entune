"""The language-model providers Entune routes suggestions to, and their suggested models."""

from __future__ import annotations

from dataclasses import dataclass

# Providers we route to, with a reasonably priced model suggested first. Each is keyed by
# the same ID as its saved API key, so Groq shares the key already saved for speech.
# CHATGPT runs OpenAI's models on a ChatGPT plan through a sign-in instead of a key.
CHATGPT = "chatgpt"
# Signing in with ChatGPT comes first: most people have a plan, and it needs no key. The
# order is also which provider a dictionary without a chosen model uses first.
LLM_PROVIDERS: dict[str, tuple[str, str]] = {
    CHATGPT: ("OpenAI · ChatGPT subscription", "chatgpt:gpt-6-astra"),
    "openai": ("OpenAI", "openai:gpt-5.4-mini"),
    "anthropic": ("Anthropic", "anthropic:claude-sonnet-5"),
    "google": ("Google Gemini", "google:gemini-3.5-flash"),
    "groq": ("Groq", "groq:openai/gpt-oss-120b"),
    "mistral": ("Mistral", "mistral:mistral-large-latest"),
}


@dataclass(frozen=True)
class ModelChoice:
    id: str
    name: str


_MODELS = {
    "anthropic": (
        ("claude-sonnet-5", "Claude Sonnet 5"),
        ("claude-fable-5-1", "Claude Fable 5.1"),
        ("claude-opus-5-5", "Claude Opus 5.5"),
        ("claude-opus-5", "Claude Opus 5"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-haiku-4-5", "Claude Haiku 4.5"),
    ),
    "openai": (
        ("gpt-5.4-mini", "GPT-5.4 mini"),
        ("gpt-6-astra", "GPT-6 Astra"),
        ("gpt-6-sol", "GPT-6 Sol"),
        ("gpt-6-luna", "GPT-6 Luna"),
        ("gpt-5.6-sol", "GPT-5.6 Sol"),
        ("gpt-5.6-terra", "GPT-5.6 Terra"),
        ("gpt-5.6-luna", "GPT-5.6 Luna"),
        ("gpt-5.4", "GPT-5.4"),
        ("gpt-5.4-pro", "GPT-5.4 Pro"),
    ),
    # The plan decides which of these it allows; per-token price does not apply.
    CHATGPT: (
        ("gpt-6-astra", "GPT-6 Astra"),
        ("gpt-6-sol", "GPT-6 Sol"),
        ("gpt-6-luna", "GPT-6 Luna"),
        ("gpt-5.4-mini", "GPT-5.4 mini"),
    ),
    "google": (
        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)"),
        ("gemini-3.8-flash", "Gemini 3.8 Flash"),
        ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite"),
    ),
    "groq": (
        ("openai/gpt-oss-120b", "GPT-OSS 120B"),
        ("openai/gpt-oss-20b", "GPT-OSS 20B"),
        ("llama-3.3-70b-versatile", "Llama 3.3 70B"),
    ),
    "mistral": (
        ("mistral-large-latest", "Mistral Large"),
        ("mistral-medium-latest", "Mistral Medium"),
        ("mistral-small-latest", "Mistral Small"),
    ),
}


def catalog(provider: str) -> list[ModelChoice]:
    """Suggested text models, default first; Settings also accepts a custom model ID."""
    return [ModelChoice(f"{provider}:{model}", name) for model, name in _MODELS[provider]]

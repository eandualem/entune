"""One suggestion request through Pydantic AI: a typed reply, checked, fixed only visibly.

The reply's shape is declared as a Pydantic model and, where the provider supports it,
enforced by the provider itself. Rules a schema cannot express run as a check; when the
reply breaks one, the model is shown the rule and asked for a corrected reply, at most
MAX_FIXES times, and the caller is told before each attempt so the person can stop it.
Nothing else is retried: the provider SDKs' own retries are off, and a failed request,
a refusal or a reply cut at the output limit ends the call at once.
"""

from __future__ import annotations

import asyncio

import pydantic_ai
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, NativeOutput, PromptedOutput, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from entune.learning.suggestion_model.catalog import CHATGPT
from entune.learning.suggestion_model.providers import TIMEOUT, provider_model
from entune.learning.suggestion_model.request import MAX_FIXES, BrokenReply, Request

# Thinking and reasoning tokens count toward this cap, so it leaves room for a long
# proposal after the model has reasoned. Billing follows actual use.
MAX_OUTPUT_TOKENS = 32_000

pydantic_ai.BANNER_ENABLED = False  # Entune's output is its own


async def run(request: Request, model: Model | None = None) -> str:
    """One request with an explicit key and official endpoint. `model` replaces the
    provider's model in tests."""
    prefix, sep, name = request.model.partition(":")
    if not sep or prefix != request.provider or not name.strip():
        raise ValueError("The dictionary model must belong to the selected provider")
    if model is not None:
        return await _run(request, model)
    async with provider_model(request.provider, request.api_key, name) as chosen:
        return await _run(request, chosen)


async def _run(request: Request, chosen: Model) -> str:
    native = chosen.profile.get("supports_json_schema_output", False)
    plan = request.provider == CHATGPT
    agent = Agent(
        chosen,
        output_type=NativeOutput(request.shape) if native else PromptedOutput(request.shape),
        instructions=request.system,
        retries={"output": MAX_FIXES},
        # No timeout here: it would replace each provider client's five-second connect limit.
        # A ChatGPT plan's endpoint takes no output limit; the plan applies its own.
        model_settings=(
            ModelSettings(thinking="medium")
            if plan
            else ModelSettings(max_tokens=MAX_OUTPUT_TOKENS, thinking="medium")
        ),
    )

    @agent.output_validator
    def check(ctx: RunContext[object], reply: BaseModel) -> BaseModel:
        if not ctx.partial_output:
            try:
                request.check(reply.model_dump_json())
            except ValueError as exc:
                raise ModelRetry(str(exc)) from exc
        return reply

    attempt = 1
    try:
        async with agent.iter(request.user) as run:
            async for node in run:
                if not Agent.is_model_request_node(node):
                    continue
                retry = [p for p in node.request.parts if isinstance(p, RetryPromptPart)]
                if retry:
                    attempt += 1
                    request.retrying(attempt, _problem(retry[-1]))
                # Streamed: a connection that carries nothing for a minute is cut on the
                # way (observed 2026-09-21), and a reasoning reply is silent for minutes.
                # The HTTP timeout only limits silence between reads, so a stream that
                # keeps sending is given TIMEOUT in total.
                try:
                    async with asyncio.timeout(TIMEOUT), node.stream(run.ctx) as stream:
                        async for _ in stream:
                            pass
                        finish = stream.response.finish_reason
                except TimeoutError as exc:
                    raise ValueError(
                        f"The reply timed out: still streaming after {TIMEOUT / 60:.0f} minutes"
                    ) from exc
                if finish == "length":
                    limit = "plan's" if plan else f"{MAX_OUTPUT_TOKENS}-token"
                    raise ValueError(
                        f"The reply reached the {limit} output limit,"
                        " which includes reasoning; nothing from this step was used"
                    )
                # Only a reply the provider says it finished is used: a stream that
                # was cut, failed or filtered can still carry text that parses.
                if finish != "stop":
                    raise ValueError(
                        f"The model did not finish its reply (finish reason: {finish})"
                    )
            assert run.result is not None
            return run.result.output.model_dump_json()
    except UnexpectedModelBehavior as exc:
        last = getattr(exc.__cause__, "tool_retry", None)
        if isinstance(last, RetryPromptPart):
            raise BrokenReply(_problem(last)) from exc
        raise ValueError(str(exc)) from exc


def _problem(part: RetryPromptPart) -> str:
    """The rule a reply broke, in the words the model was shown."""
    if isinstance(part.content, str):
        return part.content
    # A reply that fits no branch of a union reports every branch; the branch whose
    # `basis` it did not claim says nothing useful.
    errors = [e for e in part.content if e["type"] != "literal_error"] or part.content
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc'] if not str(p)[:1].isupper())}: {e['msg']}"
        for e in errors[:3]
    )

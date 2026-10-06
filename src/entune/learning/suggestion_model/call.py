"""One suggestion request through Pydantic AI: a typed reply, checked, fixed only visibly.

The reply's shape is declared as a Pydantic model and, where the provider supports it,
enforced by the provider itself. Rules a schema cannot express run as a check; when the
reply breaks one, the model is shown the rule and asked for a corrected reply, at most
MAX_FIXES times, and the caller is told before each attempt so the person can stop it.
Nothing else is retried: the provider SDKs' own retries are off, and a failed request,
a refusal, a reply cut at the output limit or one past its time limit ends the call at
once. `passing` tells the caller which failures another attempt of the same request may
overcome.
"""

from __future__ import annotations

import asyncio
from typing import cast

import httpx
import httpx2
import openai
import pydantic_ai
from opentelemetry import trace
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, NativeOutput, PromptedOutput, RunContext
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings, ThinkingLevel

from entune.learning.suggestion_model.catalog import CHATGPT
from entune.learning.suggestion_model.providers import PLAN_TIMEOUT, TIMEOUT, provider_model
from entune.learning.suggestion_model.request import (
    MAX_FIXES,
    BrokenReply,
    ReplyStopped,
    ReplyTimedOut,
    ReplyTooLong,
    Request,
)

# Thinking and reasoning tokens count toward this cap, so it leaves room for a long
# proposal after the model has reasoned. Billing follows actual use.
MAX_OUTPUT_TOKENS = 32_000
# Anthropic's xhigh is a 32,768-token thinking budget, which must stay below the output
# limit: that one combination gets 64,000, the most Claude models write in one reply.
ANTHROPIC_XHIGH_OUTPUT_TOKENS = 64_000

# A runaway reply streams valid JSON, then only whitespace until the connection is cut,
# and never recovers; no finished reply has ended in a run this long.
RUNAWAY = 2_000  # whitespace characters

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
    plan = request.provider == CHATGPT
    # On a ChatGPT plan the strict JSON schema lets a reply fall into endless blank space
    # between tokens, which never recovers (156 of GPT-6 Astra's 170 failed attempts in
    # the September experiments). The schema there goes in the instructions instead; the
    # reply is still validated against it and sent back for a fix when it breaks it.
    native = chosen.profile.get("supports_json_schema_output", False) and not plan
    agent = Agent(
        chosen,
        output_type=NativeOutput(request.shape) if native else PromptedOutput(request.shape),
        instructions=request.system,
        retries={"output": MAX_FIXES},
        # No timeout here: it would replace each provider client's five-second connect limit.
        # A ChatGPT plan's endpoint takes no output limit; the plan applies its own.
        model_settings=(
            ModelSettings(thinking=_effort(request.effort))
            if plan
            else ModelSettings(max_tokens=_output_limit(request), thinking=_effort(request.effort))
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
                    problem = _problem(retry[-1])
                    _mark_rejected(problem)
                    request.retrying(attempt, problem)
                # Streamed: a connection that carries nothing for a minute is cut on the
                # way (observed 2026-09-21), and a reasoning reply is silent for minutes.
                # The HTTP timeout only limits silence between reads, so a stream that
                # keeps sending is given TIMEOUT in total, or PLAN_TIMEOUT on a plan.
                allowed = PLAN_TIMEOUT if plan else TIMEOUT
                try:
                    async with asyncio.timeout(allowed), node.stream(run.ctx) as stream:
                        blank = 0  # whitespace characters the reply's text ends in
                        received: list[str] = []  # the reply so far, for a trace if it stops
                        try:
                            async for event in stream:
                                text = _text(event)
                                received.append(text)
                                kept = text.rstrip()
                                blank = blank + len(text) if not kept else len(text) - len(kept)
                                if blank > RUNAWAY:
                                    raise ReplyStopped(
                                        "the reply ran into empty output",
                                        f"The reply ran into empty output: more than {RUNAWAY}"
                                        " whitespace characters in a row, so it was stopped",
                                    )
                        except BaseException:
                            _keep_partial(received, blank)  # stopped, timed out or cut off
                            raise
                        finish = stream.response.finish_reason
                except TimeoutError as exc:
                    raise ReplyTimedOut(
                        f"the reply ran past its {allowed / 60:g}-minute limit",
                        f"The reply timed out: still streaming after {allowed / 60:g} minutes"
                        + (", just before the ChatGPT plan would cut it" if plan else ""),
                    ) from exc
                if finish == "length":
                    limit = "plan's" if plan else f"{_output_limit(request)}-token"
                    raise ReplyTooLong(
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


def passing(error: Exception) -> str | None:
    """Why another attempt of the same request may succeed, or None when it would fail the
    same way: Entune stopped the reply, the connection failed or the service had a server
    error. A refused key, a limit or quota (401, 403, 429) and anything else do not pass."""
    if isinstance(error, ReplyTimedOut):
        return None  # the same part would most likely run as long again
    if isinstance(error, ReplyStopped):
        return error.reason
    if isinstance(error, ModelHTTPError):
        code = error.status_code
        return f"the service returned error {code}" if code >= 500 else None
    if isinstance(error, ModelAPIError | httpx.TransportError | httpx2.TransportError):
        # An error the service sent inside a streamed reply (a ChatGPT plan's usage limit,
        # for one) has no status code; only a temporary one there may pass.
        cause = error.__cause__
        if isinstance(cause, openai.APIError) and not isinstance(cause, openai.APIConnectionError):
            body = cause.body if isinstance(cause.body, dict) else {}
            if body.get("code") not in STREAMED_TEMPORARY:
                return None
            return f"the service reported {body['code']}"
        return "the connection failed"
    return None


# Codes of streamed errors worth another attempt: a server error, and a ChatGPT plan's
# usage briefly unavailable (OpenAI's Sign in with ChatGPT error list).
STREAMED_TEMPORARY = frozenset({"server_error", "usage_unavailable"})


PARTIAL = 50_000  # characters of an interrupted reply kept on its trace


def _mark_rejected(problem: str) -> None:
    """When tracing is on, mark the run whose reply was sent back as a warning with the
    rule it broke, so a filter on level finds every rejected reply across runs."""
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("langfuse.observation.level", "WARNING")
        span.set_attribute("langfuse.observation.status_message", f"Reply sent back: {problem}")


def _keep_partial(received: list[str], blank: int) -> None:
    """When tracing is on, put what an interrupted reply had sent on its request's trace;
    the instrumentation records only finished replies."""
    span = trace.get_current_span()
    if not span.is_recording():
        return
    text = "".join(received).rstrip()
    span.set_attribute("entune.partial_reply", text[-PARTIAL:])
    span.set_attribute("entune.partial_reply_characters", len(text))
    span.set_attribute("entune.trailing_blank_characters", blank)


def _output_limit(request: Request) -> int:
    if request.provider == "anthropic" and request.effort == "xhigh":
        return ANTHROPIC_XHIGH_OUTPUT_TOKENS
    return MAX_OUTPUT_TOKENS


def _effort(value: str) -> ThinkingLevel:
    """The reasoning level by its own name; app/learning.py accepts only these."""
    return cast(ThinkingLevel, value)


def _text(event: object) -> str:
    """The reply text a stream event adds; reasoning is not the reply."""
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta
    return ""


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

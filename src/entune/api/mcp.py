"""The MCP endpoint at /mcp: a person's agent runs Entune for them. These are the dictionary
tools: read it, look up how its words are used, and improve it; api/mcp_setup.py
has the rest (models, settings, audio, building the dictionary, recent dictations).

Every change names the version it was made on and goes through the same validation and
lock as the dictionary page, so nothing added meanwhile is overwritten.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.routing import Route
from starlette.types import ASGIApp

from entune import __version__, prompts
from entune.api import mcp_setup
from entune.app.dictionary_file import DictionaryChanged
from entune.app.entune import Entune
from entune.dictionary import document as dictionary_document
from entune.dictionary import edits
from entune.dictionary.entries import Dictionary, key
from entune.learning import view
from entune.learning.replies import occurrences

INSTRUCTIONS = (
    "Entune is the person's dictation app, and you run it for them: set it up, build its "
    "dictionary and keep it accurate, so they never have to think about it. Start with "
    "entune_setup, and read dictionary_guide before changing anything. Act without asking "
    "them to choose, review or approve; ask only for what only they can give (an API key "
    "they save in Entune's Settings, a permission, a private name's spelling). When done, "
    "tell them in one line that it is ready to use."
)
EXCERPT = 120  # characters of transcript either side of an occurrence

READ = ToolAnnotations(read_only_hint=True)
EDIT = ToolAnnotations(read_only_hint=False, destructive_hint=False)
REMOVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)


def server(app: Entune) -> MCPServer:
    mcp = MCPServer(name="Entune", instructions=INSTRUCTIONS, version=__version__)

    def change(update: Callable[[Dictionary], Dictionary], version: str) -> str:
        try:
            return app.dictionary.change(update, version)
        except (ValueError, DictionaryChanged) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=READ)
    def dictionary_guide() -> str:
        """How to run Entune for the person without involving them: setting it up step by
        step, how the dictionary works, and what makes an entry right. Read it first."""
        return prompts.text("dictionary-guide.md")

    @mcp.tool(annotations=READ)
    def read_dictionary() -> dict[str, Any]:
        """The dictionary with its version: the words the person uses, each with its
        meaning and where they used it. The same words serve every speech model."""
        text, version = app.dictionary.dictionary_snapshot()
        return {"version": version, "words": json.loads(text)["words"]}

    @mcp.tool(annotations=READ)
    def find_in_transcripts(
        text: str, speech_model: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
        """Where `text` occurs as whole words (ignoring case) in the person's transcripts:
        `total` occurrences in how many `transcripts` of how many `searched`, and up to
        `limit` (at most 50) `excerpts`, newest first, the occurrence marked ⟦like this⟧.
        Transcripts are what `speech_model` wrote, the default speech model when omitted;
        another model's transcripts often show best how the person uses a word."""
        model = speech_model or app.models.default_model()
        if model is None:
            raise ToolError("No default speech model is set; name one from read_dictionary")
        # Any speech model's transcripts, including one removed since.
        provider, _, name = model.partition("/")
        found: list[dict[str, str]] = []
        total = transcripts = searched = 0
        for item in app.store.learning_inputs(provider, name, scope="all"):
            searched += 1
            spans = occurrences(item.text, text)
            total += len(spans)
            transcripts += bool(spans)
            for start, end in spans:
                if len(found) >= max(1, min(limit, 50)):
                    break
                before = item.text[max(0, start - EXCERPT) : start]
                after = item.text[end : end + EXCERPT]
                excerpt = f"{before}\u27e6{item.text[start:end]}\u27e7{after}"
                found.append({"transcript": item.id, "excerpt": " ".join(excerpt.split())})
        return {
            "total": total,
            "transcripts": transcripts,
            "searched": searched,
            "excerpts": found,
        }

    @mcp.tool(annotations=EDIT)
    def set_word(
        version: str,
        spelling: str,
        meaning: str,
        casing: Literal["fixed", "ordinary"] | None = None,
        personal_context: str | None = None,
        said_as: str | None = None,
        word_id: str | None = None,
    ) -> dict[str, str]:
        """Add a word the person uses, or with `word_id` edit one; editing keeps what you
        leave out, and "" clears a note. `meaning` is a short phrase (at most 120
        characters) saying what the word is. `casing` is "fixed" (a new word's default)
        or "ordinary". `said_as` is how it is said, only when its spelling does not tell
        ("kube control" for kubectl). Returns the word's ID and the dictionary's new
        version."""
        identity = word_id or "w_" + uuid.uuid4().hex

        def update(dictionary: Dictionary) -> Dictionary:
            stored = next((w for w in dictionary.words if w.id == identity), None)
            if word_id is not None and stored is None:
                raise ValueError(f"No word has the ID {word_id}")
            (word,) = dictionary_document.parse_words(
                [
                    {
                        "id": identity,
                        "spelling": spelling,
                        "meaning": meaning,
                        "personal_context": (
                            stored.personal_context
                            if stored and personal_context is None
                            else personal_context or None
                        ),
                        "casing": casing or (stored.casing if stored else "fixed"),
                        "needs_review": not meaning.strip(),
                        "said_as": (
                            stored.said_as if stored and said_as is None else said_as or None
                        ),
                    }
                ],
                "word",
            )
            if len(word.meaning) > view.MEANING_CHARS:
                raise ValueError(
                    f"Keep the meaning to a short phrase of at most {view.MEANING_CHARS} characters"
                )
            # A new word spelled and described as one the dictionary has (a retried call)
            # is that word: edit it instead.
            same = next(
                (
                    w
                    for w in dictionary.words
                    if word_id is None
                    and key(w.spelling) == key(word.spelling)
                    and key(w.meaning) == key(word.meaning)
                ),
                None,
            )
            if same is not None:
                raise ValueError(f"{same.spelling} is already the word {same.id}; edit that one")
            return edits.set_word(dictionary, word)

        return {"word_id": identity, "version": change(update, version)}

    @mcp.tool(annotations=REMOVE)
    def delete_word(version: str, word_id: str) -> dict[str, str]:
        """Delete a word the person does not use. Returns the new version."""
        return {"version": change(lambda d: edits.delete_word(d, word_id), version)}

    mcp_setup.register(mcp, app)
    return mcp


def endpoint(app: Entune) -> tuple[MCPServer, ASGIApp]:
    """The server and the handler for /mcp. Replies are stateless JSON: an agent's tool call
    is one request and one answer. Hosts and origins must be this machine's, as the app's
    LocalOnly middleware also requires; a Host header may come without a port."""
    agents = server(app)
    local = ("127.0.0.1", "localhost", "[::1]")
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*local, *(f"{host}:*" for host in local)],
        allowed_origins=[f"http://{host}{port}" for host in local for port in ("", ":*")],
    )
    starlette = agents.streamable_http_app(
        stateless_http=True, json_response=True, transport_security=security
    )
    route = starlette.routes[0]
    assert isinstance(route, Route)
    return agents, route.app

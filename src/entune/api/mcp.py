"""The MCP endpoint at /mcp: a person's agent reads the dictionary, looks up how its heard
texts are used, and improves it with them.

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
from entune.app.dictionary_file import DictionaryChanged
from entune.app.entune import Entune
from entune.dictionary import changes, edits
from entune.dictionary import document as dictionary_document
from entune.dictionary.entries import Dictionary, key
from entune.learning import view
from entune.learning.replies import occurrences

INSTRUCTIONS = (
    "Entune's personal dictation dictionary: the words a speech model gets wrong, and what "
    "the person meant. Call dictionary_guide before changing anything, read the dictionary "
    "with read_dictionary, and change only what the person agrees to."
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

    def scope_of(scope: str) -> str:
        # A speech model removed since keeps its learned entries: those stay editable.
        known = scope == edits.PINNED or scope in app.dictionary.dictionary().learned
        if not known and app.models.resolve(scope) is None:
            raise ToolError(
                f'Unknown speech model "{scope}": use "pinned" or a speech model from'
                " read_dictionary"
            )
        return scope

    @mcp.tool(annotations=READ)
    def dictionary_guide() -> str:
        """How Entune's dictionary works, why it is structured so, and what makes an entry
        right. Read it before changing anything."""
        return prompts.text("dictionary-guide.md")

    @mcp.tool(annotations=READ)
    def read_dictionary(speech_model: str | None = None) -> dict[str, Any]:
        """The dictionary with its version. Each speech model has its own dictionary: the
        pinned entries, shared by every model, and its own learned entries, because every
        model mishears differently. `entries_in_use` is what `speech_model` (the default
        one when omitted) applies to a dictation; `dictionary` holds every section, with
        the learned entries of each model under its ID. `unused_words` are words no entry
        names any more."""
        text, version = app.dictionary.dictionary_snapshot()
        document = json.loads(text)
        dictionary = dictionary_document.parse(text)
        model = speech_model or app.models.default_model()
        pinned = {key(h.text) for h in dictionary.pinned}
        in_use = dictionary.effective(model) if model else dictionary.pinned
        named = {
            c.word
            for entries in (dictionary.pinned, *dictionary.learned.values())
            for h in entries
            for c in h.candidates
        }
        models = [m.id for m in app.models.available_models()]
        return {
            "version": version,
            "default_speech_model": app.models.default_model(),
            "speech_models": list(dict.fromkeys((*models, *document["learned"]))),
            "speech_model": model,
            "entries_in_use": [
                {
                    "text": h.text,
                    "scope": edits.PINNED if key(h.text) in pinned else model,
                    "words": [c.word for c in h.candidates],
                }
                for h in in_use
            ],
            "unused_words": [w.id for w in dictionary.words if w.id not in named],
            "dictionary": document,
        }

    @mcp.tool(annotations=READ)
    def find_in_transcripts(
        text: str, speech_model: str | None = None, limit: int = 10
    ) -> list[dict[str, str]]:
        """Excerpts of the person's transcripts where `text` occurs as whole words (ignoring
        case), newest first, the occurrence marked ⟦like this⟧. Transcripts are what
        `speech_model` wrote, the default speech model when omitted."""
        model = speech_model or app.models.default_model()
        if model is None:
            raise ToolError("No default speech model is set; name one from read_dictionary")
        # Any speech model's transcripts, including one removed since.
        provider, _, name = model.partition("/")
        found = []
        for item in app.store.learning_inputs(provider, name, scope="all"):
            for start, end in occurrences(item.text, text):
                before = item.text[max(0, start - EXCERPT) : start]
                after = item.text[end : end + EXCERPT]
                excerpt = f"{before}\u27e6{item.text[start:end]}\u27e7{after}"
                found.append({"transcript": item.id, "excerpt": " ".join(excerpt.split())})
                if len(found) >= max(1, min(limit, 50)):
                    return found
        return found

    @mcp.tool(annotations=EDIT)
    def set_word(
        version: str,
        spelling: str,
        meaning: str,
        casing: Literal["fixed", "ordinary"] = "fixed",
        personal_context: str | None = None,
        word_id: str | None = None,
    ) -> dict[str, str]:
        """Add a word, or with `word_id` edit one; the edit reaches every entry naming it.
        `meaning` is a short phrase (at most 120 characters) saying what the word is. A new
        spelling or casing clears Always approvals naming the word. Returns the word's ID
        and the dictionary's new version."""
        identity = word_id or "w_" + uuid.uuid4().hex
        try:
            (word,) = dictionary_document.parse_words(
                [
                    {
                        "id": identity,
                        "spelling": spelling,
                        "meaning": meaning,
                        "personal_context": personal_context or None,
                        "casing": casing,
                        "needs_review": not meaning.strip(),
                    }
                ],
                "word",
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if len(word.meaning) > view.MEANING_CHARS:
            raise ToolError(
                f"Keep the meaning to a short phrase of at most {view.MEANING_CHARS} characters"
            )

        def update(dictionary: Dictionary) -> Dictionary:
            if word_id is not None and not any(w.id == word_id for w in dictionary.words):
                raise ValueError(f"No word has the ID {word_id}")
            return edits.set_word(dictionary, word)

        return {"word_id": identity, "version": change(update, version)}

    @mcp.tool(annotations=EDIT)
    def set_heard_entry(
        version: str,
        scope: str,
        text: str,
        words: list[str],
        always: str | None = None,
        always_reason: str = "",
    ) -> dict[str, str]:
        """Add the heard entry `text`, or replace the words it can stand for. `scope` is
        "pinned" (every speech model) or the ID of the one speech model whose mistake it
        is: learned entries belong to that model only. `words` are word IDs, in
        order; a word it already had keeps its evidence. `always` approves one of them to
        be written without reading the sentence (rare; needs `always_reason`); "" clears an
        approval, and leaving it out keeps one. Returns the new version."""
        where = scope_of(scope)
        return {
            "version": change(
                lambda d: edits.set_entry(d, where, text, words, always, always_reason), version
            )
        }

    @mcp.tool(annotations=REMOVE)
    def remove_heard_entry(version: str, scope: str, text: str) -> dict[str, str]:
        """Remove a heard entry from `scope` ("pinned" or a speech model's ID). Its words
        stay in the dictionary. Returns the new version."""
        where = scope_of(scope)
        return {"version": change(lambda d: edits.remove_entry(d, where, text), version)}

    @mcp.tool(annotations=EDIT)
    def pin_heard_entry(version: str, speech_model: str, text: str) -> dict[str, str]:
        """Move a speech model's learned heard entry to pinned, so every speech model uses
        it and suggestions leave it alone. A pinned entry with the same text gains its
        words. Returns the new version."""
        model = scope_of(speech_model)
        return {"version": change(lambda d: changes.pin(d, model, text), version)}

    @mcp.tool(annotations=REMOVE)
    def delete_word(version: str, word_id: str) -> dict[str, Any]:
        """Delete a word: it is removed from every entry naming it, and an entry left with
        no word goes too. Returns the new version and the heard texts it was removed from."""
        touched: list[str] = []

        def update(dictionary: Dictionary) -> Dictionary:
            updated, removed = edits.delete_word(dictionary, word_id)
            touched.extend(removed)
            return updated

        return {"version": change(update, version), "removed_from": touched}

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

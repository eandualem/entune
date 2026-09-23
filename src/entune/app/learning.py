"""Suggestion runs the person starts: prepare the input, then retry, apply or discard.

The run itself (batches, the model, checkpoints) belongs to learning/builds.py; this
part decides what a run reads and what applying its proposal writes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from entune.app.dictionary_file import DictionaryChanged, DictionaryFile
from entune.app.models import NoDefaultModel, SpeechModels, UnknownModel
from entune.app.settings import Settings
from entune.app.suggestion_runs import BuildInput, DictionaryBuilds, Source
from entune.dictionary import changes as dictionary_changes
from entune.dictionary import document as dictionary_document
from entune.dictionary.changes import Proposal
from entune.learning import batches, suggestion_model
from entune.providers.local.contracts import Downloadable
from entune.storage.store import Store


class Learning:
    def __init__(
        self,
        store: Store,
        builds: DictionaryBuilds,
        dictionary: DictionaryFile,
        settings: Settings,
        models: SpeechModels,
        changed: Callable[[], None],
    ) -> None:
        self._store, self._builds, self._dictionary = store, builds, dictionary
        self._settings, self._models, self._changed = settings, models, changed

    def _dictionary_builder(self) -> tuple[str, str, str]:
        model = self._settings.dictionary_model()
        if model is None:
            raise ValueError("Add an Anthropic or OpenAI key under Settings first.")
        provider = model.partition(":")[0]
        api_key = self._settings.key(provider)
        if api_key is None:
            raise ValueError(f"No API key set for {suggestion_model.LLM_PROVIDERS[provider][0]}.")
        return provider, api_key, model

    def start_dictionary_build(
        self,
        source: Source,
        *,
        mode: str,
        scope: str = "new",
        audio_ids: list[str] | None = None,
    ) -> dict[str, object]:
        # Generation and refinement are different jobs; the workflow names which one.
        if mode not in {"generate", "refine"}:
            raise ValueError("Choose generate or refine")
        if scope not in {"new", "all"}:
            raise ValueError("Choose new or all history")
        previous = self._builds.status()
        state = self._builds.start(
            lambda: replace(
                self._build_input(source, scope=scope, audio_ids=audio_ids),
                mode="generate" if mode == "generate" else "refine",
            )
        )
        if previous.get("phase") in {"failed", "cancelled"}:
            self._store.finish_learning(
                str(previous["id"]),
                str(previous["model"]),
                str(previous["source"]),
                (),
                "replaced",
                previous,
                applied=False,
            )
        return state

    def dictionary_build_status(self, job_id: str | None = None) -> dict[str, object]:
        return self._builds.status(job_id)

    def cancel_dictionary_build(self, job_id: str) -> None:
        self._builds.cancel(job_id)

    def discard_dictionary_build(self, job_id: str) -> None:
        state = self._builds.status(job_id)
        self._builds.discard(job_id)
        state.pop("proposal", None)
        self._store.finish_learning(
            job_id, str(state["model"]), str(state["source"]), (), "discarded", state, applied=False
        )

    def retry_dictionary_build(self, job_id: str) -> None:
        def refresh(spec: BuildInput) -> BuildInput:
            with self._dictionary.lock:
                speech_key = spec.speech_key
                if spec.source == "audio" and not isinstance(spec.speech.provider, Downloadable):
                    configured = self._settings.key(spec.speech.provider.id)
                    if configured is None:
                        raise ValueError(f"No API key set for {spec.speech.provider.name}.")
                    speech_key = configured
                return replace(
                    spec,
                    speech_key=speech_key,
                    builder=self._dictionary_builder(),
                    dictionary=dictionary_changes.share(self._dictionary.dictionary(), set()),
                    revision=self._dictionary.dictionary_version(),
                )

        self._builds.retry(job_id, refresh)

    def accept_dictionary_build(self, job_id: str, selected: object = None) -> None:
        def save(proposal: Proposal, spec: BuildInput, covered: tuple[str, ...]) -> bool:
            with self._dictionary.lock:
                if proposal.version.strip('"') != self._dictionary.dictionary_version():
                    raise DictionaryChanged(
                        "The dictionary file changed outside this learning flow. "
                        "Discard this proposal and rebuild from the current file."
                    )
                current = self._dictionary.dictionary()
                updated = dictionary_changes.review(current, proposal, selected)
                applied = updated != current
                state = self._builds.status()
                state["coveredInputIds"] = list(covered)
                path = self._store.data_dir / dictionary_document.FILENAME
                original = path.read_bytes() if path.exists() else None
                if applied:
                    dictionary_document.save(self._store.data_dir, updated)
                try:
                    self._store.finish_learning(
                        job_id,
                        spec.speech.id,
                        spec.source,
                        covered,
                        "applied" if applied else "no_changes",
                        state,
                        applied=applied,
                    )
                except Exception:
                    if applied:
                        if original is None:
                            path.unlink(missing_ok=True)
                        else:
                            temporary = path.with_suffix(".rollback")
                            temporary.write_bytes(original)
                            temporary.replace(path)
                    raise
                if applied:
                    self._changed()
                return applied

        self._builds.accept(job_id, save)

    def _build_input(
        self, source: Source, *, scope: str = "new", audio_ids: list[str] | None = None
    ) -> BuildInput:
        builder = self._dictionary_builder()
        try:
            ref = self._models.choose_model(None)
        except NoDefaultModel as exc:
            raise ValueError("Pick a default model first.") from exc
        except UnknownModel as exc:
            raise ValueError(str(exc)) from exc
        with self._dictionary.lock:
            current = dictionary_changes.share(self._dictionary.dictionary(), set())
            version = self._dictionary.dictionary_version()
        if source == "history":
            inputs = self._store.learning_inputs(
                ref.provider.id, ref.model, scope=scope, limit=batches.MAX_TRANSCRIPTS
            )
            if not inputs:
                raise ValueError(
                    f"No new transcripts to learn from for {ref.label}. To read used ones "
                    "again, turn on Include transcripts already used under Options."
                    if scope == "new"
                    else f"No transcripts to learn from for {ref.label} yet."
                )
            return BuildInput(
                source, ref, "", builder, current, version, tuple(inputs), scope=scope
            )
        speech_key = (
            "" if isinstance(ref.provider, Downloadable) else self._settings.key(ref.provider.id)
        )
        if speech_key is None:
            raise ValueError(f"No API key set for {ref.provider.name}.")
        available = self._store.learning_audio()
        if audio_ids is None:
            # API callers without a selection get imported audio; the page always sends
            # its explicit selection, including normal recordings.
            audio = tuple(
                (item, path) for item, path in available if not item.id.startswith("recording:")
            )
        else:
            if not audio_ids or len(audio_ids) != len(set(audio_ids)):
                raise ValueError("Select one or more distinct saved recordings")
            ids = set(audio_ids)
            audio = tuple((item, path) for item, path in available if item.id in ids)
            if len(audio) != len(ids):
                raise ValueError("Some selected audio is no longer available; reload the selection")
        if not audio:
            raise ValueError("Select saved recordings or import audio first.")
        return BuildInput(
            source, ref, speech_key, builder, current, version, audio=audio, scope="selected"
        )

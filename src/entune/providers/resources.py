"""Own speech resources and give waiting dictation priority between local operations."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError
from contextlib import contextmanager

from entune.providers.contracts import Closeable, Provider
from entune.providers.local.contracts import Downloadable
from entune.providers.registry import ModelRef


class SpeechResources:
    def __init__(self, providers: list[Provider], report_error: Callable[[str], None]) -> None:
        self.providers = providers
        self._report = report_error
        self._condition = threading.Condition()
        self._local_busy = False
        self._foreground_waiters = 0
        self._active = 0
        self._selected: ModelRef | None = None
        self._warm_pending = False
        self._warm_thread: threading.Thread | None = None
        self._close_thread: threading.Thread | None = None
        self._closed = False
        self._cleanup_failed = False

    @contextmanager
    def use(
        self,
        ref: ModelRef | None,
        *,
        background: bool = False,
        cancel: threading.Event | None = None,
    ) -> Iterator[None]:
        # None reserves the local slot for warming/removal. Cloud requests can run
        # concurrently, but their clients cannot close until all leases return.
        local = ref is None or isinstance(ref.provider, Downloadable)
        with self._condition:
            if local and not background:
                self._foreground_waiters += 1
            try:
                while True:
                    if self._closed or (cancel is not None and cancel.is_set()):
                        raise CancelledError()
                    if not local or not (
                        self._local_busy or (background and self._foreground_waiters)
                    ):
                        break
                    self._condition.wait(0.05)
                self._active += 1
                if local:
                    self._local_busy = True
            finally:
                if local and not background:
                    self._foreground_waiters -= 1
        error = None
        try:
            if local and ref is not None:
                self._unload_except(ref)
            yield
        finally:
            try:
                if local:
                    with self._condition:
                        selected = None if self._closed else self._selected
                    self._unload_except(selected)
            except Exception as exc:
                error = f"Model cleanup: {type(exc).__name__}: {exc}"
                self._report(error)
            finally:
                with self._condition:
                    self._active -= 1
                    if local:
                        self._local_busy = False
                    self._condition.notify_all()
            # A failed cleanup must not turn already successful foreground speech
            # into a transcription failure. A background build can fail visibly.
            if error and background:
                raise ValueError(error)

    def _unload_except(self, ref: ModelRef | None) -> None:
        for provider in self.providers:
            if isinstance(provider, Downloadable):
                keep = ref.model if ref is not None and ref.provider is provider else None
                provider.unload(keep=keep)

    def select(self, ref: ModelRef | None) -> None:
        with self._condition:
            if self._closed:
                return
            self._selected = ref
            self._warm_pending = True
            if self._warm_thread is None or not self._warm_thread.is_alive():
                self._warm_thread = threading.Thread(
                    target=self._warm, daemon=True, name="entune-warm"
                )
                self._warm_thread.start()

    @property
    def warming(self) -> bool:
        with self._condition:
            return self._warm_thread is not None and self._warm_thread.is_alive()

    def _warm(self) -> None:
        while True:
            with self._condition:
                if self._closed or not self._warm_pending:
                    self._warm_thread = None
                    return
            try:
                with self.use(None, background=True):
                    with self._condition:
                        ref = self._selected
                        self._warm_pending = False
                    self._unload_except(ref)
                    if ref is not None and isinstance(ref.provider, Downloadable):
                        ref.provider.warm(ref.model)
            except CancelledError:
                return
            except Exception as exc:
                self._report(f"Could not load local model: {exc}")

    def close(self, timeout: float = 2.0) -> bool:
        """Stop new work now; drain in-use resources on an owned cleanup worker."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            if self._close_thread is None:
                self._close_thread = threading.Thread(
                    target=self._cleanup, daemon=True, name="entune-speech-cleanup"
                )
                self._close_thread.start()
            thread = self._close_thread
        thread.join(max(0.0, timeout))
        return not thread.is_alive() and not self._cleanup_failed

    def _cleanup(self) -> None:
        with self._condition:
            while self._active:
                self._condition.wait()
        for provider in self.providers:
            try:
                if isinstance(provider, Closeable):
                    provider.close()
                elif isinstance(provider, Downloadable):
                    provider.unload()
            except Exception as exc:
                self._cleanup_failed = True
                self._report(f"Provider cleanup: {type(exc).__name__}: {exc}")

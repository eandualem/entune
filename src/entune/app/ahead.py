"""Decisions asked while the user still speaks.

Fast mode transcribes each piece of a dictation as it is cut. Each time the transcript
grows, a pass asks the decision model about what is settled in it (jev.Pass): a sentence
once two more follow it, a filler or a dictionary word once enough text follows. The
answers are kept in a memo; after the stop, the final pass reuses them and asks only the
rest, so the wait after the stop covers the last piece instead of the whole dictation.

Passes run one at a time on a thread of their own. A transcript that arrives while one
runs replaces any still waiting, and nothing new starts once the recording stops. A pass
that fails changes nothing: the final pass asks what it did not answer.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import CancelledError

from entune.processing import jev
from entune.providers.contracts import Transcript

# The transcript so far, the pass, and an event set when the passes stop.
Work = Callable[[Transcript, jev.Pass, threading.Event], None]


class Ahead:
    def __init__(self, work: Work) -> None:
        self.memo = jev.Memo()
        self._work = work
        self._changed = threading.Condition()
        self._waiting: Transcript | None = None
        self._stopped = False
        self._halt = threading.Event()  # cancels a pass under way
        self._thread = threading.Thread(target=self._run, daemon=True, name="entune-ahead")
        self._thread.start()

    def update(self, transcript: Transcript) -> None:
        """A longer transcript of the dictation so far."""
        with self._changed:
            if self._stopped:
                return
            if self._waiting is None or len(transcript.text) > len(self._waiting.text):
                self._waiting = transcript
            self._changed.notify()

    def stop(self) -> None:
        """No pass starts after this, and one under way is cancelled: the final pass
        does not wait for it."""
        self._halt.set()
        with self._changed:
            self._stopped = True
            self._waiting = None
            self._changed.notify()

    def _run(self) -> None:
        while True:
            with self._changed:
                while self._waiting is None and not self._stopped:
                    self._changed.wait()
                if self._stopped:
                    return
                transcript, self._waiting = self._waiting, None
            assert transcript is not None
            try:
                self._work(transcript, jev.Pass(self.memo, final=False), self._halt)
            except CancelledError:
                return
            except Exception:
                logging.getLogger(__name__).exception("A pass while speaking failed")

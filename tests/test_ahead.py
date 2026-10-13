import threading
import time
from contextlib import closing
from typing import Any

import httpx

from entune.app.ahead import Ahead
from entune.processing import jev, jev_client
from entune.providers.contracts import Transcript
from tests.test_jev import answering, call

SENTENCES = [f"This is sentence number {n} of the dictation." for n in range(6)]


def asked(requests: list[Any]) -> list[str]:
    return sorted(
        body["state"]["sentences"][name] for body in requests for name in body["questions"]
    )


def test_a_sentence_is_asked_once_two_follow_it_and_never_again() -> None:
    requests, handler = answering(lambda name, _: {"continues": 1.0})
    memo = jev.Memo()
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        early = " ".join(SENTENCES[:4])
        result = jev.format_edits(early, call(client), jev.Pass(memo, final=False))
        assert result == jev.TextResult()
        assert asked(requests) == SENTENCES[:2]  # two more follow the first two only
        requests.clear()
        whole = " ".join(SENTENCES)
        context = call(client)
        jev.format_edits(whole, context, jev.Pass(memo))
        assert asked(requests) == SENTENCES[2:]  # the final pass asks only the rest
        assert context.decisions == 6  # the reused answers count as this step's decisions


def test_a_filler_or_a_word_near_the_end_waits_for_what_follows() -> None:
    requests, handler = answering(lambda name, _: {"hesitation": 1.0})
    memo = jev.Memo()
    text = "Um, " + "we talk about the plan for a while. " * 10 + "Then uh we go."
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        jev.cleanup_edits(text, call(client), jev.Pass(memo, final=False))
        (first,) = requests
        assert list(first["questions"]) == ["F00"]  # the "uh" at the end waits
        requests.clear()
        result = jev.cleanup_edits(text, call(client), jev.Pass(memo))
        assert [list(r["questions"]) for r in requests] == [["F01"]]
        assert len(result.changes) == 2


def test_passes_run_one_at_a_time_on_the_latest_text_and_stop() -> None:
    seen: list[str] = []
    running = threading.Event()
    release = threading.Event()

    def work(transcript: Transcript, run: jev.Pass) -> None:
        assert not run.final and run.memo is ahead.memo
        seen.append(transcript.text)
        running.set()
        release.wait(2)

    ahead = Ahead(work)
    ahead.update(Transcript("one"))
    assert running.wait(2)
    ahead.update(Transcript("one two"))
    ahead.update(Transcript("one two three"))
    ahead.update(Transcript("one two"))  # an older text never replaces a newer one
    release.set()
    deadline = time.monotonic() + 2
    while len(seen) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    ahead.stop()
    ahead.update(Transcript("one two three four"))
    time.sleep(0.05)
    assert seen == ["one", "one two three"]

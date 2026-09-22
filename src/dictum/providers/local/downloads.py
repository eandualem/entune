"""Resumable model-file downloads shared by local engines."""

from __future__ import annotations

import re
import threading
from pathlib import Path

import httpx

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


class Download:
    """A model's files, fetched in turn on a thread; each resumes from its `.part`."""

    def __init__(self, client: httpx.Client, files: list[tuple[str, Path]], size: int) -> None:
        self._client, self._files, self._size = client, files, size
        self.received = sum(_have(target) for _, target in files)
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="dictum-download")

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    @property
    def progress(self) -> float:
        return min(self.received / self._size, 1.0) if self._size else 0.0

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        for url, target in self._files:
            if target.exists():
                continue
            if not self._fetch(url, target):
                return

    def _fetch(self, url: str, target: Path) -> bool:
        part = target.with_name(target.name + ".part")
        have = part.stat().st_size if part.exists() else 0
        headers = {"Accept-Encoding": "identity"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with self._client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416:
                    complete = re.fullmatch(
                        r"bytes \*/(\d+)", response.headers.get("content-range", "")
                    )
                    if complete is None or have == 0 or have != int(complete[1]):
                        raise ValueError(
                            "The server refused the range, but the partial file is not complete"
                        )
                elif response.status_code in (200, 206):
                    expected = None
                    if response.status_code == 206:
                        interval = re.fullmatch(
                            r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("content-range", "")
                        )
                        if (
                            interval is None
                            or int(interval[1]) != have
                            or int(interval[2]) != int(interval[3]) - 1
                        ):
                            raise ValueError("The server returned a different range than requested")
                        expected = int(interval[3])
                    else:
                        self.received -= have
                        have = 0
                        length = response.headers.get("content-length")
                        expected = int(length) if length is not None else None
                    with part.open("ab" if have else "wb") as out:
                        for chunk in response.iter_bytes():
                            out.write(chunk)
                            self.received += len(chunk)
                    if expected is not None and part.stat().st_size != expected:
                        raise ValueError("The download ended before the complete file arrived")
                else:
                    self.error = f"HTTP {response.status_code} from {url}"
                    return False
            part.replace(target)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return False
        return True


def _have(target: Path) -> int:
    if target.exists():
        return target.stat().st_size
    part = target.with_name(target.name + ".part")
    return part.stat().st_size if part.exists() else 0

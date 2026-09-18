"""Runs inside the parakeet-mlx tool environment, not inside Dictum: one JSON request
per line on stdin, one JSON answer per line on stdout. The model is loaded once.

Kept free of Dictum imports on purpose; Dictum starts it with the engine's own
Python (`uv tool install parakeet-mlx`), which has MLX and the model code.
"""

import json
import sys


def main() -> None:
    model = None
    for line in sys.stdin:
        request = json.loads(line)
        answer: dict[str, object]
        try:
            if request["op"] == "load":
                from parakeet_mlx import from_pretrained

                model = from_pretrained(request["model"])
                answer = {"ok": True}
            elif request["op"] == "transcribe":
                if model is None:
                    raise RuntimeError("model not loaded")
                answer = {"text": model.transcribe(request["path"]).text}
            else:
                answer = {"error": f"unknown op {request['op']}"}
        except Exception as exc:  # the answer carries it; the process stays up
            answer = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(answer), flush=True)


if __name__ == "__main__":
    main()

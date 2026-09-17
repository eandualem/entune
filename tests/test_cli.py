import socket

import pytest

from dictum.cli import build_parser, main, port_is_free


def test_port_probe_sees_a_listener() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert not port_is_free(port)
        with pytest.raises(SystemExit, match="already running"):
            main(["--no-menu", "--no-open", "--port", str(port)])
    assert port_is_free(port)


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.port == 4187 and args.data is None and not args.no_menu

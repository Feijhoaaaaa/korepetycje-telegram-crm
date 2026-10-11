from pathlib import Path

import pytest

from app.launcher import SingleInstanceLock, extract_tunnel_url


def test_extracts_quick_tunnel_url() -> None:
    line = "INF Your quick Tunnel has been created! Visit it at https://quiet-river.trycloudflare.com"
    assert extract_tunnel_url(line) == "https://quiet-river.trycloudflare.com"
    assert extract_tunnel_url("starting connector") is None


def test_single_instance_lock(tmp_path: Path) -> None:
    path = tmp_path / "launcher.lock"
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="już uruchomiony"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()

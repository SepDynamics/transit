import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import redis


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


@pytest.fixture
def valkey_url(tmp_path):
    binary = os.environ.get("VALKEY_SERVER_BIN") or shutil_which("valkey-server") or shutil_which("redis-server")
    if not binary:
        pytest.skip("valkey-server or redis-server is required for integration tests")
    port = _reserve_port()
    data_dir = tmp_path / "valkey"
    data_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            binary,
            "--save",
            "",
            "--appendonly",
            "no",
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--dir",
            str(data_dir),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
    )
    url = f"redis://127.0.0.1:{port}/0"
    client = redis.from_url(url, decode_responses=True)
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            if client.ping():
                break
        except redis.RedisError:
            time.sleep(0.1)
    else:
        process.terminate()
        raise RuntimeError("temporary Valkey did not start")
    try:
        yield url
    finally:
        try:
            client.close()
        except Exception:
            pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)

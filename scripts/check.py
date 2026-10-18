"""Compile the board firmware and run all tests against an isolated local FalkorDB."""
import argparse
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from redislite.falkordb_client import FalkorDB

ROOT = Path(__file__).resolve().parents[1]


def run(command, *, cwd=ROOT, env=None):
    subprocess.run(command, cwd=cwd, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Clean firmware objects before rebuilding")
    args = parser.parse_args()
    pio = ["uv", "tool", "run", "--from", "platformio==6.1.18", "pio", "run"]
    print("1/3 Compile T-Display-S3 firmware", flush=True)
    if args.force:
        run([*pio, "--target", "clean"], cwd=ROOT / "firmware")
    run(pio, cwd=ROOT / "firmware")

    print("2/3 Run Python, API and C++ interoperability tests with a temporary FalkorDB", flush=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="purrsona-check-") as directory:
        db = FalkorDB(str(Path(directory) / "tests.rdb"),
                     serverconfig={"port": str(port), "bind": "127.0.0.1"})
        try:
            environment = {**os.environ, "FALKORDB_HOST": "127.0.0.1", "FALKORDB_PORT": str(port),
                           "PURRSONA_USB": "0", "PURRSONA_KEYS_DIR": str(Path(directory) / "keys")}
            run([sys.executable, "-m", "pytest", "-c", "pyproject.toml", "tests", "../firmware/tests", "-q"],
                cwd=ROOT / "api", env=environment)
        finally:
            db.client.shutdown()
    print("3/3 Check patch whitespace", flush=True)
    run(["git", "diff", "--check"])
    print("All checks passed. Physical USB, LCD, button and power-loss checks are separate.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)

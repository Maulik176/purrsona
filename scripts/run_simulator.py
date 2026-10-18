"""Run the actual firmware protocol/display with a desktop screen and isolated demo API."""
import argparse
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "api"), str(ROOT / "firmware/scripts")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--state-dir", type=Path, default=ROOT / "private/board-simulator")
    args = parser.parse_args()
    from build_native import build
    if not (ROOT / "firmware/.pio/libdeps/tdisplay_s3/Crypto").exists():
        subprocess.run(["uv", "tool", "run", "--from", "platformio==6.1.18", "pio", "run"],
                       cwd=ROOT / "firmware", check=True)
    binary = build(ROOT / "firmware/.pio/native-protocol")
    from redislite.falkordb_client import FalkorDB
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="purrsona-simulator-") as directory:
        db = FalkorDB(str(Path(directory) / "graph.rdb"),
                     serverconfig={"port": str(port), "bind": "127.0.0.1"})
        try:
            os.environ.update(FALKORDB_HOST="127.0.0.1", FALKORDB_PORT=str(port), PURRSONA_USB="0",
                              PURRSONA_KEYS_DIR=str(Path(directory) / "unused-virtual-keys"))
            from purrsona.board_simulator import create_demo
            import uvicorn
            print(f"Board simulator: http://127.0.0.1:{args.port}/sim/", flush=True)
            print("Actual C++ protocol and display; simulated USB, flash, button inputs and AI. No model calls.", flush=True)
            uvicorn.run(create_demo(binary, args.state_dir), host="127.0.0.1", port=args.port, access_log=False)
        finally:
            db.client.shutdown()


if __name__ == "__main__":
    main()

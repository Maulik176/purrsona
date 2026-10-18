"""Run the physical USB key with isolated FalkorDB; use --real-ai for Claude replies."""
import argparse
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--real-ai", action="store_true", help="Use Claude with ANTHROPIC_API_KEY from .env")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    if args.real_ai and not os.getenv("ANTHROPIC_API_KEY", "").strip():
        parser.error("--real-ai needs ANTHROPIC_API_KEY in the repo's local .env or environment")
    from redislite.falkordb_client import FalkorDB
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        database_port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="purrsona-hardware-") as directory:
        db = FalkorDB(str(Path(directory) / "graph.rdb"),
                     serverconfig={"port": str(database_port), "bind": "127.0.0.1"})
        try:
            os.environ.update(FALKORDB_HOST="127.0.0.1", FALKORDB_PORT=str(database_port),
                              PURRSONA_USB="1", PURRSONA_HOSTED="0",
                              PURRSONA_KEYS_DIR=str(Path(directory) / "unused-virtual-keys"))
            from purrsona import app as api
            if not args.real_ai:
                # Reuse only AI fixtures, never the simulated board or its public test identity.
                from purrsona.board_simulator import mock_draft, mock_refine, mock_learning
                api.claude = lambda: None
                api.write_draft, api.refine_draft = mock_draft, mock_refine
                api.learning.learn_rules = mock_learning
                api.relationships.resolve = lambda graph, client, text, circles: {
                    "name": api.relationships.normalize(text), "circle": None, "source": "mock"}
            import uvicorn
            mode = "real Claude calls" if args.real_ai else "mocked AI, no model calls"
            print(f"Physical board + real FalkorDB; {mode}.", flush=True)
            print(f"Practice page: http://127.0.0.1:{args.port}/practice/", flush=True)
            print("Press B on the board to unlock. Pair first if the laptop does not trust it yet.", flush=True)
            uvicorn.run(api.app, host="127.0.0.1", port=args.port, access_log=False, timeout_graceful_shutdown=2)
        finally:
            db.client.shutdown()


if __name__ == "__main__":
    main()

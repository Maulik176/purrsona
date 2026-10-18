"""Run the physical USB key with an isolated local FalkorDB and fixed mock AI replies."""
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
    args = parser.parse_args()
    from redislite.falkordb_client import FalkorDB
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        database_port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="purrsona-hardware-") as directory:
        db = FalkorDB(str(Path(directory) / "graph.rdb"),
                     serverconfig={"port": str(database_port), "bind": "127.0.0.1"})
        try:
            os.environ.update(FALKORDB_HOST="127.0.0.1", FALKORDB_PORT=str(database_port),
                              PURRSONA_USB="1", PURRSONA_KEYS_DIR=str(Path(directory) / "unused-virtual-keys"))
            from purrsona import app as api
            # Reuse only AI fixtures, never the simulated board or its public test identity.
            from purrsona.board_simulator import mock_draft, mock_refine, mock_learning
            api.claude = lambda: None
            api.write_draft, api.refine_draft = mock_draft, mock_refine
            api.learning.learn_rules = mock_learning
            api.relationships.resolve = lambda graph, client, text, circles: {
                "name": api.relationships.normalize(text), "circle": None, "source": "mock"}
            import uvicorn
            print("Physical board + real FalkorDB; mocked AI, no model calls.", flush=True)
            print(f"Practice page: http://127.0.0.1:{args.port}/practice/", flush=True)
            print("Press B on the board to unlock. Pair first if the laptop does not trust it yet.", flush=True)
            uvicorn.run(api.app, host="127.0.0.1", port=args.port, access_log=False)
        finally:
            db.client.shutdown()


if __name__ == "__main__":
    main()

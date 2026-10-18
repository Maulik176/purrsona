"""FalkorDB connection helpers. One graph per memory key: voice:<key_id>."""
import os

from falkordb import FalkorDB


_embedded = None  # the hosted demo's own FalkorDB, started inside the container


def connect() -> FalkorDB:
    path = os.getenv("PURRSONA_EMBEDDED_DB")  # hosted demo: run FalkorDB in this process's container
    if path:
        global _embedded
        from redislite.falkordb_client import FalkorDB as Embedded
        port = int(os.getenv("FALKORDB_PORT", "6390"))
        _embedded = _embedded or Embedded(path, serverconfig={"port": str(port), "bind": "127.0.0.1"})
        return FalkorDB(host="127.0.0.1", port=port)
    return FalkorDB(
        host=os.getenv("FALKORDB_HOST", "localhost"),
        port=int(os.getenv("FALKORDB_PORT", "6380")),
    )


def graph_name(key_id: str) -> str:
    return f"voice:{key_id}"


def rows(result) -> list[dict]:
    """Turn a FalkorDB result into a list of {column: value} dicts."""
    cols = [c[1] if isinstance(c, (list, tuple)) else c for c in result.header]
    return [dict(zip(cols, r)) for r in result.result_set]

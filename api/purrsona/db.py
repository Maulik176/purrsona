"""FalkorDB connection helpers. One graph per memory key: voice:<key_id>."""
import os

from falkordb import FalkorDB


def connect() -> FalkorDB:
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

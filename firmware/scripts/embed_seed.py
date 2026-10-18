"""Embed the existing synthetic seed; firmware writes it only when no snapshot exists."""
import json
from pathlib import Path

Import("env")

project = Path(env["PROJECT_DIR"])
snapshot = json.loads((project.parent / "data/seed/demo-maulik.json").read_text())
payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
if len(payload.encode()) > 60_000:
    raise ValueError("Demo snapshot is too large for the USB protocol")
generated = Path(env.subst("$BUILD_DIR")) / "generated"
generated.mkdir(parents=True, exist_ok=True)
(generated / "demo_seed.h").write_text(
    '#pragma once\nstatic const char DEMO_SEED[] = R"purrsona(' + payload + ')purrsona";\n'
)
env.Append(CPPPATH=[str(generated)])

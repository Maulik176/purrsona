"""Exercise the real board loop with a clock that advances while a request is decoded."""
import subprocess
from pathlib import Path


def test_reply_deadline_handles_processing_time_clock_wrap_and_backpressure(tmp_path):
    tests = Path(__file__).parent
    for header in ("Arduino.h", "LittleFS.h", "display.h", "flash_storage.h"):
        (tmp_path / header).write_text('#include "loop_fakes.h"\n')
    binary = tmp_path / "main-loop"
    subprocess.run(["c++", "-std=c++17", "-I", str(tmp_path), "-I", str(tests),
                    str(tests / "loop_main.cpp"), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=5)

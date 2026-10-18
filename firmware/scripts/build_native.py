"""Build the firmware protocol on the laptop for interoperability tests (after pio run)."""
import shutil
import subprocess
from pathlib import Path


def build(output: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    libraries = root / ".pio/libdeps/tdisplay_s3"
    crypto = libraries / "Crypto"
    if not crypto.exists():
        raise RuntimeError("Run the firmware PlatformIO build first to install the pinned libraries")
    compiler = shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        raise RuntimeError("Install a C++ compiler to run firmware interoperability tests")
    sources = ["Ed25519", "Curve25519", "BigNumberUtil", "SHA512", "SHA256", "Hash", "Crypto"]
    command = [compiler, "-std=c++17", "-O1", "-Wall", "-Wextra",
               "-I", str(root / "simulator"), "-I", str(root / "include"),
               "-I", str(libraries / "TFT_eSPI"), "-I", str(libraries / "ArduinoJson/src"), "-I", str(crypto),
               str(root / "tests/native_main.cpp"), str(root / "src/key_device.cpp"), str(root / "src/display.cpp"),
               *(str(crypto / f"{source}.cpp") for source in sources), "-o", str(output)]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return output


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(build(root / ".pio/native-protocol"))

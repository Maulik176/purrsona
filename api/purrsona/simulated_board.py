"""Desktop-only adapter for the actual C++ firmware; never used by the USB device path."""
import json
import queue
import struct
import subprocess
import threading
import time
import zlib
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .key_protocol import to_base64
from .usb_key import UsbKey

ROOT = Path(__file__).resolve().parents[2]
PORT = "simulated-tdisplay-s3"
# Public test identity, isolated from the physical device's trust directory.
TEST_PUBLIC_KEY = to_base64(Ed25519PrivateKey.from_private_bytes(bytes(range(32))).public_key().public_bytes_raw())


def screen_png(frame: dict) -> bytes:
    pixels = bytes.fromhex(frame["pixels"])
    if len(pixels) != 320 * 170 * 2:
        raise ValueError("Incomplete simulated screen")
    rgb = bytearray()
    for y in range(170):
        rgb.append(0)
        for x in range(320):
            offset = (y * 320 + x) * 2
            color = (pixels[offset] << 8) | pixels[offset + 1]
            rgb.extend((((color >> 11) & 31) * 255 // 31,
                        ((color >> 5) & 63) * 255 // 63, (color & 31) * 255 // 31))

    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 320, 170, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rgb)) + chunk(b"IEND", b""))


class SimulatedBoard:
    def __init__(self, binary: Path, directory: Path, *, realtime=True):
        self.binary, self.directory, self.realtime = binary, directory, realtime
        self.connected = False
        self.drop_save_ack = False
        self.condition = threading.Condition()
        self.writer = threading.Lock()
        self.controls = threading.Lock()
        self.frames = queue.Queue()
        self.received = bytearray()
        self._start()

    def _start(self):
        self.stopped = threading.Event()
        self.process = subprocess.Popen([str(self.binary), str(ROOT / "data/seed/demo-maulik.json"), str(self.directory)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.threads = [threading.Thread(target=self._receive, daemon=True)]
        if self.realtime:
            self.threads.append(threading.Thread(target=self._tick, daemon=True))
        for thread in self.threads:
            thread.start()

    def _receive(self):
        for line in self.process.stdout:
            message = json.loads(line)
            if message.get("simulator") == "frame":
                self.frames.put(message)
                continue
            with self.condition:
                if self.drop_save_ack and message.get("type") == "ACK" and "event_id" in message:
                    self.drop_save_ack = False
                    self.connected = False
                else:
                    self.received.extend(line)
                self.condition.notify_all()
        with self.condition:
            self.condition.notify_all()

    def _tick(self):
        previous = time.monotonic()
        while not self.stopped.wait(0.08):
            now = time.monotonic()
            self.command(f"!tick {max(1, int((now - previous) * 1000))}")
            previous = now

    def send(self, data):
        with self.writer:
            if self.stopped.is_set() or self.process.poll() is not None:
                raise OSError("Simulated board is off")
            self.process.stdin.write(data)
            self.process.stdin.flush()

    def command(self, value):
        self.send(value.encode() + b"\n")

    def frame(self):
        with self.controls:
            self.command("!frame")
            return self.frames.get(timeout=3)

    def ports(self):
        return [PORT] if self.connected else []

    def connect(self):
        with self.condition:
            self.received.clear()
            self.connected = True

    def disconnect(self):
        with self.condition:
            self.connected = False
            self.received.clear()
            self.condition.notify_all()
        self.command("!disconnect")

    def client(self, port):
        return UsbKey(port, transport=SimulatedSerial(self))

    def power_cycle(self):
        self.disconnect()
        self.close()
        self.frames = queue.Queue()
        self._start()

    def close(self):
        self.stopped.set()
        with self.writer:
            if self.process.poll() is None:
                self.process.stdin.close()
        self.process.wait(timeout=3)
        for thread in self.threads:
            thread.join(timeout=3)
        self.process.stdout.close()


class SimulatedSerial:
    def __init__(self, board):
        self.board, self.closed = board, False
        self.pending = bytearray()

    def write(self, data):
        if self.closed or not self.board.connected:
            raise OSError("Simulated cable disconnected")
        # Exercise framing with chunks smaller than either endpoint's frame buffer.
        part = data[:37]
        self.pending.extend(part)
        # Keep simulator controls out of partial protocol lines. The C++ adapter
        # feeds each complete line to the firmware in 17-byte fragments.
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending[:] = rest
            self.board.send(line + b"\n")
        return len(part)

    def read(self, size):
        with self.board.condition:
            if not self.board.received and not self.closed and self.board.connected:
                self.board.condition.wait(0.1)
            if self.closed or not self.board.connected or self.board.process.poll() is not None:
                raise OSError("Simulated cable disconnected")
            data = bytes(self.board.received[:min(size, 53)])
            del self.board.received[:len(data)]
            return data

    def close(self):
        self.closed = True
        with self.board.condition:
            self.board.condition.notify_all()

    cancel_read = close
    cancel_write = close

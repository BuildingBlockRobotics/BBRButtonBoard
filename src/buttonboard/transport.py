"""Transports for the config channel.

The line protocol is transport-independent (§9.8): the same byte stream runs
over CDC today and over the vendor HID interface once Phase 0.1 promotes it.
A transport is anything with `start(on_line)`, `write(text)` and `close()`;
`Board` never imports pyserial itself.
"""
from __future__ import annotations

import threading
import time

from .errors import BoardError, NoBoardFound, NotConnected


def _pyserial():
    """pyserial is only needed for the CDC transport; the mock and the tests
    run without it. Keep the missing-dependency message actionable."""
    try:
        import serial
        return serial
    except ImportError:
        raise BoardError("needs pyserial:  python3 -m pip install pyserial")

VID, PID = 0x1209, 0x0001          # §8.4, still the pid.codes test PID


def find_port():
    """Prefer our VID:PID, then the product string, then a lone USB CDC port."""
    _pyserial()
    from serial.tools import list_ports

    ports = list(list_ports.comports())
    for p in ports:
        if p.vid == VID and p.pid == PID:
            return p.device
    for p in ports:
        # Substring, not equality: iProduct is "BBR Button Board" (PROTOCOL.md
        # rev 0.19) and this still finds a board on pre-0.19 firmware.
        if p.product and "Button Board" in p.product:
            return p.device
    usb = [p for p in ports if "usbmodem" in p.device or "ACM" in p.device
           or (p.vid is not None and "COM" in p.device)]
    if len(usb) == 1:
        return usb[0].device
    if not ports:
        raise NoBoardFound("no serial ports found -- is the board plugged in?")
    raise NoBoardFound("could not identify the board; pass port=...:\n  " +
                       "\n  ".join("%s  %s" % (p.device, p.description) for p in ports))


class SerialTransport:
    """CDC transport over pyserial, with a reader thread.

    Two things a generic terminal gets wrong and this does not: lines end in
    `\\n` (a bare `\\r` is ignored, §9.1), and DTR is asserted for the whole
    session because dropping it ends test and bind mode (§9.10). Baud is
    ignored by the device; the value here is only what pyserial insists on.
    """

    def __init__(self, port=None, baud=115200, settle=0.3):
        serial = _pyserial()
        self.port = port or find_port()
        try:
            self._s = serial.Serial(self.port, baud, timeout=0.1)
        except serial.SerialException as e:
            raise NotConnected("could not open %s: %s" % (self.port, e)) from e
        self._s.dtr = True
        time.sleep(settle)                              # let the host settle
        self._s.reset_input_buffer()
        self._stop = threading.Event()
        self._thread = None

    def start(self, on_line):
        self._thread = threading.Thread(target=self._read, args=(on_line,),
                                        name="bb-reader", daemon=True)
        self._thread.start()

    def _read(self, on_line):
        buf = b""
        while not self._stop.is_set():
            try:
                buf += self._s.read(256)
            except Exception:                           # port yanked mid-read
                self._stop.set()
                break
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode("utf-8", "replace").strip()
                if text:
                    on_line(text)

    def write(self, text):
        if self._stop.is_set():
            raise NotConnected("transport is closed")
        try:
            self._s.write(text.encode())
            self._s.flush()
        except Exception as e:
            raise NotConnected("write to %s failed: %s" % (self.port, e)) from e

    def close(self):
        # Closing drops DTR, which ends test and bind mode by design (§9.10).
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        try:
            self._s.close()
        except Exception:
            pass


class LoopTransport:
    """In-process transport driven by a callable, for tests and the mock board.

    `handler(line) -> iterable of str` is called on the writer's thread; each
    string it yields is delivered as a received line.
    """

    def __init__(self, handler):
        self._handler = handler
        self._on_line = None
        self.written = []

    def start(self, on_line):
        self._on_line = on_line

    def write(self, text):
        for line in text.split("\n"):
            if not line:
                continue
            self.written.append(line)
            for reply in self._handler(line) or ():
                self._on_line(reply)

    def inject(self, line):
        """Deliver an unsolicited event line (§9.3)."""
        self._on_line(line)

    def close(self):
        pass

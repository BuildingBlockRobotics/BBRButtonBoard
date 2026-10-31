#!/usr/bin/env python3
"""Self-test for the buttonboard package. No hardware, no pytest:

    python3 tools/test_buttonboard.py     # tests/ in the public repository
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from buttonboard import CommandError, MockBoard, attr, rgb      # noqa: E402
from buttonboard import picture_bytes                           # noqa: E402
from buttonboard.display import (ascii_text, is_color, slot_chunks,  # noqa: E402
                                 slot_size)

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s\n    got:  %r\n    want: %r" % (name, got, want))


def check_raises(name, exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return
    except Exception as e:
        fails.append("%s: raised %r, wanted %s" % (name, e, exc.__name__))
        return
    fails.append("%s: nothing raised, wanted %s" % (name, exc.__name__))


# ---- colours (§9.2 wants 6 hex digits or `off`) -----------------------------
check("rgb name", rgb("red"), "FF0000")
check("rgb hash", rgb("#0040ff"), "0040FF")
check("rgb bare", rgb("0040ff"), "0040FF")
check("rgb tuple", rgb((0, 64, 255)), "0040FF")
check("rgb int", rgb(0x0040FF), "0040FF")
check("rgb none", rgb(None), "off")
check("rgb off", rgb("off"), "off")
check_raises("rgb bad", ValueError, rgb, "nope")
check_raises("rgb bool", ValueError, rgb, True)
check_raises("rgb range", ValueError, rgb, (0, 300, 0))

# ---- command formatting -----------------------------------------------------
board = MockBoard()
check("hello", board.hello()["product"], "BBR Button Board")
board.led(3, "red")
check("led", board.state.sent[-1], "led 3 FF0000")
board.led(7, (255, 136, 0), fx="blink", press=False)
check("led fx press", board.state.sent[-1], "led 7 FF8800 blink off")
# `press` is positional after `fx`, so it cannot be sent alone: §9.2's grammar
# is `led <btn> <rgb> [fx] [press]`.
board.led(7, "off", press=True)
check("led press implies fx", board.state.sent[-1], "led 7 off solid on")
board.led_tile(1, 9, "200020")
check("led tile", board.state.sent[-1], "led tile 1 9 200020")
board.led_all("off")
check("led all", board.state.sent[-1], "led all off")
board.led_reset()
check("led reset", board.state.sent[-1], "led reset")
check_raises("btn range", ValueError, board.led, 33, "red")
check_raises("addr range", ValueError, board.led_tile, 32, 0, "red")
check_raises("fx name", ValueError, board.led, 1, "red", "strobe")

board.test("walk", 250)
check("test walk", board.state.sent[-1], "test walk 250")
board.map_set({"v": 1, "bind": [{"btn": 1, "addr": 1, "key": 0}]})
check("map set", board.state.sent[-1],
      'map set {"v":1,"bind":[{"btn":1,"addr":1,"key":0}]}')
check("map get", board.map_get()["bind"][0]["btn"], 1)
board.unbind(4, 1, 2)
check("unbind one", board.state.sent[-1], "unbind 4 1 2")
check_raises("line limit", ValueError, board.command, "x" * 4096)

# ---- errors -----------------------------------------------------------------
try:
    board.command("frobnicate")
    fails.append("unknown command did not raise")
except CommandError as e:
    check("err code", e.err, "unknown")

# ---- LED layer diffing (§13.5: only changes go on the bus) ------------------
board = MockBoard()
layer = board.led_layer()
layer.set(1, "red").set(2, "off")
check("layer first flush", layer.flush(), 2)
layer.set(1, "red").set(2, "off")
check("layer no change", layer.flush(), 0)
layer.set(1, "blue")
check("layer one change", layer.flush(), 1)
check("layer sent", board.state.leds[("btn", 1)], "led 1 0000FF")
layer.forget()
layer.set(1, "blue")
check("layer after forget", layer.flush(), 1)

# ---- display (§14.4) --------------------------------------------------------
check("ascii clip", ascii_text("hello world, this is long", cols=21),
      "hello world, this is ")
check("ascii sanitise", ascii_text("a\tb\né"), "a?b??")

board = MockBoard()
screen = board.screen(4)                       # tile 4 reports cols/rows
check("screen geometry", (screen.cols, screen.rows), (21, 4))
screen.text(0, "AUTO 12.4s")
screen.bar(3, 0, 21, 128)
check("screen first flush", screen.flush(), 2)
check("show sent", board.state.sent[-1], "disp 4 show")
check("screen committed", board.state.screen_text(4),
      ["AUTO 12.4s           ", "bar 0 21 128"])
screen.text(0, "AUTO 12.4s")
check("screen no change", screen.flush(), 0)
screen.text(0, "TELEOP 2.0s")
check("screen one change", screen.flush(), 1)
check("row padded to erase", board.state.screen_text(4)[0], "TELEOP 2.0s          ")
check_raises("row range", ValueError, screen.text, 4, "x")

# Geometry falls back to the §14.1 panel when the tile reports none.
check("screen default geometry", (board.screen(1).cols, board.screen(1).rows), (21, 4))

# Long form: column and attributes, and the contrast op.
board.disp_text(4, 1, "GO", col=3, attr=3)
check("disp long form", board.state.sent[-1], "disp 4 text 1 3 3 GO")
board.disp_contrast(4, 64)
check("disp contrast", board.state.sent[-1], "disp 4 contrast 64")
check("contrast recorded", board.state.contrast[4], 64)

# ---- colour (§14.7) ---------------------------------------------------------
# Index 0 is "default", so an unstyled write is still ATTR 0 on a colour tile.
check("attr default", attr(), 0)
check("attr mono bits", (attr(inverse=True), attr(double=True)), (1, 2))
check("attr fg", attr("red"), 1 << 2)
check("attr fg and bg", attr("red on white"), (1 << 2) | (7 << 5))
# Black paper is index 0's own resolution, so naming it changes nothing.
check("attr black bg", attr("red on black"), attr("red"))
# Black ink has no index: it is the background field swapped in by inverse.
check("attr black ink", attr("black on yellow"), (3 << 2) | 1)
check("attr keywords match the spec", attr(fg="black", bg="yellow"),
      attr("black on yellow"))
# `inverse` on top of a colour means what it says: it swaps what you asked for.
check("attr inverse swaps", attr("black on yellow inverse"), 3 << 2)
check("attr double", attr("white on blue double"), (4 << 5) | 2)
# White in the foreground *is* index 0 (§14.7), so the two plainest styles set
# no colour bit at all and still reach a mono panel.
check("white foreground is the default index",
      (attr("white on black"), attr("black on white")), (0, 1))
check("plain styles stay mono-safe",
      [is_color(attr(x)) for x in ("white on black", "black on white")],
      [False, False])
check("attr int passes through", attr(12), 12)
check("is_color", [is_color(attr(a)) for a in (None, 3, "red", "black on red")],
      [False, False, True, True])
check_raises("black on black", ValueError, attr, "black")
check_raises("unknown colour", ValueError, attr, "puce")
check_raises("spec and keywords", ValueError, attr, "red", fg="blue")
check_raises("attr out of range", ValueError, attr, 256)

board = MockBoard()
# Tile 5 is the colour screenkey1: 10 x 8, so geometry comes from the tile.
sk = board.screen(5)
check("colour screen geometry", (sk.cols, sk.rows), (10, 8))
sk.text(0, "ABORT", fg="black", bg="red")
check("colour flush", sk.flush(), 1)
check("colour line", board.state.sent[-2], "disp 5 text 0 0 5 ABORT     ")
check("colour attr recorded", board.state.attrs[5][0], 5)
# Same text, same style: nothing goes out.
sk.text(0, "ABORT", fg="black", bg="red")
check("colour no change", sk.flush(), 0)
# Same text, different style: the row is redrawn.
sk.text(0, "ABORT", fg="red")
check("style change redraws", sk.flush(), 1)
check("style change line", board.state.sent[-2], "disp 5 text 0 0 4 ABORT     ")

board.disp_text(5, 1, "GO", fg="green")
check("disp_text colour", board.state.sent[-1], "disp 5 text 1 0 8 GO")
board.disp_text(5, 2, "OK")
check("disp_text plain stays short form", board.state.sent[-1], "disp 5 2 OK")

# A mono panel refuses colour rather than drawing white (§14.7). The client
# says so from `caps` before anything reaches the wire...
check_raises("colour to a mono tile", ValueError, board.disp_text, 4, 0, "GO",
             fg="red")
# The only thing that went out was the `tiles` that read its capabilities.
check("nothing drawn on the mono tile", board.state.sent[-1], "tiles")
# ...and the board refuses it too, the way §14.4 says it does.
check_raises("board refuses colour to a mono tile", CommandError,
             board.command, "disp 4 text 0 0 4 GO")
# Inverse and double are not colour, and a mono tile still takes them.
board.disp_text(4, 0, "GO", inverse=True)
check("mono inverse still works", board.state.sent[-1], "disp 4 text 0 0 1 GO")

# The Screen knows the panel from the same descriptor it took its geometry
# from, so it refuses at the row rather than mid-flush.
mono = board.screen(4)
check("screen knows the panel", (mono.color, sk.color), (False, True))
check_raises("colour on a mono screen", ValueError, mono.text, 0, "GO", "red")
mono.text(0, "GO", inverse=True)
check("mono screen still takes inverse", mono.flush(), 1)

# A board older than §14 answers `unknown`.
old = MockBoard(display=False)
check_raises("disp unimplemented", CommandError, old.disp_clear, 4)

# ---- sound (§16) ------------------------------------------------------------
board = MockBoard()
board.sound(3, 880, 200)
check("sound one note", board.state.sent[-1], "sound 3 880 200")
board.sound_play(3, [(880, 100), (0, 50), (880, 100)])
check("sound sequence", board.state.sent[-1], "sound 3 play 880:100,0:50,880:100")
board.sound_play(3, [(660, 80)], repeat=3)
check("sound repeat", board.state.sent[-1], "sound 3 play 660:80 repeat 3")
board.sound_play(3, "error")
check("sound named tune", board.state.sent[-1],
      "sound 3 play 2700:120,0:60,1400:240 repeat 1")
board.sound_play(3, "alarm")
check("alarm repeats forever", board.state.sent[-1].endswith("repeat 0"), True)
board.sound_stop(3)
check("sound stop", board.state.sent[-1], "sound 3 stop")
board.sound_stop()
check("sound stop all", board.state.sent[-1], "sound all stop")
board.sound_volume(3, 128)
check("sound volume", board.state.sent[-1], "sound 3 volume 128")

# §16.3 bounds: a rest is the only frequency allowed outside 100-10000 Hz.
board.sound_play(3, [(0, 50)])
check("rest allowed", board.state.sent[-1], "sound 3 play 0:50")
check_raises("freq low", ValueError, board.sound, 3, 50, 100)
check_raises("freq high", ValueError, board.sound, 3, 12000, 100)
check_raises("zero duration", ValueError, board.sound, 3, 880, 0)
check_raises("duration over 2.55 s", ValueError, board.sound, 3, 880, 3000)
check_raises("too many notes", ValueError, board.sound_play, 3, [(880, 10)] * 24)
check_raises("empty sequence", ValueError, board.sound_play, 3, [])

# What today's firmware does with `sound`.
today = MockBoard(sound=False)
check_raises("sound unimplemented", CommandError, today.sound, 3, 880, 100)

# ---- events (§9.3) ----------------------------------------------------------
board = MockBoard()
seen = []
off = board.on("tile", seen.append)
board.inject({"ev": "tile", "addr": 3, "state": "lost"})
check("event delivered", seen, [{"ev": "tile", "addr": 3, "state": "lost"}])
off()
board.inject({"ev": "tile", "addr": 3, "state": "online"})
check("unsubscribed", len(seen), 1)


# ---- the serial reader never waits out its timeout on a short reply ----------
from buttonboard.transport import SerialTransport              # noqa: E402


class _Port(object):
    """pyserial's read(n) blocks until n bytes or the timeout. This one
    records a read that would have blocked with data already waiting."""

    def __init__(self, data, stop):
        self.data, self.stop, self.stalls = bytearray(data), stop, 0

    @property
    def in_waiting(self):
        return len(self.data)

    def read(self, n):
        if not self.data:
            self.stop.set()
            return b""
        if n > len(self.data):
            self.stalls += 1            # the real port would sit out its timeout
        out = bytes(self.data[:n])
        del self.data[:n]
        return out


_t = SerialTransport.__new__(SerialTransport)
_t._stop = __import__("threading").Event()
_t._s = _Port(b'{"ok":true}\n{"ev":"tile"}\n', _t._stop)
_lines = []
_t._read(_lines.append)
check("reader delivers both lines", _lines, ['{"ok":true}', '{"ev":"tile"}'])
check("reader never asks for more than is waiting", _t._s.stalls, 0)

# ---- firmware transfer (§9.4) ---------------------------------------------
from buttonboard import crc16_ccitt_false                       # noqa: E402

# The CCITT-FALSE check value, the one vector every implementation agrees on.
check("crc16 check value", crc16_ccitt_false(b"123456789"), 0x29B1)

image = bytes((i * 7 + 3) & 0xFF for i in range(3796))          # a real app's size
board = MockBoard()
done = []
result = board.flash(1, image, on_progress=lambda e: done.append(e["done"]))
sent = list(board.state.sent)                                   # before any later command
check("flash ok", result["ok"], True)
check("flash reports the new version", result["fw"], "0.1.1")
check("image arrived intact", board.state.flashed[1], image)
check("tile now reports the new version", board.tile(1)["fw"], "0.1.1")
check("command line", sent[0],
      "flash 1 3796 %04X" % crc16_ccitt_false(image))
chunks = sent[1:]
check("chunk count", len(chunks), 60)                           # 59 x 64 + 20
check("full chunk is 64 bytes of hex", len(chunks[0]), 128)
check("last chunk is the remainder", len(chunks[-1]), 40)
check("progress per chunk", done, list(range(64, 3796, 64)) + [3796])

# A short image still ends on a terminal line, not on the chunk loop.
board = MockBoard()
check("single short chunk", board.flash(1, b"\x01\x02\x03")["ok"], True)

# Refused before a byte goes out, so a typo cannot half-write a tile.
board = MockBoard()
check_raises("empty image", ValueError, board.flash, 1, b"")
check("nothing sent for an empty image", board.state.sent, [])
check_raises("bad address", ValueError, board.flash, 99, image)

board = MockBoard()
check_raises("unknown tile", CommandError, board.flash, 9, image)

# A VERIFY mismatch must raise, and carry both CRCs (§9.4).
board = MockBoard()
_chunk = board.state._flash_chunk
board.state._flash_chunk = lambda line: _chunk(
    "FF" + line[2:] if board.state.flash["done"] == 0 else line)
try:
    board.flash(1, image)
    fails.append("verify mismatch did not raise")
except CommandError as e:
    check("verify error", e.err, "verify")
    check("verify expected", e.response["expected"], "%04X" % crc16_ccitt_false(image))
    check("verify reports a different actual", e.response["expected"] != e.response["actual"], True)


# ---- stored pictures (§14.9) ------------------------------------------------
board = MockBoard()
check("slot count from the descriptor", board.disp_slots(5), 32)
check("a tile with no store reports none", board.disp_slots(4), 0)
check("slot sizes", (slot_size("1bpp", 128, 128), slot_size("rgb565", 128, 128)),
      (2048, 32768))

picture = bytes((i * 7) & 0xFF for i in range(2048))
seen = []
board.disp_slot_store(5, 1, picture, "1bpp", fg="black", bg="red",
                      on_progress=lambda done, total: seen.append(done))
check("every chunk reported", (len(seen), seen[-1]), (32, 2048))
check("committed", board.state.slots[(5, 1)]["data"], picture)
# The mock NAKs the first chunk after BEGIN the way the picture flash does,
# so the client must have re-sent it: `busy` is backpressure, not a failure.
check("busy was retried, not raised",
      board.state.sent.count("disp 5 slot 1 data 0 " + picture[:64].hex().upper()), 2)

# INFO and READ hand a slot back (§14.9 rev 0.35).
check("info on an empty slot", board.disp_slot_info(5, 0), None)
info = board.disp_slot_info(5, 1)
check("info is the header", (info["fmt"], info["len"], info["crc"]),
      ("1bpp", 2048, crc16_ccitt_false(picture)))
reads = []
back = board.disp_slot_read(5, 1, on_progress=lambda done, total: reads.append(done))
check("every byte comes back", back["data"], picture)
check("16 bytes a read", (len(reads), reads[0], reads[-1]), (128, 16, 2048))
check("read of an empty slot", board.disp_slot_read(5, 0), None)

board.disp_slot_show(5, 1)
check("a shown slot owns the panel", board.state.showing, {5: 1})
board.disp_clear(5)
check("clear takes the panel back", board.state.showing, {})

# Writes are strictly ascending and verified (§14.9).
board.disp_slot_begin(5, 2, "1bpp", 0)
board.disp_slot_data(5, 2, 0, b"\xaa\xbb")
board.disp_slot_data(5, 2, 0, b"\xaa\xbb")          # a repeat is discarded
check_raises("a gap is refused", CommandError, board.disp_slot_data, 5, 2, 64, b"\x01")
board.disp_slot_data(5, 2, 2, b"\xcc")
check_raises("a wrong CRC is refused", CommandError,
             board.disp_slot_end, 5, 2, 0)          # `flash`, §9.2
# The header is written last, so a failed commit leaves the slot EMPTY.
check("an uncommitted slot holds nothing", (5, 2) in board.state.slots, False)
check_raises("show on an empty slot", CommandError, board.disp_slot_show, 5, 2)
board.disp_slot_begin(5, 2, "1bpp", 0)
check_raises("info mid-transfer is refused", CommandError, board.disp_slot_info, 5, 2)
board.disp_slot_erase(5, 2)
check_raises("a read over 16 bytes never reaches the tile", CommandError,
             board.command, "disp 5 slot 1 read 0 17")

# An interrupted store is the same story: BEGIN erases, and nothing replaces it.
board.disp_slot_store(5, 3, picture, "1bpp")
check("slot 3 filled", (5, 3) in board.state.slots, True)
board.disp_slot_begin(5, 3, "1bpp", 0)
check("and emptied again by the next BEGIN", (5, 3) in board.state.slots, False)
board.disp_slot_erase(5, 3)

# RGB565 carries its colour in the pixels, so its ATTR must be zero (§14.9).
check_raises("rgb565 with an attribute", ValueError,
             board.disp_slot_begin, 5, 0, "rgb565", "red")
check_raises("an unknown format", ValueError, board.disp_slot_begin, 5, 0, "rgb888")
check_raises("a tile with no store", CommandError, board.disp_slot_begin, 4, 0, "1bpp")

# Chunking: ascending, contiguous, 64 bytes at a time.
chunks = list(slot_chunks(bytes(200)))
check("chunk offsets", [off for off, _ in chunks], [0, 64, 128, 192])
check("last chunk is the remainder", len(chunks[-1][1]) // 2, 8)

# The encoders, which are what a legend is actually made of. Pillow only.
try:
    from PIL import Image
except ImportError:
    print("note: Pillow missing, skipping the picture encoders")
else:
    red = Image.new("RGB", (128, 128), (255, 0, 0))
    colour = picture_bytes(red, 128, 128, "rgb565")
    check("rgb565 is two bytes a pixel", len(colour), 32768)
    check("red is big-endian F800", colour[:2], b"\xf8\x00")
    mono = picture_bytes(red, 128, 128, "1bpp")
    check("1 bpp is 2 KB", len(mono), 2048)
    # A set bit is ink: black dithers to ink everywhere, white to paper.
    # (A mid-tone like that red dithers to a PATTERN, which is the point of
    # dithering and the reason the preview shows the wire bytes.)
    black = picture_bytes(Image.new("RGB", (128, 128), (0, 0, 0)), 128, 128, "1bpp")
    check("a set bit is ink", black[:2], b"\xff\xff")
    check("and a clear bit is paper",
          picture_bytes(Image.new("RGB", (128, 128), (255, 255, 255)), 128, 128, "1bpp")[:2],
          b"\x00\x00")
    check("invert swaps them",
          picture_bytes(Image.new("RGB", (128, 128), (0, 0, 0)), 128, 128, "1bpp",
                        invert=True)[:2], b"\x00\x00")
    # Letterboxed rather than cropped: a wide picture leaves paper above and
    # below rather than losing its ends.
    wide = picture_bytes(Image.new("RGB", (128, 32), (255, 0, 0)), 128, 128, "rgb565",
                         paper="black")
    check("letterbox bars are paper", wide[:2], b"\x00\x00")
    check("and the middle is the picture", wide[(64 * 128 + 64) * 2:(64 * 128 + 64) * 2 + 2],
          b"\xf8\x00")

# ---- rumble as a control channel (§8.8) -------------------------------------
import threading as _threading                                  # noqa: E402
from buttonboard import Timeout, rumble                         # noqa: E402

check("255 / CODE_MAX is CODE_STEP exactly, with no remainder",
      (255 // rumble.CODE_MAX, 255 % rumble.CODE_MAX), (rumble.CODE_STEP, 0))

# Every code round-trips, and sits in the middle of a +-8 band: nothing is one
# rounding away from being read as its neighbour.
check("every code round-trips",
      [rumble.code_of(rumble.wire_byte(c)) for c in range(1, 16)], list(range(1, 16)))
check("the top code is the full motor", rumble.wire_byte(15), 255)
check("the SDK argument is c/15", rumble.rumble_value(8), 8 / 15.0)
check("and it scales to the wire byte",
      round(rumble.rumble_value(8) * 255), rumble.wire_byte(8))
check("+-8 either side still decodes",
      [rumble.code_of(136 + d) for d in (-8, -1, 0, 1, 8)], [8] * 5)
check("a stop is code 0", rumble.code_of(0), 0)
check_raises("a value is a byte", ValueError, rumble.code_of, 256)

# Code 0 is never a binding: rumble(0,0,ms) is how the SDK stops a rumble.
check_raises("code 0", ValueError, rumble.check_code, 0)
check_raises("code 16", ValueError, rumble.check_code, 16)
check_raises("code -1", ValueError, rumble.check_code, -1)
check_raises("code words", ValueError, rumble.check_code, "one")
check("a code may arrive as a string, as it does from a command line",
      rumble.check_code("7"), 7)

# The three ranges of §9.5's `rumble` row, checked here so a mistake is caught
# locally rather than as an `err rumble` round trip.
check_raises("addr 0", ValueError, rumble.entry, 1, 0, 0)
check_raises("addr 32", ValueError, rumble.entry, 1, 32, 0)
check_raises("slot 255", ValueError, rumble.entry, 1, 5, 255)
check_raises("slot -1", ValueError, rumble.entry, 1, 5, -1)
check("a good entry is in §8.8's field order",
      list(rumble.entry(1, 5, 0).items()),
      [("code", 1), ("addr", 5), ("slot", 0)])

# Whole-section validation: shape, then the one-entry-per-code rule.
check_raises("a section is a list", ValueError, rumble.validate, {"code": 1})
check_raises("an entry is an object", ValueError, rumble.validate, [[1, 5, 0]])
check_raises("a missing field", ValueError, rumble.validate, [{"code": 1, "addr": 5}])
check_raises("a stray field", ValueError, rumble.validate,
             [{"code": 1, "addr": 5, "slot": 0, "btn": 3}])
check_raises("a code bound twice", ValueError, rumble.validate,
             [{"code": 1, "addr": 5, "slot": 0}, {"code": 1, "addr": 5, "slot": 1}])
check("two codes to one slot are fine: many codes may name one legend",
      len(rumble.validate([{"code": 1, "addr": 5, "slot": 0},
                           {"code": 2, "addr": 5, "slot": 0}])), 2)
check("no section is no bindings", rumble.validate(None), [])

# The map round trip. Pure functions: nothing is on the wire until `map set`.
m = {"v": 1, "bind": [{"btn": 1, "addr": 1, "key": 0}]}
m2 = rumble.bind(m, 2, 5, 3)
check("bind leaves the original alone", "rumble" in m, False)
check("bind writes §8.8's shape", m2["rumble"], [{"code": 2, "addr": 5, "slot": 3}])
check("and keeps the rest of the map", m2["bind"], m["bind"])
m2 = rumble.bind(m2, 1, 5, 0)
check("bindings are listed by code", [e["code"] for e in rumble.bindings(m2)], [1, 2])
m2 = rumble.bind(m2, 2, 5, 1)
check("rebinding replaces rather than duplicating",
      rumble.bindings(m2), [{"code": 1, "addr": 5, "slot": 0},
                            {"code": 2, "addr": 5, "slot": 1}])
check("find", rumble.find(m2, 2)["slot"], 1)
check("find an unbound code", rumble.find(m2, 9), None)
m2 = rumble.unbind(m2, 2)
check("unbind", [e["code"] for e in rumble.bindings(m2)], [1])
m2 = rumble.unbind(m2, 9)
check("unbinding what was never bound is not an error",
      [e["code"] for e in rumble.bindings(m2)], [1])
check("the section is dropped when the last one goes, not left as []",
      "rumble" in rumble.unbind(m2, 1), False)

# Through the board: read-modify-write of the map, and the board's own refusal.
board = MockBoard()
check("a fresh map has no bindings", board.rumble_bindings(), [])
board.rumble_bind(1, 5, 0)
board.rumble_bind(2, 5, 1)
check("map set carries the section", board.state.sent[-1],
      'map set {"v":1,"bind":[],"rumble":[{"code":1,"addr":5,"slot":0},'
      '{"code":2,"addr":5,"slot":1}]}')
check("and it reads back", board.rumble_bindings(),
      [{"code": 1, "addr": 5, "slot": 0}, {"code": 2, "addr": 5, "slot": 1}])
check("one binding at a time", board.rumble_binding(2)["slot"], 1)
board.rumble_unbind(1)
check("unbind through the board", [e["code"] for e in board.rumble_bindings()], [2])
# Refused before the wire, with the rule in it rather than `err rumble` -- and
# before the `map get`, so a bad argument costs no round trip at all.
_before = len(board.state.sent)
check_raises("the client refuses code 0", ValueError, board.rumble_bind, 0, 5, 0)
check_raises("and a slot out of range", ValueError, board.rumble_bind, 1, 5, 255)
check("nothing went out for either", len(board.state.sent), _before)
# ...and the mock refuses a duplicate the way the firmware does, so a client
# that skipped the check above would still not get one past it.
try:
    board.map_set({"v": 1, "rumble": [{"code": 3, "addr": 5, "slot": 0},
                                      {"code": 3, "addr": 5, "slot": 1}]})
    fails.append("the mock accepted a duplicate rumble code")
except CommandError as e:
    check("duplicate code error", e.err, "rumble")
check_raises("and a bad range too", CommandError, board.map_set,
             {"v": 1, "rumble": [{"code": 16, "addr": 5, "slot": 0}]})

# ---- the events, §9.3 -------------------------------------------------------
# Every decoded packet is reported whether or not the code is bound: that is
# what lets a config app bind by doing, the way §9.6 does for keys.
board = MockBoard()
seen = []
board.on("rumble", seen.append)
board.rumble_code(8)
check("a rumble event is reported", seen[-1],
      {"ev": "rumble", "left": 136, "right": 0, "code": 8})
board.rumble_packet(0)
check("a stop is reported too, as code 0", seen[-1]["code"], 0)
players = []
board.on("player", players.append)
board.player(1)
check("the host's player index is an event too (§8.5)", players,
      [{"ev": "player", "n": 1}])

# The wait helpers: what a bench "press it and see" flow needs. The mock
# delivers on the caller's thread, so a waiter has to be woken from another.
board = MockBoard()
_threading.Timer(0.02, lambda: board.rumble_packet(0)).start()     # a stop first
_threading.Timer(0.06, lambda: board.rumble_code(3)).start()
check("next_rumble_code skips the stop and returns the code",
      board.next_rumble_code(timeout=2.0), 3)
_threading.Timer(0.02, lambda: board.player(2)).start()
check("next_player", board.next_player(timeout=2.0), 2)
check_raises("and a silent board times out", Timeout, board.next_rumble, 0.05)

# `bound=True` waits for a code this map has a binding for, and ignores the rest.
board.rumble_bind(4, 5, 0)
_threading.Timer(0.02, lambda: board.rumble_code(9)).start()       # unbound
_threading.Timer(0.06, lambda: board.rumble_code(4)).start()
check("bound=True ignores an unbound code",
      board.next_rumble(timeout=2.0, bound=True)["code"], 4)

# Press-it-and-bind-it, for codes instead of keys.
board = MockBoard()
_threading.Timer(0.02, lambda: board.rumble_code(6)).start()
check("bind the code that arrives", board.rumble_bind_next(5, 2, timeout=2.0),
      {"code": 6, "addr": 5, "slot": 2})
check("and it is in the map", board.rumble_binding(6)["slot"], 2)

# ---- the mock honours a binding (§8.8) --------------------------------------
board = MockBoard()
legend = bytes((i * 3) & 0xFF for i in range(2048))
board.disp_slot_store(5, 0, legend, "1bpp")
board.rumble_bind(1, 5, 0)
board.rumble_code(1)
check("a bound code shows the picture", board.state.showing, {5: 0})
# §8.8's three silences. An unbound code, an empty slot and an absent tile all
# do nothing: a match is not the place to report a configuration error.
board.disp_clear(5)
board.rumble_code(2)
check("an unbound code does nothing", board.state.showing, {})
board.rumble_bind(2, 5, 3)                      # slot 3 was never written
board.rumble_code(2)
check("a bound but empty slot does nothing", board.state.showing, {})
board.rumble_bind(3, 9, 0)                      # no tile at 9
board.rumble_code(3)
check("a binding to an absent tile does nothing", board.state.showing, {})
check("every packet was recorded anyway",
      [e["code"] for e in board.state.rumbles], [1, 2, 2, 3])
board.rumble_code(1)
check("and the good one still works", board.state.showing, {5: 0})

# ---- LED actions on a code (§8.8, rev 0.33) ---------------------------------
# An entry with a btn and an rgb sets that button's live colour; a code may
# carry a legend and any number of them.
check("an LED action, canonical", rumble.led_entry(3, 5, "red", "blink"),
      {"code": 3, "btn": 5, "rgb": "FF0000", "fx": "blink"})
check("solid is left out", rumble.led_entry(3, 5, "00ff00", "solid"),
      {"code": 3, "btn": 5, "rgb": "00FF00"})
check("black is off", rumble.led_entry(3, 5, "000000")["rgb"], "off")
check("map keeps no effect", rumble.led_entry(3, 5, "map", "fast"),
      {"code": 3, "btn": 5, "rgb": "map"})
check_raises("btn 0", ValueError, rumble.led_entry, 1, 0, "red")
check_raises("btn 33", ValueError, rumble.led_entry, 1, 33, "red")
check_raises("an LED action's code 0", ValueError, rumble.led_entry, 0, 1, "red")
check_raises("a bad effect", ValueError, rumble.led_entry, 1, 1, "red", "strobe")

# validate: the board's canonical order is legends first, then LED actions.
section = rumble.validate([{"code": 3, "btn": 5, "rgb": "ff0000", "fx": "blink"},
                           {"code": 3, "addr": 5, "slot": 2},
                           {"code": 3, "btn": 6, "rgb": "map"},
                           {"code": 4, "btn": 5, "rgb": "off"},
                           {"code": 4, "btn": 6, "rgb": "map", "fx": "fast"}])
check("validate puts legends first and normalises", section,
      [{"code": 3, "addr": 5, "slot": 2},
       {"code": 3, "btn": 5, "rgb": "FF0000", "fx": "blink"},
       {"code": 3, "btn": 6, "rgb": "map"},
       {"code": 4, "btn": 5, "rgb": "off"},
       {"code": 4, "btn": 6, "rgb": "map"}])
check_raises("one kind or the other", ValueError, rumble.validate,
             [{"code": 2, "btn": 1, "rgb": "FF0000", "addr": 5, "slot": 0}])
check_raises("an LED action needs an rgb", ValueError, rumble.validate,
             [{"code": 2, "btn": 1}])
check_raises("and a btn", ValueError, rumble.validate, [{"code": 2, "rgb": "FF0000"}])
check_raises("the map takes hex, not names", ValueError, rumble.validate,
             [{"code": 2, "btn": 1, "rgb": "red"}])
check_raises("fx is case sensitive on the board", ValueError, rumble.validate,
             [{"code": 2, "btn": 1, "rgb": "FF0000", "fx": "Blink"}])
check_raises("one action per (code, btn)", ValueError, rumble.validate,
             [{"code": 2, "btn": 1, "rgb": "FF0000"}, {"code": 2, "btn": 1, "rgb": "map"}])
check("the same button on two codes is fine",
      len(rumble.validate([{"code": 2, "btn": 1, "rgb": "FF0000"},
                           {"code": 5, "btn": 1, "rgb": "map"}])), 2)
full = [{"code": 1 + i // 16, "btn": 1 + i % 16, "rgb": "map"} for i in range(rumble.LED_MAX)]
check("a full table", len(rumble.validate(full)), rumble.LED_MAX)
check_raises("and one too many", ValueError, rumble.validate,
             full + [{"code": 3, "btn": 1, "rgb": "map"}])

# The pure helpers keep each other's entries.
m = rumble.bind({"v": 1, "bind": []}, 3, 5, 2)
m = rumble.bind_led(m, 3, 5, "red", "blink")
m = rumble.bind_led(m, 4, 5, "map")
check("bind_led adds after the legend", m["rumble"],
      [{"code": 3, "addr": 5, "slot": 2},
       {"code": 3, "btn": 5, "rgb": "FF0000", "fx": "blink"},
       {"code": 4, "btn": 5, "rgb": "map"}])
m = rumble.bind_led(m, 3, 5, "green")
check("rebinding (code, btn) replaces in place", rumble.leds_for(m, 3),
      [{"code": 3, "btn": 5, "rgb": "00FF00"}])
m = rumble.bind(m, 1, 5, 0)
check("bind keeps the LED actions", len(rumble.led_actions(m)), 2)
check("bindings lists legends only", [e["code"] for e in rumble.bindings(m)], [1, 3])
m = rumble.unbind(m, 3)
check("unbind keeps them too", len(rumble.led_actions(m)), 2)
m = rumble.unbind_led(m, 4)
check("unbind_led by code", [e["code"] for e in rumble.led_actions(m)], [3])
m = rumble.unbind(rumble.unbind_led(m, 3, 5), 1)
check("the section goes with the last entry of either kind", "rumble" in m, False)

# Through the mock: `map get` returns the canonical form, and a code applies
# its LED actions to the live layer as `led <btn> <rgb> [fx]` would.
board = MockBoard()
board.map_set({"v": 1, "bind": [], "rumble": [
    {"code": 3, "btn": 5, "rgb": "ff0000", "fx": "blink"},
    {"code": 3, "addr": 5, "slot": 0},
    {"code": 4, "btn": 5, "rgb": "map"},
    {"code": 4, "btn": 6, "rgb": "000000"}]})
check("map get reads back canonical", board.map_get()["rumble"],
      [{"code": 3, "addr": 5, "slot": 0},
       {"code": 3, "btn": 5, "rgb": "FF0000", "fx": "blink"},
       {"code": 4, "btn": 5, "rgb": "map"},
       {"code": 4, "btn": 6, "rgb": "off"}])
board.rumble_code(3)
check("a code sets the colour", board.state.leds, {("btn", 5): "led 5 FF0000 blink"})
board.rumble_code(4)
check("map drops it and off is a colour", board.state.leds, {("btn", 6): "led 6 off"})
board.rumble_code(9)
board.rumble_packet(0)
check("an unbound code and a stop touch no LED", board.state.leds,
      {("btn", 6): "led 6 off"})
board.rumble_led_bind(9, 7, "blue")
board.rumble_code(9)
check("rumble_led_bind through the board", board.state.leds[("btn", 7)], "led 7 0000FF")
check("rumble_leds", board.rumble_leds(9), [{"code": 9, "btn": 7, "rgb": "0000FF"}])
board.rumble_led_unbind(9)
check("rumble_led_unbind", board.rumble_leds(9), [])
check("the legend survived all of it", board.rumble_binding(3),
      {"code": 3, "addr": 5, "slot": 0})
check_raises("the mock refuses a mixed entry", CommandError, board.map_set,
             {"v": 1, "rumble": [{"code": 2, "btn": 1, "rgb": "FF0000", "slot": 0}]})


# ---- LED actions on a tile LED (§8.8 rev 0.38) -------------------------------
# A key bound to no button can still be an indicator: the action names the
# tile and LED index instead of a button.
check("a tile LED action, canonical", rumble.tile_led_entry(3, 4, 2, "green", "breathe"),
      {"code": 3, "addr": 4, "led": 2, "rgb": "00FF00", "fx": "breathe"})
check_raises("led 32", ValueError, rumble.tile_led_entry, 3, 4, 32, "red")
check_raises("addr 0", ValueError, rumble.tile_led_entry, 3, 0, 1, "red")
section = rumble.validate([{"code": 3, "led": 2, "addr": 4, "rgb": "00ff00"},
                           {"code": 3, "btn": 5, "rgb": "off"},
                           {"code": 4, "addr": 4, "led": 2, "rgb": "map"}])
check("validate keeps tile LED actions in order, canonical", section,
      [{"code": 3, "addr": 4, "led": 2, "rgb": "00FF00"},
       {"code": 3, "btn": 5, "rgb": "off"},
       {"code": 4, "addr": 4, "led": 2, "rgb": "map"}])
check("is_led knows a tile LED action", rumble.is_led(section[0]), True)
check_raises("not a button too", ValueError, rumble.validate,
             [{"code": 2, "addr": 4, "led": 1, "btn": 3, "rgb": "FF0000"}])
check_raises("not a legend slot", ValueError, rumble.validate,
             [{"code": 2, "addr": 4, "led": 1, "slot": 0, "rgb": "FF0000"}])
check_raises("needs an addr", ValueError, rumble.validate, [{"code": 2, "led": 1, "rgb": "FF0000"}])
check_raises("one per (code, addr, led)", ValueError, rumble.validate,
             [{"code": 2, "addr": 4, "led": 1, "rgb": "FF0000"},
              {"code": 2, "addr": 4, "led": 1, "rgb": "map"}])
check("another LED on the same tile is fine",
      len(rumble.validate([{"code": 2, "addr": 4, "led": 1, "rgb": "FF0000"},
                           {"code": 2, "addr": 4, "led": 2, "rgb": "FF0000"}])), 2)
m = rumble.bind_led({"v": 1, "bind": []}, 3, 5, "red")
m = rumble.bind_tile_led(m, 3, 4, 2, "blue")
m = rumble.bind_tile_led(m, 3, 4, 2, "green")
check("bind_tile_led replaces (code, addr, led) in place", rumble.leds_for(m, 3),
      [{"code": 3, "btn": 5, "rgb": "FF0000"}, {"code": 3, "addr": 4, "led": 2, "rgb": "00FF00"}])
check("unbind_led by button leaves the tile LED",
      rumble.leds_for(rumble.unbind_led(m, 3, 5), 3), [{"code": 3, "addr": 4, "led": 2, "rgb": "00FF00"}])
check("unbind_tile_led leaves the button",
      rumble.leds_for(rumble.unbind_tile_led(m, 3, 4, 2), 3), [{"code": 3, "btn": 5, "rgb": "FF0000"}])
check("unbind_led by code drops both", "rumble" in rumble.unbind_led(m, 3), False)
board = MockBoard()
board.rumble_tile_led_bind(6, 4, 2, "blue", "blink")
board.rumble_code(6)
check("a code sets a tile LED as `led tile` would", board.state.leds,
      {("tile", 4, 2): "led tile 4 2 0000FF blink"})
board.rumble_tile_led_bind(7, 4, 2, "map")
board.rumble_code(7)
check("map drops the tile LED's override", board.state.leds, {})
board.rumble_tile_led_unbind(6, 4, 2)
check("rumble_tile_led_unbind", board.rumble_leds(6), [])

# ---- messages on rumble codes (§8.8 rev 0.36) --------------------------------
m = {"v": 1, "bind": []}
m = rumble.bind_text(m, 3, 6, ["INTAKE", "", "ON"])
m = rumble.bind_led(m, 3, 5, "red")
m = rumble.bind_text(m, 3, 9, ["INTAKE", "running"], ["white on red double", 0])
check("messages sit between legends and LED actions", [sorted(e) for e in m["rumble"]],
      [["addr", "code", "rows"], ["addr", "attrs", "code", "rows"], ["btn", "code", "rgb"]])
check("attrs are ATTR bytes, kept only when one is set", rumble.texts_for(m, 3)[1]["attrs"][1], 0)
check("a plain message has no attrs", "attrs" in rumble.texts_for(m, 3)[0], False)
m = rumble.bind(m, 3, 9, 0)
check("a picture on the same tile replaces the message", [e["addr"] for e in rumble.texts(m)], [6])
m = rumble.bind_text(m, 3, 9, ["BACK"])
check("... and a message replaces the picture", rumble.bindings(m), [])
check_raises("one row too many", ValueError, rumble.text_entry, 1, 6, ["x"] * 9)
check_raises("a row too long", ValueError, rumble.text_entry, 1, 6, ["x" * 33])
check_raises("not ASCII", ValueError, rumble.text_entry, 1, 6, ["caf\u00e9"])
check_raises("attrs not one per row", ValueError, rumble.text_entry, 1, 6, ["a", "b"], [0])
check_raises("validate refuses a picture and a message on one tile", ValueError, rumble.validate,
             [{"code": 1, "addr": 6, "slot": 0}, {"code": 1, "addr": 6, "rows": ["a"]}])
check_raises("... and two messages on one tile", ValueError, rumble.validate,
             [{"code": 1, "addr": 6, "rows": ["a"]}, {"code": 1, "addr": 6, "rows": ["b"]}])
m = rumble.unbind_text(m, 3)
check("unbind_text by code", rumble.texts(m), [])

board = MockBoard()
board.rumble_text_bind(4, 6, ["SHOOT", "READY"])
board.rumble_code(4)
check("a code puts its message on the panel", board.state.messages[6], ["SHOOT", "READY"])
check("rumble_texts", board.rumble_texts(4), [{"code": 4, "addr": 6, "rows": ["SHOOT", "READY"]}])
board.rumble_text_unbind(4, 6)
check("rumble_text_unbind", board.rumble_texts(), [])
# Between MAP_JSON_MAX and the 4096-byte line: the board's own refusal, not
# the client's line check.
import json as _json                                            # noqa: E402
big = {"v": 1, "bind": [], "rumble": [{"code": c, "addr": a, "rows": ["X" * 31] * 3}
                                     for c in range(1, 16) for a in (2, 3)]}
size = len(_json.dumps(big, separators=(",", ":")))
left, rows = 4070 - size - len(',{"code":1,"addr":4,"rows":[""]}'), []
while left > 0:
    rows.append("Y" * min(32, left))
    left -= len(rows[-1]) + (3 if left > 32 else 0)
big["rumble"].append({"code": 1, "addr": 4, "rows": rows})
check("the test map is valid", len(rumble.validate(big["rumble"])), 31)
check("... and lands in the gap", 4064 <= len(_json.dumps(big, separators=(",", ":"))) < 4080, True)
try:
    board.map_set(big)
    check("a map too big to store is refused", "accepted", "line_too_long")
except CommandError as e:
    check("a map too big to store is refused", e.err, "line_too_long")

# ---- companion app, §9.8 / §14.6 -------------------------------------------
# The engine behind `bbdisplay.py`. What is worth testing is not the format
# strings but the three rules it exists to enforce.
import time as _time                                            # noqa: E402
from buttonboard.companion import (Companion, Layout, MappingSource,  # noqa: E402
                          STALE_TEXT)

lay = Layout.from_json({"rows": [{"row": 0, "text": "{mode:<8}{match:>5}"},
                                 {"row": 1, "text": "BATT {battery:4.1f}V"}]})
check("layout names", lay.names(), ["mode", "match", "battery"])
check("row renders", lay.rows[0].render({"mode": "AUTO", "match": "0:12"}),
      "AUTO     0:12")
# §14.4 tells a sending client to substitute rather than drop; one absent value
# must cost that field, not the panel.
check("missing value substitutes", lay.rows[1].render({}), "BATT ????V")
check("wrong type substitutes", lay.rows[1].render({"battery": "x"}), "BATT ????V")

board = MockBoard()
addr = next(t["addr"] for t in board.tiles() if t.get("cols"))
vals = {"mode": "AUTO", "match": "0:12", "battery": 12.4}
app = Companion(board, addr, lay, MappingSource(lambda: dict(vals)), hz=0)

check("first frame writes every row", app.tick(), 2)
check("rows are padded to the panel", board.state.screen_text(addr)[0],
      "AUTO     0:12".ljust(21))
check("an unchanged frame writes nothing", app.tick(), 0)
vals["match"] = "0:11"
check("only the changed row is written", app.tick(), 1)

# §14.6's rule, one layer up: the module keeps polling whatever happens to this
# program, so the tile never blanks itself and stale match data would sit there.
app.source = MappingSource(lambda: None)
t0 = _time.monotonic()
check("a blip shorter than stale_after holds", app.tick(t0 + 1.0), 0)
check("still the last good values", board.state.screen_text(addr)[0],
      "AUTO     0:11".ljust(21))
check("going stale rewrites every row", app.tick(t0 + 30.0), 2)
check("the notice replaces row 0", board.state.screen_text(addr)[0],
      STALE_TEXT.ljust(21))
# Every row, not just the first: no field of a dead source may survive.
check("and blanks the rest", board.state.screen_text(addr)[1], " " * 21)
check("staying stale writes nothing more", app.tick(t0 + 31.0), 0)

# The diff is a belief about the glass. A tile that left the bus came back
# blank (§14.6 blanks at power-up too), so the belief has to be given up.
app.source = MappingSource(lambda: dict(vals))
app.tick(t0 + 32.0)
check("fresh data comes back", board.state.screen_text(addr)[0],
      "AUTO     0:11".ljust(21))
check("and then holds", app.tick(t0 + 33.0), 0)
board._on_line(json.dumps({"ev": "tile", "addr": addr, "state": "online"}))
check("ev:tile drops the diff", app.tick(t0 + 34.0), 2)

# §14.5: the bus is not a frame buffer. Ticking faster than `hz` must not write.
fast = Companion(board, addr, lay, MappingSource(lambda: dict(vals)), hz=5)
fast.tick(t0)
check("rate limit holds the bus", sum(fast.tick(t0 + i * 0.01) for i in range(1, 20)), 0)
check("and lets the next period through", fast.tick(t0 + 0.5) >= 0, True)

# A write that failed did not land, so the diff must not claim it did.
class _Dead(MappingSource):
    pass

seen = []
broken = Companion(board, addr, lay, MappingSource(lambda: dict(vals)), hz=0,
                   on_error=seen.append)
broken.tick()
def _boom(*a, **kw):
    raise CommandError("disp", {"ok": False, "err": "busy"})
broken.screen.board.disp_text = _boom
broken.screen.forget()
check("a failed write is reported, not raised", broken.tick(), 0)
check("and the error reached the callback", len(seen), 1)


# ---- combos, §9.7 -------------------------------------------------------------
board = MockBoard()
check("bind combo", board.bind_combo([8, 1])["btns"], [1, 8])
check("bind combo wire form", board.state.sent[-1], "bind 8+1")
check_raises("a combo needs two buttons", ValueError, board.bind_combo, [8])
check_raises("a combo needs distinct buttons", ValueError, board.bind_combo, [8, 8])
check_raises("a combo button in range", ValueError, board.bind_combo, [8, 33])


# ---- status pixel, §8.6 -------------------------------------------------------
board = MockBoard()
check("pixel on by default", board.hello()["pixel"], True)
check("pixel off", board.pixel(False)["pixel"], False)
check("pixel off wire form", board.state.sent[-1], "pixel off")
check("hello reports it", board.hello()["pixel"], False)


def report(path=__file__):
    if fails:
        print("FAIL\n" + "\n".join(fails))
        sys.exit(1)
    print("ok -- %s" % os.path.basename(path))


if __name__ == "__main__":
    report()

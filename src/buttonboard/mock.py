"""A board that isn't there.

For writing the Driver Station companion app before hardware exists, and for
the display half of the API, which no firmware answers yet. Speaks the same
line protocol over an in-process transport and keeps enough state that a test
can assert on what was sent.

    board = MockBoard()
    board.led(3, "red")
    assert board.state.leds["btn", 3] == "led 3 FF0000"
"""
from __future__ import annotations

import json

from . import display, rumble
from .client import Board
from .crc import crc16_ccitt_false
from .transport import LoopTransport

DEFAULT_TILES = [
    {"addr": 1, "type": "mx9", "uid": "A3F2C1", "fw": "1.0.2", "proto": 1,
     "keys": 9, "leds": 9, "enc": 0, "caps": 7, "state": "online",
     "crcErr": 0, "missed": 0, "lastSeen": 3, "pressed": [], "debounce": 5},
    {"addr": 4, "type": "disp1", "uid": "77BB01", "fw": "0.1.0", "proto": 1,
     "keys": 0, "leds": 0, "enc": 0, "caps": 19, "state": "online",
     "cols": 21, "rows": 4,
     "crcErr": 0, "missed": 0, "lastSeen": 5, "pressed": [], "debounce": 5},
    # caps 83 = BOOTLOADER | DEBOUNCE | DISPLAY | DISP_COLOR, the colour key
    # of §14.7: 10 x 8 cells of the 12x16 font, and one key under the screen.
    # pxW/pxH are the §14.8 advertisement (it takes a streamed picture) and
    # `slots` the §14.9 one (it keeps whole pictures in a local flash). Both
    # are fields rather than capability bits, so their absence is the answer.
    {"addr": 5, "type": "screenkey1", "uid": "5C7E01", "fw": "0.1.0", "proto": 1,
     "keys": 1, "leds": 0, "enc": 0, "caps": 83, "state": "online",
     "cols": 10, "rows": 8, "pxW": 128, "pxH": 128, "slots": 32,
     "crcErr": 0, "missed": 0, "lastSeen": 2, "pressed": [], "debounce": 8},
    # RGB builds of mx4 and mx1 are the same type with CAP_LEDS and an LED per
    # key (board_mx4.h, board_mx1.h): caps 7 = BOOTLOADER | DEBOUNCE | LEDS.
    {"addr": 2, "type": "mx4", "uid": "4A11C7", "fw": "0.1.0", "proto": 1,
     "keys": 4, "leds": 4, "enc": 0, "caps": 7, "state": "online",
     "crcErr": 0, "missed": 0, "lastSeen": 2, "pressed": [], "debounce": 5},
    {"addr": 3, "type": "mx1", "uid": "1B0D58", "fw": "0.1.0", "proto": 1,
     "keys": 1, "leds": 1, "enc": 0, "caps": 7, "state": "online",
     "crcErr": 0, "missed": 0, "lastSeen": 3, "pressed": [], "debounce": 5},
    # The 1.8" colour display (board_disp2.h): no keys, 160 x 128 landscape,
    # 13 x 8 cells of the 12x16 font, and a picture store like screenkey1's.
    {"addr": 6, "type": "disp2", "uid": "D2C0A9", "fw": "0.1.0", "proto": 1,
     "keys": 0, "leds": 0, "enc": 0, "caps": 83, "state": "online",
     "cols": 13, "rows": 8, "pxW": 160, "pxH": 128, "slots": 32,
     "crcErr": 0, "missed": 0, "lastSeen": 2, "pressed": [], "debounce": 5},
]


MAP_JSON_MAX = 4064                 # firmware/core/map.h: one flash record, NUL included

class MockState(object):
    def __init__(self, tiles=None, display=True, sound=True):
        self.tiles = list(DEFAULT_TILES if tiles is None else tiles)
        self.display = display          # False emulates a board older than §14
        self.sound = sound              # §16 is still reserved: no sounder tile
        self.pixel = True               # §8.6 status pixel, on until turned off
        self.playing = {}               # addr -> (notes, repeat) or None
        self.volume = {}                # addr -> 0-255
        self.leds = {}                  # ("btn", n) / ("tile", addr, i) -> line
        self.rows = {}                  # addr -> {row: text}
        self.attrs = {}                 # addr -> {row: ATTR byte, §14.7}
        self.shown = {}                 # addr -> snapshot at the last SHOW
        self.contrast = {}              # addr -> 0-255
        # Picture stores, §14.9. `slots` holds only COMMITTED pictures and
        # `writing` the at-most-one transfer in flight, because the separation
        # is the rule worth modelling: the header is written last, so a store
        # that stops anywhere leaves the slot empty rather than half a picture.
        self.slots = {}                 # (addr, slot) -> {"fmt","attr","data"}
        self.writing = None             # {"addr","slot","fmt","attr","data","last"}
        self.showing = {}               # addr -> the slot on the panel
        self.messages = {}              # addr -> the rows a rumble message put there
        # One `busy` after each BEGIN, the way the flash NAKs while its erase
        # runs (§14.9). Deterministic so a test can prove the client retries
        # rather than treating backpressure as a failure.
        self.busy_after_begin = True
        self._busy_owed = False
        self.map = {"v": 1, "bind": []}
        self.rumbles = []               # every packet decoded, §8.8
        self.saved = False
        self.sent = []
        self.flash = None               # transfer in flight, §9.4
        self.flashed = {}               # addr -> the image that was accepted
        self.flash_fw = {}              # addr -> the version to report after
        self._raw = ""

    # -- helpers a test reads --------------------------------------------------

    def screen_text(self, addr):
        """The rows as they are on the panel: what the last SHOW committed."""
        rows = self.shown.get(addr, {})
        return [rows[k] for k in sorted(rows)]

    def _caps(self, addr):
        """The `caps` word of a tile, 0 for an address that holds none."""
        return (self._tile(addr) or {}).get("caps", 0)

    def _tile(self, addr):
        """The §9.5 entry for an address, or None."""
        for t in self.tiles:
            if t.get("addr") == addr:
                return t
        return None

    # -- the protocol ----------------------------------------------------------

    def handle(self, line):
        self.sent.append(line)
        self._raw = line
        if self.flash is not None:
            return [json.dumps(m) for m in self._flash_chunk(line)]
        try:
            return [json.dumps(self._dispatch(line.split()))]
        except _Err as e:
            return [json.dumps(e.body)]

    # -- firmware transfer, §9.4 ----------------------------------------------

    def _cmd_flash(self, argv):
        if len(argv) != 3:
            raise _Err("args")
        try:
            addr, length, crc = int(argv[0]), int(argv[1]), int(argv[2], 16)
        except ValueError:
            raise _Err("args")
        if not any(t.get("addr") == addr for t in self.tiles):
            raise _Err("no_tile")
        self.flash = {"addr": addr, "total": length, "crc": crc,
                      "done": 0, "data": bytearray()}
        return {"ok": True, "ready": True, "chunk": 64}

    def _flash_chunk(self, line):
        """One hex line of a transfer in flight. Returns the lines to send back."""
        f = self.flash
        try:
            piece = bytes.fromhex(line.strip())
        except ValueError:
            self.flash = None                           # §9.4: aborts the transfer
            return [{"ok": False, "err": "hex"}]
        f["data"] += piece
        f["done"] += len(piece)
        out = [{"ev": "progress", "addr": f["addr"],
                "done": f["done"], "total": f["total"]}]
        if f["done"] < f["total"]:
            return out
        self.flash = None
        if len(f["data"]) != f["total"]:
            return out + [{"ok": False, "err": "len"}]
        actual = crc16_ccitt_false(f["data"])
        if actual != f["crc"]:
            return out + [{"ok": False, "err": "verify",
                           "expected": "%04X" % f["crc"], "actual": "%04X" % actual}]
        self.flashed[f["addr"]] = bytes(f["data"])
        fw = self.flash_fw.get(f["addr"], "0.1.1")
        for t in self.tiles:
            if t.get("addr") == f["addr"]:
                t["fw"] = fw
        return out + [{"ok": True, "addr": f["addr"], "fw": fw,
                       "done": f["done"], "total": f["total"]}]

    def _dispatch(self, argv):
        if not argv:
            raise _Err("args")
        cmd = argv[0].lower()
        rest = argv[1:]
        handler = getattr(self, "_cmd_" + cmd, None)
        if handler is None:
            raise _Err("unknown")
        return handler(rest)

    def _cmd_hello(self, argv):
        return {"ok": True, "product": "BBR Button Board", "fw": "0.1.0", "proto": 1,
                "serial": "MOCK", "hw": "A", "mapValid": True,
                "tiles": len(self.tiles), "mode": "run",
                "usb": "frc", "usbSaved": "frc", "pixel": self.pixel}

    def _cmd_tiles(self, argv):
        return {"ok": True, "tiles": self.tiles}

    def _cmd_stats(self, argv):
        return {"ok": True, "uptime": 12, "pollCycleUs": 4310, "bindMode": False,
                "test": "off", "fault": False,
                "power": {"budget": 450, "worst": 388, "brightness": 64}}

    def _cmd_save(self, argv):
        self.saved = True
        return {"ok": True, "saved": True}

    def _cmd_map(self, argv):
        if argv and argv[0] == "get":
            return {"ok": True, "map": self.map, "dirty": not self.saved}
        if argv and argv[0] == "set":
            mapping = json.loads(" ".join(argv[1:]))
            # §9.5's table gives the whole `rumble` section one error code, and
            # a fake that is more permissive than the board is worse than no
            # fake: a duplicate code accepted here is a duplicate code refused
            # on hardware, at the bench, by a `map set` the client thought was
            # already checked.
            try:
                section = rumble.validate(mapping.get("rumble"))
            except ValueError:
                raise _Err("rumble")
            # Stored as the board would emit it: legends, then LED actions,
            # colours upper case, black as `off` -- and no empty section.
            if section:
                mapping["rumble"] = section
            else:
                mapping.pop("rumble", None)
            # §9.1: a map whose canonical form will not fit one flash record
            # is refused at `map set`, not left to fail at `save`.
            if len(json.dumps(mapping, separators=(",", ":"))) >= MAP_JSON_MAX:
                raise _Err("line_too_long")
            self.map = mapping
            self.saved = False
            return {"ok": True, "dirty": True}
        raise _Err("args")

    # -- rumble as a control channel, §8.8 -------------------------------------

    def rumble_packet(self, left, right=0):
        """Decode one XInput OUT rumble packet and act on it.

        Returns the `{"ev":"rumble",...}` the module reports for EVERY packet
        it decodes, bound or not -- that is what lets a config app bind by
        doing. If the code has a binding it also SHOWs the picture, with
        §8.8's three silences: an absent tile, a tile with no picture store
        and an empty slot all do nothing, because a match is not the place to
        report a configuration error.

        The code's LED actions (rev 0.33) are applied first, every time it
        arrives, to the live layer exactly as `led <btn> <rgb> [fx]` would be
        -- `map` drops the button's override. The firmware does the same, and
        does not hold them back behind the SHOW rate limit.

        Not modelled, because both ends do it and neither is observable from
        here: the module collapses a burst to its newest code and holds the
        SHOW to one per 50 ms.
        """
        left, right = int(left) & 0xFF, int(right) & 0xFF
        code = rumble.code_of(left)
        event = {"ev": "rumble", "left": left, "right": right, "code": code}
        self.rumbles.append(event)
        if code:
            for action in rumble.leds_for(self.map, code):
                key = rumble.target(action)     # rev 0.38: a tile LED is `led tile`
                if action["rgb"] == rumble.MAP:
                    self.leds.pop(key, None)
                else:
                    where = [str(action["btn"])] if key[0] == "btn" \
                        else ["tile", str(action["addr"]), str(action["led"])]
                    self.leds[key] = " ".join(
                        ["led"] + where + [action["rgb"]]
                        + ([action["fx"]] if "fx" in action else []))
            for entry in self.map.get("rumble") or []:
                if rumble.is_led(entry) or rumble.is_text(entry) or entry.get("code") != code:
                    continue
                addr, slot = entry.get("addr"), entry.get("slot")
                if (addr, slot) in self.slots:      # an empty slot refuses SHOW
                    self.showing[addr] = slot
                break
            # Messages (rev 0.36): CLEAR, the rows the panel has, SHOW --
            # so the panel shows the rows, and a picture on it goes.
            for message in rumble.texts_for(self.map, code):
                addr = message["addr"]
                self.showing.pop(addr, None)
                self.messages[addr] = list(message["rows"])
        return event

    def _cmd_test(self, argv):
        return {"ok": True, "test": argv[0] if argv else "off"}

    def _cmd_bind(self, argv):
        if argv and argv[0] == "cancel":
            return {"ok": True}
        if argv and argv[0] == "axis":
            return {"ok": True, "axis": int(argv[1]), "addr": 4, "idx": 0}
        # A real bind waits for a key press; the mock takes the first key of
        # the first online tile so a script can be exercised end to end.
        if "+" in argv[0]:                                  # a combo, §9.7
            try:
                btns = [int(b) for b in argv[0].split("+")]
            except ValueError:
                raise _Err("args")
            if len(argv) != 1 or len(btns) < 2 or len(set(btns)) != len(btns) \
                    or not all(1 <= b <= 32 for b in btns):
                raise _Err("args")
            return {"ok": True, "btns": sorted(btns), "addr": self.tiles[0]["addr"], "key": 0}
        return {"ok": True, "btn": int(argv[0]), "addr": self.tiles[0]["addr"],
                "key": 0, "sources": 1}

    def _cmd_unbind(self, argv):
        if argv and argv[0] == "axis":
            return {"ok": True}
        return {"ok": True, "sources": 0}

    def _cmd_enc(self, argv):
        return {"ok": True}

    def _cmd_usb(self, argv):
        return {"ok": True, "usb": argv[0], "pending": True}

    def _cmd_pixel(self, argv):
        if len(argv) != 1 or argv[0] not in ("on", "off"):
            raise _Err("args")
        self.pixel = argv[0] == "on"
        return {"ok": True, "pixel": self.pixel}

    def _cmd_power(self, argv):
        return {"ok": True, "budget": int(argv[-1])}

    def _cmd_replace(self, argv):
        return {"ok": True}

    def _cmd_reset(self, argv):
        return {"ok": True}

    def _cmd_boot(self, argv):
        return {"ok": True}

    def _cmd_led(self, argv):
        if not argv:
            raise _Err("args")
        if argv[0] == "reset":
            self.leds.clear()
            return {"ok": True}
        if argv[0] == "all":
            self.leds.clear()
            self.leds["all"] = " ".join(["led"] + argv)
            return {"ok": True}
        if argv[0] == "tile":
            if len(argv) < 4:
                raise _Err("args")
            key = ("tile", int(argv[1]), int(argv[2]))
        else:
            key = ("btn", int(argv[0]))
        self.leds[key] = " ".join(["led"] + argv)
        return {"ok": True}

    def _cmd_sound(self, argv):
        if not self.sound:
            raise _Err("unknown")           # what the board answers today
        if len(argv) < 2:
            raise _Err("args")
        what = argv[1].lower()
        if argv[0].lower() == "all":
            if what != "stop":
                raise _Err("args")
            self.playing.clear()
            return {"ok": True}
        addr = int(argv[0])
        if what == "stop":
            self.playing[addr] = None
            return {"ok": True}
        if what == "volume":
            self.volume[addr] = int(argv[2])
            return {"ok": True}
        if what == "play":
            repeat = 1
            rest = argv[2:]
            if len(rest) >= 3 and rest[-2].lower() == "repeat":
                repeat = int(rest[-1])
                rest = rest[:-2]
            self.playing[addr] = (" ".join(rest), repeat)
            return {"ok": True}
        # `sound <addr> <freq> <ms>`: the one-note shorthand.
        if len(argv) != 3:
            raise _Err("args")
        self.playing[addr] = ("%s:%s" % (argv[1], argv[2]), 1)
        return {"ok": True}

    def _cmd_slot(self, addr, argv):
        """`disp <addr> slot <n> ...`, §14.9. Strict, on purpose.

        Every refusal below is one a client can get wrong quietly: an offset
        out of step, a picture committed without its CRC checked, a colour
        attribute on a format that carries none.

        Stricter than the module in one way worth knowing: `disp` is
        fire-and-forget, so a real board ACKs a line the moment it is queued
        and the tile's own NAK never reaches the config channel. This mock
        answers with the tile's verdict instead, which is the only way a
        client's handling of it can be exercised at all -- so treat these
        refusals as real, and do not depend on getting one.
        """
        tile = self._tile(addr) or {}
        if not tile.get("slots"):
            raise _Err("args")
        if len(argv) < 2:
            raise _Err("args")
        try:
            slot = int(argv[0])
        except ValueError:
            raise _Err("args")
        if not 0 <= slot < tile["slots"]:
            raise _Err("args")
        op = argv[1].lower()
        rest = argv[2:]
        writing = self.writing
        mine = writing is not None and writing["addr"] == addr and writing["slot"] == slot

        if op == "begin":
            if len(rest) != 2:
                raise _Err("args")
            fmt = rest[0].lower()
            if fmt not in display.SLOT_FORMATS:
                raise _Err("args")
            try:
                attr = int(rest[1])
            except ValueError:
                raise _Err("args")
            if not 0 <= attr <= 0xFF:
                raise _Err("args")
            if fmt == "rgb565" and attr:
                raise _Err("args")
            if fmt == "1bpp":
                if attr & display.ATTR_DOUBLE:              # meaningless here
                    raise _Err("args")
                if display.is_color(attr) and not self._caps(addr) & display.CAP_DISP_COLOR:
                    raise _Err("args")
            # The erase happens now; nothing replaces it until END passes.
            self.slots.pop((addr, slot), None)
            if self.showing.get(addr) == slot:
                self.showing.pop(addr)
            self.writing = {"addr": addr, "slot": slot, "fmt": fmt, "attr": attr,
                            "data": bytearray(), "last": None,
                            "limit": display.slot_size(fmt, tile["pxW"], tile["pxH"])}
            self._busy_owed = self.busy_after_begin
            return {"ok": True}

        if op == "data":
            if len(rest) != 2:
                raise _Err("args")
            try:
                off = int(rest[0])
            except ValueError:
                raise _Err("args")
            try:
                chunk = bytes.fromhex(rest[1])
            except ValueError:
                raise _Err("hex")
            if not chunk or not 1 <= len(chunk) <= display.SLOT_CHUNK:
                raise _Err("args")
            if not mine:
                raise _Err("args")
            # A repeat of the chunk just accepted is acknowledged and thrown
            # away: `busy` flow control means a sender repeats itself.
            if writing["last"] is not None and off == writing["last"]:
                return {"ok": True}
            if off != len(writing["data"]):
                raise _Err("args")
            if off + len(chunk) > writing["limit"]:
                raise _Err("args")
            if self._busy_owed:
                self._busy_owed = False
                raise _Err("busy")
            writing["last"] = off
            writing["data"] += chunk
            return {"ok": True}

        if op == "end":
            if len(rest) != 1:
                raise _Err("args")
            try:
                want = int(rest[0])
            except ValueError:
                raise _Err("args")
            if not mine:
                raise _Err("args")
            self.writing = None
            data = bytes(writing["data"])
            if want != crc16_ccitt_false(data):
                # `flash` is §9.2's code for "it did not read back as it was
                # sent". Empty, not partial: the header was never written.
                raise _Err("flash")
            self.slots[(addr, slot)] = {"fmt": writing["fmt"],
                                        "attr": writing["attr"], "data": data}
            return {"ok": True}

        if op == "show":
            if rest:
                raise _Err("args")
            if (addr, slot) not in self.slots:
                raise _Err("args")                  # SHOW on an empty slot
            self.showing[addr] = slot               # owns the panel until CLEAR
            return {"ok": True}

        if op == "info":
            if rest:
                raise _Err("args")
            if mine:
                raise _Err("tile")                  # mid-transfer: ERR_BAD_STATE
            entry = self.slots.get((addr, slot))
            if entry is None:
                return {"ok": True, "slot": slot, "empty": True}
            return {"ok": True, "slot": slot, "empty": False, "fmt": entry["fmt"],
                    "attr": entry["attr"], "len": len(entry["data"]),
                    "crc": crc16_ccitt_false(entry["data"])}

        if op == "read":
            if len(rest) != 2:
                raise _Err("args")
            try:
                off, n = int(rest[0]), int(rest[1])
            except ValueError:
                raise _Err("args")
            if not 0 <= off <= 0xFFFF or not 1 <= n <= display.SLOT_READ:
                raise _Err("args")
            if mine:
                raise _Err("tile")
            entry = self.slots.get((addr, slot))
            if entry is None or off + n > len(entry["data"]):
                raise _Err("args")
            return {"ok": True, "slot": slot, "off": off,
                    "hex": entry["data"][off:off + n].hex().upper()}

        if op == "erase":
            if rest:
                raise _Err("args")
            self.slots.pop((addr, slot), None)
            if mine:
                self.writing = None
            if self.showing.get(addr) == slot:
                self.showing.pop(addr)
            return {"ok": True}
        raise _Err("args")

    def _cmd_disp(self, argv):
        if not self.display:
            raise _Err("unknown")           # what the board answers today
        if len(argv) < 2:
            raise _Err("args")
        addr = int(argv[0])
        what = argv[1].lower()
        rows = self.rows.setdefault(addr, {})
        if what == "slot":
            return self._cmd_slot(addr, argv[2:])
        if what == "clear":
            # CLEAR is what takes the panel back from a picture (§14.9).
            self.showing.pop(addr, None)
            rows.clear()
            self.attrs.pop(addr, None)
            self.shown[addr] = {}
            return {"ok": True}
        if what == "show":
            self.shown[addr] = dict(rows)
            return {"ok": True}
        if what == "bar":
            if len(argv) != 6:
                raise _Err("args")
            row, col, width, value = (int(x) for x in argv[2:6])
            rows[row] = "bar %d %d %d" % (col, width, value)
            return {"ok": True}
        if what == "contrast":
            if len(argv) != 3:
                raise _Err("args")
            self.contrast[addr] = int(argv[2])
            return {"ok": True}
        if what == "text":
            # `disp <addr> text <row> <col> <attr> <text...>`: the long form,
            # the only way to reach inverse, double height and colour (§14.4).
            if len(argv) < 5:
                raise _Err("args")
            attr = int(argv[4])
            if not 0 <= attr <= 0xFF:
                raise _Err("args")
            # What the module does with the descriptor it already holds: a
            # fire-and-forget caller would never see the tile's NAK (§14.4).
            if attr & 0xFC and not self._caps(addr) & 0x40:
                raise _Err("args")
            rows[int(argv[2])] = _rest_after(self._raw, 6)
            self.attrs.setdefault(addr, {})[int(argv[2])] = attr
            return {"ok": True}
        # The row text is the rest of the line verbatim (§14.4), so trailing
        # spaces survive and a short string can erase a longer one.
        rows[int(what)] = _rest_after(self._raw, 3)
        self.attrs.setdefault(addr, {})[int(what)] = 0      # the short form is ATTR 0
        return {"ok": True}


def _rest_after(raw, count):
    """The remainder of `raw` after `count` tokens, the way lineproto.c splits.

    Python's `str.split(None, n)` is the wrong tool and gets this backwards: it
    eats runs of whitespace and strips the remainder's leading spaces. §14.4
    specifies single-space tokenising with exactly one separator consumed, so
    the spaces on either side of display text survive -- and they have to,
    because padding a row with spaces is the documented way to erase what a
    longer string left behind. A mock that drops them certifies a client that
    cannot blank a row on real hardware.
    """
    p = raw
    i = 0
    while i < len(p) and p[i] == " ":
        i += 1
    seen = 0
    while i < len(p) and seen < count:
        while i < len(p) and p[i] != " ":
            i += 1
        seen += 1
        if i < len(p):
            i += 1                              # one separator eaten
            if seen == count:
                return p[i:]                    # the rest, verbatim
            while i < len(p) and p[i] == " ":
                i += 1
    return ""


class _Err(Exception):
    def __init__(self, err, **extra):
        Exception.__init__(self, err)
        self.body = dict({"ok": False, "err": err}, **extra)


class MockBoard(Board):
    """A Board backed by MockState. `board.state` is the state to assert on."""

    def __init__(self, tiles=None, display=True, sound=True, **kwargs):
        self.state = MockState(tiles=tiles, display=display, sound=sound)
        Board.__init__(self, LoopTransport(self.state.handle), **kwargs)

    def inject(self, event):
        """Deliver an unsolicited event (§9.3), e.g. {"ev":"tile",...}."""
        self._t.inject(json.dumps(event))

    def rumble_packet(self, left, right=0):
        """A rumble packet arrives from the host, §8.8.

        The direction a real board never sees on this channel -- the packet
        comes in on the XInput OUT endpoint -- so this is the only way to
        exercise a binding without a Driver Station. Returns the event it
        reported, after reporting it.
        """
        event = self.state.rumble_packet(left, right)
        self.inject(event)
        return event

    def rumble_code(self, code):
        """The same, addressed by legend code: the byte an OpMode's
        `showLegend(code)` puts on the wire (§8.8)."""
        return self.rumble_packet(rumble.wire_byte(code))

    def player(self, n):
        """The host assigns a player index, §8.5: `{"ev":"player","n":1}`."""
        self.inject({"ev": "player", "n": int(n)})

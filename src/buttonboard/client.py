"""Board — the config-channel client (PROTOCOL.md §9).

One command in flight at a time, terminal {"ok":...} lines resolve it, and
event lines (§9.3) are fanned out to listeners whether or not a command is
pending. Mirrors web/src/protocol.js so the two clients cannot drift.
"""
from __future__ import annotations

import json
import queue
import threading
import time

from . import display, leds, rumble, sound
from .crc import crc16_ccitt_false
from .errors import CommandError, NotConnected, Timeout
from .transport import SerialTransport

MAX_LINE = 4096                    # §9.1; longer lines are refused by the board
FLASH_CHUNK = 64                   # FLASH_CHUNK, shared/protocol.h

# Each is the firmware's own budget for the step plus a second of slack, so a
# host timeout means the transfer really is stuck rather than that the two
# sides disagree about how long a tile may take. From lineproto.h:
# FLASH_STEP_TIMEOUT_US 1s, FLASH_BOOT_WAIT_US 2s, FLASH_BACK_WAIT_US 4s.
FLASH_READY_TIMEOUT = 4.0          # ENTER_BOOT, reset, GET_DESC until IN_BOOT
FLASH_CHUNK_TIMEOUT = 2.0          # one WRITE and the tile's ACK
FLASH_VERIFY_TIMEOUT = 6.0         # VERIFY, RESET, tile online again

# `busy` on a `disp` command is the auxiliary queue (§3.2), or §14.9's picture
# flash, saying it has no room this cycle. Waiting slightly under a slot period
# means a refused line goes out on the first cycle with room; bounded by
# attempts rather than by wall clock, so a slow link fails on effort rather
# than on the clock. 512 DATA lines is the case that needs it. A try costs
# about the wait once a reply comes back in a millisecond, so the tries cover
# a 64 KB block erase at its 2 s datasheet worst case with room to spare.
DISP_BUSY_WAIT = 0.005
DISP_BUSY_TRIES = 1000


def _int_in(name, value, low, high):
    n = int(value)
    if not low <= n <= high:
        raise ValueError("%s must be %d-%d, not %r" % (name, low, high, value))
    return n


def _image_bytes(image):
    """The image as bytes. A str or PathLike is a file to read it from."""
    if isinstance(image, (bytes, bytearray, memoryview)):
        return bytes(image)
    with open(image, "rb") as f:
        return f.read()


def _flash_done(line, response, sent, total):
    """Check the final line of a transfer and hand it back.

    `sent` is what this client actually put on the wire, not what the board
    says it received: the terminal line of §9.4 carries no byte count, so a
    board that answered ok early would otherwise be reported as a success
    with part of the image never sent. Counting locally catches that whatever
    the board chooses to tell us.
    """
    if sent != total:
        raise CommandError(line, dict(response, ok=False, err="short",
                                      sent=sent, total=total))
    response["events"] = []
    return response


class _Pending(object):
    __slots__ = ("line", "done", "response", "events")

    def __init__(self, line):
        self.line = line
        self.done = threading.Event()
        self.response = None
        self.events = []


class Board(object):
    """Connected board. Safe to call from several threads: commands serialise.

    Event callbacks run on the reader thread and must not issue commands --
    the reader is what completes them, so a command from inside a callback
    deadlocks until its timeout. Push to a queue instead.
    """

    DEFAULT_TIMEOUT = 5.0
    BIND_TIMEOUT = 35.0            # the board's own bind timeout is 30 s (§9.2)
    EVENT_TIMEOUT = 30.0           # `wait_event`: a person at a bench, not a bus
    SAVE_TIMEOUT = 10.0            # flash write, read back, verify (§9.2)

    def __init__(self, transport, timeout=DEFAULT_TIMEOUT):
        self.timeout = timeout
        self._t = transport
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending = None
        self._listeners = {}
        self._line_hooks = []
        self._caps = {}                # addr -> `caps` from §9.5, for disp_color
        self._slots = {}               # addr -> `slots` from §9.5, for §14.9
        self._closed = False
        transport.start(self._on_line)

    # ---- lifecycle ----------------------------------------------------------

    @classmethod
    def open(cls, port=None, timeout=DEFAULT_TIMEOUT, **kwargs):
        """Open the board's CDC port, auto-detecting it when `port` is None."""
        return cls(SerialTransport(port=port, **kwargs), timeout=timeout)

    def close(self):
        self._closed = True
        self._t.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---- listeners ----------------------------------------------------------

    def on(self, ev, cb):
        """Subscribe to an event name from §9.3, or "*" for every event.
        Returns a callable that unsubscribes."""
        with self._state_lock:
            self._listeners.setdefault(ev, []).append(cb)

        def off():
            with self._state_lock:
                try:
                    self._listeners.get(ev, []).remove(cb)
                except ValueError:
                    pass
        return off

    def on_line(self, cb):
        """Every received line, verbatim. For logging and for `bbctl` echo."""
        with self._state_lock:
            self._line_hooks.append(cb)
        return lambda: self._line_hooks.remove(cb)

    # ---- the line protocol --------------------------------------------------

    def command(self, line, timeout=None):
        """Send one command line and return its terminal object.

        Raises CommandError on {"ok":false}, Timeout if no terminal line
        arrives. The returned dict carries an "events" list of the event
        lines that arrived while the command was in flight.
        """
        if self._closed:
            raise NotConnected("board is closed")
        if len(line) + 1 > MAX_LINE:
            # The board would answer line_too_long (§9.1); say so without a
            # round trip, and name the real limit rather than the JSON one.
            raise ValueError("command is %d bytes, over the %d-byte line limit (§9.1)"
                             % (len(line) + 1, MAX_LINE))
        seconds = self.timeout if timeout is None else timeout
        pending = _Pending(line)
        with self._send_lock:
            with self._state_lock:
                self._pending = pending
            self._t.write(line + "\n")
            if not pending.done.wait(seconds):
                with self._state_lock:
                    self._pending = None
                raise Timeout(line, seconds)
        response = pending.response
        response["events"] = pending.events
        if not response.get("ok"):
            raise CommandError(line, response)
        return response

    def write_line(self, line):
        """Write a line without waiting for its terminal response.

        For an interactive console, where the user types whatever they like
        and the reader thread prints whatever comes back, and for the hex
        chunks of a firmware transfer (§9.4), which are not commands.
        """
        if self._closed:
            raise NotConnected("board is closed")
        self._t.write(line + "\n")

    def _on_line(self, text):
        for hook in list(self._line_hooks):
            hook(text)
        try:
            msg = json.loads(text)
        except ValueError:
            return                                      # garbage: ignore, §9.1
        if not isinstance(msg, dict):
            return
        if "ev" in msg:
            with self._state_lock:
                pending = self._pending
                callbacks = list(self._listeners.get(msg["ev"], []))
                callbacks += list(self._listeners.get("*", []))
            if pending is not None:
                pending.events.append(msg)
            for cb in callbacks:
                cb(msg)
            return
        if "ok" in msg:
            with self._state_lock:
                pending, self._pending = self._pending, None
            if pending is not None:
                pending.response = msg
                pending.done.set()

    # ---- §9.2 commands ------------------------------------------------------

    def hello(self):
        return self.command("hello")

    def tiles(self):
        """Every known address, §9.5. Returns the list, not the envelope."""
        return self.command("tiles")["tiles"]

    def tile(self, addr):
        """One entry from `tiles`, or None if that address is not known."""
        for entry in self.tiles():
            if entry.get("addr") == addr:
                return entry
        return None

    def stats(self):
        return self.command("stats")

    def usb(self, mode):
        if mode not in ("frc", "xinput"):
            raise ValueError("usb mode must be frc or xinput, not %r" % (mode,))
        return self.command("usb %s" % mode)

    def save(self):
        return self.command("save", timeout=self.SAVE_TIMEOUT)

    def reset(self, factory=False):
        """Reboots the board: the port disappears and this object is done."""
        return self.command("reset factory" if factory else "reset")

    def boot(self):
        """Reboot into BOOTSEL for a UF2 update. The port disappears (§9.2)."""
        return self.command("boot")

    def pixel(self, on):
        """The main module's status pixel, §8.6: a white flash on every press.
        Persisted by `save`. `hello()["pixel"]` is absent on a board without one."""
        return self.command("pixel %s" % ("on" if on else "off"))

    def power_budget(self, milliamps):
        return self.command("power budget %d" % _int_in("budget", milliamps, 0, 100000))

    def replace(self, old_addr, new_addr):
        # Both addresses now describe different tiles than they did, so the
        # capability cache behind `disp_color` is stale.
        self._caps.clear()
        self._slots.clear()
        return self.command("replace %d %d" % (_int_in("old_addr", old_addr, 1, 31),
                                               _int_in("new_addr", new_addr, 1, 31)))

    # bindings, §9.6/§9.7
    def bind(self, btn, add=False):
        line = "bind %d add" % _int_in("btn", btn, 1, 32) if add else "bind %d" % _int_in("btn", btn, 1, 32)
        return self.command(line, timeout=self.BIND_TIMEOUT)

    def bind_combo(self, btns):
        """`bind 8+1`, §9.7: the next key pressed feeds exactly these buttons,
        all in the same report. Start+A (8, 1) and Start+B (8, 2) are how the
        FTC Driver Station assigns a pad to gamepad 1 and 2."""
        btns = [_int_in("btn", b, 1, 32) for b in btns]
        if len(btns) < 2 or len(set(btns)) != len(btns):
            raise ValueError("a combo is two or more distinct buttons, got %r" % (btns,))
        return self.command("bind " + "+".join(str(b) for b in btns), timeout=self.BIND_TIMEOUT)

    def bind_cancel(self):
        return self.command("bind cancel")

    def unbind(self, btn, addr=None, key=None):
        btn = _int_in("btn", btn, 1, 32)
        if addr is None:
            return self.command("unbind %d" % btn)
        return self.command("unbind %d %d %d" % (btn, _int_in("addr", addr, 1, 31),
                                                 _int_in("key", key, 0, 15)))

    # encoders, §15.7
    def bind_axis(self, n):
        return self.command("bind axis %d" % _int_in("axis", n, 1, 4),
                            timeout=self.BIND_TIMEOUT)

    def unbind_axis(self, n):
        return self.command("unbind axis %d" % _int_in("axis", n, 1, 4))

    def enc_zero(self, n=None):
        return self.command("enc zero" if n is None else "enc zero %d" % _int_in("axis", n, 1, 4))

    # map, §9.5
    def map_get(self):
        return self.command("map get")["map"]

    def map_set(self, mapping):
        return self.command("map set " + json.dumps(mapping, separators=(",", ":")))

    # ---- rumble bindings, §8.8 ---------------------------------------------
    # Configuration time only. The board these reach is in `frc` or `config`
    # mode; the board that acts on the bindings is the same board in `xinput`,
    # where there is no config channel to reach it with at all. That split is
    # the whole point: bind here, switch, and the codes work in the match.

    def rumble_bindings(self):
        """Every rumble binding in the RAM map, sorted by code (§8.8)."""
        return rumble.bindings(self.map_get())

    def rumble_binding(self, code):
        """The binding for one code, or None if it is not bound."""
        return rumble.find(self.map_get(), code)

    def rumble_bind(self, code, addr, slot):
        """Bind a legend code to a tile's stored picture, and `map set` it.

        Read-modify-write of the map, validated here first so a bad code, a
        bad address or a second entry for the same code is a ValueError with
        the rule in it rather than `{"ok":false,"err":"rumble"}`.

        This leaves the map in RAM, like every other `map set` (§9.5): call
        `save()` when the layout is finished. A binding that is not saved is
        gone at the next power-up, which in a match is the same as never
        having made it.
        """
        one = rumble.entry(code, addr, slot)        # before the `map get`
        return self.map_set(rumble.bind(self.map_get(), one["code"],
                                        one["addr"], one["slot"]))

    def rumble_unbind(self, code):
        """Drop a code's binding. A code with no entry does nothing (§8.8)."""
        code = rumble.check_code(code)              # before the `map get`
        return self.map_set(rumble.unbind(self.map_get(), code))

    def rumble_leds(self, code=None):
        """The map's rumble LED actions (rev 0.33), or one code's, in order."""
        mapping = self.map_get()
        return rumble.led_actions(mapping) if code is None else rumble.leds_for(mapping, code)

    def rumble_led_bind(self, code, btn, rgb, fx=None):
        """Make a code set button `btn`'s LED to `rgb` (hex, a name, `off` or
        `map`), and `map set` it. Same read-modify-write and same RAM-only
        caveat as `rumble_bind`; the colour itself lands on the live layer
        when the code arrives, so it is never saved."""
        one = rumble.led_entry(code, btn, rgb, fx)  # before the `map get`
        return self.map_set(rumble.bind_led(self.map_get(), one["code"], one["btn"],
                                            one["rgb"], one.get("fx")))

    def rumble_led_unbind(self, code, btn=None):
        """Drop code's LED action on `btn`, or all of code's when btn is None."""
        code = rumble.check_code(code)
        btn = None if btn is None else rumble.check_btn(btn)
        return self.map_set(rumble.unbind_led(self.map_get(), code, btn))

    def rumble_tile_led_bind(self, code, addr, led, rgb, fx=None):
        """Make a code set tile `addr`'s LED `led` to `rgb` (rev 0.38): the
        robot colour for a key bound to no button, an indicator light. Same
        read-modify-write and RAM-only caveat as `rumble_led_bind`."""
        one = rumble.tile_led_entry(code, addr, led, rgb, fx)   # before the `map get`
        return self.map_set(rumble.bind_tile_led(self.map_get(), one["code"], one["addr"],
                                                 one["led"], one["rgb"], one.get("fx")))

    def rumble_tile_led_unbind(self, code, addr, led):
        """Drop code's LED action on tile `addr`'s LED `led`."""
        code = rumble.check_code(code)
        addr, led = rumble.check_addr(addr), rumble.check_led(led)
        return self.map_set(rumble.unbind_tile_led(self.map_get(), code, addr, led))

    def rumble_texts(self, code=None):
        """The map's messages (§8.8 rev 0.36), or one code's."""
        mapping = self.map_get()
        return rumble.texts(mapping) if code is None else rumble.texts_for(mapping, code)

    def rumble_text_bind(self, code, addr, rows, attrs=None):
        """Make `code` write `rows` to display tile `addr` during a match.

            board.rumble_text_bind(3, 6, ["INTAKE", "", "ON"])
            board.rumble_text_bind(4, 6, ["SHOOT"], ["black on yellow"])

        The panel is cleared first, so rows left out are blank. Replaces the
        code's message on that tile, or a picture legend it had there. Like
        every map edit it is live at once and kept only after `save()`.
        """
        one = rumble.text_entry(code, addr, rows, attrs)   # before the `map get`
        return self.map_set(rumble.bind_text(self.map_get(), one["code"], one["addr"],
                                             one["rows"], one.get("attrs")))

    def rumble_text_unbind(self, code, addr=None):
        """Remove code's message on `addr`, or all of code's messages."""
        code = rumble.check_code(code)
        addr = None if addr is None else rumble.check_addr(addr)
        return self.map_set(rumble.unbind_text(self.map_get(), code, addr))

    def rumble_bind_next(self, addr, slot, timeout=None):
        """Wait for a rumble code to arrive and bind it to (`addr`, `slot`).

        §9.6's press-it-and-bind-it flow, for codes instead of keys: run the
        OpMode call, take whatever number turns up, and write it down. Cheaper
        than reading `showLegend(3)` off a screen and trusting it. Returns the
        binding that was made.
        """
        addr, slot = rumble.check_addr(addr), rumble.check_slot(slot)
        code = self.next_rumble_code(timeout)
        self.rumble_bind(code, addr, slot)
        return rumble.entry(code, addr, slot)

    # ---- waiting for an event, §9.3 -----------------------------------------

    def wait_event(self, ev, timeout=None, match=None):
        """Block until the next `ev` event and return it.

        The counterpart to `on()` for a script that is driving a bench by
        hand -- press it, see what arrives, bind it -- which is the flow §9.6
        runs for keys and §8.8 asks for with rumble codes. `match(event)` may
        filter; events that do not match are left to the other listeners.

        Not callable from an event callback: callbacks run on the reader
        thread, and that thread is what would have to deliver this one.
        """
        seconds = self.EVENT_TIMEOUT if timeout is None else timeout
        inbox = queue.Queue()

        def hook(msg):
            if match is None or match(msg):
                inbox.put(msg)

        off = self.on(ev, hook)
        try:
            return inbox.get(timeout=seconds)
        except queue.Empty:
            raise Timeout("event %r" % (ev,), seconds)
        finally:
            off()

    def next_rumble(self, timeout=None, bound=None):
        """The next decoded rumble packet (§8.8), as its whole event object.

        `{"ev":"rumble","left":136,"right":0,"code":8}`. Every packet the
        module decodes is reported whether or not the code is bound, which is
        what lets a config app bind by doing.

        `code` 0 is skipped: it is the SDK's stop, not an action, and a
        gamepad that is buzzing sends one at the end of every rumble. Pass
        `bound=True` to wait only for a code this map has a binding for.
        """
        codes = None
        if bound:
            codes = {e["code"] for e in self.rumble_bindings()}

        def wanted(msg):
            code = msg.get("code")
            return bool(code) and (codes is None or code in codes)

        return self.wait_event("rumble", timeout, match=wanted)

    def next_rumble_code(self, timeout=None):
        """The next rumble code, as an int. `next_rumble` without the wrapper."""
        return self.next_rumble(timeout)["code"]

    def next_player(self, timeout=None):
        """The next player index the host assigns, as an int (§8.5, §9.3)."""
        return self.wait_event("player", timeout)["n"]

    # tile firmware transfer, §9.4
    def flash(self, addr, image, on_progress=None,
              ready_timeout=FLASH_READY_TIMEOUT,
              chunk_timeout=FLASH_CHUNK_TIMEOUT,
              verify_timeout=FLASH_VERIFY_TIMEOUT):
        """Send a firmware image to a tile and wait for it to come back.

        `image` is the raw application binary (`make bin` in firmware/tile) as
        bytes, or a path to read it from. Offsets are relative to the
        application start, so this is the `.bin`, never the `.hex` or `.elf`.
        Returns the final `{"ok":true,"addr":..,"fw":".."}`; `fw` is read back
        from the running application, so it is the proof the update took.

        `on_progress(event)` is called on the reader thread for each progress
        event (§9.3) with `done` and `total` in bytes.

        This is the one command that is not `command()`: §9.4 answers twice,
        once with `ready` and again when VERIFY has passed and the tile is
        back, with the hex chunks in between. It holds the send lock for the
        whole transfer -- a half-written tile is not a state to interleave
        another command with -- so callers on other threads block until it
        finishes.

        On failure the tile is left in the bootloader at its address, so
        re-running `flash` retries (§9.4); it does not need rediscovering.
        """
        addr = _int_in("addr", addr, 1, 31)
        data = _image_bytes(image)
        if not data:
            raise ValueError("firmware image is empty")
        line = "flash %d %d %04X" % (addr, len(data), crc16_ccitt_false(data))
        if self._closed:
            raise NotConnected("board is closed")

        inbox = queue.Queue()

        def hook(text):
            try:
                msg = json.loads(text)
            except ValueError:
                return                                  # garbage: ignore, §9.1
            if not isinstance(msg, dict):
                return
            if msg.get("ev") == "progress" and msg.get("addr") == addr:
                inbox.put(("progress", msg))
            elif "ok" in msg:
                inbox.put(("terminal", msg))

        off = self.on_line(hook)
        try:
            with self._send_lock:
                self._t.write(line + "\n")
                ready = self._flash_expect(inbox, ready_timeout, line, "terminal")
                chunk = _int_in("chunk", ready.get("chunk", FLASH_CHUNK), 1, MAX_LINE // 2)

                sent = 0
                for offset in range(0, len(data), chunk):
                    piece = data[offset:offset + chunk]
                    self._t.write(piece.hex().upper() + "\n")
                    sent += len(piece)
                    kind, msg = self._flash_next(inbox, chunk_timeout, line)
                    if kind == "terminal":
                        # An error, or a board that finished without a last
                        # progress. Either way the transfer is over.
                        if not msg.get("ok"):
                            raise CommandError(line, msg)
                        return _flash_done(line, msg, sent, len(data))
                    if on_progress is not None:
                        on_progress(msg)

                return _flash_done(line, self._flash_expect(
                    inbox, verify_timeout, line, "terminal"), sent, len(data))
        finally:
            off()

    def _flash_next(self, inbox, seconds, line):
        """The next progress or terminal line, whichever arrives first."""
        try:
            return inbox.get(timeout=seconds)
        except queue.Empty:
            raise Timeout(line, seconds)

    def _flash_expect(self, inbox, seconds, line, kind):
        """Wait for one line of `kind`, discarding progress lines before it."""
        deadline = time.monotonic() + seconds
        while True:
            got, msg = self._flash_next(inbox, max(0.0, deadline - time.monotonic()), line)
            if got == kind:
                if not msg.get("ok"):
                    raise CommandError(line, msg)
                return msg

    # test pattern, §9.10
    def test(self, mode="off", arg=None):
        if mode not in ("walk", "hold", "all", "off"):
            raise ValueError("test mode must be walk/hold/all/off, not %r" % (mode,))
        return self.command("test %s%s" % (mode, "" if arg is None else " %d" % arg))

    # ---- LEDs, §13.5 --------------------------------------------------------

    def led_line(self, btn, colour, fx=None, press=None):
        """The `led` command line, without sending it. LedLayer diffs on this."""
        parts = ["led", str(_int_in("btn", btn, 1, 32)), leds.rgb(colour)]
        effect, feedback = leds.fx(fx), leds.press(press)
        if feedback is not None and effect is None:
            effect = "solid"                            # `press` is positional
        if effect is not None:
            parts.append(effect)
        if feedback is not None:
            parts.append(feedback)
        return " ".join(parts)

    def led_tile_line(self, addr, index, colour, fx=None):
        parts = ["led", "tile", str(_int_in("addr", addr, 1, 31)),
                 str(_int_in("led", index, 0, 31)), leds.rgb(colour)]
        effect = leds.fx(fx)
        if effect is not None:
            parts.append(effect)
        return " ".join(parts)

    def led(self, btn, colour, fx=None, press=None):
        """Set one logical button's live colour."""
        return self.command(self.led_line(btn, colour, fx, press))

    def led_tile(self, addr, index, colour, fx=None):
        """Set one LED by tile address and index, for LEDs not under a key."""
        return self.command(self.led_tile_line(addr, index, colour, fx))

    def led_all(self, colour):
        """Every LED on every tile. `led_all("off")` is the panic button."""
        return self.command("led all %s" % leds.rgb(colour))

    def led_reset(self):
        return self.command("led reset")

    def led_layer(self):
        """A diffing writer for a loop that recomputes every LED each pass."""
        return leds.LedLayer(self)

    # ---- display tiles, §14.4 ----------------------------------------------

    def disp_color(self, addr, refresh=False):
        """Does the tile at `addr` have a colour panel? None if not known.

        §9.5 gives colour no field of its own -- it is `CAP_DISP_COLOR`, bit 6
        of `caps`, and nothing else (§14.2) -- so this reads the bit. Cached,
        because the alternative is a `tiles` round trip for every coloured row
        of every redraw. `replace` drops the cache; for a tile swapped for a
        different type behind this client's back, pass `refresh`.
        """
        addr = _int_in("addr", addr, 1, 31)
        if refresh or addr not in self._caps:
            self._caps[addr] = (self.tile(addr) or {}).get("caps")
        caps = self._caps[addr]
        return None if caps is None else bool(caps & display.CAP_DISP_COLOR)

    def disp_text(self, addr, row, text, attr=None, *, col=0,
                  fg=None, bg=None, inverse=False, double=False):
        """One row of text, optionally styled.

        The short form is column 0 with no attributes; anything else takes the
        long form, which is the only way to reach inverse, double height and
        the colour of §14.7. Style it the way `display.attr` reads:

            board.disp_text(3, 0, "BOOST", "red")
            board.disp_text(3, 1, "ABORT", fg="black", bg="red")
            board.disp_text(3, 2, "OK", 3)              # a raw ATTR still works

        `col` is keyword-only: the style is what a caller reaches for, and
        putting it fourth is what makes the common line read.

        Colour on a mono tile is refused here rather than on the wire. The
        board refuses it too (§14.4), but as `args`, which is also what a bad
        row and a bad character come back as; this names the reason.
        """
        style = display.attr(attr, fg=fg, bg=bg, inverse=inverse, double=double)
        if display.is_color(style) and self.disp_color(addr) is False:
            raise ValueError(
                "tile %d has no colour panel (no CAP_DISP_COLOR): it refuses "
                "the colour bits rather than drawing white (§14.7)" % (addr,))
        if col or style:
            return self.command("disp %d text %d %d %d %s" % (
                _int_in("addr", addr, 1, 31), _int_in("row", row, 0, 31),
                _int_in("col", col, 0, 255), style,
                display.ascii_text(text)))
        return self.command("disp %d %d %s" % (_int_in("addr", addr, 1, 31),
                                               _int_in("row", row, 0, 31),
                                               display.ascii_text(text)))

    def disp_clear(self, addr):
        return self.command("disp %d clear" % _int_in("addr", addr, 1, 31))

    def disp_bar(self, addr, row, col, width, value):
        return self.command("disp %d bar %d %d %d %d" % (
            _int_in("addr", addr, 1, 31), _int_in("row", row, 0, 31),
            _int_in("col", col, 0, 255), _int_in("width", width, 1, 255),
            _int_in("value", value, 0, 255)))

    def disp_show(self, addr):
        return self.command("disp %d show" % _int_in("addr", addr, 1, 31))

    def disp_contrast(self, addr, level):
        """Panel brightness 0-255. Not part of a commit: it applies at once."""
        return self.command("disp %d contrast %d" % (
            _int_in("addr", addr, 1, 31), _int_in("level", level, 0, 255)))

    # ---- stored pictures, §14.9 --------------------------------------------

    def disp_slots(self, addr, refresh=False):
        """How many pictures the tile at `addr` can keep, 0 if it has no store.

        §14.9 gives the store no capability bit: the slot count in the `tiles`
        entry IS the advertisement, exactly as `pxW`/`pxH` are for a streamed
        picture. Cached like `disp_color`, and cleared by `replace`.
        """
        addr = _int_in("addr", addr, 1, 31)
        if refresh or addr not in self._slots:
            self._slots[addr] = (self.tile(addr) or {}).get("slots", 0)
        return self._slots[addr] or 0

    def _disp(self, line, tries=DISP_BUSY_TRIES, wait=DISP_BUSY_WAIT):
        """One `disp` line, re-sent while the module answers `busy`.

        `busy` is backpressure, not a fault: the auxiliary slot carries one
        command per poll cycle (§3.2) against a queue eight deep, so anything
        that sends more than eight lines in a row outruns the bus and is
        refused partway -- a stored colour picture is 512 DATA lines. §14.9
        adds a second source of the same answer, the picture flash itself,
        which NAKs for ~0.7 ms after a page program and tens of milliseconds
        after an erase. Both read identically here, which is the point.

        A refused command was never queued, so re-sending it is the only way
        it reaches the tile. Every other error is a rejection of the command
        and would be rejected again just as fast, so only `busy` retries.
        """
        for attempt in range(tries):
            try:
                return self.command(line)
            except CommandError as e:
                if e.response.get("err") != "busy":
                    raise
                if attempt + 1 >= tries:
                    raise
                time.sleep(wait)

    def disp_slot_begin(self, addr, slot, fmt="rgb565", attr=None, *,
                        fg=None, bg=None, inverse=False):
        """Erase a slot and arm it for writes (§14.9).

        `fmt` is `"1bpp"` or `"rgb565"`. The attribute is §14.7's and applies
        to 1 bpp only -- it is the ink and paper the tile expands the bits
        into; RGB565 carries its colour in the pixels and its attribute must
        be zero, which is checked here rather than on the wire.
        """
        fmt = display.slot_format(fmt)
        style = display.attr(attr, fg=fg, bg=bg, inverse=inverse)
        if fmt == "rgb565" and style:
            raise ValueError("RGB565 carries its own colour: its ATTR must be "
                             "0, not %d (§14.9)" % (style,))
        if display.is_color(style) and self.disp_color(addr) is False:
            raise ValueError(
                "tile %d has no colour panel (no CAP_DISP_COLOR): it refuses "
                "the colour bits rather than drawing white (§14.7)" % (addr,))
        return self._disp("disp %d slot %d begin %s %d" % (
            _int_in("addr", addr, 1, 31), _int_in("slot", slot, 0, 255),
            fmt, style))

    def disp_slot_data(self, addr, slot, off, data):
        """One DATA chunk at `off`, 1-64 bytes. `data` is bytes or a hex str.

        Offsets are strictly ascending and contiguous: OFF must equal the
        number of bytes the tile has already accepted (§14.9). Prefer
        `disp_slot_store`, which does the whole transfer.
        """
        payload = data if isinstance(data, str) else bytes(data).hex().upper()
        if not payload or len(payload) % 2 or len(payload) // 2 > display.SLOT_CHUNK:
            raise ValueError("a DATA chunk is 1-%d bytes, not %d"
                             % (display.SLOT_CHUNK, len(payload) / 2))
        return self._disp("disp %d slot %d data %d %s" % (
            _int_in("addr", addr, 1, 31), _int_in("slot", slot, 0, 255),
            _int_in("off", off, 0, 0xFFFF), payload))

    def disp_slot_end(self, addr, slot, crc16):
        """Commit a slot against a CRC-16 over every data byte (§14.9).

        The header is written last and that IS the commit, so a mismatch
        leaves the slot empty rather than holding a picture with a hole in it
        -- as does a transfer that simply stops. Note that `disp` is
        fire-and-forget (§14.4): the board ACKs the line once it is queued, so
        the tile's own verdict on the CRC (`flash`, §9.2) is not something a
        caller can rely on seeing. Showing the slot is the check that costs
        nothing.
        """
        return self._disp("disp %d slot %d end %d" % (
            _int_in("addr", addr, 1, 31), _int_in("slot", slot, 0, 255),
            _int_in("crc16", crc16, 0, 0xFFFF)))

    def disp_slot_show(self, addr, slot):
        """Put a stored picture on the panel. One frame, read from the tile's
        own flash, and it owns the panel until CLEAR (§14.9). `args` means the
        slot is empty."""
        return self._disp("disp %d slot %d show" % (
            _int_in("addr", addr, 1, 31), _int_in("slot", slot, 0, 255)))

    def disp_slot_erase(self, addr, slot):
        """Drop a slot's contents."""
        return self._disp("disp %d slot %d erase" % (
            _int_in("addr", addr, 1, 31), _int_in("slot", slot, 0, 255)))

    def disp_slot_info(self, addr, slot):
        """What a slot holds, from its header: one frame (§14.9).

        Returns None for an empty slot, else a dict with `fmt` ("1bpp" or
        "rgb565"), `attr`, `len` and `crc` -- the CRC-16 of the stored bytes,
        which is enough to recognise a picture already seen without reading it.
        """
        r = self._disp("disp %d slot %d info" % (
            _int_in("addr", addr, 1, 31), _int_in("slot", slot, 0, 255)))
        if r.get("empty"):
            return None
        return {k: r[k] for k in ("fmt", "attr", "len", "crc")}

    def disp_slot_read(self, addr, slot, *, on_progress=None):
        """Read a stored picture back: INFO, then every byte (§14.9).

        Returns None for an empty slot, else `disp_slot_info`'s dict with the
        bytes under `data`, checked against the CRC the header carries. READ
        is 16 bytes a frame, the most that fits inside T_RESP_MAX, so a 32 KB
        colour picture is 2,048 of them: seconds, not milliseconds. Check
        `disp_slot_info` first when all you need to know is which picture it is.
        """
        info = self.disp_slot_info(addr, slot)
        if info is None:
            return None
        data = bytearray()
        while len(data) < info["len"]:
            n = min(display.SLOT_READ, info["len"] - len(data))
            r = self._disp("disp %d slot %d read %d %d" % (addr, slot, len(data), n))
            data += bytes.fromhex(r["hex"])
            if on_progress is not None:
                on_progress(len(data), info["len"])
        if crc16_ccitt_false(bytes(data)) != info["crc"]:
            raise ValueError("slot %d on tile %d changed while it was being read: "
                             "its CRC no longer matches the header" % (slot, addr))
        return dict(info, data=bytes(data))

    def disp_slot_store(self, addr, slot, data, fmt="rgb565", attr=None, *,
                        fg=None, bg=None, inverse=False, on_progress=None):
        """Load a whole picture into a slot: BEGIN, every DATA chunk, END.

        `data` is the encoded picture -- `display.picture_bytes` makes one from
        an image file, or `disp_store_image` does both in one call. Returns the
        END response.

        This is the one config-channel operation measured in seconds: 32,768
        bytes of colour is 512 chunks and 2-4 s of auxiliary slot (§14.9). It
        is spent once, at configuration time; what it buys is a legend change
        that costs a single SHOW frame afterwards. `on_progress(done, total)`
        is called per chunk, because a caller with no progress line during it
        looks hung.
        """
        data = bytes(data)
        if not data:
            raise ValueError("a stored picture cannot be empty")
        self.disp_slot_begin(addr, slot, fmt, attr, fg=fg, bg=bg, inverse=inverse)
        done = 0
        for off, payload in display.slot_chunks(data):
            self.disp_slot_data(addr, slot, off, payload)
            done += len(payload) // 2
            if on_progress is not None:
                on_progress(done, len(data))
        return self.disp_slot_end(addr, slot, crc16_ccitt_false(data))

    def disp_store_image(self, addr, slot, source, fmt="rgb565", *,
                         paper="black", invert=False, contain=True,
                         px_w=None, px_h=None, show=False, on_progress=None):
        """Encode an image file for this tile's panel and store it in a slot.

        The whole of §14.9 in one call, which is what configuration time
        actually looks like:

            board.disp_store_image(5, 0, "logo.png")             # full colour
            board.disp_store_image(5, 1, "abort.png", "1bpp", fg="black", bg="red")
            board.disp_slot_show(5, 0)                           # one frame

        Geometry comes from the tile's descriptor (`pxW`/`pxH` in the §9.5
        entry) unless given, so this follows the tile rather than the type.
        Needs Pillow; `disp_slot_store` does not.
        """
        entry = self.tile(addr) or {}
        px_w = px_w if px_w is not None else entry.get("pxW")
        px_h = px_h if px_h is not None else entry.get("pxH")
        if not px_w or not px_h:
            raise ValueError("tile %d reports no pixel geometry, so it takes no "
                             "pictures (§14.8)" % (addr,))
        if not entry.get("slots"):
            raise ValueError("tile %d has no picture store: `slots` is the "
                             "advertisement and it reports none (§14.9)" % (addr,))
        data = display.picture_bytes(source, px_w, px_h, fmt,
                                     paper=paper, invert=invert, contain=contain)
        response = self.disp_slot_store(addr, slot, data, fmt,
                                        on_progress=on_progress)
        if show:
            return self.disp_slot_show(addr, slot)
        return response

    # ---- piezo sound, §16 (reserved: no tile has a sounder yet) ------------

    def sound(self, addr, freq, ms):
        """One note on a tile's piezo. `freq` 0 is a rest."""
        return self.command("sound %d %s" % (_int_in("addr", addr, 1, 31),
                                             sound.note(freq, ms).replace(":", " ")))

    def sound_play(self, addr, notes, repeat=None):
        """Play a sequence: a tune name, `"freq:ms,..."`, or (freq, ms) pairs.

        A named tune carries its own repeat count -- "alarm" repeats until
        stopped -- which an explicit `repeat` overrides.
        """
        text, tune_repeat = sound.sequence(notes)
        if repeat is None:
            repeat = tune_repeat
        line = "sound %d play %s" % (_int_in("addr", addr, 1, 31), text)
        if repeat is not None:
            line += " repeat %d" % _int_in("repeat", repeat, 0, 255)
        return self.command(line)

    def sound_stop(self, addr=None):
        """Silence one tile, or every tile. `sound_stop()` is the panic button:
        the master gives a broadcast STOP priority over the auxiliary queue."""
        return self.command("sound all stop" if addr is None
                            else "sound %d stop" % _int_in("addr", addr, 1, 31))

    def sound_volume(self, addr, volume):
        """Tile piezo volume, 0-255. 0 mutes. Persisted by `save`."""
        return self.command("sound %d volume %d" % (_int_in("addr", addr, 1, 31),
                                                    _int_in("volume", volume, 0, 255)))

    def screen(self, addr, cols=None, rows=None):
        """A row-diffing writer for a display tile, §14.5."""
        return display.Screen(self, addr, cols=cols, rows=rows)

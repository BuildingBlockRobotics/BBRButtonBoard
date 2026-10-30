"""Colour parsing and the live LED layer (PROTOCOL.md §13.5)."""
from __future__ import annotations

OFF = "off"

FX = ("solid", "blink", "fast", "breathe")

# Enough named colours to write a Driver Station app without a colour picker.
NAMED = {
    "off": OFF, "black": "000000", "white": "FFFFFF",
    "red": "FF0000", "green": "00FF00", "blue": "0000FF",
    "yellow": "FFFF00", "cyan": "00FFFF", "magenta": "FF00FF",
    "orange": "FF8800", "purple": "8000FF", "pink": "FF40A0",
    "amber": "FFBF00", "lime": "80FF00", "teal": "00FF80",
    # FRC alliance colours, the two a companion app reaches for first.
    "alliance_red": "FF0000", "alliance_blue": "0000FF",
}


def rgb(value):
    """Normalise a colour to what the line protocol wants: 6 hex digits or `off`.

    Accepts a name from NAMED, "#RRGGBB" or "RRGGBB", an (r, g, b) triple of
    0-255, an int 0xRRGGBB, or None/"off" for off.
    """
    if value is None:
        return OFF
    if isinstance(value, str):
        text = value.strip()
        low = text.lower()
        if low in NAMED:
            return NAMED[low]
        if text.startswith("#"):
            text = text[1:]
        if len(text) == 6:
            try:
                int(text, 16)
            except ValueError:
                raise ValueError("not a colour: %r" % value)
            return text.upper()
        raise ValueError("not a colour: %r" % value)
    if isinstance(value, bool):                         # bool is an int; refuse it
        raise ValueError("not a colour: %r" % value)
    if isinstance(value, int):
        if not 0 <= value <= 0xFFFFFF:
            raise ValueError("colour out of range: %r" % value)
        return "%06X" % value
    if isinstance(value, (tuple, list)) and len(value) == 3:
        out = ""
        for c in value:
            c = int(c)
            if not 0 <= c <= 255:
                raise ValueError("colour component out of range: %r" % (value,))
            out += "%02X" % c
        return out
    raise ValueError("not a colour: %r" % (value,))


def fx(value):
    if value is None:
        return None
    text = str(value).lower()
    if text not in FX:
        raise ValueError("fx must be one of %s, not %r" % (", ".join(FX), value))
    return text


def press(value):
    """`press` is `on` or `off` on the wire; accept bools too."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value).lower()
    if text in ("on", "off"):
        return text
    raise ValueError("press must be on/off, not %r" % (value,))


class LedLayer:
    """Diffing writer for the live layer.

    A companion app recomputes every LED from NetworkTables each loop, but
    §13.5's live layer is last-writer-wins per LED and the bus only carries
    what changed. Staging through this class means a 50 Hz loop that sets the
    same colours sends nothing at all; only real changes reach the board.

        leds = LedLayer(board)
        leds.set(3, "red" if arm_out_of_bounds else "off")
        leds.flush()
    """

    def __init__(self, board):
        self.board = board
        self._sent = {}                                 # key -> command line
        self._staged = {}

    def set(self, btn, colour, fx_=None, press_=None):
        """Stage a colour for a logical button (1-32)."""
        line = self.board.led_line(btn, colour, fx_, press_)
        self._staged[("btn", int(btn))] = line
        return self

    def set_tile(self, addr, index, colour, fx_=None):
        """Stage a colour for one LED by tile address and index, for LEDs that
        are not under a bound key (§9.2 `led tile`)."""
        line = self.board.led_tile_line(addr, index, colour, fx_)
        self._staged[("tile", int(addr), int(index))] = line
        return self

    def all(self, colour):
        """`led all` — clears everything this layer thinks it knows, because
        the board applies it to every LED on every tile."""
        self.board.led_all(colour)
        self._sent.clear()
        self._staged.clear()
        return self

    def reset(self):
        """Discard the live layer; LEDs return to the map's defaults (§13.5)."""
        self.board.led_reset()
        self._sent.clear()
        self._staged.clear()
        return self

    def flush(self):
        """Send the staged lines that differ from what was last sent.

        Returns the number of commands actually written.
        """
        sent = 0
        for key, line in self._staged.items():
            if self._sent.get(key) == line:
                continue
            self.board.command(line)
            self._sent[key] = line
            sent += 1
        self._staged.clear()
        return sent

    def forget(self):
        """Drop the diff state, so the next flush re-sends everything. Use
        after the board reboots or a tile rejoins under a different map."""
        self._sent.clear()
        return self

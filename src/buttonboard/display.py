"""Display tiles (PROTOCOL.md §14).

Two panels exist. `disp1` is a 128x32 mono SSD1306, which is 21 x 4 cells of
the 6x8 font; `screenkey1` is a 128x128 colour ST7735 in a key cap, 10 x 8
cells of the 12x16 font. Always prefer the geometry the tile reports in
`tiles` (§9.5): the constants below are only the fallback for a board too old
to report any, and they are the mono panel's.
"""
from __future__ import annotations

DEFAULT_COLS = 21                  # 128x32 panel, 6x8 font (§14.1)
DEFAULT_ROWS = 4

MAX_TEXT = 64                      # DISPLAY TEXT carries 1..64 chars (§14.3)

CAP_DISP_COLOR = 1 << 6            # `caps` bit 6 in the §9.5 entry; protocol.h

# The ATTR byte of DISPLAY TEXT (§14.3, §14.7), mirroring shared/protocol.h.
ATTR_INVERSE = 0x01
ATTR_DOUBLE = 0x02
ATTR_FG_SHIFT = 2
ATTR_BG_SHIFT = 5
ATTR_COLOR_MASK = 0xFC             # the six bits a mono tile refuses
ATTR_MAX = 0xFF                    # every bit is defined; none are reserved

# §14.7's palette. Index 0 is "default" -- white as ink, black as paper --
# and NOT black, so an ATTR of 0 still means legible text on a colour tile.
COLORS = {"default": 0, "red": 1, "green": 2, "yellow": 3,
          "blue": 4, "magenta": 5, "cyan": 6, "white": 7}


def ascii_text(text, cols=None, pad=False):
    """Coerce to what DISPLAY TEXT accepts: printable ASCII, 0x20-0x7E (§14.3).

    Anything else becomes '?' rather than raising, because the writer is a
    companion app pushing strings it got from NetworkTables and the board
    "never interprets the content" (§14.4). Clipped at `cols`, since the tile
    clips at the edge anyway and sending more just wastes bus.
    """
    out = "".join(c if 0x20 <= ord(c) <= 0x7E else "?" for c in str(text))
    limit = MAX_TEXT if cols is None else min(cols, MAX_TEXT)
    out = out[:limit]
    if pad and cols:
        out = out.ljust(cols)
    return out


def _is_black(value):
    """Black is the one colour with no palette index of its own (§14.7)."""
    return isinstance(value, str) and value.strip().lower() == "black"


def _index(value, field):
    """One palette index: a name from COLORS, an int 0-7, or None for default."""
    if value is None:
        return COLORS["default"]
    if isinstance(value, bool):                         # bool is an int; refuse it
        raise ValueError("%s is not a display colour: %r" % (field, value))
    if isinstance(value, int):
        if not 0 <= value <= 7:
            raise ValueError("%s palette index must be 0-7, not %r" % (field, value))
        return value
    name = str(value).strip().lower()
    if name in COLORS:
        return COLORS[name]
    raise ValueError("%s is not a display colour: %r (try %s or black)"
                     % (field, value, ", ".join(sorted(COLORS))))


def _fg_index(index):
    """White in the foreground field is index 0's own resolution (§14.7), so
    spell it that way: it renders the same and it costs no colour bit, which
    means "white on black" still reaches a mono panel instead of being refused
    for asking a monochrome tile for a colour."""
    return COLORS["default"] if index == COLORS["white"] else index


def _parse_spec(text):
    """`"red on white double"` -> (fg, bg, inverse, double).

    Written for the way a person says it out loud, because the alternative is
    an integer whose meaning is three shifts and a table away.
    """
    fg = bg = None
    inverse = double = False
    field, seen = "fg", set()
    for word in text.replace(",", " ").split():
        low = word.lower()
        if low == "on":
            field = "bg"
            continue
        if low == "inverse":
            inverse = True
            continue
        if low == "double":
            double = True
            continue
        if field in seen:
            raise ValueError("two %s colours in %r" % (field, text))
        seen.add(field)
        if field == "fg":
            fg = low
        else:
            bg = low
    return fg, bg, inverse, double


def attr(spec=None, fg=None, bg=None, inverse=False, double=False):
    """The ATTR byte of DISPLAY TEXT (§14.3, §14.7), said out loud.

        attr()                          0   -- default ink on default paper
        attr("red")                         -- red on the default background
        attr("red on white")
        attr("black on yellow")             -- see below
        attr("white on blue double")
        attr(fg="cyan", inverse=True)
        attr(12)                            -- an int is already an ATTR byte

    `spec` may also be an int, which is passed through after a range check, so
    a caller holding a raw byte does not need a second entry point.

    Two pieces of §14.7 are handled here so that no caller has to know them:
    palette index 0 is *default* rather than black, and black ink has no index
    at all -- it is reached by putting the wanted colour in the foreground
    field and setting inverse, which swaps the two after the defaults resolve.
    So `attr("black on yellow")` is yellow in the foreground field with
    inverse set, and it draws black on yellow. `inverse=True` on top of that
    means what it says: it swaps whatever you asked for.

    White in the foreground is spelled as the default index, so `attr("white
    on black")` is still 0 and reaches a mono panel. Anything that does set a
    colour bit is valid only on a tile with CAP_DISP_COLOR; `Board.disp_text`
    is where that is checked.
    """
    if isinstance(spec, bool):
        raise ValueError("not an attribute: %r" % (spec,))
    if spec is not None and (fg is not None or bg is not None or inverse or double):
        raise ValueError("give `attr` a spec or the fg/bg/inverse/double "
                         "arguments, not both")
    if isinstance(spec, int):
        if not 0 <= spec <= ATTR_MAX:
            raise ValueError("attr must be 0-%d, not %r" % (ATTR_MAX, spec))
        return spec
    if spec is not None:
        fg, bg, inverse, double = _parse_spec(str(spec))

    if _is_black(fg):
        # There is no black index, so ask for the background in the foreground
        # field and let inverse swap it into the ink (§14.7). Black paper is
        # index 0's own resolution, which is why the background field is 0 --
        # and why a black background here would be black on black.
        back = 0 if (bg is None or _is_black(bg)) else _index(bg, "bg")
        if back == COLORS["default"]:
            raise ValueError('black ink needs a background: try "black on yellow"')
        value = _fg_index(back) << ATTR_FG_SHIFT
        inverse = not inverse
    else:
        value = _fg_index(_index(fg, "fg")) << ATTR_FG_SHIFT
        if not _is_black(bg):                           # black paper is the default
            value |= _index(bg, "bg") << ATTR_BG_SHIFT
    if inverse:
        value |= ATTR_INVERSE
    if double:
        value |= ATTR_DOUBLE
    return value


# Alias, so that a method whose keyword argument is `attr` (which is what the
# line protocol calls the field) can still reach this function.
_attr_byte = attr


def is_color(value):
    """True if this ATTR byte needs CAP_DISP_COLOR (§14.7)."""
    return bool(value & ATTR_COLOR_MASK)


class Screen(object):
    """Row-diffing writer for one display tile.

    A full four-row redraw is four auxiliary slots, about 20 ms at 8 tiles
    (§14.5), so a loop that redraws every pass would eat the display budget
    for no reason. Stage rows, then flush: only changed rows go out, followed
    by one SHOW so the panel never tears (§14.3 op 0x03).

        screen = board.screen(4)
        screen.text(0, "AUTO  12.4s")
        screen.text(1, "LOW BATT", fg="black", bg="red")   # colour tile only
        screen.bar(3, 0, 21, int(255 * battery))
        screen.flush()
    """

    def __init__(self, board, addr, cols=None, rows=None):
        self.board = board
        self.addr = addr
        # None means "not asked": with both geometries given there is no
        # descriptor to read the capability out of, so leave the colour check
        # to the board rather than guess a tile is mono.
        self.color = None
        if cols is None or rows is None:
            cols, rows, caps = self._ask(board, addr, cols, rows)
            if caps is not None:
                self.color = bool(caps & CAP_DISP_COLOR)
        self.cols = cols
        self.rows = rows
        self._sent = {}
        self._staged = {}

    @staticmethod
    def _ask(board, addr, cols, rows):
        """Take the geometry from the tile's descriptor when it reports one.

        §14.2 puts DISP_COLS/DISP_ROWS in the descriptor and §9.5's `tiles`
        entry passes them on as `cols`/`rows`. Fall back to the §14.1 panel
        for a board too old to report them, rather than refusing to work.
        Returns the geometry and the `caps` word, colour being a capability
        bit and not a field of its own (§14.2).
        """
        entry = None
        try:
            entry = board.tile(addr)
        except Exception:
            pass
        entry = entry or {}
        return (cols if cols is not None else entry.get("cols", DEFAULT_COLS),
                rows if rows is not None else entry.get("rows", DEFAULT_ROWS),
                entry.get("caps"))

    def text(self, row, value, attr=None, fg=None, bg=None,
             inverse=False, double=False):
        """Stage one row of text, in one style.

        Padded to the full width so that a shorter string erases what the last
        one left behind -- and, on a colour tile, so that a background colour
        fills the whole row rather than stopping at the last glyph, since
        colour is an attribute of a cell (§14.7).

        The style arguments are `attr()`'s: `screen.text(0, "LOW", "red")`,
        `screen.text(0, "LOW", fg="black", bg="red")`. One style per row is
        all the diff has room for; a row of mixed colours is several
        `board.disp_text` calls at different columns, without the diffing.

        `double` is the caller's to lay out: it draws into this row and the
        next (§14.3), and the screen does not know that the row below is
        spoken for.
        """
        if not 0 <= row < self.rows:
            raise ValueError("row must be 0-%d, not %r" % (self.rows - 1, row))
        style = _attr_byte(attr, fg=fg, bg=bg, inverse=inverse, double=double)
        if is_color(style) and self.color is False:
            # Caught here rather than in `flush` so that the traceback points
            # at the row that asked for a colour the panel does not have.
            raise ValueError("tile %d has a monochrome panel: it refuses the "
                             "colour bits rather than drawing white (§14.7)"
                             % (self.addr,))
        self._staged[row] = ("text", ascii_text(value, self.cols, pad=True), style)
        return self

    def bar(self, row, col, width, value):
        """Stage a horizontal bar, `width` cells wide, filled value/255."""
        if not 0 <= row < self.rows:
            raise ValueError("row must be 0-%d, not %r" % (self.rows - 1, row))
        self._staged[row] = ("bar", int(col), int(width), int(value))
        return self

    def clear(self):
        """Clear the panel now, and forget what was on it."""
        self.board.disp_clear(self.addr)
        self._sent.clear()
        self._staged.clear()
        return self

    def flush(self, show=True):
        """Write the rows that changed; commit with SHOW. Returns rows written."""
        written = 0
        for row in sorted(self._staged):
            item = self._staged[row]
            if self._sent.get(row) == item:
                continue
            if item[0] == "text":
                self.board.disp_text(self.addr, row, item[1], attr=item[2])
            else:
                self.board.disp_bar(self.addr, row, item[1], item[2], item[3])
            self._sent[row] = item
            written += 1
        self._staged.clear()
        if written and show:
            # The module SHOWs by itself 20 ms after the last disp (§14.4);
            # asking explicitly makes the commit atomic instead of timed.
            self.board.disp_show(self.addr)
        return written

    def forget(self):
        """Drop the diff state so the next flush redraws every row."""
        self._sent.clear()
        return self


# ---- stored pictures, §14.9 ------------------------------------------------
#
# A tile whose `tiles` entry carries `slots` keeps whole pictures in a local
# SPI flash. Loading one costs the 2-4 s of auxiliary slot that §14.8 refused
# to spend on a colour picture -- but it costs it ONCE, at configuration time,
# and every later change of legend is a single SHOW frame. That is what puts
# RGB565 back on the table: the objection to 16 bits a pixel was always the
# bus, never the tile, and a shown picture never crosses the bus at all.

SLOT_CHUNK = 64                    # DATA carries 1..64 bytes (§14.9)
SLOT_READ = 16                     # READ returns 1..16: what fits inside T_RESP_MAX (§14.9)

# FMT 0x01 is §14.8's 1 bpp encoding, expanded through ATTR; FMT 0x02 is
# RGB565, two bytes a pixel, big endian, and its ATTR must be zero.
SLOT_FORMATS = ("1bpp", "rgb565")

# §14.7's palette as RGB, the values `web/src/dispattr.js` previews with. Used
# only to letterbox and to composite transparency for FMT 0x02, which carries
# no attribute: a stored colour picture's paper is in its pixels.
PALETTE_RGB = {
    "black": (0, 0, 0), "red": (255, 0, 0), "green": (0, 255, 0),
    "yellow": (255, 255, 0), "blue": (0, 0, 255), "magenta": (255, 0, 255),
    "cyan": (0, 255, 255), "white": (255, 255, 255),
}


def slot_format(fmt):
    """Canonical format word for `disp <addr> slot <n> begin <fmt> <attr>`."""
    name = str(fmt).strip().lower()
    if name not in SLOT_FORMATS:
        raise ValueError("picture format must be one of %s, not %r"
                         % (", ".join(SLOT_FORMATS), fmt))
    return name


def slot_size(fmt, px_w, px_h):
    """Bytes a whole picture takes in a slot. 2,048 at 1 bpp; 32,768 in colour."""
    if px_w % 8:
        raise ValueError("panel width %d is not a whole number of bytes at "
                         "1 bpp (§14.8)" % (px_w,))
    return px_w * px_h * 2 if slot_format(fmt) == "rgb565" else (px_w // 8) * px_h


def slot_chunks(data, size=SLOT_CHUNK):
    """(offset, hex) for each DATA line, ascending and contiguous.

    §14.9 requires exactly that: OFF must equal the number of bytes already
    accepted, which is what lets the tile tell a retried chunk from a dropped
    one. `ERR_BUSY` flow control means a sender repeats itself, and a picture
    with a stripe missing is worse than a refusal.
    """
    data = bytes(data)
    for off in range(0, len(data), size):
        yield off, data[off:off + size].hex().upper()


def _pillow(what):
    try:
        from PIL import Image, ImageOps                  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "%s needs Pillow (pip install pillow). Pass the encoded bytes to "
            "`disp_slot_store` instead if you would rather not have it." % (what,))
    return Image, ImageOps


def _fit(image, px_w, px_h, paper, contain=True):
    """Scale onto a px_w x px_h RGB canvas of `paper`, letterboxed not cropped.

    Letterboxed because a legend is usually a logo or a wordmark and cropping
    takes the edges off it; composited onto a paper colour because a
    transparent PNG's alpha IS the shape, so something has to be behind it.
    """
    Image, _ = _pillow("encoding a picture")
    if isinstance(paper, str):
        if paper.strip().lower() not in PALETTE_RGB:
            raise ValueError("paper must be one of %s, not %r"
                             % (", ".join(sorted(PALETTE_RGB)), paper))
        paper = PALETTE_RGB[paper.strip().lower()]
    src = image.convert("RGBA")
    if contain:
        scale = min(px_w / src.width, px_h / src.height)
        size = (max(1, round(src.width * scale)), max(1, round(src.height * scale)))
    else:
        size = (px_w, px_h)
    src = src.resize(size, Image.LANCZOS)
    canvas = Image.new("RGBA", (px_w, px_h), tuple(paper) + (255,))
    canvas.paste(src, ((px_w - size[0]) // 2, (px_h - size[1]) // 2), src)
    return canvas.convert("RGB")


def encode_rgb565(image, px_w, px_h, paper="black", contain=True):
    """A picture as FMT 0x02: two bytes a pixel, big endian (§14.9)."""
    fitted = _fit(image, px_w, px_h, paper, contain)
    out = bytearray(px_w * px_h * 2)
    for i, (r, g, b) in enumerate(fitted.getdata()):
        value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[i * 2] = (value >> 8) & 0xFF                # big endian: high byte first
        out[i * 2 + 1] = value & 0xFF
    return bytes(out)


def encode_1bpp(image, px_w, px_h, invert=False, contain=True):
    """A picture as FMT 0x01: 1 bpp, MSB the leftmost pixel (§14.8).

    Dithered (Floyd-Steinberg, which is what Pillow's `convert("1")` does)
    rather than hard-thresholded, because a logo thresholded at 50 % loses
    every soft edge and at this size that is most of the picture.

    A **set bit is ink**, and the dither sets the dark pixels, so the default
    is a picture drawn in the ATTR's ink where the file is dark. `invert`
    swaps that; the two colours themselves are the ATTR's, given at BEGIN.
    """
    Image, ImageOps = _pillow("dithering a picture")
    # White paper, so the letterbox bars come out as paper: an unset bit IS
    # paper on the tile, and the inversion below turns white into 0.
    grey = _fit(image, px_w, px_h, "white", contain).convert("L")
    bits = grey.convert("1")                            # 1 = white, MSB leftmost
    raw = bits.tobytes()
    # PIL packs 1 for white; a set bit is ink, so the dark pixels are the ones
    # that must come out set.
    mask = 0x00 if invert else 0xFF
    return bytes(b ^ mask for b in raw)


def picture_bytes(source, px_w, px_h, fmt="rgb565", paper="black",
                  invert=False, contain=True):
    """Encode an image file (or a Pillow image) for a slot, in `fmt`.

    `source` is a path, an open binary file or a Pillow `Image`. Returns the
    exact bytes `disp_slot_store` puts on the wire, so a caller that wants to
    see or cache them can.
    """
    Image, _ = _pillow("reading a picture")
    image = source if hasattr(source, "convert") else Image.open(source)
    if slot_format(fmt) == "rgb565":
        return encode_rgb565(image, px_w, px_h, paper=paper, contain=contain)
    return encode_1bpp(image, px_w, px_h, invert=invert, contain=contain)

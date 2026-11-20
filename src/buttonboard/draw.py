"""Vector drawing on pixel display tiles (PROTOCOL.md §14.10).

A tile that reports `draw` in its `tiles` entry rasterises lines, rectangles
and text itself: the host sends end points, never pixels, so a line drawing
costs the bus a frame or two where the same picture as an image would be
forty. Nothing on the tile keeps a framebuffer, though, and the panel cannot
be read back -- so **the host owns the scene**. Erasing is drawing again in
the background colour, or putting part of a stored picture back (`slot ...
rect`), and a tile that restarts comes back blank.

`Canvas` is the part of that a script should not have to write. Shapes go on
layers; `flush()` works out what left the glass since last time, erases it,
and redraws whatever the erase cut through:

    canvas = board.canvas(6)                    # a disp2
    frame = canvas.layer()
    frame.rect(30, 14, 100, 100, "white")
    wheel = canvas.layer()
    while True:
        wheel.clear()
        wheel.box(50, 34, 10, 22, heading, "green")   # centre, size, degrees
        canvas.flush()                          # only what changed crosses the bus

Colours are RGB565 on the wire. Anything `buttonboard.rgb()` takes works here
-- a name, "#RRGGBB", an (r, g, b) triple -- and `rgb565()` is the conversion.

`Framebuffer` is the same rasteriser in Python: `MockBoard` draws into one,
so a script can be written, run and looked at (`save("out.png")`) with no
board attached.
"""
from __future__ import annotations

import math

from . import leds

DRAW_MAX = 64                      # data bytes in one DRAW frame (§14.10)
LINE_MAX = DRAW_MAX // 4           # segments in a LINE frame
POLY_MAX = DRAW_MAX // 2           # points in a POLY frame
FILL_MAX = DRAW_MAX // 4           # rectangles in a FILL frame
GLYPH_W = 12                       # DRAW TEXT's glyphs: the colour font's cell
GLYPH_H = 16
TEXT_MAX = DRAW_MAX - 4            # characters in a TEXT frame, before the panel's edge

IMAGE_MAX = 64                     # bytes in one IMAGE chunk (§14.8)


def rgb565(colour):
    """A colour as the 16-bit value DRAW carries: 5 bits red, 6 green, 5 blue.

    Takes what `buttonboard.rgb()` takes -- a name ("red", "orange",
    "alliance_blue"), "#RRGGBB", an (r, g, b) triple, an int 0xRRGGBB -- and
    None or "off" for black, which is how a drawing is erased.
    """
    text = leds.rgb(colour)
    if text == leds.OFF:
        return 0
    value = int(text, 16)
    r, g, b = value >> 16, (value >> 8) & 0xFF, value & 0xFF
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def _rgb888(value):
    """RGB565 back to an (r, g, b) triple, for a preview."""
    r, g, b = (value >> 11) & 0x1F, (value >> 5) & 0x3F, value & 0x1F
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)


# ---- clipping ---------------------------------------------------------------
#
# The tile refuses a point off the panel rather than clipping it (§14.10):
# there is no divider on an ATtiny to cut a line with. So the cut is made
# here, where there is one.

def clip_segment(x0, y0, x1, y1, w, h):
    """The part of a segment inside a w x h panel, as four ints, or None.

    Cohen-Sutherland on the real coordinates, rounded only at the end, so a
    line that leaves the panel still points where it was going.
    """
    xmax, ymax = w - 1, h - 1

    def code(x, y):
        return ((1 if x < 0 else 2 if x > xmax else 0) |
                (4 if y < 0 else 8 if y > ymax else 0))

    x0, y0, x1, y1 = float(x0), float(y0), float(x1), float(y1)
    c0, c1 = code(x0, y0), code(x1, y1)
    for _ in range(8):
        if not (c0 | c1):
            return (int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
        if c0 & c1:
            return None
        c = c0 or c1
        if c & 1:
            x, y = 0.0, y0 + (y1 - y0) * (0 - x0) / (x1 - x0)
        elif c & 2:
            x, y = float(xmax), y0 + (y1 - y0) * (xmax - x0) / (x1 - x0)
        elif c & 4:
            x, y = x0 + (x1 - x0) * (0 - y0) / (y1 - y0), 0.0
        else:
            x, y = x0 + (x1 - x0) * (ymax - y0) / (y1 - y0), float(ymax)
        if c == c0:
            x0, y0, c0 = x, y, code(x, y)
        else:
            x1, y1, c1 = x, y, code(x, y)
    return None


def clip_poly(points, w, h):
    """A polyline cut to the panel: a list of polylines, each wholly inside."""
    out, run = [], []
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        seg = clip_segment(ax, ay, bx, by, w, h)
        if seg is None:
            if run:
                out.append(tuple(run))
                run = []
            continue
        a, b = (seg[0], seg[1]), (seg[2], seg[3])
        if run and run[-1] == a:
            run.append(b)
        else:
            if run:
                out.append(tuple(run))
            run = [a, b]
    if run:
        out.append(tuple(run))
    return out


def clip_rect(x, y, w, h, pw, ph):
    """A rectangle cut to the panel, or None if nothing of it is on it."""
    x, y, w, h = int(round(x)), int(round(y)), int(round(w)), int(round(h))
    x1, y1 = min(x + w, pw), min(y + h, ph)
    x, y = max(x, 0), max(y, 0)
    if x1 <= x or y1 <= y:
        return None
    return (x, y, x1 - x, y1 - y)


def circle_points(cx, cy, r, steps=None):
    """A closed polyline round a circle. The tile draws lines, not arcs, so a
    circle is as many chords as its size needs -- 8 for a small marker, 31
    (one POLY frame) for anything large."""
    if steps is None:
        steps = max(8, min(POLY_MAX - 1, int(round(r * 1.6))))
    pts = [(cx + r * math.cos(2 * math.pi * i / steps),
            cy + r * math.sin(2 * math.pi * i / steps)) for i in range(steps)]
    return pts + [pts[0]]


def box_points(cx, cy, w, h, degrees=0.0):
    """The corners of a w x h rectangle about (cx, cy), turned `degrees`
    clockwise as it looks on the panel, closed. A wheel, a robot, a module."""
    a = math.radians(degrees)
    ca, sa = math.cos(a), math.sin(a)
    pts = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        pts.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
    return pts + [pts[0]]


# ---- the wire ---------------------------------------------------------------

def poly_frames(points):
    """A polyline as the point lists of one or more POLY frames. A frame holds
    32 points, so a longer line carries on from the last point of the frame
    before."""
    points = list(points)
    out = []
    while len(points) > 1:
        out.append(points[:POLY_MAX])
        points = points[POLY_MAX - 1:]
    return out


def image_chunks(data, px_w):
    """(row, hex) for each IMAGE chunk of a 1 bpp picture (§14.8).

    A chunk is a whole number of pixel rows and at most 64 bytes: four rows on
    a 128 px panel, three on a 160 px one.
    """
    if px_w % 8:
        raise ValueError("panel width %d is not a whole number of bytes at "
                         "1 bpp (§14.8)" % (px_w,))
    stride = px_w // 8
    if len(data) % stride:
        raise ValueError("a 1 bpp picture is whole rows of %d bytes; %d is not"
                         % (stride, len(data)))
    step = (IMAGE_MAX // stride) * stride
    for off in range(0, len(data), step):
        yield off // stride, bytes(data[off:off + step]).hex().upper()


# ---- the same rasteriser, on the host ---------------------------------------

# firmware/core/dispfont.c: 5 x 7 glyphs, column major, bit 0 the top row,
# 0x20 first. DRAW TEXT doubles them into 12 x 16 cells (skfont.c).
_FONT = bytes.fromhex(
    "000000000000005F00000007000700147F147F14242A7F2A1223130864623649552250"
    "0005030000001C2241000041221C0014083E081408083E080800503000000808080808"
    "006060000020100804023E5149453E00427F400042615149462141454B311814127F10"
    "27454545393C4A49493001710905033649494936064949291E00363600000056360000"
    "0814224100141414141400412214080201510906324979413E7E1111117E7F49494936"
    "3E414141227F4141221C7F494949417F090909013E4149497A7F0808087F00417F4100"
    "2040413F017F081422417F404040407F020C027F7F0408107F3E4141413E7F09090906"
    "3E4151215E7F09192946464949493101017F01013F4040403F1F2040201F3F4038403F"
    "631408146307087008076151494543007F41410002040810200041417F000402010204"
    "4040404040000102040020545454787F484444383844444420384444487F3854545418"
    "087E0901020C5252523E7F0804047800447D40002040443D007F1028440000417F4000"
    "7C041804787C0804047838444444387C14141408081414187C7C080404084854545420"
    "043F4440203C4040207C1C2040201C3C4030403C44281028440C5050503C4464544C44"
    "000836410000007F000000413608000804081008")


class Framebuffer(object):
    """A panel's glass in memory, drawn on exactly as the tile draws.

    The line stepping is firmware/core/draw.c's and the glyphs are the tile's
    own, so what `MockBoard` shows is what a disp2 shows -- which makes it a
    preview worth looking at. `pixel(x, y)` reads one RGB565 value back;
    `save(path)` writes it out as a PNG.
    """

    def __init__(self, width, height):
        self.width = int(width)
        self.height = int(height)
        self.px = [0] * (self.width * self.height)

    def pixel(self, x, y):
        return self.px[y * self.width + x]

    def fill(self, x, y, w, h, colour):
        for yy in range(y, y + h):
            base = yy * self.width
            self.px[base + x:base + x + w] = [colour] * w

    def clear(self, colour=0):
        self.px = [colour] * (self.width * self.height)

    def line(self, x0, y0, x1, y1, colour):
        """core/draw.c's line: the major axis always counts upwards, so a
        segment is the same pixels from either end."""
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        steep = dy > dx
        if steep:
            a0, a1, b0, b1, da, db = y0, y1, x0, x1, dy, dx
        else:
            a0, a1, b0, b1, da, db = x0, x1, y0, y1, dx, dy
        if a0 > a1:
            a0, a1, b0, b1 = a1, a0, b1, b0
        sb = 1 if b1 >= b0 else -1
        err, b = da >> 1, b0
        for a in range(a0, a1 + 1):
            if steep:
                self.px[a * self.width + b] = colour
            else:
                self.px[b * self.width + a] = colour
            if err < db:
                err += da - db
                b += sb
            else:
                err -= db

    def poly(self, points, colour):
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            self.line(ax, ay, bx, by, colour)

    def text(self, x, y, text, fg, bg):
        for i, ch in enumerate(text):
            glyph = _FONT[(ord(ch) - 0x20) * 5:(ord(ch) - 0x20) * 5 + 5]
            for py in range(GLYPH_H):
                row = (py - 1) >> 1 if 1 <= py <= 14 else None
                base = (y + py) * self.width + x + i * GLYPH_W
                for px in range(GLYPH_W):
                    col = px >> 1
                    ink = row is not None and col < 5 and glyph[col] >> row & 1
                    self.px[base + px] = fg if ink else bg

    def blit_rgb565(self, data, x=0, y=0, w=None, h=None):
        """Part of a stored RGB565 picture (two bytes a pixel, big endian, the
        panel's full width a row), at the place it came from."""
        w = self.width if w is None else w
        h = self.height if h is None else h
        for yy in range(y, min(y + h, len(data) // (self.width * 2))):
            for xx in range(x, x + w):
                o = (yy * self.width + xx) * 2
                self.px[yy * self.width + xx] = (data[o] << 8) | data[o + 1]

    def blit_1bpp(self, data, ink, paper, x=0, y=0, w=None, h=None, row0=0):
        """1 bpp rows, MSB leftmost (§14.8): `data` starts at pixel row `row0`."""
        stride = self.width // 8
        w = self.width if w is None else w
        rows = len(data) // stride
        h = rows if h is None else h
        for yy in range(max(y, row0), min(y + h, row0 + rows)):
            for xx in range(x, x + w):
                bit = data[(yy - row0) * stride + (xx >> 3)] >> (7 - (xx & 7)) & 1
                self.px[yy * self.width + xx] = ink if bit else paper

    def png(self, scale=3):
        """The glass as the bytes of a PNG file, each pixel `scale` across.
        Written out by hand, so a preview needs nothing installed."""
        import struct
        import zlib
        w, h = self.width * scale, self.height * scale
        raw = bytearray()
        for y in range(self.height):
            row = bytearray(b"\x00")                    # filter: none
            for v in self.px[y * self.width:(y + 1) * self.width]:
                row += bytes(_rgb888(v)) * scale
            raw += bytes(row) * scale

        def chunk(kind, data):
            body = kind + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))

    def save(self, path, scale=3):
        """Write the glass to `path` as a PNG."""
        with open(path, "wb") as f:
            f.write(self.png(scale))
        return path


# ---- Canvas -----------------------------------------------------------------

def _bbox(item):
    kind = item[0]
    if kind == "poly":
        xs = [p[0] for p in item[1]]
        ys = [p[1] for p in item[1]]
        return (min(xs), min(ys), max(xs), max(ys))
    if kind == "fill":
        return (item[1], item[2], item[1] + item[3] - 1, item[2] + item[4] - 1)
    return (item[1], item[2], item[1] + len(item[3]) * GLYPH_W - 1, item[2] + GLYPH_H - 1)


def _touches(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _inside(a, b):
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def _boxes(item):
    """The boxes an item's ink lies in: one per segment for a line, so a big
    outline does not count as touching everything it encloses."""
    if item[0] != "poly":
        return [_bbox(item)]
    return [(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
            for a, b in zip(item[1], item[1][1:])]


def _hits(item, rects):
    return any(_touches(box, r) for box in _boxes(item) for r in rects)


class Layer(object):
    """One object on a canvas: the shapes that move, appear and vanish together.

    Shapes accumulate until `clear()`, so something that never changes is
    staged once and something that moves is cleared and staged again each
    pass. Nothing reaches the board until the canvas is flushed.

    Coordinates are pixels from the panel's top left and may be floats; a
    shape that leaves the panel is cut at its edge. Every method returns the
    layer, so they chain.
    """

    def __init__(self, canvas):
        self.canvas = canvas
        self.items = []             # what should be on the glass
        self._sent = []             # what is

    def clear(self):
        """Stage nothing: the next flush takes this layer off the glass
        unless something is drawn on it again first."""
        self.items = []
        return self

    def line(self, x0, y0, x1, y1, colour="white"):
        return self.poly([(x0, y0), (x1, y1)], colour)

    def poly(self, points, colour="white", closed=False):
        """A connected line through `points`; `closed` joins the last to the first."""
        points = [(float(x), float(y)) for x, y in points]
        if closed and len(points) > 2:
            points.append(points[0])
        if len(points) < 2:
            raise ValueError("a line needs two points")
        c = rgb565(colour)
        for piece in clip_poly(points, self.canvas.width, self.canvas.height):
            self.items.append(("poly", piece, c))
        return self

    def rect(self, x, y, w, h, colour="white"):
        """The outline of a rectangle."""
        return self.poly([(x, y), (x + w - 1, y), (x + w - 1, y + h - 1), (x, y + h - 1)],
                         colour, closed=True)

    def fill(self, x, y, w, h, colour="white"):
        """A solid rectangle."""
        r = clip_rect(x, y, w, h, self.canvas.width, self.canvas.height)
        if r is not None:
            self.items.append(("fill",) + r + (rgb565(colour),))
        return self

    def box(self, cx, cy, w, h, degrees=0.0, colour="white"):
        """The outline of a w x h rectangle centred on (cx, cy) and turned
        `degrees` clockwise: a swerve module, a robot on a field."""
        return self.poly(box_points(cx, cy, w, h, degrees), colour)

    def circle(self, cx, cy, r, colour="white", steps=None):
        return self.poly(circle_points(cx, cy, r, steps), colour)

    def arrow(self, x0, y0, x1, y1, colour="white", head=5):
        """A line with a head at (x1, y1): a velocity, a heading."""
        self.line(x0, y0, x1, y1, colour)
        length = math.hypot(x1 - x0, y1 - y0)
        if length >= 1:
            ux, uy = (x1 - x0) / length, (y1 - y0) / length
            head = min(head, length)
            for side in (1, -1):
                self.line(x1, y1,
                          x1 - head * (ux * 0.87 - side * uy * 0.5),
                          y1 - head * (uy * 0.87 + side * ux * 0.5), colour)
        return self

    def text(self, x, y, text, fg="white", bg="black"):
        """Text in the tile's 12 x 16 glyphs, top left at (x, y). Each glyph
        is a solid cell of `bg` with `fg` ink -- there is no transparent
        paper, because there is nothing behind it to keep. Characters that
        would not fit whole on the panel are dropped."""
        from .display import ascii_text
        x, y = int(round(x)), int(round(y))
        text = ascii_text(text)
        if y < 0 or y + GLYPH_H > self.canvas.height:
            return self
        while text and x < 0:
            text, x = text[1:], x + GLYPH_W
        text = text[:max(0, (self.canvas.width - x) // GLYPH_W)][:TEXT_MAX]
        if text:
            self.items.append(("text", x, y, text, rgb565(fg), rgb565(bg)))
        return self


class Canvas(object):
    """A diffing scene on one drawing tile.

    `background` is a colour, or `slot=` names a stored picture (§14.9) to
    draw over -- a field, say, loaded once with `disp_store_image`. The
    difference is how a shape is taken off again: over a colour it is drawn
    again in that colour, over a picture the part of the picture under it is
    put back (`slot ... rect`), which is one frame however complicated the
    shape was.

    `flush()` sends only what changed. The first one clears the panel, since
    nothing is known about what is on it; so does the first after `forget()`,
    which is what to call when the tile has been away (it comes back blank).
    """

    def __init__(self, board, addr, background="black", slot=None,
                 width=None, height=None):
        self.board = board
        self.addr = addr
        self.slot = slot
        self.background = rgb565(background)
        if width is None or height is None:
            entry = board.tile(addr) or {}
            if not entry.get("draw"):
                raise ValueError(
                    "tile %d does not draw: its `tiles` entry has no `draw` "
                    "(§14.10). That takes a disp2 or a screenkey1 on firmware "
                    "that has it." % (addr,))
            width = entry.get("pxW") if width is None else width
            height = entry.get("pxH") if height is None else height
        self.width = width
        self.height = height
        self.layers = []
        self._fresh = False
        self.frames = 0             # DRAW and RECT frames sent, for anyone counting

    def layer(self):
        """A new layer, drawn over the ones made before it."""
        layer = Layer(self)
        self.layers.append(layer)
        return layer

    def forget(self):
        """Assume nothing is on the panel: the next flush clears and redraws."""
        self._fresh = False
        return self

    def clear(self):
        """Clear the panel to the background now. Layers keep their shapes and
        the next flush draws them all again."""
        if self.slot is not None:
            self.board.disp_slot_show(self.addr, self.slot)
        else:
            self.board.disp_draw_fill(self.addr, self.background,
                                      [(0, 0, self.width, self.height)], raw=True)
        self.frames += 1
        for layer in self.layers:
            layer._sent = []
        self._fresh = True
        return self

    # -- sending ---------------------------------------------------------------

    def _send(self, items, colour=None):
        """Draw `items`, packed into as few frames as their order allows.
        `colour` overrides theirs: that is an erase.

        A frame is one colour and one kind, so shapes are gathered into
        batches of a kind -- and a shape may join an earlier batch, ahead of
        things that were staged before it, as long as it touches none of
        them: where two shapes do not overlap, which went down first cannot
        be seen. Four green wheels and four yellow arrows are two frames, not
        eight.
        """
        batches = []                        # [key, items, boxes], in drawing order
        for item in items:
            kind = item[0]
            erase_text = kind == "text" and colour is not None
            c = colour if colour is not None else (item[4] if kind == "text" else item[-1])
            if kind == "text" and not erase_text:
                key = None                  # a frame of its own
            else:
                key = ("line" if kind == "poly" else "fill", c)
            boxes = _boxes(item)
            after = -1
            for i, batch in enumerate(batches):
                if any(_touches(a, b) for a in boxes for b in batch[2]):
                    after = i
            # The batch it touches will do too: inside one, order is kept.
            for batch in batches[max(after, 0):]:
                if key is not None and batch[0] == key:
                    batch[1].append(item)
                    batch[2].extend(boxes)
                    break
            else:
                batches.append([key, [item], list(boxes)])

        for key, members, _ in batches:
            if key is None:
                item = members[0]
                self.board.disp_draw_text(self.addr, item[1], item[2], item[3],
                                          item[4], item[5], raw=True)
                self.frames += 1
            elif key[0] == "fill":
                # Erasing text is filling its cells: there is no ink to undo.
                rects = [item[1:5] if item[0] == "fill" else
                         (lambda b: (b[0], b[1], b[2] - b[0] + 1, b[3] - b[1] + 1))(_bbox(item))
                         for item in members]
                for i in range(0, len(rects), FILL_MAX):
                    self.board.disp_draw_fill(self.addr, key[1], rects[i:i + FILL_MAX], raw=True)
                    self.frames += 1
            else:
                # A long polyline is cheapest as POLY frames, 31 segments
                # each; short ones share LINE frames, 16 segments each.
                segments = []
                for item in members:
                    pts = item[1]
                    if len(pts) > LINE_MAX:
                        for frame in poly_frames(pts):
                            self.board.disp_draw_poly(self.addr, key[1], frame, raw=True)
                            self.frames += 1
                    else:
                        segments.extend(a + b for a, b in zip(pts, pts[1:]))
                for i in range(0, len(segments), LINE_MAX):
                    self.board.disp_draw_line(self.addr, key[1], segments[i:i + LINE_MAX], raw=True)
                    self.frames += 1

    def flush(self):
        """Make the panel match the layers. Returns the frames it took: 0 when
        nothing changed, which is what a steady scene costs."""
        before = self.frames
        if not self._fresh:
            self.clear()
        damage, erase = [], []
        for layer in self.layers:
            if layer.items == layer._sent:
                continue
            keep, had = set(layer.items), set(layer._sent)
            # A new solid shape that covers an old one replaces it outright:
            # a marker changing colour is one frame, not an erase and a draw.
            covers = [_bbox(item) for item in layer.items
                      if item not in had and item[0] != "poly"]
            gone = [item for item in layer._sent if item not in keep
                    and not any(_inside(_bbox(item), c) for c in covers)]
            if not gone:
                continue
            if self.slot is not None:
                # One rectangle of the stored picture covers everything this
                # layer lost, however many lines that was.
                boxes = [_bbox(item) for item in gone]
                box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                       max(b[2] for b in boxes), max(b[3] for b in boxes))
                self.board.disp_slot_rect(self.addr, self.slot, box[0], box[1],
                                          box[2] - box[0] + 1, box[3] - box[1] + 1)
                self.frames += 1
                damage.append(box)
            else:
                erase.extend(gone)
                for item in gone:
                    damage.extend(_boxes(item))
        self._send(erase, colour=self.background)
        # Whatever is new goes down, and so does anything an erase may have
        # cut through -- in layer order, and taking with it whatever lies over
        # it, so what was on top still is.
        todo = []
        for layer in self.layers:
            had = set(layer._sent)
            for item in layer.items:
                if item not in had or _hits(item, damage):
                    todo.append(item)
                    damage.extend(_boxes(item))
        self._send(todo)
        for layer in self.layers:
            layer._sent = list(layer.items)
        return self.frames - before

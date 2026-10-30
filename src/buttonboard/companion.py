"""Driver Station companion: put live robot data on a display tile (§14.4).

PROTOCOL.md §9.8 names this: *"The FRC Driver Station itself never sends data
to a joystick. A companion app reading NetworkTables (robot mode, match time,
alliance, mechanism state) is the intended source for LED colours and display
text."* This module is that app's engine. It runs on the Driver Station
laptop beside the DS itself, which is why it talks CDC — the `frc` personality
carries the config channel alongside the gamepad (§8.3), and Phase 0.1 showed
the DS is undisturbed by it.

Three things make this more than a format string in a loop, and all three are
rules from elsewhere in the protocol rather than preferences:

**Stale data is worse than no data.** §14.6 blanks a tile that has heard
nothing for `T_SILENCE`, and says why: "whatever is on the screen came from a
host that has been gone for two seconds, and stale match data is worse than an
empty screen." That rule cannot fire here. The module keeps polling whatever
happens to this program, so the tile never sees silence, and a panel reading
`ENABLED 0:42` while this process is dead or NetworkTables is gone is exactly
the failure §14.6 exists to prevent — one layer up, where the tile cannot see
it. So the same rule is enforced here: once the source has been quiet for
`stale_after`, the values stop being drawn. What replaces them is a short
notice rather than a blank panel, because unlike the tile we know something
true and useful to say — that the data is old — and §14.6 blanks only because
at that layer there is nothing safe to put up.

**The bus is not a frame buffer.** §14.5: a four-row redraw is about 20 ms of
auxiliary slot at eight tiles, and NetworkTables updates at 50 Hz. Redrawing
at the rate the data changes would spend the bus on a display. So updates are
capped (`hz`) and only rows that actually changed are written — `Display`
already diffs, and the cap is what stops a value that flickers between two
strings from writing every tick.

**A diff has to be given up when the tile goes away.** The row diff is this
program's belief about what is on the glass. A tile that drops off the bus and
comes back has a blank panel (§14.6 blanks at power-up too), so every row this
program believes it drew is gone. Any `ev:tile` for our address drops the
diff, or the next update would write only what changed and leave the rest of
the panel empty.
"""

import time

from .errors import BoardError, CommandError, Timeout

__all__ = [
    "Row", "Layout", "Source", "MappingSource", "NetworkTablesSource",
    "Companion", "DEFAULT_HZ", "DEFAULT_STALE_AFTER",
]

# §14.5's arithmetic decides this, not taste: a four-row redraw is ~20 ms of
# auxiliary slot at eight tiles, so five a second is ~10 % of the slot in the
# worst case and invisible in the common one, where nothing changed.
DEFAULT_HZ = 5.0
# Two seconds, the same figure §14.6 uses for T_SILENCE, and for the same
# reason. A source that has said nothing for this long is not trusted.
DEFAULT_STALE_AFTER = 2.0
# What goes up in place of the values. Short enough for the narrowest panel in
# the range (`screenkey1` is 10 cells).
STALE_TEXT = "-- NO DATA"


class Row(object):
    """One row of a layout: a template, and the style to draw it in.

    `template` is `str.format`'s, over whatever names the source provides:
    `"{mode} {match:>5}"`. A name the source does not have renders as `?`
    rather than raising — a display that loses one field should lose that
    field, not the whole panel.
    """

    def __init__(self, row, template, attr=None, fg=None, bg=None,
                 inverse=False, double=False):
        self.row = int(row)
        self.template = template
        self.attr = attr
        self.fg = fg
        self.bg = bg
        self.inverse = bool(inverse)
        self.double = bool(double)

    def render(self, values):
        try:
            return self.template.format(**values)
        except (KeyError, IndexError, ValueError):
            # A missing or unformattable value is per-row damage. Substituting
            # keeps the rest of the panel alive, and §14.4 already says a
            # sending client should substitute rather than drop.
            return _missing(self.template, values)

    @classmethod
    def from_dict(cls, spec):
        spec = dict(spec)
        return cls(spec.pop("row"), spec.pop("text", spec.pop("template", "")), **spec)


def _missing(template, values):
    """Render `template` with every unresolvable field replaced by `?`."""
    import string
    out = []
    for literal, field, spec, conv in string.Formatter().parse(template):
        out.append(literal)
        if field is None:
            continue
        try:
            value = values[field.split(".")[0].split("[")[0]]
            out.append(format(value, spec or ""))
        except Exception:
            out.append("?" * max(1, len(spec or "") or 1))
    return "".join(out)


class Layout(object):
    """The rows of one panel, and how wide they may be."""

    def __init__(self, rows):
        self.rows = [r if isinstance(r, Row) else Row.from_dict(r) for r in rows]

    @classmethod
    def from_json(cls, data):
        """A layout as `{"rows": [{"row": 0, "text": "...", "fg": "red"}, ...]}`."""
        if isinstance(data, dict):
            data = data.get("rows", [])
        return cls(data)

    def names(self):
        """Every value name the templates mention, for a source to subscribe to."""
        import string
        found = []
        for row in self.rows:
            for _, field, _, _ in string.Formatter().parse(row.template):
                if field:
                    name = field.split(".")[0].split("[")[0]
                    if name and name not in found:
                        found.append(name)
        return found


class Source(object):
    """Where values come from. Subclasses provide `poll()`."""

    def names(self, wanted):
        """Told which names the layout wants. Optional."""

    def poll(self):
        """Return a dict of values, or None if nothing fresh is available."""
        raise NotImplementedError

    def close(self):
        pass


class MappingSource(Source):
    """A dict, or a callable returning one. For tests, demos and scripts."""

    def __init__(self, values):
        self._values = values

    def poll(self):
        v = self._values() if callable(self._values) else self._values
        return None if v is None else dict(v)


class NetworkTablesSource(Source):
    """NetworkTables 4, through `ntcore` (`pip install pyntcore`).

    Imported lazily and only here, so the rest of `buttonboard` keeps its
    dependency budget — pyserial and nothing else. A Driver Station laptop
    that runs this app has the robotpy stack or can install it; a bench that
    only wants `bbctl` should not have to.

    Names map to topics: a layout field `{mode}` reads the topic `mode` under
    `table` (default `/SmartDashboard`), unless the name contains a `/`, in
    which case it is an absolute topic path.
    """

    def __init__(self, table="/SmartDashboard", server=None, team=None,
                 identity="bb-companion"):
        self.table = table.rstrip("/")
        self.server = server
        self.team = team
        self.identity = identity
        self._inst = None
        self._subs = {}
        self._wanted = []

    def _topic(self, name):
        return name if name.startswith("/") else "%s/%s" % (self.table, name)

    def names(self, wanted):
        self._wanted = list(wanted)

    def start(self):
        try:
            import ntcore
        except ImportError:  # pragma: no cover - depends on the host
            raise BoardError(
                "NetworkTables support needs ntcore: pip install pyntcore")
        self._inst = ntcore.NetworkTableInstance.getDefault()
        self._inst.startClient4(self.identity)
        if self.server:
            self._inst.setServer(self.server)
        elif self.team:
            self._inst.setServerTeam(int(self.team))
        else:
            self._inst.setServerTeam(0)
        for name in self._wanted:
            self._subs[name] = self._inst.getTopic(self._topic(name)).genericSubscribe()
        return self

    def connected(self):
        return bool(self._inst and self._inst.isConnected())

    def poll(self):
        if self._inst is None:
            self.start()
        if not self.connected():
            # Not an error and not stale-by-itself: the caller's stale clock
            # decides, so a blip shorter than `stale_after` does not flicker
            # the panel.
            return None
        out = {}
        for name, sub in self._subs.items():
            value = sub.get()
            out[name] = value.value() if hasattr(value, "value") else value
        return out

    def close(self):
        if self._inst is not None:
            self._inst.stopClient()
            self._inst = None


class Companion(object):
    """Drive one display tile from one source.

    `board` is an open `buttonboard.Board`, `addr` the tile. Call `run()` for the loop,
    or `tick()` from your own.
    """

    def __init__(self, board, addr, layout, source, hz=DEFAULT_HZ,
                 stale_after=DEFAULT_STALE_AFTER, stale_text=STALE_TEXT,
                 on_error=None):
        self.board = board
        self.screen = board.screen(addr)
        self.layout = layout if isinstance(layout, Layout) else Layout.from_json(layout)
        self.source = source
        self.period = 1.0 / float(hz) if hz else 0.0
        self.stale_after = float(stale_after)
        self.stale_text = stale_text
        self.on_error = on_error
        self.addr = addr
        self._last_draw = 0.0
        self._last_good = None
        self._values = {}
        self._stale_drawn = False
        self.writes = 0

        source.names(self.layout.names())
        # The row diff is a belief about the glass, and a tile that left the
        # bus came back blank (§14.6). Drop it whenever this tile's state
        # changes rather than trying to work out which way.
        self._unhook = board.on("tile", self._on_tile)

    def _on_tile(self, ev):
        if ev.get("addr") == self.addr:
            self.screen.forget()

    def close(self):
        try:
            self._unhook()
        except Exception:
            pass
        self.source.close()

    # -- the loop ---------------------------------------------------------
    def tick(self, now=None):
        """One pass. Returns the number of rows written (0 most of the time)."""
        now = time.monotonic() if now is None else now
        fresh = self.source.poll()
        if fresh is not None:
            self._values = fresh
            self._last_good = now
        if self.period and now - self._last_draw < self.period:
            return 0
        self._last_draw = now

        if self._is_stale(now):
            return self._draw_stale()
        return self._draw(self._values)

    def _is_stale(self, now):
        if self._last_good is None:
            return True
        return (now - self._last_good) > self.stale_after

    def _draw(self, values):
        self._stale_drawn = False
        for row in self.layout.rows:
            self.screen.text(row.row, row.render(values), attr=row.attr,
                             fg=row.fg, bg=row.bg, inverse=row.inverse,
                             double=row.double)
        return self._flush()

    def _draw_stale(self):
        """Replace the values, not merely annotate them (§14.6's rule).

        Every row the layout owns is overwritten, so no field of a dead source
        survives anywhere on the panel. The notice goes on the layout's first
        row because that is the one a reader looks at.
        """
        if self._stale_drawn:
            return 0
        rows = sorted(r.row for r in self.layout.rows)
        for i, r in enumerate(rows):
            self.screen.text(r, self.stale_text if i == 0 else "")
        written = self._flush()
        self._stale_drawn = True
        return written

    def _flush(self):
        try:
            written = self.screen.flush()
        except (CommandError, Timeout, BoardError) as exc:
            # A write that failed did not land, so the diff must not claim it
            # did -- the next tick redraws everything rather than skipping the
            # rows it thinks are already there.
            self.screen.forget()
            if self.on_error:
                self.on_error(exc)
                return 0
            raise
        self.writes += written
        return written

    def run(self, stop=None, sleep=0.02):
        """Loop until `stop()` is true, or forever. KeyboardInterrupt returns."""
        try:
            while not (stop and stop()):
                self.tick()
                time.sleep(sleep)
        except KeyboardInterrupt:
            pass
        return self.writes

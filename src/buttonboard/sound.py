"""Piezo sound (PROTOCOL.md §16).

Reserved and draft: no tile carries a sounder yet, so the board NAKs TONE and
`sound` answers `unknown`. Written now because §16 settled the wire format,
and because this is where the named tunes live — §16.5 keeps them out of the
firmware deliberately, so that changing a chirp is a client change and not a
firmware release for every tile.
"""
from __future__ import annotations

FREQ_MIN = 100                     # §16.3; outside this the element says nothing
FREQ_MAX = 10000
REST = 0
MAX_NOTES = 23                     # (FRAME_MAX_LEN - 2) / 3, one frame
DUR_UNIT_MS = 10                   # the wire's unit; the line protocol takes ms

# Piezo elements are sharply resonant, usually 2-4 kHz, and a note an octave
# off resonance can be inaudible across a pit (§16.8). These sit in that band
# on purpose: they are chirps, not music. Measure against the real element
# before treating any of them as final.
TUNES = {
    "tick":    ([(3000, 20)], 1),
    "beep":    ([(2700, 60)], 1),
    "ok":      ([(2100, 60), (2700, 90)], 1),
    "confirm": ([(2100, 60), (2700, 90)], 1),
    "error":   ([(2700, 120), (REST, 60), (1400, 240)], 1),
    "warn":    ([(2400, 80), (REST, 60), (2400, 80)], 1),
    "bind":    ([(2400, 40), (REST, 30), (3200, 60)], 1),
    "lost":    ([(1400, 200), (REST, 80), (1400, 200)], 1),
    "alarm":   ([(2700, 150), (REST, 120)], 0),        # 0 = until stopped
}


def note(freq, ms):
    """Validate one note and return it as the `freq:ms` the line protocol takes."""
    freq = int(freq)
    ms = int(ms)
    if freq != REST and not FREQ_MIN <= freq <= FREQ_MAX:
        raise ValueError("frequency must be %d-%d Hz, or 0 for a rest, not %r"
                         % (FREQ_MIN, FREQ_MAX, freq))
    if ms < 1:
        raise ValueError("duration must be at least 1 ms, not %r" % (ms,))
    # The board rounds up to the wire's 10 ms unit (§16.5). Say so here rather
    # than let a caller wonder why 5 ms and 10 ms sound identical.
    if ms > 255 * DUR_UNIT_MS:
        raise ValueError("duration must be at most %d ms, not %r"
                         % (255 * DUR_UNIT_MS, ms))
    return "%d:%d" % (freq, ms)


def sequence(notes):
    """Coerce a sequence to the comma-separated form `sound ... play` takes.

    Accepts a tune name, a string already in wire form, or an iterable of
    (freq, ms) pairs. Returns (text, repeat) — a named tune brings its own
    repeat count, e.g. "alarm" repeats until stopped.
    """
    repeat = None
    if isinstance(notes, str):
        if notes in TUNES:
            notes, repeat = TUNES[notes]
        else:
            # Already `freq:ms,freq:ms`. Re-validate rather than trust it.
            notes = [tuple(int(x) for x in part.split(":", 1))
                     for part in notes.split(",") if part.strip()]
    notes = list(notes)
    if not notes:
        raise ValueError("a sequence needs at least one note")
    if len(notes) > MAX_NOTES:
        raise ValueError("at most %d notes fit in one frame (§16.3), not %d"
                         % (MAX_NOTES, len(notes)))
    return ",".join(note(f, ms) for f, ms in notes), repeat

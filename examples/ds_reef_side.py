#!/usr/bin/env python3
"""
Show the REEFSCAPE reef from the side on a disp2, with the level the robot is going to score on lit up.

Built on the bbr-buttonboard library (tools/buttonboard) and its Canvas (PROTOCOL.md 14.10). The reef is drawn as it
looks from the driver station end: the pipe, the trough at the bottom (L1), the two angled branches (L2, L3) and the
upright one at the top (L4). The chosen level is lit in yellow on a band behind it, an arrow points at it, and its
name is written beside it. Changing level is a few frames on the bus; a steady choice costs nothing.

Where the choice comes from:

* **The robot (the default).** The operator's L2/L3/L4 buttons go to the robot like any other button, and the
  robot publishes the level it is set up for, 1-4, or 0 for none. Then the panel shows what the robot will
  actually do, not just what was pressed:

      SmartDashboard.putNumber("ReefLevel", targetLevel);   // in robotPeriodic()

* **An mx4 on the panel (--mx4).** For trying it with no robot code: the script watches the keys of an mx4 (the
  four-key cluster) and the last one pressed picks the level. The three keys in a row are L2, L3 and L4, left to
  right, and the key on its own above them is the trough, L1. The chosen key lights yellow if the mx4 has LEDs.

* **Any tile's keys (--keys).** The same with your own choice of keys: --keys 3:0,1,2,3 means tile 3's keys 0-3
  are L1-L4; -1 skips a level.

Install and run (the board in frc mode, the config website not connected to it):

    pip install "bbr-buttonboard[networktables]"
    python ds_reef_side.py --team 1234
    python ds_reef_side.py --mx4                   # the first mx4 online picks the level
    python ds_reef_side.py --keys 3:-1,0,1,2       # an mx3 at address 3 as L2, L3, L4

    python ds_reef_side.py --demo                  # the level changes by itself, no robot needed
    python ds_reef_side.py --mock --png side.png   # no board either: draw it into a picture
"""
import argparse
import sys
import time

from buttonboard import Board, BoardError, MockBoard
from buttonboard.companion import DEFAULT_STALE_AFTER, MappingSource, NetworkTablesSource

HZ = 10
GROUND = 124                     # the carpet, in pixels from the top
PX_PER_CM = 0.6                  # 183 cm of reef is 110 pixels
PIPE_X = 24                      # the pipe's right-hand edge; branches point right, towards the drivers
OUT = 24                         # how far L2 and L3 reach out from the pipe, pixels
UP = 17                          # ... and how far up: 35 degrees, as on the field
REEF, LIT, BAND = "#B060FF", "yellow", "#403400"
TEXT_X = 88                      # the label column: six characters to the panel's edge



def y(cm):
    """Height above the carpet in centimetres to a pixel row."""
    return round(GROUND - cm * PX_PER_CM)


LEVELS = {
    # name, the band behind it (y0, y1), and the point its arrow touches
    1: ("TROUGH", (y(52), y(22)), (PIPE_X + 31, (y(46) + y(28)) // 2)),
    2: ("BOTTOM", (y(90), y(52)), (PIPE_X + OUT + 4, y(81))),
    3: ("MIDDLE", (y(130), y(90)), (PIPE_X + OUT + 4, y(121))),
    4: ("TOP", (4, y(130)), (PIPE_X + 22, y(183) + 2)),
}


def thick(layer, points, colour):
    """A branch three pixels wide: the line, and the same line one and two pixels to the right."""
    for dx in (0, 1, 2):
        layer.poly([(px + dx, py) for px, py in points], colour)


def draw_level(layer, level, lit):
    """One level of the reef. The heights are where the coral sits on the real reef: the trough's lip at 46 cm,
    the tips of L2 and L3 at 81 and 121 cm, both branches at 35 degrees, and L4 standing up to 183 cm."""
    colour = LIT if lit else REEF
    layer.clear()
    if level == 1:
        # The trough: an open-topped box on the pipe, its far lip the edge coral rests against.
        for d in (0, 1):
            layer.poly([(PIPE_X + 1, y(28) - d), (PIPE_X + 27 - d, y(28) - d),
                        (PIPE_X + 27 - d, y(46))], colour)
    elif level == 2:
        thick(layer, [(PIPE_X + 1, y(81) + UP), (PIPE_X + OUT, y(81))], colour)
    elif level == 3:
        thick(layer, [(PIPE_X + 1, y(121) + UP), (PIPE_X + OUT, y(121))], colour)
    else:
        thick(layer, [(PIPE_X + 1, y(150)), (PIPE_X + 17, y(150) - 11), (PIPE_X + 17, y(183))], colour)


def draw_pipe(layer):
    """The pipe the branches hang off, and the base it stands on."""
    for px in (PIPE_X - 5, PIPE_X):
        layer.line(px, GROUND - 7, px, y(150) - 4, REEF)
    layer.fill(PIPE_X - 4, y(150) - 4, 4, GROUND - 3 - y(150), "#402060")
    layer.rect(2, GROUND - 6, 60, 7, REEF)


def draw_choice(band, arrow, label, level, stale):
    band.clear()
    arrow.clear()
    label.clear()
    if stale:
        label.text(TEXT_X, 40, "NO", "red").text(TEXT_X, 60, "ROBOT", "red")
        return
    if level not in LEVELS:
        label.text(TEXT_X, 40, "PICK A", "white").text(TEXT_X, 60, "LEVEL", "white")
        return
    name, (y0, y1), (tx, ty) = LEVELS[level]
    band.fill(2, y0, 70, y1 - y0, BAND)
    arrow.arrow(TEXT_X - 6, ty, tx + 6, ty, LIT, head=5)
    label.text(TEXT_X + 18, max(4, ty - 26), f"L{level}", LIT)
    label.text(TEXT_X, max(4, ty - 26) + 18, name, LIT)


def parse_keys(text):
    """`3:0,1,2,3` -> (3, {0: 1, 1: 2, 2: 3, 3: 4}): the tile, and which of its keys picks which level."""
    try:
        addr, keys = text.split(":")
        keys = [int(k) for k in keys.split(",")]
    except ValueError:
        sys.exit(f"--keys wants ADDR:K1,K2,K3,K4 (key numbers from 0, -1 to skip a level), not {text!r}")
    if len(keys) != 4:
        sys.exit("--keys wants four key numbers, one for each of L1 to L4 (-1 to skip a level)")
    return int(addr), {k: level for level, k in enumerate(keys, 1) if k >= 0}


def find_keys(board, addr, mx4):
    """The tile whose keys pick the level: `addr`, or with --mx4 and no address, the first mx4 online."""
    for t in board.tiles():
        if t["state"] == "online" and (t["addr"] == addr if addr else mx4 and t["type"] == "mx4"):
            return t["addr"]
    sys.exit(f"No {'tile at address ' + str(addr) if addr else 'mx4'} online. Check its cable, or give its address.")


def find_panel(board, addr=None):
    """The disp2 to draw on: the one at `addr`, or the first online that draws lines."""
    for t in board.tiles():
        if t["state"] == "online" and (t["addr"] == addr if addr else t["type"] == "disp2" and t.get("draw")):
            if not t.get("draw"):
                sys.exit(f"The tile at address {addr} does not draw lines. This needs a disp2 on current firmware.")
            return t["addr"]
    sys.exit(f"No {'tile at address ' + str(addr) if addr else 'disp2 that draws lines'} online. "
             "Check its cable and its firmware, or pass --addr.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--team", type=int, help="team number, to find the robot")
    ap.add_argument("--server", help="NetworkTables server address instead of the team's robot")
    ap.add_argument("--key", default="ReefLevel",
                    help="the level entry, 1-4 or 0 for none: a name under /SmartDashboard, or a full path")
    ap.add_argument("--mx4", nargs="?", const=0, type=int, metavar="ADDR",
                    help="take the level from an mx4's keys: the row is L2-L4, the key on its own L1 "
                         "(default: the first mx4 online)")
    ap.add_argument("--keys", help="take the level from a tile's own keys instead: ADDR:K1,K2,K3,K4")
    ap.add_argument("--port", help="the board's serial port (default: found by USB id)")
    ap.add_argument("--addr", type=int, help="the display's address (default: the first disp2 that draws)")
    ap.add_argument("--demo", action="store_true", help="the level changes by itself, no robot needed")
    ap.add_argument("--mock", action="store_true", help="no board: draw into memory (implies --demo)")
    ap.add_argument("--png", help="with --mock, write the finished panel to this file")
    a = ap.parse_args()

    clock = time.monotonic
    if a.mock:
        ticks = iter(range(10 ** 9))
        clock = lambda: next(ticks) / HZ            # noqa: E731 -- made-up time, so --mock does not wait
    source, keys = None, None
    if a.keys:
        keys = parse_keys(a.keys)
    elif a.mx4 is not None:
        # The mx4's keys are in reading order (PROTOCOL.md §5): key 0 is the one on its own, then the row
        # left to right. The address is looked up once the board is open.
        keys = (a.mx4, {0: 1, 1: 2, 2: 3, 3: 4})
    elif a.demo or a.mock:
        # Every level in turn and a moment with none, two seconds each.
        source = MappingSource(lambda: {a.key: (4, 3, 2, 1, 0)[int(clock() / 2) % 5]})
    elif a.team or a.server:
        source = NetworkTablesSource(team=a.team, server=a.server, identity="bbr-ds-reef-side")
    else:
        sys.exit("Give --team (or --server) to follow the robot, or --mx4 or --keys to use the panel's own keys. "
                 "Or try --demo.")
    if source is not None:
        source.names([a.key])

    with (MockBoard() if a.mock else Board.open(port=a.port)) as board:
        addr = find_panel(board, a.addr)
        leds = None
        if keys is not None:
            keys = (find_keys(board, keys[0], a.mx4 is not None), keys[1])
            if (board.tile(keys[0]) or {}).get("leds", 0) >= 4:
                leds = board.led_layer()
        canvas = board.canvas(addr)
        # The band goes first, so the reef is drawn over it and lighting a level never hides the branch.
        band = canvas.layer()
        draw_pipe(canvas.layer())
        levels = {n: canvas.layer() for n in LEVELS}
        arrow, label = canvas.layer(), canvas.layer()
        # A tile that drops off the bus comes back blank: draw it all again, not just what changed.
        board.on("tile", lambda ev: ev.get("addr") == addr and canvas.forget())
        print(f"drawing on the display at address {addr}" + ("" if a.mock else "; Ctrl-C to stop"), flush=True)

        level, last_good, held, passes = 0, None, set(), 0
        try:
            while not a.mock or passes < 125:      # --mock: two rounds, ending on L3
                now = time.monotonic()
                try:
                    if keys is not None:
                        # The last key pressed wins and stays chosen after it is let go.
                        entry = board.tile(keys[0]) or {}
                        down = set(entry.get("pressed", []))
                        for k in sorted(down - held):
                            if k in keys[1]:
                                level = keys[1][k]
                        held, stale = down, entry.get("state") != "online"
                    else:
                        v = (source.poll() or {}).get(a.key)
                        if isinstance(v, (int, float)) and v == v:
                            last_good, level = now, int(v)
                        stale = last_good is None or now - last_good > DEFAULT_STALE_AFTER
                    if leds is not None:
                        # The chosen key lit, the others dark: the panel says which button is in charge.
                        for k, n in keys[1].items():
                            leds.set_tile(keys[0], k, LIT if n == level and not stale else "off")
                        leds.flush()
                    for n, layer in levels.items():
                        draw_level(layer, n, not stale and n == level)
                    draw_choice(band, arrow, label, level, stale)
                    canvas.flush()
                except BoardError as e:     # a frame that failed did not land: start from a clear panel next time
                    canvas.forget()
                    if leds is not None:
                        leds.forget()
                    print(f"display {addr}: {e}", file=sys.stderr, flush=True)
                passes += 1
                if not a.mock:
                    time.sleep(1.0 / HZ)
        finally:
            if leds is not None:
                board.led_reset()       # hand the key colours back to the saved configuration
        print(f"{passes} passes took {canvas.frames} frames on the bus", flush=True)
        if a.png:
            print("wrote", board.state.panel(addr).save(a.png), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

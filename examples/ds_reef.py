#!/usr/bin/env python3
"""
Show the REEFSCAPE reef on a disp2: the reef from above, every branch at every level, filled in as coral is scored.

Built on the bbr-buttonboard library (tools/buttonboard) and its Canvas (PROTOCOL.md 14.10). The reef is drawn once.
After that a coral scored is one frame on the bus -- a small filled square -- and a change of target is two.

Face A/B is at the bottom, nearest the drivers, and the letters run counter-clockwise from there as they do on the
field: C/D lower right, E/F upper right, G/H at the top, I/J and K/L down the left. On each branch the three squares
are L2, L3 and L4 going outwards. The number in the middle is how many are scored; the label in the corner is the
branch the robot is going for.

The robot has to publish what it has scored: 36 booleans, branch A's L2, L3, L4, then B's, through to L. And,
if it has one, the index of its target in that same order (C's L4 is 2 * 3 + 2 = 8), or -1 for none:

    SmartDashboard.putBooleanArray("Reef", scored);          // boolean[36]
    SmartDashboard.putNumber("ReefTarget", targetIndex);     // optional

Install and run (the board in frc mode, the config website not connected to it):

    pip install "bbr-buttonboard[networktables]"
    python ds_reef.py --team 1234

    python ds_reef.py --demo                       # a made-up match, no robot needed
    python ds_reef.py --mock --png reef.png        # no board either: draw it into a picture
"""
import argparse
import math
import sys
import time

from buttonboard import Board, BoardError, MockBoard
from buttonboard.companion import DEFAULT_STALE_AFTER, MappingSource, NetworkTablesSource

HZ = 10
CX, CY = 64, 64                  # the reef's centre: the left 128 pixels of the panel are its square
FLAT = 19                        # centre to the middle of a face, pixels
LEVELS = (28, 39, 50)            # centre to the L2, L3 and L4 squares
SPREAD = 7                       # a branch sits this far either side of the middle of its face
MARK = 7                         # a square's side
BRANCHES = "ABCDEFGHIJKL"
EMPTY, SCORED, TARGET, REEF = "#505050", "white", "yellow", "magenta"


def find_panel(board, addr=None):
    """The disp2 to draw on: the one at `addr`, or the first online that draws lines."""
    for t in board.tiles():
        if t["state"] == "online" and (t["addr"] == addr if addr else t["type"] == "disp2" and t.get("draw")):
            if not t.get("draw"):
                sys.exit(f"The tile at address {addr} does not draw lines. This needs a disp2 on current firmware.")
            return t["addr"]
    sys.exit(f"No {'tile at address ' + str(addr) if addr else 'disp2 that draws lines'} online. "
             "Check its cable and its firmware, or pass --addr.")


def mark_centre(index):
    """Where square `index` goes: branch index // 3, level index % 3.

    A face looks out from the centre at 90 degrees for A/B (down the screen), and each face after it is 60
    degrees further round, counter-clockwise as seen from above. The first branch of a face is on its left as
    you stand outside the reef looking at it, which is A's side of A/B from the driver station."""
    branch, level = divmod(index, 3)
    face = math.radians(90 - 60 * (branch // 2))
    nx, ny = math.cos(face), math.sin(face)        # out of the face
    tx, ty = math.sin(face), -math.cos(face)       # along it, the way the letters run
    side = SPREAD if branch % 2 else -SPREAD
    return (CX + nx * LEVELS[level] + tx * side, CY + ny * LEVELS[level] + ty * side)


def draw_reef(layer):
    """The reef itself: a hexagon, a face square on to each pair of branches."""
    corner = FLAT / math.cos(math.radians(30))
    layer.poly([(CX + corner * math.cos(math.radians(60 * k)), CY + corner * math.sin(math.radians(60 * k)))
                for k in range(6)], REEF, closed=True)


def draw_marks(layer, scored):
    """All 36 squares: an outline where there is no coral yet, solid where there is. A square that fills in
    is drawn over its own outline, so scoring is one small frame and nothing has to be erased."""
    layer.clear()
    for i in range(36):
        x, y = mark_centre(i)
        x, y = round(x - MARK / 2), round(y - MARK / 2)
        if scored[i]:
            layer.fill(x, y, MARK, MARK, SCORED)
        else:
            layer.fill(x, y, MARK, MARK, "black")
            layer.rect(x, y, MARK, MARK, EMPTY)


def draw_target(layer, target):
    """A ring round the square the robot is going for."""
    layer.clear()
    if target is not None:
        x, y = mark_centre(target)
        layer.rect(round(x - MARK / 2) - 2, round(y - MARK / 2) - 2, MARK + 4, MARK + 4, TARGET)


def draw_labels(layer, scored, target, width):
    layer.clear()
    if scored is None:
        layer.text(16, 56, "NO ROBOT", "red")
        return
    layer.text(CX - 12, CY - 8, f"{sum(scored):2d}", "white")
    if target is not None:
        layer.text(width - 26, 2, f"{BRANCHES[target // 3]}{target % 3 + 2}", TARGET)


def demo_values(t):
    """A made-up match: a coral every second or so in a fixed shuffle, the target always the next one."""
    order = [(i * 23 + 5) % 36 for i in range(36)]
    done = int(t / 1.2) % 40                       # a few seconds full, then start again
    scored = [False] * 36
    for i in order[:min(done, 36)]:
        scored[i] = True
    return {"Reef": scored, "ReefTarget": order[done] if done < 36 else -1}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--team", type=int, help="team number, to find the robot")
    ap.add_argument("--server", help="NetworkTables server address instead of the team's robot")
    ap.add_argument("--key", default="Reef",
                    help="the scored array, boolean[36]: a name under /SmartDashboard, or a full path starting with /")
    ap.add_argument("--target-key", default="ReefTarget", help="the target index entry, -1 for none (optional)")
    ap.add_argument("--port", help="the board's serial port (default: found by USB id)")
    ap.add_argument("--addr", type=int, help="the display's address (default: the first disp2 that draws)")
    ap.add_argument("--demo", action="store_true", help="a made-up match, no robot needed")
    ap.add_argument("--mock", action="store_true", help="no board: draw into memory (implies --demo)")
    ap.add_argument("--png", help="with --mock, write the finished panel to this file")
    a = ap.parse_args()

    clock = time.monotonic
    if a.mock:
        ticks = iter(range(10 ** 9))
        clock = lambda: next(ticks) / 2             # noqa: E731 -- made-up time, so --mock does not wait
    if a.demo or a.mock:
        def made_up():
            v = demo_values(clock())
            return {a.key: v["Reef"], a.target_key: v["ReefTarget"]}
        source = MappingSource(made_up)
    elif a.team or a.server:
        source = NetworkTablesSource(team=a.team, server=a.server, identity="bbr-ds-reef")
    else:
        sys.exit("Give --team (or --server), so the script can find the robot. Or try --demo.")
    source.names([a.key, a.target_key])

    with (MockBoard() if a.mock else Board.open(port=a.port)) as board:
        addr = find_panel(board, a.addr)
        canvas = board.canvas(addr)
        draw_reef(canvas.layer())
        # The ring goes under the squares, so taking it away never has to redraw one.
        ring, marks, labels = canvas.layer(), canvas.layer(), canvas.layer()
        # A tile that drops off the bus comes back blank: draw it all again, not just what changed.
        board.on("tile", lambda ev: ev.get("addr") == addr and canvas.forget())
        print(f"drawing on the display at address {addr}" + ("" if a.mock else "; Ctrl-C to stop"), flush=True)

        last_good, scored, target, passes = None, None, None, 0
        while not a.mock or passes < 45:
            now = time.monotonic()
            values = source.poll() or {}
            v = values.get(a.key)
            if isinstance(v, (list, tuple)) and len(v) >= 36:
                last_good, scored = now, [bool(x) for x in v[:36]]
                t = values.get(a.target_key)
                target = int(t) if isinstance(t, (int, float)) and 0 <= t < 36 else None
            fresh = last_good is not None and now - last_good <= DEFAULT_STALE_AFTER
            try:
                if fresh:
                    draw_marks(marks, scored)
                    draw_target(ring, target)
                else:
                    marks.clear()
                    ring.clear()
                draw_labels(labels, scored if fresh else None, target, canvas.width)
                canvas.flush()
            except BoardError as e:     # a frame that failed did not land: start from a clear panel next time
                canvas.forget()
                print(f"display {addr}: {e}", file=sys.stderr, flush=True)
            passes += 1
            if not a.mock:
                time.sleep(1.0 / HZ)
        print(f"{passes} passes took {canvas.frames} frames on the bus", flush=True)
        if a.png:
            print("wrote", board.state.panel(addr).save(a.png), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

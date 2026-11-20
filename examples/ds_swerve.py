#!/usr/bin/env python3
"""
Draw the swerve drive on a disp2: the chassis from above, each module turned to its angle, an arrow for its speed.

Built on the bbr-buttonboard library (tools/buttonboard) and its Canvas, which sends the tile end points and lets it
draw the lines (PROTOCOL.md 14.10). Only what changed crosses the bus, so a robot standing still costs nothing and a
module that turns costs a few frames. The front of the robot is the top of the screen.

The robot has to publish its module states: angle in degrees (0 forward, counter-clockwise positive) and speed in
m/s, front left, front right, back left, back right. In robotPeriodic():

    SwerveModuleState[] s = drive.getModuleStates();
    SmartDashboard.putNumberArray("SwerveStates", new double[] {
        s[0].angle.getDegrees(), s[0].speedMetersPerSecond, s[1].angle.getDegrees(), s[1].speedMetersPerSecond,
        s[2].angle.getDegrees(), s[2].speedMetersPerSecond, s[3].angle.getDegrees(), s[3].speedMetersPerSecond});

Install and run (the board in frc mode, the config website not connected to it):

    pip install "bbr-buttonboard[networktables]"
    python ds_swerve.py --team 1234

    python ds_swerve.py --demo                       # a made-up robot driving in circles, no robot needed
    python ds_swerve.py --mock --png swerve.png      # no board either: draw it into a picture
"""
import argparse
import math
import sys
import time

from buttonboard import Board, BoardError, MockBoard
from buttonboard.companion import DEFAULT_STALE_AFTER, MappingSource, NetworkTablesSource

HZ = 10
CX, CY, HALF = 80, 52, 34        # the chassis: centre and half its side, in pixels
WHEEL_W, WHEEL_H = 9, 19         # a module, pointing forward
ARROW_MAX = 24                   # pixels of arrow at --max speed
ANGLE_STEP = 3                   # degrees a module turns before it is worth redrawing

# Front left, front right, back left, back right, as WPILib orders them: (to the right, down the screen).
CORNERS = ((-1, -1), (1, -1), (-1, 1), (1, 1))


def find_panel(board, addr=None):
    """The disp2 to draw on: the one at `addr`, or the first online that draws lines."""
    for t in board.tiles():
        if t["state"] == "online" and (t["addr"] == addr if addr else t["type"] == "disp2" and t.get("draw")):
            if not t.get("draw"):
                sys.exit(f"The tile at address {addr} does not draw lines. This needs a disp2 on current firmware.")
            return t["addr"]
    sys.exit(f"No {'tile at address ' + str(addr) if addr else 'disp2 that draws lines'} online. "
             "Check its cable and its firmware, or pass --addr.")


def demo_states(t):
    """A robot driving a slow circle while it spins: chassis speeds turned into module states the way
    SwerveDriveKinematics does it, with x forward and y to the left."""
    vx, vy, omega = 2.2 * math.cos(t / 2), 2.2 * math.sin(t / 2), 2.0 * math.sin(t / 3.3)
    out = []
    for sx, sy in CORNERS:
        rx, ry = -sy * 0.3, -sx * 0.3              # the module's place on the robot, metres
        mx, my = vx - omega * ry, vy + omega * rx
        out += [math.degrees(math.atan2(my, mx)), math.hypot(mx, my)]
    return out


def draw_frame(layer):
    layer.rect(CX - HALF, CY - HALF, 2 * HALF + 1, 2 * HALF + 1, "white")
    # A tick on the front edge, so "forward" does not depend on remembering it is up.
    layer.poly([(CX - 5, CY - HALF), (CX, CY - HALF - 6), (CX + 5, CY - HALF)], "white")


def draw_module(layer, corner, angle, speed, top):
    """One wheel and its arrow. `angle` is counter-clockwise from forward, and forward is up the screen."""
    layer.clear()
    x, y = CX + corner[0] * HALF, CY + corner[1] * HALF
    angle = round(angle / ANGLE_STEP) * ANGLE_STEP
    if speed < 0:                                   # a module driving backwards points the other way
        angle, speed = angle + 180, -speed
    layer.box(x, y, WHEEL_W, WHEEL_H, -angle, "lime")
    length = round(min(speed / top, 1.0) * ARROW_MAX)
    if length >= 3:
        a = math.radians(angle)
        layer.arrow(x, y, x - length * math.sin(a), y - length * math.cos(a), "yellow", head=4)


def draw(layers, states, top):
    frame, modules, label = layers
    label.clear()
    if states is None:
        for m in modules:
            m.clear()
        label.text(32, 108, "NO ROBOT", "red")
        return
    for i, m in enumerate(modules):
        draw_module(m, CORNERS[i], states[2 * i], states[2 * i + 1], top)
    fastest = max(abs(states[2 * i + 1]) for i in range(4))
    label.text(38, 108, f"{fastest:4.1f} m/s", "white")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--team", type=int, help="team number, to find the robot")
    ap.add_argument("--server", help="NetworkTables server address instead of the team's robot")
    ap.add_argument("--key", default="SwerveStates",
                    help="the entry to draw: a name under /SmartDashboard, or a full path starting with /")
    ap.add_argument("--max", type=float, default=4.5, help="the speed a full-length arrow means, m/s")
    ap.add_argument("--port", help="the board's serial port (default: found by USB id)")
    ap.add_argument("--addr", type=int, help="the display's address (default: the first tile that draws)")
    ap.add_argument("--demo", action="store_true", help="a made-up robot driving in circles, no robot needed")
    ap.add_argument("--mock", action="store_true", help="no board: draw into memory (implies --demo)")
    ap.add_argument("--png", help="with --mock, write the finished panel to this file")
    a = ap.parse_args()

    clock = time.monotonic
    if a.mock:
        ticks = iter(range(10 ** 9))
        clock = lambda: next(ticks) / HZ            # noqa: E731 -- made-up time, so --mock does not wait
    if a.demo or a.mock:
        source = MappingSource(lambda: {a.key: demo_states(clock())})
    elif a.team or a.server:
        source = NetworkTablesSource(team=a.team, server=a.server, identity="bbr-ds-swerve")
    else:
        sys.exit("Give --team (or --server), so the script can find the robot. Or try --demo.")
    source.names([a.key])

    with (MockBoard() if a.mock else Board.open(port=a.port)) as board:
        addr = find_panel(board, a.addr)
        canvas = board.canvas(addr)
        frame = canvas.layer()
        draw_frame(frame)
        layers = (frame, [canvas.layer() for _ in CORNERS], canvas.layer())
        # A tile that drops off the bus comes back blank: draw it all again, not just what changed.
        board.on("tile", lambda ev: ev.get("addr") == addr and canvas.forget())
        print(f"drawing on the display at address {addr}" + ("" if a.mock else "; Ctrl-C to stop"), flush=True)

        last_good, states, passes = None, None, 0
        while not a.mock or passes < 50:
            now = time.monotonic()
            v = (source.poll() or {}).get(a.key)
            if isinstance(v, (list, tuple)) and len(v) >= 8 and not any(math.isnan(float(x)) for x in v[:8]):
                last_good, states = now, [float(x) for x in v[:8]]
            fresh = last_good is not None and now - last_good <= DEFAULT_STALE_AFTER
            try:
                draw(layers, states if fresh else None, a.max)
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

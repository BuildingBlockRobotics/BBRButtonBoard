#!/usr/bin/env python3
"""
Show where the robot is on the field, on a disp2: the field from above and a small robot turned to its heading.

Built on the bbr-buttonboard library (tools/buttonboard) and its Canvas (PROTOCOL.md 14.10). Two ways to draw the
field. With nothing else given it is a line drawing: the border, the centre line and the two alliance walls. With
--image it is a picture of your own, stored on the tile once (a few seconds, and only when the picture changes) and
shown from the tile's own flash; the robot is then drawn over it, and moving it costs two frames: one to put the
picture back where it was, one to draw it where it is.

The robot has to publish its pose. WPILib's Field2d already does, as x and y in metres from the blue alliance's
right-hand corner and a heading in degrees, counter-clockwise from facing the red wall:

    private final Field2d field = new Field2d();
    SmartDashboard.putData("Field", field);          // once
    field.setRobotPose(drive.getPose());             // in robotPeriodic()

Install and run (the board in frc mode, the config website not connected to it):

    pip install "bbr-buttonboard[networktables]"
    python ds_field.py --team 1234
    python ds_field.py --team 1234 --image field.png      # needs bbr-buttonboard[pictures]

    python ds_field.py --demo                        # a made-up robot driving a figure of eight
    python ds_field.py --mock --png field_out.png    # no board either: draw it into a picture
"""
import argparse
import math
import sys
import time

from buttonboard import Board, BoardError, MockBoard, crc16_ccitt_false, picture_bytes
from buttonboard.companion import DEFAULT_STALE_AFTER, MappingSource, NetworkTablesSource

HZ = 10
TEXT_H = 16                      # one line of text under the field
MOVE_STEP = 0.5                  # pixels the robot moves before it is worth redrawing
TURN_STEP = 5                    # ... and degrees it turns


def find_panel(board, addr=None):
    """The disp2 to draw on: the one at `addr`, or the first online that draws lines."""
    for t in board.tiles():
        if t["state"] == "online" and (t["addr"] == addr if addr else t["type"] == "disp2" and t.get("draw")):
            if not t.get("draw"):
                sys.exit(f"The tile at address {addr} does not draw lines. This needs a disp2 on current firmware.")
            return t
    sys.exit(f"No {'tile at address ' + str(addr) if addr else 'disp2 that draws lines'} online. "
             "Check its cable and its firmware, or pass --addr.")


class Field(object):
    """Metres on the field to pixels on the panel. The field's length runs across the screen, blue wall on the
    left, and y runs up it -- the view from the scoring table, which is how Field2d draws it too."""

    def __init__(self, length, width, box):
        self.length, self.width = length, width
        x, y, w, h = box                           # the pixels the field may use
        self.scale = min((w - 1) / length, (h - 1) / width)
        self.w, self.h = length * self.scale, width * self.scale
        self.x0 = x + (w - 1 - self.w) / 2
        self.y0 = y + (h - 1 - self.h) / 2

    def px(self, x, y):
        return self.x0 + x * self.scale, self.y0 + self.h - y * self.scale


def demo_pose(t, length, width):
    """A figure of eight round the middle of the field, facing the way it is going."""
    x = length / 2 + length * 0.36 * math.sin(t / 3)
    y = width / 2 + width * 0.32 * math.sin(2 * t / 3)
    dx, dy = length * 0.36 * math.cos(t / 3) / 3, width * 0.32 * math.cos(2 * t / 3) * 2 / 3
    return [x, y, math.degrees(math.atan2(dy, dx))]


def draw_field(layer, field):
    """The field as lines: the border, the centre line, and each alliance's wall in its colour."""
    (x0, y1), (x1, y0) = field.px(0, 0), field.px(field.length, field.width)
    layer.rect(x0, y0, x1 - x0 + 1, y1 - y0 + 1, "white")
    mid = (x0 + x1) / 2
    layer.line(mid, y0, mid, y1, "#606060")
    layer.line(x0 + 1, y0 + 1, x0 + 1, y1 - 1, "blue")
    layer.line(x1 - 1, y0 + 1, x1 - 1, y1 - 1, "red")


def draw_robot(layer, field, pose, size, colour):
    """The robot's outline turned to its heading, and a line from its middle to its front."""
    layer.clear()
    if pose is None:
        return
    x, y = field.px(pose[0], pose[1])
    x, y = round(x / MOVE_STEP) * MOVE_STEP, round(y / MOVE_STEP) * MOVE_STEP
    heading = round(pose[2] / TURN_STEP) * TURN_STEP
    side = max(7.0, size * field.scale)
    layer.box(x, y, side, side, -heading, colour)   # the panel turns clockwise, the field counter-clockwise
    a = math.radians(heading)
    layer.line(x, y, x + side * 0.75 * math.cos(a), y - side * 0.75 * math.sin(a), colour)


def store_picture(board, addr, slot, path, px_w, px_h):
    """Put the field picture in the slot, unless that picture is already there: a slot's CRC says which one it
    holds, so only a new picture costs the few seconds it takes to load."""
    data = picture_bytes(path, px_w, px_h, "rgb565")
    info = board.disp_slot_info(addr, slot)
    if info and info["len"] == len(data) and info["crc"] == crc16_ccitt_false(data):
        return
    print(f"storing {path} in slot {slot} (once)...", flush=True)
    board.disp_slot_store(addr, slot, data, "rgb565",
                          on_progress=lambda done, total: print(f"\r  {100 * done // total}%", end="", flush=True))
    print(flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--team", type=int, help="team number, to find the robot")
    ap.add_argument("--server", help="NetworkTables server address instead of the team's robot")
    ap.add_argument("--key", default="Field/Robot",
                    help="the pose entry, [x, y, degrees]: a name under /SmartDashboard, or a full path starting with /")
    ap.add_argument("--length", type=float, default=16.54, help="field length in metres (default: 16.54)")
    ap.add_argument("--width", type=float, default=8.07, help="field width in metres (default: 8.07)")
    ap.add_argument("--robot", type=float, default=0.9, help="the robot's side with bumpers, metres")
    ap.add_argument("--colour", default="yellow", help="the robot's colour (default: yellow)")
    ap.add_argument("--image", help="a picture of the field from above, the same shape as the field, blue wall left")
    ap.add_argument("--slot", type=int, default=0, help="the picture slot --image uses (default: 0)")
    ap.add_argument("--port", help="the board's serial port (default: found by USB id)")
    ap.add_argument("--addr", type=int, help="the display's address (default: the first disp2 that draws)")
    ap.add_argument("--demo", action="store_true", help="a made-up robot driving a figure of eight, no robot needed")
    ap.add_argument("--mock", action="store_true", help="no board: draw into memory (implies --demo)")
    ap.add_argument("--png", help="with --mock, write the finished panel to this file")
    a = ap.parse_args()

    clock = time.monotonic
    if a.mock:
        ticks = iter(range(10 ** 9))
        clock = lambda: next(ticks) / HZ            # noqa: E731 -- made-up time, so --mock does not wait
    if a.demo or a.mock:
        source = MappingSource(lambda: {a.key: demo_pose(clock(), a.length, a.width)})
    elif a.team or a.server:
        source = NetworkTablesSource(team=a.team, server=a.server, identity="bbr-ds-field")
    else:
        sys.exit("Give --team (or --server), so the script can find the robot. Or try --demo.")
    source.names([a.key])

    with (MockBoard() if a.mock else Board.open(port=a.port)) as board:
        tile = find_panel(board, a.addr)
        addr, px_w, px_h = tile["addr"], tile["pxW"], tile["pxH"]
        if a.image:
            # The picture fills the panel the way picture_bytes fits it, so the field is the whole of it
            # and there is no room for a line of text underneath.
            if not tile.get("slots"):
                sys.exit(f"The tile at address {addr} has no picture store, so it cannot keep --image.")
            store_picture(board, addr, a.slot, a.image, px_w, px_h)
            canvas = board.canvas(addr, slot=a.slot)
            field = Field(a.length, a.width, (0, 0, px_w, px_h))
            label = None
        else:
            canvas = board.canvas(addr)
            field = Field(a.length, a.width, (0, 0, px_w, px_h - TEXT_H - 2))
            draw_field(canvas.layer(), field)
            label = canvas.layer()
        robot = canvas.layer()
        # A tile that drops off the bus comes back blank: draw it all again, not just what changed.
        board.on("tile", lambda ev: ev.get("addr") == addr and canvas.forget())
        print(f"drawing on the display at address {addr}" + ("" if a.mock else "; Ctrl-C to stop"), flush=True)

        last_good, pose, passes = None, None, 0
        while not a.mock or passes < 50:
            now = time.monotonic()
            v = (source.poll() or {}).get(a.key)
            if isinstance(v, (list, tuple)) and len(v) >= 3 and not any(math.isnan(float(x)) for x in v[:3]):
                last_good, pose = now, [float(x) for x in v[:3]]
            fresh = last_good is not None and now - last_good <= DEFAULT_STALE_AFTER
            try:
                draw_robot(robot, field, pose if fresh else None, a.robot, a.colour)
                if label is not None:
                    label.clear()
                    if fresh:
                        label.text(2, px_h - TEXT_H, f"{pose[0]:4.1f} {pose[1]:3.1f} {pose[2] % 360:3.0f}", "white")
                    else:
                        label.text(32, px_h - TEXT_H, "NO ROBOT", "red")
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

#!/usr/bin/env python3
r"""bbctl -- console for the Button Board config channel (PROTOCOL.md §9).

    bbctl                                  # interactive console
    bbctl hello stats                      # one-shot, then exit
    bbctl -p COM7 "test walk 250"          # explicit port
    bbctl --flash 3 tile-arc4.bin          # tile firmware update (§9.4)
    bbctl --disp 5 0 ABORT --style "black on red"   # §14.7
    bbctl --picture 5 0 logo.png                    # §14.9
    bbctl --rumble-bind 3 5 0                       # §8.8
    bbctl --rumble-watch                            # §8.8

`bbctl` is the command `pip install bbr-buttonboard` puts on the PATH; in
this repository it is `python3 tools/bbctl.py`. A thin shell over the
`buttonboard` package, which is where the protocol handling
lives. It exists because a generic serial terminal gets three things wrong
here: Enter must send \n (a bare \r is ignored, §9.1); DTR must stay asserted
or test mode ends (§9.10); and a reply may be preceded by event lines (§9.3),
so a client must read on to the terminal {"ok":...} line.

Baud rate is ignored by the device; any value works.
"""
import argparse
import os
import sys
import time

from . import rumble
from .client import Board
from .errors import BoardError, CommandError, Timeout


def one_shot(board, commands):
    rc = 0
    for cmd in commands:
        print("> %s" % cmd)
        try:
            board.command(cmd)
        except CommandError:
            rc = 1                      # the response itself was printed by the hook
        except Timeout as e:
            print(e)
            rc = 1
    return rc


def interactive(board):
    print("connected. commands: PROTOCOL.md §9.2   (Ctrl-D or 'quit' to exit)")
    print("try: hello | stats | tiles | test walk 250 | test off | led all off")
    try:
        while True:
            try:
                cmd = input().strip()
            except EOFError:
                break
            if cmd in ("quit", "exit"):
                break
            if cmd:
                board.write_line(cmd)
                time.sleep(0.15)        # let the reply land before the next prompt
    except KeyboardInterrupt:
        pass
    # Closing drops DTR, which ends test mode by design (§9.10).
    print("\nclosing -- DTR drops, so any running test pattern ends here.")
    return 0


def disp(board, addr, row, text, style):
    """Write one row on a display tile (§14.4). Returns a process exit code.

    `style` is `display.attr`'s spec -- "red", "black on yellow", "white on
    blue double" -- because the alternative on a command line is an ATTR byte
    the user has to assemble out of §14.7's two shifts. The explicit `show`
    commits it now rather than 20 ms later, which matters when the next thing
    this process does is close the port.
    """
    try:
        addr, row = int(addr), int(row)
    except ValueError:
        print("--disp takes ADDR ROW TEXT, with ADDR and ROW numbers")
        return 1
    try:
        board.disp_text(addr, row, text, attr=style)
        board.disp_show(addr)
    except ValueError as e:                 # a style or a range this client knows is wrong
        print(e)
        return 1
    except (CommandError, Timeout) as e:
        print("disp failed: %s" % e)
        return 1
    return 0


def picture(board, addr, slot, path, fmt, show=True):
    """Store a picture in a display tile's slot, §14.9. Returns an exit code.

    The one config-channel operation measured in seconds -- a full-colour
    128x128 picture is 32,768 bytes, 512 chunks and 2-4 s of auxiliary slot --
    so it gets the same progress bar `--flash` has, for the same reason. It is
    spent once: showing the picture afterwards is a single frame.
    """
    try:
        addr, slot = int(addr), int(slot)
    except ValueError:
        print("--picture takes ADDR SLOT FILE, with ADDR and SLOT numbers")
        return 1
    width = 40

    def bar(done, total):
        filled = width * done // max(1, total)
        sys.stderr.write("\r  [%s%s] %d/%d bytes" % ("#" * filled, "." * (width - filled),
                                                     done, total))
        sys.stderr.flush()

    print("> disp %d slot %d <- %s (%s)" % (addr, slot, path, fmt))
    try:
        board.disp_store_image(addr, slot, path, fmt, show=show, on_progress=bar)
    except (ValueError, RuntimeError) as e:     # Pillow missing, or a bad format
        sys.stderr.write("\n")
        print(e)
        return 1
    except (CommandError, Timeout) as e:
        sys.stderr.write("\n")
        print("picture failed: %s" % e)
        # An interrupted store leaves the slot EMPTY rather than half a
        # picture (§14.9), so the retry is just this command again.
        print("slot %d on tile %d is empty; re-run to retry" % (slot, addr))
        return 1
    sys.stderr.write("\n")
    print("ok -- slot %d holds %s%s" % (slot, os.path.basename(path),
                                        ", and is on the panel" if show else ""))
    return 0


def rumble_list(board):
    """Print the map's rumble bindings, §8.8. Returns an exit code."""
    try:
        mapping = board.map_get()
        bound = rumble.bindings(mapping)
        actions = rumble.led_actions(mapping)
    except (CommandError, Timeout) as e:
        print("map get failed: %s" % e)
        return 1
    except ValueError as e:                 # the board's map is not one we like
        print("the map's rumble section is not valid: %s" % e)
        return 1
    if not bound and not actions:
        print("no rumble bindings; a code with no entry does nothing (§8.8)")
        return 0
    if bound:
        print("code  tile  slot   from an OpMode")
        for entry in bound:
            print("%4d  %4d  %4d   showLegend(%d)"
                  % (entry["code"], entry["addr"], entry["slot"], entry["code"]))
    if actions:
        print("code  key           colour (live layer, rev 0.33)")
        for entry in actions:
            where = ("btn %d" % entry["btn"]) if "btn" in entry \
                else ("tile %d LED %d" % (entry["addr"], entry["led"]))
            print("%4d  %-12s  %s%s" % (entry["code"], where, entry["rgb"],
                                        " " + entry["fx"] if "fx" in entry else ""))
    return 0


def rumble_bind(board, code, addr, slot, save=True):
    """Bind one legend code to a tile's stored picture. Returns an exit code."""
    try:
        entry = rumble.entry(code, addr, slot)
    except ValueError as e:
        print(e)
        return 1
    try:
        board.rumble_bind(entry["code"], entry["addr"], entry["slot"])
        if save:
            board.save()
    except (CommandError, Timeout) as e:
        print("rumble bind failed: %s" % e)
        return 1
    print("ok -- code %d shows tile %d slot %d%s"
          % (entry["code"], entry["addr"], entry["slot"],
             "" if save else "; NOT saved, so it is gone at the next power-up"))
    return 0


def rumble_unbind(board, code, save=True):
    try:
        code = rumble.check_code(code)
    except ValueError as e:
        print(e)
        return 1
    try:
        board.rumble_unbind(code)
        if save:
            board.save()
    except (CommandError, Timeout) as e:
        print("rumble unbind failed: %s" % e)
        return 1
    print("ok -- code %d is unbound%s"
          % (code, "" if save else "; NOT saved"))
    return 0


def rumble_watch(board):
    """Print rumble codes and player indices as they arrive, until Ctrl-C.

    The bench half of §8.8: run the OpMode call and read the number the board
    actually decoded, rather than trusting the arithmetic. Every packet is
    reported whether or not it is bound, which is what makes this a binding
    flow and not just a log.
    """
    bound = {}
    try:
        bound = {e["code"]: e for e in board.rumble_bindings()}
    except (BoardError, ValueError):
        pass                        # a board with no readable map still watches
    board.on("rumble", lambda ev: print(
        "rumble left=%-3d right=%-3d -> code %-2d %s"
        % (ev.get("left", 0), ev.get("right", 0), ev.get("code", 0),
           "(stop, not an action)" if not ev.get("code")
           else ("-> tile %d slot %d" % (bound[ev["code"]]["addr"],
                                         bound[ev["code"]]["slot"]))
           if ev["code"] in bound else "(unbound)")))
    board.on("player", lambda ev: print("player index %s" % ev.get("n")))
    print("watching for rumble codes (§8.8). An OpMode sends one with")
    print("  board.showLegend(c);   // robot/ftc, i.e. gamepad.rumble(c/15.0, 0, ms)")
    print("Ctrl-C to stop.")
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    return 0


def flash(board, addr, path):
    """Stream a tile firmware image, §9.4. Returns a process exit code."""
    size = os.path.getsize(path)
    print("> flash %s %s (%d bytes)" % (addr, path, size))
    width, state = 40, {"n": 0}

    def bar(ev):
        state["n"] = ev["done"]
        filled = width * ev["done"] // max(1, ev["total"])
        sys.stderr.write("\r  [%s%s] %d/%d bytes" % ("#" * filled, "." * (width - filled),
                                                     ev["done"], ev["total"]))
        sys.stderr.flush()

    try:
        result = board.flash(int(addr), path, on_progress=bar)
    except (CommandError, Timeout) as e:
        sys.stderr.write("\n")
        print("flash failed: %s" % e)
        # A tile that got as far as ENTER_BOOT is sitting in its bootloader at
        # the same address, so the retry is just this command again (§9.4) --
        # worth saying, because a half-written tile otherwise looks bricked.
        # The refusals below come from the `flash` line itself, before the
        # tile was ever told to reboot, so they would be a lie.
        refused = isinstance(e, CommandError) and e.err in ("args", "no_tile", "unknown")
        if not refused:
            print("the tile is in its bootloader at address %s%s; re-run to retry"
                  % (addr, " (if it got that far)" if isinstance(e, Timeout) else ""))
        return 1
    sys.stderr.write("\n")
    print("ok -- tile %s is running %s" % (result.get("addr", addr), result.get("fw", "?")))
    return 0


def main():
    ap = argparse.ArgumentParser(prog="bbctl", description="Button Board config channel client")
    ap.add_argument("-p", "--port", help="serial port (default: auto-detect)")
    ap.add_argument("--flash", nargs=2, metavar=("ADDR", "IMAGE"),
                    help="send a tile firmware image (the .bin from `make bin`), then exit")
    ap.add_argument("--disp", nargs=3, metavar=("ADDR", "ROW", "TEXT"),
                    help="write one row on a display tile, then exit")
    ap.add_argument("--picture", nargs=3, metavar=("ADDR", "SLOT", "FILE"),
                    help="store an image in a display tile's picture slot and "
                         "show it, then exit (§14.9). Needs Pillow.")
    ap.add_argument("--fmt", default="rgb565", choices=("rgb565", "1bpp"),
                    help="picture format for --picture: full-colour RGB565 "
                         "(default) or 1 bpp")
    ap.add_argument("--rumble-list", action="store_true",
                    help="list the map's rumble legend bindings (§8.8), then exit")
    ap.add_argument("--rumble-bind", nargs=3, metavar=("CODE", "ADDR", "SLOT"),
                    help="bind legend CODE (1-15) to a stored picture (§14.9) "
                         "on tile ADDR, then save and exit")
    ap.add_argument("--rumble-unbind", metavar="CODE",
                    help="drop a legend code's binding, then save and exit")
    ap.add_argument("--rumble-watch", action="store_true",
                    help="print rumble codes and player indices as they "
                         "arrive, until Ctrl-C (§8.8, §8.5)")
    ap.add_argument("--no-save", action="store_true",
                    help="for --rumble-bind/--rumble-unbind: leave the change "
                         "in RAM rather than committing it to flash")
    ap.add_argument("--style", metavar="SPEC",
                    help='style for --disp: "red", "black on yellow", '
                         '"white on blue double", "inverse" (§14.7). Colour '
                         'needs a colour tile; a mono panel refuses it.')
    ap.add_argument("commands", nargs="*", help="commands to run, then exit")
    args = ap.parse_args()
    if args.style and not args.disp:
        ap.error("--style is for --disp")
    if args.fmt != "rgb565" and not args.picture:
        ap.error("--fmt is for --picture")
    if args.no_save and not (args.rumble_bind or args.rumble_unbind):
        ap.error("--no-save is for --rumble-bind and --rumble-unbind")
    rumbling = bool(args.rumble_list or args.rumble_bind or args.rumble_unbind
                    or args.rumble_watch)

    try:
        board = Board.open(port=args.port)
    except BoardError as e:
        sys.exit(str(e))
    # The rumble modes print a line of their own for each event, so the raw
    # echo would say everything twice.
    if not args.flash and not args.picture and not rumbling:
        board.on_line(print)            # echo everything the board says
    try:
        if args.flash:                  # its own progress bar; raw echo would fight it
            return flash(board, args.flash[0], args.flash[1])
        if args.picture:            # its own progress bar, like --flash
            return picture(board, args.picture[0], args.picture[1],
                           args.picture[2], args.fmt)
        if args.disp:
            return disp(board, args.disp[0], args.disp[1], args.disp[2], args.style)
        if args.rumble_bind:
            return rumble_bind(board, args.rumble_bind[0], args.rumble_bind[1],
                               args.rumble_bind[2], save=not args.no_save)
        if args.rumble_unbind:
            return rumble_unbind(board, args.rumble_unbind, save=not args.no_save)
        if args.rumble_list:
            return rumble_list(board)
        if args.rumble_watch:
            return rumble_watch(board)
        return one_shot(board, args.commands) if args.commands else interactive(board)
    finally:
        board.close()


if __name__ == "__main__":
    sys.exit(main())

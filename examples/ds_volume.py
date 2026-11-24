#!/usr/bin/env python3
"""
Show the computer's volume on a disp2 as a bar, and light the mute key red while the sound is muted.

Built on the bbr-buttonboard library (tools/buttonboard) for a board in desk mode (PROTOCOL.md 8.9). The board
does the work itself: an enc1 whose knob sends Volume up and Volume down turns the computer's volume, and a key
that sends Mute mutes it, with no script at all. This script is the feedback. It reads the volume from the
computer, not from the knob, so the bar follows the keyboard's own volume keys and the menu bar slider too.

Set the board up in the config app first:
  1. Configure -> Mode -> Keyboard & media. Save, unplug and plug back in.
  2. Select the enc1's knob -> Knob -> Volume knob.
  3. Select the mx1's key -> Sends -> Mute. Save.

Install and run (the config website not connected to the board):

    pip install bbr-buttonboard
    python ds_volume.py                              # macOS and Linux as they are
    pip install pycaw && python ds_volume.py         # Windows needs pycaw to read the volume

    python ds_volume.py --mock --png volume.png      # no board: a made-up volume, drawn into a picture
"""
import argparse
import platform
import re
import subprocess
import sys
import time

from buttonboard import Board, BoardError, MockBoard

HZ = 10
W = 160                          # the disp2's panel, pixels across
BAR_X, BAR_Y, BAR_W, BAR_H = 8, 44, 144, 28
CHAR_W = 12                      # the tile's font: 12 x 16 per character


def centred(text):
    return (W - CHAR_W * len(text)) // 2


# ---- reading the computer's volume: (percent 0-100, muted), or None when it cannot be read ---------------------

def _run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout


def volume_macos():
    # "output volume:44, input volume:50, alert volume:100, output muted:false". An output with no volume control
    # (HDMI, some USB DACs) says "missing value".
    out = _run("osascript", "-e", "get volume settings")
    v, m = re.search(r"output volume:(\d+)", out), re.search(r"output muted:(true|false)", out)
    return (int(v.group(1)), m.group(1) == "true") if v and m else None


def volume_linux():
    try:                                            # PipeWire: "Volume: 0.50 [MUTED]"
        out = _run("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@")
        v = re.search(r"Volume:\s*([\d.]+)", out)
        if v:
            return round(float(v.group(1)) * 100), "MUTED" in out
    except FileNotFoundError:
        pass
    # PulseAudio: "Volume: front-left: 32768 /  50% / ..." and "Mute: no"
    v = re.search(r"(\d+)%", _run("pactl", "get-sink-volume", "@DEFAULT_SINK@"))
    m = re.search(r"Mute:\s*(yes|no)", _run("pactl", "get-sink-mute", "@DEFAULT_SINK@"))
    return (int(v.group(1)), m.group(1) == "yes") if v and m else None


def volume_windows():
    from pycaw.pycaw import AudioUtilities          # pip install pycaw
    speakers = AudioUtilities.GetSpeakers()
    ep = getattr(speakers, "EndpointVolume", None)  # pycaw 2025 and later
    if ep is None:                                  # older pycaw: activate the interface by hand
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume
        ep = cast(speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))
    return round(ep.GetMasterVolumeLevelScalar() * 100), bool(ep.GetMute())


def volume_reader():
    system = platform.system()
    if system == "Darwin":
        return volume_macos
    if system == "Linux":
        return volume_linux
    if system == "Windows":
        try:
            import pycaw  # noqa: F401
        except ImportError:
            sys.exit("Reading the volume on Windows needs pycaw: pip install pycaw")
        return volume_windows
    sys.exit(f"Don't know how to read the volume on {system}.")


def volume_demo(t):
    """A made-up volume for --mock: up from 0 to 100, then muted for a while."""
    level = min(100, int(t * 25))
    return level, t > 5


# ---- the board ---------------------------------------------------------------------------------------------------

def find_panel(board, addr=None):
    """The disp2 to draw on: the one at `addr`, or the first online that draws."""
    for t in board.tiles():
        if t["state"] == "online" and (t["addr"] == addr if addr else t["type"] == "disp2" and t.get("draw")):
            if not t.get("draw"):
                sys.exit(f"The tile at address {addr} cannot draw. This needs a disp2 on firmware 0.3.0 or newer.")
            return t["addr"]
    sys.exit(f"No {'tile at address ' + str(addr) if addr else 'disp2 that draws'} online. "
             "Check its cable and its firmware, or pass --addr.")


def check_setup(board, mock=False):
    """The button numbers that send Mute (a key and a knob's push can both), and a word about anything that will not work as expected."""
    if not mock and board.hello().get("usb") != "desk":     # the mock is always frc
        print("note: the board is not in desk mode, so the knob and the mute key will not reach the computer. "
              "The display still follows the volume.", file=sys.stderr)
    m = board.map_get()
    hid = m.get("hid", {})
    if not any(hid.get(str(e.get("cw"))) == "vol_up" for e in m.get("enc", [])):
        print("note: no knob turns the volume up. In the config app select the knob and pick Volume knob.",
              file=sys.stderr)
    mute = sorted(int(b) for b, act in hid.items() if act == "mute")
    if not mute:
        print("note: no key sends Mute, so no key will light. In the config app select the key and pick Mute.",
              file=sys.stderr)
    return mute


def draw(layers, reading):
    bar, label = layers
    bar.clear()
    label.clear()
    if reading is None:
        label.text(centred("NO VOLUME"), 84, "NO VOLUME", "orange")
        return
    level, muted = reading
    width = round((BAR_W - 4) * max(0, min(level, 100)) / 100)
    if width:
        bar.fill(BAR_X + 2, BAR_Y + 2, width, BAR_H - 4, "#404040" if muted else "lime")
    text = f"{level}%"
    label.text(centred(text), 84, text, "#808080" if muted else "white")
    if muted:
        label.text(centred("MUTED"), 106, "MUTED", "white", "red")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", help="the board's serial port (default: found by USB id)")
    ap.add_argument("--addr", type=int, help="the display's address (default: the first disp2)")
    ap.add_argument("--mock", action="store_true", help="no board and a made-up volume: draw into memory")
    ap.add_argument("--png", help="with --mock, write the finished panel to this file")
    a = ap.parse_args()

    if a.mock:
        ticks = iter(range(10 ** 9))
        read = lambda: volume_demo(next(ticks) / HZ)    # noqa: E731 -- made-up time, so --mock does not wait
    else:
        read = volume_reader()

    with (MockBoard() if a.mock else Board.open(port=a.port)) as board:
        addr = find_panel(board, a.addr)
        if a.mock:                  # the set-up the docstring asks for: a volume knob and a mute key
            board.map_set(dict(board.map_get(), hid={"1": "mute", "2": "vol_up", "3": "vol_down"},
                               enc=[{"addr": 3, "idx": 0, "cw": 2, "ccw": 3}]))
        mute_btns = check_setup(board, a.mock)
        canvas = board.canvas(addr)
        frame = canvas.layer()
        frame.text(centred("VOLUME"), 8, "VOLUME", "white")
        frame.rect(BAR_X, BAR_Y, BAR_W, BAR_H, "white")
        layers = (canvas.layer(), canvas.layer())
        # A tile that drops off the bus comes back blank: draw it all again, not just what changed.
        board.on("tile", lambda ev: ev.get("addr") == addr and canvas.forget())
        print(f"showing the volume on the display at address {addr}" + ("" if a.mock else "; Ctrl-C to stop"),
              flush=True)

        lit, passes = None, 0
        try:
            while not a.mock or passes < 80:
                try:
                    reading = read()
                except (OSError, subprocess.SubprocessError, ValueError):
                    reading = None
                muted = bool(reading and reading[1])
                try:
                    draw(layers, reading)
                    canvas.flush()
                    if mute_btns and muted != lit:
                        # Red while muted. Unmuted, the key goes back to its colour from the config app.
                        if muted:
                            for btn in mute_btns:
                                board.led(btn, "red")
                        else:
                            board.led_reset()
                        lit = muted
                except BoardError as e:     # a frame that failed did not land: start from a clear panel next time
                    canvas.forget()
                    lit = None
                    print(f"board: {e}", file=sys.stderr, flush=True)
                passes += 1
                if not a.mock:
                    time.sleep(1.0 / HZ)
        finally:
            if lit:
                board.led_reset()
        print(f"{passes} passes took {canvas.frames} frames on the bus", flush=True)
        if a.png:
            print("wrote", board.state.panel(addr).save(a.png), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

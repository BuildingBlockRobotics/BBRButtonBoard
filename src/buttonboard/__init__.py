"""Button Board host client (PROTOCOL.md §9).

    from buttonboard import Board
    with Board.open() as board:                 # auto-detects the COM port
        board.hello()
        board.led(3, "red", fx="blink")
        board.screen(4).text(0, "AUTO 12.4s").flush()
        board.screen(5).text(0, "ABORT", fg="black", bg="red").flush()

The intended writer is a Driver Station companion app (§9.8, §13.5, §14.4):
it reads NetworkTables and pushes LED colours and display text. For a loop
that recomputes everything each pass, use the diffing writers -- `LedLayer`
and `Screen` -- so only real changes reach the bus.

Colour (§14.7) is said the way it reads out loud -- `"red on white"`, or
`fg="black", bg="yellow"` -- and `buttonboard.attr()` turns that into the ATTR byte.
Only a tile advertising `CAP_DISP_COLOR` accepts it: a mono panel refuses
colour rather than drawing white, and this client refuses it first.

MockBoard speaks the same protocol in-process, which is how the display and
sound halves are tested without hardware. `disp` is live (§14); `sound` is
still reserved and the board answers `unknown` to it (§16). Its default chain
carries a mono `disp1` at address 4 and a colour `screenkey1` at address 5;
the screen-key has a picture store (§14.9), so `board.disp_store_image(5, 0,
"logo.png")` loads a full-colour legend once and `board.disp_slot_show(5, 0)`
puts it on the glass in a single frame. `board.rumble_bind(1, 5, 0)` binds a
legend code to that slot (§8.8), which is how a match-time `gamepad.rumble()`
from an OpMode reaches a board that has no config channel at all.
"""
__version__ = "0.1.0"

from . import rumble
from .client import Board, FLASH_CHUNK, MAX_LINE
from .crc import crc16_ccitt_false, crc8_maxim
from .display import COLORS, Screen, attr, picture_bytes
from .errors import (BoardError, CommandError, NoBoardFound, NotConnected,
                     Timeout)
from .leds import FX, NAMED, LedLayer, rgb
from .mock import MockBoard
from .rumble import CODE_MAX as RUMBLE_CODE_MAX, CODE_STEP as RUMBLE_CODE_STEP
from .sound import TUNES
from .transport import LoopTransport, SerialTransport, find_port

__all__ = [
    "Board", "MockBoard", "Screen", "LedLayer", "attr", "COLORS",
    "picture_bytes",
    "SerialTransport", "LoopTransport", "find_port",
    "BoardError", "CommandError", "NoBoardFound", "NotConnected", "Timeout",
    "rgb", "FX", "NAMED", "TUNES", "MAX_LINE", "FLASH_CHUNK",
    "rumble", "RUMBLE_CODE_MAX", "RUMBLE_CODE_STEP",
    "crc8_maxim", "crc16_ccitt_false",
]

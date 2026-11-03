# bbr-buttonboard

Python client for the [BBR Button Board](https://bbrbuttonboard.com/), a
modular driver station panel for FIRST robotics.

The board works as a game controller with nothing installed. This library is
for making the panel react to the robot: a short script on the Driver Station
laptop reads robot state (from NetworkTables, in FRC) and sets key colours,
display text and pictures to match.

## Install

```sh
pip install bbr-buttonboard
```

Python 3.9 or newer. The only dependency is `pyserial`. Two extras:

```sh
pip install "bbr-buttonboard[pictures]"       # Pillow, to load images onto display tiles
pip install "bbr-buttonboard[networktables]"  # pyntcore, for the companion app
```

## A first script

```python
from buttonboard import Board

with Board.open() as board:          # finds the port by itself
    leds = board.led_layer()

    while True:
        arm_fault = read_from_networktables("arm_fault")
        in_range = read_from_networktables("in_scoring_range")

        leds.set(3, "red" if arm_fault else "off")
        leds.set(7, "green" if in_range else "off", fx="breathe")
        leds.flush()                 # sends only what changed
```

Display tiles take text the same way:

```python
board.screen(5).text(0, "ABORT", fg="black", bg="red").flush()
```

`MockBoard` is an in-process board, so a script can be written and tested
with no hardware attached:

```python
from buttonboard import MockBoard

board = MockBoard()
board.led(3, "red")
print(board.state.leds)              # {('btn', 3): 'led 3 FF0000'}
```

Only one program can have the board open at a time. Close the config app's
browser tab before running a script, and the other way round.

## bbctl

Installing the package also installs `bbctl`, a console for the board's
config channel:

```sh
bbctl                       # interactive
bbctl hello stats           # run commands and exit
bbctl -p COM7 "led reset"   # explicit port
```

## Documentation

* [Install and first script](https://buttonboard.buildingblockrobotics.com/driver-station/install.html)
* [API summary](https://buttonboard.buildingblockrobotics.com/driver-station/api.html)
* [Config channel commands](https://buttonboard.buildingblockrobotics.com/reference/commands.html)

## Tests

```sh
python tests/test_buttonboard.py
```

No hardware and no pytest needed.

## FTC (Java)

[`ftc/`](https://github.com/BuildingBlockRobotics/BBRButtonBoard/tree/main/ftc) holds the Java helpers for an FTC OpMode: changing a screen
key's legend mid-match with `gamepad.rumble()`, and reading a knob through a
trigger. FTC teams copy the files into TeamCode; see [ftc/README.md](https://github.com/BuildingBlockRobotics/BBRButtonBoard/blob/main/ftc/README.md).

## About this repository

This is a published copy of the library. It is developed alongside the
Button Board firmware and exported here for each release, so a pull request
is welcome but will be applied upstream and come back in the next export.

The `§` references in the source point into the Button Board protocol
specification, which is not published yet.

## Licence

MIT. See [LICENSE](LICENSE).

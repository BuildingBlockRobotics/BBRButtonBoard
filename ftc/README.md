# Button Board helpers for FTC

Java for an FTC OpMode that works with a [BBR Button Board](https://bbrbuttonboard.com/)
plugged into the Driver Station as a gamepad:

- **`ButtonBoard`** changes the legend on a screen key, a `disp2` or a `disp1`
  during a match: `board.showLegend(2)`.
- **`TriggerKnob`** reads a knob set to count on a trigger, as a running total
  of clicks.

There is no Gradle dependency. Copy four files into TeamCode and you are done.

## Adding it to TeamCode

1. Download this repository: **Code → Download ZIP** on GitHub, or
   `git clone https://github.com/BuildingBlockRobotics/BBRButtonBoard.git`.
2. Copy the `org` folder from `ftc/` into your TeamCode sources, merging it
   with the one already there:

   ```
   cp -r BBRButtonBoard/ftc/org  TeamCode/src/main/java/
   ```

   That gives you `TeamCode/src/main/java/org/firstinspires/ftc/teamcode/buttonboard/`
   with four files:

   | File | What |
   |---|---|
   | `ButtonBoard.java` | Legend codes over `gamepad.rumble()`: the encoding, sending only on change, and refusing code 0 |
   | `GamepadRumbleSink.java` | Connects `ButtonBoard` to a `Gamepad`. The only file that uses the FTC SDK |
   | `RumbleSink.java` | The one-method interface between those two |
   | `TriggerKnob.java` | A knob's trigger count back to a running total |

3. If your team uses a different package root, change the `package` line at
   the top of all four files to match where you put them.

Requires FTC SDK v7.0 or newer.

## Changing legends

Set the board up in the [config app](https://configure.buttonboard.buildingblockrobotics.com/)
first: load the pictures or messages onto the display tile, then bind each one
to a **legend code** from 1 to 15 on the tile's *Legend codes* section. The
Legends tab shows the exact `showLegend` line for every code in use.

Then, in the OpMode:

```java
import org.firstinspires.ftc.teamcode.buttonboard.ButtonBoard;
import org.firstinspires.ftc.teamcode.buttonboard.GamepadRumbleSink;

static final int LEGEND_INTAKE = 1;     // the codes as the config app binds them
static final int LEGEND_SCORE  = 2;

// gamepad1 must be the position the Button Board took on the Driver Station
ButtonBoard board = new ButtonBoard(new GamepadRumbleSink(gamepad1));

while (opModeIsActive()) {
    board.showLegend(gamepad1.a ? LEGEND_SCORE : LEGEND_INTAKE);   // safe every loop
}
```

| Call | Does |
|---|---|
| `showLegend(int code)` | Show the legend bound to `code`, unless it is already the last one sent. Codes are 1–15. |
| `refresh()` | Send the last code again, for when the board was power-cycled mid-match. |
| `forget()` | Drop the record of the last code without sending anything. |
| `lastLegend()` | The last code sent. |
| `stopRumble()` | Zero power. Does **not** clear the key. To blank a key, bind a code to a blank picture. |

Things worth knowing:

- **The number is a legend code, not a key.** `showLegend(3)` shows whatever
  the board's map binds to code 3, so the panel can be rearranged without
  changing robot code. A code with nothing bound does nothing.
- **Rumble goes to one gamepad.** If the board is gamepad 2, pass `gamepad2`.
  Sending to the wrong one fails silently, because the board cannot reply.
- **Hold a legend for at least ~100 ms** if it has to be seen. The SDK keeps
  only the newest rumble command and sends about every 50 ms, so a quick
  A → B → C may skip B. The last code always arrives.

## Reading a knob

The Driver Station puts a deadzone on every stick, so a knob can't use one.
In the config app set the knob's **Trigger (xinput)** to LT or RT, and it
counts clicks on that trigger instead:

```java
import org.firstinspires.ftc.teamcode.buttonboard.TriggerKnob;

TriggerKnob knob = new TriggerKnob();               // before the loop
int clicks = knob.update(gamepad2.left_trigger);    // every loop, on the board's gamepad and trigger
```

Call `update` every loop. It stays right as long as fewer than 128 clicks pass
between two reads. The count starts at 0, and `reset()` makes the current
position zero.

## Status

Both have run on a Driver Station: a legend change from an OpMode reached a
screen key, and a knob counted correctly through `TriggerKnob`. Test with your
own Driver Station before relying on either in a match.

The full guide is in the Button Board documentation:
[Changing legends in a match](https://buttonboard.buildingblockrobotics.com/on-the-robot/legends.html).

The `§` references in the comments point into the Button Board protocol
specification, which is not public yet.

## Licence

MIT, as the rest of this repository.

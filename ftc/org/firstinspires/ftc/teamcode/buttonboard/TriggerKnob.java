package org.firstinspires.ftc.teamcode.buttonboard;

/**
 * A Button Board knob read from an xinput trigger (PROTOCOL.md §15.6, rev 0.37).
 *
 * <p>In {@code xinput} the board cannot put a knob on a stick axis: the FTC SDK
 * gives every stick axis a 0.15 deadzone and rescales the rest. The triggers have
 * neither, so an encoder whose map entry has {@code "trigger": "lt"} or {@code "rt"}
 * sends its click count there, wrapping at 256. This turns that back into a running
 * total:
 *
 * <pre>
 * TriggerKnob knob = new TriggerKnob();
 * while (opModeIsActive()) {
 *     int clicks = knob.update(gamepad2.left_trigger);   // the board's gamepad
 *     ...
 * }
 * </pre>
 *
 * <p>Each update adds the change since the last one, taking the short way round the
 * wrap, so it stays right as long as the knob moves fewer than 128 clicks between two
 * reads. Call it every loop. The first reading is the baseline: the count starts at
 * 0 there, whatever the trigger says.
 *
 * <p>The count is relative, not an absolute position: the board does not reset it
 * when an OpMode starts, and {@code reset()} only moves this side's zero. Clockwise
 * counts up; the map's {@code invert} flips that.
 *
 * <p>Platform-free, so it is tested on a desktop JDK with the rest of the library.
 */
public final class TriggerKnob {
    private int count;
    private int last = -1;

    /** The trigger value as the board sent it, 0..255. The SDK reports {@code raw / 255}. */
    public static int rawOf(float trigger) {
        int v = Math.round(trigger * 255f);
        return v < 0 ? 0 : (v > 255 ? 255 : v);
    }

    /** Feed this loop's trigger reading; returns the running count. */
    public int update(float trigger) {
        int v = rawOf(trigger);
        if (last >= 0) {
            int d = ((v - last + 128) & 0xFF) - 128;    // -128..127: the short way round
            count += d;
        }
        last = v;
        return count;
    }

    /** The count as of the last {@link #update}. */
    public int count() {
        return count;
    }

    /** Make the current position zero. The next update carries on from here. */
    public void reset() {
        count = 0;
    }
}

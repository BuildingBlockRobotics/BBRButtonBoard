package org.firstinspires.ftc.teamcode.buttonboard;

/**
 * The one call this library needs from the outside world: put a rumble packet
 * on the wire.
 *
 * <p>The signature is the FTC SDK's {@code Gamepad.rumble(double, double, int)}
 * and nothing more, so that {@link ButtonBoard} — the encoding and the
 * change detection — never mentions the SDK and compiles and tests on a plain
 * JDK. {@code GamepadRumbleSink} is the two-line adapter that holds a real
 * {@code com.qualcomm.robotcore.hardware.Gamepad}; a test passes a fake that
 * records what it was handed.
 *
 * <p>This is the same split the board's firmware uses: protocol logic in a
 * platform-free module, I/O behind a small callback, glue thin and separate.
 */
public interface RumbleSink {
    /**
     * @param left       motor 1 power, 0.0–1.0. This is the byte the board
     *                   decodes as a legend code (PROTOCOL.md §8.8).
     * @param right      motor 2 power, 0.0–1.0. Reported by the board but not
     *                   matched; this library always sends 0.
     * @param durationMs how long the step lasts, or
     *                   {@code Gamepad.RUMBLE_DURATION_CONTINUOUS} (-1).
     */
    void rumble(double left, double right, int durationMs);
}

package org.firstinspires.ftc.teamcode.buttonboard;

import com.qualcomm.robotcore.hardware.Gamepad;

/**
 * The only file in this library that mentions the FTC SDK.
 *
 * <p>It holds a {@code Gamepad} and forwards one call. Everything else —
 * the encoding, the change detection, the refusal of code 0 — lives in
 * {@link ButtonBoard}, which compiles and is tested on a plain JDK with a fake
 * {@link RumbleSink}. Keep it that way: if logic starts appearing here, it
 * stops being testable.
 *
 * <p>It lives in {@code src-ftc/} rather than {@code src/} for the same
 * reason: the host build compiles {@code src/} and cannot see the SDK.
 * Both directories use the same package, so a team that copies the whole
 * package into TeamCode gets all of it in one place.
 *
 * <p>Usage in an OpMode:
 * <pre>
 * ButtonBoard board = new ButtonBoard(new GamepadRumbleSink(gamepad1));
 * </pre>
 *
 * <p>The gamepad reference is held, not copied. That is deliberate: the FTC
 * SDK replaces the <i>contents</i> of {@code gamepad1} each loop but keeps the
 * object, so holding it is correct and holding a snapshot would not be.
 */
public final class GamepadRumbleSink implements RumbleSink {

    private final Gamepad gamepad;

    /**
     * @param gamepad the OpMode's {@code gamepad1} or {@code gamepad2} —
     *                whichever one the Button Board is bound to in the Driver
     *                Station. Rumble goes to that pad and no other, so binding
     *                the board as gamepad1 and calling this with gamepad2 sends
     *                the legend to the wrong device, silently.
     */
    public GamepadRumbleSink(Gamepad gamepad) {
        if (gamepad == null) {
            throw new IllegalArgumentException("gamepad is null");
        }
        this.gamepad = gamepad;
    }

    @Override
    public void rumble(double left, double right, int durationMs) {
        gamepad.rumble(left, right, durationMs);
    }
}

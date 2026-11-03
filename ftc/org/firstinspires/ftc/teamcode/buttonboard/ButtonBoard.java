package org.firstinspires.ftc.teamcode.buttonboard;

/**
 * Change the legend on a Button Board screen key from an OpMode, over rumble.
 *
 * <p><b>Why rumble.</b> A board in {@code xinput} mode is single-interface and
 * has no config channel (PROTOCOL.md §8.3), so during a match the XInput OUT
 * endpoint is the only way a host can reach it. {@code gamepad.rumble()} is the
 * sanctioned OpMode call that puts bytes there. It cannot carry text, but it
 * carries a number, and a picture stored on a screen-key tile (§14.9) is
 * switched by a number. So one call changes a key's legend, in full colour, in
 * one frame.
 *
 * <p><b>What the number means, and the indirection to be honest about.</b> The
 * argument here is a <i>legend code</i>, 1–15. It is <b>not</b> a key number, a
 * button index or a slot number. The board's map binds each code to a
 * {@code (tile address, picture slot)} pair:
 *
 * <pre>
 * "rumble":[{"code":1,"addr":5,"slot":0},
 *           {"code":2,"addr":5,"slot":3}]
 * </pre>
 *
 * <p>Rebinding the map — in the web config app, without touching robot code —
 * changes which key lights up and which picture appears for the same code. That
 * is the point of the indirection: the OpMode says "state 3", the panel layout
 * says what state 3 looks like. A caller who reads {@code showLegend(3)} as
 * "key 3" will be surprised the first time someone rebinds, which is why no
 * method here is called {@code setButton}.
 *
 * <p>A code with no binding does nothing. So does a binding whose tile is
 * absent or whose slot is empty — a match is not the place to report a
 * configuration error (§8.8).
 *
 * <p><b>Code 0 is never an action.</b> {@code rumble(0, 0, ms)} is how the SDK
 * <i>stops</i> a rumble, and a stop must not change what is on a screen. This
 * class refuses code 0 from {@link #showLegend(int)} and never sends it as a
 * legend change; {@link #stopRumble()} sends it deliberately, and the board
 * ignores it.
 *
 * <p><b>Send only on change.</b> An OpMode loop runs at about 50 Hz. Calling
 * rumble every iteration would hammer the SDK's outgoing rumble slot for no
 * reason — and the SDK's own sample says in as many words not to spam it.
 * {@link #showLegend(int)} remembers the last code it sent and suppresses
 * repeats, the way the display client diffs rows before writing them. When the
 * belief is stale — the board was power-cycled mid-match, someone unplugged it
 * — {@link #refresh()} sends the remembered code again and {@link #forget()}
 * drops the belief so the next call sends whatever it is handed.
 *
 * <p><b>Not verified on hardware.</b> Nothing in this path has run on a real
 * Driver Station against a real board. See the README before trusting it in a
 * match.
 *
 * <p>Not thread-safe; call it from the OpMode thread, as with everything else
 * in an OpMode.
 */
public final class ButtonBoard {

    /**
     * Highest legend code. Mirrors {@code RUMBLE_CODE_MAX} in
     * {@code shared/protocol.h}; the test asserts the two still agree.
     */
    public static final int LEGEND_CODE_MAX = 15;

    /**
     * Byte step per code. 255 / 15 is 17 exactly, so code {@code c} is sent as
     * {@code c / 15.0}, arrives as {@code c * 17}, and the firmware recovers it
     * with {@code (v + 8) / 17} — every code sits in the middle of a ±8 band
     * and nothing lands on a rounding boundary. Mirrors
     * {@code RUMBLE_CODE_STEP}.
     */
    public static final int LEGEND_CODE_STEP = 17;

    /**
     * No legend has been sent yet, or the belief has been dropped.
     * {@link #lastLegend()} returns this until the first successful send.
     */
    public static final int LEGEND_NONE = 0;

    /**
     * Duration of the rumble step, in milliseconds. Not a caller's choice, and
     * deliberately not exposed.
     *
     * <p>It has no effect the board can see: the packet is read the moment it
     * arrives, and the duration only decides when the Driver Station sends the
     * zero that ends the step — which is code 0, which is never an action
     * (§8.8). Every legal value looks identical from the tile.
     *
     * <p>What it can do is break the feature, which is why it is a constant
     * rather than an argument. The SDK's outgoing rumble is a one-element
     * <em>evicting</em> queue drained about every 50 ms. Make the step much
     * shorter than that and the Robot Controller's own trailing zero is queued
     * inside the same window, evicting the legend change before it is ever
     * transmitted — the call returns, nothing reaches the wire, and nothing
     * reports an error. 100 ms clears the drain interval with margin, and is
     * short enough not to buzz a gamepad that does have motors or to leave
     * {@code isRumbling()} lying for long.
     *
     * <p>Reasoned from the SDK's source, not measured. See the README.
     */
    private static final int STEP_MS = 100;

    private final RumbleSink sink;

    /** Last code actually handed to the sink, or {@link #LEGEND_NONE}. */
    private int lastCode = LEGEND_NONE;

    /**
     * @param sink where rumble packets go — {@code GamepadRumbleSink} in an
     *             OpMode, a recording fake in a test.
     */
    public ButtonBoard(RumbleSink sink) {
        if (sink == null) {
            throw new IllegalArgumentException("sink is null");
        }
        this.sink = sink;
    }

    /**
     * Show the legend bound to {@code code}, unless it is already the last one
     * sent.
     *
     * @param code 1–15, a legend code from the board's map — not a key number.
     * @return true if a rumble packet was sent, false if it was suppressed as a
     *         repeat. Almost every caller can ignore this; it is here so a loop
     *         can count or log the sends it actually made.
     * @throws IllegalArgumentException if {@code code} is outside 1–15. Zero is
     *         rejected with the rest: it is the SDK's stop, never an action.
     */
    public boolean showLegend(int code) {
        checkCode(code);
        if (code == lastCode) {
            return false;
        }
        send(code);
        return true;
    }

    /**
     * Send the last code again, whether or not it has changed.
     *
     * <p>For the case the change detection cannot see: the board was
     * power-cycled or replugged mid-match, so it is showing whatever its map
     * says it starts with and this object's belief is a legend older than the
     * hardware. Nothing on the wire tells us that — an {@code xinput} board has
     * no way to talk back — so the caller has to know.
     *
     * @return true if something was sent; false if no legend has been shown
     *         yet, in which case there is nothing to resend.
     */
    public boolean refresh() {
        if (lastCode == LEGEND_NONE) {
            return false;
        }
        send(lastCode);
        return true;
    }

    /**
     * Forget which legend was last sent, so the next {@link #showLegend(int)}
     * sends whatever it is given even if it repeats.
     *
     * <p>Sends nothing. Use it when the next code is not known yet but the
     * belief is already known to be wrong.
     */
    public void forget() {
        lastCode = LEGEND_NONE;
    }

    /**
     * The last code sent, or {@link #LEGEND_NONE} if none has been or the
     * belief was dropped. This is what was <i>sent</i>, not what the board is
     * known to be showing — see {@link #refresh()}.
     */
    public int lastLegend() {
        return lastCode;
    }

    /**
     * Stop any rumble the gamepad is running, by sending zero power.
     *
     * <p><b>This does not clear the key.</b> Zero is the SDK's stop and the
     * board ignores it by design, so the legend stays exactly as it was. To
     * blank a key, bind a code to a blank picture and show that code. The
     * method exists because a real gamepad sharing the OpMode may be buzzing,
     * and because a caller who goes looking for "stop" should find this
     * paragraph rather than invent {@code showLegend(0)}.
     *
     * <p>Leaves the remembered legend alone: nothing on the panel changed.
     */
    public void stopRumble() {
        sink.rumble(0.0, 0.0, STEP_MS);
    }

    /**
     * The double to pass to {@code Gamepad.rumble()} for a legend code.
     *
     * <p>{@code c / 15.0} is exact in binary floating point for every code in
     * range in the sense that matters: the SDK computes
     * {@code Math.round(value * 255)} and that is exactly {@code c * 17} for
     * all of 1–15, with no code near a boundary. Public so a team can check the
     * arithmetic themselves, and so the test can.
     *
     * @throws IllegalArgumentException if {@code code} is outside 1–15.
     */
    public static double rumbleValueFor(int code) {
        checkCode(code);
        return (double) code / (double) LEGEND_CODE_MAX;
    }

    /**
     * The byte the board will see for a legend code, {@code code * 17}. The
     * firmware's {@code RUMBLE_CODE()} maps it back to {@code code}.
     *
     * @throws IllegalArgumentException if {@code code} is outside 1–15.
     */
    public static int wireByteFor(int code) {
        checkCode(code);
        return code * LEGEND_CODE_STEP;
    }

    private void send(int code) {
        sink.rumble(rumbleValueFor(code), 0.0, STEP_MS);
        lastCode = code;
    }

    private static void checkCode(int code) {
        if (code < 1 || code > LEGEND_CODE_MAX) {
            throw new IllegalArgumentException(
                    "legend code must be 1.." + LEGEND_CODE_MAX + ", not " + code
                            + (code == 0 ? " (0 is the SDK's stop, never an action)" : ""));
        }
    }
}

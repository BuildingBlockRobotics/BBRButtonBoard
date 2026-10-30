"""Rumble as a control channel (PROTOCOL.md §8.8).

An `xinput` board is single-interface and has no config channel, so during a
match nothing can reach it -- except the XInput OUT endpoint, where the FTC
SDK's `gamepad.rumble(left, right, ms)` lands. That door is far too narrow for
text. It carries a number, and a stored picture (§14.9) is switched by a
number, so the two halves fit:

- **Configuration time**, over the config channel: load the legends into the
  tile's slots and bind rumble codes to them. That is what this module does.
- **Match time**, over rumble: `showLegend(3)` from the OpMode (`robot/ftc`)
  changes the key's legend, in full colour, in one frame.

A binding lives in the map (§9.5) so it is saved with the layout:

    "rumble":[{"code":1,"addr":5,"slot":0},{"code":2,"addr":5,"slot":3}]

Since rev 0.33 a code can also set a button's LED colour. An entry that names
a `btn` and an `rgb` instead of an `addr` and a `slot` is an LED action, and
one code may carry a legend and any number of them:

    "rumble":[{"code":3,"addr":5,"slot":2},
              {"code":3,"btn":5,"rgb":"FF0000","fx":"blink"},
              {"code":4,"btn":5,"rgb":"map"}]

`rgb` is 6 hex digits, `off`, or `map` (drop the override, back to the
button's `led` default). On the board it is `led <btn> <rgb> [fx]` on the live
layer (§13.5): not saved, cleared by `led reset`.

Since rev 0.38 an LED action may name one LED by tile and index instead of a
button, `{"code":3,"addr":4,"led":2,"rgb":"00FF00"}`, so a key bound to no
button still works as an indicator. On the board that is `led tile`.

The functions here are pure: they take a map object and hand back a new one,
so nothing is on the wire until the caller `map set`s it. `Board.rumble_bind`
does both. Validation mirrors the firmware's exactly, which is the point of
having it here at all: a mistake is a ValueError naming the rule rather than
an `{"ok":false,"err":"rumble"}` round trip that names only the section.
"""
from __future__ import annotations

from . import leds

# Mirrors of shared/protocol.h. The header is the source of truth and
# `test_bb.py` reads RUMBLE_CODE_MAX and RUMBLE_CODE_STEP out of it and
# asserts these two match -- the same guard `robot/ftc`'s test puts on the
# Java mirror, for the same reason: three copies of an encoding drift.
CODE_MAX = 15                      # RUMBLE_CODE_MAX
CODE_STEP = 17                     # RUMBLE_CODE_STEP, 255 / CODE_MAX exactly
CODE_MIN = 1                       # 0 is `stop`, never a code -- see below

ADDR_MIN, ADDR_MAX = 1, 31         # §9.5's addr rule
SLOT_MIN, SLOT_MAX = 0, 254        # §9.5's slot rule
BTN_MIN, BTN_MAX = 1, 32           # §9.5's btn rule, for LED actions
LED_MAX = 32                       # MAP_RUMBLE_LED_MAX in firmware/core/map.h
LED_INDEX_MAX = 31                 # LED_MAX_COUNT - 1, for a tile LED action

MAP = "map"                        # an LED action's "back to the map default"
OFF = leds.OFF
FX = leds.FX
_LEGEND_KEYS = ("code", "addr", "slot")
_LED_KEYS = ("code", "btn", "rgb", "fx")
_TILE_LED_KEYS = ("code", "addr", "led", "rgb", "fx")
_TEXT_KEYS = ("code", "addr", "rows", "attrs")
TEXT_MAX = 32                      # MAP_RUMBLE_TEXT_MAX in firmware/core/map.h
ROWS_MAX = 8                       # DISP_MAX_ROWS
COLS_MAX = 32                      # DISP_MAX_COLS

ERR = "rumble"                     # the firmware's error code for all of it


def check_code(code):
    """A legend code, 1-15. Returns it as an int.

    Code 0 is refused rather than passed on: `rumble(0, 0, ms)` is how the SDK
    *stops* a rumble, and stopping a rumble must not change what is on a
    screen (§8.8). It is the one wrong number a caller is likely to reach for.
    """
    n = _as_int("code", code)
    if n == 0:
        raise ValueError(
            "code 0 is not a binding: rumble(0, 0, ms) is how the SDK stops a "
            "rumble, and a stop must not change what is on a screen (§8.8)")
    if not CODE_MIN <= n <= CODE_MAX:
        raise ValueError("code must be %d-%d, not %r (§8.8)"
                         % (CODE_MIN, CODE_MAX, code))
    return n


def check_addr(addr):
    n = _as_int("addr", addr)
    if not ADDR_MIN <= n <= ADDR_MAX:
        raise ValueError("addr must be %d-%d, not %r (§9.5)"
                         % (ADDR_MIN, ADDR_MAX, addr))
    return n


def check_slot(slot):
    n = _as_int("slot", slot)
    if not SLOT_MIN <= n <= SLOT_MAX:
        raise ValueError("slot must be %d-%d, not %r (§9.5)"
                         % (SLOT_MIN, SLOT_MAX, slot))
    return n


def check_btn(btn):
    n = _as_int("btn", btn)
    if not BTN_MIN <= n <= BTN_MAX:
        raise ValueError("btn must be %d-%d, not %r (§9.5)"
                         % (BTN_MIN, BTN_MAX, btn))
    return n


def check_led(led):
    n = _as_int("led", led)
    if not 0 <= n <= LED_INDEX_MAX:
        raise ValueError("led must be 0-%d, not %r (§9.5)" % (LED_INDEX_MAX, led))
    return n


def check_rgb(value):
    """An LED action's colour, as the board writes it back.

    Accepts what `leds.rgb` does -- names, "#RRGGBB", triples -- plus `map`.
    Black comes back as `off` and hex as upper case, which is the firmware's
    canonical form, so a `map get` after a `map set` reads the same.
    """
    if isinstance(value, str) and value.strip().lower() == MAP:
        return MAP
    out = leds.rgb(value)
    return OFF if out in (OFF, "000000") else out


def _as_int(name, value):
    # bool is an int in Python and True would pass every range check below.
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            return int(str(value), 10)
        except (TypeError, ValueError):
            raise ValueError("%s must be a whole number, not %r" % (name, value))
    return value


def entry(code, addr, slot):
    """One validated binding, in the field order §8.8 writes it in."""
    return {"code": check_code(code), "addr": check_addr(addr),
            "slot": check_slot(slot)}


def led_entry(code, btn, rgb, fx=None):
    """One validated LED action, in the firmware's canonical form.

    `fx` is left out when it is `solid`, and always for `map`, which puts the
    button back on its default and has no effect of its own to keep.
    """
    one = {"code": check_code(code), "btn": check_btn(btn), "rgb": check_rgb(rgb)}
    return _with_fx(one, fx)


def tile_led_entry(code, addr, led, rgb, fx=None):
    """One validated LED action on a tile LED (rev 0.38), canonical form.

    For a key bound to no button: `led` is the key's index on the tile, which
    is also its LED's.
    """
    one = {"code": check_code(code), "addr": check_addr(addr), "led": check_led(led),
           "rgb": check_rgb(rgb)}
    return _with_fx(one, fx)


def _with_fx(one, fx):
    effect = leds.fx(fx)
    if effect and effect != "solid" and one["rgb"] != MAP:
        one["fx"] = effect
    return one


def is_led(item):
    """True for an LED action, false for a legend binding. The firmware tells
    them apart the same way: a `btn`, a `led` or an `rgb` makes it one."""
    return isinstance(item, dict) and ("btn" in item or "led" in item or "rgb" in item)


def target(action):
    """What an LED action recolours: ("btn", n) or ("tile", addr, led)."""
    if "btn" in action:
        return ("btn", action["btn"])
    return ("tile", action["addr"], action["led"])


def is_text(item):
    """True for a message (rev 0.36): `rows` or `attrs`, and not an LED action."""
    return isinstance(item, dict) and not is_led(item) and ("rows" in item or "attrs" in item)


def text_entry(code, addr, rows, attrs=None):
    """One validated message, in the firmware's canonical form.

    `rows` is a list of strings, row 0 first, printable ASCII, at most 32
    characters each and 8 rows; the panel clips to its own size. `attrs`, if
    given, is one ATTR byte per row -- a name like "red on white" works too,
    through `display.attr`. It is left out when every row is plain.
    """
    from . import display
    code, addr = check_code(code), check_addr(addr)
    if isinstance(rows, str) or not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= ROWS_MAX:
        raise ValueError("a message is a list of 1 to %d rows, not %r" % (ROWS_MAX, rows))
    out = []
    for row in rows:
        if not isinstance(row, str) or len(row) > COLS_MAX or \
                any(not " " <= c <= "~" for c in row):
            raise ValueError("a message row is printable ASCII, at most %d "
                             "characters, not %r" % (COLS_MAX, row))
        out.append(row)
    one = {"code": code, "addr": addr, "rows": out}
    if attrs is not None:
        if isinstance(attrs, (str, int)) or len(attrs) != len(out):
            raise ValueError("attrs is one ATTR per row: %d of them, not %r" % (len(out), attrs))
        values = [display.attr(a) for a in attrs]
        if any(values):
            one["attrs"] = values
    return one


def _strict_rgb(item):
    # The board takes 6 hex digits, `off` or `map` and nothing friendlier: a
    # name like "red" is the client's convenience, not the map's syntax.
    value = item.get("rgb")
    if not isinstance(value, str) or not (
            value in (OFF, MAP) or (len(value) == 6 and
                                    all(c in "0123456789abcdefABCDEF" for c in value))):
        raise ValueError("an LED action's rgb is 6 hex digits, off or map, "
                         "not %r (§8.8)" % (value,))
    return value


def validate(entries):
    """Check a whole `rumble` section and return it normalised.

    Every rule the firmware applies before answering `err rumble`: the shape,
    the ranges, at most one legend per code -- a code names exactly one
    legend, so a second entry for it is a configuration mistake rather than a
    second action -- and at most one LED action per (code, btn), or per
    (code, addr, led) for one on a tile LED.

    The result is in the board's canonical order: legend entries first, then
    LED actions, each in the order given. That is what `map get` returns.
    """
    if entries is None:
        return []
    if isinstance(entries, dict) or not isinstance(entries, (list, tuple)):
        raise ValueError("the map's `rumble` section is a list of "
                         "{code,addr,slot} or {code,btn,rgb} objects (§9.5), "
                         "not %r" % (entries,))
    legends, texts, actions, seen, seen_led, seen_text = [], [], [], set(), set(), set()
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("a rumble binding is an object with code, addr "
                             "and slot, not %r" % (item,))
        if is_text(item):
            missing = [k for k in ("code", "addr", "rows") if k not in item]
            if missing:
                raise ValueError("rumble message %r is missing %s" % (item, ", ".join(missing)))
            extra = [k for k in item if k not in _TEXT_KEYS]
            if extra:
                raise ValueError("a message carries code, addr, rows and attrs "
                                 "only; %r also has %s" % (item, ", ".join(sorted(extra))))
            attrs = item.get("attrs")
            if attrs is not None and (not isinstance(attrs, list) or
                                      any(not isinstance(a, int) or isinstance(a, bool) or not 0 <= a <= 255
                                          for a in attrs)):
                raise ValueError("a message's attrs are ATTR bytes 0..255, not %r" % (attrs,))
            one = text_entry(item["code"], item["addr"], item["rows"], attrs)
            key = (one["code"], one["addr"])
            if key in seen_text:
                raise ValueError("code %d writes tile %d twice; the map holds at "
                                 "most one message per (code, tile) (§9.5)" % key)
            seen_text.add(key)
            if len(texts) >= TEXT_MAX:
                raise ValueError("at most %d messages fit the map (MAP_RUMBLE_TEXT_MAX)" % TEXT_MAX)
            texts.append(one)
            continue
        if is_led(item):
            tile = "led" in item and "btn" not in item
            if "slot" in item or "rows" in item or "attrs" in item or ("addr" in item and not tile):
                raise ValueError("%r mixes an LED action (btn or addr+led, rgb) with "
                                 "a legend (addr, slot) or a message; an entry is one "
                                 "kind only (§8.8)" % (item,))
            need, allowed = (("code", "addr", "led", "rgb"), _TILE_LED_KEYS) if tile \
                else (("code", "btn", "rgb"), _LED_KEYS)
            missing = [k for k in need if k not in item]
            if missing:
                raise ValueError("rumble LED action %r is missing %s"
                                 % (item, ", ".join(missing)))
            extra = [k for k in item if k not in allowed]
            if extra:
                raise ValueError("an LED action carries %s only; %r also has %s"
                                 % (", ".join(allowed), item, ", ".join(sorted(extra))))
            if "fx" in item and item["fx"] not in FX:       # case matters on the board
                raise ValueError("fx must be one of %s, not %r"
                                 % (", ".join(FX), item["fx"]))
            if tile:
                one = tile_led_entry(item["code"], item["addr"], item["led"],
                                     _strict_rgb(item), item.get("fx"))
            else:
                one = led_entry(item["code"], item["btn"], _strict_rgb(item), item.get("fx"))
            key = (one["code"],) + target(one)
            if key in seen_led:
                raise ValueError("code %d sets %s twice; the map holds at most one LED "
                                 "action per (code, button) or (code, tile LED) (§9.5)"
                                 % (one["code"], _describe(one)))
            seen_led.add(key)
            if len(actions) >= LED_MAX:
                raise ValueError("at most %d LED actions fit the map "
                                 "(MAP_RUMBLE_LED_MAX)" % LED_MAX)
            actions.append(one)
            continue
        missing = [k for k in _LEGEND_KEYS if k not in item]
        if missing:
            raise ValueError("rumble binding %r is missing %s"
                             % (item, ", ".join(missing)))
        extra = [k for k in item if k not in _LEGEND_KEYS]
        if extra:
            raise ValueError("a rumble binding carries code, addr and slot "
                             "only; %r also has %s" % (item, ", ".join(sorted(extra))))
        one = entry(item["code"], item["addr"], item["slot"])
        if one["code"] in seen:
            raise ValueError("code %d is bound twice; a code names one legend, "
                             "so the map holds at most one entry per code (§9.5)"
                             % (one["code"],))
        seen.add(one["code"])
        legends.append(one)
    for one in texts:
        if any(e["code"] == one["code"] and e["addr"] == one["addr"] for e in legends):
            raise ValueError("code %d shows a picture on tile %d and writes a message "
                             "to it; the two would race for one panel (§8.8)"
                             % (one["code"], one["addr"]))
    return legends + texts + actions


def bindings(mapping):
    """The validated legend bindings of a map, sorted by code.

    Sorted because the order carries no meaning on the wire and a caller
    listing bindings wants them by code; `bind` keeps the same order so a
    `map get` after a `map set` reads the same way. LED actions are not
    here: they are `led_actions`.
    """
    section = (mapping or {}).get("rumble")
    return sorted([e for e in validate(section) if not is_led(e) and not is_text(e)],
                  key=lambda e: e["code"])


def led_actions(mapping):
    """The validated LED actions of a map, in the order given."""
    return [e for e in validate((mapping or {}).get("rumble")) if is_led(e)]


def texts(mapping):
    """The validated messages of a map, in the order given."""
    return [e for e in validate((mapping or {}).get("rumble")) if is_text(e)]


def texts_for(mapping, code):
    """The messages one code writes, one per tile."""
    code = check_code(code)
    return [e for e in texts(mapping) if e["code"] == code]


def bind_text(mapping, code, addr, rows, attrs=None):
    """A copy of `mapping` where `code` writes `rows` to tile `addr`.

    Replaces the message already on (code, addr), and a picture legend the
    code had on the same tile, rather than leave a map the board refuses.
    """
    one = text_entry(code, addr, rows, attrs)
    kept = [e for e in texts(mapping) if (e["code"], e["addr"]) != (one["code"], one["addr"])]
    if len(kept) >= TEXT_MAX:
        raise ValueError("at most %d messages fit the map (MAP_RUMBLE_TEXT_MAX)" % TEXT_MAX)
    legends = [e for e in bindings(mapping)
               if (e["code"], e["addr"]) != (one["code"], one["addr"])]
    return _with(mapping, legends, led_actions(mapping),
                 sorted(kept + [one], key=lambda e: (e["code"], e["addr"])))


def unbind_text(mapping, code, addr=None):
    """A copy of `mapping` without code's message on `addr`, or all of code's
    messages when `addr` is None. Removing what is not there is not an error."""
    code = check_code(code)
    addr = None if addr is None else check_addr(addr)
    kept = [e for e in texts(mapping) if not (e["code"] == code and (addr is None or e["addr"] == addr))]
    return _with(mapping, bindings(mapping), led_actions(mapping), kept)


def leds_for(mapping, code):
    """The LED actions one code carries, in the order the board applies them."""
    code = check_code(code)
    return [e for e in led_actions(mapping) if e["code"] == code]


def _describe(action):
    if "btn" in action:
        return "button %d" % action["btn"]
    return "tile %d LED %d" % (action["addr"], action["led"])


def bind_led(mapping, code, btn, rgb, fx=None):
    """A copy of `mapping` where `code` sets button `btn` to `rgb`.

    Replaces an action already on (code, btn), in place, rather than adding a
    second one the board would refuse. `rgb` may be `map`.
    """
    return _bind_action(mapping, led_entry(code, btn, rgb, fx))


def bind_tile_led(mapping, code, addr, led, rgb, fx=None):
    """A copy of `mapping` where `code` sets tile `addr`'s LED `led` to
    `rgb` (rev 0.38): the robot colour for a key bound to no button."""
    return _bind_action(mapping, tile_led_entry(code, addr, led, rgb, fx))


def _bind_action(mapping, one):
    actions, replaced = [], False
    for e in led_actions(mapping):
        if (e["code"],) + target(e) == (one["code"],) + target(one):
            actions.append(one)
            replaced = True
        else:
            actions.append(e)
    if not replaced:
        if len(actions) >= LED_MAX:
            raise ValueError("at most %d LED actions fit the map "
                             "(MAP_RUMBLE_LED_MAX)" % LED_MAX)
        actions.append(one)
    return _with(mapping, bindings(mapping), actions)


def unbind_led(mapping, code, btn=None):
    """A copy of `mapping` without code's action on `btn`, or all of code's
    LED actions when `btn` is None. Removing what is not there is not an
    error, for the same reason as `unbind`."""
    code = check_code(code)
    btn = None if btn is None else check_btn(btn)
    actions = [e for e in led_actions(mapping)
               if not (e["code"] == code and (btn is None or e.get("btn") == btn))]
    return _with(mapping, bindings(mapping), actions)


def unbind_tile_led(mapping, code, addr, led):
    """A copy of `mapping` without code's action on tile `addr`'s LED `led`."""
    key = (check_code(code), "tile", check_addr(addr), check_led(led))
    actions = [e for e in led_actions(mapping) if (e["code"],) + target(e) != key]
    return _with(mapping, bindings(mapping), actions)


def find(mapping, code):
    """The binding for `code`, or None."""
    code = check_code(code)
    for one in bindings(mapping):
        if one["code"] == code:
            return one
    return None


def bind(mapping, code, addr, slot):
    """A copy of `mapping` with `code` bound to (`addr`, `slot`).

    Rebinding a code replaces its entry rather than adding a second one --
    one entry per code is the rule, so the alternative is a map the board
    refuses. Nothing is sent: `map set` the result, then `save`, or use
    `Board.rumble_bind`.
    """
    one = entry(code, addr, slot)
    kept = [e for e in bindings(mapping) if e["code"] != one["code"]]
    msgs = [e for e in texts(mapping) if (e["code"], e["addr"]) != (one["code"], one["addr"])]
    return _with(mapping, sorted(kept + [one], key=lambda e: e["code"]),
                 led_actions(mapping), msgs)


def unbind(mapping, code):
    """A copy of `mapping` with `code` unbound. Unbinding what was never
    bound is not an error: the map already says what the caller asked for,
    and a code with no entry does nothing (§8.8)."""
    code = check_code(code)
    return _with(mapping, [e for e in bindings(mapping) if e["code"] != code],
                 led_actions(mapping))


def _with(mapping, legends, actions=(), messages=None):
    """`mapping` with its `rumble` section replaced, dropped when empty.

    Legends, then messages, then LED actions: the board's canonical order.
    `messages` None keeps the map's own. Dropped rather than left as `[]`
    because the map is one 4064-byte flash record (§9.1) and an empty section
    is bytes spent saying nothing.
    """
    if messages is None:
        messages = texts(mapping)
    out = dict(mapping or {})
    entries = list(legends) + list(messages) + list(actions)
    if entries:
        out["rumble"] = entries
    else:
        out.pop("rumble", None)
    return out


# ---- the encoding, §8.8 ------------------------------------------------------
# 255 / 15 is 17 exactly, so every code round-trips and sits in the middle of a
# +-8 band. Taking the top four bits instead would put half the codes on a
# bucket boundary, where the SDK's own double-to-byte rounding decides them.

def wire_byte(code):
    """The byte a code arrives as in the XInput OUT packet: `code * 17`."""
    return check_code(code) * CODE_STEP


def rumble_value(code):
    """The double an OpMode passes to `gamepad.rumble(left, ...)` for a code.

    Quoted from `robot/ftc`, which is the API to tell a user about:
    `board.showLegend(code)` sends exactly this. Here for checking the
    arithmetic and for driving a mock, not as a second way to say it.
    """
    return check_code(code) / float(CODE_MAX)


def code_of(value):
    """Recover a code from the left motor byte: `RUMBLE_CODE()` of protocol.h.

    `(v + 8) / 17`. Returns 0 for a stop and for anything below half a step,
    which is not a code -- callers filter on it rather than acting on it.
    """
    v = _as_int("value", value)
    if not 0 <= v <= 255:
        raise ValueError("a motor value is a byte 0-255, not %r" % (value,))
    return (v + CODE_STEP // 2) // CODE_STEP

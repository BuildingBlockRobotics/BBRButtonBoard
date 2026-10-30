"""Exceptions for the Button Board client (PROTOCOL.md §9)."""


class BoardError(Exception):
    """Base for everything this package raises."""


class NoBoardFound(BoardError):
    """Port auto-detection found no board, or could not choose between ports."""


class NotConnected(BoardError):
    """The transport is closed."""


class Timeout(BoardError):
    """No terminal {"ok":...} line arrived in time (§9.1)."""

    def __init__(self, command, seconds):
        super().__init__("no reply to %r within %.1fs" % (command, seconds))
        self.command = command
        self.seconds = seconds


class CommandError(BoardError):
    """The board answered {"ok":false,"err":...}.

    `err` is the protocol error code, `response` the whole object: several
    errors carry context, e.g. {"ok":false,"err":"dup_btn","btn":7} from
    `map set` (§9.5), or "expected"/"actual" from a failed VERIFY (§9.4).
    """

    def __init__(self, command, response):
        self.command = command
        self.response = response
        self.err = response.get("err", "error")
        detail = {k: v for k, v in response.items() if k not in ("ok", "err", "events")}
        super().__init__("%s: %s%s" % (command, self.err, (" %r" % detail) if detail else ""))

"""Errors shared by the reply service and the reply workflow."""


class ReplyError(RuntimeError):
    """A reply cannot be drafted, edited or approved as asked (shown to the user at H7)."""

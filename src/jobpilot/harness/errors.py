from __future__ import annotations

from jobpilot.core.status import Checkpoint


class HarnessError(RuntimeError):
    pass


class ToolNotAllowed(HarnessError):
    pass


class UnknownTool(HarnessError):
    pass


class ToolInputInvalid(HarnessError):
    pass


class ToolOutputInvalid(HarnessError):
    pass


class ToolTimeout(HarnessError):
    pass


class ToolFailed(HarnessError):
    pass


class RetryableToolError(HarnessError):
    """Raise inside a tool for transient failures (network blip, slow page) to get a retry."""


class BudgetExceeded(HarnessError):
    pass


class CircuitOpen(HarnessError):
    pass


class GuardrailViolation(HarnessError):
    pass


class ApprovalRequired(HarnessError):
    """The tool has an external side effect and no valid approval was supplied.
    The supervisor turns this into a LangGraph interrupt at `checkpoint`."""

    def __init__(self, checkpoint: Checkpoint, payload_sha256: str, reason: str) -> None:
        super().__init__(f"{checkpoint.value} approval required: {reason}")
        self.checkpoint = checkpoint
        self.payload_sha256 = payload_sha256


class OutcomeUnknown(HarnessError):
    """An external action was attempted before and its result was never confirmed."""

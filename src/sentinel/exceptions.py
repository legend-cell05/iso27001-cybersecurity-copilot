"""Exception taxonomy.

The distinction that matters in a compliance tool is between *the tool is
broken* and *the organisation is not ready*. They look similar from the
outside -- both stop the run -- and conflating them is how a readiness gate
ends up being ignored: if a refusal and a crash produce the same output, a
scheduler treats both as noise and someone switches the gate off.

So a blocked gate is its own exception, carrying the findings that blocked it,
and the CLI turns it into exit code 3 rather than 1.
"""

from __future__ import annotations


class SentinelError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigurationError(SentinelError):
    """Settings are missing or contradictory."""


class DatabaseError(SentinelError):
    """The database refused an operation."""


class CatalogueError(SentinelError):
    """The control catalogue is invalid, missing or internally inconsistent.

    Raised at load time, on purpose: a catalogue that names a theme nobody
    defined should fail before an assessment quotes it in a Statement of
    Applicability, not halfway through generating one.
    """

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message if path is None else f"{path}: {message}")
        self.path = path


class RiskModelError(SentinelError):
    """A risk, an asset or a treatment decision is not usable.

    A risk scored outside the 1-5 scales, a residual score above its own
    inherent score, a treatment that names a control the catalogue does not
    contain: all of them mean the register is saying something arithmetic
    cannot support.
    """


class EvidenceError(SentinelError):
    """A piece of evidence cannot be recorded or verified."""


class AssessmentNotFound(SentinelError):
    """No assessment with that identifier exists."""


class ReadinessBlocked(SentinelError):
    """The audit-readiness gate refused.

    Not a failure of the tool. The organisation is not ready, the findings say
    why, and the caller gets exit code 3 so a pipeline can tell the difference
    between "not ready" and "crashed".
    """

    def __init__(self, message: str, *, failed: list[str] | None = None) -> None:
        super().__init__(message)
        self.failed = failed or []

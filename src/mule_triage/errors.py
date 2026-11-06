class MuleTriageError(Exception):
    """The base of the tool's own exceptions."""


class ReconciliationError(MuleTriageError):
    """A run's stored rows don't account for every line it read."""

class MuleTriageError(Exception):
    """The base of the tool's own exceptions."""


class ReconciliationError(MuleTriageError):
    """A run's stored rows don't account for every line it read."""


class RulesError(MuleTriageError):
    """The rules file can't be read, or holds a missing, invalid or unknown setting."""

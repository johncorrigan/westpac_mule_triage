class MuleTriageError(Exception):
    """The base of the tool's own exceptions."""


class ReconciliationError(MuleTriageError):
    """A run's stored rows don't account for every line it read."""


class StoreError(MuleTriageError):
    """The database was made with a different schema, so it can't be used as it is."""


class RulesError(MuleTriageError):
    """The rules file can't be read, or holds a missing, invalid or unknown setting."""


class SettingsError(MuleTriageError):
    """An AI setting, such as the mode, model, endpoint or timeout, is invalid."""


class RecordingMissing(MuleTriageError):
    """No recorded reply is filed under a recording key."""

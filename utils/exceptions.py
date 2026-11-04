class NoNewDataError(Exception):
    """Raised when the incremental window has no new data to validate."""

class NoNewSyncError(Exception):
    """Raised when the Sync date from API and Sync date from Control table is the same"""
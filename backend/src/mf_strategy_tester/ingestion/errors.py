class SourceIngestionError(RuntimeError):
    """Base error for a source retrieval or validation failure."""


class SourceDownloadError(SourceIngestionError):
    pass


class SourceParseError(SourceIngestionError):
    pass

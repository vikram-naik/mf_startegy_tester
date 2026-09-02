class SourceIngestionError(RuntimeError):
    """Base error for a source retrieval or validation failure."""


class SourceDownloadError(SourceIngestionError):
    pass


class SourceNotPublishedError(SourceIngestionError):
    """The official endpoint returned its known missing-artifact response."""


class SourceTemporarilyUnavailableError(SourceIngestionError):
    """The source returned a recognized transient failure payload."""


class SourceParseError(SourceIngestionError):
    pass

from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPResponse
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from mf_strategy_tester.ingestion.errors import SourceDownloadError

ALLOWED_OFFICIAL_HOSTS = frozenset(
    {"www.amfiindia.com", "portal.amfiindia.com", "files.hdfcfund.com"}
)


@dataclass(frozen=True)
class DownloadedSource:
    content: bytes
    media_type: str
    status_code: int
    final_url: str


class SourceDownloader:
    """Bounded HTTPS downloader restricted to explicitly approved official hosts."""

    def __init__(
        self,
        *,
        timeout_seconds: int,
        max_bytes: int,
        retry_attempts: int = 4,
        retry_backoff_seconds: float = 1.0,
        sleep_function: Callable[[float], None] = sleep,
    ) -> None:
        if retry_attempts < 1:
            raise ValueError("retry_attempts must be at least one")
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep_function

    def download(self, url: str) -> DownloadedSource:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_OFFICIAL_HOSTS:
            raise SourceDownloadError("source URL must use HTTPS on an approved official host")

        is_hdfc_file = parsed.hostname == "files.hdfcfund.com"
        request = Request(
            url,
            headers={
                "Accept": (
                    "application/pdf, */*;q=0.1"
                    if is_hdfc_file
                    else "text/plain, application/json;q=0.9, */*;q=0.1"
                ),
                "User-Agent": (
                    "Mozilla/5.0" if is_hdfc_file else "mf-fund-screener/0.1 (+local-research)"
                ),
                **({"Referer": "https://www.hdfcfund.com/"} if is_hdfc_file else {}),
            },
        )
        for attempt in range(1, self._retry_attempts + 1):
            try:
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    return self._read_response(response)
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == self._retry_attempts:
                    raise SourceDownloadError(
                        f"official source returned HTTP {error.code} for {url} "
                        f"after {attempt} attempt(s)"
                    ) from error
            except (TimeoutError, URLError) as error:
                if attempt == self._retry_attempts:
                    raise SourceDownloadError(
                        f"failed to retrieve official source {url} "
                        f"after {attempt} attempt(s): {error}"
                    ) from error
            self._sleep(self._retry_backoff_seconds * (2 ** (attempt - 1)))
        raise AssertionError("download retry loop exited unexpectedly")

    def _read_response(self, response: HTTPResponse) -> DownloadedSource:
        declared_size = response.headers.get("Content-Length")
        if declared_size is not None:
            try:
                parsed_size = int(declared_size)
            except ValueError as error:
                raise SourceDownloadError("source returned an invalid Content-Length") from error
            if parsed_size > self._max_bytes:
                raise SourceDownloadError(
                    f"source declares {declared_size} bytes, exceeding limit {self._max_bytes}"
                )
        final_url = response.url
        parsed_final_url = urlparse(final_url)
        if (
            parsed_final_url.scheme != "https"
            or parsed_final_url.hostname not in ALLOWED_OFFICIAL_HOSTS
        ):
            raise SourceDownloadError("official source redirected to an unapproved host")
        content = response.read(self._max_bytes + 1)
        if len(content) > self._max_bytes:
            raise SourceDownloadError(f"source exceeds download limit of {self._max_bytes} bytes")
        media_type = response.headers.get_content_type()
        return DownloadedSource(
            content=content,
            media_type=media_type,
            status_code=response.status,
            final_url=final_url,
        )

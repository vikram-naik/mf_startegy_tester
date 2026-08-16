from email.message import Message
from types import TracebackType
from urllib.error import HTTPError

import mf_strategy_tester.ingestion.http as http_module
from mf_strategy_tester.ingestion.http import SourceDownloader


class FakeResponse:
    url = "https://portal.amfiindia.com/spages/NAVAll.txt"
    status = 200

    def __init__(self) -> None:
        self.headers = Message()
        self.headers["Content-Type"] = "text/plain"

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback

    def read(self, _limit: int) -> bytes:
        return b"official"


def test_transient_amfi_error_is_retried(monkeypatch: object) -> None:
    attempts = 0
    sleeps: list[float] = []

    def fake_urlopen(_request: object, *, timeout: int) -> FakeResponse:
        nonlocal attempts
        del timeout
        attempts += 1
        if attempts == 1:
            raise HTTPError(FakeResponse.url, 503, "unavailable", {}, None)
        return FakeResponse()

    monkeypatch.setattr(http_module, "urlopen", fake_urlopen)  # type: ignore[attr-defined]
    downloader = SourceDownloader(
        timeout_seconds=10,
        max_bytes=1024,
        retry_attempts=2,
        retry_backoff_seconds=1,
        sleep_function=sleeps.append,
    )

    result = downloader.download(FakeResponse.url)

    assert result.content == b"official"
    assert attempts == 2
    assert sleeps == [1]

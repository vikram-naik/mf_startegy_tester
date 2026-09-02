#!/usr/bin/env python3
"""Capture AdvisorKhoj distribution histories as immutable JSONL.

Catalog mode captures every exact scheme name discovered by the separate public-catalog snapshot.
Legacy manifest mode remains available for bounded investigations. Neither mode treats a source
display name as an AMFI identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mf_strategy_tester.ingestion.advisorkhoj import (
    ADVISORKHOJ_HOST,
    API_CAPTURE_SCHEMA_VERSION,
    CAPTURE_SCHEMA_VERSION,
    HISTORICAL_DISTRIBUTIONS_PATH,
    SCHEME_DETAILS_PATH,
    AdvisorkhojCaptureParser,
    parse_catalog,
    parse_distribution_details_json,
    parse_historical_distributions_html,
)
from mf_strategy_tester.ingestion.errors import SourceParseError

BASE_URL = f"https://{ADVISORKHOJ_HOST}{HISTORICAL_DISTRIBUTIONS_PATH}"
DETAILS_URL = f"https://{ADVISORKHOJ_HOST}{SCHEME_DETAILS_PATH}"
USER_AGENT = "mf-strategy-tester/0.1 (+local-personal-research)"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class _DownloadResult:
    requested_url: str
    source_url: str
    source_payload: str | None
    error: Exception | None


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.delay_seconds < 0:
        raise SystemExit("--delay-seconds must be non-negative")
    if arguments.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    if arguments.retry_attempts < 1:
        raise SystemExit("--retry-attempts must be at least one")
    if arguments.workers < 1 or arguments.workers > 8:
        raise SystemExit("--workers must be between one and eight")
    targets, catalog_sha256 = _read_targets(arguments)
    total_targets = len(targets)
    carried_payload = ""
    carried_forward = 0
    if arguments.resume_from is not None:
        if catalog_sha256 is None:
            raise SystemExit("--resume-from requires --catalog-file")
        carried_bytes = arguments.resume_from.read_bytes()
        carried_captures = AdvisorkhojCaptureParser().parse_records(carried_bytes)
        if any(
            capture.catalog_sha256 != catalog_sha256 for capture in carried_captures
        ):
            raise SystemExit("resume capture references a different catalog")
        completed = {
            (capture.amc_name, capture.requested_scheme_name)
            for capture in carried_captures
        }
        if len(completed) != len(carried_captures):
            raise SystemExit("resume capture contains duplicate AMC/scheme entries")
        targets = tuple(
            target for target in targets if (target[0], target[2]) not in completed
        )
        carried_payload = carried_bytes.decode("utf-8")
        if carried_payload and not carried_payload.endswith("\n"):
            carried_payload += "\n"
        carried_forward = len(carried_captures)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_handle = arguments.output.open("x", encoding="utf-8")
    except FileExistsError as error:
        raise SystemExit(
            f"refusing to overwrite immutable capture {arguments.output}"
        ) from error

    captured = carried_forward
    failed = 0
    with output_handle, ThreadPoolExecutor(max_workers=arguments.workers) as executor:
        output_handle.write(carried_payload)
        output_handle.flush()
        downloads = executor.map(
            lambda target: _download_target(
                target, arguments, catalog_mode=catalog_sha256 is not None
            ),
            targets,
        )
        for (amc_name, category, scheme_name), download in zip(
            targets, downloads, strict=True
        ):
            requested_url = download.requested_url
            source_payload = download.source_payload
            source_url = download.source_url
            try:
                if download.error is not None:
                    raise download.error
                if source_payload is None:
                    raise OSError("AdvisorKhoj download returned no payload")
                captured_at = datetime.now(UTC).isoformat()
                if catalog_sha256 is None:
                    page = parse_historical_distributions_html(source_payload)
                    if page.source_scheme_name.casefold() != scheme_name.casefold():
                        raise SourceParseError(
                            "rendered scheme does not match manifest entry: "
                            f"{page.source_scheme_name!r} != {scheme_name!r}"
                        )
                    records = len(page.rows)
                    display_scheme_name = page.display_scheme_name
                    document = {
                        "schema_version": CAPTURE_SCHEMA_VERSION,
                        "provider": "advisorkhoj",
                        "requested_url": requested_url,
                        "source_url": source_url,
                        "captured_at": captured_at,
                        "amc_name": amc_name,
                        "requested_scheme_name": scheme_name,
                        "source_payload_sha256": hashlib.sha256(
                            source_payload.encode("utf-8")
                        ).hexdigest(),
                        "source_payload": source_payload,
                    }
                else:
                    rows = parse_distribution_details_json(source_payload)
                    records = len(rows)
                    display_scheme_name = scheme_name
                    document = {
                        "schema_version": API_CAPTURE_SCHEMA_VERSION,
                        "provider": "advisorkhoj",
                        "catalog_sha256": catalog_sha256,
                        "source_url": source_url,
                        "captured_at": captured_at,
                        "amc_name": amc_name,
                        "category": category,
                        "requested_scheme_name": scheme_name,
                        "source_payload_sha256": hashlib.sha256(
                            source_payload.encode("utf-8")
                        ).hexdigest(),
                        "source_payload": source_payload,
                    }
                output_handle.write(
                    json.dumps(document, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
                output_handle.flush()
                captured += 1
                if captured % 100 == 0 or captured == total_targets:
                    print(
                        json.dumps(
                            {
                                "event": "advisorkhoj_capture_progress",
                                "captured": captured,
                                "failed": failed,
                                "schemes_requested": total_targets,
                                "latest_scheme_name": scheme_name,
                                "latest_display_scheme_name": display_scheme_name,
                                "latest_records": records,
                            },
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                    )
            except KeyboardInterrupt:
                raise
            except (
                OSError,
                SourceParseError,
                UnicodeError,
                urllib.error.URLError,
            ) as error:
                failed += 1
                _append_error(
                    arguments.output,
                    requested_url=requested_url,
                    source_url=source_url,
                    amc_name=amc_name,
                    scheme_name=scheme_name,
                    source_payload=source_payload,
                    error=error,
                )
                print(
                    json.dumps(
                        {
                            "event": "advisorkhoj_scheme_capture_failed",
                            "scheme_name": scheme_name,
                            "failure_type": type(error).__name__,
                            "error": str(error),
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
    print(
        json.dumps(
            {
                "event": "advisorkhoj_capture_summary",
                "schemes_requested": total_targets,
                "carried_forward": carried_forward,
                "captured_new": captured - carried_forward,
                "captured": captured,
                "failed": failed,
                "output": str(arguments.output),
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return int(failed > 0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--scheme-file", type=Path)
    source.add_argument("--catalog-file", type=Path)
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--amc-name")
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--retry-attempts", type=int, default=3)
    parser.add_argument("--workers", type=int, default=1)
    return parser


def _read_targets(
    arguments: argparse.Namespace,
) -> tuple[tuple[tuple[str, str, str], ...], str | None]:
    if arguments.catalog_file is not None:
        payload = arguments.catalog_file.read_bytes()
        catalog = parse_catalog(payload)
        return (
            tuple(
                (scheme.amc_name, scheme.category, scheme.scheme_name)
                for scheme in catalog.schemes
            ),
            hashlib.sha256(payload).hexdigest(),
        )
    if arguments.scheme_file is None or not arguments.amc_name:
        raise SystemExit("--scheme-file requires --amc-name")
    return (
        tuple(
            (arguments.amc_name, "unknown", scheme_name)
            for scheme_name in _read_scheme_manifest(arguments.scheme_file)
        ),
        None,
    )


def _read_scheme_manifest(path: Path) -> tuple[str, ...]:
    schemes = tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if not schemes:
        raise SystemExit(f"scheme manifest {path} contains no scheme names")
    if len(schemes) != len(set(schemes)):
        raise SystemExit(f"scheme manifest {path} contains duplicate scheme names")
    return schemes


def _download_target(
    target: tuple[str, str, str],
    arguments: argparse.Namespace,
    *,
    catalog_mode: bool,
) -> _DownloadResult:
    _, _, scheme_name = target
    requested_url = (
        DETAILS_URL
        if catalog_mode
        else BASE_URL + urllib.parse.quote(scheme_name, safe="")
    )
    try:
        source_payload, source_url = _download(
            requested_url,
            arguments,
            form={"scheme": scheme_name} if catalog_mode else None,
        )
        if arguments.delay_seconds:
            time.sleep(arguments.delay_seconds)
        return _DownloadResult(requested_url, source_url, source_payload, None)
    except KeyboardInterrupt:
        raise
    except (OSError, UnicodeError, urllib.error.URLError) as error:
        return _DownloadResult(requested_url, requested_url, None, error)


def _download(
    url: str,
    arguments: argparse.Namespace,
    *,
    form: dict[str, str] | None,
) -> tuple[str, str]:
    data = urllib.parse.urlencode(form).encode("ascii") if form is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": (
                "application/json"
                if form is not None
                else "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"
            ),
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, arguments.retry_attempts + 1):
        try:
            with urllib.request.urlopen(
                request, timeout=arguments.timeout_seconds
            ) as response:
                final_url = response.geturl()
                parsed_final = urllib.parse.urlparse(final_url)
                expected_path = (
                    SCHEME_DETAILS_PATH
                    if form is not None
                    else HISTORICAL_DISTRIBUTIONS_PATH
                )
                if (
                    parsed_final.scheme != "https"
                    or parsed_final.hostname != ADVISORKHOJ_HOST
                    or not parsed_final.path.startswith(expected_path)
                ):
                    raise OSError(
                        "Advisorkhoj response redirected outside the approved page path"
                    )
                media_type = response.headers.get_content_type()
                expected_media_types = (
                    {"application/json", "text/plain"}
                    if form is not None
                    else {"text/html", "application/xhtml+xml"}
                )
                if media_type not in expected_media_types:
                    raise OSError(
                        f"Advisorkhoj returned unexpected media type {media_type!r}"
                    )
                declared_size = response.headers.get("Content-Length")
                if declared_size is not None:
                    try:
                        parsed_size = int(declared_size)
                    except ValueError as error:
                        raise OSError(
                            "Advisorkhoj returned an invalid Content-Length"
                        ) from error
                    if parsed_size > MAX_RESPONSE_BYTES:
                        raise OSError(
                            f"Advisorkhoj page declares {declared_size} bytes, exceeding limit"
                        )
                content = response.read(MAX_RESPONSE_BYTES + 1)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise OSError(
                        f"Advisorkhoj page exceeds {MAX_RESPONSE_BYTES} byte capture limit"
                    )
                return content.decode("utf-8"), final_url
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in {429, 500, 502, 503, 504}:
                raise
        except (TimeoutError, urllib.error.URLError) as error:
            last_error = error
        if attempt < arguments.retry_attempts:
            time.sleep(2 ** (attempt - 1))
    raise OSError(
        f"failed to retrieve Advisorkhoj page after {arguments.retry_attempts} attempts: "
        f"{last_error}"
    )


def _append_error(
    output: Path,
    *,
    requested_url: str,
    source_url: str,
    amc_name: str,
    scheme_name: str,
    source_payload: str | None,
    error: Exception,
) -> None:
    error_path = output.with_name(output.name + ".errors.jsonl")
    value = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "provider": "advisorkhoj",
        "requested_url": requested_url,
        "source_url": source_url,
        "captured_at": datetime.now(UTC).isoformat(),
        "amc_name": amc_name,
        "requested_scheme_name": scheme_name,
        "source_payload_sha256": (
            hashlib.sha256(source_payload.encode("utf-8")).hexdigest()
            if source_payload is not None
            else None
        ),
        "source_payload": source_payload,
        "failure_type": type(error).__name__,
        "error": str(error),
    }
    with error_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        )


if __name__ == "__main__":
    raise SystemExit(main())

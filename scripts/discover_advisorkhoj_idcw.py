#!/usr/bin/env python3
"""Capture AdvisorKhoj's complete AMC/category/IDCW-scheme catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from mf_strategy_tester.ingestion.advisorkhoj import (
    ADVISORKHOJ_HOST,
    AMC_DISTRIBUTIONS_PATH,
    API_CAPTURE_SCHEMA_VERSION,
    parse_amc_catalog_html,
    parse_catalog,
)

BASE_URL = f"https://{ADVISORKHOJ_HOST}"
CATEGORY_PATH = "/mutual-funds-research/getSchemeCategoriesByAmcCompany"
SCHEME_PATH = "/mutual-funds-research/getDividendSchemeShrtNameByAmcAndCategory"
USER_AGENT = "mf-strategy-tester/0.1 (+local-personal-research)"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.delay_seconds < 0:
        raise SystemExit("--delay-seconds must be non-negative")
    if arguments.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    if arguments.retry_attempts < 1:
        raise SystemExit("--retry-attempts must be at least one")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    if arguments.output.exists():
        raise SystemExit(f"refusing to overwrite immutable catalog {arguments.output}")

    landing_payload = _request(AMC_DISTRIBUTIONS_PATH, None, arguments)
    amcs = parse_amc_catalog_html(landing_payload)
    queries: list[dict[str, object]] = []
    schemes: list[dict[str, str]] = []
    for amc_number, amc_name in enumerate(amcs, start=1):
        categories_payload = _request(CATEGORY_PATH, {"amc": amc_name}, arguments)
        categories = _string_array(categories_payload, f"categories for {amc_name}")
        scheme_queries: list[dict[str, str]] = []
        for category in categories:
            scheme_payload = _request(
                SCHEME_PATH,
                {"amc": amc_name, "category": category},
                arguments,
            )
            category_schemes = _string_array(
                scheme_payload, f"schemes for {amc_name}/{category}"
            )
            scheme_queries.append(
                {
                    "category": category,
                    "source_payload_sha256": _sha256(scheme_payload),
                    "source_payload": scheme_payload,
                }
            )
            schemes.extend(
                {
                    "amc_name": amc_name,
                    "category": category,
                    "scheme_name": scheme_name,
                }
                for scheme_name in category_schemes
            )
        queries.append(
            {
                "amc_name": amc_name,
                "categories_payload_sha256": _sha256(categories_payload),
                "categories_payload": categories_payload,
                "scheme_queries": scheme_queries,
            }
        )
        print(
            json.dumps(
                {
                    "event": "advisorkhoj_catalog_amc_captured",
                    "amc_number": amc_number,
                    "amcs_total": len(amcs),
                    "amc_name": amc_name,
                    "categories": len(categories),
                    "schemes_total_so_far": len(schemes),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )

    document = {
        "schema_version": API_CAPTURE_SCHEMA_VERSION,
        "provider": "advisorkhoj",
        "captured_at": datetime.now(UTC).isoformat(),
        "source_url": BASE_URL + AMC_DISTRIBUTIONS_PATH,
        "source_payload_sha256": _sha256(landing_payload),
        "source_payload": landing_payload,
        "queries": queries,
        "schemes": schemes,
    }
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    # Validate the exact artifact before making it visible as a completed catalog.
    catalog = parse_catalog(encoded)
    with arguments.output.open("xb") as handle:
        handle.write(encoded)
    print(
        json.dumps(
            {
                "event": "advisorkhoj_catalog_complete",
                "amcs": len(catalog.amcs),
                "category_queries": catalog.category_queries,
                "schemes": len(catalog.schemes),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "output": str(arguments.output),
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--delay-seconds", type=float, default=0.1)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--retry-attempts", type=int, default=3)
    return parser


def _request(
    path: str,
    form: dict[str, str] | None,
    arguments: argparse.Namespace,
) -> str:
    url = BASE_URL + path
    data = urllib.parse.urlencode(form).encode("ascii") if form is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json" if form is not None else "text/html",
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
                final = urllib.parse.urlparse(response.geturl())
                if final.scheme != "https" or final.hostname != ADVISORKHOJ_HOST:
                    raise OSError("AdvisorKhoj catalog response redirected off-site")
                content = response.read(MAX_RESPONSE_BYTES + 1)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise OSError(
                        f"AdvisorKhoj catalog response exceeds {MAX_RESPONSE_BYTES} bytes"
                    )
                payload = content.decode("utf-8")
                if arguments.delay_seconds:
                    time.sleep(arguments.delay_seconds)
                return payload
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in {429, 500, 502, 503, 504}:
                raise
        except (TimeoutError, UnicodeError, urllib.error.URLError) as error:
            last_error = error
        if attempt < arguments.retry_attempts:
            time.sleep(2 ** (attempt - 1))
    raise OSError(
        f"failed to retrieve AdvisorKhoj catalog endpoint {path}: {last_error}"
    )


def _string_array(payload: str, label: str) -> tuple[str, ...]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise OSError(f"AdvisorKhoj {label} returned invalid JSON") from error
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise OSError(f"AdvisorKhoj {label} did not return a string array")
    result = tuple(item.strip() for item in value)
    if len(result) != len(set(result)):
        raise OSError(f"AdvisorKhoj {label} returned duplicate values")
    return result


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())

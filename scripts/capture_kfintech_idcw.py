#!/usr/bin/env python3
"""Capture KFintech IDCW history as source-containing JSONL.

The public page is an ASP.NET form, so each scheme query begins with a fresh GET and
posts back its hidden state. The emitted source payload is the exact response HTML;
the importer validates its SHA-256 before accepting any extracted row.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mf_strategy_tester.ingestion.errors import SourceParseError
from mf_strategy_tester.ingestion.kfintech import (
    KfintechFund,
    KfintechScheme,
    classify_plan,
    classify_scheme_option_variant,
    parse_distributions,
    parse_funds,
    parse_scheme_form,
)

LANDING_URL = (
    "https://mfs.kfintech.com/mfs/InvestorServices/NAVDividend/NAV_Dividend.aspx"
)
DIVIDEND_URL = "https://mfs.kfintech.com/mfs/NAVDividend/Dividend.aspx?Fund={fund_code}"
USER_AGENT = "MFStrategyTester-RTA-Capture/1.0 (+local research; auditable ingestion)"
MAX_RESPONSE_BYTES = 12 * 1024 * 1024


class SchemeExtractionError(RuntimeError):
    def __init__(self, message: str, *, source_url: str, source_payload: str) -> None:
        super().__init__(message)
        self.source_url = source_url
        self.source_payload = source_payload


class FundSchemeInventoryError(RuntimeError):
    def __init__(self, message: str, *, source_url: str, source_payload: str) -> None:
        super().__init__(message)
        self.source_url = source_url
        self.source_payload = source_payload


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.from_date > arguments.to_date:
        raise SystemExit("--from-date must not be after --to-date")
    if arguments.max_consecutive_failures < 1:
        raise SystemExit("--max-consecutive-failures must be positive")
    if arguments.scheme_code and (
        not arguments.fund_code or len(set(arguments.fund_code)) != 1
    ):
        raise SystemExit("--scheme-code requires exactly one --fund-code")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    completed = _completed_keys(arguments.output)
    recovered, retained_failures = _recover_failed_captures(arguments.output, completed)
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    landing = _get(opener, LANDING_URL, arguments)
    funds = tuple(
        fund
        for fund in parse_funds(landing)
        if not arguments.fund_code or fund.code in arguments.fund_code
    )
    if arguments.fund_code:
        missing = set(arguments.fund_code) - {fund.code for fund in funds}
        if missing:
            raise SystemExit(f"unknown KFintech fund code(s): {sorted(missing)}")

    captured = skipped = deferred = failed = consecutive_failures = funds_failed = 0
    stopped_early = False
    for fund in funds:
        try:
            schemes = _schemes(opener, fund, arguments)
        except (OSError, RuntimeError, SourceParseError, ValueError) as error:
            funds_failed += 1
            _append_fund_error(arguments.output, fund, error)
            print(
                json.dumps(
                    {
                        "event": "kfintech_fund_inventory_failed",
                        "fund_code": fund.code,
                        "fund_name": fund.name,
                        "failure_type": type(error).__name__,
                        "error": str(error),
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            continue
        schemes = _filter_schemes(schemes, arguments.scheme_code)
        for scheme in schemes:
            key = (fund.code, scheme.code)
            if key in completed:
                skipped += 1
                continue
            if key in retained_failures and not arguments.retry_retained_failures:
                deferred += 1
                print(
                    json.dumps(
                        {
                            "event": "kfintech_scheme_failure_deferred",
                            "fund_code": fund.code,
                            "scheme_code": scheme.code,
                            "message": (
                                "latest checksum-verified response still fails strict parsing; "
                                "use --retry-retained-failures for a new network attempt"
                            ),
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
                continue
            if (
                arguments.max_schemes is not None
                and captured + failed >= arguments.max_schemes
            ):
                _summary(
                    captured,
                    recovered,
                    skipped,
                    deferred,
                    failed,
                    funds_failed,
                    stopped_early,
                )
                return int(failed > 0 or funds_failed > 0)
            try:
                document = _capture_scheme(opener, fund, scheme, arguments)
                _append_capture(arguments.output, document)
                captured += 1
                consecutive_failures = 0
                print(
                    json.dumps(
                        {
                            "event": "kfintech_scheme_captured",
                            "fund_code": fund.code,
                            "scheme_code": scheme.code,
                            "records": len(document["records"]),
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
            except KeyboardInterrupt:
                raise
            except (OSError, RuntimeError, SourceParseError, ValueError) as error:
                failed += 1
                consecutive_failures += 1
                _append_error(arguments.output, fund, scheme, error)
                print(
                    json.dumps(
                        {
                            "event": "kfintech_scheme_capture_failed",
                            "fund_code": fund.code,
                            "scheme_code": scheme.code,
                            "failure_type": type(error).__name__,
                            "error": str(error),
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
                if consecutive_failures >= arguments.max_consecutive_failures:
                    stopped_early = True
                    print(
                        json.dumps(
                            {
                                "event": "kfintech_capture_circuit_open",
                                "consecutive_failures": consecutive_failures,
                                "message": (
                                    "stopping KFintech capture early; rerun the same resumable "
                                    "command after correcting the parser or source outage"
                                ),
                            },
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                    )
                    break
            if arguments.delay_seconds:
                time.sleep(arguments.delay_seconds)
        if stopped_early:
            break
    _summary(
        captured,
        recovered,
        skipped,
        deferred,
        failed,
        funds_failed,
        stopped_early,
    )
    return int(failed > 0 or funds_failed > 0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--from-date", type=date.fromisoformat, default=date(2000, 1, 1)
    )
    parser.add_argument(
        "--to-date",
        type=date.fromisoformat,
        default=datetime.now(ZoneInfo("Asia/Kolkata")).date(),
    )
    parser.add_argument("--fund-code", action="append")
    parser.add_argument(
        "--scheme-code",
        action="append",
        help="capture only this proprietary scheme code; requires one --fund-code",
    )
    parser.add_argument("--max-schemes", type=int)
    parser.add_argument("--delay-seconds", type=float, default=0.25)
    parser.add_argument("--timeout-seconds", type=float, default=45)
    parser.add_argument("--retry-attempts", type=int, default=4)
    parser.add_argument("--max-consecutive-failures", type=int, default=5)
    parser.add_argument(
        "--retry-retained-failures",
        action="store_true",
        help="retry schemes whose latest retained response still fails strict parsing",
    )
    return parser


def _filter_schemes(
    schemes: tuple[KfintechScheme, ...], requested_codes: list[str] | None
) -> tuple[KfintechScheme, ...]:
    if not requested_codes:
        return schemes
    requested = set(requested_codes)
    selected = tuple(scheme for scheme in schemes if scheme.code in requested)
    missing = requested - {scheme.code for scheme in selected}
    if missing:
        raise SystemExit(f"unknown KFintech scheme code(s): {sorted(missing)}")
    return selected


def _schemes(
    opener: urllib.request.OpenerDirector,
    fund: KfintechFund,
    arguments: argparse.Namespace,
) -> tuple[KfintechScheme, ...]:
    url = DIVIDEND_URL.format(fund_code=urllib.parse.quote(fund.code, safe=""))
    page = _get(opener, url, arguments)
    try:
        schemes, _ = parse_scheme_form(page)
    except SourceParseError as error:
        raise FundSchemeInventoryError(
            f"{type(error).__name__}: {error}",
            source_url=url,
            source_payload=page,
        ) from error
    # KFintech's dividend form includes legacy "Dividend" labels as well as IDCW.
    selected = tuple(
        scheme
        for scheme in schemes
        if any(
            "idcw" in name.casefold() or "dividend" in name.casefold()
            for name in (scheme.name, *scheme.aliases)
        )
    )
    if not selected:
        raise FundSchemeInventoryError(
            f"KFintech fund {fund.code} has no IDCW/dividend scheme options",
            source_url=url,
            source_payload=page,
        )
    return selected


def _capture_scheme(
    opener: urllib.request.OpenerDirector,
    fund: KfintechFund,
    scheme: KfintechScheme,
    arguments: argparse.Namespace,
) -> dict[str, object]:
    url = DIVIDEND_URL.format(fund_code=urllib.parse.quote(fund.code, safe=""))
    form_page = _get(opener, url, arguments)
    _, hidden = parse_scheme_form(form_page)
    fields = dict(hidden)
    fields.update(
        {
            "ctl00$ContentPlaceHolder1$SelScheme": scheme.code,
            "ctl00$ContentPlaceHolder1$txtFromDate": arguments.from_date.strftime(
                "%d/%m/%Y"
            ),
            "ctl00$ContentPlaceHolder1$txtToDate": arguments.to_date.strftime(
                "%d/%m/%Y"
            ),
            "ctl00$ContentPlaceHolder1$btnGetDividend": "Get Data",
        }
    )
    # Image-button coordinates and stale event fields can change postback semantics.
    fields.pop("ctl00$ContentPlaceHolder1$ibFrom.x", None)
    fields.pop("ctl00$ContentPlaceHolder1$ibFrom.y", None)
    fields.pop("ctl00$ContentPlaceHolder1$ibTo.x", None)
    fields.pop("ctl00$ContentPlaceHolder1$ibTo.y", None)
    response = _post(opener, url, fields, arguments)
    return _document_from_response(
        fund,
        scheme,
        url=url,
        response=response,
        captured_at=datetime.now(UTC).isoformat(),
    )


def _document_from_response(
    fund: KfintechFund,
    scheme: KfintechScheme,
    *,
    url: str,
    response: str,
    captured_at: str,
) -> dict[str, object]:
    try:
        rows = parse_distributions(response)
    except SourceParseError as error:
        raise SchemeExtractionError(
            f"{type(error).__name__}: {error}",
            source_url=url,
            source_payload=response,
        ) from error
    return {
        "schema_version": 1,
        "provider": "kfintech",
        "source_url": url,
        "captured_at": captured_at,
        "fund": {"code": fund.code, "name": fund.name},
        "scheme": {
            "code": scheme.code,
            "name": scheme.name,
            "plan_type": classify_plan(scheme.name),
            "option_variant": classify_scheme_option_variant(scheme),
            "latest_nav_date": None,
            "latest_nav_value": None,
        },
        "source_payload_sha256": hashlib.sha256(response.encode("utf-8")).hexdigest(),
        "source_payload": response,
        "records": [
            {
                "record_date": row.record_date,
                "individual_amount": row.individual_amount,
                "non_individual_amount": row.non_individual_amount,
                "ex_nav": row.ex_nav,
                "cum_nav": row.cum_nav,
                "source_terminology": "KFintech dividend history",
            }
            for row in rows
        ],
    }


def _get(
    opener: urllib.request.OpenerDirector, url: str, arguments: argparse.Namespace
) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return _request(opener, request, arguments)


def _post(
    opener: urllib.request.OpenerDirector,
    url: str,
    fields: dict[str, str],
    arguments: argparse.Namespace,
) -> str:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("ascii"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": url,
        },
        method="POST",
    )
    return _request(opener, request, arguments)


def _request(
    opener: urllib.request.OpenerDirector,
    request: urllib.request.Request,
    arguments: argparse.Namespace,
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, arguments.retry_attempts + 1):
        try:
            with opener.open(request, timeout=arguments.timeout_seconds) as response:
                content = response.read(MAX_RESPONSE_BYTES + 1)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise RuntimeError(
                        f"KFintech response exceeds {MAX_RESPONSE_BYTES} bytes"
                    )
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    return content.decode(charset)
                except UnicodeDecodeError:
                    return content.decode("windows-1252")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt == arguments.retry_attempts:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
    raise RuntimeError(f"KFintech request failed after retries: {last_error}")


def _append_capture(path: Path, document: dict[str, object]) -> None:
    encoded = (
        json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")
    with path.open("ab") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())


def _append_error(
    output_path: Path,
    fund: KfintechFund,
    scheme: KfintechScheme,
    error: Exception,
) -> None:
    source_url = (
        error.source_url
        if isinstance(error, SchemeExtractionError)
        else DIVIDEND_URL.format(fund_code=urllib.parse.quote(fund.code, safe=""))
    )
    source_payload = (
        error.source_payload if isinstance(error, SchemeExtractionError) else ""
    )
    document = {
        "schema_version": 1,
        "provider": "kfintech",
        "captured_at": datetime.now(UTC).isoformat(),
        "fund": {"code": fund.code, "name": fund.name},
        "scheme": {"code": scheme.code, "name": scheme.name},
        "source_url": source_url,
        "source_payload_sha256": hashlib.sha256(
            source_payload.encode("utf-8")
        ).hexdigest(),
        "source_payload": source_payload,
        "failure_type": type(error).__name__,
        "error": str(error),
    }
    error_path = output_path.with_name(output_path.name + ".errors.jsonl")
    _append_capture(error_path, document)


def _append_fund_error(
    output_path: Path,
    fund: KfintechFund,
    error: Exception,
) -> None:
    source_url = (
        error.source_url
        if isinstance(error, FundSchemeInventoryError)
        else DIVIDEND_URL.format(fund_code=urllib.parse.quote(fund.code, safe=""))
    )
    source_payload = (
        error.source_payload if isinstance(error, FundSchemeInventoryError) else ""
    )
    document = {
        "schema_version": 1,
        "provider": "kfintech",
        "captured_at": datetime.now(UTC).isoformat(),
        "fund": {"code": fund.code, "name": fund.name},
        "source_url": source_url,
        "source_payload_sha256": hashlib.sha256(
            source_payload.encode("utf-8")
        ).hexdigest(),
        "source_payload": source_payload,
        "failure_type": type(error).__name__,
        "error": str(error),
    }
    error_path = output_path.with_name(output_path.name + ".fund-errors.jsonl")
    _append_capture(error_path, document)


def _completed_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    completed: set[tuple[str, str]] = set()
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                document = json.loads(line)
                completed.add((document["fund"]["code"], document["scheme"]["code"]))
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise RuntimeError(
                    f"existing capture has an invalid line {line_number}; refusing to append"
                ) from error
    return completed


def _recover_failed_captures(
    output_path: Path, completed: set[tuple[str, str]]
) -> tuple[int, frozenset[tuple[str, str]]]:
    """Replay the latest retained response for each failed scheme before network access."""
    error_path = output_path.with_name(output_path.name + ".errors.jsonl")
    if not error_path.exists():
        return 0, frozenset()
    latest_offsets: dict[tuple[str, str], tuple[int, int]] = {}
    with error_path.open("rb") as source:
        line_number = 0
        while True:
            offset = source.tell()
            raw_line = source.readline()
            if not raw_line:
                break
            line_number += 1
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
                fund = value["fund"]
                scheme = value["scheme"]
                key = (str(fund["code"]), str(scheme["code"]))
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise RuntimeError(
                    f"KFintech error sidecar has an invalid line {line_number}"
                ) from error
            latest_offsets[key] = (offset, line_number)

    recovered = 0
    retained_failures: set[tuple[str, str]] = set()
    with error_path.open("rb") as source:
        for key in sorted(latest_offsets):
            if key in completed:
                continue
            offset, line_number = latest_offsets[key]
            source.seek(offset)
            value = json.loads(source.readline())
            expected = {
                "schema_version",
                "provider",
                "captured_at",
                "fund",
                "scheme",
                "source_url",
                "source_payload_sha256",
                "source_payload",
                "failure_type",
                "error",
            }
            if not isinstance(value, dict) or set(value) != expected:
                raise RuntimeError(
                    f"KFintech error sidecar line {line_number} has an unexpected shape"
                )
            response = value["source_payload"]
            expected_sha = value["source_payload_sha256"]
            if not isinstance(response, str) or not response:
                continue
            if (
                not isinstance(expected_sha, str)
                or hashlib.sha256(response.encode("utf-8")).hexdigest() != expected_sha
            ):
                raise RuntimeError(
                    f"KFintech error sidecar checksum mismatch at line {line_number}"
                )
            fund_value = value["fund"]
            scheme_value = value["scheme"]
            if not isinstance(fund_value, dict) or not isinstance(scheme_value, dict):
                raise TypeError(
                    f"KFintech error sidecar identity is invalid at line {line_number}"
                )
            fund = KfintechFund(
                code=key[0], name=str(fund_value.get("name", "")).strip()
            )
            scheme = KfintechScheme(
                code=key[1], name=str(scheme_value.get("name", "")).strip()
            )
            if not fund.name or not scheme.name:
                raise RuntimeError(
                    f"KFintech error sidecar identity is blank at line {line_number}"
                )
            try:
                document = _document_from_response(
                    fund,
                    scheme,
                    url=str(value["source_url"]),
                    response=response,
                    captured_at=str(value["captured_at"]),
                )
            except SchemeExtractionError:
                retained_failures.add(key)
                continue
            _append_capture(output_path, document)
            completed.add(key)
            recovered += 1
            print(
                json.dumps(
                    {
                        "event": "kfintech_scheme_recovered",
                        "fund_code": key[0],
                        "scheme_code": key[1],
                        "records": len(document["records"]),
                        "error_line": line_number,
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
    return recovered, frozenset(retained_failures)


def _summary(
    captured: int,
    recovered: int,
    skipped: int,
    deferred: int,
    failed: int,
    funds_failed: int,
    stopped_early: bool,
) -> None:
    print(
        json.dumps(
            {
                "event": "kfintech_capture_completed",
                "captured": captured,
                "recovered_from_error_payloads": recovered,
                "resumed_skipped": skipped,
                "retained_failures_deferred": deferred,
                "failed": failed,
                "funds_failed": funds_failed,
                "stopped_early": stopped_early,
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())

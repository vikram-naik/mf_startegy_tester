from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import urlparse

from mf_strategy_tester.ingestion.errors import SourceParseError

PARSER_VERSION = "rta-capture-2026.08.4"
CAPTURE_SCHEMA_VERSION = 1
RTA_HOSTS = {"www.camsonline.com": "cams", "mfs.kfintech.com": "kfintech"}
_CAMS_AMOUNT_QUANTUM = Decimal("0.0000000001")
_CAMS_FLOAT_NOISE_TOLERANCE = Decimal("0.000000000001")


@dataclass(frozen=True)
class RtaDistributionRow:
    record_date: date
    raw_individual_amount: str
    individual_amount_per_unit_inr: Decimal
    raw_non_individual_amount: str | None
    non_individual_amount_per_unit_inr: Decimal | None
    ex_nav: Decimal | None
    cum_nav: Decimal | None
    source_terminology: str


@dataclass(frozen=True)
class RtaSchemeCapture:
    provider: str
    source_url: str
    captured_at: datetime
    rta_fund_code: str
    rta_fund_name: str
    rta_scheme_code: str
    source_scheme_name: str
    plan_type: str
    option_variant: str
    latest_nav_date: date | None
    latest_nav_value: Decimal | None
    source_payload_sha256: str
    source_payload: str
    rows: tuple[RtaDistributionRow, ...]


class RtaCaptureParser:
    """Strict parser for newline-delimited, source-containing RTA browser captures."""

    version = PARSER_VERSION

    def validate(self, payload: bytes) -> int:
        return len(self.parse_records(payload))

    def parse_records(self, payload: bytes) -> tuple[RtaSchemeCapture, ...]:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SourceParseError("RTA capture is not valid UTF-8") from error
        captures: list[RtaSchemeCapture] = []
        seen: set[tuple[str, str, str, datetime]] = set()
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError as error:
                raise SourceParseError(f"RTA capture line {line_number} is invalid JSON") from error
            capture = self._parse_capture(value, line_number)
            key = (
                capture.provider,
                capture.rta_fund_code,
                capture.rta_scheme_code,
                capture.captured_at,
            )
            if key in seen:
                raise SourceParseError(
                    f"duplicate RTA scheme capture at line {line_number}: {key[:3]}"
                )
            seen.add(key)
            captures.append(capture)
        if not captures:
            raise SourceParseError("RTA capture contains no scheme observations")
        providers = {capture.provider for capture in captures}
        if len(providers) != 1:
            raise SourceParseError("one RTA capture artifact cannot mix providers")
        return tuple(captures)

    def _parse_capture(self, value: object, line_number: int) -> RtaSchemeCapture:
        if not isinstance(value, dict):
            raise SourceParseError(f"RTA capture line {line_number} must be an object")
        required = {
            "schema_version",
            "provider",
            "source_url",
            "captured_at",
            "fund",
            "scheme",
            "source_payload_sha256",
            "source_payload",
            "records",
        }
        if set(value) != required:
            raise SourceParseError(f"RTA capture line {line_number} has an unexpected field set")
        if value["schema_version"] != CAPTURE_SCHEMA_VERSION:
            raise SourceParseError(f"unsupported RTA capture schema at line {line_number}")
        provider = _required_string(value["provider"], "provider", line_number)
        source_url = _required_string(value["source_url"], "source_url", line_number)
        parsed_url = urlparse(source_url)
        if parsed_url.scheme != "https" or RTA_HOSTS.get(parsed_url.hostname or "") != provider:
            raise SourceParseError(f"RTA capture line {line_number} source URL/provider mismatch")
        captured_at = _aware_datetime(value["captured_at"], line_number)
        fund = _exact_object(value["fund"], {"code", "name"}, "fund", line_number)
        scheme = _exact_object(
            value["scheme"],
            {
                "code",
                "name",
                "plan_type",
                "option_variant",
                "latest_nav_date",
                "latest_nav_value",
            },
            "scheme",
            line_number,
        )
        plan_type = _required_string(scheme["plan_type"], "plan_type", line_number)
        if plan_type not in {"direct", "regular", "unknown"}:
            raise SourceParseError(f"invalid RTA plan type at line {line_number}")
        option_variant = _required_string(scheme["option_variant"], "option_variant", line_number)
        if option_variant not in {"payout", "reinvestment", "unknown"}:
            raise SourceParseError(f"invalid RTA option variant at line {line_number}")
        latest_nav_date = _optional_date(scheme["latest_nav_date"], line_number)
        latest_nav_value = _optional_decimal(
            scheme["latest_nav_value"], "latest_nav_value", line_number, allow_zero=True
        )
        if (latest_nav_date is None) != (latest_nav_value is None):
            raise SourceParseError(
                f"RTA latest NAV date/value must both be present at line {line_number}"
            )
        source_payload = _required_source_payload(value["source_payload"], line_number)
        source_payload_sha256 = _required_string(
            value["source_payload_sha256"], "source_payload_sha256", line_number
        )
        actual_sha256 = hashlib.sha256(source_payload.encode("utf-8")).hexdigest()
        if source_payload_sha256 != actual_sha256:
            raise SourceParseError(f"RTA source payload checksum mismatch at line {line_number}")
        raw_records = value["records"]
        if not isinstance(raw_records, list):
            raise SourceParseError(f"RTA records must be an array at line {line_number}")
        rows = tuple(self._parse_row(row, line_number, provider) for row in raw_records)
        if len(rows) != len(set(rows)):
            raise SourceParseError(f"duplicate RTA source row at line {line_number}")
        return RtaSchemeCapture(
            provider=provider,
            source_url=source_url,
            captured_at=captured_at,
            rta_fund_code=_required_string(fund["code"], "fund.code", line_number),
            rta_fund_name=_required_string(fund["name"], "fund.name", line_number),
            rta_scheme_code=_required_string(scheme["code"], "scheme.code", line_number),
            source_scheme_name=_required_string(scheme["name"], "scheme.name", line_number),
            plan_type=plan_type,
            option_variant=option_variant,
            latest_nav_date=latest_nav_date,
            latest_nav_value=latest_nav_value,
            source_payload_sha256=source_payload_sha256,
            source_payload=source_payload,
            rows=rows,
        )

    @staticmethod
    def _parse_row(value: object, line_number: int, provider: str) -> RtaDistributionRow:
        row = _exact_object(
            value,
            {
                "record_date",
                "individual_amount",
                "non_individual_amount",
                "ex_nav",
                "cum_nav",
                "source_terminology",
            },
            "record",
            line_number,
        )
        raw_individual = _required_string(
            row["individual_amount"], "individual_amount", line_number
        )
        raw_non_individual = _optional_string(row["non_individual_amount"], line_number)
        individual_amount = _required_decimal(raw_individual, "individual_amount", line_number)
        non_individual_amount = (
            _optional_decimal(
                raw_non_individual,
                "non_individual_amount",
                line_number,
                allow_zero=True,
            )
            if raw_non_individual is not None
            else None
        )
        if provider == "cams":
            individual_amount = _normalize_cams_amount(
                individual_amount, "individual_amount", line_number
            )
            if non_individual_amount is not None:
                non_individual_amount = _normalize_cams_amount(
                    non_individual_amount, "non_individual_amount", line_number
                )
        return RtaDistributionRow(
            record_date=_required_date(row["record_date"], line_number),
            raw_individual_amount=raw_individual,
            individual_amount_per_unit_inr=individual_amount,
            raw_non_individual_amount=raw_non_individual,
            non_individual_amount_per_unit_inr=non_individual_amount,
            ex_nav=_optional_decimal(row["ex_nav"], "ex_nav", line_number, allow_zero=True),
            cum_nav=_optional_decimal(row["cum_nav"], "cum_nav", line_number, allow_zero=True),
            source_terminology=_required_string(
                row["source_terminology"], "source_terminology", line_number
            ),
        )


def _exact_object(
    value: object, expected: set[str], label: str, line_number: int
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise SourceParseError(f"RTA {label} has an unexpected shape at line {line_number}")
    return value


def _required_string(value: object, label: str, line_number: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceParseError(f"RTA {label} must be non-empty at line {line_number}")
    return value.strip()


def _required_source_payload(value: object, line_number: int) -> str:
    """Validate raw source text without changing checksum-significant whitespace."""
    if not isinstance(value, str) or not value.strip():
        raise SourceParseError(f"RTA source_payload must be non-empty at line {line_number}")
    return value


def _optional_string(value: object, line_number: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SourceParseError(f"RTA optional string is invalid at line {line_number}")
    return value.strip()


def _aware_datetime(value: object, line_number: int) -> datetime:
    if not isinstance(value, str):
        raise SourceParseError(f"RTA captured_at must be a string at line {line_number}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SourceParseError(f"invalid RTA captured_at at line {line_number}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceParseError(f"RTA captured_at lacks timezone at line {line_number}")
    return parsed


def _required_date(value: object, line_number: int) -> date:
    parsed = _optional_date(value, line_number)
    if parsed is None:
        raise SourceParseError(f"RTA record date is missing at line {line_number}")
    return parsed


def _optional_date(value: object, line_number: int) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceParseError(f"RTA date must be a string at line {line_number}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SourceParseError(f"invalid RTA date at line {line_number}") from error


def _required_decimal(value: object, label: str, line_number: int) -> Decimal:
    parsed = _optional_decimal(value, label, line_number, allow_zero=False)
    if parsed is None:
        raise SourceParseError(f"RTA {label} is missing at line {line_number}")
    return parsed


def _optional_decimal(
    value: object, label: str, line_number: int, *, allow_zero: bool
) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SourceParseError(f"RTA {label} must be a decimal string at line {line_number}")
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation as error:
        raise SourceParseError(f"invalid RTA {label} at line {line_number}") from error
    if not parsed.is_finite() or parsed < 0 or (parsed == 0 and not allow_zero):
        raise SourceParseError(f"invalid RTA {label} at line {line_number}")
    return parsed


def _normalize_cams_amount(value: Decimal, label: str, line_number: int) -> Decimal:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise SourceParseError(f"CAMS {label} is not finite at line {line_number}")
    if exponent >= -10:
        return value
    rounded = value.quantize(_CAMS_AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)
    if abs(value - rounded) > _CAMS_FLOAT_NOISE_TOLERANCE:
        raise SourceParseError(
            f"CAMS {label} has unsupported precision beyond 10 decimals at line {line_number}"
        )
    return rounded

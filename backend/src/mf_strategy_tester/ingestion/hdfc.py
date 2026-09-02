from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from mf_strategy_tester.ingestion.amfi import ArtifactParser
from mf_strategy_tester.ingestion.errors import SourceParseError

PARSER_VERSION = "hdfc-amc-2026.08.1"
HDFC_FILES_HOST = "files.hdfcfund.com"


class HdfcSourceType(StrEnum):
    DISTRIBUTION_NOTICE = "distribution_notice"
    SCHEME_SUMMARY = "scheme_summary"


@dataclass(frozen=True)
class HdfcSourceRequest:
    source_type: HdfcSourceType
    url: str
    parameters: dict[str, str]
    parser: ArtifactParser
    provider: str = field(default="hdfc_amc", init=False)


@dataclass(frozen=True)
class HdfcNoticeDistribution:
    scheme_name: str
    plan_type: str
    option_label: str
    record_date: date
    raw_amount_per_unit_inr: str
    amount_per_unit_inr: Decimal


@dataclass(frozen=True)
class HdfcSchemeIdentity:
    amfi_scheme_code: str
    scheme_name: str
    plan_type: str
    option_type: str


class HdfcDistributionNoticeParser:
    """Parse the strict, table-based HDFC IDCW declaration notice format."""

    version = PARSER_VERSION

    def validate(self, payload: bytes) -> int:
        return len(self.parse_records(payload))

    def parse_records(self, payload: bytes) -> tuple[HdfcNoticeDistribution, ...]:
        return self.parse_extracted_text(_extract_pdf_text(payload))

    def parse_extracted_text(self, text: str) -> tuple[HdfcNoticeDistribution, ...]:
        normalized = _normalize_text(text)
        if "HDFC Asset Management Company Limited" not in normalized:
            raise SourceParseError("HDFC notice is missing the official AMC issuer marker")
        if "Distribution cum Capital Withdrawal" not in normalized:
            raise SourceParseError("HDFC notice is not an IDCW distribution declaration")

        record_date = self._record_date(normalized)
        table = self._table_text(normalized)
        options = tuple(_NOTICE_OPTION.finditer(table))
        if not options:
            raise SourceParseError("HDFC notice table contains no supported IDCW option rows")

        amounts = self._shared_distribution_amounts(table, options)
        if len(amounts) != 1:
            raise SourceParseError(
                "HDFC notice table must contain exactly one row-spanning distribution amount"
            )
        raw_amount, amount = amounts[0]
        return tuple(
            HdfcNoticeDistribution(
                scheme_name=_clean_scheme_name(match.group("scheme_name")),
                plan_type=match.group("plan_type").lower(),
                option_label="IDCW Option (Payout and Reinvestment)",
                record_date=record_date,
                raw_amount_per_unit_inr=raw_amount,
                amount_per_unit_inr=amount,
            )
            for match in options
        )

    @staticmethod
    def _record_date(text: str) -> date:
        match = re.search(
            r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
            r"(?P<month>[A-Za-z]+)\s+(?P<day>[0-9]{1,2}),\s*(?P<year>[0-9]{4})"
            r".*?fixed as the Record Date",
            text,
            flags=re.IGNORECASE,
        )
        if match is None:
            raise SourceParseError("HDFC notice has no unambiguous declared record date")
        try:
            return datetime.strptime(
                f"{match.group('month')} {match.group('day')} {match.group('year')}",
                "%B %d %Y",
            ).date()
        except ValueError as error:
            raise SourceParseError("HDFC notice contains an invalid record date") from error

    @staticmethod
    def _table_text(text: str) -> str:
        header = "Name of the Scheme / Plan(s) / Option(s)"
        start = text.find(header)
        end = text.find("#Amount of distribution per unit")
        if start < 0 or end <= start:
            raise SourceParseError("HDFC notice distribution table boundaries are missing")
        table = text[start:end]
        if "Amount of Distribution" not in table or "Face Value" not in table:
            raise SourceParseError("HDFC notice distribution table headers changed")
        return table

    @staticmethod
    def _shared_distribution_amounts(
        table: str, options: tuple[re.Match[str], ...]
    ) -> tuple[tuple[str, Decimal], ...]:
        # Each supported option row has one three-decimal NAV immediately after its label.
        # Remove those NAVs first; the remaining positive decimal before the face value is
        # the single table cell geometrically shared by the listed plan rows.
        without_rows = table
        for match in reversed(options):
            row_tail = without_rows[match.end() :]
            nav_match = re.match(r"\s*(?P<nav>[0-9]+\.[0-9]{3,6})", row_tail)
            if nav_match is None:
                raise SourceParseError(
                    f"HDFC notice has no NAV beside its {match.group('plan_type')} IDCW row"
                )
            start = match.end() + nav_match.start("nav")
            end = match.end() + nav_match.end("nav")
            without_rows = without_rows[:start] + without_rows[end:]

        numeric_values = re.findall(r"(?<![0-9.])([0-9]+\.[0-9]{2,6})(?![0-9.])", without_rows)
        # HDFC notices label face value separately; it is not a payout amount.
        candidates = [value for value in numeric_values if Decimal(value) != Decimal("10.00")]
        parsed: list[tuple[str, Decimal]] = []
        for value in candidates:
            try:
                amount = Decimal(value)
            except InvalidOperation as error:
                raise SourceParseError("HDFC notice has an invalid distribution amount") from error
            if amount <= 0:
                raise SourceParseError("HDFC notice distribution amount must be positive")
            parsed.append((value, amount))
        return tuple(parsed)


class HdfcSchemeSummaryParser:
    """Extract explicit AMFI-code identity evidence from an HDFC scheme summary."""

    version = PARSER_VERSION

    def validate(self, payload: bytes) -> int:
        return len(self.parse_records(payload))

    def parse_records(self, payload: bytes) -> tuple[HdfcSchemeIdentity, ...]:
        return self.parse_extracted_text(_extract_pdf_text(payload))

    def parse_extracted_text(self, text: str) -> tuple[HdfcSchemeIdentity, ...]:
        normalized = _normalize_text(text)
        if "SCHEME SUMMARY DOCUMENT" not in normalized or "HDFC" not in normalized:
            raise SourceParseError("HDFC scheme summary issuer or document marker is missing")
        section_match = re.search(
            r"AMFI Codes \(To be phased out\)\s*(?P<section>.+?)30\s+SEBI Codes",
            normalized,
            flags=re.IGNORECASE,
        )
        if section_match is None:
            raise SourceParseError("HDFC scheme summary AMFI-code section is missing")
        section = section_match.group("section")
        matches = tuple(
            re.finditer(
                r"(?P<code>[0-9]{6})\s*-\s*(?P<label>.+?)(?=\s*[0-9]{6}\s*-|$)",
                section,
            )
        )
        if not matches:
            raise SourceParseError("HDFC scheme summary contains no AMFI-code mappings")
        identities: list[HdfcSchemeIdentity] = []
        for match in matches:
            label = match.group("label").strip()
            option_type = "idcw" if "IDCW" in label.upper() else "growth"
            plan_type = "direct" if "DIRECT PLAN" in label.upper() else "regular"
            scheme_name = re.sub(
                r"\s*-\s*(?:IDCW|Growth) Plan(?:\s*-\s*Direct Plan)?\s*$",
                "",
                label,
                flags=re.IGNORECASE,
            ).strip()
            identities.append(
                HdfcSchemeIdentity(
                    amfi_scheme_code=match.group("code"),
                    scheme_name=_clean_scheme_name(scheme_name),
                    plan_type=plan_type,
                    option_type=option_type,
                )
            )
        return tuple(identities)


def distribution_notice_request(url: str) -> HdfcSourceRequest:
    return HdfcSourceRequest(
        source_type=HdfcSourceType.DISTRIBUTION_NOTICE,
        url=url,
        parameters={},
        parser=HdfcDistributionNoticeParser(),
    )


def scheme_summary_request(url: str) -> HdfcSourceRequest:
    return HdfcSourceRequest(
        source_type=HdfcSourceType.SCHEME_SUMMARY,
        url=url,
        parameters={},
        parser=HdfcSchemeSummaryParser(),
    )


def _extract_pdf_text(payload: bytes) -> str:
    if not payload.startswith(b"%PDF-"):
        raise SourceParseError("official HDFC source is not a PDF payload")
    try:
        reader = PdfReader(BytesIO(payload), strict=True)
        if reader.is_encrypted:
            raise SourceParseError("official HDFC PDF is encrypted")
        pages = [page.extract_text() for page in reader.pages]
    except (PdfReadError, OSError, ValueError) as error:
        raise SourceParseError("official HDFC PDF could not be parsed") from error
    if not pages or any(page is None for page in pages):
        raise SourceParseError("official HDFC PDF contains no extractable text")
    return "\n".join(page for page in pages if page is not None)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("`", "₹")).strip()


def _clean_scheme_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -")


_NOTICE_OPTION = re.compile(
    r"(?P<scheme_name>HDFC\s+.+?)\s*-\s*"
    r"(?P<plan_type>Regular|Direct)\s+Plan\s*-\s*"
    r"IDCW\s+Option\s*\(Payout\s+and\s+Reinvestment\)",
    flags=re.IGNORECASE,
)

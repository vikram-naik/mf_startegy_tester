from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

from mf_strategy_tester.ingestion.errors import SourceParseError

PARSER_VERSION = "advisorkhoj-distribution-2026.08.2"
CAPTURE_SCHEMA_VERSION = 1
API_CAPTURE_SCHEMA_VERSION = 2
ADVISORKHOJ_HOST = "www.advisorkhoj.com"
HISTORICAL_DISTRIBUTIONS_PATH = "/mutual-funds-research/mutual-funds-historical-dividends/"
AMC_DISTRIBUTIONS_PATH = "/mutual-funds-research/amc-wise-dividends"
SCHEME_DETAILS_PATH = "/mutual-funds-research/getSchemeDividendDetails"
_EXPECTED_HEADERS = (
    "dividend record date",
    "dividend unit",
    "nav",
    "dividend yield",
)
_SCHEME_LITERAL = re.compile(r"var\s+scheme_name\s*=\s*'(?P<name>[^']+)'\s*;")


@dataclass(frozen=True)
class AdvisorkhojDistributionRow:
    record_date: date
    raw_amount_per_unit_inr: str
    amount_per_unit_inr: Decimal
    raw_reference_nav: str
    reference_nav: Decimal
    raw_yield_percent: str
    yield_percent: Decimal
    is_positive_cash_distribution: bool


@dataclass(frozen=True)
class AdvisorkhojSchemeCapture:
    requested_url: str
    source_url: str
    captured_at: datetime
    amc_name: str
    requested_scheme_name: str
    source_scheme_name: str
    display_scheme_name: str
    plan_type: str
    option_variant: str
    source_frequency: str
    display_frequency: str
    frequency_conflict: bool
    source_payload_sha256: str
    source_payload: str
    rows: tuple[AdvisorkhojDistributionRow, ...]
    catalog_sha256: str | None = None


@dataclass(frozen=True)
class AdvisorkhojPage:
    source_scheme_name: str
    display_scheme_name: str
    plan_type: str
    option_variant: str
    source_frequency: str
    display_frequency: str
    frequency_conflict: bool
    rows: tuple[AdvisorkhojDistributionRow, ...]


@dataclass(frozen=True)
class AdvisorkhojCatalogScheme:
    amc_name: str
    category: str
    scheme_name: str


@dataclass(frozen=True)
class AdvisorkhojCatalog:
    captured_at: datetime
    source_url: str
    source_payload_sha256: str
    amcs: tuple[str, ...]
    category_queries: int
    schemes: tuple[AdvisorkhojCatalogScheme, ...]


class AdvisorkhojCaptureParser:
    """Strict parser for source-containing Advisorkhoj capture JSONL."""

    version = PARSER_VERSION

    def validate(self, payload: bytes) -> int:
        return sum(len(capture.rows) for capture in self.parse_records(payload))

    def parse_records(self, payload: bytes) -> tuple[AdvisorkhojSchemeCapture, ...]:
        text = _decode_utf8(payload, "Advisorkhoj capture")
        captures: list[AdvisorkhojSchemeCapture] = []
        seen: set[tuple[str, datetime]] = set()
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError as error:
                raise SourceParseError(
                    f"Advisorkhoj capture line {line_number} is invalid JSON"
                ) from error
            capture = self._parse_capture(value, line_number)
            key = (capture.source_url, capture.captured_at)
            if key in seen:
                raise SourceParseError(f"duplicate Advisorkhoj page capture at line {line_number}")
            seen.add(key)
            captures.append(capture)
        if not captures:
            raise SourceParseError("Advisorkhoj capture contains no scheme pages")
        catalog_hashes = {capture.catalog_sha256 for capture in captures}
        if len(catalog_hashes) != 1:
            raise SourceParseError("one Advisorkhoj capture cannot mix catalog snapshots")
        return tuple(captures)

    def _parse_capture(self, value: object, line_number: int) -> AdvisorkhojSchemeCapture:
        if isinstance(value, dict) and value.get("schema_version") == API_CAPTURE_SCHEMA_VERSION:
            return self._parse_api_capture(value, line_number)
        expected = {
            "schema_version",
            "provider",
            "requested_url",
            "source_url",
            "captured_at",
            "amc_name",
            "requested_scheme_name",
            "source_payload_sha256",
            "source_payload",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SourceParseError(
                f"Advisorkhoj capture line {line_number} has an unexpected field set"
            )
        if value["schema_version"] != CAPTURE_SCHEMA_VERSION:
            raise SourceParseError(f"unsupported Advisorkhoj capture schema at line {line_number}")
        if value["provider"] != "advisorkhoj":
            raise SourceParseError(
                f"Advisorkhoj capture line {line_number} has an invalid provider"
            )
        requested_url = _source_url(value["requested_url"], "requested_url", line_number)
        source_url = _source_url(value["source_url"], "source_url", line_number)
        requested_scheme_name = _required_string(
            value["requested_scheme_name"], "requested_scheme_name", line_number
        )
        requested_path_name = unquote(
            urlparse(requested_url).path.removeprefix(HISTORICAL_DISTRIBUTIONS_PATH)
        )
        if _space(requested_path_name).casefold() != requested_scheme_name.casefold():
            raise SourceParseError(
                f"Advisorkhoj requested URL/scheme mismatch at line {line_number}"
            )
        source_payload = _required_string(
            value["source_payload"], "source_payload", line_number, strip=False
        )
        expected_sha256 = _required_string(
            value["source_payload_sha256"], "source_payload_sha256", line_number
        )
        actual_sha256 = hashlib.sha256(source_payload.encode("utf-8")).hexdigest()
        if expected_sha256 != actual_sha256:
            raise SourceParseError(
                f"Advisorkhoj source payload checksum mismatch at line {line_number}"
            )
        page = parse_historical_distributions_html(source_payload)
        if page.source_scheme_name.casefold() != requested_scheme_name.casefold():
            raise SourceParseError(
                f"Advisorkhoj rendered/requested scheme mismatch at line {line_number}: "
                f"{page.source_scheme_name!r} != {requested_scheme_name!r}"
            )
        return AdvisorkhojSchemeCapture(
            requested_url=requested_url,
            source_url=source_url,
            captured_at=_aware_datetime(value["captured_at"], line_number),
            amc_name=_required_string(value["amc_name"], "amc_name", line_number),
            requested_scheme_name=requested_scheme_name,
            source_scheme_name=page.source_scheme_name,
            display_scheme_name=page.display_scheme_name,
            plan_type=page.plan_type,
            option_variant=page.option_variant,
            source_frequency=page.source_frequency,
            display_frequency=page.display_frequency,
            frequency_conflict=page.frequency_conflict,
            source_payload_sha256=expected_sha256,
            source_payload=source_payload,
            rows=page.rows,
        )

    def _parse_api_capture(
        self, value: dict[object, object], line_number: int
    ) -> AdvisorkhojSchemeCapture:
        expected = {
            "schema_version",
            "provider",
            "catalog_sha256",
            "source_url",
            "captured_at",
            "amc_name",
            "category",
            "requested_scheme_name",
            "source_payload_sha256",
            "source_payload",
        }
        if set(value) != expected:
            raise SourceParseError(
                f"Advisorkhoj API capture line {line_number} has an unexpected field set"
            )
        if value["provider"] != "advisorkhoj":
            raise SourceParseError(
                f"Advisorkhoj API capture line {line_number} has an invalid provider"
            )
        catalog_sha256 = _sha256_string(value["catalog_sha256"], "catalog_sha256", line_number)
        source_url = _exact_api_url(value["source_url"], SCHEME_DETAILS_PATH, line_number)
        source_payload = _required_string(
            value["source_payload"], "source_payload", line_number, strip=False
        )
        expected_sha256 = _sha256_string(
            value["source_payload_sha256"], "source_payload_sha256", line_number
        )
        actual_sha256 = hashlib.sha256(source_payload.encode("utf-8")).hexdigest()
        if expected_sha256 != actual_sha256:
            raise SourceParseError(
                f"Advisorkhoj source payload checksum mismatch at line {line_number}"
            )
        scheme_name = _required_string(
            value["requested_scheme_name"], "requested_scheme_name", line_number
        )
        rows = parse_distribution_details_json(source_payload)
        return AdvisorkhojSchemeCapture(
            requested_url=source_url,
            source_url=source_url,
            captured_at=_aware_datetime(value["captured_at"], line_number),
            amc_name=_required_string(value["amc_name"], "amc_name", line_number),
            requested_scheme_name=scheme_name,
            source_scheme_name=scheme_name,
            display_scheme_name=scheme_name,
            plan_type=_classify_plan(scheme_name),
            option_variant=_classify_variant(scheme_name),
            source_frequency=_classify_frequency(scheme_name),
            display_frequency="unknown",
            frequency_conflict=False,
            source_payload_sha256=expected_sha256,
            source_payload=source_payload,
            rows=rows,
            catalog_sha256=catalog_sha256,
        )


class _HistoricalDistributionPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.display_names: list[str] = []
        self.tables_found = 0
        self.table_rows: list[list[str]] = []
        self._heading_depth = 0
        self._heading_parts: list[str] = []
        self._table_depth = 0
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "h1" and {"panel-title", "ak-bold"}.issubset(classes):
            self._heading_depth = 1
            self._heading_parts = []
        elif self._heading_depth:
            self._heading_depth += 1

        if tag == "table" and values.get("id") == "tbl_dividend_history":
            self.tables_found += 1
            self._table_depth = 1
        elif self._table_depth and tag == "table":
            self._table_depth += 1
        elif self._table_depth == 1 and tag == "tr":
            self._current_row = []
        elif self._table_depth == 1 and tag in {"td", "th"}:
            if self._current_row is None:
                raise SourceParseError("Advisorkhoj distribution cell appears outside a row")
            self._current_cell = []
        elif self._table_depth == 1 and tag == "br" and self._current_cell is not None:
            self._current_cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._heading_depth:
            self._heading_parts.append(data)
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._heading_depth:
            self._heading_depth -= 1
            if self._heading_depth == 0 and tag == "h1":
                heading = _space("".join(self._heading_parts))
                prefix = "Historical Dividends of "
                if heading.startswith(prefix):
                    self.display_names.append(heading.removeprefix(prefix).strip())

        if not self._table_depth:
            return
        if self._table_depth == 1 and tag in {"td", "th"}:
            if self._current_cell is None or self._current_row is None:
                raise SourceParseError("Advisorkhoj distribution table has malformed cells")
            self._current_row.append(_space("".join(self._current_cell)))
            self._current_cell = None
        elif self._table_depth == 1 and tag == "tr":
            if self._current_row is None:
                raise SourceParseError("Advisorkhoj distribution table has a malformed row")
            if any(self._current_row):
                self.table_rows.append(self._current_row)
            self._current_row = None
        elif tag == "table":
            self._table_depth -= 1


class _AmcCatalogPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.selects_found = 0
        self.amcs: list[str] = []
        self._in_select = False
        self._option_parts: list[str] | None = None
        self._option_value: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "select" and values.get("id") == "sel_amcCompanies":
            self.selects_found += 1
            self._in_select = True
        elif self._in_select and tag == "option":
            self._option_parts = []
            self._option_value = values.get("value")

    def handle_data(self, data: str) -> None:
        if self._option_parts is not None:
            self._option_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._in_select and tag == "option":
            if self._option_parts is None:
                raise SourceParseError("Advisorkhoj AMC catalog has a malformed option")
            label = _space("".join(self._option_parts))
            value = _space(self._option_value or "")
            if label and value:
                if label != value:
                    raise SourceParseError(
                        f"Advisorkhoj AMC option label/value mismatch: {label!r} != {value!r}"
                    )
                self.amcs.append(value)
            self._option_parts = None
            self._option_value = None
        elif self._in_select and tag == "select":
            self._in_select = False


def parse_amc_catalog_html(payload: str) -> tuple[str, ...]:
    parser = _AmcCatalogPageParser()
    try:
        parser.feed(payload)
        parser.close()
    except SourceParseError:
        raise
    except Exception as error:
        raise SourceParseError("Advisorkhoj AMC catalog HTML is malformed") from error
    if parser.selects_found != 1:
        raise SourceParseError("Advisorkhoj AMC catalog must contain exactly one AMC select")
    if not parser.amcs:
        raise SourceParseError("Advisorkhoj AMC catalog contains no AMCs")
    if len(parser.amcs) != len(set(parser.amcs)):
        raise SourceParseError("Advisorkhoj AMC catalog contains duplicate AMCs")
    return tuple(parser.amcs)


def parse_catalog(payload: bytes) -> AdvisorkhojCatalog:
    text = _decode_utf8(payload, "Advisorkhoj catalog")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise SourceParseError("Advisorkhoj catalog is invalid JSON") from error
    expected = {
        "schema_version",
        "provider",
        "captured_at",
        "source_url",
        "source_payload_sha256",
        "source_payload",
        "queries",
        "schemes",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise SourceParseError("Advisorkhoj catalog has an unexpected field set")
    if value["schema_version"] != API_CAPTURE_SCHEMA_VERSION:
        raise SourceParseError("unsupported Advisorkhoj catalog schema")
    if value["provider"] != "advisorkhoj":
        raise SourceParseError("Advisorkhoj catalog has an invalid provider")
    source_url = _exact_api_url(value["source_url"], AMC_DISTRIBUTIONS_PATH, 1)
    source_payload = _required_string(value["source_payload"], "source_payload", 1, strip=False)
    source_sha256 = _sha256_string(value["source_payload_sha256"], "source_payload_sha256", 1)
    if hashlib.sha256(source_payload.encode("utf-8")).hexdigest() != source_sha256:
        raise SourceParseError("Advisorkhoj AMC catalog source payload checksum mismatch")
    amcs = parse_amc_catalog_html(source_payload)
    queries = value["queries"]
    if not isinstance(queries, list) or len(queries) != len(amcs):
        raise SourceParseError("Advisorkhoj catalog must contain one query record per AMC")
    derived_schemes: list[AdvisorkhojCatalogScheme] = []
    category_queries = 0
    seen_amcs: set[str] = set()
    for query_number, query in enumerate(queries, start=1):
        query_expected = {
            "amc_name",
            "categories_payload_sha256",
            "categories_payload",
            "scheme_queries",
        }
        if not isinstance(query, dict) or set(query) != query_expected:
            raise SourceParseError(
                f"Advisorkhoj catalog AMC query {query_number} has an unexpected field set"
            )
        amc_name = _required_string(query["amc_name"], "amc_name", query_number)
        if amc_name not in amcs or amc_name in seen_amcs:
            raise SourceParseError(
                f"Advisorkhoj catalog AMC query {query_number} is unknown or duplicated"
            )
        seen_amcs.add(amc_name)
        categories_payload = _required_string(
            query["categories_payload"], "categories_payload", query_number, strip=False
        )
        _verify_embedded_sha256(
            categories_payload,
            query["categories_payload_sha256"],
            "categories payload",
            query_number,
        )
        categories = _string_array(categories_payload, f"AMC {amc_name} categories")
        scheme_queries = query["scheme_queries"]
        if not isinstance(scheme_queries, list) or len(scheme_queries) != len(categories):
            raise SourceParseError(
                f"Advisorkhoj catalog AMC {amc_name!r} must query every category"
            )
        seen_categories: set[str] = set()
        for scheme_query_number, scheme_query in enumerate(scheme_queries, start=1):
            if not isinstance(scheme_query, dict) or set(scheme_query) != {
                "category",
                "source_payload_sha256",
                "source_payload",
            }:
                raise SourceParseError(
                    f"Advisorkhoj scheme query {query_number}.{scheme_query_number} "
                    "has an unexpected field set"
                )
            category = _required_string(scheme_query["category"], "category", scheme_query_number)
            if category not in categories or category in seen_categories:
                raise SourceParseError(
                    f"Advisorkhoj category query {amc_name!r}/{category!r} is unknown or duplicated"
                )
            seen_categories.add(category)
            scheme_payload = _required_string(
                scheme_query["source_payload"],
                "source_payload",
                scheme_query_number,
                strip=False,
            )
            _verify_embedded_sha256(
                scheme_payload,
                scheme_query["source_payload_sha256"],
                "scheme-list payload",
                scheme_query_number,
            )
            for scheme_name in _string_array(
                scheme_payload, f"AMC {amc_name} category {category} schemes"
            ):
                derived_schemes.append(AdvisorkhojCatalogScheme(amc_name, category, scheme_name))
            category_queries += 1
    if seen_amcs != set(amcs):
        raise SourceParseError("Advisorkhoj catalog did not query every listed AMC")
    if len(derived_schemes) != len({(item.amc_name, item.scheme_name) for item in derived_schemes}):
        raise SourceParseError("Advisorkhoj catalog assigns a scheme to multiple categories")
    schemes = value["schemes"]
    if not isinstance(schemes, list):
        raise SourceParseError("Advisorkhoj catalog schemes must be a list")
    declared_schemes: list[AdvisorkhojCatalogScheme] = []
    for item_number, item in enumerate(schemes, start=1):
        if not isinstance(item, dict) or set(item) != {"amc_name", "category", "scheme_name"}:
            raise SourceParseError(
                f"Advisorkhoj declared catalog scheme {item_number} has an unexpected field set"
            )
        declared_schemes.append(
            AdvisorkhojCatalogScheme(
                _required_string(item["amc_name"], "amc_name", item_number),
                _required_string(item["category"], "category", item_number),
                _required_string(item["scheme_name"], "scheme_name", item_number),
            )
        )
    if declared_schemes != derived_schemes:
        raise SourceParseError("Advisorkhoj declared schemes do not match raw catalog responses")
    return AdvisorkhojCatalog(
        captured_at=_aware_datetime(value["captured_at"], 1),
        source_url=source_url,
        source_payload_sha256=source_sha256,
        amcs=amcs,
        category_queries=category_queries,
        schemes=tuple(declared_schemes),
    )


def parse_distribution_details_json(payload: str) -> tuple[AdvisorkhojDistributionRow, ...]:
    try:
        value = json.loads(payload, parse_float=Decimal, parse_int=Decimal)
    except (json.JSONDecodeError, InvalidOperation) as error:
        raise SourceParseError("Advisorkhoj distribution details are invalid JSON") from error
    # The catalog includes some schemes for which this endpoint returns JSON null rather than [];
    # retain that observed source convention as an explicitly empty history.
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SourceParseError("Advisorkhoj distribution details must be a list")
    rows: list[AdvisorkhojDistributionRow] = []
    for row_number, item in enumerate(value, start=1):
        if not isinstance(item, dict) or set(item) != {
            "dividend_date",
            "net_asset_value",
            "dividend_value",
            "dividend_yield",
        }:
            raise SourceParseError(
                f"Advisorkhoj distribution detail row {row_number} has an unexpected field set"
            )
        date_text = item["dividend_date"]
        if not isinstance(date_text, str):
            raise SourceParseError(
                f"Advisorkhoj distribution detail date at row {row_number} must be a string"
            )
        try:
            record_date = datetime.strptime(date_text, "%d-%m-%Y").date()
        except ValueError as error:
            raise SourceParseError(
                f"invalid Advisorkhoj record date at row {row_number}: {date_text!r}"
            ) from error
        amount = _json_decimal(item["dividend_value"], "distribution amount", row_number)
        nav = _json_decimal(item["net_asset_value"], "reference NAV", row_number)
        source_yield = _json_decimal(item["dividend_yield"], "dividend yield", row_number)
        if nav < 0:
            raise SourceParseError(
                f"Advisorkhoj reference NAV cannot be negative at row {row_number}"
            )
        computed_yield = amount / nav * Decimal("100") if nav > 0 else None
        if computed_yield is not None and abs(computed_yield - source_yield) > Decimal(
            "0.0000000001"
        ):
            raise SourceParseError(
                f"Advisorkhoj dividend yield mismatch at row {row_number}: source "
                f"{source_yield}, computed {computed_yield}"
            )
        rows.append(
            AdvisorkhojDistributionRow(
                record_date=record_date,
                raw_amount_per_unit_inr=str(amount),
                amount_per_unit_inr=amount,
                raw_reference_nav=str(nav),
                reference_nav=nav,
                raw_yield_percent=str(source_yield),
                yield_percent=source_yield,
                is_positive_cash_distribution=amount > 0,
            )
        )
    dates = [row.record_date for row in rows]
    if len(dates) != len(set(dates)):
        raise SourceParseError("Advisorkhoj distribution details contain duplicate record dates")
    if dates != sorted(dates, reverse=True):
        raise SourceParseError(
            "Advisorkhoj distribution details are not ordered by descending record date"
        )
    return tuple(rows)


def parse_historical_distributions_html(payload: str) -> AdvisorkhojPage:
    parser = _HistoricalDistributionPageParser()
    try:
        parser.feed(payload)
        parser.close()
    except SourceParseError:
        raise
    except Exception as error:
        raise SourceParseError("Advisorkhoj historical-distribution HTML is malformed") from error
    if parser.tables_found != 1:
        raise SourceParseError(
            "Advisorkhoj page must contain exactly one historical-distribution table"
        )
    if len(set(parser.display_names)) != 1:
        raise SourceParseError(
            "Advisorkhoj page must contain one unambiguous historical-distribution heading"
        )
    literals = {_space(match.group("name")) for match in _SCHEME_LITERAL.finditer(payload)}
    if len(literals) != 1:
        raise SourceParseError("Advisorkhoj page must expose one unambiguous source scheme name")
    if not parser.table_rows:
        raise SourceParseError("Advisorkhoj historical-distribution table has no header")
    header = tuple(_header(value) for value in parser.table_rows[0])
    if header != _EXPECTED_HEADERS:
        raise SourceParseError(
            f"unexpected Advisorkhoj distribution header: {parser.table_rows[0]!r}"
        )
    rows = tuple(
        _parse_distribution_row(raw_row, row_number)
        for row_number, raw_row in enumerate(parser.table_rows[1:], start=1)
    )
    dates = [row.record_date for row in rows]
    if len(dates) != len(set(dates)):
        raise SourceParseError("Advisorkhoj distribution table contains duplicate record dates")
    if dates != sorted(dates, reverse=True):
        raise SourceParseError(
            "Advisorkhoj distribution rows are not ordered by descending record date"
        )
    source_name = next(iter(literals))
    display_name = next(iter(set(parser.display_names)))
    source_frequency = _classify_frequency(source_name)
    display_frequency = _classify_frequency(display_name)
    return AdvisorkhojPage(
        source_scheme_name=source_name,
        display_scheme_name=display_name,
        plan_type=_classify_plan(source_name, display_name),
        option_variant=_classify_variant(source_name, display_name),
        source_frequency=source_frequency,
        display_frequency=display_frequency,
        frequency_conflict=(
            source_frequency != "unknown"
            and display_frequency != "unknown"
            and source_frequency != display_frequency
        ),
        rows=rows,
    )


def _parse_distribution_row(raw_row: list[str], row_number: int) -> AdvisorkhojDistributionRow:
    if len(raw_row) != len(_EXPECTED_HEADERS):
        raise SourceParseError(
            f"Advisorkhoj distribution row {row_number} has {len(raw_row)} columns; expected 4"
        )
    date_text, amount_text, nav_text, yield_text = raw_row
    try:
        record_date = datetime.strptime(date_text, "%d-%m-%Y").date()
    except ValueError as error:
        raise SourceParseError(
            f"invalid Advisorkhoj record date at row {row_number}: {date_text!r}"
        ) from error
    amount = _nonnegative_decimal(amount_text, "distribution amount", row_number)
    nav = _positive_decimal(nav_text, "reference NAV", row_number)
    yield_match = re.fullmatch(r"(?P<value>(?:0|[1-9]\d*)(?:\.\d+)?)%", yield_text)
    if yield_match is None:
        raise SourceParseError(
            f"invalid Advisorkhoj dividend yield at row {row_number}: {yield_text!r}"
        )
    yield_percent = _decimal(yield_match.group("value"), "dividend yield", row_number)
    expected_yield = (amount / nav * Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if yield_percent != expected_yield:
        raise SourceParseError(
            f"Advisorkhoj dividend yield mismatch at row {row_number}: displayed "
            f"{yield_percent}% but amount/NAV rounds to {expected_yield}%"
        )
    return AdvisorkhojDistributionRow(
        record_date=record_date,
        raw_amount_per_unit_inr=amount_text,
        amount_per_unit_inr=amount,
        raw_reference_nav=nav_text,
        reference_nav=nav,
        raw_yield_percent=yield_text,
        yield_percent=yield_percent,
        is_positive_cash_distribution=amount > 0,
    )


def _classify_plan(*names: str) -> str:
    normalized = f" {' '.join(names).casefold()} "
    direct = " direct " in normalized or " dir " in normalized
    regular = " regular " in normalized or " reg " in normalized
    if direct and regular:
        return "unknown"
    if direct:
        return "direct"
    if regular:
        return "regular"
    return "unknown"


def _classify_variant(*names: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", " ".join(names).casefold())
    reinvestment = any(token.startswith("reinvest") or token == "reinv" for token in tokens)
    payout = any(token in {"pay", "payment", "payout"} for token in tokens)
    if reinvestment and payout:
        return "mixed"
    if reinvestment:
        return "reinvestment"
    if payout:
        return "payout"
    return "unknown"


def _classify_frequency(name: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", name.casefold())
    joined = " ".join(tokens)
    frequencies: set[str] = set()
    token_mapping = {
        "daily": "daily",
        "fortn": "fortnightly",
        "fortnightly": "fortnightly",
        "hly": "half_yearly",
        "monthly": "monthly",
        "mly": "monthly",
        "qly": "quarterly",
        "quarterly": "quarterly",
        "weekly": "weekly",
        "wly": "weekly",
        "annual": "annual",
        "annually": "annual",
    }
    frequencies.update(token_mapping[token] for token in tokens if token in token_mapping)
    if "half yearly" in joined or "halfyearly" in joined:
        frequencies.add("half_yearly")
    if len(frequencies) > 1:
        return "multiple"
    return next(iter(frequencies), "unknown")


def _source_url(value: object, label: str, line_number: int) -> str:
    source_url = _required_string(value, label, line_number)
    parsed = urlparse(source_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ADVISORKHOJ_HOST
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith(HISTORICAL_DISTRIBUTIONS_PATH)
    ):
        raise SourceParseError(
            f"Advisorkhoj {label} is not an approved historical-distribution URL "
            f"at line {line_number}"
        )
    return source_url


def _exact_api_url(value: object, path: str, line_number: int) -> str:
    source_url = _required_string(value, "source_url", line_number)
    parsed = urlparse(source_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ADVISORKHOJ_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise SourceParseError(
            f"Advisorkhoj source_url is not the expected API URL at line {line_number}"
        )
    return source_url


def _aware_datetime(value: object, line_number: int) -> datetime:
    if not isinstance(value, str):
        raise SourceParseError(f"Advisorkhoj captured_at must be a string at line {line_number}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SourceParseError(f"invalid Advisorkhoj captured_at at line {line_number}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceParseError(
            f"Advisorkhoj captured_at must include a timezone at line {line_number}"
        )
    return parsed


def _required_string(value: object, label: str, line_number: int, *, strip: bool = True) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceParseError(f"Advisorkhoj {label} must be non-empty at line {line_number}")
    return value.strip() if strip else value


def _sha256_string(value: object, label: str, line_number: int) -> str:
    parsed = _required_string(value, label, line_number)
    if not re.fullmatch(r"[0-9a-f]{64}", parsed):
        raise SourceParseError(
            f"Advisorkhoj {label} must be a lowercase SHA-256 at line {line_number}"
        )
    return parsed


def _verify_embedded_sha256(payload: str, expected: object, label: str, line_number: int) -> None:
    expected_sha256 = _sha256_string(expected, f"{label}_sha256", line_number)
    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != expected_sha256:
        raise SourceParseError(f"Advisorkhoj {label} checksum mismatch at line {line_number}")


def _string_array(payload: str, label: str) -> tuple[str, ...]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise SourceParseError(f"Advisorkhoj {label} response is invalid JSON") from error
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise SourceParseError(f"Advisorkhoj {label} response must be a string array")
    normalized = tuple(item.strip() for item in value)
    if len(normalized) != len(set(normalized)):
        raise SourceParseError(f"Advisorkhoj {label} response contains duplicates")
    return normalized


def _json_decimal(value: object, label: str, row_number: int) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise SourceParseError(
            f"Advisorkhoj {label} must be a finite JSON number at row {row_number}"
        )
    return value


def _positive_decimal(value: str, label: str, row_number: int) -> Decimal:
    parsed = _decimal(value, label, row_number)
    if parsed <= 0:
        raise SourceParseError(f"Advisorkhoj {label} must be positive at row {row_number}")
    return parsed


def _nonnegative_decimal(value: str, label: str, row_number: int) -> Decimal:
    parsed = _decimal(value, label, row_number)
    if parsed < 0:
        raise SourceParseError(f"Advisorkhoj {label} cannot be negative at row {row_number}")
    return parsed


def _decimal(value: str, label: str, row_number: int) -> Decimal:
    if not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d+)?", value):
        raise SourceParseError(f"invalid Advisorkhoj {label} at row {row_number}: {value!r}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise SourceParseError(
            f"invalid Advisorkhoj {label} at row {row_number}: {value!r}"
        ) from error
    if not parsed.is_finite():
        raise SourceParseError(f"invalid Advisorkhoj {label} at row {row_number}: {value!r}")
    return parsed


def _header(value: str) -> str:
    return _space(re.sub(r"[^a-z0-9]+", " ", value.casefold()))


def _space(value: str) -> str:
    return " ".join(value.split())


def _decode_utf8(payload: bytes, label: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceParseError(f"{label} is not valid UTF-8") from error

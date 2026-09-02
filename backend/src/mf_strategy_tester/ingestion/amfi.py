import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlencode

from mf_strategy_tester.ingestion.errors import (
    SourceParseError,
    SourceTemporarilyUnavailableError,
)

PARSER_VERSION = "amfi-2026.09.1"
DISTRIBUTION_PERCENTAGE_THROUGH = date(2009, 4, 6)
_DISTRIBUTION_COMPOSITE_VALUE = re.compile(
    r"(?P<percentage>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))%\s+"
    r"\(Rs\s+(?P<amount>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))/-\s+Per Unit\)?",
    flags=re.IGNORECASE,
)
_DISTRIBUTION_DASH_COMPOSITE_VALUE = re.compile(
    r"(?P<percentage>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))%\s*-\s*"
    r"Rs\s+(?P<amount>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))\s+Per Unit",
    flags=re.IGNORECASE,
)
CURRENT_NAV_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
HISTORICAL_NAV_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
AMFI_API_URL = "https://www.amfiindia.com/api"
AMFI_FUND_CATALOG_URL = "https://www.amfiindia.com/otherdata/scheme-details"

CURRENT_NAV_HEADER = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
    "Scheme Name;Net Asset Value;Date"
)
CURRENT_NAV_HEADER_WITH_QUALIFIERS = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
    "Scheme Name;Plan;Option;Net Asset Value;Date"
)
HISTORICAL_NAV_HEADER = (
    "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
    "Net Asset Value;Repurchase Price;Sale Price;Date"
)
HISTORICAL_NAV_HEADER_WITH_QUALIFIERS = (
    "Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;"
    "ISIN Div Reinvestment;Net Asset Value;Date"
)


class AmfiSourceType(StrEnum):
    FUND_LIST = "fund_list"
    CURRENT_NAV = "current_nav"
    HISTORICAL_NAV = "historical_nav"
    SCHEME_LIST = "scheme_list"
    SCHEME_DETAILS = "scheme_details"
    DISTRIBUTIONS = "distributions"


class SchemeType(StrEnum):
    ALL = "all"
    OPEN_ENDED = "open_ended"
    CLOSE_ENDED = "close_ended"
    INTERVAL = "interval"


class ArtifactParser(Protocol):
    version: str

    def validate(self, payload: bytes) -> int: ...


@dataclass(frozen=True)
class AmfiSourceRequest:
    source_type: AmfiSourceType
    url: str
    parameters: dict[str, str]
    parser: ArtifactParser
    provider: str = field(default="amfi", init=False)


@dataclass(frozen=True)
class NavSourceRecord:
    scheme_code: str
    scheme_name: str
    isin_payout_or_growth: str | None
    isin_reinvestment: str | None
    nav: Decimal
    nav_date: date
    scheme_classification: str
    fund_house: str
    source_plan: str | None = None
    source_option: str | None = None


@dataclass(frozen=True)
class InvalidNavSourceRecord:
    scheme_code: str
    scheme_name: str
    isin_payout_or_growth: str | None
    isin_reinvestment: str | None
    raw_nav_value: str
    nav_date: date
    scheme_classification: str
    fund_house: str
    rejection_reason: str
    source_plan: str | None = None
    source_option: str | None = None


NavParsedRecord = NavSourceRecord | InvalidNavSourceRecord


@dataclass(frozen=True)
class _NavFieldMap:
    scheme_code: int
    scheme_name: int
    payout_isin: int
    reinvestment_isin: int
    nav: int
    nav_date: int
    plan: int | None = None
    option: int | None = None


_NAV_FIELD_MAPS = {
    CURRENT_NAV_HEADER: _NavFieldMap(0, 3, 1, 2, 4, 5),
    CURRENT_NAV_HEADER_WITH_QUALIFIERS: _NavFieldMap(0, 3, 1, 2, 6, 7, 4, 5),
    HISTORICAL_NAV_HEADER: _NavFieldMap(0, 1, 2, 3, 4, 7),
    HISTORICAL_NAV_HEADER_WITH_QUALIFIERS: _NavFieldMap(0, 1, 4, 5, 6, 7, 2, 3),
}


@dataclass(frozen=True)
class FundSourceRecord:
    mutual_fund_id: str
    mutual_fund_name: str


@dataclass(frozen=True)
class SchemeListRecord:
    scheme_id: str
    scheme_name: str


@dataclass(frozen=True)
class SchemeDetailRecord:
    mutual_fund_id: str
    scheme_id: str
    mutual_fund_name: str
    scheme_name: str
    scheme_type: str
    scheme_category: str
    launch_date: datetime | None


@dataclass(frozen=True)
class DistributionSourceRecord:
    mutual_fund_id: str
    source_option_id: str
    source_scheme_id: str
    scheme_name: str
    nav_name: str
    record_date: date
    raw_source_value: str
    source_value: Decimal | None
    ratio_numerator: int | None
    ratio_denominator: int | None
    annotated_amount_per_unit_inr: Decimal | None
    source_record_signature: str
    source_plan: str | None = None
    source_option: str | None = None


@dataclass(frozen=True)
class DistributionParseIssue:
    record_number: int
    issue_code: str
    error_details: str
    raw_record: Any
    source_record_signature: str


@dataclass(frozen=True)
class DistributionParseResult:
    records: tuple[DistributionSourceRecord, ...]
    issues: tuple[DistributionParseIssue, ...]

    @property
    def rows_received(self) -> int:
        return len(self.records) + len(self.issues)


class AmfiNavParser:
    version = PARSER_VERSION

    def __init__(self, *, allow_empty_report: bool = False) -> None:
        self._allow_empty_report = allow_empty_report

    def validate(self, payload: bytes) -> int:
        return len(self.parse_records(payload))

    def parse_records(self, payload: bytes) -> tuple[NavParsedRecord, ...]:
        text = _decode_utf8(payload)
        if _is_amfi_nav_application_error(text):
            raise SourceTemporarilyUnavailableError(
                "AMFI NAV endpoint returned its application-error page"
            )
        if "No data found on the basis of selected parameters for this report" in text:
            if self._allow_empty_report:
                return ()
            raise SourceParseError("AMFI NAV source returned a no-data report")
        lines = text.splitlines()
        header_index = next((index for index, line in enumerate(lines) if line.strip()), None)
        if header_index is None:
            raise SourceParseError("AMFI NAV source is empty")

        header = lines[header_index].strip()
        field_map = _NAV_FIELD_MAPS.get(header)
        if field_map is None:
            raise SourceParseError(f"unexpected AMFI NAV header at source line {header_index + 1}")

        records: list[NavParsedRecord] = []
        seen: set[tuple[str, date]] = set()
        classification: str | None = None
        fund_house: str | None = None
        for index, raw_line in enumerate(lines[header_index + 1 :], start=header_index + 2):
            line = raw_line.strip()
            if not line:
                continue
            if ";" not in line:
                if _is_scheme_classification(line):
                    classification = line
                elif classification is not None:
                    fund_house = line
                else:
                    raise SourceParseError(f"unexpected NAV section text at source line {index}")
                continue

            if classification is None or fund_house is None:
                raise SourceParseError(
                    f"NAV row has no classification/fund house at source line {index}"
                )
            fields = [field.strip() for field in line.split(";")]
            expected_fields = (
                max(
                    index
                    for index in (
                        field_map.scheme_code,
                        field_map.scheme_name,
                        field_map.payout_isin,
                        field_map.reinvestment_isin,
                        field_map.nav,
                        field_map.nav_date,
                        field_map.plan or 0,
                        field_map.option or 0,
                    )
                )
                + 1
            )
            if len(fields) != expected_fields:
                raise SourceParseError(
                    f"AMFI NAV parser expected {expected_fields} fields but received "
                    f"{len(fields)} at source line {index}"
                )
            record = self._parse_row(fields, index, field_map, classification, fund_house)
            key = (record.scheme_code, record.nav_date)
            if key in seen:
                raise SourceParseError(
                    f"duplicate scheme/date {record.scheme_code}/{record.nav_date} "
                    f"at source line {index}"
                )
            seen.add(key)
            records.append(record)

        if not records:
            raise SourceParseError("AMFI NAV source contains no data rows")
        return tuple(records)

    @staticmethod
    def _parse_row(
        fields: list[str],
        line_number: int,
        field_map: _NavFieldMap,
        classification: str,
        fund_house: str,
    ) -> NavParsedRecord:
        scheme_code = fields[field_map.scheme_code]
        name = fields[field_map.scheme_name]
        payout = fields[field_map.payout_isin]
        reinvestment = fields[field_map.reinvestment_isin]
        nav_text = fields[field_map.nav]
        date_text = fields[field_map.nav_date]
        source_plan = fields[field_map.plan] or None if field_map.plan is not None else None
        source_option = fields[field_map.option] or None if field_map.option is not None else None
        if not scheme_code.isdigit():
            raise SourceParseError(f"invalid scheme code at source line {line_number}")
        if not name:
            raise SourceParseError(f"empty scheme name at source line {line_number}")
        payout_isin = _parse_isin(payout, line_number)
        reinvestment_isin = _parse_isin(reinvestment, line_number)
        try:
            nav_date = datetime.strptime(date_text, "%d-%b-%Y").date()
        except ValueError as error:
            raise SourceParseError(f"invalid NAV date at source line {line_number}") from error
        try:
            nav = Decimal(nav_text)
            if not nav.is_finite() or nav < 0:
                raise InvalidOperation
        except InvalidOperation:
            return InvalidNavSourceRecord(
                scheme_code=scheme_code,
                scheme_name=name,
                isin_payout_or_growth=payout_isin,
                isin_reinvestment=reinvestment_isin,
                raw_nav_value=nav_text,
                nav_date=nav_date,
                scheme_classification=classification,
                fund_house=fund_house,
                rejection_reason="NAV is not a non-negative finite decimal",
                source_plan=source_plan,
                source_option=source_option,
            )
        return NavSourceRecord(
            scheme_code=scheme_code,
            scheme_name=name,
            isin_payout_or_growth=payout_isin,
            isin_reinvestment=reinvestment_isin,
            nav=nav,
            nav_date=nav_date,
            scheme_classification=classification,
            fund_house=fund_house,
            source_plan=source_plan,
            source_option=source_option,
        )


class AmfiFundListParser:
    version = PARSER_VERSION
    _record_pattern = re.compile(
        r'\\"mf_id\\":\\"(?P<id>\d+)\\",\\"mf_name\\":\\"(?P<name>.*?)\\",'
        r'\\"amc_name\\":'
    )

    def validate(self, payload: bytes) -> int:
        return len(self.parse_records(payload))

    def parse_records(self, payload: bytes) -> tuple[FundSourceRecord, ...]:
        text = _decode_utf8(payload)
        records: dict[str, FundSourceRecord] = {}
        for match in self._record_pattern.finditer(text):
            mutual_fund_id = match.group("id")
            try:
                mutual_fund_name = json.loads(f'"{match.group("name")}"')
            except json.JSONDecodeError as error:
                raise SourceParseError(
                    f"invalid escaped mutual-fund name for AMFI fund {mutual_fund_id}"
                ) from error
            record = FundSourceRecord(mutual_fund_id, mutual_fund_name.strip())
            existing = records.get(mutual_fund_id)
            if existing is not None and existing != record:
                raise SourceParseError(f"conflicting fund names for AMFI fund {mutual_fund_id}")
            records[mutual_fund_id] = record
        if len(records) < 10:
            raise SourceParseError(
                f"AMFI fund catalog structure changed; found only {len(records)} fund records"
            )
        return tuple(sorted(records.values(), key=lambda record: int(record.mutual_fund_id)))


class AmfiSchemeListParser:
    version = PARSER_VERSION

    def validate(self, payload: bytes) -> int:
        return len(self.parse_records(payload))

    def parse_records(self, payload: bytes) -> tuple[SchemeListRecord, ...]:
        value = _parse_json(payload)
        if not isinstance(value, list):
            raise SourceParseError("AMFI scheme list must be a JSON array")
        records: list[SchemeListRecord] = []
        seen: set[str] = set()
        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict) or set(item) != {"scheme_id", "scheme_name"}:
                raise SourceParseError(f"unexpected scheme-list structure at record {index}")
            scheme_id, scheme_name = item["scheme_id"], item["scheme_name"]
            if not isinstance(scheme_id, str) or not scheme_id.isdigit():
                raise SourceParseError(f"invalid scheme_id at record {index}")
            if not isinstance(scheme_name, str) or not scheme_name.strip():
                raise SourceParseError(f"invalid scheme_name at record {index}")
            if scheme_id in seen:
                raise SourceParseError(f"duplicate scheme_id {scheme_id} at record {index}")
            seen.add(scheme_id)
            records.append(SchemeListRecord(scheme_id, scheme_name.strip()))
        return tuple(records)


class AmfiSchemeDetailsParser:
    version = PARSER_VERSION
    _fields = frozenset(
        {
            "MF_Name",
            "Scheme_Name",
            "Scheme_Objective",
            "SchemeType_Desc",
            "SchemeCat_Desc",
            "Launch_Date",
            "Scheme_load",
            "Scheme_min_amt",
            "AMC_Website",
            "scheme_Id",
            "MF_Id",
        }
    )

    def validate(self, payload: bytes) -> int:
        return len(self.parse_records(payload))

    def parse_records(self, payload: bytes) -> tuple[SchemeDetailRecord, ...]:
        value = _parse_json(payload)
        data = value.get("data") if isinstance(value, dict) else None
        if not isinstance(data, list) or not data:
            raise SourceParseError("AMFI scheme details must contain a non-empty data array")
        records: list[SchemeDetailRecord] = []
        for index, item in enumerate(data, start=1):
            if not isinstance(item, dict) or set(item) != self._fields:
                raise SourceParseError(f"unexpected scheme-details structure at record {index}")
            mutual_fund_id = str(item["MF_Id"])
            scheme_id = str(item["scheme_Id"])
            if not mutual_fund_id.isdigit() or not scheme_id.isdigit():
                raise SourceParseError(f"invalid scheme-details identity at record {index}")
            raw_launch_date = item["Launch_Date"]
            launch_date: datetime | None = None
            if raw_launch_date is not None:
                if not isinstance(raw_launch_date, str) or not raw_launch_date.strip():
                    raise SourceParseError(f"invalid launch date at record {index}")
                try:
                    launch_date = datetime.fromisoformat(raw_launch_date)
                except ValueError as error:
                    raise SourceParseError(f"invalid launch date at record {index}") from error
                if launch_date.tzinfo is None or launch_date.utcoffset() is None:
                    raise SourceParseError(
                        f"launch date must include a timezone offset at record {index}"
                    )
                if launch_date.utcoffset() != timedelta(hours=5, minutes=30):
                    raise SourceParseError(
                        f"launch date must use the Asia/Kolkata UTC offset at record {index}"
                    )
            records.append(
                SchemeDetailRecord(
                    mutual_fund_id=mutual_fund_id,
                    scheme_id=scheme_id,
                    mutual_fund_name=_required_text(item["MF_Name"], "MF_Name", index),
                    scheme_name=_required_text(item["Scheme_Name"], "Scheme_Name", index),
                    scheme_type=_required_text(item["SchemeType_Desc"], "SchemeType_Desc", index),
                    scheme_category=_required_text(item["SchemeCat_Desc"], "SchemeCat_Desc", index),
                    launch_date=launch_date,
                )
            )
        return tuple(records)


class AmfiDistributionParser:
    version = PARSER_VERSION
    _legacy_fields = frozenset(
        {
            "MF_ID",
            "SD_ID",
            "scheme_id",
            "Scheme_Name",
            "Nav_name",
            "Div_year",
            "year",
            "Rate_of_div",
        }
    )
    _qualified_fields = _legacy_fields | {"Plan", "Option"}

    def validate(self, payload: bytes) -> int:
        return len(self.parse_records(payload))

    def parse_records(self, payload: bytes) -> tuple[DistributionSourceRecord, ...]:
        result = self.parse_with_issues(payload)
        if result.issues:
            raise SourceParseError(result.issues[0].error_details)
        return result.records

    def parse_with_issues(self, payload: bytes) -> DistributionParseResult:
        value = _parse_json(payload)
        data = value.get("data") if isinstance(value, dict) else None
        if not isinstance(data, list):
            raise SourceParseError("AMFI distribution response must contain a data array")
        records: list[DistributionSourceRecord] = []
        issues: list[DistributionParseIssue] = []
        seen: set[tuple[str, date]] = set()
        for index, item in enumerate(data, start=1):
            signature = _json_signature(item)
            try:
                record = self._parse_record(item, index, signature)
                key = (record.source_option_id, record.record_date)
                if key in seen:
                    raise SourceParseError(
                        f"duplicate distribution option/date "
                        f"{record.source_option_id}/{record.record_date} at record {index}"
                    )
            except SourceParseError as error:
                issues.append(
                    DistributionParseIssue(
                        record_number=index,
                        issue_code="SOURCE_PARSE_ERROR",
                        error_details=str(error),
                        raw_record=item,
                        source_record_signature=signature,
                    )
                )
                continue
            seen.add(key)
            records.append(record)
        return DistributionParseResult(records=tuple(records), issues=tuple(issues))

    def _parse_record(
        self, item: object, index: int, source_record_signature: str
    ) -> DistributionSourceRecord:
        if not isinstance(item, dict) or frozenset(item) not in {
            self._legacy_fields,
            self._qualified_fields,
        }:
            raise SourceParseError(f"unexpected distribution structure at record {index}")
        mutual_fund_id = _numeric_text(item["MF_ID"], "MF_ID", index)
        source_option_id = _numeric_text(item["SD_ID"], "SD_ID", index)
        source_scheme_id = _numeric_text(item["scheme_id"], "scheme_id", index)
        try:
            timestamp = datetime.fromisoformat(str(item["Div_year"]).replace("Z", "+00:00"))
        except ValueError as error:
            raise SourceParseError(f"invalid distribution date at record {index}") from error
        if (
            timestamp.tzinfo is None
            or timestamp.timetz().replace(tzinfo=None) != datetime.min.time()
        ):
            raise SourceParseError(
                f"distribution date must be timezone-aware midnight at record {index}"
            )
        record_date = timestamp.date()
        if str(item["year"]) != str(record_date.year):
            raise SourceParseError(f"distribution year mismatch at record {index}")
        raw_source_value = str(item["Rate_of_div"]).strip()
        source_plan = _optional_source_text(item.get("Plan"), "Plan", index)
        source_option = _optional_source_text(item.get("Option"), "Option", index)
        composite_match = _DISTRIBUTION_COMPOSITE_VALUE.fullmatch(
            raw_source_value
        ) or _DISTRIBUTION_DASH_COMPOSITE_VALUE.fullmatch(raw_source_value)
        ratio_match = re.fullmatch(r"([1-9][0-9]*):([1-9][0-9]*)", raw_source_value)
        if composite_match is not None:
            source_value = Decimal(composite_match.group("percentage"))
            ratio_numerator = None
            ratio_denominator = None
            annotated_amount_per_unit_inr = Decimal(composite_match.group("amount"))
            if annotated_amount_per_unit_inr <= 0:
                raise SourceParseError(f"invalid distribution value at record {index}")
        elif ratio_match is not None:
            source_value = None
            ratio_numerator = int(ratio_match.group(1))
            ratio_denominator = int(ratio_match.group(2))
            annotated_amount_per_unit_inr = None
        else:
            numeric_source_value = raw_source_value.removesuffix("%").strip()
            try:
                source_value = Decimal(numeric_source_value)
                if not source_value.is_finite() or source_value < 0:
                    raise InvalidOperation
            except InvalidOperation as error:
                raise SourceParseError(f"invalid distribution value at record {index}") from error
            ratio_numerator = None
            ratio_denominator = None
            annotated_amount_per_unit_inr = None
        return DistributionSourceRecord(
            mutual_fund_id=mutual_fund_id,
            source_option_id=source_option_id,
            source_scheme_id=source_scheme_id,
            scheme_name=_required_text(item["Scheme_Name"], "Scheme_Name", index),
            nav_name=_required_text(item["Nav_name"], "Nav_name", index),
            record_date=record_date,
            raw_source_value=raw_source_value,
            source_value=source_value,
            ratio_numerator=ratio_numerator,
            ratio_denominator=ratio_denominator,
            annotated_amount_per_unit_inr=annotated_amount_per_unit_inr,
            source_record_signature=source_record_signature,
            source_plan=source_plan,
            source_option=source_option,
        )


def current_nav_request() -> AmfiSourceRequest:
    return AmfiSourceRequest(
        source_type=AmfiSourceType.CURRENT_NAV,
        url=CURRENT_NAV_URL,
        parameters={},
        parser=AmfiNavParser(),
    )


def fund_list_request() -> AmfiSourceRequest:
    return AmfiSourceRequest(
        source_type=AmfiSourceType.FUND_LIST,
        url=AMFI_FUND_CATALOG_URL,
        parameters={},
        parser=AmfiFundListParser(),
    )


def historical_nav_request(
    *, mutual_fund_id: str, from_date: date, to_date: date, scheme_type: SchemeType
) -> AmfiSourceRequest:
    if to_date < from_date:
        raise ValueError("historical NAV to_date must be on or after from_date")
    if (to_date - from_date).days + 1 > 90:
        raise ValueError("AMFI historical NAV requests cannot exceed 90 calendar days")
    if mutual_fund_id != "all" and not mutual_fund_id.isdigit():
        raise ValueError("mutual_fund_id must be 'all' or numeric")
    type_parameter = {
        SchemeType.ALL: "",
        SchemeType.OPEN_ENDED: "1",
        SchemeType.CLOSE_ENDED: "2",
        SchemeType.INTERVAL: "3",
    }[scheme_type]
    parameters = {
        "mf": mutual_fund_id,
        "frmdt": from_date.strftime("%d-%b-%Y"),
        "todt": to_date.strftime("%d-%b-%Y"),
    }
    if type_parameter:
        parameters["tp"] = type_parameter
    return AmfiSourceRequest(
        source_type=AmfiSourceType.HISTORICAL_NAV,
        url=f"{HISTORICAL_NAV_URL}?{urlencode(parameters)}",
        parameters=parameters,
        parser=AmfiNavParser(allow_empty_report=True),
    )


def scheme_list_request(mutual_fund_id: str) -> AmfiSourceRequest:
    _validate_numeric_identifier(mutual_fund_id, "mutual_fund_id")
    parameters = {"MF_ID": mutual_fund_id}
    return AmfiSourceRequest(
        source_type=AmfiSourceType.SCHEME_LIST,
        url=f"{AMFI_API_URL}/populate-scheme?{urlencode(parameters)}",
        parameters=parameters,
        parser=AmfiSchemeListParser(),
    )


def scheme_details_request(mutual_fund_id: str, scheme_id: str) -> AmfiSourceRequest:
    _validate_numeric_identifier(mutual_fund_id, "mutual_fund_id")
    _validate_numeric_identifier(scheme_id, "scheme_id")
    parameters = {"MF_ID": mutual_fund_id, "scheme_id": scheme_id}
    return AmfiSourceRequest(
        source_type=AmfiSourceType.SCHEME_DETAILS,
        url=f"{AMFI_API_URL}/scheme-details?{urlencode(parameters)}",
        parameters=parameters,
        parser=AmfiSchemeDetailsParser(),
    )


def distribution_request(mutual_fund_id: str, scheme_id: str, year: str) -> AmfiSourceRequest:
    _validate_numeric_identifier(mutual_fund_id, "mutual_fund_id")
    _validate_numeric_identifier(scheme_id, "scheme_id")
    if year != "All" and (not year.isdigit() or len(year) != 4):
        raise ValueError("distribution year must be 'All' or a four-digit year")
    parameters = {"MF_ID": mutual_fund_id, "strSDid": scheme_id, "strYear": year}
    return AmfiSourceRequest(
        source_type=AmfiSourceType.DISTRIBUTIONS,
        url=f"{AMFI_API_URL}/scheme-dividend?{urlencode(parameters)}",
        parameters=parameters,
        parser=AmfiDistributionParser(),
    )


def _decode_utf8(payload: bytes) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SourceParseError("AMFI source is not valid UTF-8") from error


def _is_amfi_nav_application_error(text: str) -> bool:
    """Recognize AMFI's HTTP-200 transient failure without masking format drift."""
    return (
        "<html" in text.lower()
        and "View/Download NAV History" in text
        and "Application Error! Please try again later" in text
    )


def _parse_json(payload: bytes) -> object:
    try:
        return json.loads(_decode_utf8(payload))
    except json.JSONDecodeError as error:
        raise SourceParseError(f"AMFI source is not valid JSON at character {error.pos}") from error


def _json_signature(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _numeric_text(value: object, field: str, record_number: int) -> str:
    normalized = str(value)
    if isinstance(value, bool) or not normalized.isdigit():
        raise SourceParseError(f"invalid {field} at record {record_number}")
    return normalized


def _optional_source_text(value: object, field: str, record_number: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceParseError(f"invalid {field} at record {record_number}")
    return value.strip() or None


def _is_scheme_classification(value: str) -> bool:
    normalized = value.lower().replace(" ", "")
    return normalized.startswith(
        ("openendedschemes(", "closeendedschemes(", "intervalfundschemes(")
    )


def _parse_isin(value: str, _line_number: int) -> str | None:
    if not value or value == "-":
        return None
    return value


def _required_text(value: object, field: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceParseError(f"invalid {field} at record {index}")
    return value.strip()


def _validate_numeric_identifier(value: str, field: str) -> None:
    if not value.isdigit():
        raise ValueError(f"{field} must be numeric")

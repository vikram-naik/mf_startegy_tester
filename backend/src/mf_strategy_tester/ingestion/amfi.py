import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlencode

from mf_strategy_tester.ingestion.errors import SourceParseError

PARSER_VERSION = "amfi-2026.08.1"
CURRENT_NAV_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
HISTORICAL_NAV_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
AMFI_API_URL = "https://www.amfiindia.com/api"
AMFI_FUND_CATALOG_URL = "https://www.amfiindia.com/otherdata/scheme-details"

CURRENT_NAV_HEADER = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
    "Scheme Name;Net Asset Value;Date"
)
HISTORICAL_NAV_HEADER = (
    "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
    "Net Asset Value;Repurchase Price;Sale Price;Date"
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


NavParsedRecord = NavSourceRecord | InvalidNavSourceRecord


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
    launch_date: datetime


@dataclass(frozen=True)
class DistributionSourceRecord:
    nav_name: str
    record_date: datetime
    source_value: str


class AmfiNavParser:
    version = PARSER_VERSION

    def __init__(self, *, allow_empty_report: bool = False) -> None:
        self._allow_empty_report = allow_empty_report

    def validate(self, payload: bytes) -> int:
        return len(self.parse_records(payload))

    def parse_records(self, payload: bytes) -> tuple[NavParsedRecord, ...]:
        text = _decode_utf8(payload)
        if "No data found on the basis of selected parameters for this report" in text:
            if self._allow_empty_report:
                return ()
            raise SourceParseError("AMFI NAV source returned a no-data report")
        lines = text.splitlines()
        header_index = next((index for index, line in enumerate(lines) if line.strip()), None)
        if header_index is None:
            raise SourceParseError("AMFI NAV source is empty")

        header = lines[header_index].strip()
        if header == CURRENT_NAV_HEADER:
            historical = False
        elif header == HISTORICAL_NAV_HEADER:
            historical = True
        else:
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
            expected_fields = 8 if historical else 6
            if len(fields) != expected_fields:
                raise SourceParseError(
                    f"AMFI NAV parser expected {expected_fields} fields but received "
                    f"{len(fields)} at source line {index}"
                )
            record = self._parse_row(fields, index, historical, classification, fund_house)
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
        historical: bool,
        classification: str,
        fund_house: str,
    ) -> NavParsedRecord:
        if historical:
            scheme_code, name, payout, reinvestment, nav_text, _, _, date_text = fields
        else:
            scheme_code, payout, reinvestment, name, nav_text, date_text = fields
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
        if not isinstance(value, list) or not value:
            raise SourceParseError("AMFI scheme list must be a non-empty JSON array")
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
    _required_fields = frozenset(
        {
            "MF_Name",
            "Scheme_Name",
            "SchemeType_Desc",
            "SchemeCat_Desc",
            "Launch_Date",
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
            if not isinstance(item, dict) or not self._required_fields.issubset(item):
                raise SourceParseError(f"unexpected scheme-details structure at record {index}")
            try:
                launch_date = datetime.fromisoformat(str(item["Launch_Date"]))
            except ValueError as error:
                raise SourceParseError(f"invalid launch date at record {index}") from error
            records.append(
                SchemeDetailRecord(
                    mutual_fund_id=str(item["MF_Id"]),
                    scheme_id=str(item["scheme_Id"]),
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
    _required_fields = frozenset({"Nav_name", "Div_year", "Rate_of_div"})

    def validate(self, payload: bytes) -> int:
        return len(self.parse_records(payload))

    def parse_records(self, payload: bytes) -> tuple[DistributionSourceRecord, ...]:
        value = _parse_json(payload)
        data = value.get("data") if isinstance(value, dict) else None
        if not isinstance(data, list):
            raise SourceParseError("AMFI distribution response must contain a data array")
        records: list[DistributionSourceRecord] = []
        for index, item in enumerate(data, start=1):
            if not isinstance(item, dict) or not self._required_fields.issubset(item):
                raise SourceParseError(f"unexpected distribution structure at record {index}")
            try:
                record_date = datetime.fromisoformat(str(item["Div_year"]).replace("Z", "+00:00"))
            except ValueError as error:
                raise SourceParseError(f"invalid distribution date at record {index}") from error
            source_value = str(item["Rate_of_div"]).strip()
            if not source_value:
                raise SourceParseError(f"empty distribution value at record {index}")
            records.append(
                DistributionSourceRecord(
                    nav_name=_required_text(item["Nav_name"], "Nav_name", index),
                    record_date=record_date,
                    source_value=source_value,
                )
            )
        return tuple(records)


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


def _parse_json(payload: bytes) -> object:
    try:
        return json.loads(_decode_utf8(payload))
    except json.JSONDecodeError as error:
        raise SourceParseError(f"AMFI source is not valid JSON at character {error.pos}") from error


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

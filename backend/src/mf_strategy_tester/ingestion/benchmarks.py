from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from http.client import HTTPResponse
from http.cookiejar import CookieJar
from time import sleep
from typing import ClassVar, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

from mf_strategy_tester.ingestion.errors import (
    SourceDownloadError,
    SourceNotPublishedError,
    SourceParseError,
)
from mf_strategy_tester.ingestion.http import DownloadedSource

BENCHMARK_PARSER_VERSION = "official-benchmarks-2026.09.19-r14"
NIFTY_MAPPING_URL = "https://liveindexsa.niftyindices.com/assets/json/IndexMapping.json"
NIFTY_PRICE_URL = "https://www.niftyindices.com/BackPage/getHistoricaldatatabletoString"
NIFTY_TOTAL_RETURN_URL = "https://www.niftyindices.com/BackPage/getTotalReturnIndexString"
NSE_ETF_MASTER_URL = "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv"
BSE_ETF_MARKET_WATCH_URL = "https://api.bseindia.com/BseIndiaAPI/api/ETFMarketwatchdatabeta/w"
UDIFF_START_DATE = date(2024, 7, 8)
NSE_LEGACY_ISIN_START_DATE = date(2011, 6, 22)
NSE_ETF_DAILY_START_DATE = date(2010, 3, 8)

_OFFICIAL_HOSTS = frozenset(
    {
        "www.niftyindices.com",
        "liveindexsa.niftyindices.com",
        "nsearchives.nseindia.com",
        "www.nseindia.com",
        "api.bseindia.com",
        "www.bseindia.com",
    }
)
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")


@dataclass(frozen=True)
class BenchmarkRequest:
    provider: str
    source_type: str
    url: str
    parameters: dict[str, str]
    body: bytes | None = None
    referer: str | None = None
    origin: str | None = None


class BenchmarkDownloader(Protocol):
    def download(self, source: BenchmarkRequest) -> DownloadedSource: ...


@dataclass(frozen=True)
class NiftyIndexMapping:
    trading_name: str
    display_name: str


@dataclass(frozen=True)
class NiftyPriceRow:
    index_name: str
    observation_date: date
    open_value: Decimal | None
    high_value: Decimal | None
    low_value: Decimal | None
    close_value: Decimal
    ohlc_issue: str | None = None


@dataclass(frozen=True)
class NiftyTotalReturnRow:
    index_name: str
    observation_date: date
    gross_total_return: Decimal
    net_total_return: Decimal | None


@dataclass(frozen=True)
class EtfMasterRow:
    symbol: str
    underlying: str
    security_name: str
    listing_date: date
    market_lot: int
    isin: str
    face_value: Decimal


@dataclass(frozen=True)
class NseEtfDailyRow:
    symbol: str
    series: str
    security_name: str
    underlying: str


@dataclass(frozen=True)
class BseEtfMarketRow:
    security_id: str
    alias: str
    security_name: str
    category: str | None
    as_of_date: date
    roster_variant: str


@dataclass(frozen=True)
class ExchangePriceRow:
    exchange: str
    observation_date: date
    security_id: str
    symbol: str | None
    series: str
    security_name: str | None
    isin: str | None
    open_value: Decimal | None
    high_value: Decimal | None
    low_value: Decimal | None
    close_value: Decimal
    last_value: Decimal | None
    previous_close: Decimal | None
    volume: int
    traded_value: Decimal
    trade_count: int | None
    ohlc_issue: str | None = None
    close_issue: str | None = None


class OfficialBenchmarkDownloader:
    """Bounded GET/POST client restricted to official benchmark source hosts."""

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
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self._nse_session_initialized = False
        self._bse_session_initialized = False

    def download(self, source: BenchmarkRequest) -> DownloadedSource:
        self._validate_url(source.url)
        if source.referer is not None:
            self._validate_url(source.referer)
        if source.origin is not None:
            self._validate_url(source.origin)
        parsed = urlparse(source.url)
        if parsed.hostname == "www.nseindia.com":
            self._initialize_nse_session()
        if parsed.hostname == "api.bseindia.com":
            self._initialize_bse_session(source.referer or "https://www.bseindia.com/")
        headers = self._browser_headers(source.referer)
        if source.origin is not None:
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": headers["Accept-Language"],
                "Origin": source.origin,
                "Referer": source.referer or source.origin,
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
                "User-Agent": headers["User-Agent"],
            }
        if source.body is not None:
            headers["Content-Type"] = "application/json; charset=UTF-8"
        if source.referer is not None:
            headers["Referer"] = source.referer
        request = Request(
            source.url,
            data=source.body,
            headers=headers,
            method="POST" if source.body is not None else "GET",
        )
        for attempt in range(1, self._retry_attempts + 1):
            try:
                with self._opener.open(request, timeout=self._timeout_seconds) as response:
                    return self._read_response(response)
            except HTTPError as error:
                if error.code == 404:
                    raise
                if error.code not in {429, 500, 502, 503, 504} or attempt == self._retry_attempts:
                    raise SourceDownloadError(
                        f"official source returned HTTP {error.code} for {source.url} "
                        f"after {attempt} attempt(s)"
                    ) from error
            except (TimeoutError, URLError) as error:
                if attempt == self._retry_attempts:
                    raise SourceDownloadError(
                        f"failed to retrieve official source {source.url} "
                        f"after {attempt} attempt(s): {error}"
                    ) from error
            self._sleep(self._retry_backoff_seconds * (2 ** (attempt - 1)))
        raise AssertionError("download retry loop exited unexpectedly")

    def _initialize_nse_session(self) -> None:
        if self._nse_session_initialized:
            return
        request = Request(
            "https://www.nseindia.com/",
            headers=self._browser_headers(None),
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                response.read(min(self._max_bytes, 1_000_000) + 1)
        except (HTTPError, TimeoutError, URLError) as error:
            raise SourceDownloadError(
                f"failed to establish browser-like NSE session: {error}"
            ) from error
        self._nse_session_initialized = True

    def _initialize_bse_session(self, referer: str) -> None:
        if self._bse_session_initialized:
            return
        self._validate_url(referer)
        request = Request(
            referer,
            headers=self._browser_headers(None),
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                response.read(min(self._max_bytes, 1_000_000) + 1)
        except (HTTPError, TimeoutError, URLError) as error:
            raise SourceDownloadError(
                f"failed to establish browser-like BSE session: {error}"
            ) from error
        self._bse_session_initialized = True

    @staticmethod
    def _browser_headers(referer: str | None) -> dict[str, str]:
        headers = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "application/json,text/csv,application/zip;q=0.8,*/*;q=0.7"
            ),
            "Accept-Language": "en-IN,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-site" if referer is not None else "none",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
        }
        if referer is not None:
            headers["Referer"] = referer
        return headers

    def _read_response(self, response: HTTPResponse) -> DownloadedSource:
        final_url = response.url
        self._validate_url(final_url)
        content = response.read(self._max_bytes + 1)
        if len(content) > self._max_bytes:
            raise SourceDownloadError(f"source exceeds download limit of {self._max_bytes} bytes")
        return DownloadedSource(
            content=content,
            media_type=response.headers.get_content_type(),
            status_code=response.status,
            final_url=final_url,
        )

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in _OFFICIAL_HOSTS:
            raise SourceDownloadError("benchmark URL must use HTTPS on an approved official host")


class NiftyIndexMappingParser:
    version = BENCHMARK_PARSER_VERSION
    _fields = frozenset({"Trading_Index_Name", "Index_long_name"})

    def parse(self, content: bytes) -> list[NiftyIndexMapping]:
        payload = _json_array(content, "Nifty index mapping")
        mappings: list[NiftyIndexMapping] = []
        seen: set[str] = set()
        for number, raw in enumerate(payload, start=1):
            if not isinstance(raw, dict) or frozenset(raw) != self._fields:
                raise SourceParseError(
                    f"Nifty index mapping row {number} has unexpected fields: "
                    f"{sorted(raw) if isinstance(raw, dict) else type(raw).__name__}"
                )
            trading_name = _required_text(raw["Trading_Index_Name"], "Trading_Index_Name", number)
            display_name = _required_text(raw["Index_long_name"], "Index_long_name", number)
            if trading_name in seen:
                raise SourceParseError(f"duplicate Nifty trading index name {trading_name!r}")
            seen.add(trading_name)
            mappings.append(NiftyIndexMapping(trading_name, display_name))
        if not mappings:
            raise SourceParseError("Nifty index mapping is empty")
        return mappings


class NiftyPriceParser:
    version = BENCHMARK_PARSER_VERSION
    _fields = frozenset(
        {
            "RequestNumber",
            "Index Name",
            "INDEX_NAME",
            "HistoricalDate",
            "OPEN",
            "HIGH",
            "LOW",
            "CLOSE",
        }
    )

    def parse(self, content: bytes) -> list[NiftyPriceRow]:
        payload = _json_array(content, "Nifty price history")
        rows: list[NiftyPriceRow] = []
        seen: set[tuple[str, date]] = set()
        for number, raw in enumerate(payload, start=1):
            raw = _require_fields(raw, self._fields, "Nifty price history", number)
            index_name = _required_text(raw["INDEX_NAME"], "INDEX_NAME", number)
            observation_date = _date(raw["HistoricalDate"], "%d %b %Y", number)
            open_value, high_value, low_value = (
                _optional_nifty_ohlc_decimal(raw[field], field, number)
                for field in ("OPEN", "HIGH", "LOW")
            )
            close_value = _positive_decimal(raw["CLOSE"], "CLOSE", number)
            invalid_high = high_value is not None and any(
                high_value < value
                for value in (open_value, low_value, close_value)
                if value is not None
            )
            invalid_low = low_value is not None and any(
                low_value > value
                for value in (open_value, high_value, close_value)
                if value is not None
            )
            ohlc_issue = None
            if invalid_high or invalid_low:
                ohlc_issue = (
                    "official Nifty OHLC bounds are inconsistent; normalized OHLC omitted "
                    f"without inference: open={open_value}, high={high_value}, "
                    f"low={low_value}, close={close_value}"
                )
                open_value = high_value = low_value = None
            identity = (index_name, observation_date)
            if identity in seen:
                raise SourceParseError(f"duplicate Nifty price observation {identity}")
            seen.add(identity)
            rows.append(
                NiftyPriceRow(
                    index_name,
                    observation_date,
                    open_value,
                    high_value,
                    low_value,
                    close_value,
                    ohlc_issue,
                )
            )
        return rows


class NiftyTotalReturnParser:
    version = BENCHMARK_PARSER_VERSION
    _fields = frozenset({"RequestNumber", "Index Name", "Date", "TotalReturnsIndex", "NTR_Value"})

    def parse(self, content: bytes) -> list[NiftyTotalReturnRow]:
        payload = _json_array(content, "Nifty total-return history")
        rows: list[NiftyTotalReturnRow] = []
        seen: set[tuple[str, date]] = set()
        for number, raw in enumerate(payload, start=1):
            raw = _require_fields(raw, self._fields, "Nifty total-return history", number)
            index_name = _required_text(raw["Index Name"], "Index Name", number)
            observation_date = _date(raw["Date"], "%d %b %Y", number)
            gross = _positive_decimal(raw["TotalReturnsIndex"], "TotalReturnsIndex", number)
            ntr_raw = raw["NTR_Value"]
            net = (
                None
                if ntr_raw is None or ntr_raw in ("", "-")
                else _positive_decimal(ntr_raw, "NTR_Value", number)
            )
            identity = (index_name, observation_date)
            if identity in seen:
                raise SourceParseError(f"duplicate Nifty total-return observation {identity}")
            seen.add(identity)
            rows.append(NiftyTotalReturnRow(index_name, observation_date, gross, net))
        return rows


class NseEtfMasterParser:
    version = BENCHMARK_PARSER_VERSION
    _fields: ClassVar[list[str]] = [
        "Symbol",
        "Underlying Asset",
        "SecurityName",
        "DateofListing",
        "MarketLot",
        "ISINNumber",
        "FaceValue",
        "ETF Underlying",
        "Underlying Key",
    ]

    def parse(self, content: bytes) -> list[EtfMasterRow]:
        reader = _csv_reader(content, "NSE ETF master")
        if reader.fieldnames != self._fields:
            raise SourceParseError(
                f"NSE ETF master expected fields {self._fields}, received {reader.fieldnames}"
            )
        rows: list[EtfMasterRow] = []
        seen: set[str] = set()
        for number, raw in enumerate(reader, start=2):
            isin = _isin(raw["ISINNumber"], number)
            if isin in seen:
                raise SourceParseError(f"duplicate NSE ETF ISIN {isin!r} at row {number}")
            seen.add(isin)
            market_lot = _nonnegative_int(raw["MarketLot"], "MarketLot", number)
            if market_lot == 0:
                raise SourceParseError(f"NSE ETF master row {number} has zero MarketLot")
            rows.append(
                EtfMasterRow(
                    symbol=_required_text(raw["Symbol"], "Symbol", number),
                    underlying=_required_text(raw["Underlying Asset"], "Underlying Asset", number),
                    security_name=_required_text(raw["SecurityName"], "SecurityName", number),
                    listing_date=_date(raw["DateofListing"], "%d-%b-%y", number),
                    market_lot=market_lot,
                    isin=isin,
                    face_value=_positive_decimal(raw["FaceValue"], "FaceValue", number),
                )
            )
        if not rows:
            raise SourceParseError("NSE ETF master is empty")
        return rows


class NseEtfDailyParser:
    """Parse the ETF-only file inside NSE's daily official press-report ZIP."""

    version = BENCHMARK_PARSER_VERSION
    _fields: ClassVar[list[str]] = [
        "MARKET",
        "SERIES",
        "SYMBOL",
        "SECURITY",
        "PREVIOUS CLOSE PRICE",
        "OPEN PRICE",
        "HIGH PRICE",
        "LOW PRICE",
        "CLOSE PRICE",
        "NET TRADED VALUE",
        "NET TRADED QTY",
        "TRADES",
        "52 WEEK HIGH",
        "52 WEEK LOW",
        "UNDERLYING",
    ]

    def parse(self, content: bytes, *, expected_date: date) -> list[NseEtfDailyRow]:
        csv_content = _extract_nse_etf_daily_csv(content, expected_date)
        if csv_content is None:
            return []
        reader = _csv_reader(csv_content, "NSE daily ETF report")
        if reader.fieldnames != self._fields:
            raise SourceParseError(f"NSE daily ETF report fields changed: {reader.fieldnames}")
        rows: list[NseEtfDailyRow] = []
        seen: set[tuple[str, str]] = set()
        for number, raw in enumerate(reader, start=2):
            if raw["MARKET"] != "N":
                raise SourceParseError(f"NSE daily ETF row {number} has unexpected market")
            symbol = _required_text(raw["SYMBOL"], "SYMBOL", number)
            series = _required_text(raw["SERIES"], "SERIES", number)
            identity = (symbol, series)
            if identity in seen:
                raise SourceParseError(f"duplicate NSE daily ETF identity {identity}")
            seen.add(identity)
            # Validate every quantitative field even though the bhavcopy is canonical for prices.
            for field in (
                "PREVIOUS CLOSE PRICE",
                "OPEN PRICE",
                "HIGH PRICE",
                "LOW PRICE",
                "CLOSE PRICE",
                "52 WEEK HIGH",
                "52 WEEK LOW",
            ):
                _positive_decimal(raw[field], field, number)
            _nonnegative_decimal(raw["NET TRADED VALUE"], "NET TRADED VALUE", number)
            _nonnegative_int(raw["NET TRADED QTY"], "NET TRADED QTY", number)
            _nonnegative_int(raw["TRADES"], "TRADES", number)
            rows.append(
                NseEtfDailyRow(
                    symbol=symbol,
                    series=series,
                    security_name=_required_text(raw["SECURITY"], "SECURITY", number),
                    underlying=_required_text(raw["UNDERLYING"], "UNDERLYING", number),
                )
            )
        if not rows:
            raise SourceParseError("NSE daily ETF report is empty")
        return rows


class BseEtfMarketParser:
    """Parse BSE's official current ETF-only market-watch roster."""

    version = BENCHMARK_PARSER_VERSION
    _common_fields = frozenset(
        {
            "Scrip_cd",
            "EtfAlias_Name",
            "TYPE",
            "Ltradert",
            "change_val",
            "HighRate",
            "LowRate",
            "prevDayClose",
            "OpenRate",
            "Wtdavg",
            "totalVol",
            "Turnover",
            "TwoWeekAvg",
            "Uchklmt",
            "Lchklmt",
            "week_High",
            "week_Low",
            "Month_High",
            "Month_Low",
            "52Week_High",
            "52Week_Low",
            "URL",
            "DT_TM",
            "Nsurl",
            "Best_Buy_Rate",
            "Best_Sale_Rate",
            "Total_Sale_Qty",
            "Total_Buy_Qty",
            "Change_Percentage",
        }
    )
    _broad_fields = _common_fields | {"ScripName"}
    _categorized_fields = _common_fields | {"ETFNAME"}

    def parse(self, content: bytes) -> list[BseEtfMarketRow]:
        payload = _json_object(content, "BSE ETF market watch")
        if frozenset(payload) != {"Table"} or not isinstance(payload["Table"], list):
            raise SourceParseError("BSE ETF market watch must contain only a Table array")
        rows: list[BseEtfMarketRow] = []
        seen: set[str] = set()
        observed_dates: set[date] = set()
        observed_variants: set[str] = set()
        for number, item in enumerate(payload["Table"], start=1):
            if not isinstance(item, dict):
                raise SourceParseError(f"BSE ETF market watch row {number} must be a JSON object")
            fields = frozenset(item)
            if fields == self._broad_fields:
                roster_variant = "broad_market_watch"
                name_field = "ScripName"
            elif fields == self._categorized_fields:
                roster_variant = "categorized_subset"
                name_field = "EtfAlias_Name"
            else:
                raise SourceParseError(
                    f"BSE ETF market watch row {number} has unexpected fields: {sorted(item)}"
                )
            raw = item
            code = raw["Scrip_cd"]
            if not isinstance(code, int) or isinstance(code, bool) or code <= 0:
                raise SourceParseError(f"BSE ETF market watch row {number} has invalid Scrip_cd")
            security_id = str(code)
            if security_id in seen:
                raise SourceParseError(f"duplicate BSE ETF scrip code {security_id!r}")
            timestamp = _required_text(raw["DT_TM"], "DT_TM", number)
            try:
                as_of_date = datetime.fromisoformat(timestamp).date()
            except ValueError as error:
                raise SourceParseError(
                    f"BSE ETF market watch row {number} has invalid DT_TM"
                ) from error
            seen.add(security_id)
            observed_dates.add(as_of_date)
            observed_variants.add(roster_variant)
            category_raw = raw["TYPE"]
            if category_raw is not None and not isinstance(category_raw, str):
                raise SourceParseError(f"BSE ETF market watch row {number} has invalid TYPE")
            rows.append(
                BseEtfMarketRow(
                    security_id=security_id,
                    alias=_required_text(raw["EtfAlias_Name"], "EtfAlias_Name", number),
                    security_name=_required_text(raw[name_field], name_field, number),
                    category=category_raw.strip() if category_raw else None,
                    as_of_date=as_of_date,
                    roster_variant=roster_variant,
                )
            )
        if not rows:
            raise SourceParseError("BSE ETF market watch is empty")
        if len(observed_dates) != 1:
            raise SourceParseError("BSE ETF market watch contains multiple as-of dates")
        if len(observed_variants) != 1:
            raise SourceParseError("BSE ETF market watch mixes incompatible schema variants")
        return rows


class UdIffBhavcopyParser:
    version = BENCHMARK_PARSER_VERSION
    _fields: ClassVar[list[str]] = [
        "TradDt",
        "BizDt",
        "Sgmt",
        "Src",
        "FinInstrmTp",
        "FinInstrmId",
        "ISIN",
        "TckrSymb",
        "SctySrs",
        "XpryDt",
        "FininstrmActlXpryDt",
        "StrkPric",
        "OptnTp",
        "FinInstrmNm",
        "OpnPric",
        "HghPric",
        "LwPric",
        "ClsPric",
        "LastPric",
        "PrvsClsgPric",
        "UndrlygPric",
        "SttlmPric",
        "OpnIntrst",
        "ChngInOpnIntrst",
        "TtlTradgVol",
        "TtlTrfVal",
        "TtlNbOfTxsExctd",
        "SsnId",
        "NewBrdLotQty",
        "Rmks",
        "Rsvd1",
        "Rsvd2",
        "Rsvd3",
        "Rsvd4",
    ]

    def parse(
        self, content: bytes, *, expected_exchange: str, expected_date: date
    ) -> list[ExchangePriceRow]:
        if expected_exchange.upper() == "BSE" and _is_bse_missing_bhavcopy_page(content):
            raise SourceNotPublishedError(
                f"BSE bhavcopy was not published for {expected_date}; "
                "the official file URL returned the generic BSE homepage"
            )
        csv_content = _extract_csv(content, "UDiFF bhavcopy")
        reader = _csv_reader(csv_content, "UDiFF bhavcopy")
        if reader.fieldnames != self._fields:
            raise SourceParseError(
                f"UDiFF bhavcopy expected {len(self._fields)} fields, received {reader.fieldnames}"
            )
        rows: list[ExchangePriceRow] = []
        seen: set[str] = set()
        for number, raw in enumerate(reader, start=2):
            exchange = _required_text(raw["Src"], "Src", number).upper()
            if exchange != expected_exchange:
                raise SourceParseError(
                    f"UDiFF row {number} exchange {exchange!r} does not match {expected_exchange!r}"
                )
            observation_date = _date(raw["TradDt"], "%Y-%m-%d", number)
            if observation_date != expected_date or raw["BizDt"] != expected_date.isoformat():
                raise SourceParseError(f"UDiFF row {number} does not match requested date")
            security_id = _required_text(raw["FinInstrmId"], "FinInstrmId", number)
            if security_id in seen:
                raise SourceParseError(f"duplicate UDiFF security ID {security_id!r}")
            seen.add(security_id)
            rows.append(_udiff_price_row(raw, number, exchange, observation_date, security_id))
        return rows


class LegacyNseBhavcopyParser:
    version = BENCHMARK_PARSER_VERSION
    _fields: ClassVar[list[str]] = [
        "SYMBOL",
        "SERIES",
        "OPEN",
        "HIGH",
        "LOW",
        "CLOSE",
        "LAST",
        "PREVCLOSE",
        "TOTTRDQTY",
        "TOTTRDVAL",
        "TIMESTAMP",
        "TOTALTRADES",
        "ISIN",
        "",
    ]
    _compact_fields: ClassVar[list[str]] = _fields[:-1]
    _pre_isin_fields: ClassVar[list[str]] = [*_fields[:-3], ""]

    def parse(self, content: bytes, *, expected_date: date) -> list[ExchangePriceRow]:
        expected_name = f"cm{expected_date:%d%b%Y}bhav.csv".lower()
        csv_content = _extract_csv(
            content,
            "legacy NSE bhavcopy",
            allowed_zip_members=frozenset(
                {
                    expected_name,
                    f"{expected_name}/{expected_name}",
                }
            ),
        )
        reader = _csv_reader(csv_content, "legacy NSE bhavcopy")
        fields = reader.fieldnames
        if fields is None:
            raise SourceParseError("legacy NSE bhavcopy has no header")
        is_pre_isin_schema = (
            fields == self._pre_isin_fields and expected_date < NSE_LEGACY_ISIN_START_DATE
        )
        if fields not in (self._fields, self._compact_fields) and not is_pre_isin_schema:
            raise SourceParseError(f"legacy NSE bhavcopy fields changed: {reader.fieldnames}")
        has_isin = "ISIN" in fields
        has_trade_count = "TOTALTRADES" in fields
        rows: list[ExchangePriceRow] = []
        seen: set[tuple[str, str]] = set()
        for number, raw in enumerate(reader, start=2):
            observation_date = _legacy_nse_date(raw["TIMESTAMP"], number)
            if observation_date != expected_date:
                raise SourceParseError(f"legacy NSE row {number} does not match requested date")
            symbol = _required_text(raw["SYMBOL"], "SYMBOL", number)
            series = _required_text(raw["SERIES"], "SERIES", number)
            identity = (symbol, series)
            if identity in seen:
                raise SourceParseError(f"duplicate legacy NSE identity {identity}")
            seen.add(identity)
            rows.append(
                _legacy_price_row(
                    raw,
                    number,
                    "NSE",
                    observation_date,
                    symbol,
                    symbol,
                    series,
                    _legacy_nse_isin(raw["ISIN"], number) if has_isin else None,
                    trades_field="TOTALTRADES" if has_trade_count else None,
                )
            )
        return rows


class LegacyBseBhavcopyParser:
    version = BENCHMARK_PARSER_VERSION
    _fields: ClassVar[list[str]] = [
        "SC_CODE",
        "SC_NAME",
        "SC_GROUP",
        "SC_TYPE",
        "OPEN",
        "HIGH",
        "LOW",
        "CLOSE",
        "LAST",
        "PREVCLOSE",
        "NO_TRADES",
        "NO_OF_SHRS",
        "NET_TURNOV",
        "TDCLOINDI",
    ]

    def parse(self, content: bytes, *, expected_date: date) -> list[ExchangePriceRow]:
        if _is_bse_missing_bhavcopy_page(content):
            raise SourceNotPublishedError(
                f"BSE bhavcopy was not published for {expected_date}; "
                "the official file URL returned the generic BSE homepage"
            )
        reader = _csv_reader(
            _extract_legacy_bse_csv(content, expected_date),
            "legacy BSE bhavcopy",
        )
        if reader.fieldnames != self._fields:
            raise SourceParseError(f"legacy BSE bhavcopy fields changed: {reader.fieldnames}")
        rows: list[ExchangePriceRow] = []
        seen: set[str] = set()
        for number, raw in enumerate(reader, start=2):
            security_id = _required_text(raw["SC_CODE"], "SC_CODE", number)
            if security_id in seen:
                raise SourceParseError(f"duplicate legacy BSE security ID {security_id!r}")
            seen.add(security_id)
            rows.append(
                _legacy_price_row(
                    raw,
                    number,
                    "BSE",
                    expected_date,
                    security_id,
                    _legacy_bse_name(raw["SC_NAME"], number),
                    _required_text(raw["SC_GROUP"], "SC_GROUP", number),
                    None,
                    volume_field="NO_OF_SHRS",
                    traded_value_field="NET_TURNOV",
                    trades_field="NO_TRADES",
                    allow_empty_previous_close=True,
                )
            )
        return rows


def nifty_mapping_request() -> BenchmarkRequest:
    return BenchmarkRequest("nifty_indices", "nifty_index_mapping", NIFTY_MAPPING_URL, {})


def nifty_history_request(
    mapping: NiftyIndexMapping, start_date: date, end_date: date, *, total_return: bool
) -> BenchmarkRequest:
    if end_date < start_date or (end_date - start_date).days > 364:
        raise ValueError("Nifty history request must span at most 365 inclusive calendar days")
    inner = json.dumps(
        {
            "name": mapping.trading_name,
            "startDate": start_date.strftime("%d-%b-%Y"),
            "endDate": end_date.strftime("%d-%b-%Y"),
            "indexName": mapping.display_name,
        },
        separators=(",", ":"),
    )
    return BenchmarkRequest(
        provider="nifty_indices",
        source_type="nifty_total_return_history" if total_return else "nifty_price_history",
        url=NIFTY_TOTAL_RETURN_URL if total_return else NIFTY_PRICE_URL,
        parameters={
            "trading_name": mapping.trading_name,
            "display_name": mapping.display_name,
            "from_date": start_date.isoformat(),
            "to_date": end_date.isoformat(),
        },
        body=json.dumps({"cinfo": inner}, separators=(",", ":")).encode(),
        referer="https://www.niftyindices.com/reports/historical-data",
    )


def nse_etf_master_request() -> BenchmarkRequest:
    return BenchmarkRequest(
        "nse",
        "nse_etf_security_master",
        NSE_ETF_MASTER_URL,
        {},
        referer="https://www.nseindia.com/static/market-data/securities-available-for-trading",
    )


def nse_etf_daily_request(observation_date: date) -> BenchmarkRequest:
    return BenchmarkRequest(
        "nse",
        "nse_daily_etf_report",
        "https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/"
        f"PR{observation_date:%d%m%y}.zip",
        {"date": observation_date.isoformat()},
        referer="https://www.nseindia.com/all-reports",
    )


def bse_etf_market_request() -> BenchmarkRequest:
    return BenchmarkRequest(
        "bse",
        "bse_etf_market_watch",
        BSE_ETF_MARKET_WATCH_URL,
        {},
        referer="https://www.bseindia.com/markets/etf/ETF_MktWatch",
        origin="https://www.bseindia.com",
    )


def bhavcopy_request(exchange: str, observation_date: date) -> BenchmarkRequest:
    normalized = exchange.upper()
    if normalized == "NSE":
        if observation_date >= UDIFF_START_DATE:
            url = (
                "https://nsearchives.nseindia.com/content/cm/"
                f"BhavCopy_NSE_CM_0_0_0_{observation_date:%Y%m%d}_F_0000.csv.zip"
            )
        else:
            month = observation_date.strftime("%b").upper()
            filename = f"cm{observation_date:%d}{month}{observation_date:%Y}bhav.csv.zip"
            url = (
                "https://nsearchives.nseindia.com/content/historical/EQUITIES/"
                f"{observation_date:%Y}/{month}/{filename}"
            )
        referer = "https://www.nseindia.com/all-reports"
    elif normalized == "BSE":
        if observation_date >= UDIFF_START_DATE:
            url = (
                "https://www.bseindia.com/download/BhavCopy/Equity/"
                f"BhavCopy_BSE_CM_0_0_0_{observation_date:%Y%m%d}_F_0000.CSV"
            )
        else:
            url = (
                "https://www.bseindia.com/download/BhavCopy/Equity/"
                f"EQ{observation_date:%d%m%y}_CSV.ZIP"
            )
        referer = "https://www.bseindia.com/markets/MarketInfo/BhavCopy.aspx"
    else:
        raise ValueError("exchange must be NSE or BSE")
    return BenchmarkRequest(
        normalized.lower(),
        "exchange_bhavcopy",
        url,
        {"exchange": normalized, "date": observation_date.isoformat()},
        referer=referer,
    )


def _json_array(content: bytes, label: str) -> list[object]:
    try:
        decoded = content.decode("utf-8-sig")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceParseError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(payload, list):
        raise SourceParseError(f"{label} must be a JSON array")
    return payload


def _is_bse_missing_bhavcopy_page(content: bytes) -> bool:
    """Recognize only BSE's observed generic-homepage response for an absent daily file."""
    prefix = content[:32_768].lstrip().lower()
    return (
        prefix.startswith(b"<!doctype html")
        and b"live stock/share market" in prefix
        and b"bse sensex" in prefix
        and b"bseindia.com" in prefix
    )


def _json_object(content: bytes, label: str) -> dict[str, object]:
    try:
        decoded = content.decode("utf-8-sig")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceParseError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise SourceParseError(f"{label} must be a JSON object")
    return payload


def _require_fields(
    raw: object, fields: frozenset[str], label: str, number: int
) -> dict[str, object]:
    if not isinstance(raw, dict) or frozenset(raw) != fields:
        raise SourceParseError(
            f"{label} row {number} has unexpected fields: "
            f"{sorted(raw) if isinstance(raw, dict) else type(raw).__name__}"
        )
    return raw


def _csv_reader(content: bytes, label: str) -> csv.DictReader[str]:
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            decoded = content.decode("cp1252")
        except UnicodeDecodeError as error:
            raise SourceParseError(f"{label} is neither UTF-8 nor Windows-1252 CSV") from error
    return csv.DictReader(io.StringIO(decoded, newline=""))


def _extract_csv(
    content: bytes,
    label: str,
    *,
    allowed_zip_members: frozenset[str] | None = None,
) -> bytes:
    if not content.startswith(b"PK"):
        return content
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            if len(files) != 1:
                raise SourceParseError(f"{label} ZIP must contain exactly one file")
            item = files[0]
            member_name = item.filename.lower()
            if allowed_zip_members is not None:
                valid_name = member_name in allowed_zip_members
            else:
                valid_name = "/" not in item.filename and member_name.endswith(".csv")
            if "\\" in item.filename or not valid_name:
                raise SourceParseError(f"{label} ZIP contains an unsafe or non-CSV filename")
            if item.file_size > 20_000_000:
                raise SourceParseError(f"{label} ZIP member exceeds 20 MB")
            return archive.read(item)
    except zipfile.BadZipFile as error:
        raise SourceParseError(f"{label} is not a valid ZIP file") from error


def _extract_legacy_bse_csv(content: bytes, expected_date: date) -> bytes:
    if not content.startswith(b"PK"):
        return content
    expected_name = f"eq{expected_date:%d%m%y}.csv".lower()
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            matches = [
                item
                for item in archive.infolist()
                if not item.is_dir() and item.filename.lower() == expected_name
            ]
            if len(matches) != 1:
                raise SourceParseError(
                    "legacy BSE bhavcopy ZIP must contain exactly one date-matching equity CSV"
                )
            item = matches[0]
            if item.file_size > 20_000_000:
                raise SourceParseError("legacy BSE bhavcopy ZIP member exceeds 20 MB")
            return archive.read(item)
    except zipfile.BadZipFile as error:
        raise SourceParseError("legacy BSE bhavcopy is not a valid ZIP file") from error


def _extract_nse_etf_daily_csv(content: bytes, expected_date: date) -> bytes | None:
    if not content.startswith(b"PK"):
        raise SourceParseError("NSE daily ETF report must be a ZIP file")
    short_date = f"{expected_date:%d%m%y}"
    expected_names = {
        f"etf{short_date}.csv".lower(),
        f"etf{expected_date:%d%m%Y}.csv".lower(),
        f"nupr{short_date}/etf{short_date}.csv".lower(),
    }
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            matches = [item for item in files if item.filename.lower() in expected_names]
            if len(matches) != 1:
                dated_etf_files: list[tuple[zipfile.ZipInfo, str]] = []
                for candidate in files:
                    dated_match = re.fullmatch(
                        r"(?:nupr(?P<folder_date>\d{6})/)?"
                        r"etf(?P<file_date>\d{6}|\d{8})\.csv",
                        candidate.filename,
                        re.IGNORECASE,
                    )
                    if dated_match is None:
                        continue
                    folder_date = dated_match.group("folder_date")
                    file_date = dated_match.group("file_date")
                    if folder_date is not None and folder_date != file_date:
                        continue
                    dated_etf_files.append((candidate, file_date))
                expected_press_name = f"pr{short_date}.csv".lower()
                date_matched_press_files = [
                    item for item in files if item.filename.lower() == expected_press_name
                ]
                if (
                    not matches
                    and not dated_etf_files
                    and expected_date < NSE_ETF_DAILY_START_DATE
                    and len(date_matched_press_files) == 1
                ):
                    return None
                if not matches and len(dated_etf_files) == 1:
                    dated_item, date_text = dated_etf_files[0]
                    member_name = dated_item.filename
                    try:
                        contained_date = datetime.strptime(
                            date_text, "%d%m%y" if len(date_text) == 6 else "%d%m%Y"
                        ).date()
                    except ValueError as error:
                        raise SourceParseError(
                            "NSE press-report ZIP contains invalid dated ETF member "
                            f"{member_name!r}"
                        ) from error
                    raise SourceNotPublishedError(
                        f"NSE ETF report was not published for {expected_date}; "
                        f"the official ZIP contained {member_name!r} for {contained_date}"
                    )
                raise SourceParseError(
                    "NSE press-report ZIP must contain exactly one date-matching ETF CSV"
                )
            item = matches[0]
            if item.file_size > 5_000_000:
                raise SourceParseError("NSE daily ETF CSV exceeds 5 MB")
            return archive.read(item)
    except zipfile.BadZipFile as error:
        raise SourceParseError("NSE daily ETF report is not a valid ZIP file") from error


def _udiff_price_row(
    raw: dict[str, str], number: int, exchange: str, observation_date: date, security_id: str
) -> ExchangePriceRow:
    ohlc = tuple(
        _positive_decimal(raw[field], field, number)
        for field in ("OpnPric", "HghPric", "LwPric", "ClsPric")
    )
    last_value = _optional_nonnegative_decimal(raw["LastPric"], "LastPric", number)
    previous_close = _nonnegative_decimal(raw["PrvsClsgPric"], "PrvsClsgPric", number)
    open_value, high_value, low_value, ohlc_issue = _validated_exchange_ohlc(
        ohlc[0], ohlc[1], ohlc[2], ohlc[3], number
    )
    return ExchangePriceRow(
        exchange=exchange,
        observation_date=observation_date,
        security_id=security_id,
        symbol=_required_text(raw["TckrSymb"], "TckrSymb", number),
        series=_required_text(raw["SctySrs"], "SctySrs", number),
        security_name=_required_text(raw["FinInstrmNm"], "FinInstrmNm", number),
        isin=_isin(raw["ISIN"], number),
        open_value=open_value,
        high_value=high_value,
        low_value=low_value,
        close_value=ohlc[3],
        last_value=last_value,
        previous_close=previous_close,
        volume=_nonnegative_int(raw["TtlTradgVol"], "TtlTradgVol", number),
        traded_value=_nonnegative_decimal(raw["TtlTrfVal"], "TtlTrfVal", number),
        trade_count=_nonnegative_int(raw["TtlNbOfTxsExctd"], "TtlNbOfTxsExctd", number),
        ohlc_issue=ohlc_issue,
        close_issue=None,
    )


def _legacy_price_row(
    raw: dict[str, str],
    number: int,
    exchange: str,
    observation_date: date,
    security_id: str,
    symbol: str | None,
    series: str,
    isin: str | None,
    *,
    volume_field: str = "TOTTRDQTY",
    traded_value_field: str = "TOTTRDVAL",
    trades_field: str | None = "TOTALTRADES",
    allow_empty_previous_close: bool = False,
) -> ExchangePriceRow:
    # Legacy exchange files can publish zero as an invalid OHLC marker while retaining a
    # usable positive close. Preserve that distinction for quality handling after ETF
    # identity filtering; negative and non-decimal source values remain structural errors.
    source_open = _nonnegative_decimal(raw["OPEN"], "OPEN", number)
    source_high = _nonnegative_decimal(raw["HIGH"], "HIGH", number)
    source_low = _nonnegative_decimal(raw["LOW"], "LOW", number)
    close_value = _nonnegative_decimal(raw["CLOSE"], "CLOSE", number)
    last_value = _nonnegative_decimal(raw["LAST"], "LAST", number)
    previous_close = (
        _optional_nonnegative_decimal(raw["PREVCLOSE"], "PREVCLOSE", number)
        if allow_empty_previous_close
        else _nonnegative_decimal(raw["PREVCLOSE"], "PREVCLOSE", number)
    )
    if close_value == 0:
        open_value: Decimal | None = source_open
        high_value: Decimal | None = source_high
        low_value: Decimal | None = source_low
        ohlc_issue = None
        close_issue = (
            "official exchange close is zero; observation omitted without substituting "
            f"another price: source_row={number}, open={source_open}, high={source_high}, "
            f"low={source_low}, close={close_value}, last={last_value}, "
            f"previous_close={previous_close}"
        )
    else:
        open_value, high_value, low_value, ohlc_issue = _validated_exchange_ohlc(
            source_open, source_high, source_low, close_value, number
        )
        close_issue = None
    return ExchangePriceRow(
        exchange=exchange,
        observation_date=observation_date,
        security_id=security_id,
        symbol=symbol.strip() if symbol is not None else None,
        series=series.strip(),
        security_name=symbol.strip() if symbol is not None else None,
        isin=isin,
        open_value=open_value,
        high_value=high_value,
        low_value=low_value,
        close_value=close_value,
        last_value=last_value,
        previous_close=previous_close,
        volume=_nonnegative_int(raw[volume_field], volume_field, number),
        traded_value=_nonnegative_decimal(raw[traded_value_field], traded_value_field, number),
        trade_count=(
            _nonnegative_int(raw[trades_field], trades_field, number)
            if trades_field is not None
            else None
        ),
        ohlc_issue=ohlc_issue,
        close_issue=close_issue,
    )


def _validated_exchange_ohlc(
    open_value: Decimal, high: Decimal, low: Decimal, close: Decimal, number: int
) -> tuple[Decimal | None, Decimal | None, Decimal | None, str | None]:
    if (
        open_value <= 0
        or high <= 0
        or low <= 0
        or high < max(open_value, low, close)
        or low > min(open_value, high, close)
    ):
        return (
            None,
            None,
            None,
            "official exchange OHLC contains a non-positive value or inconsistent bounds; "
            "normalized OHLC omitted "
            f"without inference: source_row={number}, open={open_value}, high={high}, "
            f"low={low}, close={close}",
        )
    return open_value, high, low, None


def _required_text(value: object, field: str, number: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceParseError(f"row {number} field {field} must be non-empty text")
    return value.strip()


def _optional_nonnegative_decimal(value: object, field: str, number: int) -> Decimal | None:
    if value == "":
        return None
    return _nonnegative_decimal(value, field, number)


def _date(value: object, date_format: str, number: int) -> date:
    text = _required_text(value, "date", number)
    try:
        return datetime.strptime(text, date_format).date()
    except ValueError as error:
        raise SourceParseError(f"row {number} has invalid date {text!r}") from error


def _legacy_nse_date(value: object, number: int) -> date:
    text = _required_text(value, "date", number)
    if re.fullmatch(r"\d{2}-[A-Za-z]{3}-\d{4}", text):
        date_format = "%d-%b-%Y"
    elif re.fullmatch(r"\d{2}-[A-Za-z]{3}-\d{2}", text):
        date_format = "%d-%b-%y"
    else:
        raise SourceParseError(f"row {number} has invalid date {text!r}")
    try:
        return datetime.strptime(text, date_format).date()
    except ValueError as error:
        raise SourceParseError(f"row {number} has invalid date {text!r}") from error


def _positive_decimal(value: object, field: str, number: int) -> Decimal:
    parsed = _nonnegative_decimal(value, field, number)
    if parsed <= 0:
        raise SourceParseError(f"row {number} field {field} must be positive")
    return parsed


def _optional_nifty_ohlc_decimal(value: object, field: str, number: int) -> Decimal | None:
    """Preserve the official Nifty '-' marker as unavailable OHLC, never as a price."""
    if value == "-":
        return None
    return _positive_decimal(value, field, number)


def _nonnegative_decimal(value: object, field: str, number: int) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise SourceParseError(f"row {number} field {field} is not decimal") from error
    if not parsed.is_finite() or parsed < 0:
        raise SourceParseError(f"row {number} field {field} must be finite and non-negative")
    return parsed


def _nonnegative_int(value: object, field: str, number: int) -> int:
    text = str(value).strip()
    if not text.isdigit():
        raise SourceParseError(f"row {number} field {field} is not a non-negative integer")
    return int(text)


def _isin(value: object, number: int) -> str:
    text = _required_text(value, "ISIN", number).upper()
    if _ISIN.fullmatch(text) is None:
        raise SourceParseError(f"row {number} has invalid ISIN {text!r}")
    return text


def _legacy_nse_isin(value: object, number: int) -> str | None:
    """Normalize exact observed unusable NSE tokens without inventing an identifier."""
    text = _required_text(value, "ISIN", number).upper()
    if text in {"DUMMY", "INE"}:
        return None
    return _isin(text, number)


def _legacy_bse_name(value: object, number: int) -> str | None:
    """Preserve an observed whitespace-only legacy BSE name as unavailable metadata."""
    if not isinstance(value, str):
        raise SourceParseError(f"row {number} field SC_NAME must be text")
    return value.strip() or None

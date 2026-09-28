import csv
import io
import json
import zipfile
from collections import defaultdict, deque
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    BenchmarkExchangeListingRecord,
    BenchmarkInstrumentRecord,
    BenchmarkIssueRecord,
    BenchmarkObservationRecord,
    BenchmarkObservationSourceRecord,
    BenchmarkSyncCheckpointRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.benchmarks import (
    BenchmarkRequest,
    BseEtfMarketParser,
    LegacyBseBhavcopyParser,
    LegacyNseBhavcopyParser,
    NiftyIndexMappingParser,
    NiftyPriceParser,
    NiftyTotalReturnParser,
    NseEtfDailyParser,
    NseEtfMasterParser,
    OfficialBenchmarkDownloader,
    UdIffBhavcopyParser,
)
from mf_strategy_tester.ingestion.errors import (
    SourceDownloadError,
    SourceNotPublishedError,
    SourceParseError,
)
from mf_strategy_tester.ingestion.http import DownloadedSource
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.benchmark_acquisition import BenchmarkAcquisitionService


class _QueuedBenchmarkDownloader:
    def __init__(self) -> None:
        self.responses: defaultdict[str, deque[bytes | Exception]] = defaultdict(deque)
        self.requests: list[BenchmarkRequest] = []

    def add(self, source_type: str, *responses: bytes | Exception) -> None:
        self.responses[source_type].extend(responses)

    def download(self, request: BenchmarkRequest) -> DownloadedSource:
        self.requests.append(request)
        if not self.responses[request.source_type]:
            raise AssertionError(f"unexpected benchmark request {request.source_type}")
        response = self.responses[request.source_type].popleft()
        if isinstance(response, Exception):
            raise response
        return DownloadedSource(
            content=response,
            media_type="application/octet-stream",
            status_code=200,
            final_url=request.url,
        )


def _session(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'benchmarks.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return factory()


def _service(
    tmp_path: Path, session: Session, downloader: _QueuedBenchmarkDownloader
) -> BenchmarkAcquisitionService:
    return BenchmarkAcquisitionService(
        session,
        IngestionRepository(session),
        ArtifactStore(tmp_path / "raw"),
        downloader,
    )


def _nifty_mapping() -> bytes:
    return json.dumps([{"Trading_Index_Name": "NIFTY 50", "Index_long_name": "Nifty 50"}]).encode()


def _nifty_price(close: str = "12182.50") -> bytes:
    return json.dumps(
        [
            {
                "RequestNumber": "His1",
                "Index Name": "",
                "INDEX_NAME": "Nifty 50",
                "HistoricalDate": "01 Jan 2020",
                "OPEN": "12202.15",
                "HIGH": "12222.20",
                "LOW": "12165.30",
                "CLOSE": close,
            }
        ]
    ).encode()


def _nifty_close_only_price() -> bytes:
    return json.dumps(
        [
            {
                "RequestNumber": "His1",
                "Index Name": "",
                "INDEX_NAME": "Nifty 50",
                "HistoricalDate": "03 Jul 1990",
                "OPEN": "-",
                "HIGH": "-",
                "LOW": "-",
                "CLOSE": "279.02",
            }
        ]
    ).encode()


def _nifty_invalid_ohlc_price() -> bytes:
    document = json.loads(_nifty_price(close="770.85"))
    document[0].update(
        {
            "HistoricalDate": "30 Dec 2002",
            "OPEN": "771.90",
            "HIGH": "765.95",
            "LOW": "770.85",
        }
    )
    return json.dumps(document).encode()


def _nifty_total_return(ntr_value: object = "15721") -> bytes:
    return json.dumps(
        [
            {
                "RequestNumber": "TRI1",
                "Index Name": "Nifty 50",
                "Date": "01 Jan 2020",
                "TotalReturnsIndex": "17096.83",
                "NTR_Value": ntr_value,
            }
        ]
    ).encode()


def _nse_master(
    *,
    symbol: str = "NIFTYBEES",
    security_name: str = "NIP IND ETF NIFTY BEES",
    isin: str = "INF204KB14I2",
) -> bytes:
    return (
        b"Symbol,Underlying Asset,SecurityName,DateofListing,MarketLot,ISINNumber,"
        b"FaceValue,ETF Underlying,Underlying Key\n"
        + f"{symbol},Nifty 50,{security_name},08-Jan-02,1,{isin},1,EQUITY,Nifty 50\n".encode()
    )


def _nse_daily_zip(
    observation_date: date,
    *,
    symbol: str = "NIFTYBEES",
    security_name: str = "NIP IND ETF NIFTY BEES",
) -> bytes:
    content = (
        b"MARKET,SERIES,SYMBOL,SECURITY,PREVIOUS CLOSE PRICE,OPEN PRICE,HIGH PRICE,"
        b"LOW PRICE,CLOSE PRICE,NET TRADED VALUE,NET TRADED QTY,TRADES,52 WEEK HIGH,"
        b"52 WEEK LOW,UNDERLYING\n"
        + f"N,EQ,{symbol},{security_name},250,251,252,250,251.5,1000,4,2,260,200,".encode()
        + b"NIFTY 50\n"
    )
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"etf{observation_date:%d%m%y}.csv", content)
    return target.getvalue()


def _nse_pre_etf_daily_zip(observation_date: date) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"Pr{observation_date:%d%m%y}.csv", b"historical price report\n")
        archive.writestr("Readme.txt", b"official press-report archive\n")
    return target.getvalue()


def _legacy_nse_bhavcopy(*, isin: str, symbol: str = "ABFRLPP1", series: str = "E1") -> bytes:
    return (
        b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,"
        b"TIMESTAMP,TOTALTRADES,ISIN,\n"
        + f"{symbol},{series},135.1,136,133.1,135.5,133.1,139.4,20362,2749956.3,".encode()
        + f"16-FEB-2021,29,{isin},\n".encode()
    )


def _legacy_nse_pre_isin_bhavcopy(*, symbol: str = "NIFTYBEES", series: str = "EQ") -> bytes:
    return (
        b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,"
        b"TIMESTAMP,\n"
        + f"{symbol},{series},537,544.45,537,539.2,537.99,537.23,80290,".encode()
        + b"43370935.61,21-JUN-2011,\n"
    )


def _combine_legacy_nse_bhavcopies(*payloads: bytes) -> bytes:
    lines = [payload.decode().splitlines() for payload in payloads]
    assert lines and all(document[0] == lines[0][0] for document in lines)
    return (
        "\n".join([lines[0][0], *(row for document in lines for row in document[1:])]) + "\n"
    ).encode()


def _zip_single_file(member_name: str, content: bytes) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, content)
    return target.getvalue()


def _bse_market(observation_date: date, *, security_id: int = 500101) -> bytes:
    row = {
        "Scrip_cd": security_id,
        "EtfAlias_Name": "NIFTYBEES",
        "ScripName": "NIP IND ETF NIFTY BEES",
        "TYPE": None,
        "Ltradert": 251.5,
        "change_val": 1.5,
        "HighRate": "252.00/250.00",
        "LowRate": 250.0,
        "prevDayClose": "250.00/250.00",
        "OpenRate": 251.0,
        "Wtdavg": 251.2,
        "totalVol": 4,
        "Turnover": 0.01,
        "TwoWeekAvg": 4.0,
        "Uchklmt": "275.00/225.00",
        "Lchklmt": 225.0,
        "week_High": 252.0,
        "week_Low": 250.0,
        "Month_High": 252.0,
        "Month_Low": 250.0,
        "52Week_High": "260.00/200.00",
        "52Week_Low": 200.0,
        "URL": f"https://www.bseindia.com/stock-share-price/x/x/{security_id}/",
        "DT_TM": f"{observation_date.isoformat()}T15:18:23.977",
        "Nsurl": f"https://www.bseindia.com/stock-share-price/x/x/{security_id}/",
        "Best_Buy_Rate": 251.0,
        "Best_Sale_Rate": 251.5,
        "Total_Sale_Qty": 2,
        "Total_Buy_Qty": 2,
        "Change_Percentage": 0.6,
    }
    return json.dumps({"Table": [row]}).encode()


def _bse_missing_bhavcopy_page() -> bytes:
    return b"""<!DOCTYPE html><html lang="en" data-critters-container=""><head>
    <title>LIVE Stock/Share Market | Indian Stock/Share Market LIVE | BSE SENSEX | BSE</title>
    <meta name="description" content="bseindia.com">
    </head><body></body></html>"""


def _legacy_bse_bhavcopy(
    *,
    security_id: str = "533230",
    symbol: str = "HDFCMFGETF",
    close: str = "0.00",
) -> bytes:
    return (
        b"SC_CODE,SC_NAME,SC_GROUP,SC_TYPE,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,"
        b"NO_TRADES,NO_OF_SHRS,NET_TURNOV,TDCLOINDI\n"
        + f"{security_id},{symbol},E,Q,2874.99,2875.00,2863.00,{close},2874.70,".encode()
        + b"2855.00,53,230,660439.00,\n"
    )


def _bse_categorized_market(observation_date: date) -> bytes:
    payload = json.loads(_bse_market(observation_date))
    row = payload["Table"][0]
    del row["ScripName"]
    row["ETFNAME"] = "Benchmark"
    row["TYPE"] = "EQUITY"
    return json.dumps(payload).encode()


_UDIFF_FIELDS = [
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


def _udiff(
    exchange: str,
    observation_date: date,
    *,
    security_id: str,
    isin: str = "INF204KB14I2",
    symbol: str = "NIFTYBEES",
    close: str = "251.5",
    open_value: str | None = None,
    high_value: str | None = None,
    low_value: str | None = None,
    last_value: str | None = None,
    series: str | None = None,
    security_name: str = "NIP IND ETF NIFTY BEES",
) -> bytes:
    row = {field: "" for field in _UDIFF_FIELDS}
    row.update(
        {
            "TradDt": observation_date.isoformat(),
            "BizDt": observation_date.isoformat(),
            "Sgmt": "CM",
            "Src": exchange,
            "FinInstrmTp": "STK",
            "FinInstrmId": security_id,
            "ISIN": isin,
            "TckrSymb": symbol,
            "SctySrs": series or ("EQ" if exchange == "NSE" else "B"),
            "FinInstrmNm": security_name,
            "OpnPric": open_value or close,
            "HghPric": high_value or close,
            "LwPric": low_value or close,
            "ClsPric": close,
            "LastPric": close if last_value is None else last_value,
            "PrvsClsgPric": close,
            "TtlTradgVol": "4",
            "TtlTrfVal": "1006",
            "TtlNbOfTxsExctd": "2",
            "SsnId": "F1",
            "NewBrdLotQty": "1",
        }
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=_UDIFF_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerow(row)
    return output.getvalue().encode()


def _combine_udiff(*payloads: bytes) -> bytes:
    lines = [payload.decode().splitlines() for payload in payloads]
    assert lines and all(document[0] == lines[0][0] for document in lines)
    return (
        "\n".join([lines[0][0], *(row for document in lines for row in document[1:])]) + "\n"
    ).encode()


def test_official_parsers_accept_exact_source_shapes_and_reject_drift() -> None:
    observation_date = date(2026, 8, 20)

    assert NiftyIndexMappingParser().parse(_nifty_mapping())[0].display_name == "Nifty 50"
    assert NiftyPriceParser().parse(_nifty_price())[0].close_value.as_tuple().exponent == -2
    assert NiftyTotalReturnParser().parse(_nifty_total_return())[0].net_total_return is not None
    nse_master = NseEtfMasterParser().parse(_nse_master())[0]
    assert nse_master.isin == "INF204KB14I2"
    assert nse_master.underlying == "Nifty 50"
    assert BseEtfMarketParser().parse(_bse_market(observation_date))[0].security_id == "500101"
    categorized = BseEtfMarketParser().parse(_bse_categorized_market(observation_date))[0]
    assert categorized.roster_variant == "categorized_subset"
    assert (
        len(
            NseEtfDailyParser().parse(
                _nse_daily_zip(observation_date), expected_date=observation_date
            )
        )
        == 1
    )
    assert (
        len(
            UdIffBhavcopyParser().parse(
                _udiff("NSE", observation_date, security_id="123"),
                expected_exchange="NSE",
                expected_date=observation_date,
            )
        )
        == 1
    )

    malformed = json.dumps([{"INDEX_NAME": "Nifty 50"}]).encode()
    with pytest.raises(SourceParseError, match="unexpected fields"):
        NiftyPriceParser().parse(malformed)

    obsolete_nse_master = _nse_master().replace(b"Underlying Asset", b"Underlying", 1)
    with pytest.raises(SourceParseError, match="NSE ETF master expected fields"):
        NseEtfMasterParser().parse(obsolete_nse_master)


def test_legacy_nse_parser_normalizes_only_observed_dummy_isin_marker() -> None:
    row = LegacyNseBhavcopyParser().parse(
        _legacy_nse_bhavcopy(isin="DUMMY"),
        expected_date=date(2021, 2, 16),
    )[0]

    assert row.symbol == "ABFRLPP1"
    assert row.series == "E1"
    assert row.isin is None

    with pytest.raises(SourceParseError, match="invalid ISIN 'UNKNOWN'"):
        LegacyNseBhavcopyParser().parse(
            _legacy_nse_bhavcopy(isin="UNKNOWN"),
            expected_date=date(2021, 2, 16),
        )


def test_legacy_nse_parser_preserves_unavailable_pre_isin_fields() -> None:
    row = LegacyNseBhavcopyParser().parse(
        _legacy_nse_pre_isin_bhavcopy(),
        expected_date=date(2011, 6, 21),
    )[0]

    assert row.symbol == "NIFTYBEES"
    assert row.isin is None
    assert row.trade_count is None
    assert row.close_value == Decimal("539.2")

    with pytest.raises(SourceParseError, match="legacy NSE bhavcopy fields changed"):
        LegacyNseBhavcopyParser().parse(
            _legacy_nse_pre_isin_bhavcopy().replace(b"21-JUN-2011", b"22-JUN-2011"),
            expected_date=date(2011, 6, 22),
        )


def test_legacy_nse_parser_accepts_only_date_matched_redundant_official_zip_path() -> None:
    observation_date = date(2020, 7, 13)
    filename = "cm13JUL2020bhav.csv"
    content = (
        _legacy_nse_bhavcopy(isin="INF204KB14I2", symbol="NIFTYBEES", series="EQ")
        .replace(b"TOTALTRADES,ISIN,\n", b"TOTALTRADES,ISIN\n")
        .replace(b"16-FEB-2021,29,INF204KB14I2,\n", b"13-Jul-20,29,INF204KB14I2\n")
    )

    rows = LegacyNseBhavcopyParser().parse(
        _zip_single_file(f"{filename}/{filename}", content),
        expected_date=observation_date,
    )

    assert len(rows) == 1
    assert rows[0].isin == "INF204KB14I2"

    for unsafe_name in (
        f"../{filename}",
        f"wrong/{filename}",
        "cm14JUL2020bhav.csv",
    ):
        with pytest.raises(SourceParseError, match="unsafe or non-CSV filename"):
            LegacyNseBhavcopyParser().parse(
                _zip_single_file(unsafe_name, content),
                expected_date=observation_date,
            )


def test_legacy_nse_parser_decouples_compact_header_from_observed_date_formats() -> None:
    compact_four_digit = (
        _legacy_nse_bhavcopy(isin="INF204KB14I2", symbol="NIFTYBEES", series="EQ")
        .replace(b"TOTALTRADES,ISIN,\n", b"TOTALTRADES,ISIN\n")
        .replace(b"16-FEB-2021,29,INF204KB14I2,\n", b"10-JUL-2017,29,INF204KB14I2\n")
    )

    rows = LegacyNseBhavcopyParser().parse(
        compact_four_digit,
        expected_date=date(2017, 7, 10),
    )

    assert len(rows) == 1
    assert rows[0].observation_date == date(2017, 7, 10)

    malformed_date = compact_four_digit.replace(b"10-JUL-2017", b"2017-07-10")
    with pytest.raises(SourceParseError, match="invalid date '2017-07-10'"):
        LegacyNseBhavcopyParser().parse(
            malformed_date,
            expected_date=date(2017, 7, 10),
        )


def test_nse_daily_parser_classifies_single_wrong_date_etf_member_as_not_published() -> None:
    requested_date = date(2024, 4, 6)
    contained_date = date(2024, 6, 4)

    with pytest.raises(SourceNotPublishedError, match=r"etf040624\.csv.*2024-06-04"):
        NseEtfDailyParser().parse(
            _nse_daily_zip(contained_date),
            expected_date=requested_date,
        )


def test_nse_daily_parser_accepts_exact_date_matched_nupr_directory() -> None:
    observation_date = date(2013, 1, 10)
    source = _nse_daily_zip(observation_date)
    root_name = f"etf{observation_date:%d%m%y}.csv"
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        csv_content = archive.read(root_name)

    rows = NseEtfDailyParser().parse(
        _zip_single_file(f"nupr{observation_date:%d%m%y}/{root_name}", csv_content),
        expected_date=observation_date,
    )

    assert len(rows) == 1
    assert rows[0].symbol == "NIFTYBEES"

    with pytest.raises(SourceParseError, match="exactly one date-matching ETF CSV"):
        NseEtfDailyParser().parse(
            _zip_single_file(f"wrong{observation_date:%d%m%y}/{root_name}", csv_content),
            expected_date=observation_date,
        )


def test_nse_daily_parser_rejects_archive_without_a_dated_etf_member() -> None:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Readme.txt", b"no ETF report")

    with pytest.raises(SourceParseError, match="exactly one date-matching ETF CSV"):
        NseEtfDailyParser().parse(
            target.getvalue(),
            expected_date=date(2024, 4, 6),
        )


def test_nse_daily_parser_accepts_verified_pre_etf_report_boundary() -> None:
    assert (
        NseEtfDailyParser().parse(
            _nse_pre_etf_daily_zip(date(2010, 3, 5)),
            expected_date=date(2010, 3, 5),
        )
        == []
    )

    with pytest.raises(SourceParseError, match="exactly one date-matching ETF CSV"):
        NseEtfDailyParser().parse(
            _nse_pre_etf_daily_zip(date(2010, 3, 8)),
            expected_date=date(2010, 3, 8),
        )


def test_nifty_price_parser_preserves_official_close_only_history() -> None:
    row = NiftyPriceParser().parse(_nifty_close_only_price())[0]

    assert row.observation_date == date(1990, 7, 3)
    assert row.open_value is None
    assert row.high_value is None
    assert row.low_value is None
    assert row.close_value.as_tuple().exponent == -2


def test_nifty_price_parser_rejects_unproven_missing_markers_and_missing_close() -> None:
    document = json.loads(_nifty_close_only_price())
    document[0]["OPEN"] = "NA"
    with pytest.raises(SourceParseError, match="field OPEN is not decimal"):
        NiftyPriceParser().parse(json.dumps(document).encode())

    document[0]["OPEN"] = "-"
    document[0]["CLOSE"] = "-"
    with pytest.raises(SourceParseError, match="field CLOSE is not decimal"):
        NiftyPriceParser().parse(json.dumps(document).encode())


def test_nifty_total_return_parser_preserves_official_missing_ntr_marker() -> None:
    row = NiftyTotalReturnParser().parse(_nifty_total_return("-"))[0]

    assert row.gross_total_return.as_tuple().exponent == -2
    assert row.net_total_return is None


def test_nifty_total_return_parser_rejects_unproven_ntr_marker() -> None:
    with pytest.raises(SourceParseError, match="field NTR_Value is not decimal"):
        NiftyTotalReturnParser().parse(_nifty_total_return("NA"))


def test_nifty_price_parser_omits_internally_inconsistent_ohlc_without_guessing() -> None:
    row = NiftyPriceParser().parse(_nifty_invalid_ohlc_price())[0]

    assert row.open_value is None
    assert row.high_value is None
    assert row.low_value is None
    assert str(row.close_value) == "770.85"
    assert row.ohlc_issue is not None
    assert "high=765.95" in row.ohlc_issue


def test_legacy_bse_parser_marks_zero_previous_close_as_valid_source_value() -> None:
    payload = (
        b"SC_CODE,SC_NAME,SC_GROUP,SC_TYPE,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,"
        b"NO_TRADES,NO_OF_SHRS,NET_TURNOV,TDCLOINDI\n"
        b"500001,TEST ETF,B,Q,10,11,9,10.5,10.5,0,2,4,42,\n"
    )

    row = LegacyBseBhavcopyParser().parse(payload, expected_date=date(2024, 7, 5))[0]

    assert row.previous_close == 0
    assert row.isin is None


def test_legacy_bse_parser_preserves_empty_previous_close_as_unavailable() -> None:
    payload = (
        b"SC_CODE,SC_NAME,SC_GROUP,SC_TYPE,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,"
        b"NO_TRADES,NO_OF_SHRS,NET_TURNOV,TDCLOINDI\n"
        b"946003,KMILSRVIII,F,D,767925.00,767930.00,767925.00,767927.66,767925.00,,"
        b"9,12,9215146.00,\n"
    )

    row = LegacyBseBhavcopyParser().parse(payload, expected_date=date(2012, 1, 25))[0]

    assert row.security_id == "946003"
    assert row.previous_close is None
    assert str(row.close_value) == "767927.66"

    with pytest.raises(SourceParseError, match="field PREVCLOSE is not decimal"):
        LegacyBseBhavcopyParser().parse(
            payload.replace(b",,9,12", b",-,9,12"),
            expected_date=date(2012, 1, 25),
        )


def test_legacy_bse_parser_omits_zero_open_without_rejecting_full_bhavcopy() -> None:
    payload = (
        b"SC_CODE,SC_NAME,SC_GROUP,SC_TYPE,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,"
        b"NO_TRADES,NO_OF_SHRS,NET_TURNOV,TDCLOINDI\n"
        b"511672,SCANSTL     ,XC,Q,0.00,14.80,14.80,14.80,14.80,14.80,1,630000,"
        b"9324000.00,\n"
    )

    row = LegacyBseBhavcopyParser().parse(payload, expected_date=date(2016, 3, 29))[0]

    assert row.security_id == "511672"
    assert row.open_value is None
    assert row.high_value is None
    assert row.low_value is None
    assert str(row.close_value) == "14.80"
    assert row.ohlc_issue is not None
    assert "source_row=2" in row.ohlc_issue
    assert "open=0.00" in row.ohlc_issue

    with pytest.raises(SourceParseError, match="field OPEN must be finite and non-negative"):
        LegacyBseBhavcopyParser().parse(
            payload.replace(b",0.00,14.80", b",-0.01,14.80"),
            expected_date=date(2016, 3, 29),
        )


def test_legacy_bse_parser_preserves_blank_descriptive_name_as_unavailable() -> None:
    row = LegacyBseBhavcopyParser().parse(
        _legacy_bse_bhavcopy(security_id="526225", symbol="            ", close="12.01"),
        expected_date=date(2013, 10, 31),
    )[0]

    assert row.security_id == "526225"
    assert row.symbol is None
    assert row.security_name is None
    assert str(row.close_value) == "12.01"


def test_legacy_bse_parser_selects_exact_dated_csv_from_multi_file_archive() -> None:
    observation_date = date(2011, 10, 13)
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("EQ131011.CSV", _legacy_bse_bhavcopy())
        archive.writestr("BD021111.dbf", b"unrelated debt-market payload")

    rows = LegacyBseBhavcopyParser().parse(target.getvalue(), expected_date=observation_date)

    assert len(rows) == 1
    assert rows[0].observation_date == observation_date

    wrong_date = io.BytesIO()
    with zipfile.ZipFile(wrong_date, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("EQ141011.CSV", _legacy_bse_bhavcopy())
    with pytest.raises(SourceParseError, match="exactly one date-matching equity CSV"):
        LegacyBseBhavcopyParser().parse(
            wrong_date.getvalue(),
            expected_date=observation_date,
        )

    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("EQ131011.CSV", _legacy_bse_bhavcopy())
        archive.writestr("eq131011.csv", _legacy_bse_bhavcopy())
    with pytest.raises(SourceParseError, match="exactly one date-matching equity CSV"):
        LegacyBseBhavcopyParser().parse(
            duplicate.getvalue(),
            expected_date=observation_date,
        )


def test_legacy_bse_parser_preserves_zero_close_as_unavailable_observation() -> None:
    row = LegacyBseBhavcopyParser().parse(_legacy_bse_bhavcopy(), expected_date=date(2018, 4, 18))[
        0
    ]

    assert row.close_value == 0
    assert row.close_issue is not None
    assert "observation omitted" in row.close_issue
    assert "last=2874.70" in row.close_issue

    with pytest.raises(SourceParseError, match="field CLOSE must be finite and non-negative"):
        LegacyBseBhavcopyParser().parse(
            _legacy_bse_bhavcopy(close="-1"), expected_date=date(2018, 4, 18)
        )


def test_exchange_parser_omits_internally_inconsistent_ohlc_without_guessing() -> None:
    row = UdIffBhavcopyParser().parse(
        _udiff(
            "BSE",
            date(2026, 7, 29),
            security_id="543925",
            close="142.50",
            open_value="144.00",
            high_value="144.00",
            low_value="144.00",
            series="IF",
            security_name="Maple Infrastructure Trust",
        ),
        expected_exchange="BSE",
        expected_date=date(2026, 7, 29),
    )[0]

    assert row.open_value is None
    assert row.high_value is None
    assert row.low_value is None
    assert str(row.close_value) == "142.50"
    assert row.ohlc_issue is not None
    assert "source_row=2" in row.ohlc_issue


def test_udiff_parser_preserves_exact_missing_last_price_without_inference() -> None:
    observation_date = date(2024, 9, 30)
    payload = _udiff(
        "NSE",
        observation_date,
        security_id="17505",
        isin="INE148I07OO1",
        symbol="SCLZC25B",
        close="1099.11",
        last_value="",
        series="AT",
        security_name="SEC RE NCD SR III",
    )

    row = UdIffBhavcopyParser().parse(
        payload,
        expected_exchange="NSE",
        expected_date=observation_date,
    )[0]

    assert row.last_value is None
    assert str(row.close_value) == "1099.11"


def test_udiff_parser_rejects_unproven_missing_last_price_marker() -> None:
    observation_date = date(2024, 9, 30)
    payload = _udiff(
        "NSE",
        observation_date,
        security_id="17505",
        last_value="NA",
    )

    with pytest.raises(SourceParseError, match="field LastPric is not decimal"):
        UdIffBhavcopyParser().parse(
            payload,
            expected_exchange="NSE",
            expected_date=observation_date,
        )


def test_nifty_sync_is_resumable_and_retains_repeated_source_provenance(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    downloader.add("nifty_index_mapping", _nifty_mapping(), _nifty_mapping())
    downloader.add("nifty_price_history", _nifty_price())
    downloader.add("nifty_total_return_history", _nifty_total_return())
    service = _service(tmp_path, session, downloader)

    first = service.sync_nifty_indices(
        index_names=("Nifty 50",),
        start_date=date(2020, 1, 1),
        end_date=date(2020, 1, 3),
    )
    second = service.sync_nifty_indices(
        index_names=("NIFTY 50",),
        start_date=date(2020, 1, 1),
        end_date=date(2020, 1, 3),
    )

    assert first.status == "completed"
    assert first.rows_inserted == 3
    assert second.requests_skipped == 2
    assert session.scalar(select(func.count()).select_from(BenchmarkInstrumentRecord)) == 3
    assert session.scalar(select(func.count()).select_from(BenchmarkObservationRecord)) == 3
    assert session.scalar(select(func.count()).select_from(BenchmarkObservationSourceRecord)) == 3
    assert session.scalar(select(func.count()).select_from(BenchmarkSyncCheckpointRecord)) == 2


def test_nifty_sync_persists_close_and_issue_for_invalid_source_ohlc(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    downloader.add("nifty_index_mapping", _nifty_mapping())
    downloader.add("nifty_price_history", _nifty_invalid_ohlc_price())
    downloader.add("nifty_total_return_history", _nifty_total_return())

    result = _service(tmp_path, session, downloader).sync_nifty_indices(
        index_names=("Nifty 50",),
        start_date=date(2002, 12, 30),
        end_date=date(2002, 12, 30),
    )

    assert result.status == "completed_with_issues"
    assert result.issue_counts == {"invalid_ohlc": 1}
    price_observation = session.scalar(
        select(BenchmarkObservationRecord)
        .join(
            BenchmarkInstrumentRecord,
            BenchmarkInstrumentRecord.id == BenchmarkObservationRecord.benchmark_instrument_id,
        )
        .where(BenchmarkInstrumentRecord.instrument_type == "price_index")
    )
    assert price_observation is not None
    assert price_observation.open_value is None
    assert price_observation.high_value is None
    assert price_observation.low_value is None
    assert str(price_observation.close_value) == "770.85"
    issue = session.scalar(select(BenchmarkIssueRecord))
    assert issue is not None
    assert issue.issue_code == "invalid_ohlc"
    assert issue.observation_date == date(2002, 12, 30)


def test_exchange_sync_uses_daily_nse_etf_evidence_and_keeps_exchanges_separate(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    observation_date = date(2026, 8, 20)
    downloader.add("nse_etf_security_master", _nse_master())
    downloader.add("bse_etf_market_watch", _bse_market(observation_date))
    downloader.add("nse_daily_etf_report", _nse_daily_zip(observation_date))
    downloader.add(
        "exchange_bhavcopy",
        _udiff("NSE", observation_date, security_id="101"),
        _udiff("BSE", observation_date, security_id="500101"),
    )

    service = _service(tmp_path, session, downloader)
    result = service.sync_exchange_etfs(
        start_date=observation_date,
        end_date=observation_date,
    )
    report = service.coverage_report()

    assert result.status == "completed"
    assert result.rows_inserted == 2
    observations = session.scalars(
        select(BenchmarkObservationRecord).order_by(BenchmarkObservationRecord.exchange)
    ).all()
    assert [row.exchange for row in observations] == ["BSE", "NSE"]
    assert all(row.identity_status == "official" for row in observations)
    assert session.scalar(select(func.count()).select_from(BenchmarkExchangeListingRecord)) == 2
    assert report.exchange_observation_counts == {"BSE": 1, "NSE": 1}
    assert report.observation_date_ranges["etf"] == {
        "first_date": "2026-08-20",
        "last_date": "2026-08-20",
    }
    assert report.latest_sync_runs["exchange_etf"]["issue_counts"] == {}


def test_exchange_sync_ignores_unrelated_legacy_nse_dummy_isin(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    observation_date = date(2021, 2, 16)
    downloader.add("nse_etf_security_master", _nse_master())
    downloader.add("nse_daily_etf_report", _nse_daily_zip(observation_date))
    downloader.add(
        "exchange_bhavcopy",
        _combine_legacy_nse_bhavcopies(
            _legacy_nse_bhavcopy(isin="DUMMY"),
            _legacy_nse_bhavcopy(
                isin="INF204KB14I2",
                symbol="NIFTYBEES",
                series="EQ",
            ),
        ),
    )

    result = _service(tmp_path, session, downloader).sync_exchange_etfs(
        start_date=observation_date,
        end_date=observation_date,
        exchanges=("NSE",),
    )

    assert result.status == "completed"
    assert result.rows_inserted == 1
    observation = session.scalar(select(BenchmarkObservationRecord))
    assert observation is not None
    assert observation.exchange == "NSE"
    instrument = session.get(BenchmarkInstrumentRecord, observation.benchmark_instrument_id)
    assert instrument is not None
    assert instrument.isin == "INF204KB14I2"


def test_exchange_sync_reports_pre_isin_nse_etf_without_failing(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    observation_date = date(2011, 6, 21)
    downloader.add("nse_etf_security_master", _nse_master())
    downloader.add("nse_daily_etf_report", _nse_daily_zip(observation_date))
    downloader.add("exchange_bhavcopy", _legacy_nse_pre_isin_bhavcopy())

    result = _service(tmp_path, session, downloader).sync_exchange_etfs(
        start_date=observation_date,
        end_date=observation_date,
        exchanges=("NSE",),
    )

    assert result.status == "completed_with_issues"
    assert result.requests_completed == 1
    assert result.requests_failed == 0
    assert result.rows_inserted == 0
    assert result.issue_counts == {"identity_unresolved": 1}
    assert session.scalar(select(func.count()).select_from(BenchmarkObservationRecord)) == 0
    issue = session.scalar(select(BenchmarkIssueRecord))
    assert issue is not None
    assert "has no official ISIN in the bhavcopy" in issue.details


def test_exchange_sync_checkpoints_pre_etf_report_source_period(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    observation_date = date(2010, 3, 5)
    downloader.add("nse_etf_security_master", _nse_master())
    downloader.add("nse_daily_etf_report", _nse_pre_etf_daily_zip(observation_date))

    result = _service(tmp_path, session, downloader).sync_exchange_etfs(
        start_date=observation_date,
        end_date=observation_date,
        exchanges=("NSE",),
    )

    assert result.status == "completed"
    assert result.requests_completed == 1
    assert result.requests_failed == 0
    assert result.rows_inserted == 0
    assert result.issue_counts == {"empty_source_period": 1}
    assert session.scalar(select(func.count()).select_from(BenchmarkObservationRecord)) == 0
    checkpoint = session.scalar(select(BenchmarkSyncCheckpointRecord))
    assert checkpoint is not None
    assert checkpoint.period_start == observation_date
    assert checkpoint.rows_received == 0


def test_legacy_nse_parser_preserves_exact_truncated_ine_as_unavailable() -> None:
    payload = _legacy_nse_bhavcopy(isin="INE", symbol="ICICI", series="M1").replace(
        b"16-FEB-2021",
        b"06-NOV-2013",
    )

    row = LegacyNseBhavcopyParser().parse(payload, expected_date=date(2013, 11, 6))[0]

    assert row.symbol == "ICICI"
    assert row.series == "M1"
    assert row.isin is None

    with pytest.raises(SourceParseError, match="invalid ISIN 'IN'"):
        LegacyNseBhavcopyParser().parse(
            payload.replace(b",INE,\n", b",IN,\n"),
            expected_date=date(2013, 11, 6),
        )


def test_bse_sync_ignores_invalid_ohlc_on_unrelated_non_etf_row(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    observation_date = date(2026, 7, 29)
    downloader.add("nse_etf_security_master", _nse_master())
    downloader.add("bse_etf_market_watch", _bse_market(observation_date))
    unrelated_row = _udiff(
        "BSE",
        observation_date,
        security_id="543925",
        isin="INE0M5S23019",
        symbol="MIT",
        close="142.50",
        open_value="144.00",
        high_value="144.00",
        low_value="144.00",
        series="IF",
        security_name="Maple Infrastructure Trust",
    )
    etf_row = _udiff("BSE", observation_date, security_id="500101")
    downloader.add("exchange_bhavcopy", _combine_udiff(unrelated_row, etf_row))

    result = _service(tmp_path, session, downloader).sync_exchange_etfs(
        start_date=observation_date,
        end_date=observation_date,
        exchanges=("BSE",),
    )

    assert result.status == "completed"
    assert result.rows_received == 1
    assert result.rows_inserted == 1
    assert result.issue_counts == {}
    observation = session.scalar(select(BenchmarkObservationRecord))
    assert observation is not None
    assert observation.exchange == "BSE"


def test_bse_sync_retains_close_and_issue_for_invalid_etf_ohlc(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    observation_date = date(2026, 7, 29)
    downloader.add("nse_etf_security_master", _nse_master())
    downloader.add("bse_etf_market_watch", _bse_market(observation_date))
    downloader.add(
        "exchange_bhavcopy",
        _udiff(
            "BSE",
            observation_date,
            security_id="500101",
            close="142.50",
            open_value="144.00",
            high_value="144.00",
            low_value="144.00",
        ),
    )

    result = _service(tmp_path, session, downloader).sync_exchange_etfs(
        start_date=observation_date,
        end_date=observation_date,
        exchanges=("BSE",),
    )

    assert result.status == "completed_with_issues"
    assert result.rows_inserted == 1
    assert result.issue_counts == {"invalid_ohlc": 1}
    observation = session.scalar(select(BenchmarkObservationRecord))
    assert observation is not None
    assert observation.open_value is None
    assert observation.high_value is None
    assert observation.low_value is None
    assert str(observation.close_value) == "142.50"
    issue = session.scalar(select(BenchmarkIssueRecord))
    assert issue is not None
    assert issue.issue_code == "invalid_ohlc"
    assert issue.observation_date == observation_date


def test_bse_sync_omits_zero_close_etf_without_substituting_last_price(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    current_date = date(2026, 8, 20)
    historical_date = date(2018, 4, 18)
    security_id = 533230
    downloader.add("nse_etf_security_master", _nse_master(), _nse_master())
    downloader.add(
        "bse_etf_market_watch",
        _bse_market(current_date, security_id=security_id),
        _bse_market(current_date, security_id=security_id),
    )
    downloader.add(
        "exchange_bhavcopy",
        _udiff("BSE", current_date, security_id=str(security_id)),
        _legacy_bse_bhavcopy(security_id=str(security_id)),
    )
    service = _service(tmp_path, session, downloader)
    service.sync_exchange_etfs(
        start_date=current_date,
        end_date=current_date,
        exchanges=("BSE",),
    )

    result = service.sync_exchange_etfs(
        start_date=historical_date,
        end_date=historical_date,
        exchanges=("BSE",),
    )

    assert result.status == "completed_with_issues"
    assert result.rows_received == 1
    assert result.rows_inserted == 0
    assert result.issue_counts == {"invalid_close": 1, "provisional_bse_identity": 1}
    assert (
        session.scalar(
            select(func.count())
            .select_from(BenchmarkObservationRecord)
            .where(BenchmarkObservationRecord.observation_date == historical_date)
        )
        == 0
    )
    issue = session.scalar(
        select(BenchmarkIssueRecord).where(BenchmarkIssueRecord.issue_code == "invalid_close")
    )
    assert issue is not None
    assert issue.severity == "error"
    assert "last=2874.70" in issue.details


@pytest.mark.parametrize("observation_date", [date(2024, 7, 7), date(2026, 8, 23)])
def test_bse_generic_homepage_for_missing_daily_file_is_audited_and_not_checkpointed(
    tmp_path: Path, observation_date: date
) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    downloader.add("nse_etf_security_master", _nse_master())
    downloader.add("bse_etf_market_watch", _bse_market(observation_date))
    downloader.add("exchange_bhavcopy", _bse_missing_bhavcopy_page())

    result = _service(tmp_path, session, downloader).sync_exchange_etfs(
        start_date=observation_date,
        end_date=observation_date,
        exchanges=("BSE",),
    )

    assert result.status == "completed"
    assert result.requests_completed == 1
    assert result.requests_failed == 0
    assert result.issue_counts == {"empty_trading_day": 1}
    assert session.scalar(select(func.count()).select_from(BenchmarkObservationRecord)) == 0
    assert session.scalar(select(func.count()).select_from(BenchmarkSyncCheckpointRecord)) == 0
    issue = session.scalar(select(BenchmarkIssueRecord))
    assert issue is not None
    assert issue.severity == "info"
    assert "generic BSE homepage" in issue.details


def test_nse_wrong_date_press_report_is_audited_and_not_checkpointed(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    requested_date = date(2024, 4, 6)
    downloader.add("nse_etf_security_master", _nse_master())
    downloader.add("nse_daily_etf_report", _nse_daily_zip(date(2024, 6, 4)))

    result = _service(tmp_path, session, downloader).sync_exchange_etfs(
        start_date=requested_date,
        end_date=requested_date,
        exchanges=("NSE",),
    )

    assert result.status == "completed"
    assert result.requests_completed == 1
    assert result.requests_failed == 0
    assert result.issue_counts == {"empty_trading_day": 1}
    assert session.scalar(select(func.count()).select_from(BenchmarkObservationRecord)) == 0
    assert session.scalar(select(func.count()).select_from(BenchmarkSyncCheckpointRecord)) == 0
    issue = session.scalar(select(BenchmarkIssueRecord))
    assert issue is not None
    assert issue.severity == "info"
    assert "etf040624.csv" in issue.details


def test_bse_unrecognized_html_still_fails_strict_parsing() -> None:
    with pytest.raises(SourceParseError, match="expected 34 fields"):
        UdIffBhavcopyParser().parse(
            b"<!DOCTYPE html><html><head><title>Access denied</title></head></html>",
            expected_exchange="BSE",
            expected_date=date(2026, 8, 23),
        )

    with pytest.raises(SourceParseError, match="legacy BSE bhavcopy fields changed"):
        LegacyBseBhavcopyParser().parse(
            b"<!DOCTYPE html><html><head><title>Access denied</title></head></html>",
            expected_date=date(2024, 7, 7),
        )


def test_legacy_bse_parser_recognizes_only_observed_missing_file_homepage() -> None:
    with pytest.raises(SourceNotPublishedError, match="generic BSE homepage"):
        LegacyBseBhavcopyParser().parse(
            _bse_missing_bhavcopy_page(),
            expected_date=date(2024, 7, 7),
        )


def test_nse_security_id_is_versioned_across_isin_conversion(tmp_path: Path) -> None:
    session = _session(tmp_path)
    downloader = _QueuedBenchmarkDownloader()
    current_date = date(2026, 7, 31)
    prior_date = date(2026, 7, 30)
    symbol = "IVZINNIFTY"
    current_isin = "INF205KA1CC7"
    prior_isin = "INF205K01DA9"
    downloader.add(
        "nse_etf_security_master",
        _nse_master(symbol=symbol, security_name="InvescoMF-NiftyETF", isin=current_isin),
    )
    downloader.add(
        "nse_daily_etf_report",
        _nse_daily_zip(current_date, symbol=symbol, security_name="INVESCO INDIA NIFTY ETF"),
        _nse_daily_zip(prior_date, symbol=symbol, security_name="INVESCO INDIA NIFTY ETF"),
    )
    downloader.add(
        "exchange_bhavcopy",
        _udiff(
            "NSE",
            current_date,
            security_id="24217",
            isin=current_isin,
            symbol=symbol,
            close="280.58",
        ),
        _udiff(
            "NSE",
            prior_date,
            security_id="24217",
            isin=prior_isin,
            symbol=symbol,
            close="2786.51",
        ),
    )

    result = _service(tmp_path, session, downloader).sync_exchange_etfs(
        start_date=prior_date,
        end_date=current_date,
        exchanges=("NSE",),
    )

    assert result.status == "completed_with_issues"
    assert result.issue_counts == {"security_identity_change": 1}
    instruments = session.scalars(
        select(BenchmarkInstrumentRecord)
        .where(BenchmarkInstrumentRecord.instrument_type == "etf")
        .order_by(BenchmarkInstrumentRecord.isin)
    ).all()
    assert [instrument.isin for instrument in instruments] == [prior_isin, current_isin]
    listings = session.scalars(
        select(BenchmarkExchangeListingRecord).where(
            BenchmarkExchangeListingRecord.exchange == "NSE",
            BenchmarkExchangeListingRecord.source_security_id == "24217",
        )
    ).all()
    assert len(listings) == 2
    observations = session.scalars(
        select(BenchmarkObservationRecord).order_by(BenchmarkObservationRecord.observation_date)
    ).all()
    assert [str(observation.close_value) for observation in observations] == [
        "2786.51",
        "280.58",
    ]
    assert observations[0].benchmark_instrument_id != observations[1].benchmark_instrument_id


def test_downloader_blocks_nonofficial_hosts_before_network_access() -> None:
    downloader = OfficialBenchmarkDownloader(timeout_seconds=1, max_bytes=1024)
    request = BenchmarkRequest("nse", "test", "https://example.com/data.csv", {})

    with pytest.raises(SourceDownloadError, match="approved official host"):
        downloader.download(request)

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from mf_strategy_tester.services.idcw_income_research import (
    DistributionPoint,
    PayoutYield,
    annual_yield_cutoffs,
    assess_payout_frequency,
    calculate_trailing_payout_yield,
    frequency_period_start,
)
from mf_strategy_tester.services.nav_performance import (
    NavPoint,
    RollingReturnSummary,
    calculate_nav_performance,
)

ANALYSIS_VERSION = "idcw-direct-income-screen-2026.09.1"
ROLLING_WINDOWS_YEARS = (1, 3, 5, 10)
LATEST_YIELD_THRESHOLD_PCT = Decimal(6)
DEFAULT_DATABASE = Path("data/research.db")
DEFAULT_REPORT_DIRECTORY = Path("data/research-reports")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export an auditable Direct-IDCW monthly/quarterly income screen from the local "
            "canonical distribution and NAV dataset"
        )
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="Research cutoff (default: latest canonical payout date with NAV no more than 7 days older)",
    )
    parser.add_argument(
        "--output", type=Path, help="CSV destination (default: timestamped report)"
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    database = arguments.database.resolve()
    if not database.is_file():
        raise SystemExit(f"database does not exist: {database}")
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        dataset = _dataset_metadata(connection, database)
        as_of = arguments.as_of or _default_as_of(dataset)
        if as_of > dataset["distribution_max_date"]:
            raise ValueError(
                f"as-of {as_of.isoformat()} exceeds canonical distribution maximum "
                f"{dataset['distribution_max_date'].isoformat()}"
            )
        report_timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output = arguments.output or (
            DEFAULT_REPORT_DIRECTORY
            / f"idcw-direct-income-screen-{as_of.isoformat()}-{report_timestamp}.csv"
        )
        output = output.resolve()
        rows, screening_counts, assumptions = _build_rows(connection, as_of)
        _write_csv(output, rows)
        manifest_path = output.with_suffix(".manifest.json")
        manifest = {
            "analysis_version": ANALYSIS_VERSION,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "as_of_date": as_of.isoformat(),
            "csv_path": str(output),
            "csv_sha256": _sha256(output),
            "qualifying_scheme_options": len(rows),
            "screening_counts": screening_counts,
            "assumptions": assumptions,
            "dataset": _json_safe(dataset),
            "limitations": [
                "Canonical events are accepted-source observations; events_present does not certify complete payout history.",
                "Historical scheme lifecycle and predecessor identity are incomplete for some options.",
                "Payout yield is cash distributions per unit divided by the most recent NAV on or before each cutoff; it is not total return.",
                "NAV-only rolling returns exclude IDCW cash distributions and therefore understate investor economic return for a payout option.",
                "The repository does not yet have an immutable cross-dataset snapshot identifier; database file metadata and table maxima are recorded instead.",
                "Past payouts, payout frequency, and yield do not assure future distributions or returns.",
            ],
        }
        _write_json(manifest_path, manifest)
    finally:
        connection.close()

    print(
        json.dumps(
            {"csv": str(output), "manifest": str(manifest_path), "rows": len(rows)}
        )
    )
    return 0


def _build_rows(
    connection: sqlite3.Connection, as_of: date
) -> tuple[list[dict[str, object]], dict[str, int], dict[str, object]]:
    frequency_start = frequency_period_start(as_of)
    cutoffs = annual_yield_cutoffs(as_of)
    earliest_yield_start = annual_yield_cutoffs(as_of, years=6)[0]
    distributions = _load_distributions(connection, earliest_yield_start, as_of)
    recent_months = {
        code: {
            (point.record_date.year, point.record_date.month)
            for point in points
            if point.record_date >= frequency_start
        }
        for code, points in distributions.items()
    }
    preliminary_codes = tuple(
        code for code, months in recent_months.items() if len(months) >= 16
    )
    latest_coverage_run, coverage = _latest_coverage(connection)
    rows: list[dict[str, object]] = []
    counts = {
        "canonical_options_with_events_in_yield_period": len(distributions),
        "options_with_at_least_16_distinct_payout_months": len(preliminary_codes),
        "current_direct_idcw_options_with_payout_isin": 0,
        "near_consistent_monthly_or_quarterly_options": 0,
        "options_with_recent_nav_for_latest_yield": 0,
        "options_with_latest_ttm_yield_strictly_above_6_pct": 0,
    }
    for code in preliminary_codes:
        metadata = _latest_metadata(connection, code, as_of)
        if metadata is None or not _is_direct_idcw_payout(metadata):
            continue
        counts["current_direct_idcw_options_with_payout_isin"] += 1
        option_distributions = distributions[code]
        frequency = assess_payout_frequency(
            str(metadata["scheme_name"]),
            tuple(point.record_date for point in option_distributions),
            period_start=frequency_start,
            period_end=as_of,
        )
        if frequency.frequency is None:
            continue
        counts["near_consistent_monthly_or_quarterly_options"] += 1
        latest_nav = (
            NavPoint(
                _as_date(metadata["nav_date"]), Decimal(str(metadata["nav_value"]))
            ),
        )
        latest_yield = calculate_trailing_payout_yield(
            option_distributions, latest_nav, window_end=as_of
        )
        if latest_yield.yield_pct is None:
            continue
        counts["options_with_recent_nav_for_latest_yield"] += 1
        if latest_yield.yield_pct <= LATEST_YIELD_THRESHOLD_PCT:
            continue
        counts["options_with_latest_ttm_yield_strictly_above_6_pct"] += 1

        nav_points = _load_nav_points(connection, code, as_of)
        performance = calculate_nav_performance(
            nav_points, rolling_windows_years=ROLLING_WINDOWS_YEARS
        )
        yields = tuple(
            calculate_trailing_payout_yield(
                option_distributions, nav_points, window_end=cutoff
            )
            for cutoff in cutoffs
        )
        source_kinds = _distribution_source_kinds(
            connection, code, frequency_start, as_of
        )
        row: dict[str, object] = {
            "as_of_date": as_of,
            "amfi_scheme_code": code,
            "scheme_name": metadata["scheme_name"],
            "fund_house_name": metadata["fund_house_name"],
            "scheme_classification": metadata["scheme_classification"],
            "plan_type": metadata["plan_type"],
            "option_type": metadata["option_type"],
            "isin_payout_or_growth": metadata["isin_payout_or_growth"],
            "isin_reinvestment": metadata["isin_reinvestment"],
            "payout_frequency": frequency.frequency,
            "frequency_basis": frequency.basis,
            "frequency_period_start": frequency_start,
            "frequency_period_end": as_of,
            "payout_events_5y": frequency.event_count,
            "distinct_payout_months_5y": frequency.distinct_payout_months,
            "payout_months_each_12m_block": "|".join(
                str(value) for value in frequency.annual_block_month_counts
            ),
            "median_days_between_payouts_5y": frequency.median_gap_days,
            "first_canonical_payout_date_5y": min(
                point.record_date
                for point in option_distributions
                if point.record_date >= frequency_start
            ),
            "last_canonical_payout_date": max(
                point.record_date for point in option_distributions
            ),
            "canonical_distribution_sources_5y": "|".join(source_kinds),
            "latest_coverage_run_id": latest_coverage_run.get("id"),
            "latest_coverage_status": coverage.get(code),
            "nav_observation_count": performance.observation_count,
            "nav_data_start_date": nav_points[0].nav_date,
            "nav_data_end_date": nav_points[-1].nav_date,
            "nav_return_treatment": "nav_only_distributions_excluded",
        }
        _add_yield_columns(row, latest_yield, "latest_ttm")
        for payout_yield in yields:
            _add_yield_columns(
                row,
                payout_yield,
                f"ttm_ending_{payout_yield.window_end_inclusive.isoformat()}",
            )
        for rolling in performance.rolling_returns:
            _add_rolling_columns(row, rolling)
        rows.append(row)

    rows.sort(
        key=lambda row: (
            -Decimal(str(row["latest_ttm_yield_pct"])),
            str(row["scheme_name"]),
            str(row["amfi_scheme_code"]),
        )
    )
    assumptions: dict[str, object] = {
        "universe": "current AMFI scheme option metadata classified as Direct + IDCW with a payout/growth ISIN",
        "payout_frequency": {
            "monthly": "at least 48 distinct payout months in 60 months and at least 8 in every 12-month block",
            "quarterly": "at least 16 distinct payout months in 60 months and at least 3 in every 12-month block",
            "generic_names": "cadence inferred only when median gaps are 20-45 days for monthly or 60-120 days for quarterly",
            "excluded_explicit_frequencies": "daily, weekly, fortnightly, annual, half-yearly, and semi-annual",
        },
        "latest_yield_screen": "sum of canonical cash distributions in (as_of - 1 year, as_of] / NAV on or before as_of; strictly greater than 6%",
        "historical_yields": "five successive trailing-12-month cash yields ending on the as-of anniversary",
        "nav_tolerance_days": 7,
        "rolling_returns": "all available valid current NAV observations; annualized using actual elapsed days; first NAV on/after each anniversary within 7 days",
        "rolling_windows_years": list(ROLLING_WINDOWS_YEARS),
        "rolling_return_treatment": "NAV only; IDCW cash distributions excluded",
    }
    return rows, counts, assumptions


def _dataset_metadata(connection: sqlite3.Connection, database: Path) -> dict[str, Any]:
    nav = connection.execute(
        """
        SELECT COUNT(*) AS rows, MIN(nav_date) AS min_date, MAX(nav_date) AS max_date
        FROM nav_revisions
        WHERE is_current = 1 AND quality_status = 'valid'
        """
    ).fetchone()
    distributions = connection.execute(
        """
        SELECT COUNT(*) AS rows, MIN(e.record_date) AS min_date, MAX(e.record_date) AS max_date
        FROM distribution_events e
        JOIN distribution_event_revisions r
          ON r.distribution_event_id = e.id AND r.is_current = 1
        """
    ).fetchone()
    migration = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    coverage_run, _ = _latest_coverage(connection)
    stat = database.stat()
    return {
        "database_path": str(database),
        "database_size_bytes": stat.st_size,
        "database_modified_at_utc": datetime.fromtimestamp(
            stat.st_mtime, UTC
        ).isoformat(),
        "alembic_version": migration["version_num"] if migration else None,
        "valid_current_nav_revisions": int(nav["rows"]),
        "nav_min_date": _as_date(nav["min_date"]),
        "nav_max_date": _as_date(nav["max_date"]),
        "canonical_current_distribution_events": int(distributions["rows"]),
        "distribution_min_date": _as_date(distributions["min_date"]),
        "distribution_max_date": _as_date(distributions["max_date"]),
        "latest_distribution_coverage_run": coverage_run,
    }


def _default_as_of(dataset: dict[str, Any]) -> date:
    distribution_max = dataset["distribution_max_date"]
    nav_max = dataset["nav_max_date"]
    return min(distribution_max, nav_max + timedelta(days=7))


def _load_distributions(
    connection: sqlite3.Connection, start_exclusive: date, end_inclusive: date
) -> dict[str, tuple[DistributionPoint, ...]]:
    grouped: dict[str, list[DistributionPoint]] = defaultdict(list)
    rows = connection.execute(
        """
        SELECT e.amfi_scheme_code, e.record_date, r.amount_per_unit_inr
        FROM distribution_events e
        JOIN distribution_event_revisions r
          ON r.distribution_event_id = e.id AND r.is_current = 1
        WHERE e.record_date > ? AND e.record_date <= ?
        ORDER BY e.amfi_scheme_code, e.record_date
        """,
        (start_exclusive.isoformat(), end_inclusive.isoformat()),
    )
    for row in rows:
        grouped[str(row["amfi_scheme_code"])].append(
            DistributionPoint(
                record_date=_as_date(row["record_date"]),
                amount_per_unit_inr=Decimal(str(row["amount_per_unit_inr"])),
            )
        )
    return {code: tuple(points) for code, points in grouped.items()}


def _latest_metadata(
    connection: sqlite3.Connection, code: str, as_of: date
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT n.nav_date, n.nav_value, m.scheme_name, m.fund_house_name,
               m.scheme_classification, m.isin_payout_or_growth, m.isin_reinvestment,
               m.plan_type, m.option_type
        FROM nav_revisions n
        JOIN scheme_metadata_versions m ON m.id = n.metadata_version_id
        WHERE n.amfi_scheme_code = ? AND n.nav_date <= ?
          AND n.is_current = 1 AND n.quality_status = 'valid'
        ORDER BY n.nav_date DESC
        LIMIT 1
        """,
        (code, as_of.isoformat()),
    ).fetchone()


def _is_direct_idcw_payout(metadata: sqlite3.Row) -> bool:
    return bool(
        metadata["plan_type"] == "direct"
        and metadata["option_type"] == "idcw"
        and metadata["isin_payout_or_growth"]
    )


def _load_nav_points(
    connection: sqlite3.Connection, code: str, as_of: date
) -> tuple[NavPoint, ...]:
    rows = connection.execute(
        """
        SELECT nav_date, nav_value
        FROM nav_revisions
        WHERE amfi_scheme_code = ? AND nav_date <= ?
          AND is_current = 1 AND quality_status = 'valid'
        ORDER BY nav_date
        """,
        (code, as_of.isoformat()),
    )
    return tuple(
        NavPoint(_as_date(row["nav_date"]), Decimal(str(row["nav_value"])))
        for row in rows
    )


def _latest_coverage(
    connection: sqlite3.Connection,
) -> tuple[dict[str, object], dict[str, str]]:
    run = connection.execute(
        """
        SELECT id, assessment_version, completed_at
        FROM distribution_coverage_runs
        WHERE status = 'completed'
        ORDER BY completed_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if run is None:
        return {}, {}
    assessments = connection.execute(
        """
        SELECT amfi_scheme_code, coverage_status
        FROM distribution_coverage_assessments
        WHERE coverage_run_id = ?
        """,
        (run["id"],),
    )
    return dict(run), {
        str(row["amfi_scheme_code"]): str(row["coverage_status"]) for row in assessments
    }


def _distribution_source_kinds(
    connection: sqlite3.Connection, code: str, start: date, end: date
) -> tuple[str, ...]:
    parameters = (code, start.isoformat(), end.isoformat())
    source_rows = connection.execute(
        """
        SELECT 'amfi' AS source_kind
        FROM distribution_events e
        JOIN distribution_event_revisions r
          ON r.distribution_event_id = e.id AND r.is_current = 1
        JOIN distribution_event_revision_sources s
          ON s.distribution_event_revision_id = r.id
        WHERE e.amfi_scheme_code = ? AND e.record_date >= ? AND e.record_date <= ?
        UNION
        SELECT 'official_amc_notice'
        FROM distribution_events e
        JOIN distribution_event_revisions r
          ON r.distribution_event_id = e.id AND r.is_current = 1
        JOIN distribution_event_revision_official_sources s
          ON s.distribution_event_revision_id = r.id
        WHERE e.amfi_scheme_code = ? AND e.record_date >= ? AND e.record_date <= ?
        UNION
        SELECT c.provider
        FROM distribution_events e
        JOIN distribution_event_revisions r
          ON r.distribution_event_id = e.id AND r.is_current = 1
        JOIN distribution_event_revision_rta_sources s
          ON s.distribution_event_revision_id = r.id
        JOIN rta_distribution_records d ON d.id = s.rta_distribution_record_id
        JOIN rta_scheme_captures c ON c.id = d.scheme_capture_id
        WHERE e.amfi_scheme_code = ? AND e.record_date >= ? AND e.record_date <= ?
        UNION
        SELECT 'advisorkhoj'
        FROM distribution_events e
        JOIN distribution_event_revisions r
          ON r.distribution_event_id = e.id AND r.is_current = 1
        JOIN distribution_event_revision_advisorkhoj_sources s
          ON s.distribution_event_revision_id = r.id
        WHERE e.amfi_scheme_code = ? AND e.record_date >= ? AND e.record_date <= ?
        ORDER BY source_kind
        """,
        parameters * 4,
    )
    return tuple(str(row["source_kind"]) for row in source_rows)


def _add_yield_columns(row: dict[str, object], value: PayoutYield, prefix: str) -> None:
    row[f"{prefix}_window_start_exclusive"] = value.window_start_exclusive
    row[f"{prefix}_window_end_inclusive"] = value.window_end_inclusive
    row[f"{prefix}_payout_per_unit_inr"] = value.payout_amount_per_unit_inr
    row[f"{prefix}_nav_date"] = value.nav_date
    row[f"{prefix}_nav"] = value.nav_value
    row[f"{prefix}_yield_pct"] = value.yield_pct


def _add_rolling_columns(row: dict[str, object], rolling: RollingReturnSummary) -> None:
    prefix = f"nav_only_rolling_{rolling.window_years}y"
    row[f"{prefix}_sample_count"] = rolling.sample_count
    row[f"{prefix}_latest_annualized_return_pct"] = (
        rolling.latest.annualized_return_pct if rolling.latest else None
    )
    row[f"{prefix}_latest_start_date"] = (
        rolling.latest.start_date if rolling.latest else None
    )
    row[f"{prefix}_latest_end_date"] = (
        rolling.latest.end_date if rolling.latest else None
    )
    row[f"{prefix}_minimum_annualized_return_pct"] = (
        rolling.minimum_annualized_return_pct
    )
    row[f"{prefix}_median_annualized_return_pct"] = rolling.median_annualized_return_pct
    row[f"{prefix}_mean_annualized_return_pct"] = rolling.mean_annualized_return_pct
    row[f"{prefix}_maximum_annualized_return_pct"] = (
        rolling.maximum_annualized_return_pct
    )
    row[f"{prefix}_positive_periods_pct"] = rolling.positive_periods_pct


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else ["as_of_date", "no_qualifying_options"]
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8-sig",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        if rows:
            writer.writerows(
                {key: _csv_value(value) for key, value in row.items()} for row in rows
            )
        else:
            writer.writerow({"as_of_date": "", "no_qualifying_options": "true"})
    temporary.replace(path)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _decimal_text(value: Decimal) -> str:
    text = format(value, ".6f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TypeError(f"expected ISO date string, received {type(value).__name__}")
    return date.fromisoformat(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (sqlite3.DatabaseError, ValueError) as error:
        print(f"IDCW income research export failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

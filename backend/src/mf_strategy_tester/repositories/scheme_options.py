from sqlalchemy import Select, select

from mf_strategy_tester.db.models import (
    NavRevisionRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
)


def current_scheme_snapshot(fund_house_name: str | None = None) -> Select[tuple[object, ...]]:
    """Return current descriptive metadata without treating names as option identity."""

    revision_match = (
        (NavRevisionRecord.amfi_scheme_code == SchemeOptionRecord.amfi_scheme_code)
        & (NavRevisionRecord.nav_date == SchemeOptionRecord.last_observed_nav_date)
        & NavRevisionRecord.is_current.is_(True)
    )
    latest_metadata_id = (
        select(NavRevisionRecord.metadata_version_id)
        .where(revision_match)
        .correlate(SchemeOptionRecord)
        .scalar_subquery()
    )
    latest_nav_value = (
        select(NavRevisionRecord.nav_value)
        .where(revision_match)
        .correlate(SchemeOptionRecord)
        .scalar_subquery()
    )
    latest_quality_status = (
        select(NavRevisionRecord.quality_status)
        .where(revision_match)
        .correlate(SchemeOptionRecord)
        .scalar_subquery()
    )
    statement = select(
        SchemeOptionRecord.amfi_scheme_code.label("amfi_scheme_code"),
        SchemeMetadataVersionRecord.scheme_name.label("scheme_name"),
        SchemeMetadataVersionRecord.fund_house_name.label("fund_house_name"),
        SchemeMetadataVersionRecord.scheme_classification.label("scheme_classification"),
        SchemeMetadataVersionRecord.plan_type.label("plan_type"),
        SchemeMetadataVersionRecord.option_type.label("option_type"),
        SchemeMetadataVersionRecord.isin_payout_or_growth.label("isin_payout_or_growth"),
        SchemeMetadataVersionRecord.isin_reinvestment.label("isin_reinvestment"),
        SchemeOptionRecord.first_observed_nav_date.label("first_nav_date"),
        SchemeOptionRecord.last_observed_nav_date.label("latest_nav_date"),
        latest_nav_value.label("latest_nav_value"),
        latest_quality_status.label("quality_status"),
    ).join(
        SchemeMetadataVersionRecord,
        SchemeMetadataVersionRecord.id == latest_metadata_id,
    )
    if fund_house_name is not None:
        statement = statement.where(SchemeMetadataVersionRecord.fund_house_name == fund_house_name)
    return statement

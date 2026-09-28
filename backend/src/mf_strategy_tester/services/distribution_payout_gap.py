"""Read-only report of declared IDCW payout evidence for options live since a cutoff date."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, and_, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    DistributionEventRecord,
    DistributionEventRevisionAdvisorkhojSourceRecord,
    DistributionEventRevisionOfficialSourceRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionRtaSourceRecord,
    DistributionEventRevisionSourceRecord,
    NavRevisionRecord,
    RtaDistributionRecord,
    RtaSchemeCaptureRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
    utc_now,
)

PAYOUT_GAP_REPORT_VERSION = "distribution-payout-gap-2026.09.1"
SOURCE_LABELS = ("amfi", "official_notice", "cams", "kfintech", "advisorkhoj")
_MAX_MISSING_OPTION_LIMIT = 5000
_INTERPRETATION = (
    "An option is live when its last observed AMFI NAV date is on or after the cutoff and the "
    "metadata attached to that NAV classifies it as IDCW. Events are current canonical "
    "idcw_cash revisions whose record date is on or after the cutoff. Source counts overlap: "
    "one event confirmed by several sources is counted under each. An option without events "
    "has no accepted declared payout evidence; that is not proof that it paid nothing."
)


@dataclass(frozen=True)
class PayoutGapFundHouse:
    fund_house_name: str
    live_idcw_options: int
    payout_eligible_options: int
    options_with_events: int
    options_without_events: int
    payout_eligible_options_without_events: int
    events: int
    events_by_source: dict[str, int]
    options_by_source: dict[str, int]


@dataclass(frozen=True)
class PayoutGapOption:
    amfi_scheme_code: str
    fund_house_name: str
    scheme_name: str
    plan_type: str
    payout_eligible: bool
    last_observed_nav_date: str


@dataclass(frozen=True)
class DistributionPayoutGapReport:
    report_version: str
    generated_at: str
    since: str
    interpretation: str
    live_idcw_options: int
    payout_eligible_options: int
    options_with_events: int
    options_without_events: int
    payout_eligible_options_without_events: int
    events: int
    events_by_source: dict[str, int]
    fund_houses: tuple[PayoutGapFundHouse, ...]
    missing_option_limit: int
    missing_options_total: int
    missing_options: tuple[PayoutGapOption, ...]


@dataclass(frozen=True)
class _LiveOption:
    amfi_scheme_code: str
    fund_house_name: str
    scheme_name: str
    plan_type: str
    payout_eligible: bool
    last_observed_nav_date: date


class DistributionPayoutGapService:
    """Summarize which live IDCW options lack declared payout evidence since a cutoff."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def build_report(
        self, *, since: date, missing_option_limit: int = 200
    ) -> DistributionPayoutGapReport:
        if not 0 <= missing_option_limit <= _MAX_MISSING_OPTION_LIMIT:
            raise ValueError(
                f"missing option limit must be between 0 and {_MAX_MISSING_OPTION_LIMIT}"
            )
        options = self._live_idcw_options(since)
        revisions_by_code = self._current_revisions(since, options)
        sources_by_revision = self._revision_sources(since)

        by_house: dict[str, list[_LiveOption]] = {}
        for option in options.values():
            by_house.setdefault(option.fund_house_name, []).append(option)
        fund_houses = tuple(
            sorted(
                (
                    _summarize(name, members, revisions_by_code, sources_by_revision)
                    for name, members in by_house.items()
                ),
                key=lambda item: (-item.options_without_events, item.fund_house_name),
            )
        )
        missing = sorted(
            (
                option
                for option in options.values()
                if option.amfi_scheme_code not in revisions_by_code
            ),
            key=lambda item: (
                not item.payout_eligible,
                item.fund_house_name,
                item.amfi_scheme_code,
            ),
        )
        total = _summarize("", list(options.values()), revisions_by_code, sources_by_revision)
        return DistributionPayoutGapReport(
            report_version=PAYOUT_GAP_REPORT_VERSION,
            generated_at=utc_now().isoformat(),
            since=since.isoformat(),
            interpretation=_INTERPRETATION,
            live_idcw_options=total.live_idcw_options,
            payout_eligible_options=total.payout_eligible_options,
            options_with_events=total.options_with_events,
            options_without_events=total.options_without_events,
            payout_eligible_options_without_events=total.payout_eligible_options_without_events,
            events=total.events,
            events_by_source=total.events_by_source,
            fund_houses=fund_houses,
            missing_option_limit=missing_option_limit,
            missing_options_total=len(missing),
            missing_options=tuple(
                PayoutGapOption(
                    amfi_scheme_code=option.amfi_scheme_code,
                    fund_house_name=option.fund_house_name,
                    scheme_name=option.scheme_name,
                    plan_type=option.plan_type,
                    payout_eligible=option.payout_eligible,
                    last_observed_nav_date=option.last_observed_nav_date.isoformat(),
                )
                for option in missing[:missing_option_limit]
            ),
        )

    def _live_idcw_options(self, since: date) -> dict[str, _LiveOption]:
        rows = self._session.execute(
            select(
                SchemeOptionRecord.amfi_scheme_code,
                SchemeOptionRecord.last_observed_nav_date,
                SchemeMetadataVersionRecord.fund_house_name,
                SchemeMetadataVersionRecord.scheme_name,
                SchemeMetadataVersionRecord.plan_type,
                SchemeMetadataVersionRecord.isin_payout_or_growth,
            )
            .join(
                NavRevisionRecord,
                and_(
                    NavRevisionRecord.amfi_scheme_code == SchemeOptionRecord.amfi_scheme_code,
                    NavRevisionRecord.nav_date == SchemeOptionRecord.last_observed_nav_date,
                    NavRevisionRecord.is_current.is_(True),
                ),
            )
            .join(
                SchemeMetadataVersionRecord,
                SchemeMetadataVersionRecord.id == NavRevisionRecord.metadata_version_id,
            )
            .where(
                SchemeOptionRecord.last_observed_nav_date >= since,
                SchemeMetadataVersionRecord.option_type == "idcw",
            )
        ).tuples()
        return {
            code: _LiveOption(
                amfi_scheme_code=code,
                fund_house_name=fund_house_name,
                scheme_name=scheme_name,
                plan_type=plan_type,
                payout_eligible=bool(payout_isin),
                last_observed_nav_date=last_nav_date,
            )
            for code, last_nav_date, fund_house_name, scheme_name, plan_type, payout_isin in rows
        }

    def _current_revisions(
        self, since: date, options: dict[str, _LiveOption]
    ) -> dict[str, list[str]]:
        revisions: dict[str, list[str]] = {}
        for code, revision_id in self._session.execute(
            select(DistributionEventRecord.amfi_scheme_code, DistributionEventRevisionRecord.id)
            .join(
                DistributionEventRevisionRecord,
                DistributionEventRevisionRecord.distribution_event_id == DistributionEventRecord.id,
            )
            .where(
                DistributionEventRecord.record_date >= since,
                DistributionEventRecord.event_type == "idcw_cash",
                DistributionEventRevisionRecord.is_current.is_(True),
            )
        ).tuples():
            if code in options:
                revisions.setdefault(code, []).append(revision_id)
        return revisions

    def _revision_sources(self, since: date) -> dict[str, set[str]]:
        sources: dict[str, set[str]] = {}

        def add(rows: Iterable[tuple[str, str]]) -> None:
            for revision_id, label in rows:
                sources.setdefault(revision_id, set()).add(label)

        amfi_link = DistributionEventRevisionSourceRecord.distribution_event_revision_id
        add(
            (revision_id, "amfi")
            for (revision_id,) in self._session.execute(
                _current_since(select(amfi_link).distinct(), amfi_link, since)
            ).tuples()
        )
        official_link = DistributionEventRevisionOfficialSourceRecord.distribution_event_revision_id
        add(
            (revision_id, "official_notice")
            for (revision_id,) in self._session.execute(
                _current_since(select(official_link).distinct(), official_link, since)
            ).tuples()
        )
        rta_link = DistributionEventRevisionRtaSourceRecord.distribution_event_revision_id
        add(
            self._session.execute(
                _current_since(
                    select(rta_link, RtaSchemeCaptureRecord.provider)
                    .distinct()
                    .join(
                        RtaDistributionRecord,
                        RtaDistributionRecord.id
                        == DistributionEventRevisionRtaSourceRecord.rta_distribution_record_id,
                    )
                    .join(
                        RtaSchemeCaptureRecord,
                        RtaSchemeCaptureRecord.id == RtaDistributionRecord.scheme_capture_id,
                    ),
                    rta_link,
                    since,
                )
            ).tuples()
        )
        advisorkhoj_link = (
            DistributionEventRevisionAdvisorkhojSourceRecord.distribution_event_revision_id
        )
        add(
            (revision_id, "advisorkhoj")
            for (revision_id,) in self._session.execute(
                _current_since(select(advisorkhoj_link).distinct(), advisorkhoj_link, since)
            ).tuples()
        )
        unknown = {label for labels in sources.values() for label in labels} - set(SOURCE_LABELS)
        if unknown:
            raise RuntimeError(
                f"unexpected canonical distribution source labels: {sorted(unknown)}"
            )
        return sources


def _current_since[T: tuple[object, ...]](
    statement: Select[T], revision_column: object, since: date
) -> Select[T]:
    return (
        statement.join(
            DistributionEventRevisionRecord,
            DistributionEventRevisionRecord.id == revision_column,
        )
        .join(
            DistributionEventRecord,
            DistributionEventRecord.id == DistributionEventRevisionRecord.distribution_event_id,
        )
        .where(
            DistributionEventRevisionRecord.is_current.is_(True),
            DistributionEventRecord.record_date >= since,
            DistributionEventRecord.event_type == "idcw_cash",
        )
    )


def _summarize(
    fund_house_name: str,
    options: list[_LiveOption],
    revisions_by_code: dict[str, list[str]],
    sources_by_revision: dict[str, set[str]],
) -> PayoutGapFundHouse:
    events_by_source = dict.fromkeys(SOURCE_LABELS, 0)
    options_by_source = dict.fromkeys(SOURCE_LABELS, 0)
    events = 0
    for option in options:
        option_sources: set[str] = set()
        for revision_id in revisions_by_code.get(option.amfi_scheme_code, ()):
            labels = sources_by_revision.get(revision_id)
            if not labels:
                raise RuntimeError(
                    f"canonical distribution revision {revision_id} has no source provenance"
                )
            events += 1
            option_sources.update(labels)
            for label in labels:
                events_by_source[label] += 1
        for label in option_sources:
            options_by_source[label] += 1
    with_events = sum(option.amfi_scheme_code in revisions_by_code for option in options)
    return PayoutGapFundHouse(
        fund_house_name=fund_house_name,
        live_idcw_options=len(options),
        payout_eligible_options=sum(option.payout_eligible for option in options),
        options_with_events=with_events,
        options_without_events=len(options) - with_events,
        payout_eligible_options_without_events=sum(
            option.payout_eligible and option.amfi_scheme_code not in revisions_by_code
            for option in options
        ),
        events=events,
        events_by_source=events_by_source,
        options_by_source=options_by_source,
    )

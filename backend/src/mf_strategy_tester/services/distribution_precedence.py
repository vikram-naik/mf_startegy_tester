from enum import IntEnum

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    DistributionEventRevisionAdvisorkhojSourceRecord,
    DistributionEventRevisionOfficialSourceRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionRtaSourceRecord,
    DistributionEventRevisionSourceRecord,
)


class DistributionSourceTier(IntEnum):
    PRIMARY = 1
    RTA = 2
    ADVISORKHOJ = 3


def canonical_revision_source_tier(session: Session, revision_id: str) -> DistributionSourceTier:
    """Return the highest-authority evidence attached to one canonical revision."""
    amfi_exists = session.scalar(
        select(
            exists().where(
                DistributionEventRevisionSourceRecord.distribution_event_revision_id == revision_id
            )
        )
    )
    official_exists = session.scalar(
        select(
            exists().where(
                DistributionEventRevisionOfficialSourceRecord.distribution_event_revision_id
                == revision_id
            )
        )
    )
    if amfi_exists or official_exists:
        return DistributionSourceTier.PRIMARY
    rta_exists = session.scalar(
        select(
            exists().where(
                DistributionEventRevisionRtaSourceRecord.distribution_event_revision_id
                == revision_id
            )
        )
    )
    if rta_exists:
        return DistributionSourceTier.RTA
    advisorkhoj_exists = session.scalar(
        select(
            exists().where(
                DistributionEventRevisionAdvisorkhojSourceRecord.distribution_event_revision_id
                == revision_id
            )
        )
    )
    if advisorkhoj_exists:
        return DistributionSourceTier.ADVISORKHOJ
    raise RuntimeError(f"canonical distribution revision {revision_id} has no source provenance")


def event_highest_source_tier(session: Session, event_id: str) -> DistributionSourceTier | None:
    revision_ids = tuple(
        session.scalars(
            select(DistributionEventRevisionRecord.id).where(
                DistributionEventRevisionRecord.distribution_event_id == event_id
            )
        )
    )
    if not revision_ids:
        return None
    return min(canonical_revision_source_tier(session, revision_id) for revision_id in revision_ids)

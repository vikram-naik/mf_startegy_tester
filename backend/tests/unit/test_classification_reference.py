from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    SchemeClassificationAliasRecord,
    SchemeClassificationRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.services.classification_reference import (
    ClassificationReferenceService,
    scheme_classification_structure,
)


def test_scheme_structure_is_explicit_and_unknown_families_remain_other() -> None:
    assert scheme_classification_structure("Open Ended Schemes ( Equity Scheme )") == "open_ended"
    assert scheme_classification_structure("Close Ended Schemes ( Income )") == "close_ended"
    assert scheme_classification_structure("Interval Fund Schemes ( Growth )") == "interval"
    assert scheme_classification_structure("Unrecognized Family ( Growth )") == "other"


def test_reference_persists_raw_aliases_under_stable_canonical_ids(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'classification.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = ClassificationReferenceService(session)
        service.ensure_approved_aliases(
            (
                "Open Ended Schemes ( Equity Scheme - Large & Mid Cap Fund )",
                "Open Ended Schemes ( Equity Schemes - Large and Midcap Fund )",
                "Open Ended Schemes ( Equity Scheme - Large Cap Fund )",
            )
        )
        session.commit()

        assert session.scalar(select(func.count()).select_from(SchemeClassificationRecord)) == 2
        assert (
            session.scalar(select(func.count()).select_from(SchemeClassificationAliasRecord)) == 3
        )
        aliases = tuple(
            session.scalars(
                select(SchemeClassificationAliasRecord).order_by(
                    SchemeClassificationAliasRecord.raw_classification
                )
            )
        )
        assert aliases[0].raw_classification.startswith("Open Ended Schemes")
        assert len({alias.classification_id for alias in aliases}) == 2

        report = service.build_report(proposal_threshold=0.5)
        assert report.canonical_classifications == 2
        assert report.approved_aliases == 3
        assert report.unmapped_source_labels == ()
        assert report.proposals
        assert all("proposal only" in proposal.evidence for proposal in report.proposals)

    engine.dispose()


def test_reference_is_idempotent_for_repeated_source_labels(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'idempotent.db'}")
    Base.metadata.create_all(engine)
    raw = "Open Ended Schemes(Equity Scheme - Large & Mid Cap Fund)"
    with Session(engine) as session:
        service = ClassificationReferenceService(session)
        service.ensure_approved_aliases((raw, raw))
        service.ensure_approved_aliases((raw,))
        session.commit()

        assert session.scalar(select(func.count()).select_from(SchemeClassificationRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(SchemeClassificationAliasRecord)) == 1
        )

    engine.dispose()

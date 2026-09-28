from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    SchemeClassificationAliasRecord,
    SchemeClassificationRecord,
    ScreenerClassificationAliasMemberRecord,
    ScreenerClassificationAliasRecord,
    ScreenerClassificationAliasRevisionRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.services.classification_reference import (
    ClassificationReferenceService,
    classification_definition,
)
from mf_strategy_tester.services.screener_classification_alias import (
    create_screener_alias,
    resolve_classification_selection,
    simple_classification_name,
    singleton_alias_id,
    update_screener_alias,
)


def test_default_alias_groups_amfi_successor_labels_without_mutating_source(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'aliases.db'}")
    old_label = "Open Ended Schemes(Debt Scheme - Corporate Bond Fund)"
    new_label = "Open Ended Schemes(Income/Debt Oriented Schemes - Corporate Bond Fund)"
    with Session(engine) as session:
        Base.metadata.create_all(engine)
        ClassificationReferenceService(session).ensure_approved_aliases((old_label, new_label))
        session.commit()

        selection = resolve_classification_selection(
            session,
            classification_id="screener-open-debt-corporate-bond",
            classification=None,
        )
        assert selection is not None
        assert selection.display_name == "Corporate Bond"
        assert len(selection.classification_ids) == 2
        assert selection.mapping_version.endswith(":v2")
        assert set(
            session.scalars(select(SchemeClassificationAliasRecord.raw_classification)).all()
        ) == {old_label, new_label}
        assert (
            session.scalar(
                select(func.count()).select_from(ScreenerClassificationAliasRevisionRecord)
            )
            == 2
        )
    engine.dispose()


def test_unmapped_canonical_classification_gets_a_singleton_screener_alias(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'singleton-alias.db'}")
    raw_label = "Open Ended Schemes ( Hybrid Scheme - Balanced Hybrid Fund )"
    definition = classification_definition(raw_label)
    with Session(engine) as session:
        Base.metadata.create_all(engine)
        ClassificationReferenceService(session).ensure_approved_aliases((raw_label,))
        session.commit()

        alias_id = singleton_alias_id(definition.classification_id)
        alias = session.get(ScreenerClassificationAliasRecord, alias_id)
        assert alias is not None
        assert alias.name == "Balanced Hybrid Fund"
        assert alias.status == "active"
        member = session.get(ScreenerClassificationAliasMemberRecord, definition.classification_id)
        assert member is not None and member.alias_id == alias_id
        selection = resolve_classification_selection(
            session, classification_id=definition.classification_id, classification=None
        )
        assert selection is not None
        assert selection.selection_id == alias_id
        assert selection.classification_ids == (definition.classification_id,)
        revision = session.scalar(
            select(ScreenerClassificationAliasRevisionRecord).where(
                ScreenerClassificationAliasRevisionRecord.alias_id == alias_id
            )
        )
        assert revision is not None
        assert revision.member_classification_ids == [definition.classification_id]
        assert revision.change_reason == (
            "Automatic singleton alias for one canonical AMFI classification"
        )
    engine.dispose()


def test_singleton_alias_names_disambiguate_equal_short_labels(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'singleton-collisions.db'}")
    raw_labels = (
        "Open Ended Schemes ( Equity Scheme - Focused Fund )",
        "Open Ended Schemes ( Income/Debt Oriented Schemes - Focused Fund )",
    )
    with Session(engine) as session:
        Base.metadata.create_all(engine)
        ClassificationReferenceService(session).ensure_approved_aliases(raw_labels)
        session.commit()

        classifications = tuple(session.scalars(select(SchemeClassificationRecord)).all())
        members = tuple(session.scalars(select(ScreenerClassificationAliasMemberRecord)).all())
        aliases = tuple(session.scalars(select(ScreenerClassificationAliasRecord)).all())
        assert len(classifications) == len(members) == len(aliases) == 2
        assert {alias.name for alias in aliases} == {
            "Equity Scheme - Focused Fund",
            "Income/Debt Oriented Schemes - Focused Fund",
        }
    engine.dispose()


def test_sectoral_and_thematic_equity_labels_share_reviewed_alias(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'sectoral-thematic-alias.db'}")
    equity_labels = (
        "Open Ended Schemes ( Equity Scheme - Sectoral Fund )",
        "Open Ended Schemes ( Equity Scheme - Sectoral/ Thematic )",
        "Open Ended Schemes ( Equity Scheme - Thematic Fund )",
    )
    debt_label = "Open Ended Schemes ( Income/Debt Oriented Schemes - Sectoral Fund )"
    with Session(engine) as session:
        Base.metadata.create_all(engine)
        ClassificationReferenceService(session).ensure_approved_aliases(
            (*equity_labels, debt_label)
        )
        session.commit()

        expected_ids = {
            classification_definition(label).classification_id for label in equity_labels
        }
        alias = session.get(
            ScreenerClassificationAliasRecord,
            "screener-open-equity-sectoral-thematic",
        )
        assert alias is not None
        assert alias.name == "Sectoral / Thematic Equity"
        assert alias.status == "active"
        assert alias.version == 3
        assert {
            member.classification_id
            for member in session.scalars(
                select(ScreenerClassificationAliasMemberRecord).where(
                    ScreenerClassificationAliasMemberRecord.alias_id == alias.id
                )
            )
        } == expected_ids
        for classification_id in expected_ids:
            selection = resolve_classification_selection(
                session,
                classification_id=classification_id,
                classification=None,
            )
            assert selection is not None
            assert selection.selection_id == alias.id
            assert set(selection.classification_ids) == expected_ids

        debt_id = classification_definition(debt_label).classification_id
        debt_member = session.get(ScreenerClassificationAliasMemberRecord, debt_id)
        assert debt_member is not None
        assert debt_member.alias_id != alias.id
    engine.dispose()


def test_alias_update_is_audited_and_rejects_stale_version(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'alias-update.db'}")
    raw_label = "Open Ended Schemes(Debt Scheme - Liquid Fund)"
    with Session(engine) as session:
        Base.metadata.create_all(engine)
        ClassificationReferenceService(session).ensure_approved_aliases((raw_label,))
        session.commit()
        alias = session.get(ScreenerClassificationAliasRecord, "screener-open-debt-liquid")
        assert alias is not None
        classification_ids = resolve_classification_selection(
            session, classification_id=alias.id, classification=None
        ).classification_ids

        updated = update_screener_alias(
            session,
            alias_id=alias.id,
            expected_version=1,
            name="Liquid Funds",
            structure_type="open_ended",
            status="active",
            classification_ids=classification_ids,
            reason="Prefer a plural label in the local screener",
        )
        assert updated.version == 2
        assert updated.name == "Liquid Funds"
        revisions = tuple(
            session.scalars(
                select(ScreenerClassificationAliasRevisionRecord)
                .where(ScreenerClassificationAliasRevisionRecord.alias_id == alias.id)
                .order_by(ScreenerClassificationAliasRevisionRecord.version)
            ).all()
        )
        assert [revision.name for revision in revisions] == ["Liquid", "Liquid Funds"]
        assert revisions[-1].change_reason == "Prefer a plural label in the local screener"

        with pytest.raises(RuntimeError, match="changed from version 1 to 2"):
            update_screener_alias(
                session,
                alias_id=alias.id,
                expected_version=1,
                name="Stale update",
                structure_type="open_ended",
                status="active",
                classification_ids=classification_ids,
                reason="This update is based on stale state",
            )
    engine.dispose()


def test_deactivation_releases_members_for_audited_reassignment(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'alias-release.db'}")
    raw_label = "Open Ended Schemes(Debt Scheme - Liquid Fund)"
    with Session(engine) as session:
        Base.metadata.create_all(engine)
        ClassificationReferenceService(session).ensure_approved_aliases((raw_label,))
        session.commit()
        original = session.get(ScreenerClassificationAliasRecord, "screener-open-debt-liquid")
        assert original is not None
        selection = resolve_classification_selection(
            session, classification_id=original.id, classification=None
        )
        assert selection is not None
        classification_ids = selection.classification_ids

        deactivated = update_screener_alias(
            session,
            alias_id=original.id,
            expected_version=1,
            name=original.name,
            structure_type="open_ended",
            status="inactive",
            classification_ids=(),
            reason="Release the classification for a reviewed replacement alias",
        )
        assert deactivated.status == "inactive"
        assert (
            resolve_classification_selection(
                session, classification_id=original.id, classification=None
            )
            is None
        )
        released_selection = resolve_classification_selection(
            session, classification_id=classification_ids[0], classification=None
        )
        assert released_selection is not None
        assert released_selection.classification_ids == classification_ids

        replacement = create_screener_alias(
            session,
            name="Liquid replacement",
            structure_type="open_ended",
            status="active",
            classification_ids=classification_ids,
            reason="Assign the released classification after local review",
        )
        assert replacement.status == "active"
        replacement_selection = resolve_classification_selection(
            session, classification_id=classification_ids[0], classification=None
        )
        assert replacement_selection is not None
        assert replacement_selection.selection_id == replacement.id

        revisions = tuple(
            session.scalars(
                select(ScreenerClassificationAliasRevisionRecord)
                .where(ScreenerClassificationAliasRevisionRecord.alias_id == original.id)
                .order_by(ScreenerClassificationAliasRevisionRecord.version)
            ).all()
        )
        assert [revision.status for revision in revisions] == ["active", "inactive"]
        assert revisions[-1].member_classification_ids == []
    engine.dispose()


def test_alias_mutations_reject_invalid_status_members_and_blank_audit_reason(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'alias-validation.db'}")
    raw_label = "Open Ended Schemes(Debt Scheme - Liquid Fund)"
    with Session(engine) as session:
        Base.metadata.create_all(engine)
        ClassificationReferenceService(session).ensure_approved_aliases((raw_label,))
        session.commit()
        original = session.get(ScreenerClassificationAliasRecord, "screener-open-debt-liquid")
        assert original is not None
        classification_ids = resolve_classification_selection(
            session, classification_id=original.id, classification=None
        ).classification_ids

        with pytest.raises(ValueError, match=r"inactive.*release all"):
            update_screener_alias(
                session,
                alias_id=original.id,
                expected_version=1,
                name=original.name,
                structure_type="open_ended",
                status="inactive",
                classification_ids=classification_ids,
                reason="Deactivate this local alias",
            )
        with pytest.raises(ValueError, match=r"reason.*at least 3"):
            update_screener_alias(
                session,
                alias_id=original.id,
                expected_version=1,
                name=original.name,
                structure_type="open_ended",
                status="active",
                classification_ids=classification_ids,
                reason="   ",
            )
    engine.dispose()


@pytest.mark.parametrize(
    ("classification", "expected"),
    [
        ("Open Ended Schemes ( Equity Scheme - Large Cap Fund )", "Large Cap Fund"),
        ("Close Ended Schemes ( Income )", "Income"),
        ("Interval Fund Schemes ( Growth )", "Growth"),
    ],
)
def test_simple_classification_name_removes_structure_and_family(
    classification: str, expected: str
) -> None:
    assert simple_classification_name(classification) == expected

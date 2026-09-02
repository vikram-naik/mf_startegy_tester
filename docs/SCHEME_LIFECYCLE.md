# Scheme lifecycle acquisition

## Scope

The first lifecycle lane captures the complete current AMFI family catalog and one official
`scheme-details` response for every returned AMFI `(MF_ID, scheme_id)` pair. It establishes:

- immutable family catalog membership observations;
- official family name, type, category, and launch date;
- exact retrieval batch, URL, parser version, and raw artifact provenance;
- an explicit family-level `launch` fact; and
- resumable per-family checkpoints.

AMFI family `scheme_id` is source-local and is not treated as an option-level AMFI scheme code.
Names are attributes, not identifiers.

## Run

This is a long, restart-safe acquisition. Run it only after the IDCW RTA and tertiary fallback
batches have been verified:

```bash
./scripts/sync_scheme_lifecycle.sh
```

The default `full` mode always captures a fresh fund-level scheme-list snapshot, then skips family
details that already have a committed checkpoint. A failed or interrupted run can therefore be
rerun without duplicating normalized facts. Each successful family commits independently.

For a bounded recovery run:

```bash
MFST_LIFECYCLE_FUND_IDS=3,20 ./scripts/sync_scheme_lifecycle.sh
```

To re-observe every current family and detect source revisions:

```bash
MFST_LIFECYCLE_MODE=refresh ./scripts/sync_scheme_lifecycle.sh
```

The wrapper uses the shared ingestion lock, applies migrations, defaults to a 0.1-second delay
after every official request, and writes timestamped sync, coverage, and data-quality reports under
`data/lifecycle-reports/`.

Individual commands:

```bash
backend/.venv/bin/mfst sync-scheme-lifecycle --mode full --delay-seconds 0.1
backend/.venv/bin/mfst scheme-lifecycle-report
```

## Correctness boundary

The current strict AMFI contract contains exactly the fields `MF_Name`, `Scheme_Name`,
`Scheme_Objective`, `SchemeType_Desc`, `SchemeCat_Desc`, `Launch_Date`, `Scheme_load`,
`Scheme_min_amt`, `AMC_Website`, `scheme_Id`, and `MF_Id`. Identity must exactly match the request,
and launch timestamps must carry the `Asia/Kolkata` UTC offset. Unknown shapes fail and retain the
raw artifact and failed ingestion batch.

Catalog absence is not closure evidence. A family removed between list snapshots creates a
`catalog_member_removed` issue and no closure event. A changed name without an official effective
date creates `name_changed_without_effective_date`; it is not backdated to retrieval time. Multiple
official launch dates remain separate immutable facts and create `launch_date_conflict`.

## Remaining lifecycle sources

AMFI scheme details do not establish merger, closure, maturity, rename effective date, or successor
identity. Those events require explicit official AMC notices, Scheme Information Documents /
Statements of Additional Information, or SEBI disclosures. The generic lifecycle event model can
retain those event types, but they must not be populated from name heuristics, missing NAVs, or
catalog disappearance.

The lifecycle acquisition lane is complete only when the coverage report shows:

- every active AMFI fund has a current list snapshot;
- every family in those latest snapshots has a detail checkpoint and launch fact;
- no unresolved identity or launch-date conflict is hidden; and
- explicit merger/closure/maturity evidence coverage and its limitations are separately reported.

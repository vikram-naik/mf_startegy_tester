# Scheme lifecycle acquisition

## Scope

The first lifecycle lane captures the complete current AMFI family catalog and one official
`scheme-details` response for every returned AMFI `(MF_ID, scheme_id)` pair. It establishes:

- immutable family catalog membership observations;
- official family name, type, category, and launch date when AMFI supplies one;
- exact retrieval batch, URL, parser version, and raw artifact provenance;
- an explicit family-level `launch` fact when the official date is present; and
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
after every official request, and writes timestamped logs, status, sync, coverage, and data-quality
reports under `data/lifecycle-reports/`. The status file records the selected mode/funds and final
exit status. A nonzero exit leaves prior family checkpoints committed; rerunning the same command
skips those checkpoints in `full` mode.

Individual commands:

```bash
backend/.venv/bin/mfst sync-scheme-lifecycle --mode full --delay-seconds 0.1
backend/.venv/bin/mfst scheme-lifecycle-report
```

## Correctness boundary

The current strict AMFI contract contains exactly the fields `MF_Name`, `Scheme_Name`,
`Scheme_Objective`, `SchemeType_Desc`, `SchemeCat_Desc`, `Launch_Date`, `Scheme_load`,
`Scheme_min_amt`, `AMC_Website`, `scheme_Id`, and `MF_Id`. Identity must exactly match the request.
Non-null launch timestamps must carry the `Asia/Kolkata` UTC offset. AMFI also returns an explicit
JSON `null` launch date for some valid family-detail rows. Such rows retain their official metadata
and checkpoint, create a `missing_launch_date` issue, and create no launch event. Blank strings,
malformed dates, and unknown shapes still fail and retain the raw artifact and failed ingestion
batch.

Catalog absence is not closure evidence. A family removed between list snapshots creates a
`catalog_member_removed` issue and no closure event. A changed name without an official effective
date creates `name_changed_without_effective_date`; it is not backdated to retrieval time. The
checkpoint then advances to the latest observed catalog name while immutable list snapshots retain
both values. Re-observing the same current name does not append the same issue again. Multiple
official launch dates remain separate immutable facts and create `launch_date_conflict`.

## Remaining lifecycle sources

AMFI scheme details do not establish merger, closure, maturity, rename effective date, or successor
identity. Those events require explicit official AMC notices, Scheme Information Documents /
Statements of Additional Information, or SEBI disclosures. The generic lifecycle event model can
retain those event types, but they must not be populated from name heuristics, missing NAVs, or
catalog disappearance.

The current AMFI family/detail/launch lane is complete when the coverage report shows:

- every active AMFI fund has a current list snapshot;
- every family in those latest snapshots has a detail checkpoint;
- every missing launch fact is explicit rather than inferred from a name or first NAV;
- no unresolved identity or launch-date conflict is hidden.

That condition is met by the 2 September recovery described below. It does not make the dataset
survivorship-free. Broader lifecycle coverage remains incomplete until merger, closure, maturity,
rename-effective-date, predecessor, and successor evidence is acquired from separate official
artifacts and its limitations are reported explicitly.

## Null launch-date recovery

The 24 August full run classified 36 official detail responses as parse failures. A checksum-backed
audit on 2 September established that every response had the requested fund/family identity and an
explicit JSON `null` `Launch_Date`; none was a transport or structural failure. The affected fund
counts are HDFC 6, JM 6, Kotak 5, ICICI Prudential 1, Nippon India 1, HSBC 1, and Motilal Oswal 16.

The bounded recovery used these fund catalogs from the repository root:

```bash
MFST_LIFECYCLE_FUND_IDS=9,16,17,20,21,37,55 ./scripts/sync_scheme_lifecycle.sh
```

This bounded run captures fresh official fund-level lists, skips existing family checkpoints, and
retries current families without checkpoints. If the official catalogs and values are unchanged,
expected completion is 36 retained detail rows, 36 `missing_launch_date` issues, no new launch
events for those families, and zero latest catalog families without detail. The number of latest
families without launch evidence should remain 36. Catalog or value changes must remain explicit in
the new reports rather than being forced to those expected counts. Historical
`scheme_detail_failure` issues remain immutable audit evidence from parser version
`amfi-2026.08.12`.

Recovery run `6ac738f7-5a73-43ce-b15c-fed6b5f4489e` completed on 2 September with all expected
financial-data postconditions: 36 details inserted, zero rejected/failed, zero latest families
without detail, and 36 latest families without launch evidence. It also observed 76 current catalog
name changes without effective dates. Those names are current attributes, not rename/merger events.

Alignment run `9eeb4e1f-bf94-4c1e-a1f7-11a7acc8f29d` then completed with all 4,004 selected
families skipped, no detail fetches, and no new issues. A database reconciliation found zero
checkpoint names differing from the latest immutable list snapshots. The lifecycle recovery and
checkpoint-name alignment are complete; do not rerun them merely to reproduce these results.

Several names describe legacy, provisional, or fixed-maturity products, but names and null dates do
not establish predecessor, merger, maturity, or closure identity. Those events still require a
stable identifier or a separate official continuity artifact.

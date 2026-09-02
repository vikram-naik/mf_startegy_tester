# IDCW distribution coverage

**Assessment version:** `distribution-coverage-2026.08.6`
**Latest live assessment:** 2026-09-01

## Purpose

A successful AMFI request proves that a source response was captured and parsed. It does not prove
that AMFI returned complete payout history. The distribution coverage assessment records this
distinction for every scheme option whose latest stored metadata classifies it as `idcw`.

Run it after source synchronization and canonical normalization:

```bash
uv run --project backend mfst assess-distribution-coverage
```

Explain every blocked option in the latest completed snapshot without changing source or
canonical data:

```bash
uv run --project backend mfst distribution-blocker-report
```

The report partitions exact AMFI rows with the same ordered normalization gate used by canonical
publication. It includes aggregate and per-option gate counts, while reporting official-notice,
RTA, and AdvisorKhoj rows separately as `other_accepted_source_rows`. A
`cash_amount_candidate` in this report is still blocked at the coverage level and must be reviewed
for precedence or amount conflicts; the report never publishes it automatically.

`scripts/sync_amfi_distributions.sh` and `scripts/sync_rta_distributions.sh` run the assessment
automatically after normalization/import.
Assessments are append-only snapshots. Each row references the exact scheme metadata version used
for option classification and retains source-row, canonical-source-link, canonical-event, blocked
row, and source record-date counts.

## Status semantics

| Status | Evidence-backed meaning | Meaning explicitly excluded |
| --- | --- | --- |
| `events_present` | At least one provenance-linked canonical event exists. | It does not certify complete history. |
| `blocked_source_rows` | Resolved accepted-source rows exist, but none can be safely published as cash events. | It does not mean zero payouts. |
| `unverified_empty` | No resolved accepted-source distribution row was captured. | It does not mean the option never paid IDCW. |

There is deliberately no `verified_no_payout` status yet. Such a conclusion requires an
authoritative AMC or AMFI artifact establishing the relevant option and historical period.

## Live coverage result

Run `1fad288c-2d38-4048-8f0d-7831bdf0ac8a` assessed 16,432 IDCW options using the metadata attached
to each option's latest current NAV revision:

| Status | Options |
| --- | ---: |
| `events_present` | 3,932 |
| `blocked_source_rows` | 67 |
| `unverified_empty` | 12,433 |

The underlying AMFI survey completed 10,977 family requests, but 10,089 returned zero rows. Thirty-six
of 57 active fund houses returned zero distribution rows across all requested families. This makes
request completion an invalid proxy for payout-history completeness.

The accepted AMFI, CAMS/KFintech, and AdvisorKhoj acquisition lanes have now been exhausted with
source failures retained explicitly. The audited AdvisorKhoj publisher may contribute canonical
events only as tertiary fallback when no higher-priority source conflicts. Acquisition completion
does not convert `unverified_empty` into proof of no payout and does not certify complete history
for an `events_present` option.

HDFC Balanced Advantage Fund family `58` is a concrete source gap. AMFI currently returns an empty
response, while HDFC's official February 2026 notice declares IDCW for the regular and direct
plans:

- [AMFI family response](https://www.amfiindia.com/api/scheme-dividend?MF_ID=9&strSDid=58&strYear=All)
- [HDFC official IDCW notice](https://files.hdfcfund.com/s3fs-public/2026-02/2056-%20Notice%20-%20IDCW%20Record%20Date%20-%20HDFC%20Balanced%20Advantage%20Fund-%20RD%20February%2025%2C%202026.pdf)

The HDFC proof adapter captured that notice plus HDFC's official scheme summary, which explicitly
maps the regular and direct IDCW plans to AMFI codes `100120` and `118969`. Both options now have a
2026-02-25 canonical event for INR 0.250 per unit, with notice and identity-document hashes in API
provenance. This closes only that notice, not either option's complete historical coverage.

## Optional official-notice evidence

The HDFC pilot now implements immutable official AMC notice ingestion without name-only identity or
NAV-drop reconstruction. It:

1. capture each official notice as a content-addressed artifact;
2. retain provider URL, retrieval time, parser version, and source document fields;
3. maps plans/options through official AMFI-code evidence, including predecessor and merger
   evidence where necessary;
4. preserve record date, amount, source unit, and the set of plans/options to which a merged-table
   value applies;
5. reconciles equal AMFI/AMC observations without overwriting either provenance;
6. blocks conflicting current amounts for explicit review; and
7. never uses NAV drops as canonical payout values.

The HDFC adapter currently supports the strict notice/table and scheme-summary formats represented
by the proof artifacts. Historical HDFC notices and other AMCs would require format-specific
fixtures and reconciliation, but that documentary expansion is optional and does not gate IDCW
acquisition completion for this personal local dataset.

The accepted completion sources are AMFI first, CAMS/KFintech as equal-priority RTA fallback, and
AdvisorKhoj as tertiary fallback. `unverified_empty` remains an explicit absence-of-evidence state:
it must not be presented as proof that no payout occurred, even after all accepted sources have
been exhausted.

Migration `20260817_0016` adds provider-neutral CAMS/KFintech capture, append-only scheme mapping,
RTA source rows, canonical revision links, and publication issues. Equal multi-source rows support
the same canonical revision; conflicting RTA amounts are retained and blocked. A captured empty RTA
table remains source evidence only and does not create a synthetic zero payout. Operational details
and queries are in `docs/RTA_DISTRIBUTIONS.md`.

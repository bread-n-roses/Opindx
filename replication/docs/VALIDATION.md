## Software 0.5.0 (23 October 2026)

OJF5 is calculated from the source-pinned all-type open checkpoint extended to
2017-2019. The verified 2022-2026 citing reference arrays are retained. Validation
checks exact current-source OJF2 type-count replay, OJF5 saved-count score/status
replay, rebuilt open annual metadata, window boundaries and all-cell annual
CSV/XLSX values. All thirty prior NF/ANS and OJF2 annual files are byte-identical.
The website builder is also exercised with the exact released two-window layout.

No repeated full raw-snapshot replay of NF/ANS or OJF2 is claimed. The original
baseline evidence is retained, with new source hashes and a version-specific
release-validation record. See ojf5-validation.json and ojf5-format-validation.json.
Interactive visual browser QA was unavailable; code, DOM, data and served-file
checks are reported separately.

## Software 0.4.0 (17 October 2026)

OJF is an additive open-input calculation. All count, score and status cells
match exactly across 578,622 journal-years (2022-2026), replayed from the frozen,
hash-bound type-count checkpoints. All annual Source metadata and Norwegian
membership columns match the frozen open-input preparation. Ten independent
OJF tests cover filtering, types, source scope, self-citations, duplicate
references, retractions, windows, zero/missing denominators, field ties, toy
Table 1 arithmetic and a raw-Parquet-to-annual workflow. The full replication
suite passed 136 tests. Complete annual CSV/XLSX cells were checked against
Parquet; dictionary-only corrections preserve the checked data worksheet XML.

All fifteen NF/ANS annual files match their existing public GitHub asset hashes.
The original NF/ANS full-replay evidence remains valid for its unchanged core.
The 0.4.0 validation does not claim a repeated complete raw-snapshot scan.
Website behavior is covered by the existing DOM-based tests; interactive browser
automation was unavailable. See the source-bound JSON evidence in this folder.

# Validation status

Prepared 2026-10-02. No publication, release creation, deployment or Git push
has occurred as part of this preparation.

## Completed checks

- 125 Python synthetic/unit/offline-publication and recovery tests: PASS.
- 12 browser-engine tests, 12 journal-details tests, 21 main-page tests: PASS.
- 4 website builder/versioning tests: PASS.
- Five-year replay from downloaded OpenAlex Works/Sources and the pinned Norwegian
  Register input: PASS. All 552,179 journal-year rows across 43 columns agree with
  the published reference. Metadata and counts are checked exactly; the configured
  floating-point reference tolerance is 1e-12.
- Additional exact check: all eight NF/ANS columns have zero unequal finite values,
  zero maximum absolute difference and identical missing-value positions for every
  year in this tested environment. See `exact-score-comparison.json`.
- CSV/XLSX export from both the finalized release and the independently reproduced
  tables: PASS for every cell. CSV round-trips exactly; XLSX float tolerance is
  2e-15, and empty text/null both appear blank in Excel. No Excel formulas are
  emitted. Original release Parquets remain byte-for-byte unchanged.
- Both sets of annual tables build the full five-year local website successfully
  (100 history and 40 ranking shards). No public site was deployed.
- Public-input/schema and package dependency audit: PASS; see public-data-audit.json.
- Pinned manifest/register input and inventory checks: PASS.
- Git portability check with Windows automatic line endings and a Unix-style
  checkout preserves the hash-bound correction/core file bytes.

The reproduced ISSN-L column has Arrow `string` rather than `large_string`:
this is an in-memory string-offset representation difference. All values and
physical Parquet column types match, and the reproduced files pass the website
builder. The prepared release retains the original Parquet files and hashes.

## Execution and recovery record

The replay began with an empty, isolated work directory. It reused only checkpoints
created during this replay, plus author-curated correction decisions packaged with
the code. It did not substitute historical research graph/field caches.

The 64 GB Windows machine initially ran with a 12 GB DuckDB allowance, then 24 GB
and four threads. The latter exhausted memory on the 2025 baseline graph. It was
resumed from verified checkpoints with 40 GB and two threads. Field extraction
ran separately with 10 GB and one thread against hard links to the fresh Work and
deduplication checkpoints, then was integrated after hash verification. It covers
44,987,056 unique eligible publication Works.

A preparation-only restart bug rewrote identical configuration/manifests and
invalidated timestamp-bound provenance. Preparation now preserves unchanged files;
the restart regression passes. The preflight was rebuilt and all three correction
outputs were byte-identical. Core calculation files did not change.

All annual calculations and comparisons passed before the original final XLSX
step encountered an incomplete XlsxWriter namespace in the sandbox interpreter.
The checked virtual environment provides XlsxWriter 3.2.9. The final format step
was resumed with `workflow/finish_formats.py`, which verifies source/output hashes
and records completion only after all format checks pass. Its two recovery tests
check changed-output rejection and refusal to mark failed conversions complete.

Full elapsed time, including retries and recovery: 4.22 hours. This is a record
of this execution, not a performance estimate for other machines. The full report,
source hashes, solver audits and resume details are in `full-replay.json`.

## Limits

The raw input verification checks manifest hashes and file inventories/sizes/mtimes;
it does not claim full content hashes of the entire roughly 707 GB Works input.
Derived checkpoints have SHA-256 receipts. Exact historical inputs must be obtained
separately, as explained in INPUTS_AND_PROVENANCE.md.

Publication tooling was tested against a simulated service and local release
bundles; no live Zenodo API transaction is claimed. XLSX values, types and workbook
structures were checked programmatically. Real Excel and browser visual inspection
were not available in this session. Actual browser/mobile layout QA remains separate.

## Publication follow-up (2026-10-02)

Both releases are now public; see `publication.json`. The prepared archives were
published without recalculation or payload changes. All public file names, sizes
and MD5 checksums match; GitHub asset SHA256 checks also pass. The website was
deployed first and its served assets checked against the tested commit.

## Version 0.2.0: additive count and interface revision (2026-10-06)

- Five annual schema-3 datasets: 552,179 rows, 47 columns. All original 43
  columns, including every NF/ANS value and missing position, remain exact.
- New citation counts are derived from receipt-checked canonical graphs and
  the exact native score universes. Non-self citation totals match each saved
  solver audit. Publication counts match canonical calculation inputs.
- `metric-counts-validation.json` records annual input/output hashes and checks.
- All annual CSV/XLSX cells pass source-Parquet verification using the same
  exact CSV and 2e-15 XLSX float rules as the initial release.
- 126 replication Python tests, 12 engine checks, 13 journal-detail checks,
  27 main-page checks, and 6 website builder tests pass.
- The local website was rebuilt with all five enriched annual files.

This is additive validation from the approved cached graphs, not a new replay
from raw Works/Sources. `full-replay.json` still describes version 0.1.0; its
source inventory must not be presented as validation of this changed version.
The owner explicitly approved using the completed additive checks instead of
repeating the full calculation. A fresh replay was started and then stopped at
the owner's request; it is not claimed as validation. The release gate binds this
exception to version 0.2.0, the current source hashes, unchanged calculation core,
and both annual count and format reports. See release-validation.json.
Browser visual QA remains outstanding: no browser connection was available.

## Publication follow-up: 2026-10-06

Software v0.2.0: https://doi.org/10.5281/zenodo.23187011 .
Dataset 2026-09-v3: https://doi.org/10.5281/zenodo.23187363 .
Both are new versions in the existing DOI series. Public file checksums,
creator/affiliations, licenses and the dataset-to-software DOI link passed.
The website was deployed and verified first. Frozen years 2022-2024 use the
new schema with unchanged metrics; 2025-2026 remain live. Validation used the
owner-approved additive exception, preserving the original full replay.

## Software 0.3.0: website averaging update (2026-10-12)

This release updates browser averaging and bundles the unchanged full computation
workflow. The existing owner-approved additive validation gate remains bound to
the exact new source inventory, protected baseline calculation files and annual
count/format evidence. The 0.2.0 validation record is preserved separately.
Current-year eligibility and the strict three-calendar-year window are covered
by regression checks, including displayed/exported averages. No new NF/ANS
recalculation or raw-input replay is claimed; existing annual data retains its DOI.

## Publication follow-up: 2026-10-12

Software 0.3.0: https://doi.org/10.5281/zenodo.23321233 .
GitHub: https://github.com/bread-n-roses/Opindx/releases/tag/software-v0.3.0 .
Website deployment and live source checks passed before software publication.
All six GitHub/Zenodo software file hashes and sizes passed verification; creator,
affiliations, version and MIT license were checked. The computation source and
annual data did not change. Dataset 2026-09-v3 retains its existing DOI:
https://doi.org/10.5281/zenodo.23187363 . This is not a new raw-input replay.

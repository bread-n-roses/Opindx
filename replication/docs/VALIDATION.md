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

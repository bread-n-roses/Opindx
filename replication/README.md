# Opindx replication package

Version 0.1.0 is a local release candidate. Publication is paused. Creator: Utz Weitzel
(VU Amsterdam; Radboud University Nijmegen). No DOI has been assigned yet. See `docs/VALIDATION.md` for
what has actually been executed.

This source package recreates the September 2026 journal-metrics workflow from a
**downloaded OpenAlex Parquet snapshot and the Norwegian Register CSV**. It includes
the reviewed annual identity decisions and the browser code needed to reproduce
percentiles, presets, field percentages and table exports. There is no dependency
on a research workspace or a commercial citation-index data file.
See [the calculation details](docs/METHODS.md) for eligibility, matrix orientation,
normalization, filtering and the NF/ANS formulas implemented by this version.

## Supported reproduction

The initial profile reproduces citing years 2022-2026 from the OpenAlex snapshot
of 2026-09-23 and the Norwegian Register input dated 2026-07-26. Input manifest
hashes, the register hash and correction-file hashes are pinned in
`configs/september-2026.json`. A different snapshot is deliberately rejected:
its identity corrections and source preflight must be reviewed and versioned.
The 2026 citing year is incomplete. Frozen/live labels are publication choices,
separate from the mathematical calculation.

The raw inputs are supplied by the user and are not bundled. Do not replace an
old input with the latest download and describe the result as exact replication.
The required OpenAlex format has `works/manifest.json`, `sources/manifest.json`
and entity Parquet partitions. This version does not consume JSONL directly.
See `docs/INPUTS_AND_PROVENANCE.md` for input sources and attribution.

## Installation

Use a new environment. The checked environment is Python 3.14.7; exact package
versions are in `environment.lock`. Keep this source tree intact and install it
in editable mode, because the CLI loads its bundled configuration and correction
files from this directory. This release is a self-contained source archive,
not a separately distributed wheel.

```sh
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r environment.lock
python -m pip install --no-deps -e .
python -c "import xlsxwriter; assert hasattr(xlsxwriter, 'Workbook'); print(xlsxwriter.__version__)"
```

## Run from downloaded inputs

```sh
opindx-replicate --snapshot /path/to/openalex-snapshot-2026-09-23 --norwegian /path/to/norwegian_register_journals_2026-07-26.csv --work /path/to/new-run --check-inputs
opindx-replicate --snapshot /path/to/openalex-snapshot-2026-09-23 --norwegian /path/to/norwegian_register_journals_2026-07-26.csv --work /path/to/new-run --memory 40GB --threads 2
```

The 40 GB memory allowance and two worker threads target the 64 GB RAM machine
used here. A 24 GB allowance with four threads exhausted DuckDB memory on the
2025 graph. Smaller machines may need further tuning; a smaller allowance is
not guaranteed to complete this profile. Use quotes around paths with spaces. `--check-inputs` checks the pinned manifests,
file inventories and register before reading Works. The full command runs source
checkpointing, Work extraction/deduplication, reviewed repairs, graph construction,
annual identity projection and matching, four NF/ANS calculation variants,
rolling fields and full annual export. It does not upload or publish anything.

Add `--reference /path/to/archived-parquets` to compare reproduced annual tables
with an existing release. Exact metadata/count agreement and score agreement
within relative tolerance 1e-12 are required. CSV and Excel conversions are checked
against every value of their canonical Parquet source. CSV preserves binary float
round trips; Excel is checked to relative tolerance 2e-15 and has its normal
approximately 15-digit precision. Missing values and empty text both appear as
blank Excel cells. Parquet/CSV preserve their distinction.

The snapshot requires roughly 707 GB for Works in this profile, plus Sources and
intermediate storage. Allow several hundred GB additional working space. Resource
measurements for the actual replication run are in `docs/VALIDATION.md`; runtime
and memory vary with hardware. Small tests do not need the real snapshot.

If all annual calculations and reference comparisons passed but final format
export failed, repair the environment and run
`python workflow/finish_formats.py /path/to/run`. This checks source and output
hashes, repeats the CSV/XLSX all-cell checks and writes completion only after
they pass. It requires the source inventory recorded as described in
`docs/RELEASING.md`. It does not repeat the scientific calculations.

## Results

- `work/exports/`: canonical annual Parquets, intermediate annual handoff CSVs,
  per-year validation and a website-compatible manifest.
- `work/release-data/`: complete annual CSV/XLSX/Parquet, dictionary and format
  validation. All formats retain the canonical field names; `share_*` is NF and
  `per_article_*` is ANS. Dictionary definitions spell out names and units.
- `work/checks/`: input and completion records.
- `work/intermediates/`: local, reproducible checkpoints; not release files.

Every included journal-year has at least one defined NF or ANS across the four
universe/treatment combinations. Zero is defined; null is unavailable.

## Reproduce the website tables

`website/` holds the corresponding website source and builder. The website uses
`engine.js` and `presets.js` for percentile membership and ranks; it is not silently
reimplemented in Python. See `docs/WEBSITE.md` to build a local site from a release.
Custom browser filtering changes the displayed selection, not the underlying
annual full-data files. Percentiles depend on settings and are not universal
columns in the annual canonical export.

## Tests

```sh
python tests/run_tests.py
```

Tests create tiny synthetic snapshots in temporary directories. They test
extraction, identities, source repairs, matching, solver normalization and export
contracts. They do not download data or use the real register. Full replay is
separate and recorded explicitly.

## Releases and licensing

Software tags use `software-v*`; dataset tags use `data-*`. Neither ordinary code
pushes nor website-only changes publish to Zenodo. See `docs/RELEASING.md`.
The package is MIT-licensed; OpenAlex metadata is CC0, and Norwegian Register
attribution is retained. Derived annual datasets use CC BY 4.0. Font and JavaScript
vendor licenses are retained with the corresponding website assets. Raw publisher
contact fields from the Norwegian input are not exported or included in releases.

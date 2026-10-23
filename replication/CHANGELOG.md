# Changes

## 0.5.0 - 2026-10-23

- Adds five-year OJF5 alongside two-year OJF2, using the same fixed Norwegian Register citing sources and open-input policies.
- Extends the target metadata window to 2017 for the 2022-2026 calculations and preserves verified citing reference arrays.
- Adds metric-specific fields, counts, reference shares and percentile pools; the alternate OJF remains an optional table column.
- Preserves all thirty prior NF/ANS and OJF2 annual CSV/XLSX/Parquet files byte-for-byte, and keeps existing NF/ANS frozen-year pins.
- Adds complete OJF5 annual exports, dictionaries, all-cell format checks and release-layout builder verification.
- Includes ANP/ANP3 labels and the reviewed concise help text. OJF tables show one decimal place; selection downloads show three, and ranks use unrounded values.
- Keeps the original baseline replay evidence. Validates the source-bound OJF5 calculation, exact OJF2 frozen-count replay and window-specific browser behavior; no repeated complete scan for prior data is claimed.

## 0.4.0 - 2026-10-17

- Adds independently calculated Open Journal Factor (OJF), with fixed Norwegian Register citing sources, two-year counts, raw/filtered scores and explicit zero/missing statuses.
- Adds the portable all-type OpenAlex extraction, open identity/Norwegian matching, twelve-month maturity policy and full annual OJF export.
- Packages separate OJF CSV/XLSX/Parquet files and definitions without changing prior NF/ANS files or frozen-year pins.
- Updates the website with ANS/OJF selection, metric-specific presets and help, display/export rounding, optional table columns and independent/trademark wording.
- Validates all 578,622 frozen OJF journal-years exactly from bound open type-count checkpoints; does not claim a repeated raw-snapshot scan.
- The separate Paper_Replication_OJF package supplies downstream study analysis without proprietary inputs or outputs.

## 0.3.0 - 2026-10-12

- Adds optional rolling three-calendar-year averages of annual percentiles.
- Requires a selected-year eligible percentile independently for Field and Total;
  missing earlier years are ignored, never counted as zero. Averages are not reranked.
- Keeps NF/ANS and all other metadata specific to the selected year; annual popup
  percentiles stay annual. CSV/XLSX selection exports match the displayed averages.
- Close to EF & AIS (select) enables averaged Total percentiles, retaining 75% per field.
- Shortens the table context and explains averaging in the journal help page.
- Includes the full OpenAlex/Norwegian computation workflow and updated website.
  Calculation code, inputs, corrections and all annual data files remain unchanged.
- Validated by current website/replication tests and source/archive checks against
  the existing computation evidence. No new raw-input replay is claimed.

## 0.2.0 - 2026-10-06

- Adds four universe/treatment citation-input columns; retains all previous values.
- Displays Publications used and Citations used, with recorded totals in journal details.
- Makes Close to EF & AIS the default; both coverage presets use filtered metrics.
- Shows excluded matches during typed searches, with targeted filter controls.
- Refreshes the bundled website and adds the working-paper link.
- Validated from receipt-checked annual graphs and native scores; the original
  full raw-input replay remains a record of version 0.1.0, not this revision.
- Owner approved additive release validation instead of repeating the raw-input replay.
  New versioned releases preserve every original NF/ANS value and prior DOI version.

## 0.1.0 - prepared 2026-10-02

- Packages the validated September OpenAlex/Norwegian calculation modules with
  explicit local input paths and pinned annual identity decisions.
- Adds an isolated full replay command and full annual CSV/XLSX conversion.
- Retains the same NF/ANS mathematics, field definitions and journal population.
- Includes the corresponding website source, percentile engine, presets and tests.
- Adds separate deliberate software/data release tooling and public-input audit.
- Does not bundle snapshot data, raw register contact fields, commercial benchmarks,
  credentials or research results. Zenodo publication is paused.

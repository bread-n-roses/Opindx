# Changes

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

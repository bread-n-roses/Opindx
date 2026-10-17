# Reproduce Open Journal Factor

OJF is calculated from OpenAlex and approved Norwegian Register sources only.
It requires a separate two-year, all-type extraction; the NF/ANS five-year graph
is not used as an OJF graph. No commercial membership or metric input is needed.

First complete the public `opindx-replicate` workflow in the package README.
Then run, with a separate OJF work directory:

```sh
opindx-ojf --nfans-run /path/to/nfans-run --work /path/to/ojf-run --threads 4 --memory 8GB
opindx-ojf-export /path/to/ojf-run/annual /path/to/ojf-complete
```

The first command reads the original downloaded snapshot through the NF/ANS run's
hash-pinned manifest and reuses its independently reproducible Source repair and
Norwegian matching rules. It extracts all Work types for 2020-2026 and reference
arrays for 2022-2026. Work IDs are deduplicated by latest update, then stable file,
linking-ISSN and row ordering. Work correction preimages are checked before use.
Unmatched Norwegian entries remain unmatched and have an explicit audit.

## Calculation

For citation year t, the denominator covers t-2 and t-1. Citable types are
articles, reviews, data papers and software papers; paratext is excluded.

- **Raw:** citations from all approved Norwegian Register channels in t to
  eligible target content, divided by all citable items in the two-year window.
- **Filtered:** remove citable items without a reported OpenAlex reference from
  the denominator and remove citations to those items from the numerator.
  Citations to non-citable content remain included.
- Journal self-citations and citations from approved non-journal channels remain
  included. Citing membership stays fixed when the website's recipient selection
  changes between OpenAlex and Norwegian Register.
- In 2024-2026, citations to/from items marked retracted or retraction notices in
  the snapshot are excluded. Denominator items remain. In 2022-2023, both are
  retained under the study's recorded primary policy. Both diagnostic policies
  can be reproduced by the engine; they are not additional public main metrics.
- A positive denominator and no citations gives a defined zero. An absent
  denominator gives a missing score; Filtered denominator collapse has a separate
  status. Never convert a missing score to zero.

Fields use Raw articles/reviews in the same two-year window, named mode with
alphabetical tie resolution and a separate Unknown group. The optional browser
percentiles use the archived website engine and selected settings. Three-year
percentile averages require an eligible current-year percentile and ignore
missing earlier years within the strict three-calendar-year window.

## Outputs and maturity

`annual/` holds full-precision annual OJF Parquets and a manifest. The exporter
adds CSV, XLSX and a data dictionary, checking every cell against its Parquet
source. OJF files are separate from five-year NF/ANS files. Public archives
preserve the complete reporting population, including compatibility rows with
no defined OJF, and include no commercial-membership flag.

All metrics follow the twelve-month policy: year t remains live through December
31 of t+1 and is freeze-eligible on January 1 of t+2. In the September 2026
snapshot, 2022-2024 are freeze-eligible and 2025-2026 are provisional; 2026 is
incomplete. Existing frozen NF/ANS year pins are preserved.

The 0.4.0 release was validated by exact replay of all count/score/status cells
from the five frozen open-input type-count checkpoints, open metadata matching,
synthetic engine tests, and all-cell format checks. A new raw-snapshot scan was
not performed for this additive release. The extraction command is supplied for
independent reproduction; it can require substantial disk space and time.

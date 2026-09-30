# Opindx website

A public website where anyone can browse, filter and download open journal scores: Journal Network Share (JNS), Article Network Score (ANS) and ANS percentiles, computed from OpenAlex by the Opindx back-end. JNS and ANS follow the recursive citation-network method of Bergstrom, West and Wiseman (2008). The original names of these scores are trademarks of the University of Washington and are not used. No accounts, no payments; all data is open. Expected traffic is low (a few downloads a day).

The back-end is a separate project (currently run on a laptop, possibly Docker/cloud later). This project covers the website, the data layout the back-end delivers, and which score years are published from which run.

## Architecture

- Static website: plain HTML, CSS and JavaScript, no framework, no build step. Everything runs in the visitor's browser.
- Hosted free on GitHub Pages, later with a custom domain.
- Each data run is published as a GitHub Release of this repository. The releases are the permanent archive.
- The website shows **one published data set**: one file per score year, each taken from the run named in `score-years.json`, or from the latest run if the year is not named there. See "Frozen and live score years".
- A deploy workflow (GitHub Actions) runs on every new release and on demand. It downloads the latest release and every run named in `score-years.json`, checks the files, assembles the published data set and pushes the finished site as a single commit to the `gh-pages` branch, which GitHub Pages serves. See `publishing-data.md`.
- No account or repository names in the code, because the repository will change owner.

## Data layout

One release per run. The tag is the run name, e.g. `2026-Q3`. Each release contains:

**`manifest.json`**

```json
{
  "run": "2026-Q3",
  "created": "2026-09-21",
  "dummy": false,
  "openalex_snapshot": "2026-06-26",
  "norwegian_register_snapshot": "2026-07-26",
  "years": [2022, 2023, 2024, 2025],
  "universes": {"n": "Norwegian Register", "oa": "OpenAlex"}
}
```

**`scores_<year>.parquet`**, one per score year: one row per journal that is in at least one universe that year. Missing values are nulls; a zero is a real zero.

| Column | Type | Meaning |
|---|---|---|
| `openalex_id` | text | OpenAlex source ID, e.g. `S117778295` |
| `title`, `publisher` | text | |
| `issn_l` | text | Linking ISSN |
| `issns` | text | All ISSNs, separated by `; ` |
| `oa_domain`, `oa_field` | text | OpenAlex primary domain and field |
| `norwegian_area`, `norwegian_field` | text | Norwegian Register area and field |
| `norwegian_level` | text | 1 or 2 in this score year; `1 | 2` when the journal has two register entries |
| `norwegian_register_url` | text | The journal's page in the Norwegian Register; empty when it is not in the register |
| `is_open_access` | boolean | |
| `score_year` | integer | Year t |
| `publications_raw` | integer | Articles and reviews published in t-5 to t-1 |
| `publications_filtered` | integer | Those with at least one recorded reference |
| `citations_raw`, `citations_filtered` | integer | Citations in year t to those publications, excluding journal self-citations |
| `reference_coverage_pct` | number | 100 × `publications_filtered` / `publications_raw` |
| `active_years` | integer | Years (of 5) with at least one publication |
| `in_<u>` | boolean | Journal is in universe `<u>` this year |
| `share_<u>_raw`, `share_<u>_filtered` | number | JNS in universe `<u>`; sums to 100 over the universe; null if not computed |
| `per_article_<u>_raw`, `per_article_<u>_filtered` | number | ANS in universe `<u>`; article-weighted mean 1; null if not computed |

`<u>` is each universe listed in the manifest. Adding a universe means new columns and a manifest entry, with no website changes.

The back-end delivers one CSV with all score years. `tools/import_run.py` converts it into the files above: it renames the pipeline's columns to the names in the table, takes the snapshot dates from the CSV, and marks a journal as being in the Norwegian Register universe when the run gives it a register entry.

## Frozen and live score years

A score year can be **frozen**: it is then always published from one specific run and no longer changes when new runs arrive. Which years are frozen is decided by hand in `score-years.json` in this repository:

```json
{
  "2024": "2026-Q2",
  "2025": "2026-Q2"
}
```

- A year in this list is **frozen** and comes from the named run, even when newer runs contain that year.
- A year not in the list is **live** and comes from the latest run, so a new run replaces it.
- The published years are all years of the latest run plus all years in the list.
- Freezing, repointing to a newer run, and unfreezing (removing the line) are changes to this file, made in a pull request, so the history shows who changed what and when.
- The deploy fails with a clear message if a named run does not exist or does not contain that year.

The runs themselves are never changed: every run stays complete and downloadable as a release.

## Published data set

The deploy writes this into the site (it is not committed to git):

```
site/data/index.json             what is published per score year
site/data/scores_<year>.parquet  the year's data, from its run
site/data/history/00…99.parquet  all years per group of journals, for the journal details
```

`index.json` holds, per score year, newest first: the year, `frozen` or `live`, the run it came from, that run's OpenAlex and Norwegian Register snapshot dates, its creation date, its release link and its universes. The website reads the universes per year, so a frozen year keeps the universes of its own run when a later run adds one.

## Website

- Top bar: score year, universe (dropdown, the universes of the selected year) and a Settings button. All other choices are in one collapsible settings panel.
- Above the table, next to the journal count: whether the year is frozen or live, plus the OpenAlex snapshot date, e.g. "Frozen · 2026-06" or "Live · 2026-09". The run name and the other snapshot dates are shown on hover, and a "?" explains the difference between frozen and live.
- The table shows only journals in the selected universe, with their JNS and ANS. Search by title, ID, ISSN or publisher; sort by any column; rows are shown a page at a time.
- When a search finds nothing, a line reports how many journals match in another universe, e.g. "3 journals match in the OpenAlex universe".
- Columns: journal (with a "More info" link that opens the details), OpenAlex field, publications, citations, reference coverage, JNS and ANS, plus the percentile columns when those are switched on.
- Filters in the settings panel: OpenAlex fields (multiple), publishers (multiple) and open access only. Of the OpenAlex classification, only the 26 fields are used: no domains, no subfields.
- Defaults: Filtered scores; a setting switches to Raw.
- Percentiles are hidden until they are switched on. Their settings start at the values from the working paper and report (ANS, reference coverage above 20%, output in at least 4 of 5 years, top 70% per field) and a "Reset percentile settings" button returns to them. They are computed in the browser, within the selected universe, grouped by OpenAlex field. Ties get the highest shared rank.
- Journal detail view: metadata, a link to OpenAlex, the Norwegian level with a link to the journal in the register and to the register's explanation of levels, and a table with the journal's scores in every published score year (selected universe and Raw/Filtered), including each year's data vintage. The other years come from small history files, downloaded when a journal is opened.
- Downloads: the current view as CSV; one CSV built from the chosen score years and universes of the published data set (journals in at least one chosen universe, Raw and Filtered columns, and the run each score year came from); complete Parquet files per year; older runs via the releases page.
- About page (`about.html`, no table): what the scores measure, journal universes, Raw and Filtered scores, frozen and live score years with their data vintage, a summary of the working paper and how to cite it, and data sources with attribution (Norwegian Register). Both pages share a small navigation.
- Dummy data runs show a clear banner.

## Repository layout

```
site/                  the website (data/ is filled in by the deploy workflow)
tests/                 tests for the ranking logic
tools/                 import_run.py (back-end CSV -> a run), make_dummy_data.py, build_site_data.py (checks runs, assembles the published data set, writes 100 small history files), serve_site.py (local preview)
.github/workflows/     deploy workflow
score-years.json       which score years are frozen, and from which run
```

## Open points

- Score names: JNS and ANS are our own names. Permission or legal advice on the original names is still open.
- License for the code and for the data.
- Custom domain.
- Small-field pooling for percentiles: the report pools fields with fewer than 100 journals, this site doesn't.

## Rolling Work fields (rolling-primary-work-broad-field-v1)

New runs include `field_assignment` in their manifest. For citing year t,
`oa_field` is the modal primary-topic broad field across the corrected,
deduplicated article/review Works published in t-5 through t-1. Use Raw works
for both score treatments and all universes. No field pooling. Exclude unnamed
fields from the mode and its denominator; use `Unknown` if none are classified.
Ties select the alphabetically first field. `oa_domain` is that field's parent.

Additional numeric columns:

- `oa_field_modal_share`: winning count / classified works, fraction 0-1; null for Unknown.
- `oa_field_modal_works`: winning count, or zero for Unknown.
- `oa_field_classified_works`: count of works with a named primary-topic field.
- `oa_field_classification_coverage`: classified works / `publications_raw`, fraction 0-1; null if no works.
- `oa_field_tied_modes`: number of fields tied for first, or zero for Unknown.

The table displays e.g. `Medicine (19.5%)`, with denominator, coverage and tie
information in the tooltip/details. Field filters and percentile groups use the
plain `oa_field`; percentages never become group keys. CSV downloads keep labels
and numeric shares separate. The build retains field-method provenance in each
published year's index entry. Historic runs without this metadata remain readable.
The existing percentile settings and freeze-year configuration are unchanged.

The integrated AIS website exporter produces these release files directly from
the immutable score CSV plus a hash-bound annual field sidecar. Use that workflow
for rolling-field runs; this repository's legacy CSV converter is for historic
source-field CSVs and must not be used to drop the rolling metadata.

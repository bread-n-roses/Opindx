# Opindx website

A public website where anyone can browse, filter and download open journal scores: Network Factor (NF), Article Network Score (ANS) and ANS percentiles, computed from OpenAlex by the Opindx back-end. NF and ANS follow the recursive citation-network method of Bergstrom, West and Wiseman (2008). The original names of these scores are trademarks of the University of Washington and are not used. No accounts, no payments; all data is open. Expected traffic is low (a few downloads a day).

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
| `share_<u>_raw`, `share_<u>_filtered` | number | NF in universe `<u>`; sums to 100 over the universe; null if not computed |
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

Normally runs remain complete and downloadable under distinct tags. Explicitly authorized same-snapshot corrections are documented in publishing-data.md.

## Published data set

The deploy writes this into the site (it is not committed to git):

```
site/data/index.json             what is published per score year
site/data/scores_<year>.parquet  the year's data, from its run
site/data/history/00…99.parquet  all years per group of journals, for the journal details
```

`index.json` holds, per score year, newest first: the year, `frozen` or `live`, the run it came from, that run's OpenAlex and Norwegian Register snapshot dates, its creation date, its release link and its universes. The website reads the universes per year, so a frozen year keeps the universes of its own run when a later run adds one.

## Website

- **Search status (2026-10-05):** A subdued blue bar below the search controls shows loading progress, then the live selection and percentile counts. It remains visible and updates with the search/filter choices. The table is a separate box with Download selection and Restore columns at the upper right; do not repeat the summary there. Loading errors and dummy-data notices remain explicit.
- **Typography (2026-10-05):** About and Journal Metrics share Quicksand page headings and taglines. Keep the About/help reading column narrower than the main journal table. About/help prose and Complete data and code use 13px body text with 1.75 line height; box headings use 15px/600 and help/reset links 12px. The selection summary and Download selection button use 14px; summary numbers are near-black, surrounding text muted grey. Keep metric-table typography compact.
- **Link behavior (2026-10-05):** Links to another web page, whether on Opindx or an external website, open in a new tab unless the user specifies otherwise. Use `target="_blank" rel="noopener noreferrer"` and the shared arrow indicator. Same-page section links continue to scroll within the current page or popup.
- Header navigation: Journal metrics and About. Main title: Journal Metrics; search is the primary control beneath the tagline.
- Search and table share visible controls: Citation year, OpenAlex fields (multiple), Journal universe, and Presets. The default citation year is the latest available full calendar year, currently 2025; newer years remain selectable.
- Max coverage (noisy): initial OpenAlex/Filtered preset; ANS Field percentiles only. Requirements: less than 80% of publications without references (equivalent to coverage strictly above 20%), at least four publication years, top 100% per field. Hide journals without percentiles is unchecked; all exported members remain visible.
- Closest to EF and AIS: same requirements as Max coverage (noisy), using Norwegian Register and ANS Field percentiles only.
- VU Amsterdam (SBE): Norwegian Register/Filtered ANS; Total percentiles only, less than 80% without references, at least four publication years, top 75% per field; hide journals without a Total percentile.
- Advanced settings replaces the cog and contains open-access/reference-treatment checkboxes and a compact shaded percentile row. Custom opens it; choosing any named preset closes it. The +/minus link allows manual opening/closing. Named presets preserve search/year/field/access filters where compatible; Reset clears these display choices while preserving the citation year.
- Open access journals only and Include publications without references (not recommended) appear in that order below the dropdowns. The latter selects Raw explicitly. There is no publisher filter or More options panel. Changing universe refreshes/reconciles field choices. The fields dropdown begins with All fields checked and individual fields unchecked. An individual choice unchecks All fields; checking All fields clears the individual selections. No repeated visible heading or field-search input.
- If a restricted search has no results, Search all OpenAlex journals switches to Full and clears field/open-access filters, retaining query/year and Filtered treatment.
- Table toolbar: selection/member counts and the retained count labelled "journals with percentile". No eligibility or field-group count. The help/tooltip explains that the percentile count covers the whole selected universe. Display filters do not redefine comparison pools. Download this selection saves CSV/XLSX for the entire selection across all pages, using only columns visible onscreen.
- Percentile settings place Show percentiles and Hide journals without percentiles side by side below the dropdowns, wrapping on narrow screens. Show percentiles stays usable when the calculation controls and hide-journals checkbox are disabled. Compact table cell padding, minimum widths and header controls reduce laptop horizontal scrolling without reducing text size; mobile touch targets remain larger.
- Table header: publication window, citation year, OpenAlex snapshot month, Frozen/Live metrics, and a help link. Metric groups identify the selected universe and Percentiles with ANS or NF. Columns: Journal title, OpenAlex field, Publications, Citations, Pubs. w/out refs., NF, ANS; optional Field and Total percentiles. Journal cells show title and more info only.
- Every main-table column except Journal title has an accessible hide button (×), separate from its sort button. Grouped headers adapt to the remaining columns. Restore columns returns hidden columns. Visibility is a display preference for the visit, preserved through year/preset/filter changes and cleared by Reset or reload. It never changes presets, sort order, percentile calculations, journal filtering or popup columns; selection downloads follow the visible columns.
- Percentiles retain the existing empirical-CDF/tie rules and use the selected universe and OpenAlex broad field. No pooling of small fields. Pool-only affects display only when percentiles are enabled. Year changes ignore stale responses and restore the prior selection on failure.
- Journal details retain the local redesign described below. Percentiles in the popup use each historical year's own population with the current ranking settings.
- A sticky section bar beneath the journal title links to the Details, Fields covered and Metrics by year headings, and contains the close button. Its links scroll only the dialog, focus the heading and correct overlap against the bar's actual bottom after scrolling, including dialog padding and wrapped navigation. The bar uses large touch targets and remains available while scrolling. Opening another journal starts at the top. Show percentiles is a multi-select picker: None, Field percentiles, Total percentiles. The selected types apply to the main table, journal history and CSV; manual header crosses affect the main table and its selection downloads. Restore columns restores only types selected in settings. The percentile-presence filter and summary count use Total when selected, otherwise Field; None disables that filter.
- Complete data and code: annual full Parquet files and older releases; separate reproducibility repository marked coming soon. No second CSV builder or download-selection dropdown.
- `journal-help.html` defines every header in the main, historical, field-breakdown and About tables. Main-table help opens `#journal-table`, including Pubs. w/out refs. alongside the other column definitions; popup links use `#journal-details`, `#fields-covered` and `#yearly-metrics`; About links to `#data-versions`. Help also explains presets, percentile settings and counts. Help and external journal links open new tabs with a shared arrow indicator.
- Shared footer: Opindx, License CC BY 4.0 linked to https://creativecommons.org/licenses/by/4.0/; Open Data · Open Source · Nonprofit · Made at Vrije Universiteit Amsterdam, with the university name as plain text (no link). No public email. Contact page deferred because no protected mail backend is configured.
- Responsive controls, readable touch targets, horizontally scrolling data tables and wrapping prose are required throughout. Browser/mobile visual QA remains pending. Dummy/error/loading notices remain visible; no routine shaded run banner.

## Repository layout

```
site/                  the website (data/ is filled in by the deploy workflow)
tests/                 tests for the ranking logic
tools/                 import_run.py (back-end CSV -> a run), make_dummy_data.py, build_site_data.py (checks runs, assembles the published data set, writes 100 small history files), serve_site.py (local preview)
.github/workflows/     deploy workflow
score-years.json       which score years are frozen, and from which run
```

## Open points

- Score names: NF and ANS are our own names. Permission or legal advice on the original names is still open.
- Code license choice and alignment of release metadata with the requested CC BY 4.0 data footer.
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

## Local journal details preview (2026-10-01)

The redesigned popup orders ISSN/publisher, OpenAlex and annual Norwegian Register
links, open-access status, publication years and Pubs. w/out refs. (100 minus reference coverage). Field shares
are shown under Fields covered; per-ID Norwegian assignments occupy separate rows
when multiple fields exist. The main register link uses a unique title match,
with other IDs retained; ambiguous matches have no designated main entry.

Metrics by year preserves the full selected universe/treatment title suffix.
Columns are Citing year, Journal universe, Level, Publications, Citations, NF, ANS.
Optional Field and Total percentile columns share a Pctiles heading identifying
the selected ranking indicator. Annual populations and current ranking settings
define each year's percentiles. Membership abbreviations OA and NR appear inside
the table. journal-help.html explains the columns without nested popups.

Schema version 2 now includes the additional field/register metadata in each
annual release file. The regular builder writes enriched history and compact
ranking files, selected through index.json journal_details metadata. The earlier
local preview installer remains available for historical testing.
The whole site must be mobile friendly; popup and help layouts have responsive
rules, but real mobile/browser visual QA remains pending.


## Complete schema version 2 (2026-10-02)

Annual Parquets retain the original columns and add `oa_field_1` through
`oa_field_3`, their corresponding `_works` integer counts, `oa_field_other_works`,
`norwegian_entries_json`, and `norwegian_primary_id`. Existing classified-work
counts provide the field-share denominator. Per-ID metadata is a JSON string
containing the true register ID, assigned field and title; the optional primary
ID controls link presentation only.

The manifest adds `schema_version: 2`, `journal_details`, `population`,
`data_revision`, `export_provenance`, and `assets` keyed by annual filename with
SHA-256, byte size and row count. Required v2 metadata is validated; additional
columns remain compatible. The builder continues to accept schema version 1.

Population is unchanged: at least one defined NF or ANS in any exported
universe/treatment. Zero counts as defined; all-missing journal-years are excluded.
All 552,179 original journal-year rows and cells are preserved. Publication years
before 2022 remain out of scope for this update.

The data builder validates inputs before replacing site/data. It creates history
shards and per-year/universe/metric/treatment rank files, with no local-preview
paths. Deployments test all JavaScript suites and the Python builder tests, and
version every local JavaScript module import and all page styles consistently.

## Branding and help navigation (2026-10-02, local update)

Use the supplied Op__ind_x header logo and circle/bullet favicon on all pages.
Keep a near-black/white layout with subdued blue accents and muted rust bars for
Pubs. w/out refs. Quicksand is locally hosted for the main title and larger
tagline; tables retain their readable sans-serif font.

Help is titled What is what? Its sticky section links leave heading titles
visible; the cross closes the tab, returning to Journal Metrics if closing is
blocked. NF means Network Factor; ANS remains Article Network Score. Public
score field identifiers and calculations remain compatible with the release.

## Download and label polish (2026-10-02, local update)

The popup and percentile controls use Share of publications w/out references;
the main table keeps Pubs. w/out refs. The Advanced settings plus/minus indicator
uses centered geometric bars rather than a font glyph.

Download selection opens CSV/XLSX choices. Both formats use the same
captured selection and displayed column order across all result pages. Hidden
columns and popup/settings metadata are omitted. Headers match the table, with
NF and ANS named directly and percentile headers naming their metric. The
OpenAlex field cell becomes a name column and a numeric percentage column
(0 to 100); hiding the field removes both. XLSX uses a local, licensed SheetJS 0.20.3
module loaded only on demand, preserving numeric values and literal strings.
The interface shows preparation status and allows retry/CSV after a failure.
The complete-data introduction omits the parenthesized file-format label.

## Public wording and controls (4 October 2026)

The year picker shows the published live/frozen status. Complete datasets link to
the DOI-versioned Zenodo archive, and all page footers credit Utz Weitzel.
The publication policy is to freeze a score year twelve months after it ends.
Release maintainers enforce this by pinning the final eligible run in score-years.json
before publishing a later run; frozen status is not a browser date calculation.
Thus 2025 remains live through 2026 and must be pinned at that year-end boundary.

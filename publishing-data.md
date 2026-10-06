# Publishing a new data run

Each pipeline run is published as a GitHub Release. The website then updates itself, but only for the score years that are **live**: years that are **frozen** keep coming from the run they are frozen to. See "Freezing a score year" below.

## One-time setup (repository owner)

**Settings â†’ Pages**: under *Build and deployment*, set **Source** to **Deploy from a branch**, then choose the branch **gh-pages** and the folder **/ (root)**, and click **Save**. The `gh-pages` branch appears after the deploy workflow has run once; it holds the finished website and is overwritten on every deploy.

## Steps

1. Run the pipeline. It writes one CSV with all score years.
2. Turn that CSV into the files for the release. Open `tools/import_run.py`, set the three values at the bottom and run the file (in VS Code: the Run button; there is nothing to type on a command line):

   - `file_path`: the CSV the pipeline wrote.
   - `output_location`: the folder to write the run into.
   - `run_name`: the name of this run, e.g. `2026-Q3` for the quarter the data was made in. Use the same name as the release tag in the next step.

   This writes `<output_location>/2026-Q3/`, with a `scores_<year>.parquet` per score year and a `manifest.json` (exact contents: see `SPEC.md`). It prints how many journals each score year has, per universe; stop and check the CSV if those numbers look wrong.
3. On GitHub, open the repository and click **Releases** (right-hand side), then **Draft a new release**.
4. Under **Choose a tag**, type the run name exactly as in `manifest.json` (e.g. `2026-Q3`) and select **Create new tag on publish**. Use the same name as the title. This name is also what `score-years.json` refers to.
5. Drag all files from the export folder into the upload box. Wait until every upload has finished.
6. Click **Publish release**.

The live score years show the new run within a few minutes; frozen years stay as they are. The website shows per score year whether it is frozen or live, with the OpenAlex snapshot date from `manifest.json`. To follow progress, open the **Actions** tab. Two runs appear one after the other: **Deploy website** checks the data and builds the site, then **pages build and deployment** puts it online.

- Green check on both: the new run is online. A *cancelled* **pages build and deployment** can be ignored: a newer one replaced it.
- Red cross on **pages build and deployment**: usually a temporary problem at GitHub. Open the run and click **Re-run failed jobs**.
- Red cross on **Deploy website**: the files failed a check and nothing was published. The previous version stays online, and GitHub sends you an email with the reason.

## Freezing a score year

A score year that should no longer change is listed in `score-years.json`, with the run it comes from:

```json
{
  "2024": "2026-Q2",
  "2025": "2026-Q2"
}
```

- Edit the file on GitHub (pencil icon) and merge the change. The deploy starts by itself, and from then on that score year is always published from that run; new runs no longer change it.
- A score year that is not listed follows the newest run.
- Remove a line to let a year follow the newest run again, or change the run name to move it to a different run.

The deploy stops with a clear message if the named run does not exist or does not contain that score year.

## Fixing mistakes

- **Wrong or missing file:** open the release, click the pencil icon, remove or add files and click **Update release**.
- **Remove a run:** open the release and delete it. First check `score-years.json`: if a score year is frozen to that run, remove or change that line in the same go, otherwise the deploy stops with an error.

After either fix, open **Actions**, select **Deploy website** and click **Run workflow**.

## September 2026 corrected release

On 30 September 2026, the owner authorized replacement of `2026-Q3` with the
corrected September snapshot data for citing years 2022-2026, including rolling
primary-work fields and modal shares. `score-years.json` pins 2022, 2023 and
2024 to this replacement; 2025 and 2026 remain live.

Use the integrated AIS exporter for these data: it already writes the complete
manifest and yearly Parquet release files, including field-method provenance.
Do not run the legacy CSV converter on the historical CSV to recreate this
release; that CSV retains the older source-based field labels.

Future data updates must use a new release tag and matching manifest run name.
Do not replace `2026-Q3` again without an explicit correction decision, because
three frozen years now depend on its assets. No automatic 12- or 18-month
freezing rule is introduced by this manual decision.

## Same-snapshot format update (2 October 2026)

The user authorized replacement of all five `2026-Q3` annual files using the
same September snapshot and schema version 2. Retain the existing 552,179
journal-year records and every original metric value. Include a journal when
at least one NF or ANS is defined in an exported universe/treatment; numerical
zero counts as defined. Do not add journals with all metrics missing.

The integrated exporter now packages the top-three field breakdown and per-ID
register metadata. `build_site_data.py` builds enriched annual history (including
citations) and compact historical percentile inputs directly from release files.
The manifest carries schema version, provenance, and each file's hash, size and
row count. The original CSV and prior scientific receipts remain unchanged.

For this owner-authorized replacement, control the deployment workflow while
performing the requested sequence: temporarily remove the freeze pins, replace
all six release assets, restore the 2022-2024 pins to `2026-Q3`, then deploy the
completed site. Keep a verified local copy of the prior release. 2025-2026 remain
live. Earlier years (2020 and 2021) are deferred to a separate update.

This same-snapshot replacement does not set a policy for future snapshots;
those should still receive distinct release tags to preserve frozen results.

## Separate software and dataset releases (prepared 2 October 2026)

The published data tag is now `2026-09-v2`, with 2022-2024 pinned to that
revision. The current preparation adds complete annual CSV/XLSX alongside
the unchanged Parquets. Both bundles are now published: [software v0.1.0](https://doi.org/10.5281/zenodo.23108424)
and [complete data-2026-09-v2](https://doi.org/10.5281/zenodo.23108621). Frozen-year pins remain on the unchanged
2026-09-v2 Parquets; live years can use the identical Parquets in data-2026-09-v2.

The full calculation and matching website are in [replication/](replication/README.md).
Use `software-v*` for the software archive and `data-*` for complete datasets.
Their Zenodo DOI series are separate; ordinary pushes never publish to Zenodo.
See [release instructions](replication/docs/RELEASING.md) and the actual
[validation status](replication/docs/VALIDATION.md) before publication.

The Zenodo workflow requires `ZENODO_PUBLISH_ENABLED=true`; it was enabled after owner authorization on 2 October 2026. Deliberate data/software release publication triggers it; ordinary code pushes do not.
Creator metadata is complete: Utz Weitzel (VU Amsterdam; Radboud University Nijmegen). The deployment workflow now
selects releases by their data manifest and annual Parquets, skips software
release events and downloads only the files needed by the website. This keeps
software releases from replacing the website's active data selection.

## Custom domain preservation

The public website is https://opindx.org. Keep `site/CNAME` set to `opindx.org`: the deployment rebuilds and replaces the `gh-pages` branch, so this file must travel with the site. The workflow checks it before publishing. Keep the existing GitHub Pages custom domain, HTTPS enforcement, verification TXT record and DNS records described in the local `website-runs/opindx_github_pages_handoff.md`.

## Twelve-month freezing policy (4 October 2026)

At the end of the calendar year following a score year, freeze that score year
to its final eligible published run in `score-years.json`. For example, 2025
remains live through 31 December 2026 and is frozen thereafter. Before publishing
a run in 2027, pin 2025 to the final 2026 run, so later data cannot alter it.
The current 2022–2024 pins already meet this policy; 2025–2026 remain live.
Apply and review the pins during release maintenance, keeping the pinned release
available. The browser displays the actual published status from `index.json`.

## Publication follow-up: 2026-10-06

Software v0.2.0: https://doi.org/10.5281/zenodo.23187011 .
Dataset 2026-09-v3: https://doi.org/10.5281/zenodo.23187363 .
Both are new versions in the existing DOI series. Public file checksums,
creator/affiliations, licenses and the dataset-to-software DOI link passed.
The website was deployed and verified first. Frozen years 2022-2024 use the
new schema with unchanged metrics; 2025-2026 remain live. Validation used the
owner-approved additive exception, preserving the original full replay.

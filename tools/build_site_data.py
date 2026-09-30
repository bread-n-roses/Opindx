"""Check data runs and assemble the published data set for the website (site/data/).

Usage: python tools/build_site_data.py <runs_folder> <latest_run> [releases_url]
<runs_folder> holds one subfolder per run (manifest.json + scores_<year>.parquet, see SPEC.md).
Score years named in score-years.json come from that run; every other year comes from <latest_run>.
The deploy workflow runs this on the downloaded releases; locally, run it on export/ to preview the site.
Stops with an error if a run doesn't match the layout, so nothing broken gets published. Needs pyarrow.
"""
import json
import shutil
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
SITE_DATA = ROOT / "site" / "data"
SCORE_YEARS = ROOT / "score-years.json"  # score year -> the run it is frozen to
COLUMNS = ["openalex_id", "title", "publisher", "issn_l", "issns", "oa_domain", "oa_field", "norwegian_area",
           "norwegian_field", "norwegian_level", "norwegian_register_url", "is_open_access", "score_year", "publications_raw",
           "publications_filtered", "citations_raw", "citations_filtered", "reference_coverage_pct", "active_years"]


FIELD_COLUMNS = ["oa_field_modal_share", "oa_field_modal_works", "oa_field_classified_works",
                 "oa_field_classification_coverage", "oa_field_tied_modes"]


def check_run(folder):
    manifest_file = folder / "manifest.json"
    if not manifest_file.exists():
        sys.exit(f"{folder.name}: manifest.json is missing")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    for key in ("run", "created", "years", "universes"):
        if key not in manifest:
            sys.exit(f"{folder.name}: manifest.json has no '{key}'")
    if manifest["run"] != folder.name:
        sys.exit(f"{folder.name}: manifest.json says run '{manifest['run']}', but the release is called '{folder.name}'")
    universes = manifest["universes"]
    expected = COLUMNS + [f"in_{u}" for u in universes] + [
        f"{metric}_{u}_{treatment}" for u in universes for metric in ("share", "per_article") for treatment in ("raw", "filtered")]
    if manifest.get("field_assignment"):
        expected += FIELD_COLUMNS
    for year in manifest["years"]:
        path = folder / f"scores_{year}.parquet"
        if not path.exists():
            sys.exit(f"{folder.name}: {path.name} is missing")
        missing = [c for c in expected if c not in pq.read_schema(path).names]
        if missing:
            sys.exit(f"{folder.name}/{path.name}: missing columns {missing}")
    return manifest


def write_history(published):
    """Split all published years into 100 small files by the last two digits of the journal ID.

    The journal details then download one small file instead of every year's full file. (GitHub Pages
    compresses Parquet files, so the browser cannot download just the part of a file it needs.)
    """
    tables = []
    for year in published:
        columns = ["openalex_id", "score_year", "norwegian_level", "publications_raw", "publications_filtered",
                   "reference_coverage_pct"]
        for u in year["universes"]:
            columns += [f"in_{u}", f"share_{u}_raw", f"share_{u}_filtered", f"per_article_{u}_raw", f"per_article_{u}_filtered"]
        tables.append(pq.read_table(SITE_DATA / f"scores_{year['year']}.parquet", columns=columns))
    history = pa.concat_tables(tables, promote_options="default")  # years can have different universes
    group = pc.utf8_slice_codeunits(history["openalex_id"], -2)
    (SITE_DATA / "history").mkdir()
    for key in pc.unique(group).to_pylist():
        pq.write_table(history.filter(pc.equal(group, key)), SITE_DATA / "history" / f"{key}.parquet")


def main(runs_folder, latest_run="", releases_url=None):
    frozen = json.loads(SCORE_YEARS.read_text(encoding="utf-8")) if SCORE_YEARS.exists() else {}
    runs, manifests = Path(runs_folder), {}

    def manifest_of(run):
        if run not in manifests:
            if not (runs / run).is_dir():
                sys.exit(f"run '{run}' was not downloaded; is there a release with that name?")
            manifests[run] = check_run(runs / run)
        return manifests[run]

    years = {year: latest_run for year in manifest_of(latest_run)["years"]} if latest_run else {}
    years.update({int(year): run for year, run in frozen.items()})

    shutil.rmtree(SITE_DATA, ignore_errors=True)
    SITE_DATA.mkdir(parents=True)
    published = []
    for year in sorted(years, reverse=True):
        run = years[year]
        manifest = manifest_of(run)
        if year not in manifest["years"]:
            sys.exit(f"score year {year} is frozen to run '{run}', but that run has no scores_{year}.parquet")
        shutil.copy2(runs / run / f"scores_{year}.parquet", SITE_DATA / f"scores_{year}.parquet")
        published.append({
            "year": year, "status": "frozen" if str(year) in frozen else "live", "run": run,
            "openalex_snapshot": manifest.get("openalex_snapshot"),
            "norwegian_register_snapshot": manifest.get("norwegian_register_snapshot"),
            "created": manifest["created"], "dummy": manifest.get("dummy", False),
            "universes": manifest["universes"],
            "field_assignment": manifest.get("field_assignment"),
            "release_url": f"{releases_url}/tag/{run}" if releases_url else None,
        })
    if published:
        write_history(published)
    index = {"releases_url": releases_url, "years": published}
    (SITE_DATA / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    frozen_count = sum(1 for year in published if year["status"] == "frozen")
    print(f"Published {len(published)} score year(s), {frozen_count} frozen, to {SITE_DATA}")


if __name__ == "__main__":
    main(*sys.argv[1:])

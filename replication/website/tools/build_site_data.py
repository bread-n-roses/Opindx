"""Check data runs and assemble the published data set for the website (site/data/).

Usage: python tools/build_site_data.py <runs_folder> <latest_run> [releases_url]
<runs_folder> holds one subfolder per run (manifest.json + scores_<year>.parquet, see SPEC.md).
Score years named in score-years.json come from that run; every other year comes from <latest_run>.
The deploy workflow runs this on the downloaded releases; locally, run it on export/ to preview the site.
Stops with an error if a run doesn't match the layout, so nothing broken gets published. Needs pyarrow.
"""
import json
import hashlib
import shutil
import sys
import tempfile
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
DETAIL_COLUMNS = [*[f"oa_field_{i}" for i in range(1, 4)],
                  *[f"oa_field_{i}_works" for i in range(1, 4)], "oa_field_other_works",
                  "norwegian_entries_json", "norwegian_primary_id"]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_table(table, year, universes, details=False):
    """Reject invalid populations and preserve null scores as nulls."""
    ids = table["openalex_id"]
    if ids.null_count or pc.count_distinct(ids).as_py() != len(table):
        raise ValueError(f"{year}: duplicate or missing journal IDs")
    if not pc.all(pc.match_substring_regex(ids, r"^S[0-9]+$")).as_py():
        raise ValueError(f"{year}: invalid journal IDs")
    if table["score_year"].null_count or not pc.all(pc.equal(table["score_year"], year)).as_py():
        raise ValueError(f"{year}: incorrect citing year")
    defined = pa.array([False] * len(table))
    members = pa.array([False] * len(table))
    for universe in universes:
        membership = table[f"in_{universe}"]
        if membership.null_count:
            raise ValueError(f"{year}: missing membership")
        members = pc.or_(members, membership)
        for metric in ("share", "per_article"):
            for treatment in ("raw", "filtered"):
                values = table[f"{metric}_{universe}_{treatment}"]
                present = pc.is_valid(values)
                if pc.any(pc.and_(present, pc.invert(membership))).as_py():
                    raise ValueError(f"{year}: score outside its universe")
                if pc.any(pc.fill_null(pc.or_(pc.less(values, 0), pc.invert(pc.is_finite(values))), False)).as_py():
                    raise ValueError(f"{year}: invalid score")
                defined = pc.or_(defined, present)
    if not pc.all(defined).as_py() or not pc.all(members).as_py():
        raise ValueError(f"{year}: every journal must belong to a universe and have at least one metric")
    if details:
        total = pa.array([0] * len(table), type=pa.int64())
        for col in [*[f"oa_field_{i}_works" for i in range(1, 4)], "oa_field_other_works"]:
            counts = table[col]
            if counts.null_count or not pa.types.is_integer(counts.type) or pc.any(pc.less(counts, 0)).as_py():
                raise ValueError(f"{year}: invalid field counts")
            total = pc.add(total, counts)
        if (table["oa_field_classified_works"].null_count
                or not pc.all(pc.equal(total, table["oa_field_classified_works"])).as_py()
                or not pc.all(pc.less_equal(total, table["publications_raw"])).as_py()):
            raise ValueError(f"{year}: inconsistent field breakdown")


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
    version = manifest.get("schema_version", 1)
    if version not in (1, 2):
        raise ValueError(f"Unsupported data schema version {version}")
    expected = COLUMNS + [f"in_{u}" for u in universes] + [
        f"{metric}_{u}_{treatment}" for u in universes for metric in ("share", "per_article") for treatment in ("raw", "filtered")]
    if manifest.get("field_assignment"):
        expected += FIELD_COLUMNS
    if version == 2:
        expected += FIELD_COLUMNS + DETAIL_COLUMNS
    for year in manifest["years"]:
        path = folder / f"scores_{year}.parquet"
        if not path.exists():
            sys.exit(f"{folder.name}: {path.name} is missing")
        missing = [c for c in expected if c not in pq.read_schema(path).names]
        if missing:
            sys.exit(f"{folder.name}/{path.name}: missing columns {missing}")
        table = pq.read_table(path)
        if version == 2:
            asset = manifest.get("assets", {}).get(path.name, {})
            if (asset.get("sha256") != digest(path) or asset.get("bytes") != path.stat().st_size
                    or asset.get("rows") != len(table)):
                raise ValueError(f"{folder.name}/{path.name}: manifest integrity check failed")
        validate_table(table, year, universes, details=version == 2)
    return manifest


def write_history(published, data):
    """Split all published years into 100 small files by the last two digits of the journal ID.

    The journal details then download one small file instead of every year's full file. (GitHub Pages
    compresses Parquet files, so the browser cannot download just the part of a file it needs.)
    """
    tables = []
    for year in published:
        columns = ["openalex_id", "score_year", "norwegian_level", "publications_raw", "publications_filtered",
                   "reference_coverage_pct", "citations_raw", "citations_filtered", "active_years"]
        for u in year["universes"]:
            columns += [f"in_{u}", f"share_{u}_raw", f"share_{u}_filtered", f"per_article_{u}_raw", f"per_article_{u}_filtered"]
        path = data / f"scores_{year['year']}.parquet"
        available = set(pq.read_schema(path).names)
        columns += [c for c in FIELD_COLUMNS + DETAIL_COLUMNS if c in available]
        tables.append(pq.read_table(path, columns=columns))
    history = pa.concat_tables(tables, promote_options="default")  # years can have different universes
    group = pc.utf8_slice_codeunits(history["openalex_id"], -2)
    (data / "history").mkdir()
    for key in pc.unique(group).to_pylist():
        pq.write_table(history.filter(pc.equal(group, key)), data / "history" / f"{key}.parquet")


def write_ranks(published, data):
    """Small optional downloads for historical percentiles, independent of display filters."""
    destination = data / "ranks"
    destination.mkdir()
    for year in published:
        table = pq.read_table(data / f"scores_{year['year']}.parquet")
        common = ["openalex_id", "oa_field", "reference_coverage_pct", "active_years"]
        for universe in year["universes"]:
            selected = table.filter(table[f"in_{universe}"])
            for metric in ("share", "per_article"):
                for treatment in ("raw", "filtered"):
                    columns = common + [f"in_{universe}", f"{metric}_{universe}_{treatment}"]
                    pq.write_table(selected.select(columns), destination / f"{year['year']}-{universe}-{metric}-{treatment}.parquet")


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

    # Validate every input before touching the currently assembled site data.
    for year, run in years.items():
        if year not in manifest_of(run)["years"]:
            raise ValueError(f"Score year {year} is missing from run {run}")
    SITE_DATA.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".data-build-", dir=SITE_DATA.parent) as temporary:
        work = Path(temporary).resolve()
        if work.parent != SITE_DATA.parent.resolve() or SITE_DATA.name != "data":
            raise ValueError("Unexpected data installation path")
        data = work / "data"
        data.mkdir()
        published = assemble(years, manifest_of, runs, data, frozen, releases_url)
        old = work / "previous"
        if SITE_DATA.exists():
            SITE_DATA.rename(old)
        try:
            data.rename(SITE_DATA)
        except Exception:
            if old.exists():
                old.rename(SITE_DATA)
            raise
    frozen_count = sum(1 for year in published if year["status"] == "frozen")
    print(f"Published {len(published)} score year(s), {frozen_count} frozen, to {SITE_DATA}")


def assemble(years, manifest_of, runs, data, frozen, releases_url):
    published = []
    for year in sorted(years, reverse=True):
        run = years[year]
        manifest = manifest_of(run)
        if year not in manifest["years"]:
            sys.exit(f"score year {year} is frozen to run '{run}', but that run has no scores_{year}.parquet")
        shutil.copy2(runs / run / f"scores_{year}.parquet", data / f"scores_{year}.parquet")
        published.append({
            "year": year, "status": "frozen" if str(year) in frozen else "live", "run": run,
            "openalex_snapshot": manifest.get("openalex_snapshot"),
            "norwegian_register_snapshot": manifest.get("norwegian_register_snapshot"),
            "created": manifest["created"], "dummy": manifest.get("dummy", False),
            "universes": manifest["universes"],
            "field_assignment": manifest.get("field_assignment"),
            "schema_version": manifest.get("schema_version", 1),
            "release_url": f"{releases_url}/tag/{run}" if releases_url else None,
        })
    if published:
        write_history(published, data)
        write_ranks(published, data)
    index = {"releases_url": releases_url, "years": published,
             "journal_details": {"version": 1, "history_path": "history", "rank_path": "ranks", "rank_layout": "metric-treatment"}}
    (data / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return published


if __name__ == "__main__":
    main(*sys.argv[1:])

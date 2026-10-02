"""Review and repair the JGR: Atmospheres source replacement before scoring.

OpenAlex's September 2026 S207178839 source combines the old JGR parent
ISSN-L with the modern Atmospheres section, and its Works include records
harvested from mirrors and repositories. The review uses exact snapshot rows,
publisher DOI patterns, source names, location provenance and citing-reference
status. Unrecognized content stops the run for fresh review.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


RUN = Path(__file__).resolve().parents[1]
SOURCE_ID = "https://openalex.org/S207178839"
JUNE_SOURCE_ID = "https://openalex.org/S4210205282"
PARENT_ISSN_L = "0148-0227"
ATMOSPHERES_ISSN_L = "2169-897X"
ATMOSPHERES_ISSNS = ("2169-897X", "2169-8996")
PUBLISHER_SOURCE_NAME = "Journal of Geophysical Research: Atmospheres"
MAG_SOURCE_NAME = "Journal of Geophysical Research Atmospheres"
AGU_ARTICLE_DOI = re.compile(r"^https://doi\.org/10\.(?:1002|1029)/20\d\djd[^/]+$", re.I)
AGU_RETRACTION_DOI = re.compile(r"^https://doi\.org/10\.(?:1002|1029)/jgrd\.[^/]+$", re.I)
EXTRA_COLUMNS = (
    "id", "language", "locations_count", "primary_location.landing_page_url",
    "primary_location.provenance", "primary_location.raw_source_name",
)
LEDGER_COLUMNS = (
    "work_id", "expected_source_id", "expected_raw_issn_l",
    "target_source_id", "target_raw_issn_l", "reason",
)
EXCLUSION_COLUMNS = (
    "work_id", "expected_source_id", "expected_raw_issn_l", "reason",
)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def extract_nonstandard_locations(metadata: pd.DataFrame,
                                  source_id: str = SOURCE_ID) -> pd.DataFrame:
    """Read only row groups with a JGR record lacking an AGU article DOI."""
    required = {"work_id", "source_id", "raw_issn_l", "source_file", "source_row", "doi"}
    if missing := sorted(required - set(metadata.columns)):
        raise ValueError(f"Targeted metadata lacks {missing}")
    affected = metadata.loc[metadata.source_id.eq(source_id)]
    if affected.empty or affected.work_id.duplicated().any():
        raise ValueError("JGR candidate Works must be present and unique")
    selected = affected.loc[~affected.doi.fillna("").map(
        lambda value: bool(AGU_ARTICLE_DOI.fullmatch(value)))]
    by_file: dict[str, list[dict]] = defaultdict(list)
    for row in selected.to_dict("records"):
        by_file[row["source_file"]].append(row)
    result: list[dict] = []
    for filename, items in sorted(by_file.items()):
        parquet = pq.ParquetFile(filename)
        boundaries = [0]
        for group in range(parquet.num_row_groups):
            boundaries.append(boundaries[-1] + parquet.metadata.row_group(group).num_rows)
        by_group: dict[int, list[tuple[int, dict]]] = defaultdict(list)
        for item in items:
            position = item["source_row"]
            if not isinstance(position, int) or position < 0 or position >= boundaries[-1]:
                raise ValueError(f"Snapshot row outside file: {filename}:{position}")
            group = next(g for g in range(parquet.num_row_groups)
                         if boundaries[g] <= position < boundaries[g + 1])
            by_group[group].append((position - boundaries[group], item))
        for group, chosen in sorted(by_group.items()):
            table = parquet.read_row_group(group, columns=list(EXTRA_COLUMNS))
            values = table.take(pa.array([position for position, _ in chosen], type=pa.int64())).to_pylist()
            for (_, item), value in zip(chosen, values):
                if value["id"] != item["work_id"]:
                    raise ValueError(f"Snapshot Work ID changed: {filename}:{item['source_row']}")
                location = value.get("primary_location") or {}
                result.append({
                    "work_id": item["work_id"], "language": value.get("language"),
                    "locations_count": value.get("locations_count"),
                    "landing_page_url": location.get("landing_page_url"),
                    "provenance": location.get("provenance"),
                    "location_raw_source_name": location.get("raw_source_name"),
                })
    if len(result) != len(selected):
        raise AssertionError("JGR content-audit row count changed")
    return pd.DataFrame(result, columns=[
        "work_id", "language", "locations_count", "landing_page_url",
        "provenance", "location_raw_source_name",
    ]).sort_values("work_id").reset_index(drop=True)


def _classify(metadata: pd.DataFrame, extra: pd.DataFrame,
              work_flags: pd.DataFrame, source_id: str = SOURCE_ID) -> pd.DataFrame:
    required = {"work_id", "source_id", "raw_issn_l", "doi", "raw_source_name"}
    if missing := sorted(required - set(metadata.columns)):
        raise ValueError(f"Targeted metadata lacks {missing}")
    jgr = metadata.loc[metadata.source_id.eq(source_id)].copy()
    if jgr.empty or jgr.work_id.duplicated().any():
        raise ValueError("JGR candidate Works must be present and unique")
    if not jgr.raw_issn_l.isin((PARENT_ISSN_L, ATMOSPHERES_ISSN_L)).all():
        raise ValueError("JGR source acquired an unreviewed ISSN-L preimage")
    if extra.work_id.duplicated().any() or work_flags.work_id.duplicated().any():
        raise ValueError("JGR evidence contains duplicate Work IDs")
    if not {"work_id", "provenance", "location_raw_source_name", "landing_page_url"}.issubset(extra):
        raise ValueError("JGR location evidence is incomplete")
    if not {"work_id", "work_type", "is_ar", "has_refs"}.issubset(work_flags):
        raise ValueError("JGR Work-type/reference evidence is incomplete")
    jgr = jgr.merge(extra, on="work_id", how="left", validate="one_to_one")
    jgr = jgr.merge(work_flags, on="work_id", how="left", validate="one_to_one")
    if jgr[["work_type", "is_ar", "has_refs"]].isna().any().any():
        raise ValueError("JGR Work flags are missing")
    categories = []
    for row in jgr.itertuples(index=False):
        doi = _text(row.doi).lower()
        raw_name = _text(row.raw_source_name)
        provenance = _text(row.provenance)
        location_name = _text(row.location_raw_source_name)
        host = urlparse(_text(row.landing_page_url)).netloc.lower()
        if AGU_ARTICLE_DOI.fullmatch(doi) and raw_name == PUBLISHER_SOURCE_NAME:
            categories.append("agu_atmospheres_article")
        elif (AGU_RETRACTION_DOI.fullmatch(doi)
              and raw_name == PUBLISHER_SOURCE_NAME
              and location_name == PUBLISHER_SOURCE_NAME
              and provenance == "crossref" and row.work_type == "retraction"):
            categories.append("agu_atmospheres_retraction")
        elif (not doi and provenance == "mag" and raw_name == MAG_SOURCE_NAME
              and location_name == MAG_SOURCE_NAME and row.is_ar and not row.has_refs
              and host and host not in ("doi.org", "agupubs.onlinelibrary.wiley.com")):
            categories.append("exclude_mag_nonpublisher_mirror")
        elif (doi and provenance == "datacite" and not raw_name
              and not location_name and row.is_ar and not row.has_refs
              and host == "doi.org"
              and not doi.startswith(("https://doi.org/10.1002/", "https://doi.org/10.1029/"))):
            categories.append("exclude_datacite_repository_record")
        else:
            raise ValueError(f"Unreviewed JGR content: {row.work_id}, {doi}, {raw_name}, {provenance}")
    jgr["review_category"] = categories
    return jgr


def build_ledger(metadata: pd.DataFrame, extra: pd.DataFrame,
                 work_flags: pd.DataFrame,
                 source_id: str = SOURCE_ID) -> pd.DataFrame:
    """Return changed publisher Works to project onto the section ISSN-L."""
    reviewed = _classify(metadata, extra, work_flags, source_id)
    accepted = reviewed.loc[reviewed.review_category.str.startswith("agu_")]
    changed = accepted.loc[accepted.raw_issn_l.ne(ATMOSPHERES_ISSN_L)]
    return pd.DataFrame({
        "work_id": changed.work_id,
        "expected_source_id": changed.source_id,
        "expected_raw_issn_l": changed.raw_issn_l,
        "target_source_id": changed.source_id,
        "target_raw_issn_l": ATMOSPHERES_ISSN_L,
        "reason": changed.review_category,
    }, columns=list(LEDGER_COLUMNS)).sort_values("work_id").reset_index(drop=True)


def build_exclusions(metadata: pd.DataFrame, extra: pd.DataFrame,
                     work_flags: pd.DataFrame,
                     source_id: str = SOURCE_ID) -> pd.DataFrame:
    """Return nonpublisher mirrors/repository records to remove from graph input."""
    reviewed = _classify(metadata, extra, work_flags, source_id)
    rejected = reviewed.loc[reviewed.review_category.str.startswith("exclude_")]
    return pd.DataFrame({
        "work_id": rejected.work_id,
        "expected_source_id": rejected.source_id,
        "expected_raw_issn_l": rejected.raw_issn_l,
        "reason": rejected.review_category,
    }, columns=list(EXCLUSION_COLUMNS)).sort_values("work_id").reset_index(drop=True)


def repair_latest_sources(latest: pd.DataFrame,
                          source_id: str = SOURCE_ID) -> pd.DataFrame:
    """Separate the section's display identity from former parent JGR aliases."""
    required = {"source_id", "raw_issn_l", "source_type", "title", "publisher", "issns"}
    if missing := sorted(required - set(latest.columns)):
        raise ValueError(f"Latest Sources lacks {missing}")
    rows = latest.loc[latest.source_id.eq(source_id)]
    if len(rows) != 1:
        raise ValueError("Expected one reviewed JGR Atmospheres source row")
    source = rows.iloc[0]
    if (source.source_type != "journal" or source.publisher != "American Geophysical Union"
            or source.raw_issn_l not in (PARENT_ISSN_L, ATMOSPHERES_ISSN_L)
            or not set(ATMOSPHERES_ISSNS).issubset(set(source.issns))
            or not set(source.issns).issubset(set((*ATMOSPHERES_ISSNS, PARENT_ISSN_L, "2156-2202")))
            or source.title not in (MAG_SOURCE_NAME, PUBLISHER_SOURCE_NAME)):
        raise ValueError("JGR source metadata changed; review its identity before scoring")
    corrected = latest.copy()
    idx = rows.index[0]
    corrected.at[idx, "raw_issn_l"] = ATMOSPHERES_ISSN_L
    corrected.at[idx, "issns"] = ATMOSPHERES_ISSNS
    corrected.at[idx, "title"] = PUBLISHER_SOURCE_NAME
    return corrected


def review(run: Path = RUN, source_id: str = SOURCE_ID) -> dict:
    """Build hash-bound local JGR evidence and correction ledgers for a dated run."""
    run = run.resolve()
    root = run / "intermediates" / "source-repairs"
    paths = {
        "content_audit": root / "jgr_content_audit.parquet",
        "ledger": root / "jgr_rekey_ledger.parquet",
        "exclusions": root / "jgr_exclusions.parquet",
    }
    report_path = root / "jgr_review.json"
    if any(path.exists() for path in (*paths.values(), report_path)):
        raise FileExistsError("Preserve existing JGR review outputs; inspect their receipts")
    metadata_path = root / "targeted_work_metadata.parquet"
    receipt_path = root / "targeted_work_metadata.receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (receipt.get("sha256") != _hash_file(metadata_path)
            or source_id not in receipt.get("source_ids", [])):
        raise ValueError("JGR target metadata lacks a valid September receipt")
    metadata = pq.read_table(metadata_path).to_pandas()
    extra = extract_nonstandard_locations(metadata, source_id)
    works_path = run / "intermediates" / "extraction" / "works.parquet"
    with duckdb.connect() as con:
        work_flags = con.execute("""
            SELECT work_id, work_type, is_ar, has_refs FROM read_parquet(?)
            WHERE source_id=?
        """, [str(works_path), source_id]).df()
    reviewed = _classify(metadata, extra, work_flags, source_id)
    publisher_count = int(reviewed.review_category.str.startswith("agu_").sum())
    ledger = build_ledger(metadata, extra, work_flags, source_id)
    exclusions = build_exclusions(metadata, extra, work_flags, source_id)
    if (len(reviewed) != publisher_count + len(exclusions)
            or len(ledger) > publisher_count):
        raise AssertionError("JGR review did not cover every candidate Work")
    with duckdb.connect() as con:
        latest = con.execute("SELECT * FROM read_parquet(?)", [str(
            run / "intermediates" / "sources" / "latest_sources.parquet")]).df()
    repair_latest_sources(latest, source_id)
    frames = {"content_audit": extra, "ledger": ledger, "exclusions": exclusions}
    for name, path in paths.items():
        partial = path.with_suffix(path.suffix + ".partial")
        if partial.exists():
            raise FileExistsError(f"Inspect incomplete JGR output: {partial}")
        pq.write_table(pa.Table.from_pandas(frames[name], preserve_index=False), partial, compression="zstd")
        os.replace(partial, path)
    report = {
        "version": "jgr-atmospheres-source-repair-v1",
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_date": receipt["snapshot_date"],
        "candidate_source_id": source_id,
        "candidate_source_parent_issn_l": PARENT_ISSN_L,
        "corrected_source_issn_l": ATMOSPHERES_ISSN_L,
        "june_atmospheres_source_id": JUNE_SOURCE_ID,
        "june_to_september_crosswalk": {JUNE_SOURCE_ID: source_id},
        "reviewed_works": len(reviewed),
        "publisher_works_confirmed": publisher_count,
        "publisher_works_rekeyed": len(ledger),
        "nonpublisher_works_excluded": len(exclusions),
        "exclusion_reasons": exclusions.reason.value_counts().to_dict(),
        "rekey_reasons": ledger.reason.value_counts().to_dict(),
        "publisher_source": "https://agupubs.onlinelibrary.wiley.com/journal/21698996",
        "issn_record": "https://portal.issn.org/resource/ISSN/2169-8996",
        "sampled_other_section_paper": "https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2017JF004361",
        "targeted_metadata_sha256": receipt["sha256"],
        "review_code_sha256": _hash_file(Path(__file__)),
        "inputs": {"works_sha256": receipt["works_sha256"],
                   "works_manifest_sha256": receipt["works_manifest_sha256"]},
        "outputs": {name: {"path": str(path.relative_to(run)), "rows": len(frames[name]),
                           "sha256": _hash_file(path)} for name, path in paths.items()},
        "status": "PASS",
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(review(), indent=2))

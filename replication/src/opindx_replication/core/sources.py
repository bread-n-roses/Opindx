"""Independent Sources metadata reader and audited annual identity projection.

Nothing runs on import. Reading Sources and projecting their identities are
separate calls; neither writes files. Source type, field and domain use the
canonical arm's nonmissing value, then a unanimous component fallback. A
conflicting type is explicitly ``Multiple source types`` and is not a journal.
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import unquote, urlparse

import duckdb
import pandas as pd

from identity import AnnualIdentityMaps, _project_one, _valid_issn_l


LATEST_COLUMNS = (
    "source_id", "raw_issn_l", "source_type", "title", "publisher",
    "issns", "oa_field", "oa_domain", "is_open_access", "updated_date",
)
DISPLAY_COLUMNS = ("source_type", "oa_field", "oa_domain", "title", "publisher", "is_open_access")
CONFLICT_LABELS = {
    "source_type": "Multiple source types",
    "oa_field": "Multiple OA fields",
    "oa_domain": "Multiple OA domains",
}
EFFECTIVE_COLUMNS = (
    "issn_l", "source_type", "openalex_id", "openalex_url", "title", "journal_title", "publisher", "issns",
    "oa_domain", "oa_field", "is_open_access", "updated_date", "source_ids", "source_rows",
    "raw_issn_l_arms", "canonical_arm_rows", "openalex_id_resolution",
    *(f"{column}_resolution" for column in DISPLAY_COLUMNS),
)


def source_paths_from_manifest(
    manifest_path: str | Path,
    sources_root: str | Path,
    *,
    snapshot_date: str,
) -> tuple[Path, ...]:
    """Resolve exactly the Sources manifest entries without reading Parquet data."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("entity") != "sources"
            or manifest.get("format") != "parquet" or manifest.get("date") != snapshot_date):
        raise ValueError("Sources manifest entity, format or snapshot date disagrees")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Sources manifest must contain files")
    root = Path(sources_root).resolve()
    paths: set[Path] = set()
    total_bytes = total_rows = 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
            raise ValueError("Malformed Sources manifest entry")
        url_path = unquote(urlparse(entry["url"]).path)
        if "/sources/" not in url_path:
            raise ValueError("Sources manifest entry lacks a /sources/ path")
        relative = url_path.split("/sources/", 1)[1]
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or path.suffix != ".parquet" or path in paths:
            raise ValueError(f"Invalid or repeated Sources manifest path: {relative}")
        meta = entry.get("meta", {})
        if not isinstance(meta, dict):
            raise ValueError("Sources manifest entry metadata must be an object")
        values = (meta.get("content_length"), meta.get("record_count"))
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("Sources manifest entry has invalid byte/row counts")
        total_bytes += values[0]
        total_rows += values[1]
        paths.add(path)
    if total_bytes != manifest.get("content_length") or total_rows != manifest.get("record_count"):
        raise ValueError("Sources manifest totals disagree with its file entries")
    return tuple(sorted(paths))


def _optional(columns: set[str], candidates: tuple[str, ...], cast: str) -> str:
    available = [f'CAST("{column}" AS {cast})' for column in candidates if column in columns]
    return f"coalesce({', '.join(available)})" if len(available) > 1 else (
        available[0] if available else f"CAST(NULL AS {cast})"
    )


def _text(value: object) -> str | None:
    if pd.isna(value):
        return None
    result = str(value).strip()
    return result or None


def _topic_labels(value: str | None) -> tuple[str | None, str | None]:
    value = _text(value)
    topics = json.loads(value) if value is not None else None
    if topics is None or topics == []:
        return None, None
    if not isinstance(topics, list) or not isinstance(topics[0], dict):
        raise ValueError("Sources topics must be an array of topic objects")
    first = topics[0]
    labels = []
    for attribute in ("field", "domain"):
        node = first.get(attribute)
        if node is not None and not isinstance(node, dict):
            raise ValueError(f"Source topic {attribute} must be an object")
        labels.append(_text(node.get("display_name")) if node else None)
    return labels[0], labels[1]


def _issns(value: str | None) -> tuple[str, ...]:
    value = _text(value)
    values = json.loads(value) if value is not None else None
    if values is None:
        return ()
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ValueError("Sources ISSNs must be an array of strings")
    return tuple(sorted({item.strip().upper() for item in values if item.strip()}))


def read_latest_sources(
    parquet_paths: str | Path | Sequence[str | Path],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read latest-by-ID Sources rows with DuckDB; reject conflicting latest ties.

    A path may be a Parquet file or glob; a sequence can come directly from the
    dated manifest. Paths are query parameters, never SQL fragments. The first
    listed source topic supplies field/domain, matching the existing contract.
    Optional publisher, ISSN-array and open-access metadata may remain missing.
    """
    paths = [str(parquet_paths)] if isinstance(parquet_paths, (str, Path)) else [str(path) for path in parquet_paths]
    if not paths or any(not path for path in paths) or len(set(paths)) != len(paths):
        raise ValueError("Sources paths must be nonempty and unique")
    with duckdb.connect() as con:
        description = con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?, union_by_name=true, hive_partitioning=false)", [paths]
        ).fetchall()
        columns = {row[0] for row in description}
        required = {"id", "issn_l", "type", "display_name", "topics", "updated_date"}
        if missing := sorted(required - columns):
            raise ValueError(f"Sources Parquet lacks required columns: {missing}")
        publisher = _optional(columns, ("host_organization_name", "publisher"), "VARCHAR")
        issn_column = next((name for name in ("issn", "issns") if name in columns), None)
        issn_json = f'to_json("{issn_column}")' if issn_column else "CAST(NULL AS VARCHAR)"
        open_access = _optional(columns, ("is_oa", "is_open_access"), "BOOLEAN")
        con.execute(f"""
            CREATE TEMP TABLE source_rows AS
            SELECT CAST(id AS VARCHAR) AS source_id,
                   CAST(issn_l AS VARCHAR) AS raw_issn_l,
                   CAST(type AS VARCHAR) AS source_type,
                   CAST(display_name AS VARCHAR) AS title,
                   {publisher} AS publisher, {issn_json} AS issns_json,
                   to_json(topics) AS topics_json, {open_access} AS is_open_access,
                   try_cast(updated_date AS TIMESTAMP) AS updated_date
            FROM read_parquet(?, union_by_name=true, hive_partitioning=false)
        """, [paths])
        invalid = con.execute("""
            SELECT count(*) FROM source_rows
            WHERE source_id IS NULL OR trim(source_id) = '' OR updated_date IS NULL
        """).fetchone()[0]
        if invalid:
            raise ValueError(f"Sources contains {invalid} rows with missing source ID or invalid updated_date")
        input_rows = con.execute("SELECT count(*) FROM source_rows").fetchone()[0]
        latest = con.execute("""
            SELECT * FROM source_rows
            QUALIFY dense_rank() OVER (PARTITION BY source_id ORDER BY updated_date DESC) = 1
            ORDER BY source_id
        """).df()
    latest_tie_rows = len(latest)
    latest = latest.drop_duplicates().reset_index(drop=True)
    if latest.source_id.duplicated().any():
        conflicts = latest.loc[latest.source_id.duplicated(keep=False), "source_id"].unique().tolist()
        raise ValueError(f"Conflicting latest Sources rows share source ID and updated_date: {conflicts[:10]}")
    for column in ("source_id", "raw_issn_l", "source_type", "title", "publisher"):
        latest[column] = latest[column].map(_text)
    if latest.source_id.duplicated().any():
        raise ValueError("Sources IDs collide after whitespace normalization")
    labels = [_topic_labels(value) for value in latest.pop("topics_json")]
    latest["oa_field"] = [value[0] for value in labels]
    latest["oa_domain"] = [value[1] for value in labels]
    latest["issns"] = latest.pop("issns_json").map(_issns)
    latest["is_open_access"] = latest["is_open_access"].astype("boolean")
    return latest.loc[:, list(LATEST_COLUMNS)], {
        "input_rows": int(input_rows),
        "latest_source_rows": len(latest),
        "older_rows_discarded": int(input_rows - latest_tie_rows),
        "identical_latest_duplicates_collapsed": latest_tie_rows - len(latest),
        "latest_source_id_unique": True,
        "optional_columns_missing": [name for name, candidates in (
            ("publisher", ("host_organization_name", "publisher")),
            ("issns", ("issn", "issns")),
            ("is_open_access", ("is_oa", "is_open_access")),
        ) if not any(candidate in columns for candidate in candidates)],
    }


def read_manifest_sources(
    manifest_path: str | Path,
    sources_root: str | Path,
    *,
    snapshot_date: str,
    expected_manifest_sha256: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read only hash-bound manifest files after validating every listed size.

    The expected hash comes from the dated run record. File sizes and mtimes are
    checked again after reading; this is a metadata inventory, not a content
    hash of each Parquet file. The generic reader is intended for fixtures or
    already-bound inputs; this is the dated-run entry point.
    """
    manifest_path = Path(manifest_path)
    payload = manifest_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_manifest_sha256:
        raise ValueError("Sources manifest hash differs from the dated run record")
    paths = source_paths_from_manifest(manifest_path, sources_root, snapshot_date=snapshot_date)
    manifest = json.loads(payload)
    root = Path(sources_root).resolve()
    expected = {
        (root / unquote(urlparse(entry["url"]).path).split("/sources/", 1)[1]).resolve(): entry["meta"]["content_length"]
        for entry in manifest["files"]
    }
    inventory = []
    for path in paths:
        stat = path.stat()
        if stat.st_size != expected[path]:
            raise ValueError(f"Sources file size differs from manifest: {path.relative_to(root)}")
        inventory.append({"path": path.as_posix(), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    latest, audit = read_latest_sources(paths)
    if audit["input_rows"] != manifest["record_count"]:
        raise RuntimeError("Sources row count differs from the bound manifest")
    for path, before in zip(paths, inventory):
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (before["bytes"], before["mtime_ns"]):
            raise RuntimeError(f"Sources file changed while reading: {path}")
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Sources manifest changed while reading")
    audit.update({
        "snapshot_date": snapshot_date,
        "manifest_sha256": digest,
        "manifest_file_count": len(paths),
        "file_inventory_sha256": hashlib.sha256(json.dumps(inventory, sort_keys=True).encode("utf-8")).hexdigest(),
        "verification_scope": "Manifest hash and listed file sizes/mtimes; no Parquet content hashes",
    })
    return latest, audit


def _attribute(group: pd.DataFrame, column: str) -> tuple[object, str]:
    canonical = set(group.loc[group.is_canonical_arm, column].dropna())
    conflict = CONFLICT_LABELS.get(column)
    missing = "Unknown" if column in CONFLICT_LABELS else None
    if len(canonical) == 1:
        return canonical.pop(), "canonical_arm"
    if len(canonical) > 1:
        return conflict, "canonical_arm_conflict"
    component = set(group[column].dropna())
    if len(component) == 1:
        return component.pop(), "unanimous_component_fallback"
    if len(component) > 1:
        return conflict, "component_conflict"
    return missing, "missing"


def project_sources(
    score_year: int,
    latest: pd.DataFrame,
    maps: AnnualIdentityMaps,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Return effective metadata, per-source provenance, and a collision audit.

    All source IDs, including missing/invalid ISSN-Ls, reach the approved source
    override before quarantine. The cascade and checksum check are shared with
    annual endpoint projection. Unresolved display conflicts stay missing and
    are labelled; no arbitrary source ID, title, publisher or OA flag is chosen.
    """
    maps.validate(score_year)
    if missing := sorted(set(LATEST_COLUMNS) - set(latest.columns)):
        raise ValueError(f"Latest source metadata lacks columns: {missing}")
    keyed = latest.copy(deep=True)
    keyed["score_year"] = score_year
    for column in ("source_id", "raw_issn_l", "source_type", "title", "publisher", "oa_field", "oa_domain"):
        keyed[column] = keyed[column].map(_text)
    if keyed.source_id.isna().any() or keyed.source_id.duplicated().any():
        raise ValueError("Latest Sources metadata must have unique nonmissing source IDs")
    if absent := sorted(set(maps.source_id_override) - set(keyed.source_id)):
        raise ValueError(f"Approved source-ID overrides are absent from Sources: {absent[:10]}")
    keyed["source_type"] = keyed["source_type"].map(
        lambda value: _text(value).casefold() if _text(value) is not None else None
    )
    keyed["issn_l"] = [_project_one(source, raw, maps) for source, raw in zip(keyed.source_id, keyed.raw_issn_l)]
    keyed["corrected_source_issn_l"] = [
        maps.source_id_override[source].target_issn_l if source in maps.source_id_override
        else (_text(raw) or "").upper()
        for source, raw in zip(keyed.source_id, keyed.raw_issn_l)
    ]
    keyed["is_canonical_arm"] = keyed.corrected_source_issn_l.eq(keyed.issn_l)
    valid = keyed.issn_l.map(_valid_issn_l)
    keyed["quarantine_reason"] = keyed.issn_l.map(
        lambda key: "" if _valid_issn_l(key) else ("missing_issn_l" if not key else "invalid_issn_l")
    )
    rows: list[dict[str, Any]] = []
    collisions: list[dict[str, Any]] = []
    for key, group in keyed.loc[valid].groupby("issn_l", sort=True):
        row: dict[str, Any] = {
            "issn_l": key,
            "source_ids": tuple(sorted(group.source_id)),
            "source_rows": len(group),
            "raw_issn_l_arms": group.corrected_source_issn_l.nunique(),
            "canonical_arm_rows": int(group.is_canonical_arm.sum()),
            "issns": tuple(sorted({key} | {value for items in group.issns for value in items})),
        }
        selected_source, row["openalex_id_resolution"] = _attribute(group, "source_id")
        canonical = group.loc[group.is_canonical_arm]
        row["updated_date"] = canonical.iloc[0].updated_date if len(canonical) == 1 else pd.NaT
        matched_id = re.fullmatch(r"(?:https?://openalex\.org/)?(S[0-9]+)", selected_source or "")
        row["openalex_id"] = matched_id.group(1) if matched_id else None
        row["openalex_url"] = f"https://openalex.org/{row['openalex_id']}" if matched_id else None
        for column in DISPLAY_COLUMNS:
            row[column], row[f"{column}_resolution"] = _attribute(group, column)
        row["journal_title"] = row["title"]
        rows.append(row)
        if len(group) > 1:
            collisions.append({
                "issn_l": key, "source_ids": list(row["source_ids"]),
                "canonical_arm_rows": row["canonical_arm_rows"],
                "attribute_resolutions": {column: row[f"{column}_resolution"] for column in DISPLAY_COLUMNS},
                "source_types_observed": sorted(group.source_type.dropna().unique()),
                "source_type_selected": row["source_type"],
                "openalex_id_resolution": row["openalex_id_resolution"],
            })
    effective = pd.DataFrame(rows, columns=EFFECTIVE_COLUMNS)
    effective["is_open_access"] = effective["is_open_access"].astype("boolean")
    if int(effective.source_rows.sum()) + int((~valid).sum()) != len(latest):
        raise AssertionError("Source identity projection lost rows")
    return effective, keyed, {
        "score_year": score_year,
        "input_latest_rows": len(latest),
        "operational_source_rows": int(valid.sum()),
        "quarantined_source_rows": int((~valid).sum()),
        "effective_source_nodes": len(effective),
        "oa_journal_nodes": int(effective.source_type.eq("journal").sum()),
        "multiple_source_type_nodes": int(effective.source_type.eq("Multiple source types").sum()),
        "missing_canonical_arm_nodes": int(effective.canonical_arm_rows.eq(0).sum()),
        "multiple_canonical_arm_nodes": int(effective.canonical_arm_rows.gt(1).sum()),
        "journal_nodes_missing_unique_canonical_source": int((
            effective.source_type.eq("journal")
            & (effective.canonical_arm_rows.ne(1) | effective.openalex_id.isna())
        ).sum()),
        "identity_collisions": collisions,
        "row_accounting_pass": True,
    }


def journal_export_view(effective: pd.DataFrame) -> pd.DataFrame:
    """Return journal metadata only when each journal has one canonical source.

    Source-type projection can be useful for scoring even when display identity
    needs review. Publication requires exactly one canonical source row and a
    unique valid bare OpenAlex ID; a fallback or an arbitrary representative ID
    cannot silently become the public journal link.
    """
    required = {"issn_l", "source_type", "canonical_arm_rows", "openalex_id", "journal_title"}
    if missing := sorted(required - set(effective.columns)):
        raise ValueError(f"Effective Sources lack export guard columns: {missing}")
    journals = effective.loc[effective.source_type.eq("journal")].copy()
    valid_id = journals.openalex_id.map(lambda value: bool(re.fullmatch(r"S[0-9]+", _text(value) or "")))
    ambiguous = journals.canonical_arm_rows.ne(1) | ~valid_id
    if ambiguous.any():
        raise ValueError(
            "Journal export requires exactly one canonical source row and a valid OpenAlex ID: "
            f"{journals.loc[ambiguous, 'issn_l'].tolist()[:10]}"
        )
    if journals.issn_l.isna().any() or journals.issn_l.duplicated().any() or journals.openalex_id.duplicated().any():
        raise ValueError("Journal export requires unique effective ISSN-L and canonical OpenAlex IDs")
    return journals.reset_index(drop=True)

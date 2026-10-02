"""Persist hash-bound latest Sources metadata before annual identity review.

Fresh execution reads only manifest-listed Sources through sources.py. Resume
checks the bound inventory and local cache without reading raw Sources rows.
No identity approval, Works read, scoring, or publication is performed here.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import duckdb
import pandas as pd

import sources


RUN = Path(__file__).resolve().parents[1]
VERSION = "website-sources-checkpoint-v1"
REVIEW_GATES = (
    "Approve identity maps separately for each score year before projecting Sources or Works.",
    "Review invalid identities, source-type conflicts and unresolved canonical source IDs.",
    "Review ambiguous/unresolved annual Norwegian matches before constructing final rosters.",
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _digest(value: Any) -> str:
    # Match sources.read_manifest_sources' file-inventory serialization.
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _partial(path: Path) -> Path:
    return path.with_name(path.name + ".partial")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = _partial(path)
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _binding(run: Path) -> tuple[dict[str, Any], Path, Path, Path]:
    config_path = run / "config" / "run.json"
    config = _json(config_path)
    record = _json(run / "run-record.json")
    expected = record["snapshot"]["entities"]["sources"]
    copied = run / "snapshot-manifests" / "sources-manifest.json"
    root = (Path(config["raw_snapshot_root"]) / "sources").resolve()
    raw_manifest = root / "manifest.json"
    digest = _sha256(copied)
    if digest != expected["manifest_sha256"] or _sha256(raw_manifest) != digest:
        raise ValueError("Copied/raw Sources manifest hashes differ from the dated run record")
    manifest = _json(copied)
    if (manifest.get("date") != config["snapshot_date"]
            or len(manifest.get("files", [])) != expected["files"]
            or manifest.get("content_length") != expected["bytes"]):
        raise ValueError("Sources manifest vintage, count or byte total differs from the run record")
    paths = sources.source_paths_from_manifest(copied, root, snapshot_date=config["snapshot_date"])
    sizes = {(root / unquote(urlparse(item["url"]).path).split("/sources/", 1)[1]).resolve():
             item["meta"]["content_length"] for item in manifest["files"]}
    inventory = []
    for path in paths:
        stat = path.stat()
        if stat.st_size != sizes[path]:
            raise ValueError(f"Sources file size differs from manifest: {path.name}")
        inventory.append({"path": path.as_posix(), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    relative = config.get("paths_relative_to_run_root", {}).get("intermediates", "intermediates")
    output = (run / relative / "sources").resolve()
    if not output.is_relative_to(run) or output == run:
        raise ValueError("Sources checkpoint must stay within the dated run")
    contract = {
        "version": VERSION, "snapshot_date": config["snapshot_date"],
        "config_sha256": _sha256(config_path), "manifest_sha256": digest,
        "file_inventory": inventory, "file_inventory_sha256": _digest(inventory),
        "checkpoint_code_sha256": _sha256(Path(__file__)),
        "sources_reader_sha256": _sha256(Path(sources.__file__)),
        "duckdb_version": duckdb.__version__, "pandas_version": pd.__version__,
        "verification_scope": "Manifest hashes and raw file sizes/mtimes; local cache content SHA256",
    }
    return contract, copied, root, output


def _issn_tuple(value: object) -> tuple[str, ...]:
    if pd.api.types.is_scalar(value):
        if pd.isna(value):
            return ()
        raise ValueError("Cached Sources ISSNs must be arrays, not scalar text")
    if isinstance(value, Mapping):
        raise ValueError("Cached Sources ISSNs must be arrays, not mappings")
    values = tuple(value)
    if any(not isinstance(item, str) for item in values):
        raise ValueError("Cached Sources ISSNs must contain only strings")
    return tuple(sorted({item.strip().upper() for item in values if item.strip()}))


def normalize_latest(frame: pd.DataFrame) -> pd.DataFrame:
    """Restore the same latest-source contract after a Parquet list reload."""
    if set(frame.columns) != set(sources.LATEST_COLUMNS):
        raise ValueError("Latest Sources cache has an unexpected column schema")
    normalized = frame.loc[:, list(sources.LATEST_COLUMNS)].copy()
    for column in ("source_id", "raw_issn_l", "source_type", "title", "publisher", "oa_field", "oa_domain"):
        normalized[column] = normalized[column].map(
            lambda value: None if pd.isna(value) else str(value).strip() or None)
    if normalized.source_id.isna().any() or normalized.source_id.duplicated().any():
        raise ValueError("Latest Sources cache has missing or duplicate source IDs")
    normalized["issns"] = normalized.issns.map(_issn_tuple)
    normalized["is_open_access"] = normalized.is_open_access.astype("boolean")
    normalized["updated_date"] = pd.to_datetime(normalized.updated_date, errors="raise").astype("datetime64[ns]")
    if normalized.updated_date.isna().any():
        raise ValueError("Latest Sources cache has a missing update timestamp")
    return normalized.sort_values("source_id").reset_index(drop=True)


def _read_cache(path: Path) -> pd.DataFrame:
    with duckdb.connect() as con:
        frame = con.execute("SELECT * FROM read_parquet(?, hive_partitioning=false)", [str(path)]).df()
    return normalize_latest(frame)


def checkpoint_sources(run: Path = RUN) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build or safely reuse the local latest-source cache; retain review gates."""
    run = run.resolve()
    contract, manifest, root, output = _binding(run)
    output.mkdir(parents=True, exist_ok=True)
    if partials := sorted(output.rglob("*.partial")):
        raise RuntimeError(f"Incomplete Sources checkpoint files require inspection: {partials}")
    lock = output / "source_checkpoint.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    try:
        cache = output / "latest_sources.parquet"
        receipt_path = output / "latest_sources.receipt.json"
        contract_path = output / "contract.json"
        if contract_path.exists():
            if _json(contract_path) != contract:
                raise RuntimeError("Sources checkpoint contract changed; refusing reuse")
        else:
            if cache.exists() or receipt_path.exists():
                raise RuntimeError("Sources cache exists without its bound contract")
            _atomic_json(contract_path, contract)
        contract_digest = _digest(contract)
        if cache.exists() != receipt_path.exists():
            raise RuntimeError("Orphan Sources cache or receipt; refusing reuse")
        reused = cache.exists()
        if reused:
            receipt = _json(receipt_path)
            if (receipt.get("contract_sha256") != contract_digest
                    or receipt.get("bytes") != cache.stat().st_size
                    or receipt.get("sha256") != _sha256(cache)):
                raise RuntimeError("Sources cache does not match its hash receipt")
            latest = _read_cache(cache)
            if len(latest) != receipt.get("rows"):
                raise RuntimeError("Sources cache row count differs from its receipt")
        else:
            latest, audit = sources.read_manifest_sources(
                manifest, root, snapshot_date=contract["snapshot_date"],
                expected_manifest_sha256=contract["manifest_sha256"])
            latest = normalize_latest(latest)
            if (audit["file_inventory_sha256"] != contract["file_inventory_sha256"]
                    or len(latest) != audit["latest_source_rows"]):
                raise RuntimeError("Sources reader audit differs from the checkpoint binding")
            with duckdb.connect() as con:
                con.register("latest_sources", latest)
                text_columns = ("source_id", "raw_issn_l", "source_type", "title", "publisher", "oa_field", "oa_domain")
                casts = [f'CAST("{column}" AS VARCHAR) AS "{column}"' for column in text_columns]
                casts.extend(("CAST(issns AS VARCHAR[]) AS issns",
                              "CAST(is_open_access AS BOOLEAN) AS is_open_access",
                              "CAST(updated_date AS TIMESTAMP) AS updated_date"))
                con.execute(f"COPY (SELECT {', '.join(casts)} FROM latest_sources) TO ? "
                            "(FORMAT PARQUET, COMPRESSION ZSTD)", [str(_partial(cache))])
            restored = _read_cache(_partial(cache))
            pd.testing.assert_frame_equal(latest, restored)
            if _binding(run)[0] != contract:
                raise RuntimeError("Sources input/config/code changed during checkpoint creation")
            os.replace(_partial(cache), cache)
            receipt = {
                "contract_sha256": contract_digest, "sha256": _sha256(cache),
                "bytes": cache.stat().st_size, "rows": len(latest),
                "created_at_utc": datetime.now(timezone.utc).isoformat(), "reader_audit": audit,
                "identity_projection_performed": False, "scores_computed": False,
                "review_gates": list(REVIEW_GATES),
            }
            _atomic_json(receipt_path, receipt)
        if _binding(run)[0] != contract:
            raise RuntimeError("Sources input/config/code changed during checkpoint access")
        return latest, {**receipt, "status": "LATEST_METADATA_READY", "reused": reused,
                        "cache_path": str(cache), "raw_sources_scans_this_invocation": int(not reused)}
    finally:
        lock.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--execute", action="store_true", help="Read Sources or validate the completed local cache")
    args = parser.parse_args()
    if not args.execute:
        parser.error("Explicit --execute is required to create or reload Sources checkpoints")
    _, report = checkpoint_sources(args.run)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

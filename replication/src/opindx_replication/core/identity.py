"""Project annual raw citation inputs through an explicitly reviewed identity map.

This contains no implicit carry-forward: the caller must supply maps approved
for the requested score year. The same cascade is applied to article counts
and to both endpoints of every citation edge before aggregation. Self rows are
retained here and removed only by the scorer after universe restriction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re

import pandas as pd


ISSN_L = re.compile(r"^[0-9]{4}-[0-9]{3}[0-9X]$")


def _valid_issn_l(value: str) -> bool:
    if not ISSN_L.fullmatch(value):
        return False
    digits = value.replace("-", "")
    checksum = sum(int(digit) * weight for digit, weight in zip(digits[:7], range(8, 1, -1)))
    checksum += 10 if digits[-1] == "X" else int(digits[-1])
    return checksum % 11 == 0


@dataclass(frozen=True)
class SourceKeyOverride:
    """Approved correction for a closed set of observed source/raw-key pairs."""

    expected_raw_issn_ls: tuple[str, ...]
    target_issn_l: str


@dataclass(frozen=True)
class AnnualIdentityMaps:
    score_year: int
    source_id_override: Mapping[str, SourceKeyOverride]
    alias_to_canonical: Mapping[str, str]
    key_correction: Mapping[str, str]
    review_status: str

    def validate(self, score_year: int) -> None:
        if self.score_year != score_year or self.review_status != "approved_for_score_year":
            raise ValueError(f"{score_year}: reviewed annual identity maps are required")
        for source, override in self.source_id_override.items():
            if (not isinstance(source, str) or not source.strip()
                    or not isinstance(override, SourceKeyOverride)
                    or not isinstance(override.expected_raw_issn_ls, tuple)
                    or not override.expected_raw_issn_ls
                    or any(not isinstance(raw, str) or raw != raw.strip().upper()
                           for raw in override.expected_raw_issn_ls)
                    or len(set(override.expected_raw_issn_ls)) != len(override.expected_raw_issn_ls)
                    or not isinstance(override.target_issn_l, str)
                    or not _valid_issn_l(override.target_issn_l)):
                raise ValueError("source_id_override requires source, reviewed raw preimages, and valid target")
        for label, mapping in (
            ("alias_to_canonical", self.alias_to_canonical),
            ("key_correction", self.key_correction),
        ):
            if any(not isinstance(key, str) or not isinstance(value, str) for key, value in mapping.items()):
                raise ValueError(f"{label}: keys and targets must be strings")
            if any(not key.strip() for key in mapping):
                raise ValueError(f"{label}: blank generic keys are forbidden; use a source-specific override")
            if any(mapping.get(value, value) != value for value in mapping.values()):
                raise ValueError(f"{label}: mapping must be one-step and idempotent")


def _project_one(source_id: object, raw_issn_l: object, maps: AnnualIdentityMaps) -> str:
    source = "" if pd.isna(source_id) else str(source_id)
    raw = "" if pd.isna(raw_issn_l) else str(raw_issn_l).strip().upper()
    override = maps.source_id_override.get(source)
    if override is not None and raw not in override.expected_raw_issn_ls:
        raise ValueError(f"Source-ID override preimage changed for {source}: {raw!r}")
    from_source = override.target_issn_l if override is not None else raw
    from_alias = maps.alias_to_canonical.get(from_source, from_source)
    return maps.key_correction.get(from_alias, from_alias)


def project_endpoints(
    score_year: int,
    raw_counts: pd.DataFrame,
    raw_edges: pd.DataFrame,
    maps: AnnualIdentityMaps,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Return effective counts/edges and a conservation/quarantine report.

    Bad or unresolved ISSN-L keys are quarantined after all approved fixes.
    Any positive article or citation weight in quarantine stops projection;
    score normalization must never hide lost mass.
    """
    maps.validate(score_year)
    count_columns = {"source_id", "raw_issn_l", "a_raw", "a_filtered"}
    edge_columns = {
        "citing_source_id", "raw_citing_issn_l", "cited_source_id",
        "raw_cited_issn_l", "n_raw", "n_filtered",
    }
    if not count_columns.issubset(raw_counts.columns):
        raise ValueError("Raw counts lack required source/key/arm columns")
    if not edge_columns.issubset(raw_edges.columns):
        raise ValueError("Raw edges lack required source/key/arm columns")
    input_counts = raw_counts.copy()
    input_edges = raw_edges.copy()
    for frame, raw, filtered in (
        (input_counts, "a_raw", "a_filtered"),
        (input_edges, "n_raw", "n_filtered"),
    ):
        for column in (raw, filtered):
            values = pd.to_numeric(frame[column], errors="raise")
            if values.isna().any() or values.lt(0).any() or values.mod(1).ne(0).any():
                raise ValueError(f"{column} must contain nonnegative integers")
            frame[column] = values.astype("int64")
        if frame[filtered].gt(frame[raw]).any():
            raise ValueError(f"{filtered} exceeds {raw}")

    counts = input_counts.copy()
    edges = input_edges.copy()
    counts["issn_l"] = [
        _project_one(source, key, maps)
        for source, key in zip(counts.source_id, counts.raw_issn_l)
    ]
    edges["citing_issn_l"] = [
        _project_one(source, key, maps)
        for source, key in zip(edges.citing_source_id, edges.raw_citing_issn_l)
    ]
    edges["cited_issn_l"] = [
        _project_one(source, key, maps)
        for source, key in zip(edges.cited_source_id, edges.raw_cited_issn_l)
    ]
    count_valid = counts.issn_l.map(_valid_issn_l)
    edge_valid = edges.citing_issn_l.map(_valid_issn_l) & edges.cited_issn_l.map(_valid_issn_l)
    rejected_counts = counts.loc[~count_valid]
    rejected_edges = edges.loc[~edge_valid]
    counts = counts.loc[count_valid].groupby("issn_l", as_index=False, sort=True)[["a_raw", "a_filtered"]].sum()
    edges = edges.loc[edge_valid].groupby(
        ["citing_issn_l", "cited_issn_l"], as_index=False, sort=True
    )[["n_raw", "n_filtered"]].sum()
    report = {
        "score_year": score_year,
        "input_count_rows": len(raw_counts),
        "input_edge_rows": len(raw_edges),
        "effective_count_rows": len(counts),
        "effective_edge_rows": len(edges),
        "quarantined_count_rows": len(rejected_counts),
        "quarantined_edge_rows": len(rejected_edges),
        "quarantined_a_raw": int(rejected_counts.a_raw.sum()),
        "quarantined_a_filtered": int(rejected_counts.a_filtered.sum()),
        "quarantined_n_raw": int(rejected_edges.n_raw.sum()),
        "quarantined_n_filtered": int(rejected_edges.n_filtered.sum()),
        "effective_self_n_raw": int(edges.loc[edges.citing_issn_l.eq(edges.cited_issn_l), "n_raw"].sum()),
    }
    for raw, filtered, original, projected, quarantined in (
        ("a_raw", "a_filtered", input_counts, counts, rejected_counts),
        ("n_raw", "n_filtered", input_edges, edges, rejected_edges),
    ):
        for column in (raw, filtered):
            if int(original[column].sum()) != int(projected[column].sum()) + int(quarantined[column].sum()):
                raise AssertionError(f"{column}: identity projection did not conserve mass")
    if any(report[key] for key in (
        "quarantined_a_raw", "quarantined_a_filtered",
        "quarantined_n_raw", "quarantined_n_filtered",
    )):
        raise ValueError(f"Positive article or citation mass was quarantined: {report}")
    return counts, edges, report


def project_retention(
    score_year: int,
    raw_by_publication_year: pd.DataFrame,
    maps: AnnualIdentityMaps,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Project the five cited years' article counts through one annual map.

    The same source-key corrections used for endpoint totals apply to each
    publication year. This preserves the active-years field in the website
    table and lets assembly reconcile *both* Raw and Filtered totals.
    """
    maps.validate(score_year)
    required = {"source_id", "raw_issn_l", "year", "a_raw", "a_filtered"}
    if missing := sorted(required - set(raw_by_publication_year.columns)):
        raise ValueError(f"Raw annual retention lacks columns: {missing}")
    rows = raw_by_publication_year.copy()
    for column in ("year", "a_raw", "a_filtered"):
        values = pd.to_numeric(rows[column], errors="raise")
        if values.isna().any() or values.mod(1).ne(0).any() or (values < 0).any():
            raise ValueError(f"{column} must be a nonnegative integer")
        rows[column] = values.astype("int64")
    if not rows.year.between(score_year - 5, score_year - 1).all():
        raise ValueError("Annual retention has years outside the cited window")
    if rows.a_filtered.gt(rows.a_raw).any():
        raise ValueError("Filtered annual retention exceeds Raw")
    rows["issn_l"] = [
        _project_one(source, raw, maps)
        for source, raw in zip(rows.source_id, rows.raw_issn_l)
    ]
    accepted = rows.issn_l.map(_valid_issn_l)
    rejected = rows.loc[~accepted]
    report = {
        "score_year": score_year,
        "input_rows": len(rows),
        "quarantined_rows": len(rejected),
        "quarantined_a_raw": int(rejected.a_raw.sum()),
        "quarantined_a_filtered": int(rejected.a_filtered.sum()),
    }
    if report["quarantined_a_raw"] or report["quarantined_a_filtered"]:
        raise ValueError(f"Positive annual article mass was quarantined: {report}")
    projected = rows.loc[accepted].groupby(
        ["issn_l", "year"], as_index=False, sort=True
    )[["a_raw", "a_filtered"]].sum()
    for column in ("a_raw", "a_filtered"):
        if int(rows[column].sum()) != int(projected[column].sum()) + report[f"quarantined_{column}"]:
            raise AssertionError(f"{column}: annual retention projection did not conserve mass")
    report["effective_rows"] = len(projected)
    return projected, report

"""Validate the independent website CSV handoff before it is written.

The public CSV retains the June 37-column contract: one canonical OpenAlex
journal per score year, provided at least one of its eight N/OA Raw/Filtered
EF/AIS values is defined. A numerical zero is defined; missing is null.
The schema has no ``source_type`` column, so the upstream source adapter must
prove that the supplied identities are journals before calling this validator.

``annual_n_members`` must be the *independently projected* annual Norwegian
roster, keyed by ``score_year, issn_l``. It may contain identities outside this
OA-journal export and repeated Norwegian entries for one canonical identity.
The comparison below is deliberately restricted to exported journal-years.

EF sums to 100 over each full computational state, which can include N members
outside the OA-journal export. Pass the complete ``score_annual`` output as
``native_full_scores`` to check normalization; never infer it from CSV N sums.
This module reads no project data and writes no files.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd


DEFAULT_YEARS = (2022, 2023, 2024, 2025, 2026)
EXPECTED_OA_SNAPSHOT = "2026-09-23"
SCORE_COLUMNS = tuple(
    f"{metric}_{universe}_{treatment}"
    for universe in ("n", "oa")
    for treatment in ("raw", "filtered")
    for metric in ("ef", "ais")
)
EXPORT_COLUMNS = (
    "openalex_id", "openalex_url", "journal_title", "issn_l", "issns",
    "publisher", "oa_domain", "oa_field", "norwegian_area", "norwegian_field",
    "norwegian_level", "is_open_access", "score_year",
    "publication_window_start", "publication_window_end",
    "eligible_publications_raw", "eligible_publications_filtered",
    "incoming_citations_raw", "incoming_citations_filtered",
    "reference_coverage_pct", "active_publication_years_of_5",
    *SCORE_COLUMNS,
    "citation_scope", "source_metadata_updated", "oa_snapshot_version",
    "norwegian_register_snapshot", "field_assignment_scope", "sample_scope",
    "data_source", "norwegian_register_url",
)

# Input names before Opindx's tools/import_run.py renames the six profile/count
# columns and the eight EF/AIS columns. Its two in_* flags are derived there.
OPINDX_REQUIRED_COLUMNS = (
    "openalex_id", "journal_title", "publisher", "issn_l", "issns",
    "oa_domain", "oa_field", "norwegian_area", "norwegian_field",
    "norwegian_level", "norwegian_register_url", "is_open_access",
    "score_year", "eligible_publications_raw", "eligible_publications_filtered",
    "incoming_citations_raw", "incoming_citations_filtered",
    "reference_coverage_pct", "active_publication_years_of_5",
    *SCORE_COLUMNS, "oa_snapshot_version", "norwegian_register_snapshot",
)

assert len(EXPORT_COLUMNS) == len(set(EXPORT_COLUMNS)) == 37
assert len(OPINDX_REQUIRED_COLUMNS) == 29
assert set(OPINDX_REQUIRED_COLUMNS) <= set(EXPORT_COLUMNS)


def _numeric(frame: pd.DataFrame, column: str, *, nullable: bool = False) -> pd.Series:
    """Convert for checking, rejecting text blanks and nonfinite values."""
    original = frame[column]
    try:
        parsed = pd.to_numeric(original, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{column} must be numeric or genuinely missing") from exc
    if (parsed.isna() & ~original.isna()).any():
        raise ValueError(f"{column} contains a text value masquerading as missing")
    if not nullable and parsed.isna().any():
        raise ValueError(f"{column} contains missing values")
    values = parsed.to_numpy(dtype=np.float64, na_value=np.nan)
    if np.isinf(values).any():
        raise ValueError(f"{column} contains nonfinite values")
    return pd.Series(values, index=frame.index)


def _whole_nonnegative(frame: pd.DataFrame, column: str, *, maximum: int | None = None) -> pd.Series:
    values = _numeric(frame, column)
    if (values < 0).any() or not np.equal(values, np.floor(values)).all():
        raise ValueError(f"{column} must contain nonnegative integers")
    if maximum is not None and (values > maximum).any():
        raise ValueError(f"{column} exceeds {maximum}")
    return values.astype(np.int64)


def _snapshot(frame: pd.DataFrame, column: str, expected: str | None) -> str:
    values = frame[column]
    if values.isna().any() or values.astype(str).str.strip().eq("").any():
        raise ValueError(f"{column} must be nonblank in every row")
    found = set(values.astype(str))
    if len(found) != 1:
        raise ValueError(f"{column} must identify one snapshot for the whole run")
    value = found.pop()
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{column} must be an ISO date (YYYY-MM-DD)") from exc
    if expected is not None and value != expected:
        raise ValueError(f"{column} is {value}, expected {expected}")
    return value


def _expected_years(years: Iterable[int]) -> tuple[int, ...]:
    selected = tuple(years)
    if not selected or any(type(year) is not int for year in selected):
        raise ValueError("expected_years must contain integer score years")
    if len(set(selected)) != len(selected):
        raise ValueError("expected_years contains duplicates")
    return tuple(sorted(selected))


def _check_full_native_scores(native: pd.DataFrame, years: tuple[int, ...]) -> dict[str, float]:
    """Check each full-state annual EF total, including N-only identities."""
    required = {"score_year", "universe", "treatment", "issn_l", "ef"}
    if not required <= set(native.columns):
        raise ValueError(f"native_full_scores is missing {sorted(required - set(native.columns))}")
    if native.empty or native[list(required)].isna().any().any():
        raise ValueError("native_full_scores contains missing state keys or EF")
    if native.duplicated(["score_year", "universe", "treatment", "issn_l"]).any():
        raise ValueError("native_full_scores contains duplicate state identities")
    native_years = _whole_nonnegative(native, "score_year")
    if set(native_years) != set(years):
        raise ValueError("native_full_scores has the wrong score years")
    if not native["universe"].isin(("N", "OA")).all() or not native["treatment"].isin(("Raw", "Filtered")).all():
        raise ValueError("native_full_scores has an unknown universe or treatment")
    ef = _numeric(native, "ef")
    if (ef < 0).any() or (ef > 100 + 1e-8).any():
        raise ValueError("native_full_scores EF must be between 0 and 100")
    states = native.assign(ef_checked=ef, year_checked=native_years).groupby(
        ["year_checked", "universe", "treatment"], sort=True
    ).ef_checked.sum()
    expected = {(year, universe, treatment) for year in years
                for universe in ("N", "OA") for treatment in ("Raw", "Filtered")}
    if set(states.index) != expected:
        raise ValueError("native_full_scores lacks a complete N/OA Raw/Filtered state")
    bad = states[(states - 100.0).abs() > 1e-8]
    if not bad.empty:
        raise ValueError(f"native_full_scores EF does not sum to 100: {bad.index[0]}")
    return {f"{year}:{universe}:{treatment}": float(value)
            for (year, universe, treatment), value in states.items()}


def validate_handoff_frame(
    frame: pd.DataFrame,
    *,
    annual_n_members: pd.DataFrame,
    expected_years: Iterable[int] = DEFAULT_YEARS,
    expected_oa_snapshot: str = EXPECTED_OA_SNAPSHOT,
    expected_norwegian_snapshot: str | None = None,
    native_full_scores: pd.DataFrame | None = None,
) -> dict:
    """Return a compact audit or raise ``ValueError`` before any CSV is written.

    ``frame`` has exactly the June 37 columns (any order). Callers can then
    write ``frame.loc[:, EXPORT_COLUMNS]`` as UTF-8 with BOM. The independent
    Norwegian membership input has ``score_year, issn_l`` columns after annual
    identity projection. Only membership for exported journal-years is tested;
    this validator does not turn missing-score journals into export rows.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("handoff frame must be a nonempty DataFrame")
    if len(frame.columns) != len(set(frame.columns)) or set(frame.columns) != set(EXPORT_COLUMNS):
        missing = sorted(set(EXPORT_COLUMNS) - set(frame.columns))
        extra = sorted(set(frame.columns) - set(EXPORT_COLUMNS))
        raise ValueError(f"handoff must have the June 37 columns; missing={missing}, extra={extra}")
    years = _expected_years(expected_years)
    score_year = _whole_nonnegative(frame, "score_year")
    if set(score_year) != set(years):
        raise ValueError(f"score years must be exactly {years}")
    start = _whole_nonnegative(frame, "publication_window_start")
    end = _whole_nonnegative(frame, "publication_window_end")
    if not start.eq(score_year - 5).all() or not end.eq(score_year - 1).all():
        raise ValueError("publication window must be score year minus five through minus one")
    ids = frame["openalex_id"].astype("string")
    keys = frame["issn_l"].astype("string")
    if ids.isna().any() or ids.str.strip().eq("").any() or keys.isna().any() or keys.str.strip().eq("").any():
        raise ValueError("OpenAlex ID and effective ISSN-L must be nonblank")
    if pd.DataFrame({"id": ids, "year": score_year}).duplicated().any():
        raise ValueError("duplicate (openalex_id, score_year) handoff row")

    oa_snapshot = _snapshot(frame, "oa_snapshot_version", expected_oa_snapshot)
    n_snapshot = _snapshot(frame, "norwegian_register_snapshot", expected_norwegian_snapshot)

    raw = _whole_nonnegative(frame, "eligible_publications_raw")
    filtered = _whole_nonnegative(frame, "eligible_publications_filtered")
    if (filtered > raw).any():
        raise ValueError("Filtered publications exceed Raw")
    incoming_raw = _whole_nonnegative(frame, "incoming_citations_raw")
    incoming_filtered = _whole_nonnegative(frame, "incoming_citations_filtered")
    if (incoming_filtered > incoming_raw).any():
        raise ValueError("Filtered incoming citations exceed Raw")
    _whole_nonnegative(frame, "active_publication_years_of_5", maximum=5)
    coverage = _numeric(frame, "reference_coverage_pct", nullable=True)
    zero_raw = raw.eq(0)
    if coverage[zero_raw].notna().any() or coverage[~zero_raw].isna().any():
        raise ValueError("reference coverage must be missing exactly when Raw publications are zero")
    if not np.allclose(coverage[~zero_raw], 100 * filtered[~zero_raw] / raw[~zero_raw], rtol=0, atol=1e-8):
        raise ValueError("reference coverage disagrees with publication counts")

    values = {column: _numeric(frame, column, nullable=True) for column in SCORE_COLUMNS}
    for column, score in values.items():
        if (score.dropna() < 0).any():
            raise ValueError(f"{column} contains negative scores")
        if column.startswith("ef_") and (score.dropna() > 100 + 1e-8).any():
            raise ValueError(f"{column} exceeds the EF scale of 100")
    score_table = pd.DataFrame(values)
    if score_table.notna().sum(axis=1).eq(0).any():
        raise ValueError("all eight score values are missing in a handoff row")
    for universe in ("n", "oa"):
        for treatment, articles in (("raw", raw), ("filtered", filtered)):
            ef = values[f"ef_{universe}_{treatment}"]
            ais = values[f"ais_{universe}_{treatment}"]
            if ais[ef.isna()].notna().any():
                raise ValueError(f"AIS without EF in {universe}/{treatment}")
            if ais[ef.notna() & articles.eq(0)].notna().any():
                raise ValueError(f"AIS defined with zero articles in {universe}/{treatment}")
            if ais[ef.notna() & articles.gt(0)].isna().any():
                raise ValueError(f"AIS missing despite positive articles in {universe}/{treatment}")
            if ais[ef.eq(0) & articles.gt(0)].ne(0).any():
                raise ValueError(f"AIS is not zero when EF is zero in {universe}/{treatment}")

    if not isinstance(annual_n_members, pd.DataFrame) or not {"score_year", "issn_l"} <= set(annual_n_members.columns):
        raise ValueError("annual_n_members must contain score_year and effective issn_l")
    if annual_n_members[["score_year", "issn_l"]].isna().any().any():
        raise ValueError("annual_n_members contains missing identity keys")
    member_year = _whole_nonnegative(annual_n_members, "score_year")
    if not set(member_year) <= set(years):
        raise ValueError("annual_n_members has score years outside the handoff")
    member_keys = annual_n_members["issn_l"].astype("string")
    if member_keys.str.strip().eq("").any():
        raise ValueError("annual_n_members contains blank effective ISSN-L")
    members = set(zip(member_year, member_keys))
    is_member = pd.Series([key in members for key in zip(score_year, keys)], index=frame.index)
    urls = frame["norwegian_register_url"].fillna("").astype(str).str.strip()
    if (urls.ne("") != is_member).any():
        raise ValueError("annual Norwegian membership and nonempty register URL disagree")
    if frame.loc[is_member, "norwegian_level"].isna().any() or (
        frame.loc[is_member, "norwegian_level"].astype(str).str.strip() == ""
    ).any():
        raise ValueError("Norwegian members must have an annual level")
    if frame.loc[~is_member, "norwegian_level"].notna().any():
        raise ValueError("nonmembers must not carry a Norwegian annual level")
    for url in urls[is_member]:
        if not all(part.startswith("https://kanalregister.hkdir.no/tidsskrift?id=") and
                   part.removeprefix("https://kanalregister.hkdir.no/tidsskrift?id=").isdigit()
                   for part in url.split("; ")):
            raise ValueError("Norwegian register URL must contain valid annual journal links")
    for treatment in ("raw", "filtered"):
        if values[f"ef_n_{treatment}"][~is_member].notna().any() or values[f"ais_n_{treatment}"][~is_member].notna().any():
            raise ValueError("nonmembers have Norwegian-universe scores")

    native_sums = _check_full_native_scores(native_full_scores, years) if native_full_scores is not None else None
    return {
        "years": list(years), "rows": len(frame),
        "rows_by_year": {str(year): int(score_year.eq(year).sum()) for year in years},
        "norwegian_members_by_year": {str(year): int((score_year.eq(year) & is_member).sum()) for year in years},
        "oa_snapshot_version": oa_snapshot,
        "norwegian_register_snapshot": n_snapshot,
        "defined_scores": {column: int(score_table[column].notna().sum()) for column in SCORE_COLUMNS},
        "zero_scores": {column: int(score_table[column].eq(0).sum()) for column in SCORE_COLUMNS},
        "native_full_state_ef_sums": native_sums,
        "columns": list(EXPORT_COLUMNS),
    }

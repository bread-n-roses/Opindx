"""Assemble one audited website year from native scores and annual inputs.

All inputs are already projected through the *same* reviewed annual identity
map. This module does not choose a map, match the register, or write the CSV.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from handoff import EXPORT_COLUMNS, SCORE_COLUMNS
from match_norwegian import AnnualNorwegianIdentity
from sources import journal_export_view


def _unique(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    if not set(columns).issubset(frame.columns):
        raise ValueError(f"{label} lacks {sorted(set(columns) - set(frame.columns))}")
    if frame[columns].isna().any().any() or frame.duplicated(columns).any():
        raise ValueError(f"{label} keys must be nonmissing and unique: {columns}")


def _joined(values: Iterable[object]) -> str:
    return " | ".join(sorted({str(value).strip() for value in values if pd.notna(value) and str(value).strip()}))


def _issns(value: object) -> str:
    if isinstance(value, (tuple, list, set)):
        return "; ".join(sorted({str(item) for item in value if str(item)}))
    if value is None or pd.isna(value):
        return ""
    return str(value)


def assemble_annual(
    year: int,
    *,
    sources: pd.DataFrame,
    counts: pd.DataFrame,
    edges: pd.DataFrame,
    retention: pd.DataFrame,
    scores: pd.DataFrame,
    norwegian: Iterable[AnnualNorwegianIdentity],
    oa_snapshot_version: str,
    norwegian_register_snapshot: str,
) -> pd.DataFrame:
    """Build June-compatible 37-column rows for one September score year.

    `retention` is the score-year-projected annual article table for t-5..t-1:
    columns `issn_l,year,a_raw,a_filtered`. It is intentionally distinct from
    the five-year `counts` totals; both arm sums are checked here.
    """
    if type(year) is not int:
        raise ValueError("score year must be an integer")
    _unique(sources, ["issn_l"], "sources")
    _unique(counts, ["issn_l"], "counts")
    _unique(edges, ["citing_issn_l", "cited_issn_l"], "edges")
    _unique(retention, ["issn_l", "year"], "retention")
    _unique(scores, ["score_year", "universe", "treatment", "issn_l"], "scores")
    if not {"source_type", "openalex_id", "title", "issns", "publisher", "oa_domain",
            "oa_field", "is_open_access", "updated_date"}.issubset(sources.columns):
        raise ValueError("sources lack export metadata or source_type")
    if not {"a_raw", "a_filtered"}.issubset(counts.columns):
        raise ValueError("counts lack Raw/Filtered articles")
    if not {"n_raw", "n_filtered"}.issubset(edges.columns):
        raise ValueError("edges lack Raw/Filtered citations")
    if not {"a_raw", "a_filtered"}.issubset(retention.columns) or not {"ef", "ais"}.issubset(scores.columns):
        raise ValueError("retention or scores lack required measures")
    if not scores.score_year.eq(year).all():
        raise ValueError("scores contain another score year")
    if not retention.year.between(year - 5, year - 1).all():
        raise ValueError("retention contains years outside the cited window")
    if not scores.universe.isin(("N", "OA")).all() or not scores.treatment.isin(("Raw", "Filtered")).all():
        raise ValueError("scores contain unexpected universe or treatment")
    if not sources.source_type.astype("string").str.casefold().eq("journal").fillna(False).any():
        raise ValueError("sources contain no OA journal identities")
    journals = sources.loc[sources.source_type.astype("string").str.casefold().eq("journal").fillna(False)].set_index("issn_l")
    oa_scored = set(scores.loc[scores.universe.eq("OA"), "issn_l"])
    if not oa_scored or not oa_scored.issubset(journals.index):
        raise ValueError("OA scores lack corrected journal source metadata")
    ids = sorted(set(scores.issn_l) & set(journals.index))
    if not ids:
        raise ValueError("no scored OA journal rows")
    # Require the independently audited canonical-arm export view for the
    # scored subset. An ambiguous unscored source cannot enter the handoff.
    frame = journal_export_view(sources.loc[sources.issn_l.isin(ids)]).set_index("issn_l").reindex(ids).copy()
    if frame.openalex_id.isna().any() or frame.openalex_id.duplicated().any():
        raise ValueError("exported journal source IDs must be present and unique")
    if frame.title.isna().any() or frame.title.astype(str).str.strip().eq("").any():
        raise ValueError("exported journal titles must be present")
    frame["openalex_url"] = frame.openalex_id.map(lambda value: f"https://openalex.org/{value}")
    frame["journal_title"] = frame.title
    frame["issns"] = frame.issns.map(_issns)
    frame["is_open_access"] = frame.is_open_access.map(
        lambda value: pd.NA if pd.isna(value) else str(bool(value)).lower()
    )
    frame["source_metadata_updated"] = pd.to_datetime(frame.updated_date, utc=True).dt.strftime("%Y-%m-%d")
    for column in SCORE_COLUMNS:
        frame[column] = np.nan
    for (universe, treatment), cell in scores.groupby(["universe", "treatment"]):
        keyed = cell.set_index("issn_l")
        for metric in ("ef", "ais"):
            frame[f"{metric}_{universe.lower()}_{treatment.lower()}"] = keyed[metric].reindex(ids)
    if frame.loc[:, SCORE_COLUMNS].isna().all(axis=1).any():
        raise AssertionError("an all-eight-missing journal entered the website export")

    count_view = counts.set_index("issn_l").reindex(ids)[["a_raw", "a_filtered"]].fillna(0).astype("int64")
    if count_view.a_filtered.gt(count_view.a_raw).any():
        raise ValueError("Filtered article mass exceeds Raw")
    frame["eligible_publications_raw"] = count_view.a_raw
    frame["eligible_publications_filtered"] = count_view.a_filtered
    frame["reference_coverage_pct"] = 100 * count_view.a_filtered / count_view.a_raw.where(count_view.a_raw.gt(0))
    observed = retention.groupby("issn_l")[["a_raw", "a_filtered"]].sum().reindex(ids).fillna(0).astype("int64")
    if not observed.equals(count_view):
        raise ValueError("annual retention does not reconcile to five-year counts")
    active = retention.loc[retention.a_raw.gt(0)].groupby("issn_l").year.nunique()
    frame["active_publication_years_of_5"] = active.reindex(ids).fillna(0).astype("int64")
    if not frame.active_publication_years_of_5.between(0, 5).all():
        raise AssertionError("active publication years exceed five")

    nonself = edges.loc[edges.citing_issn_l.ne(edges.cited_issn_l)]
    incoming = nonself.groupby("cited_issn_l")[["n_raw", "n_filtered"]].sum().reindex(ids).fillna(0).astype("int64")
    if incoming.n_filtered.gt(incoming.n_raw).any():
        raise ValueError("Filtered incoming citation mass exceeds Raw")
    frame["incoming_citations_raw"] = incoming.n_raw
    frame["incoming_citations_filtered"] = incoming.n_filtered

    membership: dict[str, AnnualNorwegianIdentity] = {}
    for group in norwegian:
        if group.score_year != year or group.issn_l in membership:
            raise ValueError("Norwegian grouped membership has wrong year or repeated identity")
        membership[group.issn_l] = group
    frame["norwegian_register_url"] = [
        membership[key].norwegian_register_url if key in membership else "" for key in ids
    ]
    frame["norwegian_area"] = [
        _joined(item.member.discipline for item in membership[key].members) if key in membership else pd.NA
        for key in ids
    ]
    frame["norwegian_field"] = [
        _joined(item.member.field for item in membership[key].members) if key in membership else pd.NA
        for key in ids
    ]
    frame["norwegian_level"] = [
        _joined(item.member.norwegian_level for item in membership[key].members) if key in membership else pd.NA
        for key in ids
    ]
    frame["score_year"] = year
    frame["publication_window_start"] = year - 5
    frame["publication_window_end"] = year - 1
    frame["citation_scope"] = "score_year_full_cached_source_graph_excluding_journal_self_citations"
    frame["oa_snapshot_version"] = oa_snapshot_version
    frame["norwegian_register_snapshot"] = norwegian_register_snapshot
    frame["field_assignment_scope"] = "snapshot_oa_primary_topic;annual_norwegian_roster_metadata"
    frame["sample_scope"] = "oa_journals_with_any_nonmissing_n_or_oa_ef_or_ais_in_score_year"
    frame["data_source"] = "website_2026-09-23_independent_scores_and_annual_inputs"
    return frame.reset_index().loc[:, EXPORT_COLUMNS]

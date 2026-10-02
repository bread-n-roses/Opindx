"""Website N/OA Raw/Filtered EF and AIS on annual effective-identity inputs.

The counts, edges, source types and Norwegian roster passed here must all use
the *same audited score-year identity map*. This module deliberately refuses
raw-identity count and edge column names; identity projection is a separate
input stage. Solver mathematics are the hash-preserved copies of the tested
square solver and sparse primitives beside this file.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse

from square_solver import solve_square


def _check_nonnegative_integer(frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    for column in columns:
        if column not in frame:
            raise ValueError(f"Missing endpoint column {column}")
        values = pd.to_numeric(frame[column], errors="raise").to_numpy(dtype=np.float64)
        if not np.isfinite(values).all() or (values < 0).any() or not np.equal(values, np.floor(values)).all():
            raise ValueError(f"Endpoint column {column} must contain nonnegative integers")


def score_annual(
    year: int,
    counts: pd.DataFrame,
    edges: pd.DataFrame,
    sources: pd.DataFrame,
    norwegian_issn_l: Collection[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute four cells; keep the Raw-active state fixed under both treatments.

    Expected inputs: effective `issn_l,a_raw,a_filtered`; effective
    `citing_issn_l,cited_issn_l,n_raw,n_filtered`; and one
    `issn_l,source_type` row per corrected OpenAlex source identity. Self-edge
    rows remain in input and are removed after state restriction.
    """
    if type(year) is not int:
        raise ValueError("Score year must be an integer")
    if not {"issn_l", "a_raw", "a_filtered"}.issubset(counts.columns):
        raise ValueError("Counts must use effective issn_l and both article arms")
    if not {"citing_issn_l", "cited_issn_l", "n_raw", "n_filtered"}.issubset(edges.columns):
        raise ValueError("Edges must use effective endpoint keys and both citation arms")
    if not {"issn_l", "source_type"}.issubset(sources.columns):
        raise ValueError("Sources must contain effective issn_l and source_type")
    for name, frame, key in (("counts", counts, "issn_l"), ("sources", sources, "issn_l")):
        if frame[key].isna().any() or frame[key].duplicated().any():
            raise ValueError(f"{name} effective identities must be nonmissing and unique")
    for key in ("citing_issn_l", "cited_issn_l"):
        if edges[key].isna().any():
            raise ValueError(f"Edges contain missing {key}")
    _check_nonnegative_integer(counts, ("a_raw", "a_filtered"))
    _check_nonnegative_integer(edges, ("n_raw", "n_filtered"))
    counts = counts.copy()
    edges = edges.copy()
    for column in ("a_raw", "a_filtered"):
        counts[column] = pd.to_numeric(counts[column]).astype("int64")
    for column in ("n_raw", "n_filtered"):
        edges[column] = pd.to_numeric(edges[column]).astype("int64")
    if (counts["a_filtered"] > counts["a_raw"]).any():
        raise ValueError("Filtered article counts exceed Raw")
    if (edges["n_filtered"] > edges["n_raw"]).any():
        raise ValueError("Filtered citation weights exceed Raw")
    if edges.duplicated(["citing_issn_l", "cited_issn_l"]).any():
        raise ValueError("Effective citation pairs must be unique")

    raw_positive = edges["n_raw"] > 0
    active = set(counts.loc[counts["a_raw"] > 0, "issn_l"])
    active.update(edges.loc[raw_positive, "citing_issn_l"])
    active.update(edges.loc[raw_positive, "cited_issn_l"])
    norwegian = set(norwegian_issn_l)
    if not norwegian or None in norwegian or "" in norwegian:
        raise ValueError("Norwegian effective roster must be nonempty and fully identified")
    oa_journals = set(sources.loc[
        sources["source_type"].astype("string").str.strip().str.casefold().eq("journal").fillna(False),
        "issn_l",
    ])
    states = {"N": sorted(active & norwegian), "OA": sorted(active & oa_journals)}
    for universe, state in states.items():
        if not state:
            raise ValueError(f"{year} {universe} Raw-active state is empty")

    counts_by_key = counts.set_index("issn_l")
    rows: list[pd.DataFrame] = []
    audits: list[dict[str, Any]] = []
    for universe, state in states.items():
        index = {key: position for position, key in enumerate(state)}
        state_set = set(state)
        projected = edges.loc[
            edges["citing_issn_l"].isin(state_set) & edges["cited_issn_l"].isin(state_set)
        ]
        nonself = projected.loc[projected["citing_issn_l"] != projected["cited_issn_l"]]
        for treatment, article_column, edge_column in (
            ("Raw", "a_raw", "n_raw"),
            ("Filtered", "a_filtered", "n_filtered"),
        ):
            positive = nonself.loc[nonself[edge_column] > 0]
            matrix = sparse.csc_matrix(
                (
                    positive[edge_column].to_numpy(dtype=np.float64),
                    (
                        positive["cited_issn_l"].map(index).to_numpy(dtype=np.int64),
                        positive["citing_issn_l"].map(index).to_numpy(dtype=np.int64),
                    ),
                ),
                shape=(len(state), len(state)),
            )
            matrix.sum_duplicates()
            matrix.eliminate_zeros()
            if matrix.diagonal().any():
                raise AssertionError("A self-citation survived matrix construction")
            articles = counts_by_key[article_column].reindex(state).fillna(0).to_numpy(dtype=np.float64)
            solution = solve_square(matrix, articles, np.asarray(state, dtype=object))
            if solution.residual_l1 > 1e-12 or abs(solution.ef_sum - 100.0) > 1e-8:
                raise AssertionError(f"{year} {universe} {treatment}: solver invariant failed")
            if not np.array_equal(np.isnan(solution.ais), ~solution.ais_defined):
                raise AssertionError("AIS missingness differs from zero article mass")
            rows.append(pd.DataFrame({
                "score_year": year,
                "universe": universe,
                "treatment": treatment,
                "issn_l": state,
                "ef": solution.ef,
                "ais": solution.ais,
                "ais_defined": solution.ais_defined,
            }))
            audits.append({
                "score_year": year,
                "universe": universe,
                "treatment": treatment,
                "state_size": len(state),
                "state_identity_sha256": _state_hash(state),
                "nonself_edge_weight": int(positive[edge_column].sum()),
                "article_mass": int(articles.sum()),
                "self_citation_rows_removed": len(projected) - len(nonself),
                "iterations": solution.iterations,
                "residual_l1": solution.residual_l1,
                "ef_sum": solution.ef_sum,
            })
    return pd.concat(rows, ignore_index=True), pd.DataFrame(audits)


def _state_hash(state: list[str]) -> str:
    import hashlib

    return hashlib.sha256("\n".join(state).encode("utf-8")).hexdigest()

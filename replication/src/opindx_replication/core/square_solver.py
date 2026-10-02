"""Paper B square-universe EF/AIS/AIP solver.

Implements ``analysis/paper_b/SOLVER_SPEC.md``. This module does not load any
Norwegian roster, graph cache, or Clarivate file, and it does not decide which
production graph Paper B uses -- that is the Step 2.5 audit's job. It takes an
already-constructed, self-citation-free citation matrix over one fixed universe
and computes EF, NEF, AIS, and AIP per the spec.

Per SOLVER_SPEC.md Section 11, the recursive solve and transport steps are not
reimplemented here: they call ``sparse_recursive.py`` with an identity
transmitter-row map (``transmitter_rows = arange(n)``), which is an exact --
not approximate -- degenerate case of the tested rectangular primitives. Only
AIS, AIP, NEF, and serialization are new code, because the rectangular module
does not compute them.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

from sparse_recursive import (  # noqa: E402  (path must be set up first)
    ALPHA,
    SparseOperators,
    article_vector,
    build_sparse_operators,
    solve_stationary,
    transport_sparse,
)

RESIDUAL_TOLERANCE = 1e-12
MASS_SUM_TOLERANCE = 1e-12
EF_SUM_TOLERANCE = 1e-8
ITERATION_TOLERANCE = 1e-14
SERIALIZATION_DECIMALS = 4


@dataclass(frozen=True)
class SquareSolution:
    """Every quantity SOLVER_SPEC.md Section 10 requires a caller to check."""

    issn_l: np.ndarray
    ef: np.ndarray
    nef: np.ndarray
    ais: np.ndarray
    ais_defined: np.ndarray
    pi: np.ndarray
    z: float
    residual_l1: float
    iterations: int
    pi_sum: float
    ef_sum: float
    universe_size: int


def build_square_operators(
    b: sparse.spmatrix,
    *,
    self_tolerance: float = 0.0,
) -> SparseOperators:
    """Build the square operator via the identity transmitter-row map.

    SOLVER_SPEC.md Section 11: with ``transmitter_rows = arange(n)`` on a
    square matrix, ``h_tt`` and ``h_et`` are the column-normalization of the
    identical input matrix, so they are equal bit-for-bit, not approximately.
    This function asserts that equality defensively at runtime rather than
    only in the specification's proof, so a future change to the reused
    primitives cannot silently break the degenerate-case argument.
    """
    n = b.shape[0]
    if b.shape[0] != b.shape[1]:
        raise ValueError("A square-universe citation matrix must be square")
    rows = np.arange(n, dtype=np.int64)
    operators = build_sparse_operators(b, rows, self_tolerance=self_tolerance)
    if operators.h_tt.shape != operators.h_et.shape or (
        operators.h_tt != operators.h_et
    ).nnz != 0:
        raise AssertionError(
            "h_tt and h_et must be identical for a square universe (T=E)"
        )
    if not np.array_equal(operators.q_t, operators.q_e):
        raise AssertionError("q_t and q_e must be identical for a square universe")
    if not np.array_equal(operators.solve_dangling, operators.transport_dangling):
        raise AssertionError(
            "solve_dangling and transport_dangling must be identical for a "
            "square universe"
        )
    return operators


def solve_square(
    b: sparse.spmatrix,
    article_counts: np.ndarray,
    issn_l: np.ndarray,
    *,
    alpha: float = ALPHA,
) -> SquareSolution:
    """Run the full SOLVER_SPEC.md Sections 2, 3, 5, 6 pipeline (EF/NEF/AIS).

    ``article_counts`` must already reflect the filter chosen for Open
    Decision 1 (Section 4): this function does not filter works itself.
    """
    n = b.shape[0]
    if len(article_counts) != n or len(issn_l) != n:
        raise ValueError("article_counts and issn_l must match the universe size")
    if len(set(issn_l.tolist())) != n:
        raise ValueError("issn_l must be unique (one row per universe member)")

    operators = build_square_operators(b)
    a = article_vector(article_counts)
    solution = solve_stationary(
        operators,
        a,
        alpha=alpha,
        iteration_tolerance=ITERATION_TOLERANCE,
        residual_tolerance=RESIDUAL_TOLERANCE,
    )
    if solution.residual_l1 > RESIDUAL_TOLERANCE:
        raise AssertionError(
            f"Fixed-point residual {solution.residual_l1:.3e} exceeds "
            f"SOLVER_SPEC.md Section 9 bound {RESIDUAL_TOLERANCE:.0e}"
        )
    pi_sum = float(solution.pi.sum())
    if abs(pi_sum - 1.0) > MASS_SUM_TOLERANCE:
        raise AssertionError(
            f"pi sums to {pi_sum!r}, outside SOLVER_SPEC.md Section 9 bound "
            f"{MASS_SUM_TOLERANCE:.0e}"
        )

    flow, e, z = transport_sparse(operators, solution.pi)
    if z <= 0:
        raise AssertionError("All prestige is transport-dangling; z must be positive")
    ef = 100.0 * e
    ef_sum = float(ef.sum())
    if abs(ef_sum - 100.0) > EF_SUM_TOLERANCE:
        raise AssertionError(
            f"EF sums to {ef_sum!r}, outside SOLVER_SPEC.md Section 9 bound "
            f"{EF_SUM_TOLERANCE:.0e} of 100"
        )

    nef = ef * (n / 100.0)

    ais_defined = a > 0
    ais = np.full(n, np.nan, dtype=np.float64)
    ais[ais_defined] = (ef[ais_defined] / 100.0) / a[ais_defined]

    return SquareSolution(
        issn_l=np.asarray(issn_l),
        ef=ef,
        nef=nef,
        ais=ais,
        ais_defined=ais_defined,
        pi=solution.pi,
        z=z,
        residual_l1=solution.residual_l1,
        iterations=solution.iterations,
        pi_sum=pi_sum,
        ef_sum=ef_sum,
        universe_size=n,
    )


def score_universe_production(
    roster_issn_l: set[str],
    links: pd.DataFrame,
    counts: pd.Series,
) -> tuple[SquareSolution, set[str], list[str], pd.DataFrame]:
    """Production universe construction per SOLVER_SPEC.md Sec 1/2/2a.

    U_t is the full U0 (every roster node present in the graph) -- NOT further
    restricted to nodes with a surviving roster-internal, non-self-citation
    edge the way the legacy-reproduction driver
    (``step02b_roster_delta.score_universe``, 2a/2b only) does. Zero-total-
    degree nodes remain in the state as all-zero dangling rows/columns;
    ``sparse_recursive.py``'s existing per-node dangling detection in
    ``column_normalize_sparse`` already handles this correctly and is not
    modified here -- only the driver's node selection changes.

    Returns (solution, u0, isolated, restricted), matching
    ``score_universe``'s shape so callers can share downstream code, except
    ``isolated`` here is purely descriptive (a zero-total-degree node is
    still present in ``solution``, not excluded from it -- see SOLVER_SPEC.md
    Sec 2a for the EF=0 / AIS=0-when-defined consequences).
    """
    nodes_global = set(
        np.union1d(links["citing_issn_l"].values, links["cited_issn_l"].values)
    )
    u0 = roster_issn_l & nodes_global

    restricted = links[
        links["citing_issn_l"].isin(u0) & links["cited_issn_l"].isin(u0)
    ]
    restricted = restricted[restricted["citing_issn_l"] != restricted["cited_issn_l"]]

    issn_l = np.array(sorted(u0))
    n = len(issn_l)
    index = {value: position for position, value in enumerate(issn_l)}
    rows = restricted["cited_issn_l"].map(index).to_numpy()
    cols = restricted["citing_issn_l"].map(index).to_numpy()
    weights = restricted["count"].to_numpy(dtype=np.float64)
    b = sparse.csc_matrix((weights, (rows, cols)), shape=(n, n))

    article_counts = counts.reindex(issn_l, fill_value=0).to_numpy(dtype=np.float64)
    solution = solve_square(b, article_counts, issn_l)

    if len(restricted):
        connected = set(
            np.union1d(restricted["citing_issn_l"].values, restricted["cited_issn_l"].values)
        )
    else:
        connected = set()
    isolated = sorted(u0 - connected)

    return solution, u0, isolated, restricted


def compute_aip(
    ais: np.ndarray,
    *,
    pool_mask: np.ndarray | None = None,
) -> np.ndarray:
    """AIP via ``rank(pct=True, method="max")``, verified against
    ``phase9_ship_v2.py`` line 125 (see SOLVER_SPEC.md Section 7/8).

    ``pool_mask`` selects which journals participate in the percentile pool.
    SOLVER_SPEC.md Section 7's production default is the full universe (every
    journal with a defined AIS); pass a narrower mask only when reproducing a
    specific historical pool (Step 2 gate runs 2a/2b), per Section 12.
    """
    ais = np.asarray(ais, dtype=np.float64)
    n = len(ais)
    if pool_mask is None:
        pool_mask = np.isfinite(ais)
    pool_mask = np.asarray(pool_mask, dtype=bool)
    if pool_mask.shape != ais.shape:
        raise ValueError("pool_mask must match the universe size")
    if np.isnan(ais[pool_mask]).any():
        raise ValueError("pool_mask selects a journal with an undefined AIS")

    aip = np.full(n, np.nan, dtype=np.float64)
    if pool_mask.any():
        pooled = pd.Series(ais[pool_mask]).rank(pct=True, method="max")
        aip[pool_mask] = pooled.to_numpy(dtype=np.float64)
    return aip


def serialize_public(values: np.ndarray) -> np.ndarray:
    """Four-decimal rounding matching ``phase9b_output.py`` lines 58-60."""
    values = np.asarray(values, dtype=np.float64)
    rounded = np.round(values, SERIALIZATION_DECIMALS)
    rounded[np.isnan(values)] = np.nan
    return rounded


def run_square_solver(
    b: sparse.spmatrix,
    article_counts: np.ndarray,
    issn_l: np.ndarray,
    *,
    pool_mask: np.ndarray | None = None,
    alpha: float = ALPHA,
) -> pd.DataFrame:
    """End-to-end SOLVER_SPEC.md pipeline, returning one row per universe member.

    Diagnostic fields (``residual_l1``, ``pi_sum``, ``ef_sum``, ``z``,
    ``iterations``, ``universe_size``) are attached to the returned frame via
    ``DataFrame.attrs`` rather than only logged, so a caller holding the
    in-memory result always has the invariants alongside the scores.

    ``DataFrame.attrs`` is a plain Python dict on the object, not a column: it
    does **not** survive ``to_csv``/``read_csv`` or most other serialization.
    A caller that writes this frame to disk (SOLVER_SPEC.md Section 12's Step
    2 driver, for example) must persist ``frame.attrs`` separately -- e.g. to
    a sibling ``.json`` run report -- or the invariants are silently lost the
    moment anyone re-reads the CSV.
    """
    solution = solve_square(b, article_counts, issn_l, alpha=alpha)
    aip = compute_aip(solution.ais, pool_mask=pool_mask)
    frame = pd.DataFrame(
        {
            "issn_l": solution.issn_l,
            "ef": solution.ef,
            "nef": solution.nef,
            "ais": solution.ais,
            "ais_public": serialize_public(solution.ais),
            "aip": aip,
            "aip_public": serialize_public(aip),
            "ais_defined": solution.ais_defined,
        }
    )
    frame.attrs["residual_l1"] = solution.residual_l1
    frame.attrs["iterations"] = solution.iterations
    frame.attrs["pi_sum"] = solution.pi_sum
    frame.attrs["ef_sum"] = solution.ef_sum
    frame.attrs["z"] = solution.z
    frame.attrs["universe_size"] = solution.universe_size
    return frame

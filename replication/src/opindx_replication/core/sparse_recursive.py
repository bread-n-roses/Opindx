"""Sparse production primitives for the pinned solve-then-transport operator."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import sparse


ALPHA = 0.85


@dataclass(frozen=True)
class SparseOperators:
    h_tt: sparse.csc_matrix
    h_et: sparse.csc_matrix
    q_t: np.ndarray
    q_e: np.ndarray
    solve_dangling: np.ndarray
    transport_dangling: np.ndarray
    transmitter_rows: np.ndarray


@dataclass(frozen=True)
class StationarySolution:
    pi: np.ndarray
    iterations: int
    final_delta_l1: float
    residual_l1: float


def as_nonnegative_csc(values: sparse.spmatrix) -> sparse.csc_matrix:
    if not sparse.issparse(values):
        raise TypeError("Production citation weights must be a SciPy sparse matrix")
    matrix = values.astype(np.float64, copy=True).tocsc()
    matrix.sum_duplicates()
    matrix.eliminate_zeros()
    if not np.isfinite(matrix.data).all():
        raise ValueError("Citation weights must be finite")
    if (matrix.data < 0).any():
        raise ValueError("Citation weights must be nonnegative")
    return matrix


def probability_vector(values: np.ndarray, *, name: str) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float64)
    if vector.ndim != 1 or not np.isfinite(vector).all() or (vector < 0).any():
        raise ValueError(f"{name} must be a finite nonnegative vector")
    if not np.isclose(float(vector.sum()), 1.0, rtol=0.0, atol=1e-12):
        raise ValueError(f"{name} must sum to one")
    return vector


def article_vector(article_counts: np.ndarray) -> np.ndarray:
    counts = np.asarray(article_counts, dtype=np.float64)
    if counts.ndim != 1 or not np.isfinite(counts).all() or (counts < 0).any():
        raise ValueError("Article counts must be a finite nonnegative vector")
    total = float(counts.sum())
    if total <= 0:
        raise ValueError("The transmitting state must have positive article mass")
    return counts / total


def column_normalize_sparse(
    values: sparse.spmatrix,
) -> tuple[sparse.csc_matrix, np.ndarray, np.ndarray]:
    matrix = as_nonnegative_csc(values)
    degrees = np.asarray(matrix.sum(axis=0)).ravel().astype(np.float64)
    active = degrees > 0
    inverse = np.zeros_like(degrees)
    inverse[active] = 1.0 / degrees[active]
    normalized = (matrix @ sparse.diags(inverse, format="csc")).tocsc()
    normalized.sum_duplicates()
    normalized.eliminate_zeros()
    return normalized, degrees, ~active


def column_sums_are_zero_or_one(
    matrix: sparse.spmatrix,
    *,
    tolerance: float = 1e-12,
) -> bool:
    sums = np.asarray(matrix.sum(axis=0)).ravel()
    return bool(
        (
            np.isclose(sums, 0.0, rtol=0.0, atol=tolerance)
            | np.isclose(sums, 1.0, rtol=0.0, atol=tolerance)
        ).all()
    )


def build_sparse_operators(
    b_et: sparse.spmatrix,
    transmitter_rows: np.ndarray,
    *,
    self_tolerance: float = 0.0,
) -> SparseOperators:
    matrix = as_nonnegative_csc(b_et)
    rows = np.asarray(transmitter_rows, dtype=np.int64)
    if rows.ndim != 1 or len(rows) != matrix.shape[1]:
        raise ValueError("One E-row index is required for every transmitter column")
    if len(np.unique(rows)) != len(rows):
        raise ValueError("Transmitter rows must be unique")
    if (rows < 0).any() or (rows >= matrix.shape[0]).any():
        raise ValueError("A transmitter row falls outside E")
    diagonal = np.asarray(matrix[rows, np.arange(matrix.shape[1])]).ravel()
    if (np.abs(diagonal) > self_tolerance).any():
        raise ValueError("Journal self-citations must be zero before normalization")

    b_tt = matrix[rows, :].tocsc()
    h_tt, q_t, solve_dangling = column_normalize_sparse(b_tt)
    h_et, q_e, transport_dangling = column_normalize_sparse(matrix)
    if not column_sums_are_zero_or_one(h_tt):
        raise AssertionError("H_TT columns must sum to zero or one")
    if not column_sums_are_zero_or_one(h_et):
        raise AssertionError("H_ET columns must sum to zero or one")
    if (q_t > q_e + 1e-12).any():
        raise AssertionError("Closed-state outdegree cannot exceed E outdegree")
    if (transport_dangling & ~solve_dangling).any():
        raise AssertionError("Every E-dangling column must also be solve-dangling")
    return SparseOperators(
        h_tt=h_tt,
        h_et=h_et,
        q_t=q_t,
        q_e=q_e,
        solve_dangling=solve_dangling,
        transport_dangling=transport_dangling,
        transmitter_rows=rows,
    )


def fixed_point_rhs(
    operators: SparseOperators,
    pi_t: np.ndarray,
    a_t: np.ndarray,
    *,
    alpha: float = ALPHA,
) -> np.ndarray:
    if alpha != ALPHA:
        raise ValueError("The pinned production damping parameter is alpha=0.85")
    pi = probability_vector(pi_t, name="pi_t")
    articles = probability_vector(a_t, name="a_t")
    if pi.shape != operators.q_t.shape or articles.shape != pi.shape:
        raise ValueError("pi_t and a_t must match the transmitting state")
    return alpha * (operators.h_tt @ pi) + (
        alpha * float(pi[operators.solve_dangling].sum()) + (1.0 - alpha)
    ) * articles


def fixed_point_residual(
    operators: SparseOperators,
    pi_t: np.ndarray,
    a_t: np.ndarray,
    *,
    alpha: float = ALPHA,
) -> float:
    pi = probability_vector(pi_t, name="pi_t")
    rhs = fixed_point_rhs(operators, pi, a_t, alpha=alpha)
    residual = float(np.abs(pi - rhs).sum())
    if not np.isfinite(residual):
        raise ValueError("The fixed-point residual is not finite")
    return residual


def solve_stationary(
    operators: SparseOperators,
    a_t: np.ndarray,
    *,
    alpha: float = ALPHA,
    iteration_tolerance: float = 1e-14,
    residual_tolerance: float = 1e-12,
    max_iterations: int = 10_000,
) -> StationarySolution:
    articles = probability_vector(a_t, name="a_t")
    if articles.shape != operators.q_t.shape:
        raise ValueError("a_t must match the transmitting state")
    if iteration_tolerance <= 0 or residual_tolerance <= 0:
        raise ValueError("Convergence tolerances must be positive")
    if max_iterations < 1:
        raise ValueError("max_iterations must be positive")

    pi = articles.copy()
    final_delta = float("inf")
    for iteration in range(1, max_iterations + 1):
        next_pi = fixed_point_rhs(operators, pi, articles, alpha=alpha)
        final_delta = float(np.abs(next_pi - pi).sum())
        pi = next_pi
        if final_delta <= iteration_tolerance:
            residual = fixed_point_residual(operators, pi, articles, alpha=alpha)
            if residual <= residual_tolerance:
                return StationarySolution(
                    pi=pi,
                    iterations=iteration,
                    final_delta_l1=final_delta,
                    residual_l1=residual,
                )
    residual = fixed_point_residual(operators, pi, articles, alpha=alpha)
    raise RuntimeError(
        "Stationary iteration did not converge: "
        f"iterations={max_iterations}, delta={final_delta:.3e}, "
        f"residual={residual:.3e}"
    )


def transport_sparse(
    operators: SparseOperators,
    pi_t: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    pi = probability_vector(pi_t, name="pi_t")
    if pi.shape != operators.q_e.shape:
        raise ValueError("pi_t must match the transmitting state")
    flow = np.asarray(operators.h_et @ pi).ravel()
    active_mass = float(flow.sum())
    if not np.isfinite(active_mass) or active_mass <= 0:
        raise ValueError("All supplied prestige is transport-dangling")
    expected = 1.0 - float(pi[operators.transport_dangling].sum())
    if not np.isclose(active_mass, expected, rtol=0.0, atol=1e-12):
        raise AssertionError("Transport mass does not equal active prestige mass")
    distribution = flow / active_mass
    if (distribution < -1e-15).any() or not np.isclose(
        float(distribution.sum()), 1.0, rtol=0.0, atol=1e-12
    ):
        raise AssertionError("Transported distribution is invalid")
    return flow, distribution, active_mass


def retention_identity_sparse(
    operators: SparseOperators,
    pi_t: np.ndarray,
) -> float:
    pi = probability_vector(pi_t, name="pi_t")
    if pi.shape != operators.q_e.shape:
        raise ValueError("pi_t must match the transmitting state")
    active = operators.q_e > 0
    denominator = float(pi[active].sum())
    if denominator <= 0:
        raise ValueError("All supplied prestige is transport-dangling")
    retention = np.zeros_like(operators.q_e)
    retention[active] = operators.q_t[active] / operators.q_e[active]
    return float((pi[active] * retention[active]).sum() / denominator)

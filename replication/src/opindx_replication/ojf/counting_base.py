"""Pure OJF count-to-score helpers; no benchmark data or discovery."""
from __future__ import annotations
from collections.abc import Sequence
import re
from typing import Any
import numpy as np
import pandas as pd
UNIVERSES = ("N",)
RETRACTION_MODES = ("all", "exclude_snapshot")
SOURCE_COMPONENTS = {"N-journal":"n_journal", "N-nonjournal":"n_nonjournal", "outside-N-journal":"outside_n_journal", "outside-N-book-proceedings":"outside_n_book_proceedings", "other":"other"}
def _identifier(value: str) -> str:
    """Accept only a relation name, never interpolated SQL or a file path."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid SQL relation identifier: {value!r}")
    return '"' + value + '"'

def _raise_if_any(conn: Any, sql: str, message: str) -> None:
    if conn.execute(sql).fetchone()[0]:
        raise ValueError(message)

def build_scores(counts: pd.DataFrame, *, retraction_modes: Sequence[str] = RETRACTION_MODES) -> pd.DataFrame:
    """Expand counts into the complete E x D cells without removing zero denominators."""
    required = {"journal_id", "universe", "d_raw", "d_ref", "c_raw", "c_ref"}
    if not required.issubset(counts):
        raise ValueError(f"Count columns missing: {sorted(required - set(counts))}")
    if counts.duplicated(["journal_id", "universe"]).any():
        raise ValueError("Counts must have unique journal x universe rows")
    if not retraction_modes or any(mode not in RETRACTION_MODES for mode in retraction_modes):
        raise ValueError("Unknown or empty retraction modes")
    frames = []
    for mode in retraction_modes:
        suffix = "" if mode == "all" else "_exclude_snapshot"
        for numerator in ("RAW", "REF"):
            c_column = f"c_{numerator.lower()}{suffix}"
            for denominator in ("RAW", "REF"):
                d_column = f"d_{denominator.lower()}"
                frame = counts.copy()
                frame["retraction_mode"] = mode
                frame["numerator_eligibility"] = numerator
                frame["denominator_eligibility"] = denominator
                frame["citations"] = frame[c_column]
                frame["citable_items"] = frame[d_column]
                numeric = frame[["citations", "citable_items"]].apply(pd.to_numeric, errors="raise")
                if not np.isfinite(numeric.to_numpy()).all() or (numeric < 0).any().any():
                    raise ValueError("Citation and denominator counts must be finite and nonnegative")
                frame["score"] = np.where(frame.citable_items > 0, frame.citations / frame.citable_items.replace(0, np.nan), np.nan)
                frame["score_status"] = np.where(frame.citable_items > 0, "defined", np.where(frame.d_raw > 0, "ref_denominator_collapse", "no_raw_citable_items"))
                frames.append(frame)
    return pd.concat(frames, ignore_index=True)

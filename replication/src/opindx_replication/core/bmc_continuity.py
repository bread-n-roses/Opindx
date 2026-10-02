"""Guard the BMC Family Practice -> BMC Primary Care source continuity.

The two titles are one serial continuation, not two simultaneous journals.
The September OpenAlex source carries both ISSNs but retains the old display
title. This module checks that exact situation before changing only the title.
Unexpected source IDs, ISSNs, keys, or extracted Work assignments require a
new review; future snapshots must not inherit this decision blindly.
"""

from __future__ import annotations

import hashlib
import json
from numbers import Integral
from typing import Any

import pandas as pd


SOURCE_ID = "https://openalex.org/S153257185"
PREDECESSOR_ISSN = "1471-2296"
CURRENT_ISSN = "2731-4553"
PREDECESSOR_TITLE = "BMC Family Practice"
CURRENT_TITLE = "BMC Primary Care"
NLM_CURRENT = "https://www.ncbi.nlm.nih.gov/nlmcatalog/124345"
NLM_PREDECESSOR = "https://www.ncbi.nlm.nih.gov/nlmcatalog/100967792"
VERSION = "bmc-continuity-v1"
REQUIRED_SOURCE_COLUMNS = {"source_id", "raw_issn_l", "source_type", "title", "issns"}
REQUIRED_WORK_COLUMNS = {"source_id", "raw_issn_l", "year", "work_count"}


def _issns(value: object) -> list[str]:
    if value is None or (not isinstance(value, (list, tuple)) and pd.api.types.is_scalar(value)
                         and pd.isna(value)):
        return []
    if isinstance(value, str) or not hasattr(value, "__iter__"):
        raise ValueError("BMC source ISSNs must be an array")
    values = list(value)
    if any(not isinstance(item, str) for item in values):
        raise ValueError("BMC source ISSNs must be strings")
    return sorted(set(item.strip().upper() for item in values if item.strip()))


def _text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    if not isinstance(value, str):
        raise ValueError("BMC source metadata must be text or missing")
    return value.strip() or None


def audit_bmc_sources(
    latest: pd.DataFrame,
    works_by_source_year: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Return a deterministic preflight report, optionally checking Works.

    ``works_by_source_year`` is an already extracted, grouped inventory with
    source_id/raw_issn_l/year/work_count. Its counts may change in a later
    snapshot; the identity preimages and source IDs may not change silently.
    """
    if not REQUIRED_SOURCE_COLUMNS.issubset(latest.columns):
        raise ValueError("Latest Sources lack columns required for BMC continuity review")
    relevant = []
    known_issns = {PREDECESSOR_ISSN, CURRENT_ISSN}
    known_titles = {PREDECESSOR_TITLE.casefold(), CURRENT_TITLE.casefold()}
    for row in latest.itertuples(index=False):
        source_id = _text(getattr(row, "source_id"))
        raw = _text(getattr(row, "raw_issn_l"))
        title = _text(getattr(row, "title"))
        aliases = _issns(getattr(row, "issns"))
        if (source_id == SOURCE_ID or raw in known_issns
                or known_issns.intersection(aliases)
                or (title is not None and title.casefold() in known_titles)):
            relevant.append({
                "source_id": source_id,
                "raw_issn_l": raw,
                "source_type": _text(getattr(row, "source_type")),
                "title": title,
                "issns": aliases,
            })
    relevant.sort(key=lambda row: (row["source_id"] or "", row["title"] or ""))
    reasons = []
    if len(relevant) != 1 or relevant[0]["source_id"] != SOURCE_ID:
        reasons.append("expected_single_continuation_source_changed")
    if len(relevant) == 1:
        row = relevant[0]
        if row["raw_issn_l"] != PREDECESSOR_ISSN:
            reasons.append("source_issn_l_changed")
        if row["issns"] != sorted(known_issns):
            reasons.append("source_issn_aliases_changed")
        if row["source_type"] != "journal":
            reasons.append("source_type_changed")
        if row["title"] not in {PREDECESSOR_TITLE, CURRENT_TITLE}:
            reasons.append("source_title_changed")

    work_rows = []
    if works_by_source_year is not None:
        if not REQUIRED_WORK_COLUMNS.issubset(works_by_source_year.columns):
            raise ValueError("BMC Works inventory lacks required columns")
        for row in works_by_source_year.itertuples(index=False):
            source_id = _text(getattr(row, "source_id"))
            raw = _text(getattr(row, "raw_issn_l"))
            year = getattr(row, "year")
            count = getattr(row, "work_count")
            if (isinstance(year, bool) or not isinstance(year, Integral)
                    or isinstance(count, bool) or not isinstance(count, Integral) or count < 0):
                raise ValueError("BMC Works inventory needs integer years and nonnegative counts")
            if (source_id == SOURCE_ID or raw in known_issns) and count:
                work_rows.append({"source_id": source_id, "raw_issn_l": raw,
                                  "year": int(year), "work_count": int(count)})
                if source_id != SOURCE_ID or raw != PREDECESSOR_ISSN:
                    reasons.append("work_source_or_issn_l_changed")
        work_rows.sort(key=lambda row: (row["year"], row["source_id"], row["raw_issn_l"]))
        if not work_rows:
            reasons.append("no_bmc_works_in_extracted_inventory")
    reasons = sorted(set(reasons))
    payload = {"version": VERSION, "sources": relevant, "works_by_source_year": work_rows}
    return {
        "version": VERSION,
        "status": "REVIEW_REQUIRED" if reasons else "APPROVED_CONTINUITY",
        "review_reasons": reasons,
        "evidence_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest(),
        "observed_sources": relevant,
        "observed_works_by_source_year": work_rows,
        "observed_work_count": sum(row["work_count"] for row in work_rows),
        "decision": {
            "graph_issn_l": PREDECESSOR_ISSN,
            "current_display_title": CURRENT_TITLE,
            "current_title_issn": CURRENT_ISSN,
            "predecessor_title": PREDECESSOR_TITLE,
            "predecessor_issn": PREDECESSOR_ISSN,
            "transition_year": 2022,
            "norwegian_current_journal_id": "503436",
            "norwegian_predecessor_journal_id": "438548",
            "nlm_current_url": NLM_CURRENT,
            "nlm_predecessor_url": NLM_PREDECESSOR,
        },
    }


def adjust_latest_sources(latest: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Correct only the validated old display title in latest Sources."""
    report = audit_bmc_sources(latest)
    if report["status"] != "APPROVED_CONTINUITY":
        raise ValueError("BMC continuation needs review: " + ", ".join(report["review_reasons"]))
    corrected = latest.copy()
    selected = corrected.source_id.eq(SOURCE_ID)
    if int(selected.sum()) != 1:
        raise ValueError("BMC source ID must occur exactly once")
    old_title = corrected.loc[selected, "title"].iloc[0]
    corrected.loc[selected, "title"] = CURRENT_TITLE
    return corrected, {**report, "metadata_changed": old_title != CURRENT_TITLE,
                       "old_display_title": old_title, "new_display_title": CURRENT_TITLE}

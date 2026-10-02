"""Select an explicitly reviewed public Sources arm after identity projection.

This adapter changes only effective Sources display metadata and its collision
audit. It never changes the annual identity map, endpoint keys, graph mass, or
the per-source provenance used for Norwegian ISSN matching.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import re
from typing import Any, Iterable

import pandas as pd

from identity import AnnualIdentityMaps, _valid_issn_l
from sources import DISPLAY_COLUMNS, _attribute


@dataclass(frozen=True)
class ReviewedRepresentative:
    score_year: int
    source_id: str
    canonical_source_id: str
    issn_l: str
    expected_component_source_ids: tuple[str, ...]
    review_status: str
    evidence: str

    def validate(self, year: int) -> None:
        ids = self.expected_component_source_ids
        if (self.score_year != year or self.review_status != "approved_for_score_year"
                or not isinstance(self.source_id, str) or not self.source_id.strip()
                or not isinstance(self.canonical_source_id, str) or not self.canonical_source_id.strip()
                or self.source_id == self.canonical_source_id
                or not isinstance(self.issn_l, str) or not _valid_issn_l(self.issn_l)
                or not isinstance(ids, tuple) or len(ids) < 2
                or any(not isinstance(value, str) or not value.strip() for value in ids)
                or ids != tuple(sorted(set(ids)))
                or self.source_id not in ids or self.canonical_source_id not in ids
                or not isinstance(self.evidence, str) or not self.evidence.strip()):
            raise ValueError("Canonical Source representative needs approved, closed annual component evidence")


def select_canonical_sources(
    year: int,
    effective: pd.DataFrame,
    provenance: pd.DataFrame,
    source_audit: dict[str, Any],
    maps: AnnualIdentityMaps,
    decisions: Iterable[ReviewedRepresentative],
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Return adjusted effective metadata/audit, leaving provenance untouched.

    The selected Source must be an existing canonical row at the exact
    corrected effective ISSN-L. The corrected source's identity and Work mass
    stay in the component; only its public metadata candidacy is suppressed.
    Every component ID must be named in the decision, so a new colliding Source
    fails closed instead of silently changing the display representative.
    """
    maps.validate(year)
    selected = tuple(decisions)
    if not selected:
        return effective, source_audit, {"score_year": year, "decisions_applied": [], "provenance_changed": False}
    required_effective = {"issn_l", "source_ids", "source_rows", "canonical_arm_rows"}
    required_provenance = {"score_year", "source_id", "raw_issn_l", "issn_l",
                           "corrected_source_issn_l", "is_canonical_arm", *DISPLAY_COLUMNS,
                           "updated_date"}
    if not required_effective <= set(effective.columns) or not required_provenance <= set(provenance.columns):
        raise ValueError("Projected Sources metadata/provenance lacks canonical-arm evidence")
    if effective.issn_l.isna().any() or effective.issn_l.duplicated().any():
        raise ValueError("Effective Sources identities must be unique")
    if provenance.source_id.isna().any() or provenance.source_id.duplicated().any():
        raise ValueError("Projected Sources provenance must have unique source IDs")
    if not provenance.score_year.eq(year).all():
        raise ValueError("Projected Sources provenance has a different year")
    updated = effective.copy(deep=True)
    audit = copy.deepcopy(source_audit)
    applied: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for decision in selected:
        if not isinstance(decision, ReviewedRepresentative):
            raise TypeError("Representative decisions must be ReviewedRepresentative records")
        decision.validate(year)
        rule = maps.source_id_override.get(decision.source_id)
        if rule is None or rule.target_issn_l != decision.issn_l:
            raise ValueError("Representative decision lacks the matching source-specific annual correction")
        if decision.issn_l in used_keys:
            raise ValueError("Representative decisions repeat an effective component")
        used_keys.add(decision.issn_l)
        matches = updated.index[updated.issn_l.eq(decision.issn_l)]
        if len(matches) != 1:
            raise ValueError("Approved representative effective key is absent or repeated")
        index = matches[0]
        row = updated.loc[index]
        component_ids = row.source_ids
        if (not isinstance(component_ids, tuple)
                or tuple(sorted(component_ids)) != decision.expected_component_source_ids
                or row.source_rows != len(component_ids)):
            raise ValueError("Projected Sources component IDs differ from the closed representative decision")
        group = provenance.loc[provenance.issn_l.eq(decision.issn_l)].copy()
        if (len(group) != len(component_ids)
                or tuple(sorted(group.source_id)) != decision.expected_component_source_ids):
            raise ValueError("Projected Sources provenance differs from the closed component")
        corrected = group.loc[group.source_id.eq(decision.source_id)]
        canonical = group.loc[group.source_id.eq(decision.canonical_source_id)]
        canonical_arms = set(group.loc[group.is_canonical_arm, "source_id"])
        if (len(corrected) != 1 or len(canonical) != 1
                or canonical_arms != {decision.source_id, decision.canonical_source_id}
                or corrected.iloc[0].issn_l != decision.issn_l
                or canonical.iloc[0].issn_l != decision.issn_l
                or canonical.iloc[0].corrected_source_issn_l != decision.issn_l
                or not isinstance(canonical.iloc[0].raw_issn_l, str)
                or canonical.iloc[0].raw_issn_l.strip().upper() != decision.issn_l
                or row.canonical_arm_rows != 2):
            raise ValueError("Selected canonical Source ID/key is absent, mismatched, or not the sole alternative arm")
        # The local copy is used only to re-run the existing Sources attribute
        # selection rule. The caller's provenance remains byte-for-byte intact.
        group.loc[group.source_id.eq(decision.source_id), "is_canonical_arm"] = False
        selected_id, resolution = _attribute(group, "source_id")
        if selected_id != decision.canonical_source_id or resolution != "canonical_arm":
            raise AssertionError("Canonical Source representative selection failed")
        openalex_id = re.fullmatch(r"(?:https?://openalex\.org/)?(S[0-9]+)", selected_id)
        if openalex_id is None:
            raise ValueError("Selected canonical Source ID is not a valid OpenAlex Source ID")
        updated.at[index, "canonical_arm_rows"] = 1
        updated.at[index, "openalex_id"] = openalex_id.group(1)
        updated.at[index, "openalex_url"] = f"https://openalex.org/{openalex_id.group(1)}"
        updated.at[index, "openalex_id_resolution"] = resolution
        updated.at[index, "updated_date"] = canonical.iloc[0].updated_date
        resolutions: dict[str, str] = {}
        for column in DISPLAY_COLUMNS:
            value, method = _attribute(group, column)
            updated.at[index, column] = value
            updated.at[index, f"{column}_resolution"] = method
            resolutions[column] = method
        updated.at[index, "journal_title"] = updated.at[index, "title"]
        collision = [item for item in audit.get("identity_collisions", [])
                     if item.get("issn_l") == decision.issn_l]
        if len(collision) != 1 or set(collision[0].get("source_ids", [])) != set(component_ids):
            raise ValueError("Sources collision audit lacks the closed approved component")
        collision[0].update({"canonical_arm_rows": 1, "attribute_resolutions": resolutions,
                             "source_type_selected": updated.at[index, "source_type"],
                             "openalex_id_resolution": resolution,
                             "reviewed_canonical_source_id": decision.canonical_source_id})
        applied.append({"issn_l": decision.issn_l, "source_id": decision.source_id,
                        "canonical_source_id": decision.canonical_source_id,
                        "expected_component_source_ids": list(component_ids),
                        "evidence": decision.evidence})
    audit["oa_journal_nodes"] = int(updated.source_type.eq("journal").sum())
    audit["multiple_source_type_nodes"] = int(updated.source_type.eq("Multiple source types").sum())
    audit["missing_canonical_arm_nodes"] = int(updated.canonical_arm_rows.eq(0).sum())
    audit["multiple_canonical_arm_nodes"] = int(updated.canonical_arm_rows.gt(1).sum())
    audit["journal_nodes_missing_unique_canonical_source"] = int((
        updated.source_type.eq("journal")
        & (updated.canonical_arm_rows.ne(1) | updated.openalex_id.isna())
    ).sum())
    audit["reviewed_representative_decisions"] = applied
    return updated, audit, {"score_year": year, "decisions_applied": applied,
                            "provenance_changed": False}

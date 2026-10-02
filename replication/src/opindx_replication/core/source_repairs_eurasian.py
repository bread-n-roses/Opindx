"""Review and repair the two Eurasian journals before annual graph extraction.

The September 2026 OpenAlex Sources row S4210169982 carries both journals'
ISSNs, and its Works use that ID and the Economic ISSN-L. A DOI identifies the
publisher journal for almost every affected Work. An unfamiliar DOI or changed
Sources configuration is a review stop, not a reason to guess a journal.

This module deliberately has no file writes. A dated run must persist and hash
its audit and Work override ledger before building corrected endpoints.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re

import pandas as pd


BUSINESS_ID = "https://openalex.org/S2764805720"
ECONOMIC_ID = "https://openalex.org/S4210169982"
BUSINESS_ISSN_L = "1309-4297"
ECONOMIC_ISSN_L = "1309-422X"
BUSINESS_ISSNS = frozenset(("1309-4297", "2147-4281"))
ECONOMIC_ISSNS = frozenset(("1309-422X", "2147-429X"))
ALL_ISSNS = BUSINESS_ISSNS | ECONOMIC_ISSNS

LEDGER_COLUMNS = (
    "work_id", "expected_source_id", "expected_raw_issn_l",
    "target_source_id", "target_raw_issn_l", "reason",
)
_DOI = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?(10\.[^\s]+)$", re.I)
_SPRINGER = re.compile(r"^10\.1007/(s40821|s40822)-", re.I)


@dataclass(frozen=True)
class ReviewedException:
    """A fixed Work/DOI decision with a verifiable publisher or record URL."""

    doi: str
    target_source_id: str
    evidence_url: str


# Future unfamiliar DOI cases require a reviewed, Work-specific decision.
# The September Zenodo item is a duplicate accepted manuscript and is handled
# by build_exclusions rather than being assigned as a second journal article.
REVIEWED_EXCEPTIONS: Mapping[str, ReviewedException] = {}
ZENODO_WORK_ID = "https://openalex.org/W7134849951"
ZENODO_DOI = "10.5281/zenodo.15554201"
PUBLISHED_WORK_ID = "https://openalex.org/W4410220348"
PUBLISHED_DOI = "10.1007/s40821-025-00302-0"
ZENODO_TITLE = "Are artificial intelligence skills a reward or a gamble? Deconstructing the AI wage premium in Europe"
ZENODO_EVIDENCE_URL = "https://zenodo.org/records/15554202"
EXCLUSION_COLUMNS = ("work_id", "expected_source_id", "expected_raw_issn_l", "reason")


def _doi(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    matched = _DOI.fullmatch(str(value).strip())
    return matched.group(1).lower() if matched else None


def classify_work(
    work_id: str, doi: object,
    exceptions: Mapping[str, ReviewedException] = REVIEWED_EXCEPTIONS,
) -> tuple[str, str]:
    """Return a verified target ID and reason, or stop for human review."""
    normalized = _doi(doi)
    if exception := exceptions.get(work_id):
        if (normalized != _doi(exception.doi)
                or exception.target_source_id not in (BUSINESS_ID, ECONOMIC_ID)
                or not exception.evidence_url.startswith("https://")):
            raise ValueError(f"Reviewed Eurasian exception changed: {work_id}")
        return exception.target_source_id, "eurasian_reviewed_exception"
    match = _SPRINGER.match(normalized or "")
    if match is None:
        raise ValueError(f"Unresolved Eurasian Work DOI: {work_id} {doi!r}")
    if match.group(1).lower() == "s40821":
        return BUSINESS_ID, "eurasian_business_publisher_doi"
    return ECONOMIC_ID, "eurasian_economic_publisher_doi"


def build_ledger(
    metadata: pd.DataFrame,
    *,
    exceptions: Mapping[str, ReviewedException] = REVIEWED_EXCEPTIONS,
    expected_work_count: int | None = None,
) -> pd.DataFrame:
    """List changed Work source/key pairs after reviewing every affected Work.

    Input may include other Sources' metadata. Both known historical OpenAlex
    IDs are included, so an already-separated snapshot produces no overrides.
    This function cannot discover an entirely new Source ID; preflight must
    separately audit Sources rows containing either journal's ISSNs.
    """
    required = {"work_id", "source_id", "raw_issn_l", "doi"}
    if missing := sorted(required - set(metadata.columns)):
        raise ValueError(f"Eurasian Work metadata lacks columns: {missing}")
    works = metadata.loc[metadata.source_id.isin((BUSINESS_ID, ECONOMIC_ID))]
    if expected_work_count is not None and len(works) != expected_work_count:
        raise ValueError(f"Eurasian Work count changed: {len(works)}")
    if works.work_id.isna().any() or works.work_id.duplicated().any():
        raise ValueError("Eurasian Work IDs must be present and unique")
    permitted = BUSINESS_ISSNS | ECONOMIC_ISSNS
    if any(raw not in permitted for raw in works.raw_issn_l):
        raise ValueError("Eurasian Work has an unexpected raw ISSN-L")
    if any(key not in set(works.work_id) for key in exceptions):
        raise ValueError("Reviewed Eurasian exception Work is absent from metadata")
    excluded = set(build_exclusions(metadata).work_id)
    ledger = []
    for row in works.itertuples(index=False):
        if row.work_id in excluded:
            continue
        target_id, reason = classify_work(row.work_id, row.doi, exceptions)
        target_key = BUSINESS_ISSN_L if target_id == BUSINESS_ID else ECONOMIC_ISSN_L
        if (row.source_id, row.raw_issn_l) != (target_id, target_key):
            ledger.append((row.work_id, row.source_id, row.raw_issn_l,
                           target_id, target_key, reason))
    return pd.DataFrame(ledger, columns=LEDGER_COLUMNS).sort_values(
        "work_id", kind="stable").reset_index(drop=True)


def build_exclusions(metadata: pd.DataFrame) -> pd.DataFrame:
    """Exclude the separately indexed Zenodo accepted manuscript, if present.

    Zenodo calls it an accepted version of the Springer Business article with
    DOI 10.1007/s40821-025-00302-0. Both Work IDs are present in September.
    The journal Version of Record remains; counting the repository copy again
    would inflate the article denominator. If either record changes, stop.
    """
    required = {"work_id", "source_id", "raw_issn_l", "doi", "title"}
    if missing := sorted(required - set(metadata.columns)):
        raise ValueError(f"Eurasian duplicate review lacks columns: {missing}")
    zenodo = metadata.loc[metadata.work_id.eq(ZENODO_WORK_ID)]
    if zenodo.empty:
        return pd.DataFrame(columns=EXCLUSION_COLUMNS)
    published = metadata.loc[metadata.work_id.eq(PUBLISHED_WORK_ID)]
    if len(zenodo) != 1 or len(published) != 1:
        raise ValueError("Eurasian duplicate/version Work IDs changed")
    copy = zenodo.iloc[0]
    record = published.iloc[0]
    if (_doi(copy.doi) != ZENODO_DOI or _doi(record.doi) != PUBLISHED_DOI
            or copy.title != ZENODO_TITLE or record.title != ZENODO_TITLE
            or copy.source_id not in (BUSINESS_ID, ECONOMIC_ID)
            or record.source_id not in (BUSINESS_ID, ECONOMIC_ID)
            or copy.raw_issn_l not in ALL_ISSNS
            or record.raw_issn_l not in ALL_ISSNS):
        raise ValueError("Eurasian Zenodo/Version of Record duplicate changed")
    return pd.DataFrame([{
        "work_id": copy.work_id,
        "expected_source_id": copy.source_id,
        "expected_raw_issn_l": copy.raw_issn_l,
        "reason": "eurasian_zenodo_duplicate_accepted_manuscript",
    }], columns=EXCLUSION_COLUMNS)


def repair_latest_sources(latest: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    """Split the known merged Sources row; verify already-separated snapshots.

    Both output rows retain genuine historical OpenAlex IDs. The absent
    Business row is reconstructed from the merged row's current shared
    publisher/domain metadata and the established journal-specific identifiers.
    """
    required = {"source_id", "raw_issn_l", "source_type", "title", "publisher",
                "issns", "oa_field", "oa_domain", "is_open_access", "updated_date"}
    if missing := sorted(required - set(latest.columns)):
        raise ValueError(f"Latest Sources lack columns: {missing}")
    if latest.source_id.isna().any() or latest.source_id.duplicated().any():
        raise ValueError("Latest Sources must have unique IDs")
    title = latest.title.fillna("").str.lower()
    relevant = latest.loc[
        latest.source_id.isin((BUSINESS_ID, ECONOMIC_ID))
        | latest.issns.map(lambda value: bool(set(value) & ALL_ISSNS))
        | title.str.contains(r"eurasian (?:business|economic) review", regex=True)
    ]
    actual_ids = set(relevant.source_id)
    allowed_ids = {BUSINESS_ID, ECONOMIC_ID}
    if not actual_ids or not actual_ids <= allowed_ids:
        raise ValueError(f"Eurasian ISSNs moved to unreviewed Source IDs: {actual_ids}")
    by_id = relevant.set_index("source_id", drop=False)
    if ECONOMIC_ID not in by_id.index:
        raise ValueError("Eurasian Economic Source ID disappeared")
    economic = by_id.loc[ECONOMIC_ID]
    if economic.source_type != "journal":
        raise ValueError("Eurasian Economic Source type changed")
    permitted_titles = ("eurasian business review", "eurasian economic review")
    if (not any(title in str(economic.title).lower() for title in permitted_titles)
            or economic.oa_field not in (
                "Business, Management and Accounting", "Economics, Econometrics and Finance")):
        raise ValueError("Eurasian Economic Source title or field changed")
    out = latest.copy(deep=True)
    if actual_ids == {ECONOMIC_ID}:
        if (set(economic.issns) != ALL_ISSNS
                or economic.raw_issn_l not in ECONOMIC_ISSNS):
            raise ValueError("Combined Eurasian Source ISSNs/key changed")
        business = economic.copy()
        business["source_id"] = BUSINESS_ID
        business["raw_issn_l"] = BUSINESS_ISSN_L
        business["issns"] = tuple(sorted(BUSINESS_ISSNS))
        business["title"] = "Eurasian Business Review"
        business["oa_field"] = "Business, Management and Accounting"
        out = pd.concat((out, pd.DataFrame([business])), ignore_index=True)
        state = "merged_source_split"
    elif actual_ids == allowed_ids:
        business = by_id.loc[BUSINESS_ID]
        if (set(business.issns) != BUSINESS_ISSNS
                or set(economic.issns) != ECONOMIC_ISSNS
                or business.raw_issn_l not in BUSINESS_ISSNS
                or economic.raw_issn_l not in ECONOMIC_ISSNS
                or business.source_type != "journal"
                or not any(title in str(business.title).lower() for title in permitted_titles)
                or business.oa_field != "Business, Management and Accounting"
                or economic.oa_field != "Economics, Econometrics and Finance"):
            raise ValueError("Separated Eurasian Source identifiers changed")
        state = "already_separate"
    else:
        raise ValueError(f"Incomplete Eurasian Source identity: {actual_ids}")
    for source_id, key, title, field, issns in (
        (BUSINESS_ID, BUSINESS_ISSN_L, "Eurasian Business Review",
         "Business, Management and Accounting", BUSINESS_ISSNS),
        (ECONOMIC_ID, ECONOMIC_ISSN_L, "Eurasian Economic Review",
         "Economics, Econometrics and Finance", ECONOMIC_ISSNS),
    ):
        selected = out.source_id.eq(source_id)
        out.loc[selected, "raw_issn_l"] = key
        out.loc[selected, "title"] = title
        out.loc[selected, "oa_field"] = field
        out.loc[selected, "issns"] = pd.Series(
            [tuple(sorted(issns))] * int(selected.sum()), index=out.index[selected], dtype=object)
    return out, {"source_state": state, "observed_source_ids": sorted(actual_ids),
                 "corrected_source_ids": sorted(allowed_ids),
                 "issn_groups_checked": True}

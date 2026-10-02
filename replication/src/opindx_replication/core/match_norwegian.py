"""Match annual Norwegian members to reviewed effective OpenAlex source keys.

Matching is exact on normalized print/online ISSNs and source ISSN aliases.
Titles never determine a match. Multiple source IDs that project to one key are
one candidate; competing canonical keys remain in an explicit review queue.
This module reads no files and neither changes identity maps nor writes rosters.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from identity import AnnualIdentityMaps, ISSN_L, _project_one, _valid_issn_l
from norwegian import NorwegianMembership, normalize_issn


NORWEGIAN_JOURNAL_URL = "https://kanalregister.hkdir.no/tidsskrift?id="


@dataclass(frozen=True)
class SourceAlias:
    score_year: int
    source_id: str
    canonical_issn_l: str
    issns: tuple[str, ...]
    ignored_invalid_issns: tuple[str, ...] = ()


GRAPH_MASS_COLUMNS = (
    "a_raw", "a_filtered", "citing_n_raw", "citing_n_filtered",
    "cited_n_raw", "cited_n_filtered", "retention_a_raw", "retention_a_filtered",
)


@dataclass(frozen=True)
class GraphAlias:
    """A positive annual Work source/key pair absent from projected Sources.

    Source ID is the observed Work ID, including None or blank when missing;
    it is never a fabricated Sources ID. The map hash and trace bind the raw
    endpoint identity to its reviewed annual effective identity.
    """

    score_year: int
    source_id: str | None
    raw_issn_l: str | None
    normalized_raw_issn_l: str
    canonical_issn_l: str
    raw_key_status: str
    a_raw: int
    a_filtered: int
    citing_n_raw: int
    citing_n_filtered: int
    cited_n_raw: int
    cited_n_filtered: int
    retention_a_raw: int
    retention_a_filtered: int
    source_override_applied: bool
    alias_applied: bool
    key_correction_applied: bool
    annual_map_sha256: str

    @property
    def observed_roles(self) -> tuple[str, ...]:
        return tuple(name for name, mass in (("article", self.a_raw),
                                              ("citing", self.citing_n_raw),
                                              ("cited", self.cited_n_raw)) if mass > 0)


@dataclass(frozen=True)
class ReviewedMatchOverride:
    score_year: int
    journal_id: str
    canonical_issn_l: str
    review_status: str
    evidence: str


@dataclass(frozen=True)
class ReviewedMatchDecision:
    """One annual register decision approving one or more effective identities.

    The ordered keys are reviewed together. No ambiguous candidate becomes a
    member merely because it appeared in an exact-ISSN or title search.
    """

    score_year: int
    journal_id: str
    canonical_issn_ls: tuple[str, ...]
    review_status: str
    evidence: str
    evidence_by_key: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedMembership:
    member: NorwegianMembership
    canonical_issn_l: str
    method: str
    candidate_keys: tuple[str, ...]
    matched_issns: tuple[str, ...]
    matched_source_ids: tuple[str, ...]
    override_evidence: str = ""
    matched_graph_aliases: tuple[GraphAlias, ...] = ()

    @property
    def norwegian_register_url(self) -> str:
        return register_url(self.member.journal_id)


@dataclass(frozen=True)
class MatchReview:
    member: NorwegianMembership
    reason: str
    candidate_keys: tuple[str, ...]
    matched_issns: tuple[str, ...]
    matched_source_ids: tuple[str, ...]
    matched_graph_aliases: tuple[GraphAlias, ...] = ()


@dataclass(frozen=True)
class MatchResult:
    resolved: tuple[ResolvedMembership, ...]
    review_queue: tuple[MatchReview, ...]


@dataclass(frozen=True)
class AnnualNorwegianIdentity:
    score_year: int
    issn_l: str
    members: tuple[ResolvedMembership, ...]

    @property
    def journal_ids(self) -> tuple[str, ...]:
        """Keep every original register ID, including its original spelling."""
        return tuple(item.member.journal_id for item in self.members)

    @property
    def norwegian_register_url(self) -> str:
        return "; ".join(item.norwegian_register_url for item in self.members)

    @property
    def norwegian_level(self) -> str:
        return _join_unique(str(item.member.norwegian_level) for item in self.members)

    @property
    def norwegian_area(self) -> str:
        return _join_unique(item.member.discipline for item in self.members)

    @property
    def norwegian_field(self) -> str:
        return _join_unique(item.member.field for item in self.members)


def _join_unique(values: Iterable[str]) -> str:
    """Preserve the June export's annual multi-record metadata convention."""
    return " | ".join(sorted({value.strip() for value in values if value.strip()}))


def _year(year: int) -> int:
    if type(year) is not int or not 1900 <= year <= 9997:
        raise ValueError("score_year must be an integer year between 1900 and 9997")
    return year


def _canonical(value: str) -> str:
    normalized = normalize_issn(value)
    if not normalized or not _valid_issn_l(normalized):
        raise ValueError(f"Invalid effective canonical ISSN-L: {value!r}")
    return normalized


def register_url(journal_id: str) -> str:
    """Build one link from a positive ASCII numeric register ID."""
    if (not isinstance(journal_id, str) or not journal_id.isascii()
            or not journal_id.isdecimal() or int(journal_id) <= 0):
        raise ValueError(f"Invalid Norwegian journal_id for a link: {journal_id!r}")
    return NORWEGIAN_JOURNAL_URL + str(int(journal_id))


def _records(value: Any) -> list[Mapping[str, Any]]:
    # Accept sources.project_sources()'s DataFrame or plain row mappings.
    rows = value.to_dict("records") if hasattr(value, "to_dict") else list(value)
    if any(not isinstance(row, Mapping) for row in rows):
        raise TypeError("Expected source/export row mappings or a DataFrame")
    return rows


def source_aliases_from_provenance(score_year: int, provenance: Any) -> tuple[SourceAlias, ...]:
    """Adapt sources.project_sources() provenance for one reviewed score year.

    Quarantined source rows are excluded. Malformed raw aliases cannot match a
    syntactically valid register ISSN; retain them as diagnostic values rather
    than losing an otherwise corrected source identity. No alias conflict is
    resolved here: several canonical targets for an ISSN remain several targets.
    """
    _year(score_year)
    required = {"source_id", "raw_issn_l", "corrected_source_issn_l", "issn_l",
                "issns", "quarantine_reason"}
    aliases: list[SourceAlias] = []
    seen: set[str] = set()
    for row in _records(provenance):
        if not required <= row.keys():
            raise ValueError(f"Source provenance lacks columns: {sorted(required - row.keys())}")
        if "score_year" in row and row["score_year"] != score_year:
            raise ValueError("Source provenance contains a different score year")
        if not isinstance(row["quarantine_reason"], str):
            raise ValueError("Source quarantine_reason must be explicit text")
        if row["quarantine_reason"]:
            continue
        source_id = row["source_id"]
        if not isinstance(source_id, str) or not source_id.strip() or source_id in seen:
            raise ValueError("Source provenance must contain unique, nonblank source IDs")
        seen.add(source_id)
        canonical = _canonical(row["issn_l"])
        if not isinstance(row["issns"], (tuple, list)):
            raise ValueError("Source issns must be an explicit alias sequence")
        raw_aliases = [row["raw_issn_l"], row["corrected_source_issn_l"], canonical, *row["issns"]]
        valid: set[str] = set()
        invalid: set[str] = set()
        for raw in raw_aliases:
            if raw is None:
                continue
            if not isinstance(raw, str):
                try:
                    if bool(pd.isna(raw)):
                        continue
                except (TypeError, ValueError):
                    pass
                invalid.add(str(raw))
                continue
            if raw == "":
                continue
            try:
                alias = normalize_issn(raw)
            except ValueError:
                invalid.add(str(raw))
            else:
                if alias:
                    valid.add(alias)
        aliases.append(SourceAlias(score_year, source_id, canonical,
                                   tuple(sorted(valid)), tuple(sorted(invalid))))
    return tuple(sorted(aliases, key=lambda item: item.source_id))


def _annual_map_hash(maps: AnnualIdentityMaps) -> str:
    payload = {
        "score_year": maps.score_year,
        "review_status": maps.review_status,
        "source_id_override": sorted((source, list(rule.expected_raw_issn_ls), rule.target_issn_l)
                                     for source, rule in maps.source_id_override.items()),
        "alias_to_canonical": sorted(maps.alias_to_canonical.items()),
        "key_correction": sorted(maps.key_correction.items()),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _raw_status(normalized: str) -> str:
    if not normalized:
        return "missing"
    if not ISSN_L.fullmatch(normalized):
        return "invalid_syntax"
    return "valid" if _valid_issn_l(normalized) else "invalid_checksum"


def _mass(value: object, name: str) -> int:
    if isinstance(value, bool) or pd.isna(value):
        raise ValueError(f"Annual pair {name} must be a nonnegative integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Annual pair {name} must be a nonnegative integer") from error
    if number < 0 or value != number:
        raise ValueError(f"Annual pair {name} must be a nonnegative integer")
    return number


def _graph_sort(alias: GraphAlias) -> tuple[str, str, str]:
    return alias.source_id or "", alias.raw_issn_l or "", alias.canonical_issn_l


def graph_aliases_from_annual_pairs(
    score_year: int,
    annual_pairs: pd.DataFrame,
    source_provenance: pd.DataFrame,
    maps: AnnualIdentityMaps,
) -> tuple[GraphAlias, ...]:
    """Adapt one audit_pairs annual slice without inventing Sources identities.

    Every positive Work pair is projected through the approved annual map.
    Source ID conflicts fail even when the Work key also appears elsewhere in
    Sources. Only keys absent from all valid projected Sources become graph
    aliases; role masses and the raw-to-effective map trace remain attached.
    """
    _year(score_year)
    maps.validate(score_year)
    pair_required = {"score_year", "source_id", "raw_issn_l", "normalized_raw_issn_l",
                     "raw_key_status", "source_id_missing", *GRAPH_MASS_COLUMNS}
    source_required = {"score_year", "source_id", "raw_issn_l", "issn_l", "quarantine_reason"}
    if missing := sorted(pair_required - set(annual_pairs.columns)):
        raise ValueError(f"Annual pair audit lacks columns: {missing}")
    if missing := sorted(source_required - set(source_provenance.columns)):
        raise ValueError(f"Projected Sources provenance lacks columns: {missing}")
    source_key_by_id: dict[str, str] = {}
    source_effective_keys: set[str] = set()
    for year, source_id, raw, effective, quarantine in source_provenance.loc[
            :, ["score_year", "source_id", "raw_issn_l", "issn_l", "quarantine_reason"]].itertuples(index=False, name=None):
        if year != score_year or not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("Projected Sources provenance has wrong year or missing source ID")
        if source_id in source_key_by_id:
            raise ValueError("Projected Sources provenance repeats a source ID")
        projected = _project_one(source_id, raw, maps)
        if effective != projected or not isinstance(quarantine, str):
            raise ValueError("Projected Sources provenance does not use the supplied annual map")
        valid = _valid_issn_l(projected)
        if bool(quarantine) == valid:
            raise ValueError("Projected Sources quarantine status disagrees with effective key")
        source_key_by_id[source_id] = projected
        if valid:
            source_effective_keys.add(projected)
    map_hash = _annual_map_hash(maps)
    aliases: list[GraphAlias] = []
    seen_pairs: set[tuple[str | None, str | None]] = set()
    work_keys_by_source: dict[str, str] = {}
    for pair in annual_pairs.itertuples(index=False):
        if pair.score_year != score_year:
            raise ValueError("Annual pair audit contains a different score year")
        source_id = pair.source_id
        if pd.isna(source_id):
            source_id = None
        elif not isinstance(source_id, str):
            raise ValueError("Annual pair source ID must be text or missing")
        raw = pair.raw_issn_l
        if pd.isna(raw):
            raw = None
        elif not isinstance(raw, str):
            raise ValueError("Annual pair raw ISSN-L must be text or missing")
        normalized = "" if raw is None else raw.strip().upper()
        if (pair.normalized_raw_issn_l != normalized
                or pair.raw_key_status != _raw_status(normalized)):
            raise ValueError("Annual pair raw-key diagnostics disagree with the raw value")
        if (not isinstance(pair.source_id_missing, (bool, np.bool_))
                or bool(pair.source_id_missing) != (source_id is None or not source_id.strip())):
            raise ValueError("Annual pair source-ID-missing diagnostic disagrees with the raw ID")
        pair_id = source_id, raw
        if pair_id in seen_pairs:
            raise ValueError("Annual pair audit repeats a source/raw-key pair")
        seen_pairs.add(pair_id)
        mass = {name: _mass(getattr(pair, name), name) for name in GRAPH_MASS_COLUMNS}
        for raw_name, filtered_name in (("a_raw", "a_filtered"),
                                        ("citing_n_raw", "citing_n_filtered"),
                                        ("cited_n_raw", "cited_n_filtered"),
                                        ("retention_a_raw", "retention_a_filtered")):
            if mass[filtered_name] > mass[raw_name]:
                raise ValueError("Annual pair Filtered mass exceeds Raw mass")
        if (mass["a_raw"] != mass["retention_a_raw"]
                or mass["a_filtered"] != mass["retention_a_filtered"]):
            raise ValueError("Annual pair article mass does not reconcile to retention")
        if not any(mass[name] for name in ("a_raw", "citing_n_raw", "cited_n_raw")):
            raise ValueError("Annual pair audit contains a zero-mass pair")
        effective = _project_one(source_id, raw, maps)
        if not _valid_issn_l(effective):
            raise ValueError("Positive annual Work pair projects to an invalid effective ISSN-L")
        if source_id and source_id in source_key_by_id and source_key_by_id[source_id] != effective:
            raise ValueError("Work source ID conflicts with projected Sources identity")
        if source_id:
            previous = work_keys_by_source.setdefault(source_id, effective)
            if previous != effective:
                raise ValueError("Work source ID projects to multiple effective keys")
        if effective in source_effective_keys:
            continue
        source_override = maps.source_id_override.get(source_id or "")
        from_source = source_override.target_issn_l if source_override else normalized
        from_alias = maps.alias_to_canonical.get(from_source, from_source)
        from_key_correction = maps.key_correction.get(from_alias, from_alias)
        if from_key_correction != effective:
            raise AssertionError("Graph alias map trace differs from identity projection")
        aliases.append(GraphAlias(
            score_year, source_id, raw, normalized, effective, pair.raw_key_status,
            *(mass[name] for name in GRAPH_MASS_COLUMNS),
            source_override is not None, from_alias != from_source,
            effective != from_alias, map_hash,
        ))
    return tuple(sorted(aliases, key=lambda item: (item.canonical_issn_l, *_graph_sort(item))))


def match_memberships(
    memberships: Iterable[NorwegianMembership],
    effective_sources: Iterable[SourceAlias],
    reviewed_overrides: Iterable[ReviewedMatchOverride | ReviewedMatchDecision] = (),
    graph_aliases: Iterable[GraphAlias] = (),
) -> MatchResult:
    """Resolve singleton annual exact-ISSN matches, queuing every other member.

    The union of print and online candidates is used; a unique print match never
    silently overrules a conflicting online match. An override needs annual
    approval and evidence, and may name only canonical identities present in that
    year's effective Sources or positive graph-only keys. Graph candidates are
    exact checksum-valid raw/effective ISSNs and retain Work-role mass separately
    from Sources aliases. One annual decision may approve several keys, but
    ambiguous candidates are never accepted without that decision. Unused,
    repeated, or stale decisions fail explicitly.
    """
    members: dict[tuple[int, str], NorwegianMembership] = {}
    numeric_ids: dict[tuple[int, int], str] = {}
    for member in memberships:
        if not isinstance(member, NorwegianMembership):
            raise TypeError("memberships must contain NorwegianMembership records")
        _year(member.score_year)
        register_url(member.journal_id)
        key = member.score_year, member.journal_id
        numeric_key = member.score_year, int(member.journal_id)
        if numeric_key in numeric_ids and numeric_ids[numeric_key] != member.journal_id:
            raise ValueError("Two register ID spellings refer to the same annual journal ID")
        numeric_ids[numeric_key] = member.journal_id
        if key in members and members[key] != member:
            raise ValueError("Conflicting duplicate annual Norwegian membership")
        members[key] = member
    index: dict[tuple[int, str], dict[str, set[str]]] = {}
    canonical_by_year: dict[int, set[str]] = {}
    sources_seen: set[tuple[int, str]] = set()
    source_key_by_id: dict[tuple[int, str], str] = {}
    for source in effective_sources:
        if not isinstance(source, SourceAlias):
            raise TypeError("effective_sources must contain SourceAlias records")
        year = _year(source.score_year)
        if not isinstance(source.source_id, str) or not source.source_id.strip():
            raise ValueError("SourceAlias source_id must be nonblank")
        source_key = year, source.source_id
        if source_key in sources_seen:
            raise ValueError("Duplicate annual source ID in effective_sources")
        sources_seen.add(source_key)
        canonical = _canonical(source.canonical_issn_l)
        source_key_by_id[source_key] = canonical
        canonical_by_year.setdefault(year, set()).add(canonical)
        if not isinstance(source.issns, (tuple, list)):
            raise ValueError("SourceAlias issns must be an alias sequence")
        for raw in (*source.issns, canonical):
            alias = normalize_issn(raw)
            if alias:
                index.setdefault((year, alias), {}).setdefault(canonical, set()).add(source.source_id)
    source_canonical_by_year = {year: set(keys) for year, keys in canonical_by_year.items()}
    graph_index: dict[tuple[int, str], dict[str, set[GraphAlias]]] = {}
    graph_by_key: dict[tuple[int, str], set[GraphAlias]] = {}
    graph_seen: set[tuple[int, str | None, str | None]] = set()
    graph_map_by_year: dict[int, str] = {}
    graph_keys_by_source: dict[tuple[int, str], str] = {}
    for graph in graph_aliases:
        if not isinstance(graph, GraphAlias):
            raise TypeError("graph_aliases must contain GraphAlias records")
        year = _year(graph.score_year)
        canonical = _canonical(graph.canonical_issn_l)
        if (canonical in source_canonical_by_year.get(year, set())
                or not isinstance(graph.annual_map_sha256, str)
                or len(graph.annual_map_sha256) != 64
                or any(character not in "0123456789abcdef" for character in graph.annual_map_sha256)):
            raise ValueError("Graph alias must be graph-only and bound to an annual map")
        if year in graph_map_by_year and graph_map_by_year[year] != graph.annual_map_sha256:
            raise ValueError("Graph aliases for one year use different annual maps")
        graph_map_by_year[year] = graph.annual_map_sha256
        graph_mass = {name: _mass(getattr(graph, name), name) for name in GRAPH_MASS_COLUMNS}
        if (graph_mass["a_filtered"] > graph_mass["a_raw"]
                or graph_mass["citing_n_filtered"] > graph_mass["citing_n_raw"]
                or graph_mass["cited_n_filtered"] > graph_mass["cited_n_raw"]
                or graph_mass["retention_a_filtered"] > graph_mass["retention_a_raw"]
                or graph_mass["a_raw"] != graph_mass["retention_a_raw"]
                or graph_mass["a_filtered"] != graph_mass["retention_a_filtered"]
                or not any(graph_mass[name] for name in ("a_raw", "citing_n_raw", "cited_n_raw"))):
            raise ValueError("Graph alias must retain positive, reconciled Raw/Filtered role mass")
        if graph.raw_issn_l is not None and not isinstance(graph.raw_issn_l, str):
            raise ValueError("Graph alias raw ISSN-L must be text or missing")
        if graph.source_id is not None and not isinstance(graph.source_id, str):
            raise ValueError("Graph alias source ID must be text or missing")
        normalized = "" if graph.raw_issn_l is None else graph.raw_issn_l.strip().upper()
        if (graph.normalized_raw_issn_l != normalized
                or graph.raw_key_status != _raw_status(normalized)):
            raise ValueError("Graph alias raw-key diagnostics disagree with its raw identity")
        pair = year, graph.source_id, graph.raw_issn_l
        if pair in graph_seen:
            raise ValueError("Duplicate annual graph source/raw-key pair")
        graph_seen.add(pair)
        if graph.source_id:
            source_key = year, graph.source_id
            if source_key in source_key_by_id:
                raise ValueError("Graph alias duplicates a projected Sources source ID")
            previous = graph_keys_by_source.setdefault(source_key, canonical)
            if previous != canonical:
                raise ValueError("Graph-only Work source ID has conflicting effective keys")
        canonical_by_year.setdefault(year, set()).add(canonical)
        graph_by_key.setdefault((year, canonical), set()).add(graph)
        for alias in {canonical, normalized}:
            if _valid_issn_l(alias):
                graph_index.setdefault((year, alias), {}).setdefault(canonical, set()).add(graph)
    overrides: dict[tuple[int, str], tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for override in reviewed_overrides:
        if not isinstance(override, (ReviewedMatchOverride, ReviewedMatchDecision)):
            raise TypeError("reviewed_overrides must contain reviewed annual match decisions")
        key = _year(override.score_year), override.journal_id
        if key not in members or key in overrides:
            raise ValueError("Unused or duplicate annual Norwegian match override")
        if (override.review_status != "approved_for_score_year"
                or not isinstance(override.evidence, str) or not override.evidence.strip()):
            raise ValueError("Match overrides require annual approval and nonblank review evidence")
        if isinstance(override, ReviewedMatchOverride):
            raw_targets = (override.canonical_issn_l,)
            per_key_evidence = (override.evidence,)
        else:
            raw_targets = override.canonical_issn_ls
            if not isinstance(raw_targets, tuple) or not raw_targets:
                raise ValueError("Reviewed match decision requires a nonempty tuple of canonical keys")
            per_key_evidence = override.evidence_by_key
            if (not isinstance(per_key_evidence, tuple)
                    or len(per_key_evidence) != len(raw_targets)
                    or any(not isinstance(value, str) or not value.strip()
                           for value in per_key_evidence)):
                raise ValueError("Reviewed match decision requires nonblank evidence for every key")
        targets = tuple(_canonical(target) for target in raw_targets)
        if len(set(targets)) != len(targets):
            raise ValueError("Reviewed match decision repeats a canonical key")
        if any(target not in canonical_by_year.get(override.score_year, set()) for target in targets):
            raise ValueError("Override target is absent from that year's effective source identities")
        overrides[key] = targets, per_key_evidence
    resolved: list[ResolvedMembership] = []
    queue: list[MatchReview] = []
    for key in sorted(members, key=lambda item: (item[0], int(item[1]))):
        member = members[key]
        register_issns = {normalize_issn(member.print_issn), normalize_issn(member.online_issn)} - {""}
        candidates: set[str] = set()
        matched_issns: set[str] = set()
        matched_sources: set[str] = set()
        for alias in register_issns:
            hits = index.get((member.score_year, alias), {})
            graph_hits = graph_index.get((member.score_year, alias), {})
            if hits or graph_hits:
                matched_issns.add(alias)
            candidates.update(hits)
            candidates.update(graph_hits)
            for sources in hits.values():
                matched_sources.update(sources)
        evidence = tuple(sorted(candidates)), tuple(sorted(matched_issns)), tuple(sorted(matched_sources))
        if key in overrides:
            targets, reasons = overrides[key]
            method = "reviewed_multi_key" if len(targets) > 1 else "reviewed_override"
            resolved.extend(ResolvedMembership(
                member, target, method, *evidence, reason,
                tuple(sorted(graph_by_key.get((member.score_year, target), ()), key=_graph_sort)),
            ) for target, reason in zip(targets, reasons))
        elif len(candidates) == 1:
            target = next(iter(candidates))
            graphs = tuple(sorted(graph_by_key.get((member.score_year, target), ()), key=_graph_sort))
            method = "exact_graph_issn" if graphs else "exact_issn"
            resolved.append(ResolvedMembership(member, target, method, *evidence, "", graphs))
        else:
            reason = ("missing_register_issns" if not register_issns else
                      "no_exact_issn_match" if not candidates else "ambiguous_exact_issn_match")
            graphs = tuple(sorted((graph for target in candidates
                                   for graph in graph_by_key.get((member.score_year, target), ())),
                                  key=_graph_sort))
            queue.append(MatchReview(member, reason, *evidence, graphs))
    resolved_ids = {(item.member.score_year, item.member.journal_id) for item in resolved}
    queued_ids = {(item.member.score_year, item.member.journal_id) for item in queue}
    if (resolved_ids & queued_ids or len(resolved_ids) + len(queued_ids) != len(members)
            or len(queued_ids) != len(queue)):
        raise AssertionError("Norwegian membership accounting failed")
    return MatchResult(tuple(resolved), tuple(queue))


def group_memberships(resolved: Iterable[ResolvedMembership]) -> tuple[AnnualNorwegianIdentity, ...]:
    """Group unique annual ID-to-key links, retaining reviewed one-to-many IDs."""
    grouped: dict[tuple[int, str], list[ResolvedMembership]] = {}
    seen: set[tuple[int, int, str]] = set()
    member_by_numeric_id: dict[tuple[int, int], NorwegianMembership] = {}
    for match in resolved:
        year = _year(match.member.score_year)
        canonical = _canonical(match.canonical_issn_l)
        register_url(match.member.journal_id)
        numeric_key = year, int(match.member.journal_id)
        link = (*numeric_key, canonical)
        if link in seen:
            raise ValueError("Duplicate annual Norwegian ID-to-key membership link")
        if (numeric_key in member_by_numeric_id
                and member_by_numeric_id[numeric_key] != match.member):
            raise ValueError("Conflicting annual Norwegian member across effective keys")
        seen.add(link)
        member_by_numeric_id[numeric_key] = match.member
        grouped.setdefault((year, canonical), []).append(match)
    return tuple(AnnualNorwegianIdentity(year, canonical,
                 tuple(sorted(matches, key=lambda item: int(item.member.journal_id))))
                 for (year, canonical), matches in sorted(grouped.items()))


def validate_url_cells(resolved: Iterable[ResolvedMembership], exported_rows: Any) -> None:
    """Check complete, exact URL cells against independently matched membership.

    Check the exported subset only: N identities with no defined website score
    need not appear. Every exported N member must retain all matched IDs' URLs;
    exported nonmembers must have a blank URL. Scores are not used as membership.
    """
    expected = {(group.score_year, group.issn_l): group.norwegian_register_url
                for group in group_memberships(resolved)}
    seen: set[tuple[int, str]] = set()
    for row in _records(exported_rows):
        if not {"score_year", "issn_l", "norwegian_register_url"} <= row.keys():
            raise ValueError("Export rows lack score_year, issn_l, or norwegian_register_url")
        key = _year(row["score_year"]), _canonical(row["issn_l"])
        if key in seen:
            raise ValueError("Duplicate annual canonical identity in exported URL rows")
        seen.add(key)
        actual = row["norwegian_register_url"]
        if actual is None:
            actual = ""
        if not isinstance(actual, str) or actual != expected.get(key, ""):
            raise ValueError("Annual Norwegian membership and complete register URL cell disagree")

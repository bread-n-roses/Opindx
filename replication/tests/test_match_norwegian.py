"""Synthetic Norwegian-to-OA matching and multi-link checks; no input files read."""

from dataclasses import replace
from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from match_norwegian import (
    NORWEGIAN_JOURNAL_URL, ReviewedMatchDecision, ReviewedMatchOverride,
    SourceAlias, group_memberships,
    match_memberships, register_url, source_aliases_from_provenance, validate_url_cells,
)
from norwegian import NorwegianMembership


A = "1234-5679"
B = "2049-3630"
C = "0378-5955"


def member(journal_id="7", year=2022, print_issn=A, online_issn="", **changes):
    value = NorwegianMembership(year, journal_id, 1, print_issn, online_issn,
                                "Same title", "Same title", "Discipline", "Field", "0", None)
    return replace(value, **changes)


def source(source_id="S1", canonical=A, aliases=(), year=2022):
    return SourceAlias(year, source_id, canonical, tuple(aliases))


def override(journal_id="7", year=2022, canonical=A, **changes):
    value = ReviewedMatchOverride(year, journal_id, canonical,
                                  "approved_for_score_year", "Reviewed source identity evidence")
    return replace(value, **changes)


def decision(journal_id="7", year=2022, canonicals=(A, B), **changes):
    value = ReviewedMatchDecision(year, journal_id, canonicals,
                                  "approved_for_score_year", "Reviewed annual source identities",
                                  tuple(f"Evidence for {key}" for key in canonicals))
    return replace(value, **changes)


def provenance(source_id="S1", raw=A, corrected=A, canonical=A, aliases=(), quarantine=""):
    return {"source_id": source_id, "raw_issn_l": raw, "corrected_source_issn_l": corrected,
            "issn_l": canonical, "issns": tuple(aliases), "quarantine_reason": quarantine}


class NorwegianMatchingTests(unittest.TestCase):
    def test_online_and_source_aliases_match_with_normalized_presentation(self):
        result = match_memberships([member(print_issn="", online_issn=" 20493630 ")],
                                   [source(aliases=(B,))])
        self.assertFalse(result.review_queue)
        resolved, = result.resolved
        self.assertEqual(resolved.canonical_issn_l, A)
        self.assertEqual(resolved.method, "exact_issn")
        self.assertEqual(resolved.matched_issns, (B,))
        self.assertEqual(resolved.matched_source_ids, ("S1",))

    def test_alias_sources_collapsing_to_one_canonical_key_are_not_ambiguous(self):
        result = match_memberships([member(print_issn=A, online_issn=B)],
                                   [source("S1", C, (A,)), source("S2", C, (B,))])
        self.assertFalse(result.review_queue)
        self.assertEqual(result.resolved[0].candidate_keys, (C,))
        self.assertEqual(result.resolved[0].matched_source_ids, ("S1", "S2"))

    def test_print_does_not_overrule_conflicting_online_match(self):
        result = match_memberships([member(print_issn=A, online_issn=B)],
                                   [source("S1", A), source("S2", B)])
        self.assertFalse(result.resolved)
        case, = result.review_queue
        self.assertEqual(case.reason, "ambiguous_exact_issn_match")
        self.assertEqual(case.candidate_keys, tuple(sorted((A, B))))

    def test_one_shared_alias_pointing_to_two_keys_remains_ambiguous(self):
        result = match_memberships([member(print_issn=C)],
                                   [source("S1", A, (C,)), source("S2", B, (C,))])
        self.assertEqual(len(result.review_queue[0].candidate_keys), 2)
        self.assertFalse(result.resolved)

    def test_years_are_scoped_and_never_carried_forward(self):
        result = match_memberships([member(year=2022), member(year=2023), member(year=2024)],
                                   [source("S1", A, year=2022), source("S1", B, (A,), year=2023)])
        self.assertEqual([(item.member.score_year, item.canonical_issn_l) for item in result.resolved],
                         [(2022, A), (2023, B)])
        self.assertEqual(result.review_queue[0].member.score_year, 2024)
        self.assertEqual(result.review_queue[0].reason, "no_exact_issn_match")

    def test_missing_identifiers_and_unmatched_identifiers_stay_in_review_queue(self):
        result = match_memberships([member("7", print_issn=""), member("8", print_issn=C)],
                                   [source()])
        self.assertFalse(result.resolved)
        self.assertEqual([item.reason for item in result.review_queue],
                         ["missing_register_issns", "no_exact_issn_match"])
        self.assertEqual(result.review_queue[0].member.original_title, "Same title")

    def test_ambiguous_resolution_requires_approved_annual_evidence(self):
        roster = [member(print_issn=A, online_issn=B)]
        sources = [source("S1", A), source("S2", B)]
        for bad in (override(review_status="draft"), override(evidence=" ")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "annual approval"):
                match_memberships(roster, sources, [bad])
        result = match_memberships(roster, sources, [override(canonical=B)])
        self.assertFalse(result.review_queue)
        resolved, = result.resolved
        self.assertEqual(resolved.canonical_issn_l, B)
        self.assertEqual(resolved.method, "reviewed_override")
        self.assertEqual(resolved.candidate_keys, tuple(sorted((A, B))))
        self.assertTrue(resolved.override_evidence)

    def test_reviewed_unmatched_override_allowed_but_unknown_target_and_unused_override_fail(self):
        result = match_memberships([member(print_issn=C)], [source()], [override()])
        self.assertEqual(result.resolved[0].candidate_keys, ())
        self.assertEqual(result.resolved[0].method, "reviewed_override")
        with self.assertRaisesRegex(ValueError, "absent from that year's"):
            match_memberships([member()], [source()], [override(canonical=B)])
        with self.assertRaisesRegex(ValueError, "Unused or duplicate"):
            match_memberships([member()], [source()], [override(year=2023)])
        with self.assertRaisesRegex(ValueError, "Unused or duplicate"):
            match_memberships([member()], [source()], [override(), override()])

    def test_reviewed_one_to_many_links_preserve_one_register_id_and_both_urls(self):
        roster = [member("7", print_issn=A, online_issn=B), member("42", print_issn=A)]
        sources = [source("S1", A), source("S2", B)]
        pending = match_memberships(roster, sources)
        self.assertEqual(len(pending.resolved), 1)
        self.assertEqual(pending.review_queue[0].reason, "ambiguous_exact_issn_match")
        result = match_memberships(roster, sources, [decision(canonicals=(B, A))])
        self.assertFalse(result.review_queue)
        links = [(item.member.journal_id, item.canonical_issn_l) for item in result.resolved]
        self.assertEqual(links, [("7", B), ("7", A), ("42", A)])
        self.assertEqual([item.method for item in result.resolved],
                         ["reviewed_multi_key", "reviewed_multi_key", "exact_issn"])
        self.assertEqual([item.override_evidence for item in result.resolved[:2]],
                         [f"Evidence for {B}", f"Evidence for {A}"])
        groups = {group.issn_l: group for group in group_memberships(result.resolved)}
        self.assertEqual(groups[A].journal_ids, ("7", "42"))
        self.assertEqual(groups[B].journal_ids, ("7",))
        self.assertEqual(groups[A].norwegian_register_url, register_url("7") + "; " + register_url("42"))
        self.assertEqual(groups[B].norwegian_register_url, register_url("7"))
        rows = [{"score_year": 2022, "issn_l": key,
                 "norwegian_register_url": groups[key].norwegian_register_url} for key in (A, B)]
        validate_url_cells(result.resolved, rows)
        with self.assertRaisesRegex(ValueError, "membership and complete"):
            validate_url_cells(result.resolved, [{**rows[0], "norwegian_register_url": register_url("7")}, rows[1]])
        with self.assertRaisesRegex(ValueError, "membership and complete"):
            validate_url_cells(result.resolved, [rows[0], {**rows[1], "norwegian_register_url": ""}])

    def test_reviewed_multi_key_decision_is_unique_annual_and_evidence_bound(self):
        roster = [member(print_issn=A, online_issn=B)]
        sources = [source("S1", A), source("S2", B)]
        for bad, message in (
            (decision(canonicals=()), "nonempty tuple"),
            (decision(canonicals=[A, B]), "nonempty tuple"),
            (decision(canonicals=(A, A)), "repeats a canonical key"),
            (decision(canonicals=(A, A.replace("-", ""))), "repeats a canonical key"),
            (decision(canonicals=(A, C)), "absent from that year's"),
            (decision(review_status="draft"), "annual approval"),
            (decision(evidence=" "), "annual approval"),
            (decision(evidence_by_key=("Evidence A", "")), "evidence for every key"),
            (decision(evidence_by_key=("Evidence A",)), "evidence for every key"),
            (decision(year=2023), "Unused or duplicate"),
        ):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, message):
                match_memberships(roster, sources, [bad])
        with self.assertRaisesRegex(ValueError, "Unused or duplicate"):
            match_memberships(roster, sources, [decision(), override()])

    def test_second_key_requires_review_even_when_one_exact_key_is_available(self):
        roster = [member(print_issn=A)]
        sources = [source("S1", A), source("S2", B)]
        automatic = match_memberships(roster, sources)
        self.assertEqual([item.canonical_issn_l for item in automatic.resolved], [A])
        reviewed = match_memberships(roster, sources, [decision(canonicals=(A, B))])
        self.assertEqual([item.canonical_issn_l for item in reviewed.resolved], [A, B])
        self.assertEqual([item.candidate_keys for item in reviewed.resolved], [(A,), (A,)])
        self.assertEqual([item.method for item in reviewed.resolved],
                         ["reviewed_multi_key", "reviewed_multi_key"])

    def test_grouping_rejects_duplicate_links_and_conflicting_member_records(self):
        result = match_memberships([member(print_issn=A, online_issn=B)],
                                   [source("S1", A), source("S2", B)], [decision()])
        with self.assertRaisesRegex(ValueError, "Duplicate annual Norwegian ID-to-key"):
            group_memberships([*result.resolved, result.resolved[0]])
        conflicting = replace(result.resolved[1], member=member(norwegian_level=2))
        with self.assertRaisesRegex(ValueError, "Conflicting annual Norwegian member"):
            group_memberships([result.resolved[0], conflicting])
        respelled = replace(result.resolved[1], member=member(journal_id="0007"))
        with self.assertRaisesRegex(ValueError, "Conflicting annual Norwegian member"):
            group_memberships([result.resolved[0], respelled])

    def test_multiple_register_ids_and_metadata_survive_one_canonical_match(self):
        roster = [member("42", norwegian_level=2, field="Second field", discipline="Second discipline"),
                  member("0007")]
        result = match_memberships(roster, [source()])
        group, = group_memberships(result.resolved)
        self.assertEqual(group.journal_ids, ("0007", "42"))
        self.assertEqual([item.member.norwegian_level for item in group.members], [1, 2])
        self.assertEqual([item.member.field for item in group.members], ["Field", "Second field"])
        self.assertEqual(group.norwegian_level, "1 | 2")
        self.assertEqual(group.norwegian_area, "Discipline | Second discipline")
        self.assertEqual(group.norwegian_field, "Field | Second field")
        self.assertEqual(group.norwegian_register_url,
                         NORWEGIAN_JOURNAL_URL + "7; " + NORWEGIAN_JOURNAL_URL + "42")

    def test_url_cells_equal_independently_matched_membership_in_exported_subset(self):
        result = match_memberships([member("7"), member("42"), member("99", print_issn=B)],
                                   [source(), source("S2", B)])
        groups = group_memberships(result.resolved)
        group_a = next(group for group in groups if group.issn_l == A)
        rows = [{"score_year": 2022, "issn_l": A,
                 "norwegian_register_url": group_a.norwegian_register_url},
                {"score_year": 2022, "issn_l": C, "norwegian_register_url": ""}]
        validate_url_cells(result.resolved, pd.DataFrame(rows))
        for wrong in ("", register_url("7"), register_url("123")):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, "membership and complete"):
                validate_url_cells(result.resolved, [{**rows[0], "norwegian_register_url": wrong}])
        with self.assertRaisesRegex(ValueError, "membership and complete"):
            validate_url_cells(result.resolved, [{**rows[1], "norwegian_register_url": register_url("7")}])
        with self.assertRaisesRegex(ValueError, "Duplicate annual"):
            validate_url_cells(result.resolved, [rows[0], rows[0]])

    def test_provenance_adapter_keeps_corrected_raw_and_all_aliases_without_resolving_conflicts(self):
        raw = [provenance("S1", raw="bad-key", corrected=B, canonical=A, aliases=(C,)),
               provenance("S2", raw=C, corrected=C, canonical=B),
               provenance("S3", raw="bad", corrected="bad", canonical="bad", quarantine="invalid_key")]
        aliases = source_aliases_from_provenance(2022, pd.DataFrame(raw))
        self.assertEqual(len(aliases), 2)
        self.assertEqual(aliases[0].ignored_invalid_issns, ("bad-key",))
        self.assertEqual(set(aliases[0].issns), {A, B, C})
        result = match_memberships([member(print_issn=C)], aliases)
        self.assertEqual(set(result.review_queue[0].candidate_keys), {A, B})
        with self.assertRaisesRegex(ValueError, "different score year"):
            source_aliases_from_provenance(2022, [{**raw[0], "score_year": 2023}])

    def test_provenance_missing_raw_aliases_are_absent_and_invalid_text_is_diagnostic(self):
        raw = [provenance("S1", raw=pd.NA, corrected=A, aliases=(None, float("nan"), pd.NA, "bad-key")),
               provenance("S2", raw=float("nan"), corrected=B, canonical=B)]
        aliases = source_aliases_from_provenance(2022, pd.DataFrame(raw))
        self.assertEqual(aliases[0].issns, (A,))
        self.assertEqual(aliases[0].ignored_invalid_issns, ("bad-key",))
        self.assertEqual(aliases[1].issns, (B,))
        self.assertEqual(aliases[1].ignored_invalid_issns, ())

    def test_duplicate_conflicting_and_invalid_ids_fail_explicitly(self):
        self.assertEqual(len(match_memberships([member(), member()], [source()]).resolved), 1)
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            match_memberships([member(), member(norwegian_level=2)], [source()])
        with self.assertRaisesRegex(ValueError, "Two register ID spellings"):
            match_memberships([member("7"), member("0007")], [source()])
        with self.assertRaisesRegex(ValueError, "Duplicate annual source"):
            match_memberships([member()], [source(), source()])
        for bad in ("0", "-1", "not-an-id", "７", "7; 8"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                register_url(bad)


if __name__ == "__main__":
    unittest.main()

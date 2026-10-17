"""One-pass open-input components for the Open Journal Factor.

The caller freezes publication identities, type policies, paratext eligibility,
and reference observability upstream. This module never discovers aliases or
uses benchmark agreement to choose a representative. A supplied flat alias map
uses canonical-record metadata (including REF, source, year and status), unions
the manifestations' recorded references, and deduplicates canonical directed
edges. It does not select the representative by REF or copy an alias's REF.

Input relations have the same schema as counting.py. Optional aliases contain
``alias_work_id, canonical_work_id`` with IDs of the same SQL type as Works.
Canonical records must be supplied even when their aliases have another year
or source; eligibility is decided from that representative. Aliases without a
record on a particular side need no metadata on that side. No paratext filter is
silently applied: eligible input relations are the caller's explicit contract.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from . import counting_base as legacy

UNIVERSES = legacy.UNIVERSES
RETRACTION_MODES = legacy.RETRACTION_MODES
SOURCE_COMPONENTS = legacy.SOURCE_COMPONENTS
DEFAULT_POLICIES = {
    "AR": ("article", "review"),
    "AR_DP": ("article", "review", "data-paper"),
    "AR_DP_SP": ("article", "review", "data-paper", "software-paper"),
    "AR_DP_SP_CP": ("article", "review", "data-paper", "software-paper", "conference-paper"),
}


@dataclass
class TypeCounts:
    denominators: pd.DataFrame
    citations: pd.DataFrame
    evaluation: pd.DataFrame
    audit: dict[str, Any]
    citation_year: int


def _guard(conn: Any, sql: str, message: str) -> None:
    legacy._raise_if_any(conn, sql, message)


def _universe_sql() -> str:
    return "CROSS JOIN (VALUES ('N')) AS u(universe) WHERE c.in_n"


def _edge_join_sql(*, unique_citing: bool, aliases: bool) -> str:
    if aliases:
        return """
          FROM (
            SELECT DISTINCT f.citing_work_id, tmatch.work_id AS target_work_id
            FROM (
              SELECT cm.work_id AS citing_work_id,
                     unnest(list_distinct(ci."references")) AS target_work_id
              FROM _ojf_citing_input ci
              LEFT JOIN _ojf_aliases ca ON ca.alias_work_id=ci.work_id
              JOIN _ojf_citing_metadata cm
                ON cm.work_id=coalesce(ca.canonical_work_id,ci.work_id)
            ) f
            LEFT JOIN _ojf_aliases ta ON ta.alias_work_id=f.target_work_id
            JOIN _ojf_targets tmatch
              ON tmatch.work_id=coalesce(ta.canonical_work_id,f.target_work_id)
          ) e
          JOIN _ojf_targets t ON t.work_id=e.target_work_id
          JOIN _ojf_citing_metadata c ON c.work_id=e.citing_work_id
        """ + _universe_sql()
    if unique_citing:
        return """
          FROM (
            SELECT work_id, journal_id, is_retracted, source_component,
                   in_n,in_n_journals,in_a_expanded,
                   unnest(list_distinct("references")) AS target_work_id
            FROM _ojf_citing_input
          ) c JOIN _ojf_targets t ON t.work_id=c.target_work_id
        """ + _universe_sql()
    return """
      FROM (
        SELECT DISTINCT f.citing_work_id,f.target_work_id FROM (
          SELECT work_id AS citing_work_id,
                 unnest(list_distinct("references")) AS target_work_id
          FROM _ojf_citing_input
        ) f JOIN _ojf_targets tm ON tm.work_id=f.target_work_id
      ) e JOIN _ojf_targets t ON t.work_id=e.target_work_id
      JOIN _ojf_citing_metadata c ON c.work_id=e.citing_work_id
    """ + _universe_sql()


def build_type_counts(conn: Any, target_relation: str = "targets",
                      citing_relation: str = "citing",
                      evaluation_relation: str = "evaluation", *,
                      citation_year: int = 2024,
                      aliases_relation: str | None = None) -> TypeCounts:
    """Count target-type/REF components in one reference-array streaming pass.

    All type policies are derived cheaply from these components. With no alias
    relation and AR policy, results reproduce counting.py exactly. Supplying an
    empty alias relation selects that fast path too. Nonempty alias maps trigger
    full canonical edge deduplication; they must be flat and unambiguous.
    """
    target, citing, evaluation = map(legacy._identifier,
                                    (target_relation, citing_relation, evaluation_relation))
    if not isinstance(citation_year, int) or isinstance(citation_year, bool) or citation_year < 1902:
        raise ValueError("citation_year must be an integer >= 1902")
    first, last = citation_year - 2, citation_year - 1
    _guard(conn, f"SELECT count(*)-count(DISTINCT journal_id) FROM {evaluation}",
           "Evaluation journal IDs must be unique and nonmissing")
    conn.execute(f"CREATE OR REPLACE TEMP TABLE _ojf_eval AS SELECT journal_id FROM {evaluation}")
    alias_rows = 0
    if aliases_relation is not None:
        aliases = legacy._identifier(aliases_relation)
        _guard(conn, f"SELECT count(*) FROM {aliases} WHERE alias_work_id IS NULL OR canonical_work_id IS NULL",
               "Alias IDs and canonical IDs must be nonmissing")
        _guard(conn, f"SELECT count(*) FROM (SELECT alias_work_id FROM {aliases} GROUP BY alias_work_id HAVING count(DISTINCT canonical_work_id)>1)",
               "Conflicting canonical aliases")
        conn.execute(f"CREATE OR REPLACE TEMP TABLE _ojf_aliases AS SELECT DISTINCT alias_work_id,canonical_work_id FROM {aliases} WHERE alias_work_id<>canonical_work_id")
        _guard(conn, "SELECT count(*) FROM _ojf_aliases a JOIN _ojf_aliases b ON a.canonical_work_id=b.alias_work_id",
               "Canonical aliases must be flat: chains and cycles are forbidden")
        alias_rows = conn.execute("SELECT count(*) FROM _ojf_aliases").fetchone()[0]
    has_aliases = alias_rows > 0
    columns = {row[0] for row in conn.execute(f"DESCRIBE {citing}").fetchall()}
    component = "source_component" if "source_component" in columns else "'other' AS source_component"
    target_scope = "" if has_aliases else f"JOIN _ojf_eval e USING(journal_id) WHERE publication_year BETWEEN {first} AND {last}"
    citing_scope = "" if has_aliases else f"WHERE publication_year={citation_year} AND (in_n OR in_n_journals OR in_a_expanded)"
    conn.execute(f"""
      CREATE OR REPLACE TEMP TABLE _ojf_targets_input AS
      SELECT t.work_id,t.journal_id,t.publication_year,t.work_type,t.observed_references,t.is_retracted
      FROM {target} t {target_scope};
      CREATE OR REPLACE TEMP VIEW _ojf_citing_input AS
      SELECT work_id,journal_id,publication_year,"references",is_retracted,
             in_n,in_n_journals,in_a_expanded,{component}
      FROM {citing} {citing_scope};
    """)
    _guard(conn, """SELECT count(*) FROM _ojf_targets_input WHERE work_id IS NULL
       OR journal_id IS NULL OR publication_year IS NULL OR work_type IS NULL
       OR observed_references IS NULL OR observed_references<0 OR is_retracted IS NULL""",
           "Target work IDs, metadata, reference counts and retraction flags must be valid")
    _guard(conn, """SELECT count(*) FROM _ojf_citing_input WHERE work_id IS NULL
       OR publication_year IS NULL OR is_retracted IS NULL
       OR in_n IS NULL OR in_n_journals IS NULL OR in_a_expanded IS NULL
       OR (in_n_journals AND NOT in_n) OR (in_n AND NOT in_a_expanded)""",
           "Citing metadata must be valid; N-journals must be within N within A-expanded")
    labels = ",".join("'" + value + "'" for value in SOURCE_COMPONENTS)
    _guard(conn, f"SELECT count(*) FROM _ojf_citing_input WHERE source_component IS NULL OR source_component NOT IN ({labels})",
           "Citing source components must use the declared exhaustive categories")
    if "source_component" in columns:
        _guard(conn, """SELECT count(*) FROM _ojf_citing_input WHERE
          (source_component='N-journal' AND NOT in_n_journals)
          OR (source_component='N-nonjournal' AND (NOT in_n OR in_n_journals))
          OR (source_component IN ('outside-N-journal','outside-N-book-proceedings') AND in_n)""",
               "Citing source component conflicts with Norwegian membership")
    _guard(conn, """SELECT count(*) FROM (SELECT work_id FROM _ojf_targets_input GROUP BY work_id
       HAVING count(DISTINCT struct_pack(journal_id:=journal_id,year:=publication_year,
          type:=work_type,refs:=observed_references,retracted:=is_retracted))>1)""",
           "Conflicting target metadata for a duplicate Work ID")
    _guard(conn, """SELECT count(*) FROM (SELECT work_id FROM _ojf_citing_input GROUP BY work_id
       HAVING count(DISTINCT struct_pack(journal_id:=journal_id,year:=publication_year,
          retracted:=is_retracted,n:=in_n,nj:=in_n_journals,a:=in_a_expanded,
          component:=source_component))>1)""",
           "Conflicting citing metadata for a duplicate Work ID")
    if has_aliases:
        for relation, side in (("_ojf_targets_input", "target"), ("_ojf_citing_input", "citing")):
            _guard(conn, f"""SELECT count(*) FROM {relation} r
                JOIN _ojf_aliases a ON r.work_id=a.alias_work_id
                WHERE NOT EXISTS(SELECT 1 FROM {relation} rep WHERE rep.work_id=a.canonical_work_id)""",
                   f"Canonical {side} representative metadata is missing")
        target_keep = "AND NOT EXISTS(SELECT 1 FROM _ojf_aliases a WHERE a.alias_work_id=t.work_id)"
        citing_keep = "AND NOT EXISTS(SELECT 1 FROM _ojf_aliases a WHERE a.alias_work_id=c.work_id)"
    else:
        target_keep = citing_keep = ""
    conn.execute(f"""
      CREATE OR REPLACE TEMP TABLE _ojf_targets AS
      SELECT DISTINCT t.*,observed_references>0 AS has_refs
      FROM _ojf_targets_input t JOIN _ojf_eval e USING(journal_id)
      WHERE publication_year BETWEEN {first} AND {last} {target_keep};
      CREATE OR REPLACE TEMP TABLE _ojf_citing_metadata AS
      SELECT DISTINCT work_id,journal_id,is_retracted,in_n,in_n_journals,in_a_expanded,source_component
      FROM _ojf_citing_input c WHERE publication_year={citation_year}
        AND (in_n OR in_n_journals OR in_a_expanded) {citing_keep};
    """)
    denominators = conn.execute("""SELECT journal_id,work_type,has_refs,is_retracted,
        count(*)::BIGINT AS works FROM _ojf_targets GROUP BY ALL
        ORDER BY journal_id,work_type,has_refs,is_retracted""").df()
    input_rows, metadata_rows = conn.execute("""SELECT
       (SELECT count(*) FROM _ojf_citing_input),
       (SELECT count(*) FROM _ojf_citing_metadata)""").fetchone()
    unique_citing = not has_aliases and input_rows == metadata_rows
    aggregate = []
    for mode, status in (("all", "TRUE"), ("exclude_snapshot", "NOT c.is_retracted AND NOT t.is_retracted")):
        aggregate.append(f"count(*) FILTER(WHERE {status})::BIGINT AS n_{mode}")
        aggregate.append(f"count(*) FILTER(WHERE ({status}) AND c.journal_id=t.journal_id)::BIGINT AS self_{mode}")
        for label, column in SOURCE_COMPONENTS.items():
            aggregate.append(f"count(*) FILTER(WHERE ({status}) AND c.source_component='{label}')::BIGINT AS {column}_{mode}")
    joined = _edge_join_sql(unique_citing=unique_citing, aliases=has_aliases)
    citations = conn.execute(f"""SELECT t.journal_id,u.universe,t.work_type,t.has_refs,
       {', '.join(aggregate)} {joined} GROUP BY t.journal_id,u.universe,t.work_type,t.has_refs
       ORDER BY t.journal_id,u.universe,t.work_type,t.has_refs""").df()
    for mode in RETRACTION_MODES:
        component_columns = [f"{column}_{mode}" for column in SOURCE_COMPONENTS.values()]
        if not citations[component_columns].sum(axis=1).eq(citations[f"n_{mode}"]).all():
            raise AssertionError("Disjoint citing-source components do not conserve citation counts")
    evaluation_frame = conn.execute("SELECT journal_id FROM _ojf_eval ORDER BY journal_id").df()
    return TypeCounts(denominators, citations, evaluation_frame,
                      {"alias_rows": int(alias_rows), "canonical_metadata": "fixed representative",
                       "reference_edges": "union manifestations; deduplicate canonical endpoints",
                       "ref_measure": "fixed canonical target observed_references > 0",
                       "citing_input_rows": int(input_rows), "citing_canonical_rows": int(metadata_rows),
                       "target_canonical_rows": int(denominators.works.sum()),
                       "unique_citing_fast_path": unique_citing,
                       "paratext": "eligibility must be supplied upstream"}, citation_year)


def _policies(policies: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    if not policies:
        raise ValueError("At least one explicit citable-type policy is required")
    result = {}
    for name, types in policies.items():
        if not isinstance(name, str) or not name.strip() or isinstance(types, str):
            raise ValueError("Type policies require a nonempty name and a sequence of types")
        values = tuple(types)
        if not values or any(not isinstance(value, str) or not value for value in values) or len(set(values)) != len(values):
            raise ValueError("Citable type names must be nonempty, distinct strings")
        result[name] = values
    return result


def derive_counts(components: TypeCounts, *,
                  policies: Mapping[str, Sequence[str]] = DEFAULT_POLICIES) -> pd.DataFrame:
    """Apply the same frozen type set K to D and target-side E-REF.

    Legacy ``c_non_ar``/``targets_non_ar`` always mean the literal AR complement;
    the added ``c_non_citable``/``targets_non_citable`` mean the policy-specific H.
    RAW numerator is identical across policies; REF always retains all H edges.
    """
    policies = _policies(policies)
    d, c = components.denominators, components.citations
    base = components.evaluation.merge(pd.DataFrame({"universe": UNIVERSES}), how="cross")
    frames = []
    for name, types in policies.items():
        den = d[["journal_id"]].copy()
        k, observed = d.work_type.isin(types), d.has_refs.astype(bool)
        den["d_raw"] = d.works.where(k, 0)
        den["d_ref"] = d.works.where(k & observed, 0)
        den["targets_non_ar"] = d.works.where(~d.work_type.isin(("article", "review")), 0)
        den["d_raw_retracted"] = d.works.where(k & d.is_retracted, 0)
        den["d_ref_retracted"] = d.works.where(k & observed & d.is_retracted, 0)
        den["targets_non_citable"] = d.works.where(~k, 0)
        den = den.groupby("journal_id", as_index=False).sum()
        num = c[["journal_id", "universe"]].copy()
        ck, cobserved = c.work_type.isin(types), c.has_refs.astype(bool)
        eligible = ~ck | cobserved
        for mode, suffix in (("all", ""), ("exclude_snapshot", "_exclude_snapshot")):
            num[f"c_raw{suffix}"] = c[f"n_{mode}"]
            num[f"c_ref{suffix}"] = c[f"n_{mode}"].where(eligible, 0)
            num[f"c_non_ar{suffix}"] = c[f"n_{mode}"].where(~c.work_type.isin(("article", "review")), 0)
            num[f"c_self{suffix}"] = c[f"self_{mode}"]
            num[f"c_ref_self{suffix}"] = c[f"self_{mode}"].where(eligible, 0)
            for column in SOURCE_COMPONENTS.values():
                num[f"c_raw_{column}{suffix}"] = c[f"{column}_{mode}"]
                num[f"c_ref_{column}{suffix}"] = c[f"{column}_{mode}"].where(eligible, 0)
            num[f"c_non_citable{suffix}"] = c[f"n_{mode}"].where(~ck, 0)
        num = num.groupby(["journal_id", "universe"], as_index=False).sum()
        frame = base.merge(den, on="journal_id", how="left", validate="many_to_one").merge(
            num, on=["journal_id", "universe"], how="left", validate="one_to_one")
        for column in frame.columns.difference(["journal_id", "universe"]):
            frame[column] = frame[column].fillna(0).astype("int64")
        frame["citation_year"] = components.citation_year
        frame["type_policy"] = name
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).sort_values(["type_policy", "journal_id", "universe"]).reset_index(drop=True)


def build_counts(conn: Any, target_relation: str = "targets", citing_relation: str = "citing",
                 evaluation_relation: str = "evaluation", *, citation_year: int = 2024,
                 aliases_relation: str | None = None,
                 policies: Mapping[str, Sequence[str]] = DEFAULT_POLICIES) -> pd.DataFrame:
    return derive_counts(build_type_counts(conn, target_relation, citing_relation,
                         evaluation_relation, citation_year=citation_year,
                         aliases_relation=aliases_relation), policies=policies)


def _per_policy(function: Any, frame: pd.DataFrame, *args: Any, **kwargs: Any) -> pd.DataFrame:
    if "type_policy" not in frame:
        raise ValueError("Revised frames require type_policy")
    outputs = []
    for name, group in frame.groupby("type_policy", sort=True):
        output = function(group.drop(columns="type_policy"), *args, **kwargs)
        output["type_policy"] = name
        outputs.append(output)
    if not outputs:
        raise ValueError("At least one type-policy row is required")
    return pd.concat(outputs, ignore_index=True)


def build_scores(counts: pd.DataFrame, *, retraction_modes: Sequence[str] = RETRACTION_MODES) -> pd.DataFrame:
    return _per_policy(legacy.build_scores, counts, retraction_modes=retraction_modes)

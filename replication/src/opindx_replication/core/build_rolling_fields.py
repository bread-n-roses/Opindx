"""Pure helpers extracted unchanged from the validated September pipeline."""
import pandas as pd
METHOD = {
    "method": "rolling-primary-work-broad-field-v1",
    "window": "publication years t-5 through t-1",
    "population": "article/review Works in the corrected Raw graph, exact deduplicated version",
    "mode": "most frequent named primary-topic field; no field pooling",
    "modal_share_denominator": "Works with a named primary-topic broad field",
    "classification_coverage_denominator": "all eligible article/review Works",
    "ties": "alphabetically first field; tied-mode count supplied",
    "no_classified_works": "Unknown, null modal share",
    "domain": "parent domain of the winning field, not an independently computed domain mode",
}

EXTRA_COLUMNS = ["oa_field_modal_share", "oa_field_modal_works", "oa_field_classified_works",
                 "oa_field_classification_coverage", "oa_field_tied_modes"]

def rolling(con, counts, mapping, year):
    con.register("annual_identity_map", mapping)
    try:
        return con.execute("""
            WITH votes AS (
                SELECT m.issn_l, c.oa_field, sum(c.works_n)::BIGINT AS votes
                FROM read_parquet(?) c JOIN annual_identity_map m USING(source_id, raw_issn_l)
                WHERE c.year BETWEEN ? AND ? GROUP BY 1,2
            ), totals AS (
                SELECT issn_l, sum(votes)::BIGINT AS total,
                       sum(CASE WHEN oa_field <> 'Unknown' THEN votes ELSE 0 END)::BIGINT AS classified
                FROM votes GROUP BY 1
            ), named AS (
                SELECT *, max(votes) OVER(PARTITION BY issn_l) AS highest,
                       row_number() OVER(PARTITION BY issn_l ORDER BY votes DESC, oa_field ASC) AS rn
                FROM votes WHERE oa_field <> 'Unknown'
            ), modes AS (
                SELECT *, sum(CASE WHEN votes=highest THEN 1 ELSE 0 END)
                       OVER(PARTITION BY issn_l)::BIGINT AS ties FROM named
            )
            SELECT t.issn_l, coalesce(n.oa_field, 'Unknown') AS oa_field,
                   n.votes::DOUBLE / nullif(t.classified,0) AS oa_field_modal_share,
                   coalesce(n.votes,0)::BIGINT AS oa_field_modal_works,
                   t.classified AS oa_field_classified_works,
                   t.classified::DOUBLE / nullif(t.total,0) AS oa_field_classification_coverage,
                   coalesce(n.ties,0)::BIGINT AS oa_field_tied_modes, t.total
            FROM totals t LEFT JOIN modes n ON t.issn_l=n.issn_l AND n.rn=1
        """, [str(counts), year-5, year-1]).fetchdf()
    finally:
        con.unregister("annual_identity_map")

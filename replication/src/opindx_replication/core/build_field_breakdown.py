"""Pure helpers extracted unchanged from the validated September pipeline."""
import pandas as pd
KEYS = ["openalex_id", "score_year"]

COUNTS = [f"oa_field_{i}_works" for i in range(1, 4)]

NAMES = [f"oa_field_{i}" for i in range(1, 4)]

def aggregate(con, counts, mapping, year):
    con.register("annual_identity_map", mapping)
    try:
        votes = con.execute("""
            SELECT m.issn_l, c.oa_field, sum(c.works_n)::BIGINT AS works
            FROM read_parquet(?) c JOIN annual_identity_map m USING(source_id, raw_issn_l)
            WHERE c.year BETWEEN ? AND ? GROUP BY 1,2
            ORDER BY issn_l, works DESC, oa_field ASC
        """, [str(counts), year - 5, year - 1]).fetchdf()
    finally:
        con.unregister("annual_identity_map")
    result = votes.groupby("issn_l").works.sum().rename("publications_raw").to_frame()
    named = votes.loc[votes.oa_field.ne("Unknown")].copy()
    result["oa_field_classified_works"] = named.groupby("issn_l").works.sum()
    named["position"] = named.groupby("issn_l").cumcount() + 1
    for i in range(1, 4):
        selected = named.loc[named.position.eq(i)].set_index("issn_l")
        result[f"oa_field_{i}"] = selected.oa_field
        result[f"oa_field_{i}_works"] = selected.works
    for col in COUNTS + ["oa_field_classified_works"]:
        result[col] = result[col].fillna(0).astype("int64")
    result[NAMES] = result[NAMES].fillna("")
    result["oa_field_other_works"] = result.oa_field_classified_works - result[COUNTS].sum(axis=1)
    return result.reset_index()

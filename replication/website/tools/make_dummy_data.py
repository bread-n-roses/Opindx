"""Generate a dummy data run in the layout described in SPEC.md.

All journals and numbers are made up. Needs numpy, pandas and pyarrow.
Usage: python tools/make_dummy_data.py [run]   (default 2026-Q3-dummy, writes export/<run>/)
A second run with a different name gives different numbers, so freezing score years can be tested.
"""
import json
import sys
import zlib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

RUN = sys.argv[1] if len(sys.argv) > 1 else "2026-Q3-dummy"
YEARS = [2022, 2023, 2024, 2025]
N_JOURNALS = 110_000
UNIVERSES = {"n": "Norwegian Register", "oa": "OpenAlex"}
SHARE_IN_UNIVERSE = {"n": 0.20, "oa": 0.99}

OA_FIELDS = {
    "Physical Sciences": ["Chemical Engineering", "Chemistry", "Computer Science", "Earth and Planetary Sciences",
                          "Energy", "Engineering", "Environmental Science", "Materials Science", "Mathematics",
                          "Physics and Astronomy"],
    "Life Sciences": ["Agricultural and Biological Sciences", "Biochemistry, Genetics and Molecular Biology",
                      "Immunology and Microbiology", "Neuroscience", "Pharmacology, Toxicology and Pharmaceutics"],
    "Health Sciences": ["Dentistry", "Health Professions", "Medicine", "Nursing", "Veterinary"],
    "Social Sciences": ["Arts and Humanities", "Business, Management and Accounting", "Decision Sciences",
                        "Economics, Econometrics and Finance", "Psychology", "Social Sciences"],
}
NORWEGIAN_FIELDS = {
    "Health Sciences": ["Surgical Sciences", "Pharmacology and Toxicology", "Public Health"],
    "Humanities": ["Art History", "History", "Linguistics", "Philosophy"],
    "Natural Sciences and Engineering": ["Biosciences", "Geosciences", "ICT", "Physics", "Chemistry"],
    "Social Science": ["Business and Finance", "Economics", "Psychology", "Library and Information Science"],
}
PUBLISHERS = ["Elsevier BV", "Springer Nature", "Wiley", "Taylor & Francis", "SAGE Publishing",
              "Oxford University Press", "MDPI", "Cambridge University Press", "De Gruyter", "Small Society Press"]

rng = np.random.default_rng(zlib.crc32(RUN.encode()))  # same run name, same numbers
n = N_JOURNALS


def snapshot_dates(run):
    """Plausible snapshot dates for a run called <year>-Q<quarter>: the quarter before it."""
    year, quarter = int(run[:4]), int(run[6])
    month = (quarter - 1) * 3
    return (f"{year}-{month:02d}-15", f"{year}-{month + 1:02d}-15") if month else (f"{year - 1}-12-15", f"{year}-01-15")


def pick(groups):
    """Draw a (group, item) pair per journal, e.g. (domain, field)."""
    pairs = [(group, item) for group, items in groups.items() for item in items]
    drawn = [pairs[k] for k in rng.integers(len(pairs), size=n)]
    return [g for g, _ in drawn], [i for _, i in drawn]


# Journal properties that stay the same across years.
member = {u: rng.random(n) < share for u, share in SHARE_IN_UNIVERSE.items()}
oa_domain, oa_field = pick(OA_FIELDS)
no_area, no_field = pick(NORWEGIAN_FIELDS)
issn_l = [f"{s[:4]}-{s[4:]}" for s in (f"9{i:07d}" for i in range(n))]
has_second_issn = rng.random(n) < 0.6
journals = pd.DataFrame({
    "openalex_id": [f"S9{i:09d}" for i in range(n)],
    "title": [f"Dummy Journal of {field} {i:06d}" for i, field in enumerate(oa_field)],
    "publisher": rng.choice(PUBLISHERS, n),
    "issn_l": issn_l,
    "issns": [f"{s}; 8{s[1:]}" if second else s for s, second in zip(issn_l, has_second_issn)],
    "oa_domain": oa_domain,
    "oa_field": oa_field,
    "norwegian_area": pd.Series(no_area).where(member["n"]),
    "norwegian_field": pd.Series(no_field).where(member["n"]),
    # Text, because a journal with two register entries has a level like "1 | 2".
    "norwegian_level": pd.Series(np.where(rng.random(n) < 0.2, "2", "1")).where(member["n"]).astype("string"),
    "norwegian_register_url": pd.Series([f"https://kanalregister.hkdir.no/tidsskrift?id={400000 + i}" for i in range(n)]).where(member["n"]),
    "is_open_access": rng.random(n) < 0.3,
})
influence = rng.lognormal(0, 1, n)          # drives ANS and citations
yearly_output = rng.lognormal(3.5, 1.1, n)  # publications per year
coverage = np.where(rng.random(n) < 0.05, 0.0, rng.beta(8, 1, n))
active_years = rng.choice([1, 2, 3, 4, 5], n, p=[0.02, 0.02, 0.03, 0.05, 0.88])


def scores(in_universe, publications, drift):
    """JNS and ANS for one universe: ANS has article-weighted mean 1, JNS sums to 100."""
    per_article = influence * drift * rng.lognormal(0, 0.15, n)
    per_article[rng.random(n) < 0.02] = 0.0  # journals without incoming citations
    per_article = np.where(in_universe & (publications > 0), per_article, np.nan)
    article_share = np.where(np.isnan(per_article), 0, publications) / publications[~np.isnan(per_article)].sum()
    per_article = per_article / np.nansum(per_article * article_share)
    return 100 * per_article * article_share, per_article


out = Path(__file__).resolve().parent.parent / "export" / RUN
out.mkdir(parents=True, exist_ok=True)
for year in YEARS:
    drift = rng.lognormal(0, 0.1, n)
    df = journals.copy()
    df["score_year"] = year
    df["publications_raw"] = rng.poisson(yearly_output * drift * active_years)
    df["publications_filtered"] = rng.binomial(df["publications_raw"], coverage)
    df["citations_raw"] = rng.poisson(df["publications_raw"] * influence * 2)
    df["citations_filtered"] = rng.binomial(df["citations_raw"], coverage)
    df["reference_coverage_pct"] = (100 * df["publications_filtered"] / df["publications_raw"]).where(df["publications_raw"] > 0)
    df["active_years"] = active_years
    for u in UNIVERSES:
        df[f"in_{u}"] = member[u]
        for treatment in ("raw", "filtered"):
            share, per_article = scores(member[u], df[f"publications_{treatment}"].to_numpy(), drift)
            df[f"share_{u}_{treatment}"], df[f"per_article_{u}_{treatment}"] = share, per_article
    df = df[np.logical_or.reduce(list(member.values()))]
    df.to_parquet(out / f"scores_{year}.parquet", index=False)

openalex_snapshot, register_snapshot = snapshot_dates(RUN)
manifest = {"run": RUN, "created": date.today().isoformat(), "dummy": True,
            "openalex_snapshot": openalex_snapshot, "norwegian_register_snapshot": register_snapshot,
            "years": YEARS, "universes": UNIVERSES}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2))
print(f"Wrote {out}")

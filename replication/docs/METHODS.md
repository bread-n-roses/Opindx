# Calculation implemented by this release

This describes the active path in `src/opindx_replication/pipeline.py` and
`core/score.py`. The bundled source is authoritative. Historical EF/AIS names
inside the inherited solver mean the values exported here as NF/ANS; they do
not denote imported commercial metrics. Other historical helper entry points
in those modules are not used by this pipeline.

## Inputs, years and identities

A citing year t uses publications from t-5 through t-1. Counted publications are
OpenAlex articles and reviews with a primary-source ISSN-L; paratext is excluded.
Citing Work types are the explicit `CITING_TYPES` list in `core/extract.py`:
article, review, letter, editorial, preprint, book-chapter, erratum, other,
retraction, report, dissertation, supplementary-materials, dataset,
reference-entry and book. A citing Work must have recorded references. Duplicate
references from one Work to the same cited Work count once.

Work IDs are deduplicated using the recorded update date and deterministic
file/row tie breakers. The exact chosen file and row are retained. Reviewed
Work-level source repairs precede annual journal identity projection. The
packaged annual corrections also resolve journal continuations, representative
OpenAlex Sources and Norwegian Register matches. They are pinned to this snapshot
and must not silently be carried over to a different one.

Raw publication counts include all eligible articles/reviews. Filtered counts
require `referenced_works_count > 0`; Filtered citation edges require that same
condition for the cited publication. Thus the filtering concerns the publications
receiving citations and their article mass. It does not invent absent references.

## Four calculation variants

The workflow calculates each combination of OpenAlex/Norwegian Register and
Raw/Filtered. The Raw-active state contains identities with positive Raw article
mass or a positive Raw citation edge. OpenAlex restricts that state to corrected
Sources of type journal; Norwegian Register restricts it to the matched annual
roster. Within each universe, the same Raw-active state is retained in both
Raw and Filtered calculations, including zero-degree nodes.

Both citation endpoints are restricted to that universe. Journal self-citations
are removed after identity projection, including self-citations created by
merging source aliases. Matrix B has cited journals as rows and citing journals
as columns. Its nonzero columns are normalized to give H; dangling columns remain
zero. Let a be the normalized publication-count vector for the chosen treatment,
and d identify the dangling columns.

The stationary vector is calculated with alpha = 0.85:

    pi = 0.85 H pi + (0.85 sum(pi[d]) + 0.15) a

Iteration starts at a. The L1 iteration threshold is 1e-14, the fixed-point
residual must be at most 1e-12, and the iteration limit is 10,000. The resulting
probability mass must sum to one within 1e-12.

Final citation transport excludes dangling redistribution and teleportation:

    f = H pi
    e = f / sum(f)
    NF = 100 e
    ANS[i] = e[i] / a[i], when a[i] > 0

NF sums to 100 in the complete calculated state (tolerance 1e-8). A displayed or
exported subset need not sum to 100. ANS is missing when its publication mass is
zero; NF can still be defined. Zero is a defined score. Annual public rows are
OpenAlex journal identities with at least one defined NF or ANS across the four
variants. No all-metrics-missing journals are added.

## Counts, fields and browser calculations

The exported incoming-citation counts use the full cached source graph,
excluding journal self-citations; they are not restricted to the selected
Norwegian scoring universe. Publication counts use the preceding five-year
window. Reference coverage is Filtered publication count divided by Raw count;
the interface displays its complement as the share without references. Coverage
is missing when the Raw denominator is zero.

OpenAlex broad-field metadata uses the exact selected version of each article
or review in the rolling five-year window, after identity corrections. All Raw
articles/reviews contribute; unclassified publications are excluded from the
field-percentage denominator. The modal field, tie information, top three fields
and remaining field counts are preserved. Norwegian fields are the matched
register assignments for the citing year.

Percentiles depend on browser settings. They are computed by the bundled
`website/site/engine.js` and `presets.js`, with the same rules as the website;
there is no separate Python approximation. They are not universal columns in the
complete annual datasets. See the bundled journal-help page for the column and
preset definitions, and `docs/WEBSITE.md` for reproducing a selected table.

The release data dictionary defines all canonical output columns and units.
`docs/VALIDATION.md` records tests actually run, including any execution limits.

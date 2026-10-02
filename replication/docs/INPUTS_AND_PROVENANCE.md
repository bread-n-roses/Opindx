# Input provenance and public-data boundary

## Numerical inputs

1. OpenAlex Works and Sources, snapshot 2026-09-23. OpenAlex metadata is CC0.
   https://help.openalex.org/access/snapshot/
   https://help.openalex.org/access/overview/
2. Norwegian Register for Scientific Journals, Series and Publishers, local CSV
   dated 2026-07-26, hash pinned in configs/september-2026.json.
   https://kanalregister.hkdir.no/en/informasjonsartikler/download-current-list
   Contains data under the Norwegian licence for Open Government Data (NLOD)
   distributed by the Norwegian Directorate for Higher Education and Skills
   (HK-dir). https://data.norge.no/nlod/en/2.0

The package does not distribute the raw Norwegian list: it contains contact
information unnecessary for metric replication outputs. The parser consumes
journal identifiers, names, classifications and annual levels. Release exports
contain no publisher contact columns. Obtain the exact input vintage separately;
if that file is not available publicly, an archived, appropriately reviewed
input distribution must be arranged before promising effortless input retrieval.
Input availability is distinct from the software's ability to reproduce it.

## Identity decisions

The correction bundles are author-curated transformations used in the published
OpenAlex/Norwegian computation: source-key corrections, journal continuations,
canonical representatives and ambiguous Norwegian-match decisions. They are not
commercial scores or a reconstruction of a proprietary journal universe.
The applied maps retain their exact citing-year scope and original approval hash.

Supporting identity evidence includes public publisher, library, Crossref and
ISSN references. Therefore the accurate statement is: the numerical data come
from OpenAlex and the Norwegian Register, with documented identity corrections.
It would be inaccurate to claim that every supporting bibliographic reference
comes from only those two websites. No licensed ISSN registry dump, publisher
full text, commercial metric, licensed journal list or private benchmark panel is
bundled. Links used as evidence are not redistributed copies of those services.

`configs/source-code-provenance.json` maps calculation modules to their reviewed
source versions. Some modules retain EF/AIS as internal variable names; website
and dataset documentation use NF (Network Factor) and ANS (Article Network Score).
The package's `.gitattributes` preserves exact file bytes across Git checkouts;
automatic line-ending conversion must not invalidate the pinned correction hashes.

## Exclusions and verification

Package contents are allowlisted before archive creation. The public runtime
imports no Paper C/WP4 analysis or benchmark readers. Real input reads are restricted
to the configured snapshot, register, bundled corrections and local derived files.
All 43 annual output fields have definitions; there are no Clarivate/JCR, Scopus,
CWTS or commercial benchmark columns. A static scan supplements this dependency
and schema audit; a keyword scan alone is not proof of provenance.

Source-manifest hashes and expected file inventories identify the snapshot.
The inherited extractor checks raw-file sizes and modification times, not a full
content hash of each 707 GB input collection. Derived artifacts have SHA-256
receipts. The limitation is explicit in every input audit.

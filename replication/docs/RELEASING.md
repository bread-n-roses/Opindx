# Separate software and dataset releases

Status: prepared locally; neither workflow activation, Git push nor Zenodo
publication has been performed. ZENODO_TOKEN exists as an Actions secret in
bread-n-roses/Opindx. It is not included anywhere in this package.

1. Creator metadata is confirmed: Utz Weitzel, affiliated with VU Amsterdam and
   Radboud University Nijmegen, in CITATION.cff and both configs/zenodo-*.json
   files. No ORCID was supplied. No fictitious DOI or paper URL is supplied.
2. Verify the full replay report, dataset all-cell checks and public-input audit.
   The software version, data version and source snapshot are separate values.
   Before a new validation run, record its source inventory with
   `python replication/workflow/record_validation.py begin replication /path/to/new-run`.
   Run the full CLI with `--reference /path/to/archived-parquets`, then record
   the completed result with the same command using `finish` instead of `begin`.
   Changed calculation, browser or dependency files invalidate the old report.
3. Build the software source archive after its full replay has passed:
   `python replication/workflow/build_software_release.py replication /path/to/empty/software-v0.1.0`.
   This checks the replay report and writes a deterministic source archive,
   README, release notes, checksums and an explicit release-manifest.json allowlist.
4. Publish the reviewed code to GitHub only when authorized. The dedicated
   software tag names the exact commit, including replication/ and this workflow.
   Dataset release assets are the already validated complete files; never run a
   scientific recalculation merely because a tag was created.
5. Install workflow/zenodo-release.yml at the repository workflow location and
   enable ZENODO_PUBLISH_ENABLED=true only after review. The current preparation
   intentionally leaves it disabled. Ordinary pushes have no Zenodo trigger.
6. A deliberate published release with a software-v* or data-* tag then validates,
   uploads and publishes immediately. It uses the versioned metadata at that tag.
   Its receipt is attached to the GitHub release. Manual dispatch of the existing
   release is supported for recovery.

The uploader defaults to an offline dry-run and needs explicit --publish plus the
ZENODO_TOKEN environment variable for any network access. It identifies existing
releases by a public series/tag/bundle-hash marker, reuses matching drafts, verifies
remote MD5 and size, and never changes files in an already published record.
Subsequent versions follow the previous record's latest_draft link. Operations are
serialized in GitHub Actions. An interrupted request with an uncertain server
outcome requires re-reading deposit state; ambiguous duplicate records stop the
uploader rather than silently choosing one. Offline tests exercise these cases;
no live Zenodo API transaction is claimed until recorded explicitly.

First publish software, then insert its returned version DOI as a related identifier
in the dataset metadata before making the deliberate dataset release. Later datasets
identify the software version used. Software and data versions need not advance
together. A subsequent record metadata edit can add the paper DOI when available.
No grant metadata is required (no funding); no community has been selected.

The site's existing deployment must select a data release by manifest/score assets,
not simply the newest GitHub release. The prepared deployment patch addresses this
before software releases can coexist in the same repository. Historical data tags
and frozen-year pins remain supported.

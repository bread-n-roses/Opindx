# Opindx website

Website for browsing and downloading open journal metrics (Network Factor and Article Network Score) computed from OpenAlex.

- `SPEC.md`: what the website does and the data layout.
- `publishing-data.md`: how to publish a new data run.
- [replication/](replication/README.md): versioned calculation source from downloaded OpenAlex and Norwegian Register inputs, complete annual exports, provenance and validation.
- [Replication release guide](replication/docs/RELEASING.md): separate software and dataset DOI series. Published [software v0.1.0](https://doi.org/10.5281/zenodo.23108424) and [complete 2022–2026 datasets](https://doi.org/10.5281/zenodo.23108621).
- `site/`: the website. `engine.js` holds the ranking logic, `app.js` the journal table, `about.html` the About page.

## Preview locally

Needs Python with numpy, pandas and pyarrow.

```
python tools/make_dummy_data.py                       # made-up run in export/2026-Q3-dummy/
python tools/make_dummy_data.py 2027-Q1-dummy         # a second run, to try out frozen score years
python tools/build_site_data.py export 2027-Q1-dummy  # checks the runs and assembles site/data/ from the named latest run
python tools/serve_site.py                            # then open http://localhost:8000/site/index.html
```

To preview a real run instead, set the three values at the bottom of `tools/import_run.py` and run it, then build the site data from that run.

`score-years.json` decides which score years are frozen to which run; everything else comes from the latest run.

Tests for the ranking logic: `node tests/engine.test.mjs` (also run on every deploy).

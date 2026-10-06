# Local reproduction of browser tables

The archive includes the website source used for the current local interface,
its percentile engine/presets, local fonts/icons and local XLSX dependency.
Browser table rendering is not a separate scientific implementation. Keep
website changes in the main site; regenerate this copy when releasing software.

To build from finalized release files:

1. Put manifest.json and scores_YEAR.parquet files in website/runs/<manifest.run>/.
2. For a single-run local replay, write {} to website/score-years.json. This is a
   local preview instruction; retain the original file when reproducing a site
   assembled from several historical frozen releases.
3. From website/, run:
   python tools/build_site_data.py runs <manifest.run>
   python tools/version_site_assets.py site 0000000
   python -m http.server 8000 --bind 127.0.0.1
4. Open http://127.0.0.1:8000/site/.

`0000000` is a local cache-version placeholder. For deployment, use the actual
Git commit hash instead.

Use the same citation year, universe, treatment, preset, percentile requirements,
field selection and column visibility to reproduce a browser selection.
NF and ANS are loaded from the annual Parquets. Field percentages and percentiles
are computed/formatted by the bundled JavaScript. Annual full data files retain
all fields; Download selection uses only visible columns across all result
pages, splitting the displayed OpenAlex field percentage into its own column.

Building the local website does not publish it. The remote Parquet reader module
is pinned in app.js; scientific outputs can be inspected without a browser using
the annual files. Node is needed only for website tests and percentile CLI use.

From `website/`, the automated checks are:

```sh
node tests/engine.test.mjs
node tests/journal-details.test.mjs
node tests/main-page.test.mjs
python -m unittest discover -s tests -p 'test_*.py'
```

These check calculations, exported selections, UI contracts and data building.
They are not a substitute for inspecting the website in a real browser.

## Public domain

The public site is https://opindx.org; https://www.opindx.org redirects to it.
The bundled `website/site/CNAME` preserves this domain when publishing the site
to the GitHub Pages branch. Keep the repository deployment guard and existing
Pages/DNS/HTTPS settings. Local builds do not change the public site.

Schema 3 data is required to display Citations used. Earlier releases do not
contain those universe-specific counts; the interface shows a dash rather than
substituting their broader recorded counts. Use the new annual files for all
years, including frozen years, without changing historical NF/ANS values.

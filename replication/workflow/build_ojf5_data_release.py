"""Add complete OJF5 annual files to a verified prior Opindx dataset release."""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from opindx_replication.datasets import digest


def build(previous,ojf5,output,*,software_doi,dataset_doi):
    previous,ojf5,output=map(Path,(previous,ojf5,output))
    old=json.loads((previous/'release-manifest.json').read_text(encoding='utf-8'))
    checked=json.loads((ojf5/'validation.json').read_text(encoding='utf-8'))
    if old['validation_status']!='PASS' or checked['status']!='PASS':raise ValueError('Both input bundles must pass validation')
    if sorted(r['year'] for r in checked['annual'])!=list(range(2022,2027)):raise ValueError('Incomplete OJF5 years')
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):raise ValueError('Use an empty output folder')
    preserved={}
    ancillary={'data_dictionary.csv','ojf_data_dictionary.csv','ojf-format-validation.json',
               'ojf_manifest.json','LICENSE.txt','metric-counts-validation.json','public-data-audit.json'}
    for name,record in old['files'].items():
        path=previous/name
        if path.stat().st_size!=record['bytes'] or digest(path)!=record['sha256']:raise ValueError('Changed prior file: '+name)
        annual=name.startswith(('scores_','ojf_scores_')) and path.suffix in ['.parquet','.csv','.xlsx']
        if annual or name in ancillary:shutil.copyfile(path,output/name)
        if annual:preserved[name]=record
    if len(preserved)!=30:raise ValueError('Expected fifteen NF/ANS and fifteen OJF2 annual files')
    manifest=json.loads((previous/'manifest.json').read_text(encoding='utf-8'))
    manifest.update(run='data-2026-09-v5',created='2026-10-23',ojf_manifest='ojf_manifest.json',ojf5_manifest='ojf5_manifest.json')
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    om=json.loads((ojf5/'source_manifest.json').read_text(encoding='utf-8'))
    if om.get('window_years')!=5:raise ValueError('Not a five-year OJF dataset')
    om['assets']={'ojf5_'+name:record for name,record in om['assets'].items()}
    for record in checked['annual']:
        if not record['csv_all_cells_exact'] or not record['xlsx_all_cells_verified']:raise ValueError('Unverified OJF5 formats')
        for name,entry in record['files'].items():
            path=ojf5/name
            if digest(path)!=entry['sha256'] or path.stat().st_size!=entry['bytes']:raise ValueError('Changed OJF5 export: '+name)
            shutil.copyfile(path,output/('ojf5_'+name))
    (output/'ojf5_manifest.json').write_text(json.dumps(om,indent=2)+'\n',encoding='utf-8')
    shutil.copyfile(ojf5/'data_dictionary.csv',output/'ojf5_data_dictionary.csv')
    shutil.copyfile(ojf5/'validation.json',output/'ojf5-format-validation.json')
    (output/'prior-annual-preservation.json').write_text(json.dumps({'status':'PASS',
        'all_thirty_annual_files_byte_identical':True,'source_tag':old['tag'],'files':preserved},indent=2)+'\n')
    (output/'README.md').write_text(f'''# Opindx complete datasets 2026-09-v5

Creator: Utz Weitzel (VU Amsterdam; Radboud University Nijmegen).
Citation years 2022-2026; OpenAlex snapshot 2026-09-23; Norwegian Register list 2026-07-26.

- `scores_YEAR.*`: NF/ANS five-year dataset, unchanged.
- `ojf_scores_YEAR.*`: OJF2 two-year dataset, unchanged.
- `ojf5_scores_YEAR.*`: OJF5 five-year dataset, newly added.

Each dataset is complete CSV, XLSX and Parquet, with its own dictionary.
CSV is UTF-8 with BOM and \\N for null; import identifiers and ISSNs as text.
Parquet and CSV retain full precision; Excel has approximately 15-digit numeric precision.
Missing values are not zero. Percentiles depend on the archived website settings.

OJF2 and OJF5 count citations received in the selected year from approved Norwegian Register
sources and include journal self-citations. Their publication windows differ.
The selected recipient group changes the percentile pool, not the OJF citing sources.
All metrics use a twelve-month maturity policy. 2022-2024 are freeze-eligible;
2025-2026 are provisional, and the 2026 citation year is incomplete in this snapshot.

Open numerical inputs: OpenAlex (CC0) and Norwegian Register (NLOD 2.0).
Derived dataset licence: CC BY 4.0. No proprietary metrics, commercial roster,
benchmark matches or empirical paper-comparison data are included.
Source acknowledgements: https://openalex.org and https://kanalregister.hkdir.no/en
Software 0.5.0: https://doi.org/{software_doi}
Dataset version: https://doi.org/{dataset_doi}
Code: https://github.com/bread-n-roses/Opindx/tree/software-v0.5.0/replication
''',encoding='utf-8')
    (output/'RELEASE_NOTES.md').write_text('''# 2026-09-v5 - 23 October 2026

- Adds OJF5 raw/filtered scores, count components, five-year fields and explicit zero/missing statuses for 2022-2026.
- Adds five complete annual CSV/XLSX/Parquet sets, a dictionary and all-cell format checks.
- Preserves all thirty published NF/ANS and OJF2 annual files byte-for-byte.
- Extends the pinned open Work metadata to 2017-2019 for the five-year target windows; reuses the verified 2022-2026 citing reference arrays.
- Checks the recorded calculation, source hashes, score arithmetic, schema-only normalization and browser-window separation. Does not claim a repeated full raw-snapshot replay of NF/ANS or OJF2.
- Keeps frozen-year NF/ANS pins unchanged. Includes no proprietary benchmark inputs or outputs.
''',encoding='utf-8')
    (output/'SHA256SUMS').write_text(''.join(digest(p)+'  '+p.name+'\n' for p in sorted(output.iterdir())),encoding='utf-8')
    release={'kind':'dataset','tag':'data-2026-09-v5','validation_status':'PASS',
        'validation_scope':'Thirty prior annual files byte-identical; new OJF5 source-bound calculation and all-cell CSV/XLSX verification',
        'files':{p.name:{'sha256':digest(p),'bytes':p.stat().st_size} for p in sorted(output.iterdir())}}
    (output/'release-manifest.json').write_text(json.dumps(release,indent=2)+'\n')
    print('Prepared data-2026-09-v5; no publication',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('previous');p.add_argument('ojf5');p.add_argument('output')
    p.add_argument('--software-doi',required=True);p.add_argument('--dataset-doi',required=True)
    a=p.parse_args();build(a.previous,a.ojf5,a.output,software_doi=a.software_doi,dataset_doi=a.dataset_doi)

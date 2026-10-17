"""Combine unchanged NF/ANS files and separately checked OJF annual exports.

Prepares a local release folder. Does not upload, publish or recalculate metrics.
"""
import argparse
import csv
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from opindx_replication.datasets import digest

def build(nfans,ojf,output):
    nfans,ojf,output=map(Path,(nfans,ojf,output))
    old=json.loads((nfans/'release-manifest.json').read_text(encoding='utf-8'))
    checked=json.loads((ojf/'validation.json').read_text(encoding='utf-8'))
    if old['validation_status']!='PASS' or checked['status']!='PASS':raise ValueError('Both source releases must pass validation')
    if sorted(r['year'] for r in checked['annual'])!=list(range(2022,2027)):raise ValueError('Incomplete OJF years')
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):raise ValueError('Use an empty output directory')
    preserved={}
    for name,record in old['files'].items():
        path=nfans/name
        if path.stat().st_size!=record['bytes'] or digest(path)!=record['sha256']:raise ValueError('Changed NF/ANS source: '+name)
        if name.startswith('scores_') and path.suffix in ['.parquet','.csv','.xlsx']:
            shutil.copyfile(path,output/name);preserved[name]=record
        elif name in ['data_dictionary.csv','LICENSE.txt','metric-counts-validation.json','public-data-audit.json']:
            shutil.copyfile(path,output/name)
    manifest=json.loads((nfans/'manifest.json').read_text(encoding='utf-8'))
    manifest['run']='data-2026-09-v4';manifest['created']='2026-10-17'
    manifest['ojf_manifest']='ojf_manifest.json'
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    om=json.loads((ojf/'source_manifest.json').read_text(encoding='utf-8'))
    om['assets']={'ojf_'+name:record for name,record in om['assets'].items()}
    for record in checked['annual']:
        if not record['csv_all_cells_exact'] or not record['xlsx_all_cells_verified']:raise ValueError('Unverified OJF formats')
        for name,entry in record['files'].items():
            path=ojf/name
            if digest(path)!=entry['sha256'] or path.stat().st_size!=entry['bytes']:raise ValueError('Changed OJF export: '+name)
            shutil.copyfile(path,output/('ojf_'+name))
    (output/'ojf_manifest.json').write_text(json.dumps(om,indent=2)+'\n',encoding='utf-8')
    shutil.copyfile(ojf/'data_dictionary.csv',output/'ojf_data_dictionary.csv')
    shutil.copyfile(ojf/'validation.json',output/'ojf-format-validation.json')
    (output/'nf-ans-preservation.json').write_text(json.dumps({'status':'PASS','all_fifteen_files_byte_identical':True,
        'source_tag':old['tag'],'files':preserved},indent=2)+'\n',encoding='utf-8')
    (output/'README.md').write_text('''# Opindx complete datasets 2026-09-v4

Creator: Utz Weitzel (VU Amsterdam; Radboud University Nijmegen).

Citation years 2022-2026; OpenAlex snapshot 2026-09-23; Norwegian Register list 2026-07-26.
`scores_YEAR.*` contains the unchanged NF/ANS five-year-window dataset.
`ojf_scores_YEAR.*` contains the independent OJF two-year-window dataset.
Each is supplied as complete CSV, XLSX and Parquet, with a separate data dictionary.
CSV is UTF-8 BOM, with \\N for null; import identifiers and ISSNs as text.
Parquet/CSV retain full precision. Excel has its normal approximately 15-digit numeric precision.
Missing values are not zeros. Percentiles are computed by the archived website engine according to selected settings.

OJF uses fixed approved Norwegian Register citing channels, including journal self-citations.
Recipient selection changes the displayed journals and percentile pool, not the OJF citing set.
All metrics use a twelve-month maturity policy. 2022-2024 are freeze-eligible;
2025-2026 are provisional, and the September 2026 citation year is incomplete.

Open numerical inputs: OpenAlex (CC0) and Norwegian Register (NLOD 2.0).
Derived dataset licence: CC BY 4.0. No commercial journal-level metrics, membership roster or benchmark matches are included.
Source acknowledgements: https://openalex.org and https://kanalregister.hkdir.no/en
Software: https://doi.org/10.5281/zenodo.23437873
Dataset version: https://doi.org/10.5281/zenodo.23437876
Code: https://github.com/bread-n-roses/Opindx/tree/main/replication
''',encoding='utf-8')
    (output/'RELEASE_NOTES.md').write_text('''# 2026-09-v4 - 17 October 2026

- Adds independent annual OJF raw/filtered data, statuses, count components and two-year field metadata.
- Adds five annual CSV/XLSX/Parquet sets, a dictionary and all-cell format validation.
- Preserves all fifteen earlier NF/ANS annual files byte-for-byte and retains the existing website frozen-year pins.
- Replays all five years of frozen open OJF count components exactly; no new raw-snapshot scan or NF/ANS score calculation.
- Includes no licensed benchmark data, commercial roster or benchmark-derived panel.
''',encoding='utf-8')
    (output/'SHA256SUMS').write_text(''.join(digest(p)+'  '+p.name+'\n' for p in sorted(output.iterdir())),encoding='utf-8')
    release={'kind':'dataset','tag':'data-2026-09-v4','validation_status':'PASS',
        'validation_scope':'All NF/ANS annual files byte-identical; OJF bound frozen-count parity and CSV/XLSX all-cell verification',
        'files':{p.name:{'sha256':digest(p),'bytes':p.stat().st_size} for p in sorted(output.iterdir())}}
    (output/'release-manifest.json').write_text(json.dumps(release,indent=2)+'\n',encoding='utf-8')
    print('Prepared data-2026-09-v4; no publication')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('nfans');p.add_argument('ojf');p.add_argument('output')
    a=p.parse_args();build(a.nfans,a.ojf,a.output)

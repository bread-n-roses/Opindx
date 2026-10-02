"""Resume verified annual format export without repeating scientific calculations."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from build_software_release import digest, validated_files

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from opindx_replication.datasets import export


def finish(work, source=ROOT):
    work, source = Path(work).resolve(), Path(source).resolve()
    read = lambda p: json.loads(p.read_text(encoding='utf-8-sig'))
    if read(work / 'checks/validated-source-inventory.json') != validated_files(source):
        raise ValueError('Validated calculation source changed')
    years = read(source / 'configs/september-2026.json')['years']
    output = work / 'exports'
    manifest = read(output / 'manifest.json')
    if manifest['years'] != years:
        raise ValueError('Export manifest has different years')
    reports = []
    for year in years:
        report = read(output / f'validation_{year}.json')
        parquet = output / f'scores_{year}.parquet'
        if report.get('status') != 'PASS' or report.get('year') != year or not (report.get('reference_parity') or '').startswith('PASS:'):
            raise ValueError('Every annual calculation and reference comparison must pass first')
        actual = digest(parquet)
        asset = manifest['assets'][parquet.name]
        if actual != report['output_sha256'] or actual != asset['sha256'] or report['rows'] != asset['rows']:
            raise ValueError('Annual output differs from its validated receipt')
        reports.append(report)
    import xlsxwriter
    if not hasattr(xlsxwriter, 'Workbook'):
        raise RuntimeError('Use the installed environment with a complete XlsxWriter package')
    started = time.perf_counter()
    export(output, work / 'release-data')
    formats = read(work / 'release-data/validation.json')
    if formats.get('status') != 'PASS' or [r['year'] for r in formats['annual']] != years:
        raise ValueError('Annual format checks did not complete')
    completion = {'status': 'PASS', 'annual': reports, 'publication_performed': False,
                  'completion_method': 'Verified format-step resume after completed annual calculations',
                  'format_resume_seconds': time.perf_counter() - started,
                  'format_resume_completed_utc': datetime.now(timezone.utc).isoformat(),
                  'format_resume_helper_sha256': digest(Path(__file__)),
                  'xlsxwriter_version': xlsxwriter.__version__}
    target = work / 'checks/completion.json'
    temporary = target.with_suffix('.json.partial')
    temporary.write_text(json.dumps(completion, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)
    print('PASS: all annual comparisons and format checks completed; no publication', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path)
    args = parser.parse_args()
    finish(args.work)

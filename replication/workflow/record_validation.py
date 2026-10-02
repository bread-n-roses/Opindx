"""Bind a real completed replication run to the source files it validates."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from build_software_release import digest, validated_files


def record(action, source, work):
    source, work = Path(source).resolve(), Path(work).resolve()
    inventory = work / 'checks/validated-source-inventory.json'
    if action == 'begin':
        if inventory.exists():
            raise ValueError('Preserve the existing source inventory; use a new run for changed source')
        inventory.parent.mkdir(parents=True, exist_ok=True)
        inventory.write_text(json.dumps(validated_files(source), indent=2) + '\n', encoding='utf-8')
        print('Recorded source hashes; run the full replication and reference comparison next')
        return
    before = json.loads(inventory.read_text(encoding='utf-8'))
    if before != validated_files(source):
        raise ValueError('Source or dependencies changed during validation')
    completion = work / 'checks/completion.json'
    report = json.loads(completion.read_text(encoding='utf-8'))
    years = json.loads((source / 'configs/september-2026.json').read_text(encoding='utf-8'))['years']
    if report.get('status') != 'PASS' or sorted(r['year'] for r in report['annual']) != sorted(years):
        raise ValueError('Incomplete replication report')
    if any(not (r.get('reference_parity') or '').startswith('PASS:') for r in report['annual']):
        raise ValueError('Run with --reference and verify every year before preparing a release')
    report['validated_source_sha256'] = before
    report['original_completion_sha256'] = digest(completion)
    report['source_inventory_recorded_utc'] = datetime.fromtimestamp(inventory.stat().st_mtime, timezone.utc).isoformat()
    report['recorded_utc'] = datetime.now(timezone.utc).isoformat()
    resume = work / 'checks/resume-record.json'
    if resume.exists():
        report['execution_resume'] = json.loads(resume.read_text(encoding='utf-8-sig'))
        start = datetime.fromisoformat(report['execution_resume']['initial_started_utc'])
        end = datetime.fromtimestamp(completion.stat().st_mtime, timezone.utc)
        report['total_wall_seconds_including_resume'] = (end - start).total_seconds()
    output = source / 'docs/full-replay.json'
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('PASS: full reference comparison and unchanged validated source; report saved locally')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['begin', 'finish'])
    p.add_argument('source', type=Path)
    p.add_argument('work', type=Path)
    args = p.parse_args()
    record(args.action, args.source, args.work)

"""Build a deterministic source archive locally; never upload or publish."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT_FILES = {'.gitattributes', '.gitignore', '.python-version', 'CHANGELOG.md', 'CITATION.cff',
              'environment.lock', 'LICENSE', 'pyproject.toml', 'README.md'}
TREES = {'configs', 'corrections', 'docs', 'src', 'tests', 'website', 'workflow'}
SUFFIXES = {'.py', '.json', '.md', '.txt', '.csv', '.html', '.js', '.mjs', '.css', '.svg',
            '.png', '.ico', '.woff', '.woff2', '.ttf', '.yml', '.yaml', '.cff'}
SKIP = {'__pycache__', '.git', '.venv', 'node_modules', 'data', 'runs', 'build', 'dist'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validated_files(root):
    """Files whose changes invalidate a previous computation validation."""
    paths = list(root.glob('src/**/*.py')) + list(root.glob('corrections/*.json'))
    paths += list(root.glob('website/site/*.js')) + list(root.glob('website/tools/*.py'))
    paths += [root / name for name in ['configs/september-2026.json',
              'configs/source-code-provenance.json', 'environment.lock', '.python-version']]
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(paths)}


def build(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or root in output.parents:
        raise ValueError('Keep release output outside the source tree')
    completion = json.loads((root / 'docs' / 'full-replay.json').read_text(encoding='utf-8'))
    if completion.get('status') != 'PASS' or len(completion.get('annual', [])) != 5:
        raise ValueError('A completed five-year replay is required')
    if any(not (r.get('reference_parity') or '').startswith('PASS:') for r in completion['annual']):
        raise ValueError('Every annual output must match the reference release')
    if completion.get('validated_source_sha256') != validated_files(root):
        raise ValueError('Calculation, website or dependency files differ from the validated source')
    version = json.loads((root / 'configs' / 'zenodo-software.json').read_text(encoding='utf-8'))['metadata']['version']
    tag = 'software-v' + version
    selected = []
    for path in sorted(root.rglob('*'), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if any(part in SKIP or part.endswith('.egg-info') for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError('Symlinks are not accepted: ' + str(relative))
        if not path.is_file():
            continue
        if any(word in str(relative).lower() for word in ('token', 'secret', 'credential')):
            raise ValueError('Credential-like path: ' + str(relative))
        if len(relative.parts) == 1:
            allowed = path.name in ROOT_FILES
        else:
            allowed = relative.parts[0] in TREES and (path.suffix.lower() in SUFFIXES or path.name.startswith('LICENSE') or path.name in {'OFL.txt', 'CNAME'})
        if not allowed:
            raise ValueError('Unexpected source file: ' + str(relative))
        selected.append((path, relative))
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Use an empty output folder')
    archive = output / f'opindx-replication-{version}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path, relative in selected:
            item = zipfile.ZipInfo('replication/' + relative.as_posix(), date_time=(2026, 10, 2, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            z.writestr(item, path.read_bytes(), compresslevel=9)
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError('Archive integrity failed')
        for path, relative in selected:
            if z.read('replication/' + relative.as_posix()) != path.read_bytes():
                raise ValueError('Archive source parity failed')
    (output / 'README.md').write_text(
        '# Opindx software ' + version + '\n\n'
        'Unpack the source archive, then follow replication/README.md.\n'
        'The archive includes source, pinned environment, reviewed identity corrections,\n'
        'website source, tests, provenance, licenses and actual replay validation.\n'
        'Raw snapshots and register lists are supplied separately. No proprietary\n'
        'benchmark files, credentials or raw register contact details are included.\n\n'
        'Prepared locally. Publication is paused. Creator: Utz Weitzel (VU Amsterdam; Radboud University Nijmegen).\n', encoding='utf-8')
    (output / 'RELEASE_NOTES.md').write_bytes((root / 'CHANGELOG.md').read_bytes())
    (output / 'LICENSE.txt').write_bytes((root / 'LICENSE').read_bytes())
    (output / 'SHA256SUMS').write_text(''.join(
        digest(p) + '  ' + p.name + '\n' for p in sorted(output.iterdir())), encoding='utf-8')
    manifest = {'kind': 'software', 'tag': tag, 'validation_status': 'PASS',
                'validation_scope': 'Five-year raw-input replay, reference table parity, unit checks and archive byte integrity',
                'files': {p.name: {'sha256': digest(p), 'bytes': p.stat().st_size} for p in sorted(output.iterdir())}}
    (output / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'Prepared {tag}: {len(selected)} source files; archive integrity PASS; no publication')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    build(args.source, args.output)

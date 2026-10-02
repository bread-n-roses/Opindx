"""Version local styles and module imports consistently for a deployed commit."""
import re
import sys
from pathlib import Path


def version_assets(site, version):
    if not re.fullmatch(r"[0-9a-f]{7,40}", version):
        raise ValueError("Expected a Git commit hash")
    for path in Path(site).glob("*.html"):
        source = path.read_text(encoding="utf-8")
        source = re.sub(r'((?:href|src)=")(styles\.css|app\.js)(?:\?v=[0-9a-f]+)?(")',
                        lambda m: m[1] + m[2] + "?v=" + version + m[3], source)
        path.write_text(source, encoding="utf-8")
    for path in Path(site).glob("*.js"):
        source = path.read_text(encoding="utf-8")
        source = re.sub(r"(from ['\"])(\./[a-zA-Z0-9-]+\.js)(?:\?v=[0-9a-f]+)?(['\"])",
                        lambda m: m[1] + m[2] + "?v=" + version + m[3], source)
        path.write_text(source, encoding="utf-8")


if __name__ == "__main__":
    version_assets(*sys.argv[1:])

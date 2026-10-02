"""Fetch only safe, explicitly listed release assets; no data regeneration."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

tag,folder=sys.argv[1],Path(sys.argv[2])
if not re.fullmatch(r"(?:data-|software-v)[A-Za-z0-9._-]+",tag):raise ValueError("Invalid tag")
manifest=json.loads((folder/"release-manifest.json").read_text())
if manifest["tag"]!=tag:raise ValueError("Manifest tag differs")
for name in manifest["files"]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*",name):raise ValueError("Unsafe asset name")
    subprocess.run(["gh","release","download",tag,"--repo",os.environ["GITHUB_REPOSITORY"],"--pattern",name,"--dir",str(folder)],check=True)

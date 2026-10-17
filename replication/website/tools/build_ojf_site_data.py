"""Install verified released OJF annual files and derive browser sidecars."""
from pathlib import Path
import argparse
import json
import sys

root=Path(__file__).resolve().parents[1]
source=next((p for p in [root/'replication/src',root.parent/'src'] if p.is_dir()),None)
if source is None:raise RuntimeError('Keep the website builder with the replication source tree')
sys.path.insert(0,str(source))
from opindx_replication.ojf.publication import install

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('annual',type=Path);p.add_argument('site_data',type=Path)
    a=p.parse_args();print(json.dumps(install(a.annual,a.site_data),indent=2))

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
    p.add_argument('--five-year',type=Path,help='Verified OJF5 annual directory; installs both OJF windows and comparison columns')
    a=p.parse_args()
    result=install(a.annual,a.site_data,other_annual=a.five_year)
    if a.five_year is not None:result['ojf5']=install(a.five_year,a.site_data,window_years=5,other_annual=a.annual)
    print(json.dumps(result,indent=2))

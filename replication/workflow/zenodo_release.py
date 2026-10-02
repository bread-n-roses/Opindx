"""Explicit, resumable dataset/software publication. Dry-run is the default."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

def digest(path,algorithm='sha256'):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,algorithm).hexdigest()

def load_bundle(folder,metadata,kind,tag):
    folder=Path(folder).resolve()
    if kind not in ['dataset','software']:raise ValueError('Unknown release kind')
    prefix='data-' if kind=='dataset' else 'software-v'
    if not tag.startswith(prefix) or not re.fullmatch(r'[A-Za-z0-9._-]+',tag):raise ValueError('Wrong release tag prefix')
    manifest=json.loads((folder/'release-manifest.json').read_text(encoding='utf-8'))
    if manifest['kind']!=kind or manifest['tag']!=tag:raise ValueError('Bundle and requested release differ')
    files=[]
    for name,entry in manifest['files'].items():
        path=(folder/name).resolve()
        if path.parent!=folder or path.name!=name or path.is_symlink():raise ValueError('Unsafe release filename')
        if any(x in name.lower() for x in ['token','secret','credential']):raise ValueError('Credential-like release file')
        if path.stat().st_size!=entry['bytes'] or digest(path)!=entry['sha256']:raise ValueError('Release file changed: '+name)
        files.append(path)
    if not files or len(files)>99:raise ValueError('Empty or oversized file count')
    if manifest.get('validation_status')!='PASS':raise ValueError('Bundle validation is not PASS')
    files.append(folder/'release-manifest.json')
    data=json.loads(Path(metadata).read_text(encoding='utf-8'))['metadata']
    if data.get('upload_type')!=kind:raise ValueError('Metadata resource type differs')
    if not data.get('creators') or any(not c.get('name','').strip() for c in data['creators']):
        raise ValueError('Complete the creator names before publication')
    for key in ['title','description','license','version']:
        if not data.get(key):raise ValueError('Missing metadata: '+key)
    version=tag.removeprefix('data-') if kind=='dataset' else tag.removeprefix('software-v')
    if data['version']!=version:raise ValueError('Tag and metadata version differ')
    return files,data

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('Authenticated redirect refused')

class Client:
    def __init__(self,token):
        self.token=token;self.opener=urllib.request.build_opener(NoRedirect())
    def request(self,path,method='GET',data=None,file=None):
        url=path if path.startswith('https://') else 'https://zenodo.org/api/deposit/depositions'+path
        parsed=urllib.parse.urlparse(url)
        if parsed.scheme!='https' or parsed.hostname!='zenodo.org' or not parsed.path.startswith('/api/'):
            raise ValueError('Unexpected authenticated API destination')
        body=json.dumps(data).encode() if data is not None else None
        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json','User-Agent':'Opindx-release'}
        if file is not None:
            # Stream local files; do not put tokens in URLs, shell arguments or logs.
            headers.update({'Content-Type':'application/octet-stream','Content-Length':str(Path(file).stat().st_size)})
            with Path(file).open('rb') as stream:
                req=urllib.request.Request(url,data=stream,headers=headers,method=method)
                with self.opener.open(req,timeout=300) as response:raw=response.read()
        else:
            req=urllib.request.Request(url,data=body,headers=headers,method=method)
            with self.opener.open(req,timeout=60) as response:raw=response.read()
        return json.loads(raw) if raw else None
    def deposits(self):
        result=[];page=1
        while True:
            batch=self.request(f'?all_versions=1&size=100&page={page}')
            result.extend(batch)
            if len(batch)<100:return result
            page+=1

def remote_files(record):
    return {f.get('filename',f.get('key')):f for f in record.get('files',[])}

def matches(file,path):
    return file.get('checksum','').removeprefix('md5:')==digest(path,'md5') and file.get('filesize',file.get('size'))==path.stat().st_size

def publish(client,files,metadata,kind,tag):
    marker=f'Opindx release series: {kind}; tag: {tag}; manifest SHA256: {digest(files[-1])}'
    prefix=f'Opindx release series: {kind};'
    records=[r for r in client.deposits() if r.get('metadata',{}).get('notes','').startswith(prefix)]
    same=[r for r in records if f'; tag: {tag};' in r['metadata']['notes']]
    if len(same)>1:raise ValueError('Multiple deposits match this release; resolve before proceeding')
    if same:
        record=client.request('/'+str(same[0]['id']))
        if record['metadata']['notes']!=marker:raise ValueError('This tag already has a different bundle; use a new version')
    else:
        published=[r for r in records if r.get('submitted')]
        if published:
            previous=max(published,key=lambda r:r['created'])
            prior=client.request('/'+str(previous['id']))
            draft_link=prior.get('links',{}).get('latest_draft')
            if draft_link and draft_link.rstrip('/').split('/')[-1]!=str(prior['id']):
                record=client.request(draft_link)
                if record.get('submitted'):raise ValueError('Unexpected published latest draft')
                if record.get('metadata',{}).get('notes')!=prior['metadata']['notes']:
                    raise ValueError('A different draft is already being prepared')
            else:
                changed=client.request('/'+str(previous['id'])+'/actions/newversion','POST')
                record=client.request(changed['links']['latest_draft'])
        else:
            # Metadata on creation makes retries discover the first draft.
            record=client.request('','POST',{'metadata':{**metadata,'notes':marker}})
    existing=remote_files(record)
    expected={p.name:p for p in files}
    if record.get('submitted'):
        if set(existing)!=set(expected) or any(not matches(existing[n],p) for n,p in expected.items()):
            raise ValueError('Published files differ; never overwrite a published version')
        return record
    record=client.request('/'+str(record['id']),'PUT',{'metadata':{**metadata,'notes':marker}})
    existing=remote_files(record)
    for name,entry in existing.items():
        if name not in expected:
            client.request('/'+str(record['id'])+'/files/'+str(entry['id']),'DELETE')
    for name,path in expected.items():
        if name in existing and matches(existing[name],path):continue
        upload=record['links']['bucket'].rstrip('/')+'/'+urllib.parse.quote(name,safe='')
        result=client.request(upload,'PUT',file=path)
        if not matches(result,path):raise ValueError('Uploaded checksum differs: '+name)
    record=client.request('/'+str(record['id']))
    observed=remote_files(record)
    if set(observed)!=set(expected) or any(not matches(observed[n],p) for n,p in expected.items()):
        raise ValueError('Final remote file verification failed')
    return client.request('/'+str(record['id'])+'/actions/publish','POST')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--files',type=Path,required=True);parser.add_argument('--metadata',type=Path,required=True)
    parser.add_argument('--kind',choices=['software','dataset'],required=True);parser.add_argument('--tag',required=True)
    parser.add_argument('--publish',action='store_true');parser.add_argument('--receipt',type=Path,default=Path('zenodo-receipt.json'))
    args=parser.parse_args()
    files,metadata=load_bundle(args.files,args.metadata,args.kind,args.tag)
    if not args.publish:
        print(json.dumps({'status':'READY','kind':args.kind,'tag':args.tag,'files':[p.name for p in files],'network_used':False}));return
    token=os.environ.get('ZENODO_TOKEN')
    if not token:raise ValueError('ZENODO_TOKEN environment variable is required')
    record=publish(Client(token),files,metadata,args.kind,args.tag)
    receipt={'kind':args.kind,'tag':args.tag,'record_id':record['id'],'doi':record.get('doi') or record.get('metadata',{}).get('doi'),
        'submitted':record.get('submitted'), 'state':record.get('state'), 'record_url':record.get('links',{}).get('record_html')}
    args.receipt.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt))

if __name__=='__main__':
    try:main()
    except urllib.error.HTTPError as error:
        print('Zenodo HTTP failure:',error.code,'(response body and credentials withheld)',file=sys.stderr);sys.exit(1)
    except (ValueError,RuntimeError,OSError) as error:
        print('Release stopped:',str(error),file=sys.stderr);sys.exit(1)

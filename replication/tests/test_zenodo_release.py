import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('publisher',Path(__file__).resolve().parents[1]/'workflow/zenodo_release.py')
publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher)

class FakeClient:
    def __init__(self):self.records=[];self.calls=[];self.fail_upload=False
    def deposits(self):return copy.deepcopy(self.records)
    def request(self,path,method='GET',data=None,file=None):
        self.calls.append((path,method))
        if path=='' and method=='POST':
            n=len(self.records)+1
            rec={'id':n,'created':str(n),'metadata':data['metadata'],'files':[], 'submitted':False,
                 'links':{'bucket':f'https://zenodo.org/api/files/{n}'}}
            self.records.append(rec);return copy.deepcopy(rec)
        if path.startswith('https://zenodo.org/api/files/'):
            if self.fail_upload:self.fail_upload=False;raise OSError('simulated upload interruption')
            n=int(path.split('/')[-2]);name=Path(file).name
            rec=self.records[n-1];rec['files']=[f for f in rec['files'] if f['filename']!=name]
            entry={'filename':name,'filesize':Path(file).stat().st_size,'checksum':publisher.digest(file,'md5'),'id':name}
            rec['files'].append(entry);return entry
        if path.startswith('https://zenodo.org/api/deposit/depositions/'):
            path='/'+path.rsplit('/',1)[1]
        n=int(path.split('/')[1]);rec=self.records[n-1]
        if path.endswith('/actions/publish'):
            rec['submitted']=True;rec['doi']=f'10.0000/example.{n}';return copy.deepcopy(rec)
        if path.endswith('/actions/newversion'):
            new=copy.deepcopy(rec);new_id=len(self.records)+1
            new.update(id=new_id,created=str(new_id),submitted=False,links={'bucket':f'https://zenodo.org/api/files/{new_id}'})
            new.pop('doi',None);self.records.append(new)
            return {'links':{'latest_draft':f'https://zenodo.org/api/deposit/depositions/{new_id}'}}
        if '/files/' in path and method=='DELETE':
            rec['files']=[f for f in rec['files'] if f['id']!=path.split('/files/')[1]];return None
        if method=='PUT':rec['metadata']=data['metadata']
        return copy.deepcopy(rec)

class ZenodoReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.file=self.root/'scores_2022.csv';self.file.write_text('title,NF\nExample,100\n')
        self.manifest=self.root/'release-manifest.json'
        self.manifest.write_text(json.dumps({'kind':'dataset','tag':'data-2026-09-v2','validation_status':'PASS',
            'files':{self.file.name:{'bytes':self.file.stat().st_size,'sha256':publisher.digest(self.file)}}}))
        self.meta={'title':'Example','description':'Synthetic data','upload_type':'dataset','license':'cc-by-4.0','version':'2026-09-v2','creators':[{'name':'Example, Test'}]}
        self.metadata=self.root/'metadata.json';self.metadata.write_text(json.dumps({'metadata':self.meta}))
        self.files=[self.file,self.manifest]
    def test_bundle_requires_complete_creators_and_rejects_changed_files(self):
        files,_=publisher.load_bundle(self.root,self.metadata,'dataset','data-2026-09-v2');self.assertEqual(len(files),2)
        self.meta['creators']=[];self.metadata.write_text(json.dumps({'metadata':self.meta}))
        with self.assertRaisesRegex(ValueError,'creator'):publisher.load_bundle(self.root,self.metadata,'dataset','data-2026-09-v2')
        self.file.write_text('changed')
        with self.assertRaisesRegex(ValueError,'changed'):publisher.load_bundle(self.root,self.metadata,'dataset','data-2026-09-v2')
    def test_first_publication_and_retry_create_one_record(self):
        client=FakeClient()
        first=publisher.publish(client,self.files,self.meta,'dataset','data-2026-09-v2')
        self.assertTrue(first['submitted']);self.assertEqual(len(client.records),1)
        again=publisher.publish(client,self.files,self.meta,'dataset','data-2026-09-v2')
        self.assertEqual(again['id'],first['id']);self.assertEqual(len(client.records),1)
    def test_interrupted_upload_resumes_same_draft(self):
        client=FakeClient();client.fail_upload=True
        with self.assertRaises(OSError):publisher.publish(client,self.files,self.meta,'dataset','data-2026-09-v2')
        self.assertEqual(len(client.records),1)
        result=publisher.publish(client,self.files,self.meta,'dataset','data-2026-09-v2')
        self.assertTrue(result['submitted']);self.assertEqual(len(client.records),1)
    def test_new_data_version_preserves_old_published_files(self):
        client=FakeClient();publisher.publish(client,self.files,self.meta,'dataset','data-2026-09-v2')
        old=copy.deepcopy(client.records[0]);self.file.write_text('title,NF\nExample,99\n')
        self.manifest.write_text('a new bundle manifest');self.meta['version']='2026-10'
        result=publisher.publish(client,self.files,self.meta,'dataset','data-2026-10')
        self.assertEqual(result['id'],2);self.assertTrue(result['submitted']);self.assertEqual(client.records[0],old)
    def test_changed_bundle_cannot_reuse_same_tag(self):
        client=FakeClient();publisher.publish(client,self.files,self.meta,'dataset','data-2026-09-v2')
        self.manifest.write_text('changed')
        with self.assertRaisesRegex(ValueError,'different bundle'):publisher.publish(client,self.files,self.meta,'dataset','data-2026-09-v2')

if __name__=='__main__':unittest.main()

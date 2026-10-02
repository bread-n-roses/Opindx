import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'workflow'))
import finish_formats as resume


class FormatResumeTest(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder);source=root/'source';work=root/'work'
        def save(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding='utf-8')
        save(source/'configs/september-2026.json', {'years':[2022]})
        save(work/'checks/validated-source-inventory.json', {})
        output=work/'exports';output.mkdir()
        parquet=output/'scores_2022.parquet';parquet.write_bytes(b'synthetic fixture')
        digest=resume.digest(parquet)
        save(output/'manifest.json', {'years':[2022], 'assets':{parquet.name:{'sha256':digest,'rows':1}}})
        save(output/'validation_2022.json', {'status':'PASS','year':2022,'reference_parity':'PASS: synthetic fixture','output_sha256':digest,'rows':1})
        return source,work,parquet

    def test_changed_output_is_rejected_before_export(self):
        with tempfile.TemporaryDirectory() as folder:
            source,work,parquet=self.fixture(folder)
            parquet.write_bytes(b'changed')
            with patch.object(resume,'validated_files',return_value={}), patch.object(resume,'export') as export:
                with self.assertRaisesRegex(ValueError,'differs from its validated receipt'):
                    resume.finish(work,source)
                export.assert_not_called()
            self.assertFalse((work/'checks/completion.json').exists())

    def test_failed_conversion_never_marks_run_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            source,work,_=self.fixture(folder)
            with patch.object(resume,'validated_files',return_value={}), \
                 patch.object(resume,'export',side_effect=RuntimeError('conversion failed')), \
                 patch.dict(sys.modules,{'xlsxwriter':SimpleNamespace(Workbook=object,__version__='test')}):
                with self.assertRaisesRegex(RuntimeError,'conversion failed'):
                    resume.finish(work,source)
            self.assertFalse((work/'checks/completion.json').exists())


if __name__ == '__main__':unittest.main()

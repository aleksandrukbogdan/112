import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from deploy import installer as I

class InstallerTests(unittest.TestCase):
    def test_preflight_apply_restore_and_custom_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);package=root/'package';target=root/'target';backup=root/'backup';target.mkdir();backup.mkdir()
            (package/'payload/api').mkdir(parents=True);(target/'api').mkdir();p=target/'api/file.py';p.write_text('old\n')
            (target/'.env').write_text('private=preserve');(target/'state').mkdir();(target/'state/data').write_text('preserve')
            src=package/'payload/api/file.py';src.write_text('new\n');new=package/'payload/api/added.py';new.write_text('added\n')
            m={'files':{'api/file.py':{'sha256':I.digest(src),'baseline_canonical':I.canonical(p)},'api/added.py':{'sha256':I.digest(new)}}}
            with patch.object(I,'ROOT',package):
                self.assertEqual(I.preflight(target,m),[]);I.copy_payload(target,backup,m)
                self.assertEqual(p.read_text(),'new\n');self.assertEqual(I.preflight(target,m),[])
                I.restore_files(target,backup);self.assertEqual(p.read_text(),'old\n');self.assertFalse((target/'api/added.py').exists())
                self.assertEqual((target/'.env').read_text(),'private=preserve');self.assertEqual((target/'state/data').read_text(),'preserve')
                p.write_text('custom\n')
                with self.assertRaises(RuntimeError):I.preflight(target,m)
                self.assertEqual(I.preflight(target,m,True),['api/file.py'])
    def test_protected_and_corrupt_payload(self):
        for path in ('../escape','/etc/config','.env','voice/app.py','state/data','tts/a','docker-compose.yml'):
            self.assertTrue(I.protected(path),path)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):I.preflight(Path(tmp),{'files':{'api/missing.py':{'sha256':'bad'}}})
if __name__=='__main__':unittest.main()

import importlib.util
from pathlib import Path
import secrets
import tempfile
import unittest
from catalog import TASKS

spec=importlib.util.spec_from_file_location('guard',Path(__file__).with_name('exec-safe.py'))
guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)

class OperationsTests(unittest.TestCase):
    def test_catalog_has_fifty_distinct_tickets(self):
        self.assertEqual(list(range(1,51)),[t['id'] for t in TASKS])
        self.assertEqual(50,len({t['title'] for t in TASKS}))
        for t in TASKS:
            self.assertTrue(t['checks'])
            for node,command in t['checks']+t['setup']:
                self.assertIn(node,('admin','app','data'))
                self.assertTrue(command)

    def test_private_value_cannot_escape(self):
        with tempfile.TemporaryDirectory() as d:
            value=secrets.token_hex(32)
            (Path(d)/'token').write_text(value)
            output,blocked=guard.redact('prefix '+value+' suffix',d)
            self.assertTrue(blocked);self.assertNotIn(value,output)

    def test_private_key_blocks_but_public_key_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            output,blocked=guard.redact('-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----',d)
            self.assertTrue(blocked);self.assertNotIn('fake',output)
            public='ssh-ed25519 non-secret-public-material'
            (Path(d)/'known_hosts').write_text(public)
            self.assertEqual((public,False),guard.redact(public,d))

    def test_business_contents_blocked_aggregate_permitted(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(guard.redact('PRIVATE_BUSINESS_RECORD_TEST',d)[1])
            self.assertEqual(('3\n',False),guard.redact('3\n',d))

    def test_guard_never_follows_secret_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            outside=Path(d)/'outside';outside.write_text('synthetic-nonsecret-value')
            root=Path(d)/'secrets';root.mkdir();(root/'link').symlink_to(outside)
            self.assertEqual(('synthetic-nonsecret-value',False),guard.redact('synthetic-nonsecret-value',root))

if __name__=='__main__':unittest.main()

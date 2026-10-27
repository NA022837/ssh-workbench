import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import build_windows


class WindowsArchiveTests(unittest.TestCase):
    def test_single_payload_excludes_unrelated_source_and_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            payload=root/'generated.bin';payload.write_bytes(b'test payload, not an executable')
            (root/'profiles.json').write_text('not-for-distribution')
            with patch.object(build_windows,'ROOT',root):
                output=build_windows.package_release(payload,'onefile')
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()),{'generated.bin','START-HERE.txt'})
            self.assertTrue(output.with_suffix('.zip.sha256').read_text().startswith(
                            hashlib.sha256(output.read_bytes()).hexdigest()))

    def test_portable_folder_preserves_nested_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);payload=root/'SSH Workbench'
            (payload/'_internal').mkdir(parents=True)
            (payload/'app.bin').write_bytes(b'test')
            (payload/'_internal'/'module.dat').write_bytes(b'dependency')
            with patch.object(build_windows,'ROOT',root):
                output=build_windows.package_release(payload,'onedir')
            with zipfile.ZipFile(output) as archive:
                self.assertIn('SSH Workbench/_internal/module.dat',archive.namelist())

    def test_rejects_cross_compilation_before_running_commands(self):
        with patch.object(build_windows.sys,'platform','linux'),patch.object(build_windows.sys,'argv',['build_windows.py']),\
             patch.object(build_windows,'run') as run:
            with self.assertRaises(SystemExit) as raised:build_windows.main()
            self.assertEqual(raised.exception.code,2)
            run.assert_not_called()

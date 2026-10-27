import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import MagicMock,patch
import paramiko
from backend import delete_directory
from test_context_menu import FolderSFTP


class TreeSFTP(FolderSFTP):
    def normalize(self,path):return str(Path(path).resolve())
    def rmdir(self,path):os.rmdir(path)
    def listdir_iter(self,path,read_aheads=1):
        for name in sorted(os.listdir(path)):
            yield paramiko.SFTPAttributes.from_stat(os.lstat(Path(path)/name),filename=name)


class FolderDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'selected';self.root.mkdir()
        self.sftp=TreeSFTP();self.client=MagicMock();self.client.open_sftp.return_value=self.sftp
        self.stop=threading.Event()

    def entry(self):
        attrs=self.sftp.lstat(self.root)
        return dict(path=str(self.root),kind='Directory',size=attrs.st_size,mtime=attrs.st_mtime,
                    permissions=stat.filemode(attrs.st_mode))

    def test_empty_folder_delete(self):
        delete_directory(self.client,self.entry(),self.stop)
        self.assertFalse(self.root.exists())

    def test_nonrecursive_refuses_nonempty_folder_without_removing_children(self):
        (self.root/'keep').write_text('keep')
        with self.assertRaises(OSError):delete_directory(self.client,self.entry(),self.stop)
        self.assertEqual((self.root/'keep').read_text(),'keep')

    def test_recursive_deletes_nested_tree_and_keeps_external_symlink_target(self):
        outside=Path(self.tmp.name)/'outside';outside.mkdir();(outside/'keep').write_text('keep')
        (self.root/'nested').mkdir();(self.root/'nested'/'data').write_text('data')
        (self.root/'outside-link').symlink_to(outside,target_is_directory=True)
        progress=[]
        delete_directory(self.client,self.entry(),self.stop,True,progress.append)
        self.assertFalse(self.root.exists())
        self.assertEqual((outside/'keep').read_text(),'keep')
        self.assertEqual(progress[-1],100)

    def test_root_cancel_and_stale_metadata_make_no_changes(self):
        with self.assertRaises(ValueError):delete_directory(self.client,dict(path='/'),self.stop,True)
        entry=self.entry();self.root.chmod(0o700)
        if stat.filemode(self.root.stat().st_mode)==entry['permissions']:self.root.chmod(0o755)
        with self.assertRaisesRegex(ValueError,'changed'):delete_directory(self.client,entry,self.stop,True)
        self.stop.set()
        with self.assertRaises(RuntimeError):delete_directory(self.client,self.entry(),self.stop,True)
        self.assertTrue(self.root.exists())

    def test_non_utf8_listing_error_happens_before_any_deletion(self):
        (self.root/'keep').write_text('keep')
        with patch.object(self.sftp,'listdir_iter',side_effect=UnicodeDecodeError('utf-8',b'\xa0',0,1,'invalid')):
            with self.assertRaisesRegex(ValueError,'before removing any items'):
                delete_directory(self.client,self.entry(),self.stop,True)
        self.assertEqual((self.root/'keep').read_text(),'keep')

    def test_directory_swapped_for_symlink_during_plan_is_refused(self):
        child=self.root/'child';child.mkdir()
        outside=Path(self.tmp.name)/'outside';outside.mkdir();(outside/'keep').write_text('keep')
        original=self.sftp.listdir_iter
        def changed(path,read_aheads=1):
            rows=list(original(path,read_aheads))
            if path==str(self.root):
                child.rmdir();child.symlink_to(outside,target_is_directory=True)
            return iter(rows)
        with patch.object(self.sftp,'listdir_iter',side_effect=changed):
            with self.assertRaises(ValueError):delete_directory(self.client,self.entry(),self.stop,True)
        self.assertTrue(self.root.exists())
        self.assertEqual((outside/'keep').read_text(),'keep')

    def test_failure_after_removal_reports_partial_deletion(self):
        (self.root/'a').write_text('a');(self.root/'b').write_text('b')
        original=self.sftp.remove
        calls=[]
        def remove(path):
            if calls:raise PermissionError('denied')
            calls.append(path);original(path)
        with patch.object(self.sftp,'remove',side_effect=remove):
            with self.assertRaisesRegex(RuntimeError,'after 1 item'):
                delete_directory(self.client,self.entry(),self.stop,True)
        self.assertTrue(self.root.exists())
        self.assertEqual(len(list(self.root.iterdir())),1)

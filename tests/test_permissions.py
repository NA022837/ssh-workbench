import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import MagicMock,patch
from types import SimpleNamespace
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from backend import read_permissions,change_permissions
from permissions_dialog import PermissionsDialog,parse_mode
from test_file_manager import DiskSFTP
from profiles import Store,validate
from app import SessionPage

APP=QApplication.instance() or QApplication([])


class PermissionsBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'example'
        self.sftp=DiskSFTP();self.client=MagicMock();self.client.open_sftp.return_value=self.sftp
        self.stop=threading.Event()

    def entry(self,kind='File'):return dict(path=str(self.path),kind=kind)

    def test_change_file_mode_preserves_contents_and_ownership(self):
        self.path.write_text('unchanged');os.chmod(self.path,0o600)
        before=self.path.stat()
        snapshot=read_permissions(self.client,self.entry())
        updated=change_permissions(self.client,snapshot,0o640,self.stop)
        self.assertEqual(updated['mode'],0o640)
        self.assertEqual(self.path.read_text(),'unchanged')
        self.assertEqual((before.st_uid,before.st_gid),(self.path.stat().st_uid,self.path.stat().st_gid))

    def test_folder_change_does_not_recurse(self):
        self.path.mkdir();child=self.path/'keep';child.write_text('keep');child.chmod(0o600)
        snapshot=read_permissions(self.client,self.entry('Directory'))
        change_permissions(self.client,snapshot,0o750,self.stop)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o750)
        self.assertEqual(stat.S_IMODE(child.stat().st_mode),0o600)

    def test_symlinks_are_refused_without_changing_target(self):
        target=Path(self.tmp.name)/'target';target.write_text('keep');target.chmod(0o600)
        self.path.symlink_to(target)
        with self.assertRaises(ValueError):read_permissions(self.client,self.entry('Link'))
        with self.assertRaises(ValueError):change_permissions(self.client,dict(path=str(self.path)),0o777,self.stop)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o600)

    def test_changed_permissions_are_not_overwritten(self):
        self.path.write_text('keep');self.path.chmod(0o600)
        snapshot=read_permissions(self.client,self.entry());self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'changed on the server'):
            change_permissions(self.client,snapshot,0o777,self.stop)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o644)

    def test_cancelled_and_invalid_modes_make_no_changes(self):
        self.path.write_text('keep');self.path.chmod(0o600)
        snapshot=read_permissions(self.client,self.entry())
        for invalid in (-1,0o10000,True,'755'):
            with self.assertRaises(ValueError):change_permissions(self.client,snapshot,invalid,self.stop)
        self.stop.set()
        with self.assertRaises(RuntimeError):change_permissions(self.client,snapshot,0o777,self.stop)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o600)

    def test_permission_denied_is_reported(self):
        self.path.write_text('keep');self.path.chmod(0o600)
        snapshot=read_permissions(self.client,self.entry())
        with patch.object(self.sftp,'chmod',side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):change_permissions(self.client,snapshot,0o777,self.stop)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o600)


class PermissionsDialogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        profile=validate(dict(name='Example',host='example.test',username='operator',auth='agent'))
        self.session=SessionPage(profile,Store(self.tmp.name))
        self.snapshot=dict(path='/tmp/example',mode=0o644,symbolic='-rw-r--r--',uid=1000,gid=1000,
                           version=(stat.S_IFREG|0o644,1000,1000))
        self.dialog=PermissionsDialog(self.session,self.snapshot)

    def tearDown(self):
        self.dialog.close();self.dialog.deleteLater();self.session.close();self.session.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.tmp.cleanup()

    def test_checkbox_and_octal_sync_including_special_bits(self):
        self.assertTrue(self.dialog.boxes[0o400].isChecked())
        self.assertFalse(self.dialog.boxes[0o002].isChecked())
        self.dialog.boxes[0o002].setChecked(True)
        self.assertEqual(parse_mode(self.dialog.mode.text()),0o646)
        self.dialog.boxes[0o1000].setChecked(True)
        self.assertEqual(parse_mode(self.dialog.mode.text()),0o1646)
        self.dialog.mode.setText('755')
        self.assertFalse(self.dialog.boxes[0o1000].isChecked())
        self.assertTrue(self.dialog.boxes[0o001].isChecked())

    def test_invalid_octal_disables_apply(self):
        for value in ('888','12','0o77','-777'):
            self.dialog.mode.setText(value)
            self.assertFalse(self.dialog.apply_button.isEnabled())
        self.dialog.mode.setText('0640')
        self.assertTrue(self.dialog.apply_button.isEnabled())

    def test_close_without_apply_performs_no_remote_operation(self):
        self.session.task=MagicMock()
        self.dialog.mode.setText('777')
        self.dialog.reject()
        self.session.task.assert_not_called()

    def test_apply_failure_retains_choices_and_shows_error(self):
        self.session.client=MagicMock();self.session.worker=SimpleNamespace(stop_event=threading.Event())
        self.session.task=lambda fn,done,failed:failed('PermissionError: denied')
        self.dialog.mode.setText('640');self.dialog.apply()
        self.assertEqual(parse_mode(self.dialog.mode.text()),0o640)
        self.assertIn('denied',self.dialog.status.text())
        self.assertEqual(self.dialog.snapshot['mode'],0o644)
        self.assertFalse(self.dialog.applying)

    def test_apply_success_updates_current_mode(self):
        self.session.client=MagicMock();self.session.worker=SimpleNamespace(stop_event=threading.Event())
        updated=dict(self.snapshot,mode=0o640,symbolic='-rw-r-----')
        self.session.task=lambda fn,done,failed:done(updated)
        self.dialog.mode.setText('640');self.dialog.apply()
        self.assertIn('0640',self.dialog.current.text())
        self.assertIn('verified',self.dialog.status.text())
        self.assertEqual(self.dialog.snapshot['mode'],0o640)

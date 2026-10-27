"""File manager regressions: disk-backed SFTP saves, sorted identity and editor close behavior."""
import os
import stat
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from app import SessionPage
from backend import read_editable, save_text, EDIT_LIMIT, delete_file
from profiles import Store, validate
from remote_editor import RemoteEditor

APP=QApplication.instance() or QApplication([])


class DiskSFTP:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def get_channel(self):return MagicMock()
    def lstat(self,path):
        a=os.lstat(path)
        return SimpleNamespace(st_mode=a.st_mode,st_uid=a.st_uid,st_gid=a.st_gid,st_mtime=int(a.st_mtime),st_size=a.st_size)
    stat=lstat
    def open(self,path,mode):return open(path,'xb' if mode=='wx' else mode)
    def chmod(self,path,mode):os.chmod(path,mode)
    def chown(self,path,uid,gid):os.chown(path,uid,gid)
    def remove(self,path):os.unlink(path)
    def posix_rename(self,source,dest):os.replace(source,dest)
    def rename(self,source,dest):
        os.link(source,dest)  # atomic no-overwrite for this local test adapter
        os.unlink(source)


class RemoteSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file=Path(self.tmp.name)/'app.conf'
        self.sftp=DiskSFTP()
        self.client=MagicMock();self.client.open_sftp.return_value=self.sftp
        self.stop=threading.Event()

    def test_create_then_update_preserves_mode_and_returns_next_version(self):
        result=save_text(self.client,str(self.file),'hello\n',None,self.stop)
        self.assertEqual(self.file.read_bytes(),b'hello\n')
        self.assertEqual(self.file.stat().st_mode & 0o777,0o600)
        os.chmod(self.file,0o640)
        result=read_editable(self.client,str(self.file))
        result=save_text(self.client,str(self.file),'updated\n',result['version'],self.stop)
        self.assertEqual(self.file.read_bytes(),b'updated\n')
        self.assertEqual(self.file.stat().st_mode & 0o777,0o640)
        self.assertEqual(result['version'],read_editable(self.client,str(self.file))['version'])
        self.assertEqual(list(Path(self.tmp.name).glob('.ssh-workbench-*')),[])

    def test_create_never_overwrites_existing_file(self):
        self.file.write_text('existing')
        with self.assertRaises(FileExistsError):save_text(self.client,str(self.file),'new',None,self.stop)
        self.assertEqual(self.file.read_text(),'existing')

    def test_conflicting_edit_retains_server_file(self):
        self.file.write_text('old')
        snapshot=read_editable(self.client,str(self.file))
        self.file.write_text('changed elsewhere')
        with self.assertRaisesRegex(ValueError,'changed on the server'):
            save_text(self.client,str(self.file),'my edit',snapshot['version'],self.stop)
        self.assertEqual(self.file.read_text(),'changed elsewhere')

    def test_atomic_rename_failure_retains_original(self):
        self.file.write_text('old')
        snapshot=read_editable(self.client,str(self.file))
        with patch.object(self.sftp,'posix_rename',side_effect=OSError('unsupported')):
            with self.assertRaises(OSError):save_text(self.client,str(self.file),'new',snapshot['version'],self.stop)
        self.assertEqual(self.file.read_text(),'old')
        self.assertEqual(len(list(Path(self.tmp.name).iterdir())),1)

    def test_cancelled_save_retains_original(self):
        self.file.write_text('old');snapshot=read_editable(self.client,str(self.file))
        self.stop.set()
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            save_text(self.client,str(self.file),'new',snapshot['version'],self.stop)
        self.assertEqual(self.file.read_text(),'old')

    def test_reject_binary_non_utf8_and_oversized_input(self):
        for data in (b'hello\x00world',b'\xff\xfe',b'x'*(EDIT_LIMIT+1)):
            self.file.write_bytes(data)
            with self.assertRaises((ValueError,UnicodeError)):read_editable(self.client,str(self.file))
        with self.assertRaises(ValueError):save_text(self.client,str(self.file),'x'*(EDIT_LIMIT+1),None,self.stop)

    def test_reject_symlink_without_replacing_target(self):
        target=Path(self.tmp.name)/'target';target.write_text('original')
        self.file.symlink_to(target)
        with self.assertRaises(ValueError):read_editable(self.client,str(self.file))
        with self.assertRaises(OSError):save_text(self.client,str(self.file),'new',None,self.stop)
        self.assertTrue(self.file.is_symlink())
        self.assertEqual(target.read_text(),'original')

    def test_crlf_is_preserved_on_save(self):
        self.file.write_bytes(b'first\r\nsecond\r\n')
        snapshot=read_editable(self.client,str(self.file))
        save_text(self.client,str(self.file),'first\nupdated\n',snapshot['version'],self.stop,snapshot['newline'])
        self.assertEqual(self.file.read_bytes(),b'first\r\nupdated\r\n')

    def entry(self):
        attrs=self.sftp.lstat(str(self.file))
        return dict(path=str(self.file),kind='Link' if stat.S_ISLNK(attrs.st_mode) else 'File',
                    size=attrs.st_size,mtime=attrs.st_mtime,permissions=stat.filemode(attrs.st_mode))

    def test_delete_removes_only_selected_regular_file(self):
        self.file.write_text('delete me')
        sibling=Path(self.tmp.name)/'keep.conf';sibling.write_text('keep')
        delete_file(self.client,self.entry(),self.stop)
        self.assertFalse(self.file.exists())
        self.assertEqual(sibling.read_text(),'keep')

    def test_delete_link_keeps_its_target(self):
        target=Path(self.tmp.name)/'target';target.write_text('keep')
        self.file.symlink_to(target)
        delete_file(self.client,self.entry(),self.stop)
        self.assertFalse(self.file.is_symlink())
        self.assertEqual(target.read_text(),'keep')

    def test_delete_rejects_directories(self):
        self.file.mkdir()
        with self.assertRaisesRegex(ValueError,'not folders'):
            delete_file(self.client,self.entry(),self.stop)
        self.assertTrue(self.file.is_dir())

    def test_delete_refuses_changed_entry_and_cancellation(self):
        self.file.write_text('original');entry=self.entry()
        self.file.write_text('changed since selection')
        with self.assertRaisesRegex(ValueError,'changed'):
            delete_file(self.client,entry,self.stop)
        self.stop.set()
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            delete_file(self.client,self.entry(),self.stop)
        self.assertEqual(self.file.read_text(),'changed since selection')

    def test_delete_permission_failure_preserves_file(self):
        self.file.write_text('keep')
        with patch.object(self.sftp,'remove',side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):delete_file(self.client,self.entry(),self.stop)
        self.assertEqual(self.file.read_text(),'keep')


class FileManagerGUITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        p=validate(dict(name='Example',host='example.test',username='user',port=22,auth='agent',start_dir='/'))
        self.page=SessionPage(p,Store(self.tmp.name))
        self.page.show();APP.processEvents()

    def tearDown(self):
        self.page.close();self.page.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()
        self.tmp.cleanup()

    def rows(self):
        return [dict(name=n,path='/var/log/'+n,kind='File',size=size,mtime=mtime,permissions='-rw-r-----')
                for n,size,mtime in [('z.log',2,300),('a.log',100,100),('m.log',20,200)]]

    def test_sort_selection_uses_correct_remote_path_and_numeric_size(self):
        self.page.show_directory(dict(path='/var/log',entries=self.rows(),truncated=False))
        for col,order,expected in [(0,Qt.SortOrder.AscendingOrder,'a.log'),
                                  (2,Qt.SortOrder.DescendingOrder,'z.log'),
                                  (1,Qt.SortOrder.AscendingOrder,'z.log')]:
            self.page.table.sortItems(col,order)
            self.page.table.selectRow(0)
            self.assertEqual(self.page.selection()['path'],'/var/log/'+expected)
        self.page.table.sortItems(2,Qt.SortOrder.DescendingOrder)
        self.page.show_directory(dict(path='/var/log',entries=self.rows(),truncated=False))
        self.page.table.selectRow(0)
        self.assertEqual(self.page.selection()['name'],'z.log')

    def test_breadcrumb_routes_to_ancestor_without_typing(self):
        self.page.show_directory(dict(path='/bppm/opt/logs',entries=[],truncated=False))
        self.page.browse=MagicMock()
        self.page.file_controls(True)
        buttons=self.page.breadcrumbs.findChildren(QPushButton)
        next(b for b in buttons if b.text()=='bppm').click()
        self.page.browse.assert_called_once_with('/bppm')
        self.page.root_button.clicked.emit()
        self.page.browse.assert_called_with('/')
        self.assertEqual([b.text() for b in buttons],['Root /','bppm','opt','logs'])

    def test_go_enters_selected_folder_even_after_sorting(self):
        rows=self.rows()
        for row in rows:row['kind']='Directory'
        self.page.show_directory(dict(path='/var/log',entries=rows,truncated=False))
        self.page.file_controls(True)
        self.page.table.sortItems(2,Qt.SortOrder.DescendingOrder)
        self.page.table.selectRow(0)
        self.page.browse=MagicMock()
        self.page.go.click()
        self.page.browse.assert_called_once_with('/var/log/z.log')

    def test_down_arrow_opens_selected_folder_and_ignores_files(self):
        rows=self.rows();rows[0]['kind']='Directory'
        self.page.show_directory(dict(path='/var/log',entries=rows,truncated=False))
        self.page.file_controls(True)
        self.page.table.sortItems(2,Qt.SortOrder.DescendingOrder)
        self.page.table.selectRow(0)
        self.page.browse=MagicMock()
        self.page.down.click()
        self.page.browse.assert_called_once_with('/var/log/z.log')
        self.page.table.selectRow(1)
        self.page.browse.reset_mock()
        self.page.down.click()
        self.page.browse.assert_not_called()

    def test_history_moves_only_after_success_and_new_branch_discards_forward(self):
        for path in ('/','/var','/var/log'):
            self.page.show_directory(dict(path=path,entries=[],truncated=False))
        self.page.file_controls(True)
        self.page.browse=MagicMock()
        self.page.back_button.click()
        self.page.browse.assert_called_once_with('/var',history_index=1)
        self.assertEqual(self.page.history_index,2)  # A failed request must leave history unchanged.
        self.page.show_directory(dict(path='/var',entries=[],truncated=False),history_index=1)
        self.assertTrue(self.page.forward_button.isEnabled())
        self.page.forward_button.click()
        self.page.browse.assert_called_with('/var/log',history_index=2)
        self.page.show_directory(dict(path='/etc',entries=[],truncated=False))
        self.assertEqual(self.page.history,['/','/var','/etc'])
        self.assertFalse(self.page.forward_button.isEnabled())
        self.page.show_directory(dict(path='/etc',entries=[],truncated=False))
        self.assertEqual(len(self.page.history),3)
        self.page.file_controls(False)
        self.assertFalse(self.page.back_button.isEnabled())

    def test_filter_hides_selection_and_remains_correct_after_sort(self):
        self.page.show_directory(dict(path='/var/log',entries=self.rows(),truncated=False))
        self.page.table.sortItems(2,Qt.SortOrder.DescendingOrder)
        self.page.table.selectRow(0)
        self.page.file_filter.setText('A.LOG')
        self.assertIsNone(self.page.selection())
        for col in (0,1,2):
            self.page.table.sortItems(col,Qt.SortOrder.AscendingOrder)
            APP.processEvents()
            visible=[self.page.table.item(r,0).text() for r in range(self.page.table.rowCount())
                     if not self.page.table.isRowHidden(r)]
            self.assertEqual(visible,['a.log'])
        self.assertIn('1 of 3',self.page.file_status.text())

    def test_copy_path_uses_sorted_selection_or_current_folder(self):
        self.page.show_directory(dict(path='/var/log',entries=self.rows(),truncated=False))
        self.page.table.sortItems(2,Qt.SortOrder.DescendingOrder)
        self.page.table.selectRow(0)
        self.page.copy_remote_path()
        self.assertEqual(APP.clipboard().text(),'/var/log/z.log')
        self.page.table.setCurrentCell(-1,-1)
        self.page.copy_remote_path()
        self.assertEqual(APP.clipboard().text(),'/var/log')

    def test_non_utf8_display_row_cannot_be_used_as_an_action_target(self):
        rows=self.rows();rows[0].update(name='bad\\xa0name [non-UTF-8 name]',path=None,unsupported_name=True)
        self.page.show_directory(dict(path='/var/log',entries=rows,truncated=False))
        self.assertIn('display-only',self.page.file_status.text())
        self.page.table.sortItems(2,Qt.SortOrder.DescendingOrder);self.page.table.selectRow(0)
        self.assertIsNone(self.page.selection())
        self.assertIn('not UTF-8',self.page.file_status.text())

    def test_shortcuts_belong_to_file_panel_and_refresh_uses_current_directory(self):
        self.assertTrue(all(s.context()==Qt.ShortcutContext.WidgetWithChildrenShortcut
                            for s in self.page.file_shortcuts))
        self.page.file_controls(True)
        self.page.show_directory(dict(path='/var/log',entries=[],truncated=False))
        self.page.path.setText('/unsubmitted')
        self.page.browse=MagicMock()
        self.page.refresh_button.click()
        self.page.browse.assert_called_once_with('/var/log')
        self.page.focus_path()
        self.assertEqual(self.page.path.selectedText(),'/unsubmitted')

    def test_go_prefers_edited_path_over_selected_folder(self):
        rows=self.rows();rows[0]['kind']='Directory'
        self.page.show_directory(dict(path='/var/log',entries=rows,truncated=False))
        self.page.file_controls(True)
        self.page.table.sortItems(2,Qt.SortOrder.DescendingOrder)
        self.page.table.selectRow(0)
        self.page.path.setText('/bppm/cell/pw/server')
        self.page.browse=MagicMock()
        self.page.go.click()
        self.page.browse.assert_called_once_with('/bppm/cell/pw/server')

    def test_go_does_not_try_to_browse_a_selected_file(self):
        self.page.show_directory(dict(path='/var/log',entries=self.rows(),truncated=False))
        self.page.file_controls(True)
        self.page.table.selectRow(0)
        self.page.browse=MagicMock()
        self.page.go.click()
        self.page.browse.assert_called_once_with('/var/log')

    def test_up_uses_displayed_directory_when_path_box_has_unsubmitted_text(self):
        self.page.show_directory(dict(path='/bppm/cell/pw/server',entries=[],truncated=False))
        self.page.file_controls(True)
        self.page.path.setText('/different/unsubmitted/path')
        self.page.browse=MagicMock()
        self.page.up.click()
        self.page.browse.assert_called_once_with('/bppm/cell/pw')

    def test_editor_cancel_keeps_dirty_text_and_discard_closes(self):
        dialog=RemoteEditor(self.page,dict(path='/tmp/test',text='old',version=('v',),newline='\n'))
        dialog.show();dialog.text.insertPlainText('new');APP.processEvents()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Cancel):
            dialog.reject()
        self.assertTrue(dialog.isVisible())
        self.assertTrue(dialog.text.document().isModified())
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Discard):dialog.reject()
        self.assertFalse(dialog.isVisible())
        dialog.deleteLater()

    def test_editor_save_failure_retains_modified_text(self):
        dialog=RemoteEditor(self.page,dict(path='/tmp/test',text='old',version=('v',),newline='\n'))
        dialog.text.insertPlainText('new')
        self.page.client=MagicMock()
        self.page.worker=SimpleNamespace(stop_event=threading.Event())
        self.page.task=lambda fn,done,fail:fail('File changed on the server')
        dialog.save()
        self.assertTrue(dialog.text.document().isModified())
        self.assertIn('changed on the server',dialog.status.text())
        self.assertFalse(dialog.saving)
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.text.document().setModified(False);dialog.close();dialog.deleteLater()

    def test_delete_file_and_folder_cancellation_do_not_run_task(self):
        self.page.show_directory(dict(path='/var/log',entries=self.rows(),truncated=False))
        self.page.table.selectRow(0)
        self.page.client=MagicMock();self.page.task=MagicMock()
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Cancel):
            self.page.delete_selected()
        self.page.task.assert_not_called()
        rows=self.rows();rows[0]['kind']='Directory'
        self.page.show_directory(dict(path='/var/log',entries=rows,truncated=False))
        self.page.table.sortItems(2,Qt.SortOrder.DescendingOrder);self.page.table.selectRow(0)
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Cancel) as dialog:
            self.page.delete_selected()
        dialog.assert_called_once();self.page.task.assert_not_called()

    def test_delete_uses_sorted_selection_and_refreshes_after_success(self):
        self.page.show_directory(dict(path='/var/log',entries=self.rows(),truncated=False))
        self.page.table.sortItems(2,Qt.SortOrder.DescendingOrder);self.page.table.selectRow(0)
        self.page.client=MagicMock();self.page.worker=SimpleNamespace(stop_event=threading.Event())
        self.page.task=MagicMock();self.page.browse=MagicMock()
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes):
            self.page.delete_selected()
        fn,done=self.page.task.call_args.args
        with patch('app.delete_file',return_value='Deleted') as remove:
            done(fn(None))
        self.assertEqual(remove.call_args.args[1]['path'],'/var/log/z.log')
        self.page.browse.assert_called_once_with('/var/log')

    def test_folder_delete_is_nonrecursive_unless_explicitly_checked(self):
        rows=self.rows();rows[0]['kind']='Directory'
        self.page.show_directory(dict(path='/var/log',entries=rows,truncated=False))
        self.page.table.sortItems(2,Qt.SortOrder.DescendingOrder);self.page.table.selectRow(0)
        self.page.client=MagicMock();self.page.worker=SimpleNamespace(stop_event=threading.Event())
        self.page.task=MagicMock()
        for include in (False,True):
            def confirm(box):
                self.assertFalse(box.checkBox().isChecked())
                self.assertEqual(box.defaultButton(),box.button(QMessageBox.StandardButton.Cancel))
                box.checkBox().setChecked(include)
                return QMessageBox.StandardButton.Yes
            with patch.object(QMessageBox,'exec',new=confirm):self.page.delete_selected()
            fn,done=self.page.task.call_args.args
            with patch('app.delete_directory',return_value='Deleted') as remove:
                fn(None)
            self.assertEqual(remove.call_args.args[1]['path'],'/var/log/z.log')
            self.assertEqual(remove.call_args.args[3],include)

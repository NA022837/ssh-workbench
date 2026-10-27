import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

from PySide6.QtCore import Qt,QPoint,QEvent
from PySide6.QtWidgets import QApplication,QInputDialog
from backend import create_directory
from app import SessionPage,file_type_label
from profiles import Store,validate
from test_file_manager import DiskSFTP

APP=QApplication.instance() or QApplication([])


class FolderSFTP(DiskSFTP):
    def mkdir(self,path,mode):os.mkdir(path,mode)


class FolderCreationTests(unittest.TestCase):
    def test_create_single_folder_and_refuse_existing_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            sftp=FolderSFTP();client=MagicMock();client.open_sftp.return_value=sftp
            stop=threading.Event()
            result=create_directory(client,tmp,'new folder',stop)
            self.assertTrue(Path(result).is_dir())
            (Path(result)/'keep').write_text('keep')
            with self.assertRaises(FileExistsError):create_directory(client,tmp,'new folder',stop)
            self.assertEqual((Path(result)/'keep').read_text(),'keep')

    def test_invalid_names_and_cancel_do_not_create_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=MagicMock();stop=threading.Event()
            for name in ('','..','.','../escape','nested/child','bad\nname','bad\x00name'):
                with self.assertRaises(ValueError):create_directory(client,tmp,name,stop)
            stop.set()
            with self.assertRaises(RuntimeError):create_directory(client,tmp,'cancelled',stop)
            client.open_sftp.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()),[])


class ContextMenuTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        p=validate(dict(name='Example',host='example.test',username='operator',auth='agent'))
        self.page=SessionPage(p,Store(self.tmp.name))
        self.page.client=MagicMock();self.page.worker=SimpleNamespace(stop_event=threading.Event())
        self.page.file_controls(True)
        self.page.show();APP.processEvents()
        self.rows=[dict(name=name,path='/var/log/'+name,kind=kind,size=1,mtime=stamp,permissions='-rw-r-----')
                   for name,kind,stamp in [('a.log','File',1),('folder','Directory',3),('z.log','File',2)]]
        self.page.show_directory(dict(path='/var/log',entries=self.rows,truncated=False));APP.processEvents()
        self.menus=[]

    def tearDown(self):
        for menu in self.menus:menu.deleteLater()
        self.page.close();self.page.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.tmp.cleanup()

    def menu(self,position):
        menu=self.page.make_file_menu(position);self.menus.append(menu)
        def flatten(current):
            for action in current.actions():
                if action.menu():yield from flatten(action.menu())
                elif not action.isSeparator():yield action.text(),action
        return dict(flatten(menu))

    def point_for(self,name):
        row=next(i for i in range(self.page.table.rowCount()) if self.page.table.item(i,0).text()==name)
        return self.page.table.visualItemRect(self.page.table.item(row,0)).center()

    def test_right_click_after_sort_targets_clicked_file_not_previous_selection(self):
        self.page.table.selectRow(0)
        self.page.table.sortItems(2,Qt.SortOrder.DescendingOrder);APP.processEvents()
        actions=self.menu(self.point_for('z.log'))
        self.assertEqual(self.page.selection()['path'],'/var/log/z.log')
        actions['Copy path'].trigger()
        self.assertEqual(APP.clipboard().text(),'/var/log/z.log')
        self.assertTrue(actions['Delete file…'].isEnabled())
        self.assertTrue(actions['Permissions…'].isEnabled())
        self.assertFalse(self.page.file_actions_bar.isVisible())

    def test_blank_space_clears_selection_and_keeps_creation_actions(self):
        self.page.table.selectRow(0)
        actions=self.menu(QPoint(5,self.page.table.viewport().height()-3))
        self.assertIsNone(self.page.selection())
        self.assertTrue(actions['New folder…'].isEnabled())
        self.assertTrue(actions['New file…'].isEnabled())
        self.assertTrue(actions['Upload file…'].isEnabled())
        self.assertFalse(actions['Delete file…'].isEnabled())
        self.assertFalse(actions['Edit file…'].isEnabled())
        actions['Copy current folder path'].trigger()
        self.assertEqual(APP.clipboard().text(),'/var/log')

    def test_folder_menu_disables_file_actions_but_keeps_permissions_and_open(self):
        actions=self.menu(self.point_for('folder'))
        for label in ('Open folder','Permissions…','New folder…','New file…','Delete folder…'):
            self.assertTrue(actions[label].isEnabled(),label)
        for label in ('View file','Edit file…','Download…'):
            self.assertFalse(actions[label].isEnabled(),label)

    def test_busy_and_unsupported_name_disable_actions(self):
        self.page.file_busy=True
        actions=self.menu(self.point_for('a.log'))
        self.assertFalse(actions['Delete file…'].isEnabled())
        self.assertFalse(actions['New folder…'].isEnabled())
        self.page.file_busy=False
        rows=[dict(self.rows[0],unsupported_name=True,path=None)]
        self.page.show_directory(dict(path='/var/log',entries=rows,truncated=False))
        actions=self.menu(self.point_for('a.log'))
        self.assertFalse(actions['Delete file…'].isEnabled())
        self.assertFalse(actions['View file'].isEnabled())

    def test_new_folder_cancel_does_nothing_and_success_refreshes_parent(self):
        self.page.task=MagicMock();self.page.browse=MagicMock()
        with patch.object(QInputDialog,'getText',return_value=('',False)):self.page.new_folder()
        self.page.task.assert_not_called()
        with patch.object(QInputDialog,'getText',return_value=('Reports',True)):self.page.new_folder()
        fn,done=self.page.task.call_args.args
        with patch('app.create_directory',return_value='/var/log/Reports') as mkdir:
            done(fn(None))
        self.assertEqual(mkdir.call_args.args[1:3],('/var/log','Reports'))
        self.page.browse.assert_called_once_with('/var/log')

    def test_type_labels_cover_extensions_folders_and_links(self):
        self.assertEqual(file_type_label(dict(self.rows[0],name='data.csv')),'CSV file')
        self.assertEqual(file_type_label(dict(self.rows[0],name='rule.baroc')),'BAROC file')
        self.assertEqual(file_type_label(dict(self.rows[0],name='job.sh')),'SH file')
        self.assertEqual(file_type_label(dict(self.rows[0],name='README')),'File')
        self.assertEqual(file_type_label(dict(self.rows[0],kind='Directory')),'Folder')
        self.assertEqual(file_type_label(dict(self.rows[0],kind='Link')),'Symbolic link')

    def test_type_sort_both_directions_retains_action_targets(self):
        rows=[dict(self.rows[0],name='rules.baroc',path='/var/log/rules.baroc'),
              dict(self.rows[0],name='events.csv',path='/var/log/events.csv'),self.rows[1]]
        self.page.show_directory(dict(path='/var/log',entries=rows,truncated=False))
        header=self.page.table.horizontalHeader()
        self.assertEqual([self.page.table.horizontalHeaderItem(header.logicalIndex(i)).text()
                          for i in range(5)],['Name','Type','Size','Modified','Perm.'])
        for order in (Qt.SortOrder.AscendingOrder,Qt.SortOrder.DescendingOrder):
            self.page.table.sortItems(4,order);APP.processEvents()
            types=[self.page.table.item(row,4).text() for row in range(3)]
            self.assertEqual(types,sorted(types,key=str.casefold,reverse=order==Qt.SortOrder.DescendingOrder))
        actions=self.menu(self.point_for('events.csv'))
        actions['Copy path'].trigger()
        self.assertEqual(APP.clipboard().text(),'/var/log/events.csv')
        self.assertTrue(actions['Delete file…'].isEnabled())

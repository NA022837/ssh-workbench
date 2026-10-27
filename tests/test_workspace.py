"""Workspace layout and backward-compatible connection grouping."""
import json
import tempfile
import unittest
from unittest.mock import MagicMock,patch
from PySide6.QtCore import Qt,QEvent
from PySide6.QtWidgets import QApplication,QInputDialog,QToolButton,QTabBar
from app import Window,ProfileDialog
from profiles import Store,validate

APP=QApplication.instance() or QApplication([])


def profile(name='Example',group='Ungrouped'):
    return validate(dict(name=name,group=group,host='example.test',username='operator',port=22,
                         auth='agent',start_dir='/'))


class GroupStoreTests(unittest.TestCase):
    def test_old_profile_loads_as_ungrouped_and_preserves_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);p=profile();p.pop('group')
            store.file.write_text(json.dumps([p]))
            loaded=store.load()[0]
            self.assertEqual(loaded['group'],'Ungrouped')
            self.assertTrue(all(loaded[k]==v for k,v in p.items()))
            loaded['group']='Customer A';loaded['password']='must-not-persist'
            store.save([loaded]);self.assertEqual(store.load()[0]['group'],'Customer A')
            self.assertNotIn('password',store.file.read_text())

    def test_groups_accept_custom_names_and_reject_invalid_values(self):
        self.assertEqual(profile(group=' Shared / Dev ')['group'],'Shared / Dev')
        self.assertEqual(profile(group='ಕಾರ್ಯ')['group'],'ಕಾರ್ಯ')
        for group in ('bad\nname','x'*81,123):
            with self.assertRaises(ValueError):profile(group=group)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.profiles=[profile('API Dev','Dev'),profile('API Cert','Cert'),profile('UI Dev','Dev')]
        self.store.save(self.profiles)
        self.window=Window(self.store);self.window.show();APP.processEvents()

    def tearDown(self):
        self.window.shutting_down=True;self.window.close();self.window.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.tmp.cleanup()

    def child(self,group,index=0):
        tree=self.window.list
        return next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                    if tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole+1)==group).child(index)

    def test_tree_groups_profiles_and_searches_group_names(self):
        self.assertEqual(self.window.list.topLevelItemCount(),2)
        self.assertEqual(self.child('Dev').parent().childCount(),2)
        self.window.search.setText('cErT')
        self.assertEqual(self.window.list.topLevelItemCount(),1)
        self.assertEqual(self.window.list.topLevelItem(0).childCount(),1)
        self.window.search.clear()
        item=self.child('Dev');self.window.list.setCurrentItem(item)
        self.assertEqual(self.window.selected_profile()['group'],'Dev')
        self.window.list.setCurrentItem(item.parent())
        self.assertIsNone(self.window.selected_profile())

    def test_move_multiple_connections_preserves_profile_identity(self):
        self.child('Dev',0).setSelected(True);self.child('Dev',1).setSelected(True)
        before={p['id'] for p in self.window.profiles}
        with patch.object(QInputDialog,'getItem',return_value=('Shared Services',True)):
            self.window.move_to_group()
        loaded=self.store.load()
        self.assertEqual({p['id'] for p in loaded},before)
        self.assertEqual(sum(p['group']=='Shared Services' for p in loaded),2)
        self.assertEqual(sum(p['group']=='Cert' for p in loaded),1)

    def test_profile_dialog_saves_custom_group(self):
        d=ProfileDialog(self.profiles[0],self.window)
        d.group.setCurrentText('Infrastructure');d.save()
        self.assertEqual(d.value['group'],'Infrastructure')
        d.deleteLater()

    def test_terminal_toggle_retains_session_and_files_fill_workspace(self):
        self.window.list.setCurrentItem(self.child('Dev'))
        self.window.open_profile();page=self.window.tabs.currentWidget();APP.processEvents()
        self.assertFalse(page.terminal_panel.isVisible())
        self.assertTrue(page.files_panel.isVisible())
        self.assertGreater(page.files_panel.width(),self.window.tabs.width()*.85)
        client=MagicMock();page.client=client
        page.terminal_toggle.click();APP.processEvents()
        self.assertTrue(page.terminal_panel.isVisible())
        self.assertEqual(page.terminal_toggle.text(),'Hide terminal')
        page.terminal_toggle.click();APP.processEvents()
        self.assertFalse(page.terminal_panel.isVisible())
        self.assertIs(page.client,client)
        client.close.assert_not_called()
        self.assertEqual(page.up.width(),28)
        self.assertEqual(page.table.iconSize().width(),14)

    def test_custom_tab_close_targets_page_after_reordering(self):
        self.window.list.setCurrentItem(self.child('Dev'))
        self.window.open_profile();page=self.window.tabs.currentWidget()
        self.window.tabs.tabBar().moveTab(1,0)
        button=self.window.tabs.tabBar().tabButton(0,QTabBar.ButtonPosition.RightSide)
        self.assertIsInstance(button,QToolButton)
        button.click();APP.processEvents()
        self.assertEqual(self.window.tabs.indexOf(page),-1)
        self.assertEqual(self.window.tabs.count(),1)

    def test_welcome_removes_requested_text(self):
        text=self.window.tabs.widget(0).text()
        self.assertNotIn('No server agent',text)
        self.assertNotIn('CEM changes',text)

    def test_rename_group_updates_all_members_even_when_search_filters_them(self):
        before={p['id'] for p in self.window.profiles}
        self.window.search.setText('API Dev')
        self.window.list.setCurrentItem(self.child('Dev'))
        with patch.object(QInputDialog,'getText',return_value=('Development',True)):
            self.window.rename_group()
        loaded=self.store.load()
        self.assertEqual({p['id'] for p in loaded},before)
        self.assertEqual(sum(p['group']=='Development' for p in loaded),2)
        self.assertEqual(sum(p['group']=='Cert' for p in loaded),1)

    def test_rename_rejects_existing_or_invalid_names_without_changes(self):
        before=self.store.file.read_bytes()
        for name in ('Cert','','bad\nname'):
            with self.assertRaises(ValueError):self.window.commit_group_rename('Dev',name)
        self.assertEqual(self.store.file.read_bytes(),before)
        self.assertEqual(sum(p['group']=='Dev' for p in self.window.profiles),2)

    def test_rename_cancel_and_save_failure_preserve_groups(self):
        self.window.list.setCurrentItem(self.child('Dev'))
        before=self.store.file.read_bytes()
        with patch.object(QInputDialog,'getText',return_value=('Changed',False)):
            self.window.rename_group()
        self.assertEqual(self.store.file.read_bytes(),before)
        with patch.object(self.store,'save',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):self.window.commit_group_rename('Dev','Changed')
        self.assertEqual(sum(p['group']=='Dev' for p in self.window.profiles),2)

    def test_icon_actions_stay_in_one_compact_row(self):
        self.window.list.setCurrentItem(self.child('Dev'))
        self.window.open_profile();page=self.window.tabs.currentWidget();APP.processEvents()
        controls=[page.preview,page.get,page.put,page.new_file_button,page.edit_file_button,page.copy_path_button]
        self.assertEqual(len({button.y() for button in controls}),1)
        self.assertEqual(len({button.parentWidget() for button in controls}),1)
        for button in controls:
            self.assertFalse(button.icon().isNull())
            self.assertTrue(button.toolTip())
            self.assertEqual(button.width(),32)

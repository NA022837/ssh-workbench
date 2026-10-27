"""Hierarchy persistence, reconnect lifecycle, compact layout and bounded log snapshots."""
import json,tempfile,threading,stat,unittest
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
from PySide6.QtCore import Qt,QEvent
from PySide6.QtWidgets import QApplication,QInputDialog,QMessageBox,QToolButton
from app import Window,SessionPage
from profiles import Store,validate,group_path
from log_viewer import tail_snapshot,LogViewer
APP=QApplication.instance() or QApplication([])


def profile(name='Example',group='Dev'):return validate(dict(name=name,group=group,host='example.test',username='operator',auth='agent'))


class HierarchyStore(unittest.TestCase):
    def test_legacy_upgrade_keeps_backup_and_filters_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);p=profile();p['password']='DO-NOT-STORE';store.file.write_text(json.dumps([p]))
            loaded=store.load();store.save(loaded,['Prod','Prod / CEM','Dev'])
            self.assertNotIn('DO-NOT-STORE',store.file.read_text())
            backup=store.file.with_name('profiles-v1-backup.json')
            self.assertNotIn('DO-NOT-STORE',backup.read_text());self.assertEqual(backup.stat().st_mode&0o777,0o600)
            original=backup.read_bytes();store.save(loaded);self.assertEqual(backup.read_bytes(),original)
            self.assertEqual(store.load_groups(),['Prod','Prod / CEM','Dev']);self.assertEqual(store.load()[0]['id'],p['id'])
    def test_nested_limits_and_atomic_failure(self):
        self.assertEqual(group_path(' Prod/ CEM / KC '),'Prod / CEM / KC')
        for value in ('a//b','/a','a/','x'*81,'/'.join(['a']*9),'a\n'):
            with self.assertRaises(ValueError):group_path(value)
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);store.save([profile()],['Dev']);old=store.file.read_bytes()
            with patch('profiles.os.replace',side_effect=OSError('disk failure')),self.assertRaises(OSError):store.save([profile('New')],['Other'])
            self.assertEqual(store.file.read_bytes(),old)


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.rows=[profile('API','Prod / CEM / KC'),profile('Worker','Prod / CEM / LS'),profile('Test','Dev')]
        self.store.save(self.rows,['Prod','Prod / CEM','Prod / CEM / KC','Prod / CEM / LS','Dev','Empty'])
        self.w=Window(self.store)
    def tearDown(self):
        self.w.shutting_down=True;self.w.close();self.w.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.tmp.cleanup()
    def node(self,path):
        def visit(item):
            if item.data(0,Qt.ItemDataRole.UserRole+1)==path:return item
            for i in range(item.childCount()):
                found=visit(item.child(i))
                if found:return found
        for i in range(self.w.list.topLevelItemCount()):
            found=visit(self.w.list.topLevelItem(i))
            if found:return found
    def test_nested_tree_selection_and_empty_groups(self):
        node=self.node('Prod / CEM / KC');self.assertEqual(node.parent().data(0,Qt.ItemDataRole.UserRole+1),'Prod / CEM')
        self.w.list.setCurrentItem(node);self.assertEqual(self.w.selected_group(),'Prod / CEM / KC')
        self.w.list.setCurrentItem(node.child(0));self.assertEqual(self.w.selected_group(),'Prod / CEM / KC')
        self.assertEqual(self.node('Empty').childCount(),0)
    def test_branch_rename_includes_hidden_descendants_preserving_order(self):
        self.w.search.setText('API');self.w.commit_group_rename('Prod / CEM','Platform')
        groups=[p['group'] for p in self.store.load()]
        self.assertIn('Prod / Platform / KC',groups);self.assertIn('Prod / Platform / LS',groups)
        self.assertNotIn('Prod / CEM',self.store.load_groups());self.assertEqual(self.store.load()[0]['id'],self.rows[0]['id'])
    def test_sibling_reorder_persists_and_does_not_move_profile_group(self):
        self.w.list.setCurrentItem(self.node('Prod / CEM / LS'));self.w.reorder_group(-1)
        parent=self.node('Prod / CEM');self.assertEqual(parent.child(0).data(0,Qt.ItemDataRole.UserRole+1),'Prod / CEM / LS')
        self.assertLess(self.store.load_groups().index('Prod / CEM / LS'),self.store.load_groups().index('Prod / CEM / KC'))
        self.w.list.setCurrentItem(self.node('Dev'));self.w.reorder_group(-1)
        self.assertEqual(self.w.list.topLevelItem(0).data(0,Qt.ItemDataRole.UserRole+1),'Dev')
        self.assertEqual([p['group'] for p in self.store.load()],[p['group'] for p in self.rows])
    def test_create_empty_subgroup_and_remove_it_without_removing_connections(self):
        self.w.list.setCurrentItem(self.node('Empty'))
        with patch.object(QInputDialog,'getText',return_value=('Child',True)):self.w.new_group(True)
        self.assertIn('Empty / Child',self.store.load_groups())
        self.w.list.setCurrentItem(self.node('Empty'))
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):self.w.remove_empty_group()
        self.assertNotIn('Empty',self.store.load_groups());self.assertEqual(len(self.store.load()),3)
        self.w.list.setCurrentItem(self.node('Prod'))
        with patch.object(QMessageBox,'information'):self.w.remove_empty_group()
        self.assertIn('Prod',self.store.load_groups())
    def test_aggregate_state_any_connected_session_is_green_and_tab_removed(self):
        self.w.list.setCurrentItem(self.node('Dev').child(0));self.w.open_profile();first=self.w.tabs.currentWidget()
        self.w.open_profile();second=self.w.tabs.currentWidget();first.set_connection_state('connected')
        item=self.node('Dev').child(0);self.assertIn('SSH state: connected',item.toolTip(0))
        first.set_connection_state('disconnected');second.set_connection_state('connected')
        self.assertIn('SSH state: connected',item.toolTip(0))
        self.w.close_tab(self.w.tabs.indexOf(second));self.assertIn('SSH state: disconnected',item.toolTip(0))


class Reconnect(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.page=SessionPage(profile(),Store(self.tmp.name))
    def tearDown(self):
        self.page.reconnect_timer.stop();self.page.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.tmp.cleanup()
    def test_disconnected_reconnect_waits_for_old_thread_and_requests_fresh_connect(self):
        worker=MagicMock();worker.isRunning.return_value=True;self.page.worker=worker
        self.page.reconnect_session();self.assertTrue(self.page.reconnect_pending);worker.stop.assert_called_once()
        with patch.object(self.page,'connect_session') as connect:
            self.page.finish_reconnect();connect.assert_not_called();worker.isRunning.return_value=False
            self.page.finish_reconnect();connect.assert_called_once()
        self.assertFalse(self.page.reconnect_pending);self.assertEqual(self.page.pending_secret,None)
    def test_connected_reconnect_cancel_and_busy_guard(self):
        self.page.client=MagicMock()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Cancel):self.page.reconnect_session()
        self.assertFalse(self.page.reconnect_pending)
        task=MagicMock();task.isRunning.return_value=True;self.page.tasks=[task];self.page.reconnect_session()
        self.assertFalse(self.page.reconnect_pending);self.assertIn('Wait',self.page.status.text());self.page.tasks=[]
    def test_disconnect_cancels_pending_reconnect_and_close_never_reconnects(self):
        self.page.reconnect_pending=True;self.page.reconnect_timer.start();self.page.disconnect_session()
        self.assertFalse(self.page.reconnect_pending);self.assertFalse(self.page.reconnect_timer.isActive())
        self.page.closing=True
        with patch.object(self.page,'connect_session') as connect:self.page.finish_reconnect();connect.assert_not_called()
    def test_success_restores_last_folder_and_state_accessibility(self):
        worker=MagicMock();self.page.worker=worker;self.page.current_dir='/var/log/app'
        with patch.object(self.page,'browse') as browse,patch.object(self.page.server_clock,'sync'):self.page.on_ready(worker,MagicMock())
        browse.assert_called_once_with('/var/log/app');self.assertEqual(self.page.connection_state,'connected')
        self.assertEqual(self.page.connection_light.accessibleName(),'SSH connected')
        self.page.on_ended(worker);self.assertEqual(self.page.connection_state,'disconnected')
    def test_compact_controls_and_path_share_navigation_row(self):
        self.assertIsInstance(self.page.connect_button,QToolButton);self.assertEqual(self.page.connect_button.width(),27)
        self.assertEqual(self.page.path.height(),26);self.assertEqual(self.page.file_filter.height(),25)
        self.assertEqual(self.page.environment.text(),'Dev')


class Logs(unittest.TestCase):
    def test_regular_file_tail_is_quoted_bounded_and_limited_to_200_lines(self):
        client=MagicMock();sftp=client.open_sftp.return_value;sftp.__enter__.return_value=sftp
        sftp.lstat.return_value=SimpleNamespace(st_mode=stat.S_IFREG|0o640)
        path="/var/log/a '$(bad).log"
        with patch('log_viewer.execute',return_value='\n'.join(str(i) for i in range(300))) as execute:
            result=tail_snapshot(client,path,threading.Event())
        self.assertTrue(result['limited']);self.assertEqual(len(result['text'].splitlines()),200)
        import shlex
        self.assertEqual(shlex.split(execute.call_args.args[1])[-1],path)
        self.assertIn('tail -c 262144 -- ',execute.call_args.args[1])
    def test_symlink_devices_binary_and_cancel_refused(self):
        client=MagicMock();sftp=client.open_sftp.return_value;sftp.__enter__.return_value=sftp
        for mode in (stat.S_IFLNK,stat.S_IFCHR):
            sftp.lstat.return_value=SimpleNamespace(st_mode=mode)
            with self.assertRaises(ValueError):tail_snapshot(client,'/file',threading.Event())
        sftp.lstat.return_value=SimpleNamespace(st_mode=stat.S_IFREG)
        with patch('log_viewer.execute',return_value='binary\x00'),self.assertRaises(ValueError):tail_snapshot(client,'/file',threading.Event())
        stop=threading.Event();stop.set()
        with self.assertRaises(RuntimeError):tail_snapshot(client,'/file',stop)
    def test_follow_default_off_filter_and_errors_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            page=SessionPage(profile(),Store(tmp))
            with patch.object(LogViewer,'load'):
                dialog=LogViewer(page,dict(path='/app.log',name='app.log'));APP.processEvents()
                self.assertFalse(dialog.follow.isChecked());self.assertFalse(dialog.timer.isActive())
                dialog.show_snapshot(dict(text='INFO done\nERROR failed',limited=False));dialog.query.setText('error')
                self.assertEqual(dialog.body.toPlainText(),'ERROR failed')
                dialog.follow.setChecked(True);self.assertTrue(dialog.timer.isActive());dialog.on_error('permission denied')
                self.assertFalse(dialog.follow.isChecked());self.assertFalse(dialog.timer.isActive());self.assertIn('stale',dialog.status.text())
                dialog.deleteLater();page.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()

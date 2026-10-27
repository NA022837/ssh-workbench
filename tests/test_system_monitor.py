"""Linux metrics, bounded SSH execution, sorting and confirmed systemd actions."""
import os,subprocess,tempfile,threading,unittest
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
from PySide6.QtCore import Qt,QEvent
from PySide6.QtWidgets import QApplication,QMessageBox
import system_backend as b
from app import SessionPage
from profiles import Store,validate
APP=QApplication.instance() or QApplication([])
MEM='MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\nBuffers: 50 kB\nCached: 200 kB\nSReclaimable: 75 kB\nShmem: 25 kB\nSwapTotal: 200 kB\nSwapFree: 150 kB\n'
SERVICES='cem-example.service loaded active running Example application\ncups.service loaded failed failed Print service\n'
INSTALLED='cem-example.service enabled enabled\ncups.service disabled enabled\nstopped.service disabled disabled\nworker@.service static -\n'


class Snapshots(unittest.TestCase):
    def test_units_and_available_memory_not_raw_free(self):
        self.assertEqual([b.format_bytes(n) for n in (0,1,1024,1048576,1073741824)],['0 B','1 B','1.0 KB','1.0 MB','1.0 GB'])
        m=b.parse_meminfo(MEM);self.assertEqual(m['used'],600*1024);self.assertEqual(m['free'],100*1024)
        self.assertEqual(m['percent'],60);self.assertEqual(m['swap_used'],50*1024);self.assertFalse(m['estimated'])
        m=b.parse_meminfo(MEM.replace('MemAvailable: 400 kB\n',''));self.assertTrue(m['estimated']);self.assertEqual(m['available'],400*1024)
        with self.assertRaises(ValueError):b.parse_meminfo('bad')
    def test_cpu_guest_not_double_counted_resets_and_disks(self):
        self.assertEqual(b.cpu_ticks('cpu 100 0 100 700 100 0 0 0 50 0'),(1000,800))
        self.assertEqual(b.cpu_percent((100,80),(200,130)),50);self.assertIsNone(b.cpu_percent((100,80),(99,80)))
        with self.assertRaises(ValueError):b.cpu_ticks('bad')
        r=b.parse_disks('Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda 100 60 35 64% /path with spaces\nbad\n')[0]
        self.assertEqual(r['mount'],'/path with spaces');self.assertEqual(r['free'],35*1024);self.assertEqual(r['percent'],64)
    def test_processes_numeric_fields_names_cap_and_nonfinite(self):
        text='123 root 110.5 4.2 2048 60 Sl java\n456 operator 0 1 1024 3600 S worker process\nbad\n'
        with patch.object(b,'execute',return_value=text):d=b.process_snapshot(None,threading.Event())
        self.assertEqual(d['rows'][0]['cpu'],110.5);self.assertEqual(d['rows'][0]['rss'],2097152)
        self.assertEqual(d['rows'][1]['name'],'worker process');self.assertEqual(d['skipped'],1)
        with patch.object(b,'execute',return_value='1 root 1 1 1 1 S app\n'*3002+'2 root nan 0 0 0 S invalid\n'):d=b.process_snapshot(None,threading.Event())
        self.assertEqual(len(d['rows']),3000);self.assertTrue(d['truncated']);self.assertEqual(d['skipped'],1)
        with patch.object(b,'execute',return_value='bad'),self.assertRaises(ValueError):b.process_snapshot(None,threading.Event())
    def test_services_loaded_installed_and_unknown_states(self):
        with patch.object(b,'execute',side_effect=[SERVICES,INSTALLED]):d=b.service_snapshot(None,threading.Event())
        rows={r['unit']:r for r in d['rows']};self.assertEqual(rows['cem-example.service']['enabled'],'enabled')
        self.assertEqual(rows['stopped.service']['active'],'unknown');self.assertEqual(d['rows'][0]['unit'],'cups.service')
        with patch.object(b,'execute',side_effect=[SERVICES,RuntimeError('not permitted')]):d=b.service_snapshot(None,threading.Event())
        self.assertIn('not permitted',d['warnings'][0]);self.assertEqual(len(d['rows']),2)
    def test_service_validation_quoting_and_sudo_explicit(self):
        for unit in ('sshd.service','app@worker.service',r'app\x2dapi.service'):self.assertTrue(b.valid_unit(unit))
        for unit in ('*','app@.service','app.service;id','app.service\nbad','../../app.service','app.socket',''):
            self.assertFalse(b.valid_unit(unit))
            with self.assertRaises(ValueError):b.service_action(None,unit,'start',threading.Event())
        for action in ('start','stop','restart'):
            with patch.object(b,'execute',return_value='') as run:result=b.service_action(None,'app.service',action,threading.Event())
            self.assertIn('--no-block '+action+' -- app.service',run.call_args.args[1]);self.assertFalse(run.call_args.args[1].startswith('sudo'))
            self.assertIn('request accepted',result)
        with patch.object(b,'execute',return_value='') as run:b.service_action(None,'app.service','restart',threading.Event(),True)
        self.assertTrue(run.call_args.args[1].startswith('sudo -n -- '))
        with self.assertRaises(ValueError):b.service_action(None,'app.service','disable',threading.Event())
        with patch.object(b,'execute',side_effect=RuntimeError('Permission denied')),self.assertRaisesRegex(RuntimeError,'Permission denied'):b.service_action(None,'app.service','stop',threading.Event())
    def test_full_metrics_framing_and_partial_disk_failure(self):
        metrics='__CPU__\ncpu 10 0 10 80 0 0 0 0\ncpu0 1 0 1 8\ncpu1 1 0 1 8\n__MEM__\n'+MEM+'__UP__\n86401 10\n__LOAD__\n0.10 0.20 0.30 1/100 1\n__HOST__\nexample.test\n__KERNEL__\nLinux 5.14\n__END__\n'
        after='cpu 20 0 20 160 0 0 0 0\n'
        with patch.object(b,'execute',side_effect=[metrics,after,RuntimeError('disk access denied')]):d=b.system_snapshot(None,threading.Event())
        self.assertEqual(d['cores'],2);self.assertEqual(d['hostname'],'example.test');self.assertEqual(d['cpu'],20)
        self.assertEqual(d['load'],['0.10','0.20','0.30']);self.assertIn('disk access denied',d['warnings'][0]);self.assertEqual(d['disks'],[])
        with patch.object(b,'execute',return_value='incomplete'),self.assertRaisesRegex(ValueError,'Incomplete'):b.system_snapshot(None,threading.Event())

    @unittest.skipUnless(os.path.exists('/proc/meminfo'),'Requires local Linux procfs')
    def test_actual_linux_read_only_metrics_and_process_parsing(self):
        def local(client,command,stop,**kwargs):return subprocess.run(command,shell=True,executable='/bin/sh',env=dict(os.environ,LC_ALL='C'),capture_output=True,text=True,timeout=20,check=True).stdout
        with patch.object(b,'execute',side_effect=local):d=b.system_snapshot(None,threading.Event());p=b.process_snapshot(None,threading.Event())
        self.assertGreater(d['memory']['total'],0);self.assertGreater(d['cores'],0);self.assertTrue(0<=d['cpu']<=100)
        self.assertGreater(len(p['rows']),0);self.assertTrue(d['disks'])


class Channel:
    def __init__(self,stdout=b'',stderr=b'',code=0):self.out=stdout;self.err=stderr;self.code=code;self.closed=False
    def settimeout(self,t):pass
    def exec_command(self,cmd):self.command=cmd
    def recv_ready(self):return bool(self.out)
    def recv_stderr_ready(self):return bool(self.err)
    def recv(self,n):data=self.out[:n];self.out=self.out[n:];return data
    def recv_stderr(self,n):data=self.err[:n];self.err=self.err[n:];return data
    def exit_status_ready(self):return True
    def recv_exit_status(self):return self.code
    def close(self):self.closed=True


class Execution(unittest.TestCase):
    def client(self,ch):client=MagicMock();client.get_transport.return_value.open_session.return_value=ch;return client
    def test_drains_both_streams_and_closes(self):
        ch=Channel(b'ok',b'warning');self.assertEqual(b.execute(self.client(ch),'uname -n',threading.Event()),'ok');self.assertTrue(ch.closed)
    def test_failure_limit_cancel_and_timeout(self):
        ch=Channel(b'',b'Permission denied',1)
        with self.assertRaisesRegex(RuntimeError,'Permission denied'):b.execute(self.client(ch),'fixed',threading.Event())
        self.assertTrue(ch.closed);ch=Channel(b'x'*100)
        with self.assertRaisesRegex(RuntimeError,'limit'):b.execute(self.client(ch),'fixed',threading.Event(),limit=50)
        self.assertTrue(ch.closed);stop=threading.Event();stop.set();client=MagicMock()
        with self.assertRaises(RuntimeError):b.execute(client,'fixed',stop)
        client.get_transport.assert_not_called();ch=Channel()
        with self.assertRaisesRegex(TimeoutError,'may still complete'):b.execute(self.client(ch),'fixed',threading.Event(),timeout=-1)
        self.assertTrue(ch.closed)


class MonitorUI(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();p=validate(dict(name='Example',host='example.test',username='operator',auth='agent'))
        self.page=SessionPage(p,Store(self.tmp.name));self.panel=self.page.monitor;self.page.client=MagicMock()
        self.page.worker=SimpleNamespace(stop_event=threading.Event());self.page.file_controls(True)
    def tearDown(self):
        self.page.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.tmp.cleanup()
    def service_rows(self):
        with patch.object(b,'execute',side_effect=[SERVICES,INSTALLED]):return b.service_snapshot(None,threading.Event())
    def select(self,unit):
        row=next(i for i in range(self.panel.services.rowCount()) if self.panel.services.item(i,0).text()==unit)
        self.panel.services.selectRow(row);self.panel.services.setCurrentCell(row,0);self.panel.update_actions()
    def test_no_requests_at_creation_and_readable_size_sorts_numerically(self):
        self.assertIsNone(self.panel.job);self.assertEqual(self.panel.interval.currentData(),0);self.assertFalse(self.panel.timer.isActive())
        rows=[dict(name=n,path='/tmp/'+n,kind='File',size=size,mtime=1,permissions='-rw-------') for n,size in [('large',1048576),('small',1024)]]
        self.page.show_directory(dict(path='/tmp',entries=rows,truncated=False));self.page.table.sortItems(1,Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.page.table.item(0,1).text(),'1.0 KB');self.assertEqual(self.page.table.item(1,1).text(),'1.0 MB')
        self.assertIn('1,024 bytes',self.page.table.item(0,1).toolTip());self.page.table.selectRow(0);self.assertEqual(self.page.selection()['name'],'small')
    def test_toggle_and_hidden_or_busy_timer_do_not_query(self):
        with patch.object(self.panel,'reload') as reload:
            self.page.monitor_toggle.click();reload.assert_called_once();self.assertEqual(self.page.workspace.currentIndex(),1)
            self.page.monitor_toggle.click();self.assertEqual(self.page.workspace.currentIndex(),0);reload.reset_mock()
            self.panel.auto_reload();reload.assert_not_called();self.panel.active=True;self.panel.job=MagicMock()
            with patch.object(self.panel,'viewing',return_value=True):self.panel.auto_reload()
            reload.assert_not_called();self.panel.job=None
    def test_process_sort_and_filter(self):
        rows=[dict(pid=10,name='java',user='app',cpu=110,memory=3,rss=1048576,elapsed=30,state='Sl'),dict(pid=2,name='sshd',user='root',cpu=1,memory=.2,rss=1024,elapsed=3600,state='S')]
        self.panel.show_snapshot(1,dict(rows=rows,total=2,skipped=0,truncated=False));self.assertEqual(self.panel.processes.item(0,1).text(),'java')
        self.panel.processes.sortItems(0,Qt.SortOrder.AscendingOrder);self.assertEqual(self.panel.processes.item(0,0).text(),'2')
        self.panel.process_filter.setText('root');self.assertTrue(self.panel.processes.isRowHidden(1))
    def test_service_sorted_selection_cancel_template_stale_guards(self):
        self.panel.show_snapshot(2,self.service_rows());self.panel.services.sortItems(0,Qt.SortOrder.DescendingOrder);self.select('cem-example.service')
        self.assertFalse(self.panel.action_buttons['start'].isEnabled());self.assertTrue(self.panel.action_buttons['restart'].isEnabled())
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Cancel),patch.object(self.panel,'launch') as launch:self.panel.change_service('restart');launch.assert_not_called()
        self.select('worker@.service');self.assertFalse(self.panel.action_buttons['restart'].isEnabled())
        self.select('cups.service');self.panel.on_error('failed');self.panel.update_actions();self.assertFalse(self.panel.action_buttons['restart'].isEnabled())
    def test_confirmed_action_targets_sorted_unit_and_selected_sudo(self):
        self.panel.show_snapshot(2,self.service_rows());self.select('cups.service');self.panel.sudo.setChecked(True)
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes),patch.object(self.panel,'launch') as launch:self.panel.change_service('restart')
        fn,done,message=launch.call_args.args
        with patch('system_panel.service_action',return_value='Restart request accepted') as action:done(fn(None))
        self.assertEqual(action.call_args.args[1:3],('cups.service','restart'));self.assertTrue(action.call_args.args[4]);self.assertIn('accepted',self.panel.status.text());self.assertFalse(self.panel.services_loaded)
    def test_real_worker_refresh_finishes_and_is_removed_from_session_tasks(self):
        import time
        from PySide6.QtTest import QTest
        self.panel.tabs.setCurrentIndex(1)
        data=dict(rows=[dict(pid=1,name='systemd',user='root',cpu=0,memory=.1,rss=1024,elapsed=10,state='Ss')],total=1,skipped=0,truncated=False)
        with patch('system_panel.process_snapshot',return_value=data):
            self.panel.reload()
            deadline=time.monotonic()+3
            while self.panel.job is not None and time.monotonic()<deadline:APP.processEvents();QTest.qWait(5)
        self.assertIsNone(self.panel.job);self.assertEqual(self.page.tasks,[])
        self.assertEqual(self.panel.processes.item(0,1).text(),'systemd')
        self.assertIn('Last successful',self.panel.status.text())

    def test_filter_clears_hidden_selection_and_disconnect_disables_controls(self):
        self.panel.show_snapshot(2,self.service_rows());self.select('cups.service');self.panel.service_filter.setText('cem-example')
        self.assertIsNone(self.panel.selected_service());self.assertFalse(self.panel.action_buttons['restart'].isEnabled())
        self.panel.service_filter.clear();self.select('cups.service');self.panel.disconnected()
        self.assertFalse(self.panel.action_buttons['restart'].isEnabled());self.assertIn('stale',self.panel.status.text())

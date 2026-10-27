"""Rename and interactive SCP regressions; no network or corporate servers."""
import os,shlex,stat,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication,QInputDialog,QLineEdit,QToolButton
from app import SessionPage,Window
from backend import rename_entry
from scp_transfer import transfer_command,prompt_kind,SCPWorker,TransferDialog
from profiles import Store,validate
from test_file_manager import DiskSFTP
APP=QApplication.instance() or QApplication([])


def snapshot(path):
    a=os.lstat(path)
    return dict(path=str(path),name=Path(path).name,kind='Directory' if stat.S_ISDIR(a.st_mode) else 'File',size=a.st_size,mtime=int(a.st_mtime),permissions=stat.filemode(a.st_mode))


class RenameSFTP(DiskSFTP):
    def rename(self,source,dest):
        if os.path.lexists(dest):raise FileExistsError(dest)
        os.rename(source,dest)


class RenameTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'before.txt';self.path.write_text('retain contents')
        self.client=MagicMock();self.client.open_sftp.return_value=RenameSFTP();self.stop=threading.Event()
    def test_file_and_folder_rename_preserve_contents(self):
        target=rename_entry(self.client,snapshot(self.path),'after.txt',self.stop)
        self.assertEqual(Path(target).read_text(),'retain contents');self.assertFalse(self.path.exists())
        folder=self.root/'old';folder.mkdir();(folder/'child').write_text('child')
        target=rename_entry(self.client,snapshot(folder),'new',self.stop)
        self.assertEqual((Path(target)/'child').read_text(),'child')
    def test_existing_destination_and_changed_source_refused(self):
        other=self.root/'existing';other.write_text('keep')
        with self.assertRaises(FileExistsError):rename_entry(self.client,snapshot(self.path),'existing',self.stop)
        self.assertEqual(other.read_text(),'keep')
        entry=snapshot(self.path);self.path.write_text('different length')
        with self.assertRaisesRegex(ValueError,'changed'):rename_entry(self.client,entry,'after',self.stop)
        self.assertFalse((self.root/'after').exists())
    def test_invalid_names_cancel_missing_source(self):
        entry=snapshot(self.path)
        for name in ('','..','.','../escape','a/b','bad\nname','bad\x7fname'):
            with self.assertRaises(ValueError):rename_entry(self.client,entry,name,self.stop)
        self.stop.set()
        with self.assertRaises(RuntimeError):rename_entry(self.client,entry,'after',self.stop)
        self.stop.clear();self.path.unlink()
        with self.assertRaises(FileNotFoundError):rename_entry(self.client,entry,'after',self.stop)


class Channel:
    def __init__(self,frames,answers=0,code=0):
        self.frames=list(frames);self.answers=answers;self.sent=[];self.closed=False;self.code=code
    def settimeout(self,value):pass
    def get_pty(self,**kwargs):self.pty=kwargs
    def exec_command(self,command):self.command=command
    def recv_ready(self):return bool(self.frames)
    def recv(self,n):return self.frames.pop(0)
    def sendall(self,data):self.sent.append(data)
    def exit_status_ready(self):return not self.frames and len(self.sent)>=self.answers
    def recv_exit_status(self):return self.code
    def close(self):self.closed=True


class SCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.file=Path(self.tmp.name)/"a '$(touch nope).txt";self.file.write_text('source')
        self.entry=snapshot(self.file);self.client=MagicMock();self.client.open_sftp.return_value=DiskSFTP();self.stop=threading.Event()
    def command(self,**kw):
        values=dict(entry=self.entry,host='target.example.test',username='operator',port=2222,destination='/var/tmp/nithin.txt');values.update(kw)
        return transfer_command(**values)
    def worker(self,channel):
        self.client.get_transport.return_value.open_session.return_value=channel
        w=SCPWorker(self.client,self.entry,self.command(),self.stop);self.addCleanup(w.deleteLater);return w
    def test_quoting_host_checks_password_auth_and_recursion(self):
        cmd=self.command();args=shlex.split(cmd.split('LC_ALL=C exec ',1)[1])
        self.assertEqual(args[-2],str(self.file));self.assertEqual(args[-1],'operator@target.example.test:/var/tmp/nithin.txt')
        self.assertTrue(cmd.startswith('stty -echo && '));self.assertIn('StrictHostKeyChecking=ask',args)
        self.assertIn('PreferredAuthentications=password',args);self.assertIn('ControlPath=none',args)
        self.assertEqual(args[1:3],['-P','2222']);self.assertNotIn('-r',args)
        self.assertIn('-r',shlex.split(self.command(entry=dict(self.entry,kind='Directory',permissions='drwxr-xr-x'))))
    @unittest.skipUnless(os.name=='posix','PTY echo verification requires POSIX')
    def test_real_pty_disables_echo_before_password_entry(self):
        import pty,select,subprocess,termios,time
        bindir=Path(self.tmp.name)/'bin';bindir.mkdir()
        executable=bindir/'scp'
        executable.write_text("#!/bin/sh\nprintf 'Password: '\nread password\nprintf '\\nCompleted\\n'\n")
        executable.chmod(0o700)
        master,slave=pty.openpty()
        env=dict(os.environ,PATH=str(bindir)+os.pathsep+os.environ.get('PATH',''))
        process=subprocess.Popen(['/bin/sh','-c',self.command()],stdin=slave,stdout=slave,stderr=slave,env=env)
        output=b''
        try:
            deadline=time.monotonic()+5
            while b'Password: ' not in output and time.monotonic()<deadline:
                if select.select([master],[],[],.1)[0]:output+=os.read(master,4096)
            self.assertIn(b'Password: ',output)
            self.assertFalse(termios.tcgetattr(slave)[3]&termios.ECHO)
            os.write(master,b'not-in-output\n')
            process.wait(timeout=5)
            while select.select([master],[],[],.1)[0]:output+=os.read(master,4096)
            self.assertIn(b'Completed',output);self.assertNotIn(b'not-in-output',output)
            self.assertEqual(process.returncode,0)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            os.close(master);os.close(slave)

    def test_invalid_destination_and_injection_refused(self):
        for kw in ({'host':'x;touch bad'},{'host':'-oProxyCommand=bad'},{'username':'a@b'},{'username':'root;bad'},
                   {'destination':'/tmp/$(touch_bad)'},{'destination':'/tmp/a b'},{'destination':'relative'},{'destination':'/tmp/../etc'},
                   {'port':0},{'port':65536},{'entry':dict(self.entry,path='/')},{'entry':dict(self.entry,kind='Link')},
                   {'entry':dict(self.entry,permissions='prw-r--r--')}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):self.command(**kw)
    def test_prompt_parser(self):
        self.assertEqual(prompt_kind('Are you sure you want to continue connecting (yes/no/[fingerprint])? '),'host')
        self.assertEqual(prompt_kind("operator@target's password: "),'password')
        self.assertIsNone(prompt_kind('100% 1024KB/s'))
    def test_password_only_in_channel_input_not_command_or_log(self):
        c=Channel([b"operator@target's pass",b'word: '],1);w=self.worker(c);logs=[];results=[]
        w.output.connect(logs.append);w.completed.connect(lambda ok,msg:results.append((ok,msg)))
        w.prompt.connect(lambda kind:w.answer(kind,'transient-secret'));w.run()
        self.assertEqual(c.sent,[b'transient-secret\n']);self.assertNotIn('transient-secret',c.command+''.join(logs))
        self.assertTrue(results[0][0]);self.assertTrue(c.closed);self.assertTrue(w.answers.empty())
    def test_host_key_confirmation_and_nonzero_exit(self):
        c=Channel([b'SHA256:example\nAre you sure you want to continue connecting (yes/no)? '],1,1)
        w=self.worker(c);results=[];prompts=[]
        w.prompt.connect(lambda kind:(prompts.append(kind),w.answer(kind,'yes')))
        w.completed.connect(lambda ok,msg:results.append((ok,msg)));w.run()
        self.assertEqual(prompts,['host']);self.assertEqual(c.sent,[b'yes\n']);self.assertFalse(results[0][0]);self.assertIn('partial',results[0][1])
    def test_cancel_and_changed_source_do_not_execute(self):
        for cancel,change in ((True,False),(False,True)):
            c=Channel([]);w=self.worker(c);results=[];w.completed.connect(lambda ok,msg:results.append((ok,msg)))
            if cancel:w.stop()
            if change:self.file.write_text('changed length')
            w.run();self.assertFalse(results[0][0]);self.assertFalse(hasattr(c,'command'))
    def test_disconnect_mid_transfer_reports_partial_and_closes_channel(self):
        c=Channel([b"operator@target's password: "],1);w=self.worker(c);results=[]
        w.prompt.connect(lambda kind:self.stop.set());w.completed.connect(lambda ok,msg:results.append((ok,msg)));w.run()
        self.assertFalse(results[0][0]);self.assertIn('partial',results[0][1]);self.assertTrue(c.closed)
    def test_answers_only_accepted_for_current_prompt(self):
        w=self.worker(Channel([]));self.assertFalse(w.answer('password','secret'));w.waiting='password'
        self.assertFalse(w.answer('password','bad\nvalue'));self.assertFalse(w.answer('host','yes'))
        self.assertTrue(w.answer('password','secret'));self.assertFalse(w.answer('password','duplicate'))


class UITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        p=validate(dict(name='Source',host='source.example.test',username='operator',auth='agent'))
        d=validate(dict(name='Destination',host='target.example.test',username='destuser',port=2222,start_dir='/data/',auth='password'))
        self.store.save([p,d]);self.page=SessionPage(p,self.store);self.page.client=MagicMock();self.page.worker=MagicMock()
        self.page.worker.stop_event=threading.Event();self.page.file_controls(True)
        self.entry=dict(name='nithin.txt',path='/tmp/nithin.txt',kind='File',size=1,mtime=1,permissions='-rw-------')
        self.page.show_directory(dict(path='/tmp',entries=[self.entry],truncated=False));self.page.table.selectRow(0);self.dialogs=[]
    def tearDown(self):
        for d in self.dialogs:d.deleteLater()
        self.page.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.tmp.cleanup()
    def test_file_folder_menu_actions(self):
        for kind,perm in [('File','-rw-------'),('Directory','drwx------')]:
            self.page.show_directory(dict(path='/tmp',entries=[dict(self.entry,kind=kind,permissions=perm)],truncated=False))
            menu=self.page.make_file_menu(self.page.table.visualItemRect(self.page.table.item(0,0)).center())
            def flatten(current):
                for action in current.actions():
                    if action.menu():yield from flatten(action.menu())
                    elif not action.isSeparator():yield action.text(),action
            actions=dict(flatten(menu))
            self.assertTrue(actions['Rename…'].isEnabled());self.assertTrue(actions['Send to server (SCP)…'].isEnabled());menu.deleteLater()
    def test_rename_cancel_and_target_refresh(self):
        self.page.task=MagicMock();self.page.browse=MagicMock()
        with patch.object(QInputDialog,'getText',return_value=('ignored',False)):self.page.rename_selected()
        self.page.task.assert_not_called()
        with patch.object(QInputDialog,'getText',return_value=('new.txt',True)):self.page.rename_selected()
        fn,done=self.page.task.call_args.args
        with patch('app.rename_entry',return_value='/tmp/new.txt') as rename:done(fn(None))
        self.assertEqual(rename.call_args.args[1]['path'],'/tmp/nithin.txt');self.page.browse.assert_called_once_with('/tmp')
    def test_saved_destination_password_masking_and_no_saved_secret(self):
        d=TransferDialog(self.page,self.entry);self.dialogs.append(d);d.profile.setCurrentIndex(2)
        self.assertEqual(d.host.text(),'target.example.test');self.assertEqual(d.port.text(),'2222')
        self.assertEqual(d.password.echoMode(),QLineEdit.EchoMode.Password);self.assertFalse(d.password.isEnabled())
        d.on_prompt('password');d.worker=MagicMock();d.worker.answer.return_value=True;d.password.setText('not-saved');d.submit_password()
        self.assertEqual(d.password.text(),'');self.assertNotIn('not-saved',self.store.file.read_text());d.worker=None
    def test_close_waits_for_worker_cleanup(self):
        d=TransferDialog(self.page,self.entry);self.dialogs.append(d);d.worker=MagicMock();self.page.tasks.append(d.worker);self.page.file_busy=True
        d.reject();d.worker.stop.assert_called_once();self.assertTrue(d.close_pending)
        d.finished_worker();self.assertFalse(self.page.file_busy);self.assertEqual(self.page.tasks,[]);d.worker=None
    def test_sidebar_one_compact_icon_row(self):
        w=Window(self.store);buttons=w.connection_tools.findChildren(QToolButton)
        self.assertEqual(len(buttons),6);self.assertEqual({b.height() for b in buttons},{30})
        self.assertTrue(all(b.toolTip() and b.accessibleName() for b in buttons));self.assertEqual(w.connection_tools.layout().count(),7)
        w.deleteLater()

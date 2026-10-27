import io
import json
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import paramiko
from PySide6.QtCore import Qt, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from profiles import Store,validate
from backend import (AskPolicy,UnknownHost,trust_host,fingerprint,preview_file,
                     list_directory,download,upload,SSHSession)
from terminal import Terminal
from app import Window, SessionPage, ProfileDialog
from theme import apply_theme
from PySide6.QtGui import QPalette,QColor

APP=QApplication.instance() or QApplication([])


def profile(**kw):
    return validate(dict({'name':'Example Linux','host':'server.example.test','username':'operator','port':22,'auth':'agent','start_dir':'/var/log'},**kw))


def fake_sftp():
    sftp=MagicMock()
    sftp.__enter__.return_value=sftp
    client=MagicMock()
    client.open_sftp.return_value=sftp
    return client,sftp


class ProfileTests(unittest.TestCase):
    def test_password_fields_are_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            p=profile();p['password']='FAKE-SHOULD-NOT-BE-SAVED';p['passphrase']='FAKE-PRIVATE'
            store.save([p])
            text=store.file.read_text()
            self.assertNotIn('FAKE-',text)
            self.assertEqual(store.file.stat().st_mode & 0o777,0o600)
            self.assertEqual(store.load()[0]['host'],p['host'])

    def test_invalid_hosts_and_ports(self):
        for data in ({'host':'https://host'},{'host':'bad host'},{'port':70000},{'username':''},{'auth':'key','key_file':''}):
            with self.assertRaises((ValueError,TypeError)):profile(**data)


class ThemeTests(unittest.TestCase):
    def test_theme_overrides_inherited_os_palette_for_all_window_states(self):
        inherited=QPalette()
        inherited.setColor(QPalette.ColorRole.Window,QColor('#ffffff'))
        inherited.setColor(QPalette.ColorRole.WindowText,QColor('#ffffff'))
        inherited.setColor(QPalette.ColorRole.ButtonText,QColor('#ffffff'))
        APP.setPalette(inherited)
        apply_theme(APP)
        for group in (QPalette.ColorGroup.Active,QPalette.ColorGroup.Inactive):
            p=APP.palette()
            self.assertEqual(p.color(group,QPalette.ColorRole.Window).name(),'#23282c')
            self.assertEqual(p.color(group,QPalette.ColorRole.WindowText).name(),'#dedede')
            self.assertEqual(p.color(group,QPalette.ColorRole.Base).name(),'#202529')
        self.assertNotEqual(APP.palette().color(QPalette.ColorGroup.Disabled,QPalette.ColorRole.ButtonText),
                            APP.palette().color(QPalette.ColorGroup.Disabled,QPalette.ColorRole.Button))


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.key=paramiko.RSAKey.generate(2048)

    def test_unknown_host_requires_decision(self):
        with self.assertRaises(UnknownHost):AskPolicy().missing_host_key(MagicMock(),'example',self.key)
        self.assertTrue(fingerprint(self.key).startswith('SHA256:'))

    def test_trust_storage_and_changed_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'known_hosts'
            trust_host(path,'example',self.key)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(paramiko.HostKeys(str(path))['example']['ssh-rsa'],self.key)
            with self.assertRaises(ValueError):trust_host(path,'example',paramiko.RSAKey.generate(2048))
            self.assertEqual(paramiko.HostKeys(str(path))['example']['ssh-rsa'],self.key)

    def test_directory_sort_and_non_shell_paths(self):
        client,sftp=fake_sftp();sftp.normalize.return_value='/var/log'
        sftp.listdir_iter.return_value=[paramiko.SFTPAttributes.from_stat(__import__('os').stat(__file__),filename='file;uname'),
          paramiko.SFTPAttributes.from_stat(__import__('os').stat(Path(__file__).parent),filename='folder')]
        result=list_directory(client,'/var/log',threading.Event())
        self.assertEqual(result['entries'][0]['kind'],'Directory')
        self.assertEqual(result['entries'][1]['path'],'/var/log/file;uname')
        client.exec_command.assert_not_called()

    def test_preview_bounded_and_binary_rejected(self):
        client,sftp=fake_sftp();sftp.stat.return_value=MagicMock(st_mode=stat.S_IFREG|0o644)
        sftp.open.return_value=io.BytesIO(b'x'*300000)
        r=preview_file(client,'/var/log/app.log')
        self.assertTrue(r['truncated']);self.assertEqual(len(r['text']),262144)
        sftp.open.return_value=io.BytesIO(b'a\x00b')
        with self.assertRaises(ValueError):preview_file(client,'/file')

    def test_special_file_preview_rejected(self):
        client,sftp=fake_sftp();sftp.stat.return_value=MagicMock(st_mode=stat.S_IFCHR)
        with self.assertRaises(ValueError):preview_file(client,'/dev/zero')

    def test_download_and_private_local_permissions(self):
        client,sftp=fake_sftp();sftp.stat.return_value=MagicMock(st_mode=stat.S_IFREG,st_size=3)
        sftp.open.return_value=io.BytesIO(b'abc')
        with tempfile.TemporaryDirectory() as tmp:
            local=Path(tmp)/'file'
            download(client,'/remote',local,threading.Event(),lambda _:None)
            self.assertEqual(local.read_bytes(),b'abc')
            self.assertEqual(local.stat().st_mode & 0o777,0o600)

    def test_cancelled_download_preserves_old_destination(self):
        client,sftp=fake_sftp();sftp.stat.return_value=MagicMock(st_mode=stat.S_IFREG,st_size=3)
        sftp.open.return_value=io.BytesIO(b'abc')
        stop=threading.Event();stop.set()
        with tempfile.TemporaryDirectory() as tmp:
            local=Path(tmp)/'file';local.write_bytes(b'old')
            with self.assertRaises(RuntimeError):download(client,'/remote',local,stop,lambda _:None)
            self.assertEqual(local.read_bytes(),b'old')
            self.assertEqual(len(list(Path(tmp).iterdir())),1)

    def test_upload_does_not_delete_destination_on_atomic_rename_failure(self):
        client,sftp=fake_sftp()
        sftp.posix_rename.side_effect=OSError('unsupported')
        with tempfile.TemporaryDirectory() as tmp:
            local=Path(tmp)/'file';local.write_bytes(b'content')
            with self.assertRaises(OSError):upload(client,local,'/destination',threading.Event(),lambda _:None)
        self.assertTrue(sftp.remove.called)
        self.assertTrue(all(c.args[0]!='/destination' for c in sftp.remove.call_args_list))


class GUITests(unittest.TestCase):
    def test_window_and_session_render_without_connecting(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=Window(Store(tmp));w.show();APP.processEvents()
            p=SessionPage(profile(),w.store,w)
            w.tabs.addTab(p,'Example');w.tabs.setCurrentWidget(p)
            APP.processEvents()
            self.assertFalse(p.terminal.online)
            self.assertIsNone(p.worker)
            self.assertGreater(p.terminal.height(),300)
            p.show_directory({'path':'/var/log','entries':[{'name':'app.log','path':'/var/log/app.log','kind':'File','size':123,'mtime':1,'permissions':'-rw-------'}],'truncated':False})
            self.assertEqual(p.table.rowCount(),1)
            p.terminal.feed(b'hello\r\n\x1b[31mworld\x1b[0m')
            p.terminal.paint_screen()
            self.assertIn('world',p.terminal.toPlainText())
            w.shutting_down=True;w.close();w.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()

    def test_terminal_keystrokes_and_scrollback(self):
        t=Terminal();t.resize(800,400);t.show();t.online=True;APP.processEvents()
        sent=[];t.send_bytes.connect(sent.append)
        QTest.keyClick(t,Qt.Key.Key_Return)
        QTest.keyClick(t,Qt.Key.Key_Up)
        QTest.keyClick(t,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
        self.assertIn(b'\r',sent);self.assertIn(b'\x1b[A',sent);self.assertIn(b'\x03',sent)
        t.feed(('line\r\n'*80).encode());t.paint_screen()
        self.assertIn('line',t.toPlainText());t.close();t.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)


class FakeServer(paramiko.ServerInterface):
    def __init__(self):self.shell=threading.Event()
    def check_auth_password(self,username,password):
        return paramiko.AUTH_SUCCESSFUL if username=='test' and password=='fake-password' else paramiko.AUTH_FAILED
    def get_allowed_auths(self,username):return 'password'
    def check_channel_request(self,kind,chanid):return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
    def check_channel_pty_request(self,*args):return True
    def check_channel_shell_request(self,channel):self.shell.set();return True


class LocalSSHTest(unittest.TestCase):
    def test_verified_session_can_send_and_receive(self):
        key=paramiko.RSAKey.generate(2048)
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(10)
        port=listener.getsockname()[1]
        stop=threading.Event()
        def serve():
            transport=None
            try:
                sock,_=listener.accept()
                transport=paramiko.Transport(sock);transport.add_server_key(key)
                server=FakeServer();transport.start_server(server=server)
                channel=transport.accept(10)
                if channel is None:return
                server.shell.wait(5);channel.settimeout(.2);channel.send(b'FAKE SHELL READY\r\n')
                while not stop.is_set():
                    try:
                        chunk=channel.recv(1024)
                        if not chunk:break
                        channel.send(chunk.upper())
                    except socket.timeout:pass
            except (EOFError,OSError,paramiko.SSHException):pass
            finally:
                if transport:transport.close()
                listener.close()
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        with tempfile.TemporaryDirectory() as tmp:
            hosts=Path(tmp)/'known_hosts';trust_host(hosts,'[127.0.0.1]:{}'.format(port),key)
            w=SSHSession(profile(host='127.0.0.1',port=port,username='test',auth='password'),'fake-password',hosts)
            received=[];errors=[]
            w.data.connect(received.append,Qt.ConnectionType.QueuedConnection)
            w.message.connect(errors.append,Qt.ConnectionType.QueuedConnection)
            w.ready.connect(lambda _:w.send(b'echo-test\r'),Qt.ConnectionType.QueuedConnection)
            try:
                w.start();deadline=time.time()+10
                while time.time()<deadline and b'ECHO-TEST' not in b''.join(received):
                    APP.processEvents();time.sleep(.01)
                self.assertIn(b'ECHO-TEST',b''.join(received),str(errors))
            finally:
                w.stop();w.wait(5000);stop.set();thread.join(2);APP.processEvents()
            self.assertFalse(w.isRunning());self.assertIsNone(w.secret)


if __name__=='__main__':unittest.main()

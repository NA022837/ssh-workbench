"""Interactive source-server SCP. No credentials in commands, profiles or logs."""
import codecs
import posixpath
import queue
import re
import shlex
import stat
import threading
import time

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (QDialog, QFormLayout, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QPushButton, QPlainTextEdit, QMessageBox)
from backend import sftp_open
from icons import icon


def transfer_command(entry, host, username, port, destination):
    # The remote end may use an older scp remote shell. Restrict its path to
    # shell-literal characters rather than relying on incompatible quoting rules.
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*',host or ''):
        raise ValueError('Enter a hostname or IPv4 address without spaces or SSH options.')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*',username or ''):
        raise ValueError('Enter a Linux username using letters, numbers, underscore, dot or hyphen.')
    if not str(port).isdigit() or not 1<=int(port)<=65535:raise ValueError('SSH port must be between 1 and 65535.')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]*',destination or '') or '..' in destination.split('/'):
        raise ValueError('Use an absolute destination path containing letters, numbers, /, dot, underscore or hyphen. Spaces and shell characters are not supported for SCP destinations.')
    source=entry.get('path')
    if not isinstance(source,str) or not source.startswith('/') or posixpath.normpath(source)!=source or source=='/' or any(ord(c)<32 or ord(c)==127 for c in source):
        raise ValueError('Select a specific file or folder with a normalized absolute path.')
    if entry.get('unsupported_name') or entry.get('kind') not in ('File','Directory') or entry.get('permissions','')[:1] not in ('-','d'):
        raise ValueError('SCP supports regular files and folders only.')
    args=['scp','-P',str(int(port))]
    for option in ('StrictHostKeyChecking=ask','PreferredAuthentications=password',
                   'PubkeyAuthentication=no','PasswordAuthentication=yes','BatchMode=no',
                   'NumberOfPasswordPrompts=3','ConnectTimeout=15',
                   'ServerAliveInterval=15','ServerAliveCountMax=3','ForwardAgent=no',
                   'ControlMaster=no','ControlPath=none','ClearAllForwardings=yes'):
        args+=['-o',option]
    if entry['kind']=='Directory':args.append('-r')
    args+=['--',source,username+'@'+host+':'+destination]
    # Refuse to continue if echo cannot be disabled on the dedicated PTY.
    return 'stty -echo && LC_ALL=C exec '+' '.join(shlex.quote(arg) for arg in args)


def prompt_kind(text):
    tail=text.lower().rstrip()
    if re.search(r'are you sure you want to continue connecting[^\n]*\?$',tail):return 'host'
    if re.search(r"(?:password|password for [^\n]+):\s*$",tail):return 'password'
    return None


class SCPWorker(QThread):
    output=Signal(str)
    prompt=Signal(str)
    completed=Signal(bool,str)

    def __init__(self,client,entry,command,session_stop,parent=None):
        super().__init__(parent)
        self.client,self.entry,self.command=client,dict(entry),command
        self.session_stop=session_stop
        self.cancelled=threading.Event()
        self.answers=queue.Queue(maxsize=1)
        self.waiting=None

    def answer(self,kind,value):
        if kind!=self.waiting or self.cancelled.is_set():return False
        if '\n' in value or '\r' in value or '\x00' in value:return False
        try:self.answers.put_nowait((kind,value));return True
        except queue.Full:return False

    def stop(self):self.cancelled.set()

    def run(self):
        channel=None
        try:
            with sftp_open(self.client) as sftp:
                attrs=sftp.lstat(self.entry['path'])
                if (attrs.st_size or 0,attrs.st_mtime or 0,stat.filemode(attrs.st_mode or 0))!=(self.entry['size'],self.entry['mtime'],self.entry['permissions']):
                    raise ValueError('Source changed since listing. Refresh and review it before sending.')
            if self.cancelled.is_set() or self.session_stop.is_set():raise RuntimeError('Transfer cancelled.')
            channel=self.client.get_transport().open_session(timeout=15)
            channel.settimeout(10)
            channel.get_pty(term='dumb',width=100,height=24)
            channel.exec_command(self.command)
            decoder=codecs.getincrementaldecoder('utf-8')(errors='replace')
            tail='';pending='';total=0;last_emit=time.monotonic();last_activity=last_emit
            while True:
                if self.cancelled.is_set() or self.session_stop.is_set():
                    raise RuntimeError('Transfer cancelled. The destination may contain a partial copy.')
                if channel.recv_ready():
                    data=channel.recv(4096)
                    if data:
                        text=decoder.decode(data)
                        # Strip terminal escape/control sequences from the plain-text log.
                        text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)
                        text=''.join(c for c in text if c in '\r\n\t' or ord(c)>=32)
                        tail=(tail+text)[-4096:]
                        if total<1024*1024:
                            pending+=text[:1024*1024-total];total+=len(text)
                        last_activity=time.monotonic()
                        kind=prompt_kind(tail)
                        if kind and not self.waiting:
                            self.waiting=kind
                            if pending:self.output.emit(pending);pending=''
                            self.prompt.emit(kind)
                try:
                    kind,value=self.answers.get_nowait()
                except queue.Empty:pass
                else:
                    if kind==self.waiting:
                        channel.sendall((value+'\n').encode('utf-8'))
                        self.waiting=None;tail='';last_activity=time.monotonic()
                    value=None
                if pending and time.monotonic()-last_emit>=.1:
                    self.output.emit(pending);pending='';last_emit=time.monotonic()
                if channel.exit_status_ready() and not channel.recv_ready():break
                if channel.closed:raise RuntimeError('SCP channel closed before a completion status was received. Check the destination for a partial copy.')
                if time.monotonic()-last_activity>300:
                    raise RuntimeError('No SCP activity for five minutes. Transfer stopped; check for a partial copy.')
                self.cancelled.wait(.02)
            if pending:self.output.emit(pending)
            code=channel.recv_exit_status()
            self.completed.emit(code==0,'Transfer completed successfully.' if code==0 else
                'SCP failed (exit {}). Review the output; the destination may contain a partial copy.'.format(code))
        except Exception as exc:self.completed.emit(False,str(exc)[:700])
        finally:
            self.waiting=None
            while not self.answers.empty():
                try:self.answers.get_nowait()
                except queue.Empty:break
            if channel:channel.close()


class TransferDialog(QDialog):
    def __init__(self,page,entry):
        super().__init__(page)
        self.page,self.entry=page,dict(entry)
        self.worker=None;self.result_message='';self.success=False;self.close_pending=False
        self.setWindowTitle('Send to server · SCP');self.resize(680,570)
        layout=QVBoxLayout(self)
        source=QLabel('From {}@{}\n{}'.format(page.profile['username'],page.profile['host'],entry['path']))
        source.setTextFormat(Qt.TextFormat.PlainText);source.setWordWrap(True);layout.addWidget(source)
        form=QFormLayout();layout.addLayout(form)
        self.profile=QComboBox();self.profile.addItem('Enter destination manually',None)
        for profile in page.store.load():
            self.profile.addItem(profile['name']+' · '+profile['host'],profile)
        form.addRow('Saved destination',self.profile)
        self.host=QLineEdit();self.user=QLineEdit(page.profile['username']);self.port=QLineEdit('22')
        self.destination=QLineEdit('/tmp/')
        for label,field in [('Destination server',self.host),('Username',self.user),('SSH port',self.port),('Destination path',self.destination)]:form.addRow(label,field)
        self.profile.currentIndexChanged.connect(self.pick_profile)
        note=QLabel('Add a trailing / to copy into an existing folder and retain the source name. Existing destination files can be overwritten. Folder copies include contents and follow nested symbolic links. The source is retained.\nSCP runs on the source server and needs network access to the destination. Password authentication must be enabled there. Destination paths cannot contain spaces or shell characters.')
        note.setWordWrap(True);note.setObjectName('hintText');layout.addWidget(note)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(2000);layout.addWidget(self.log,1)
        self.status=QLabel('Review the destination, then start the transfer.');self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.status)
        auth=QHBoxLayout();self.password=QLineEdit();self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Enter destination password when prompted');self.password.setEnabled(False)
        self.send_password=QPushButton('Send password');self.send_password.setEnabled(False)
        self.send_password.clicked.connect(self.submit_password);self.password.returnPressed.connect(self.submit_password)
        self.trust=QPushButton('Verify host key…');self.trust.setEnabled(False);self.trust.clicked.connect(self.trust_host)
        auth.addWidget(self.password,1);auth.addWidget(self.send_password);auth.addWidget(self.trust);layout.addLayout(auth)
        buttons=QHBoxLayout();buttons.addStretch()
        self.start_button=QPushButton('Start transfer');self.start_button.setIcon(icon('send'));self.start_button.setProperty('role','primary')
        self.start_button.clicked.connect(self.start)
        self.close_button=QPushButton('Close');self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.start_button);buttons.addWidget(self.close_button);layout.addLayout(buttons)

    def pick_profile(self):
        profile=self.profile.currentData()
        if profile:
            self.host.setText(profile['host']);self.user.setText(profile['username']);self.port.setText(str(profile['port']))
            self.destination.setText(profile.get('start_dir','/') if str(profile.get('start_dir','/')).startswith('/') else '/tmp/')

    def start(self):
        try:command=transfer_command(self.entry,self.host.text().strip(),self.user.text().strip(),self.port.text().strip(),self.destination.text().strip())
        except ValueError as exc:QMessageBox.warning(self,'Check destination',str(exc));return
        if not self.page.client or self.page.closing:return
        if QMessageBox.question(self,'Confirm SCP copy','Copy to {}@{}:{}?\n\nExisting destination files may be overwritten. Folder copies follow nested symlinks.'.format(self.user.text().strip(),self.host.text().strip(),self.destination.text().strip()),QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        self.worker=SCPWorker(self.page.client,self.entry,command,self.page.worker.stop_event,self)
        self.page.tasks.append(self.worker)
        self.page.file_busy=True;self.page.file_controls(False)
        for field in (self.profile,self.host,self.user,self.port,self.destination,self.start_button):field.setEnabled(False)
        self.close_button.setText('Cancel transfer');self.status.setText('Connecting from source server…')
        self.worker.output.connect(self.show_output,Qt.ConnectionType.QueuedConnection)
        self.worker.prompt.connect(self.on_prompt,Qt.ConnectionType.QueuedConnection)
        self.worker.completed.connect(self.completed,Qt.ConnectionType.QueuedConnection)
        self.worker.finished.connect(self.finished_worker,Qt.ConnectionType.QueuedConnection)
        self.worker.start()

    def show_output(self,text):
        cursor=self.log.textCursor();cursor.movePosition(cursor.MoveOperation.End);cursor.insertText(text)
        self.log.setTextCursor(cursor);self.log.ensureCursorVisible()

    def on_prompt(self,kind):
        if self.close_pending:return
        self.password.setEnabled(kind=='password');self.send_password.setEnabled(kind=='password')
        self.trust.setEnabled(kind=='host')
        self.status.setText('Enter the destination password below.' if kind=='password' else 'Verify the destination fingerprint shown above before trusting it.')
        if kind=='password':self.password.setFocus()

    def submit_password(self):
        if self.worker and self.password.isEnabled() and self.password.text():
            if self.worker.answer('password',self.password.text()):
                self.password.clear();self.password.setEnabled(False);self.send_password.setEnabled(False)
                self.status.setText('Authenticating / transferring…')

    def trust_host(self):
        if not self.worker or self.worker.waiting!='host':return
        box=QMessageBox(self);box.setWindowTitle('Verify destination host key')
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText('Confirm the fingerprint with your administrator before trusting this destination. The key will be saved in the source user’s known_hosts.\n\n'+self.log.toPlainText()[-4096:])
        box.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec()==QMessageBox.StandardButton.Yes:
            self.worker.answer('host','yes');self.trust.setEnabled(False)

    def completed(self,success,message):
        self.success,self.result_message=success,message
        self.status.setText(message);self.password.clear();self.password.setEnabled(False)
        self.send_password.setEnabled(False);self.trust.setEnabled(False)

    def finished_worker(self):
        if self.worker in self.page.tasks:self.page.tasks.remove(self.worker)
        self.page.file_busy=False
        if not self.page.closing:self.page.file_controls(bool(self.page.client))
        self.close_button.setText('Close')
        if self.close_pending:super().reject()

    def reject(self):
        if self.worker and self.worker in self.page.tasks:
            self.close_pending=True;self.worker.stop();self.password.clear()
            self.status.setText('Cancelling… The destination may contain a partial copy.')
            return
        super().reject()

    def closeEvent(self,event):
        if self.worker and self.worker in self.page.tasks:self.reject();event.ignore()
        else:super().closeEvent(event)

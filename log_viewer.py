"""Bounded, read-only tail snapshots. Follow is opt-in and pauses when hidden."""
from datetime import datetime
import shlex,stat
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QCheckBox,QPushButton,QPlainTextEdit,QApplication
from backend import Task,sftp_open
from system_backend import execute
from icons import icon


def tail_snapshot(client,path,stop):
    if not isinstance(path,str) or not path.startswith('/') or any(ord(c)<32 or ord(c)==127 for c in path):raise ValueError('Select an absolute regular-file path.')
    if stop.is_set():raise RuntimeError('Log request cancelled')
    with sftp_open(client) as sftp:
        if not stat.S_ISREG(sftp.lstat(path).st_mode or 0):raise ValueError('Log snapshots support regular files only, not symlinks or devices.')
    text=execute(client,'tail -c 262144 -- '+shlex.quote(path),stop,timeout=15,limit=1048576)
    if '\x00' in text:raise ValueError('This appears to be a binary file.')
    lines=text.splitlines()
    return dict(text='\n'.join(lines[-200:]),limited=len(lines)>200 or len(text.encode('utf-8'))>=262144)


class LogViewer(QDialog):
    def __init__(self,page,entry):
        super().__init__(page);self.page=page;self.path=entry['path'];self.job=None;self.text='';self.close_pending=False
        self.setWindowTitle('Log viewer · '+entry['name']);self.resize(950,630)
        layout=QVBoxLayout(self)
        path=QLabel(page.profile['host']+' · '+self.path);path.setTextFormat(Qt.TextFormat.PlainText);path.setWordWrap(True);layout.addWidget(path)
        row=QHBoxLayout();self.query=QLineEdit();self.query.setPlaceholderText('Filter loaded lines…');self.query.setClearButtonEnabled(True);self.query.textChanged.connect(self.filter_lines);row.addWidget(self.query,1)
        self.follow=QCheckBox('Follow · 5s');self.follow.setToolTip('Refresh a bounded tail snapshot every five seconds while this window is visible. This is not a lossless log stream.');row.addWidget(self.follow)
        self.refresh=QPushButton('Refresh');self.refresh.setIcon(icon('refresh'));self.refresh.clicked.connect(self.load);row.addWidget(self.refresh)
        copy=QPushButton('Copy visible');copy.setIcon(icon('copy'));copy.clicked.connect(lambda:QApplication.clipboard().setText(self.body.toPlainText()));row.addWidget(copy)
        layout.addLayout(row)
        self.body=QPlainTextEdit();self.body.setReadOnly(True);self.body.setMaximumBlockCount(220);layout.addWidget(self.body,1)
        self.status=QLabel('Read-only · Last 200 lines from at most 256 KB · Follow off');self.status.setTextFormat(Qt.TextFormat.PlainText);self.status.setWordWrap(True);layout.addWidget(self.status)
        close=QPushButton('Close');close.clicked.connect(self.reject);layout.addWidget(close)
        self.timer=QTimer(self);self.timer.setInterval(5000);self.timer.timeout.connect(self.auto_refresh)
        self.follow.toggled.connect(lambda enabled:self.timer.start() if enabled else self.timer.stop())
        QTimer.singleShot(0,self.load)

    def filter_lines(self):
        query=self.query.text().casefold();self.body.setPlainText('\n'.join(line for line in self.text.splitlines() if query in line.casefold()))
        self.body.verticalScrollBar().setValue(self.body.verticalScrollBar().maximum())

    def auto_refresh(self):
        if self.isVisible() and not self.job and not self.close_pending:self.load()

    def load(self):
        if self.job or self.close_pending:return
        if not self.page.client or self.page.closing or self.page.worker.stop_event.is_set():
            self.timer.stop();self.follow.setChecked(False);self.status.setText('Disconnected · Last loaded log snapshot is stale.');return
        client,stop=self.page.client,self.page.worker.stop_event
        self.job=Task(lambda progress:tail_snapshot(client,self.path,stop),self)
        task=self.job;self.page.tasks.append(task);self.refresh.setEnabled(False)
        task.result.connect(self.show_snapshot,Qt.ConnectionType.QueuedConnection)
        task.failed.connect(self.on_error,Qt.ConnectionType.QueuedConnection)
        task.finished.connect(lambda:self.finished_job(task),Qt.ConnectionType.QueuedConnection);task.start()

    def show_snapshot(self,data):
        self.text=data['text'];self.filter_lines()
        self.status.setText('Snapshot: '+datetime.now().astimezone().strftime('%d-%b-%Y %H:%M:%S %Z')+' · Last 200 lines / 256 KB maximum'+(' · Truncated' if data['limited'] else ''))

    def on_error(self,message):
        self.follow.setChecked(False);self.timer.stop()
        self.status.setText(message+' · Last loaded snapshot is stale; follow paused.')

    def finished_job(self,task):
        if task in self.page.tasks:self.page.tasks.remove(task)
        self.job=None;task.deleteLater();self.refresh.setEnabled(True)
        if self.close_pending:super().reject()

    def reject(self):
        self.timer.stop()
        self.close_pending=True
        if self.job:
            self.status.setText('Finishing the current bounded read before closing…');return
        super().reject()

    def closeEvent(self,event):
        if self.job:self.reject();event.ignore()
        else:self.close_pending=True;self.timer.stop();super().closeEvent(event)

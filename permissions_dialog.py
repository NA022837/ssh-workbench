"""Review and apply Linux mode bits for one remote file or directory."""
import re
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox,QDialog,QGridLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QVBoxLayout
from backend import change_permissions


def parse_mode(text):
    text=text.strip()
    if not re.fullmatch(r'[0-7]{3,4}',text):raise ValueError('Use 3 or 4 octal digits, for example 644 or 0755.')
    return int(text,8)


class PermissionsDialog(QDialog):
    def __init__(self,session,snapshot):
        super().__init__(session)
        self.session,self.snapshot=session,dict(snapshot)
        self.applying=False
        self.syncing=False
        self.setWindowTitle('Remote permissions')
        self.setMinimumWidth(490)
        layout=QVBoxLayout(self)
        self.target=QLabel(snapshot['path'])
        self.target.setTextFormat(Qt.TextFormat.PlainText)
        self.target.setWordWrap(True)
        layout.addWidget(self.target)
        self.current=QLabel()
        layout.addWidget(self.current)
        layout.addWidget(QLabel('Owner UID: {}  ·  Group GID: {}'.format(snapshot['uid'],snapshot['gid'])))
        grid=QGridLayout()
        for column,title in enumerate(('Owner','Group','Others'),1):grid.addWidget(QLabel(title),0,column)
        self.boxes={}
        for row,(title,mask) in enumerate((('Read',4),('Write',2),('Execute / traverse',1)),1):
            grid.addWidget(QLabel(title),row,0)
            for column,shift in enumerate((6,3,0),1):
                bit=mask<<shift
                box=QCheckBox()
                box.setAccessibleName(('Owner','Group','Others')[column-1]+' '+title)
                box.toggled.connect(self.boxes_changed)
                self.boxes[bit]=box
                grid.addWidget(box,row,column)
        layout.addLayout(grid)
        special=QHBoxLayout()
        for bit,title in ((0o4000,'Set UID'),(0o2000,'Set GID'),(0o1000,'Sticky')):
            box=QCheckBox(title)
            box.setToolTip('Special Unix permission bit; retained unless you change it')
            box.toggled.connect(self.boxes_changed)
            self.boxes[bit]=box
            special.addWidget(box)
        layout.addLayout(special)
        row=QHBoxLayout()
        row.addWidget(QLabel('Octal mode'))
        self.mode=QLineEdit()
        self.mode.setMaxLength(4)
        self.mode.setPlaceholderText('0755')
        self.mode.textChanged.connect(self.mode_changed)
        row.addWidget(self.mode)
        layout.addLayout(row)
        note=QLabel('Applies only to this item, without recursion. Your connected account must have permission to change its mode.')
        note.setWordWrap(True)
        note.setObjectName('hintText')
        layout.addWidget(note)
        self.status=QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions=QHBoxLayout();actions.addStretch()
        self.apply_button=QPushButton('Apply permissions')
        self.apply_button.setProperty('role','primary')
        self.apply_button.clicked.connect(self.apply)
        self.close_button=QPushButton('Close')
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.apply_button);actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.update_current()
        self.mode.setText('{:04o}'.format(snapshot['mode']))

    def update_current(self):
        self.current.setText('Current: {:04o}  ({})'.format(self.snapshot['mode'],self.snapshot['symbolic']))

    def boxes_changed(self,*args):
        if not self.syncing:self.mode.setText('{:04o}'.format(sum(bit for bit,box in self.boxes.items() if box.isChecked())))

    def mode_changed(self,text):
        try:value=parse_mode(text)
        except ValueError as exc:
            self.apply_button.setEnabled(False)
            self.status.setText(str(exc))
            return
        self.syncing=True
        for bit,box in self.boxes.items():box.setChecked(bool(value&bit))
        self.syncing=False
        self.apply_button.setEnabled(not self.applying)
        self.status.setText('Review change: {:04o} → {:04o}'.format(self.snapshot['mode'],value))

    def apply(self):
        if self.applying:return
        try:value=parse_mode(self.mode.text())
        except ValueError as exc:
            self.status.setText(str(exc));return
        session=self.session
        if not session.client or not session.worker or session.worker.stop_event.is_set():
            self.status.setText('Session disconnected. Reconnect before changing permissions.');return
        if session.file_busy:
            self.status.setText('Wait for the current file operation to finish.');return
        if value==self.snapshot['mode']:
            self.status.setText('Permissions are unchanged.');return
        client,stop=session.client,session.worker.stop_event
        snapshot=dict(self.snapshot)
        self.applying=True
        self.set_controls(False)
        self.status.setText('Applying permissions…')
        def done(updated):
            self.snapshot=updated
            self.applying=False
            self.set_controls(True)
            self.update_current()
            self.status.setText('Applied and verified: {:04o}'.format(updated['mode']))
        def failed(message):
            self.applying=False
            self.set_controls(True)
            self.status.setText(message)
        session.task(lambda progress:change_permissions(client,snapshot,value,stop),done,failed)

    def set_controls(self,enabled):
        self.mode.setEnabled(enabled)
        for box in self.boxes.values():box.setEnabled(enabled)
        self.apply_button.setEnabled(enabled)
        self.close_button.setEnabled(enabled)

    def reject(self):
        if not self.applying:super().reject()

    def closeEvent(self,event):
        if self.applying:event.ignore()
        else:
            event.accept()
            super().reject()

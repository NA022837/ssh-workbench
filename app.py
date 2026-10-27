#!/usr/bin/env python3
"""SSH Workbench: desktop SSH terminal and SFTP browser, independent of CEM."""
import getpass
from pathlib import Path
import posixpath
import shlex
import sys
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QSize, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QScrollArea, QToolButton, QTabBar, QMenu, QCheckBox, QStackedWidget)

from backend import SSHSession, Task, fingerprint, trust_host, list_directory, preview_file, download, upload, read_editable, delete_file, read_permissions, create_directory, delete_directory
from file_actions import archive_entry, archive_command, file_properties, file_checksum, shell_command, verify_script, valid_path
from remote_editor import RemoteEditor
from permissions_dialog import PermissionsDialog
from scp_transfer import TransferDialog
from system_backend import format_bytes
from system_panel import SystemPanel
from log_viewer import LogViewer
from server_clock import ServerClock
from backend import rename_entry
from profiles import Store, validate, group_path, group_parent, complete_groups
from terminal import Terminal
from theme import apply_theme
from icons import icon

QUEUED = Qt.ConnectionType.QueuedConnection


class FileItem(QTableWidgetItem):
    def __lt__(self, other):
        return self.data(Qt.ItemDataRole.UserRole + 1) < other.data(Qt.ItemDataRole.UserRole + 1)


def file_type_label(entry):
    if entry['kind']=='Directory':return 'Folder'
    if entry['kind']=='Link':return 'Symbolic link'
    special={'p':'Named pipe','s':'Socket','c':'Character device','b':'Block device'}
    prefix=entry.get('permissions','')[:1]
    if prefix in special:return special[prefix]
    if entry.get('unsupported_name'):return 'File'
    suffix=posixpath.splitext(entry['name'])[1].lstrip('.')
    return suffix.upper()+' file' if suffix else 'File'


def entry_icon(kind):
    """Small blue folders and neutral documents, painted at display scale."""
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    if kind == 'Directory':
        painter.setBrush(QColor('#4e92cf'))
        painter.drawRoundedRect(4, 8, 16, 11, 3, 3)
        painter.drawRoundedRect(4, 13, 32, 23, 4, 4)
    else:
        painter.setBrush(QColor('#8d99a6' if kind == 'File' else '#76b6d5'))
        painter.drawRoundedRect(9, 5, 23, 31, 3, 3)
        painter.setPen(QColor('#303841'))
        for y in (16, 22, 28):
            painter.drawLine(14, y, 26, y)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class ProfileDialog(QDialog):
    def __init__(self, profile=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Connection profile')
        self.setMinimumWidth(460)
        self.profile = profile or {}
        form = QFormLayout(self)
        self.fields = {}
        defaults = {'port':22, 'username':getpass.getuser(), 'start_dir':'.'}
        labels = {'name':'Display name', 'host':'Hostname / IP', 'port':'SSH port', 'username':'Username', 'start_dir':'Initial SFTP directory'}
        for key, label in labels.items():
            field = QLineEdit(str(self.profile.get(key, defaults.get(key, ''))))
            self.fields[key] = field
            form.addRow(label, field)
        self.group=QComboBox()
        self.group.setEditable(True)
        groups={'Ungrouped','Dev','Cert','Prod'}
        groups.update(p.get('group','Ungrouped') for p in getattr(parent,'profiles',[]))
        self.group.addItems(sorted(groups,key=str.casefold))
        self.group.setCurrentText(self.profile.get('group','Ungrouped'))
        groups.update(getattr(parent,'groups',[]))
        self.group.clear();self.group.addItems(sorted(groups,key=str.casefold))
        self.group.setCurrentText(self.profile.get('group','Ungrouped'))
        self.group.setToolTip('Choose a group or type a nested path, e.g. Prod / CEM / Kansas City')
        form.addRow('Connection group',self.group)
        self.auth = QComboBox()
        for label, value in [('SSH agent / local keys','agent'), ('Password','password'), ('Private key file','key')]:
            self.auth.addItem(label, value)
        self.auth.setCurrentIndex(max(0, self.auth.findData(self.profile.get('auth','agent'))))
        form.addRow('Authentication', self.auth)
        self.key = QLineEdit(self.profile.get('key_file', ''))
        keyrow = QHBoxLayout()
        keyrow.addWidget(self.key)
        choose = QPushButton('Browse…')
        choose.clicked.connect(self.pick_key)
        keyrow.addWidget(choose)
        form.addRow('Private key path', keyrow)
        note = QLabel('Passwords and passphrases are requested when connecting and are not saved in profiles.')
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def pick_key(self):
        name, _ = QFileDialog.getOpenFileName(self, 'Select private key', str(Path.home()/'.ssh'))
        if name:
            self.key.setText(name)

    def save(self):
        try:
            self.value = validate(dict(self.profile, **{k:v.text() for k,v in self.fields.items()}, auth=self.auth.currentData(), key_file=self.key.text(),group=self.group.currentText()))
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, 'Check connection details', str(exc))


def session_button(text):
    button=QToolButton();button.setText(text)
    button.setObjectName('sessionTool')
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setFixedSize(27,25);button.setIconSize(QSize(14,14))
    button.setToolTip(text);button.setAccessibleName(text)
    return button


class SessionPage(QWidget):
    connection_changed=Signal(str)
    def __init__(self, profile, store, parent=None):
        super().__init__(parent)
        self.profile, self.store = dict(profile), store
        self.worker, self.client = None, None
        self.tasks = []
        self.file_busy, self.closing, self.waiting_for_trust = False, False, False
        self.pending_secret = None
        self.connection_state='disconnected'
        self.reconnect_pending=False
        self.reconnect_timer=QTimer(self)
        self.reconnect_timer.setInterval(100)
        self.reconnect_timer.timeout.connect(self.finish_reconnect)
        self.entries = []
        self.current_dir = profile.get('start_dir') or '.'
        self.history=[]
        self.history_index=-1
        self.file_controls_enabled=False
        self.list_truncated=False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8,4,8,4)
        layout.setSpacing(3)
        head = QHBoxLayout()
        target = QLabel('{}@{}:{}'.format(profile['username'],profile['host'],profile['port']))
        target.setTextFormat(Qt.TextFormat.PlainText)
        target.setObjectName('sessionTarget')
        self.connection_light=QLabel();self.connection_light.setFixedSize(16,16)
        head.addWidget(self.connection_light)
        head.addWidget(target)
        self.environment=QLabel(profile.get('group','Ungrouped'))
        self.environment.setTextFormat(Qt.TextFormat.PlainText)
        self.environment.setObjectName('environmentBadge')
        self.environment.setProperty('production',any(part.strip().casefold() in ('prod','production') for part in profile.get('group','').split('/')))
        self.environment.setMaximumWidth(230)
        self.environment.setToolTip('Session group: '+profile.get('group','Ungrouped'))
        head.addWidget(self.environment)
        head.addStretch()
        self.connect_button = session_button('Connect')
        self.connect_button.setProperty('role','primary')
        self.disconnect_button = session_button('Disconnect')
        self.reconnect_button=session_button('Reconnect SSH')
        self.reconnect_button.setIcon(icon('refresh'))
        self.reconnect_button.clicked.connect(self.reconnect_session)
        self.interrupt_button = session_button('Send Ctrl+C')
        self.terminal_toggle=session_button('Show terminal')
        self.terminal_toggle.setCheckable(True)
        self.terminal_toggle.setToolTip('Show or hide the terminal; the SSH connection stays open')
        self.terminal_toggle.toggled.connect(self.toggle_terminal)
        self.interrupt_button.setEnabled(False)
        self.interrupt_button.clicked.connect(lambda:self.send_bytes(b'\x03'))
        self.disconnect_button.setEnabled(False)
        self.connect_button.clicked.connect(self.connect_session)
        self.disconnect_button.clicked.connect(self.disconnect_session)
        head.addWidget(self.terminal_toggle)
        self.monitor_toggle=session_button('System monitor')
        self.monitor_toggle.setCheckable(True)
        self.monitor_toggle.setIcon(icon('activity'))
        self.monitor_toggle.toggled.connect(self.toggle_monitor)
        head.addWidget(self.monitor_toggle)
        head.addWidget(self.connect_button)
        head.addWidget(self.reconnect_button)
        head.addWidget(self.interrupt_button)
        head.addWidget(self.disconnect_button)
        self.interrupt_button.setIcon(icon('stop'))
        layout.addLayout(head)
        self.status = QLabel('Disconnected · No connection made')
        self.status.setObjectName('sessionStatus')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setMaximumHeight(27)
        self.server_time=QLabel()
        self.server_time.setObjectName('serverTime')
        self.server_time.setTextFormat(Qt.TextFormat.PlainText)
        self.server_time.setToolTip('Server-local date and timezone · SSH sync every 60 seconds; displayed seconds advance locally between syncs')
        self.server_time.hide()
        status_row=QHBoxLayout();status_row.setContentsMargins(0,0,0,0)
        status_row.addWidget(self.status,1);status_row.addWidget(self.server_time)
        layout.addLayout(status_row)
        self.server_clock=ServerClock(self)
        self.server_clock.changed.connect(self.show_server_time)
        self.connection_changed.connect(self.server_clock.state_changed)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.session_splitter=split
        split.setChildrenCollapsible(False)
        split.setHandleWidth(2)
        files = QWidget()
        self.files_panel=files
        files.setObjectName('filesPanel')
        files.setMinimumWidth(280)
        files_layout = QVBoxLayout(files)
        files_layout.setContentsMargins(0,0,2,0)
        files_layout.setSpacing(3)
        files_title = QLabel('REMOTE FILES  /  SFTP')
        files_title.setObjectName('panelTitle')
        files_layout.addWidget(files_title)
        self.breadcrumbs = QWidget()
        self.crumb_layout = QHBoxLayout(self.breadcrumbs)
        self.crumb_layout.setContentsMargins(0,0,0,0)
        self.crumb_layout.setSpacing(3)
        crumb_scroll = QScrollArea()
        crumb_scroll.setWidgetResizable(True)
        crumb_scroll.setWidget(self.breadcrumbs)
        crumb_scroll.setFixedHeight(23)
        crumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        files_layout.addWidget(crumb_scroll)
        self.root_button = QPushButton('/')
        self.root_button.setToolTip('Root folder /')
        self.root_button.clicked.connect(lambda:self.browse('/'))
        self.home_button = QPushButton('~')
        self.home_button.setToolTip('Home folder')
        self.home_button.clicked.connect(lambda:self.browse('.'))
        self.refresh_button = QPushButton('↻')
        self.refresh_button.setToolTip('Refresh folder · F5')
        self.refresh_button.clicked.connect(lambda:self.browse(self.current_dir))
        self.path = QLineEdit(profile.get('start_dir') or '.')
        self.path.returnPressed.connect(self.go_to_path)
        self.go = QPushButton('Go')
        self.go.clicked.connect(self.go_to_path)
        self.go.setToolTip('Open a typed path, or enter the selected folder when the path is unchanged')
        self.up = QPushButton('↑')
        self.up.setToolTip('Parent folder · Alt+Up')
        self.up.clicked.connect(lambda:self.browse(posixpath.dirname(self.current_dir.rstrip('/')) or '/'))
        navrow=QHBoxLayout()
        self.back_button=QPushButton('←')
        self.back_button.setAccessibleName('Back')
        self.back_button.setToolTip('Previous folder · Alt+Left')
        self.back_button.clicked.connect(lambda:self.navigate_history(-1))
        self.forward_button=QPushButton('→')
        self.forward_button.setAccessibleName('Forward')
        self.forward_button.setToolTip('Next folder · Alt+Right')
        self.forward_button.clicked.connect(lambda:self.navigate_history(1))
        self.down=QPushButton('↓')
        self.down.setToolTip('Enter selected folder · Alt+Down')
        self.down.clicked.connect(self.enter_selected_folder)
        for button in (self.back_button,self.forward_button,self.up,self.down,self.root_button,self.home_button,self.refresh_button):
            button.setFixedSize(28,26)
            button.setProperty('role','tool')
            button.setAccessibleName(button.toolTip())
            navrow.addWidget(button)
        for button,name in zip((self.back_button,self.forward_button,self.up,self.down,self.root_button,self.home_button,self.refresh_button),
                               ('back','forward','up','down','root','home','refresh')):
            button.setText('')
            button.setIcon(icon(name))
            button.setIconSize(QSize(15,15))
        navrow.addWidget(self.path,1)
        navrow.addWidget(self.go)
        self.path.setFixedHeight(26)
        self.go.setText('');self.go.setIcon(icon('forward'));self.go.setFixedSize(28,26)
        files_layout.addLayout(navrow)
        self.file_filter=QLineEdit()
        self.file_filter.setPlaceholderText('Filter files in this folder…')
        self.file_filter.setClearButtonEnabled(True)
        self.file_filter.setFixedHeight(25)
        self.file_filter.textChanged.connect(self.apply_file_filter)
        files_layout.addWidget(self.file_filter)
        self.table = QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(['Name','Size','Modified','Perm.','Type'])
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(14,14))
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for column in (1,2,3,4):
            self.table.horizontalHeader().setSectionResizeMode(column,QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().moveSection(4,1)
        self.table.verticalHeader().hide()
        self.table.setSortingEnabled(True)
        self.table.sortItems(0,Qt.SortOrder.AscendingOrder)
        self.table.cellDoubleClicked.connect(self.open_item)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.file_context_menu)
        self.table.setToolTip('Right-click a file, folder or empty space for file actions')
        self.table.model().layoutChanged.connect(self.apply_file_filter)
        files_layout.addWidget(self.table)
        self.file_actions_bar=QWidget()
        self.file_actions_bar.setObjectName('fileActions')
        buttons = QHBoxLayout(self.file_actions_bar)
        buttons.setContentsMargins(4,3,4,3)
        buttons.setSpacing(3)
        self.preview = QToolButton()
        self.preview.clicked.connect(self.view_selected)
        self.get = QToolButton()
        self.get.clicked.connect(self.download_selected)
        self.put = QToolButton()
        self.put.clicked.connect(self.upload_file)
        self.new_file_button = QToolButton()
        self.new_file_button.clicked.connect(self.new_file)
        self.edit_file_button = QToolButton()
        self.edit_file_button.clicked.connect(self.edit_selected)
        self.copy_path_button=QToolButton()
        self.copy_path_button.clicked.connect(self.copy_remote_path)
        self.delete_button=QToolButton()
        self.delete_button.clicked.connect(self.delete_selected)
        self.permissions_button=QToolButton()
        self.permissions_button.clicked.connect(self.edit_permissions)
        self.new_folder_button=QToolButton()
        self.new_folder_button.clicked.connect(self.new_folder)
        for button,name,label in ((self.preview,'view','View file'),(self.get,'download','Download'),
                                  (self.put,'upload','Upload file'),(self.new_file_button,'new','New file'),
                                  (self.edit_file_button,'edit','Edit file'),(self.copy_path_button,'copy','Copy path'),
                                  (self.delete_button,'remove','Delete item'),(self.permissions_button,'lock','Permissions'),
                                  (self.new_folder_button,'new-folder','New folder')):
            button.setIcon(icon(name,'#ee9898' if name=='remove' else '#b9c9db'))
            button.setIconSize(QSize(17,17))
            button.setFixedSize(32,30)
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.setText(label)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            buttons.addWidget(button)
        buttons.addStretch()
        files_layout.addWidget(self.file_actions_bar)
        self.file_actions_bar.hide()
        self.file_status = QLabel('Double-click a directory to open it. File browsing and shell working directories are independent.')
        self.file_status.setWordWrap(True)
        self.file_status.setObjectName('hintText')
        self.file_status.setTextFormat(Qt.TextFormat.PlainText)
        files_layout.addWidget(self.file_status)
        self.file_buttons = [self.go,self.up,self.preview,self.get,self.put,self.path,self.breadcrumbs,
                             self.root_button,self.home_button,self.refresh_button,self.new_file_button,self.edit_file_button,
                             self.down,self.file_filter,self.copy_path_button,self.delete_button,self.permissions_button,
                             self.new_folder_button]
        self.file_controls(False)
        self.file_shortcuts=[]
        for key,button in [('Alt+Up',self.up),('Alt+Down',self.down),('Alt+Left',self.back_button),
                           ('Alt+Right',self.forward_button),('F5',self.refresh_button)]:
            shortcut=QShortcut(QKeySequence(key),files)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(button.click)
            self.file_shortcuts.append(shortcut)
        for key in ('Ctrl+L','Meta+L'):
            shortcut=QShortcut(QKeySequence(key),files)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.focus_path)
            self.file_shortcuts.append(shortcut)
        terminal_box = QWidget()
        self.terminal_panel=terminal_box
        term_layout = QVBoxLayout(terminal_box)
        term_layout.setContentsMargins(8,0,0,0)
        term_title = QLabel('SSH TERMINAL')
        term_title.setObjectName('panelTitle')
        term_title.setToolTip('Commands run as the connected user')
        term_layout.addWidget(term_title)
        self.terminal = Terminal()
        self.terminal.send_bytes.connect(self.send_bytes)
        self.terminal.dimensions.connect(self.resize_terminal)
        term_layout.addWidget(self.terminal)
        self.terminal.feed(b'SSH Workbench\r\nConnect to a profile to start a remote shell.\r\n')
        self.terminal_toggle.setIcon(icon('terminal'))
        self.connect_button.setIcon(icon('connect','#ffffff'))
        self.disconnect_button.setIcon(icon('disconnect'))
        split.addWidget(files)
        split.addWidget(terminal_box)
        split.setStretchFactor(0,1)
        split.setStretchFactor(1,1)
        split.setSizes([800,500])
        self.workspace=QStackedWidget()
        self.workspace.addWidget(split)
        self.monitor=SystemPanel(self)
        self.workspace.addWidget(self.monitor)
        layout.addWidget(self.workspace,1)
        terminal_box.hide()
        self.interrupt_button.hide()
        self.set_connection_state('disconnected')

    def set_connection_state(self,state):
        self.connection_state=state
        color={'connected':'#63ce91','connecting':'#e5b85b','disconnecting':'#e5b85b','disconnected':'#ef7979'}[state]
        self.connection_light.setPixmap(icon('status',color).pixmap(14,14))
        self.connection_light.setToolTip(state.capitalize());self.connection_light.setAccessibleName('SSH '+state)
        self.connection_changed.emit(state)

    def show_server_time(self,text):
        visible=bool(text) and self.connection_state=='connected' and not self.closing
        self.server_time.setText(text if visible else '')
        self.server_time.setVisible(visible)

    def reconnect_session(self):
        if self.closing or self.reconnect_pending:return
        if any(task.isRunning() for task in self.tasks):
            self.status.setText('Wait for active operations to finish before reconnecting.');return
        if self.client and QMessageBox.question(self,'Reconnect SSH','Reconnect this SSH session? The current shell will close. Running commands may be interrupted.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        self.reconnect_pending=True;self.reconnect_button.setEnabled(False)
        self.disconnect_session(keep_reconnect=True);self.reconnect_timer.start()

    def finish_reconnect(self):
        if self.closing:
            self.reconnect_timer.stop();self.reconnect_pending=False;return
        if self.worker and self.worker.isRunning():return
        if any(task.isRunning() for task in self.tasks):return
        self.reconnect_timer.stop();self.reconnect_pending=False;self.reconnect_button.setEnabled(True)
        self.connect_session()

    def toggle_monitor(self,visible):
        self.workspace.setCurrentIndex(1 if visible else 0)
        self.monitor_toggle.setText('Back to files' if visible else 'System monitor')
        self.monitor_toggle.setToolTip(self.monitor_toggle.text())
        self.monitor.set_active(visible)
        self.terminal_toggle.setVisible(not visible)
        self.interrupt_button.setVisible(not visible and self.terminal_toggle.isChecked())

    def toggle_terminal(self, visible):
        self.terminal_panel.setVisible(visible)
        self.interrupt_button.setVisible(visible)
        self.terminal_toggle.setText('Hide terminal' if visible else 'Show terminal')
        self.terminal_toggle.setToolTip(self.terminal_toggle.text())
        if visible:
            width=max(700,self.session_splitter.width())
            self.session_splitter.setSizes([int(width*.6),int(width*.4)])
            if self.terminal.online:self.terminal.setFocus()

    def file_controls(self, enabled):
        self.file_controls_enabled=enabled
        for w in self.file_buttons:
            w.setEnabled(enabled)
        self.update_history_buttons()

    def update_history_buttons(self):
        self.back_button.setEnabled(self.file_controls_enabled and self.history_index>0)
        self.forward_button.setEnabled(self.file_controls_enabled and self.history_index+1<len(self.history))
        self.back_button.setToolTip('Previous folder · Alt+Left' if self.history_index>0 else
                                   'Back is available after opening another folder')
        self.forward_button.setToolTip('Next folder · Alt+Right' if self.history_index+1<len(self.history) else
                                      'Forward is available after you go Back')

    def focus_path(self):
        if self.path.isEnabled():
            self.path.setFocus()
            self.path.selectAll()

    def navigate_history(self, delta):
        index=self.history_index+delta
        if 0<=index<len(self.history):self.browse(self.history[index],history_index=index)

    def enter_selected_folder(self):
        entry=self.selection()
        if entry and entry['kind'] in ('Directory','Link'):self.browse(entry['path'])
        else:self.file_status.setText('Select a folder, then click ↓ Open to enter it.')

    def copy_remote_path(self):
        entry=self.selection()
        QApplication.clipboard().setText(entry['path'] if entry else self.current_dir)
        self.file_status.setText('Remote path copied to clipboard.')

    def make_file_menu(self,position):
        index=self.table.indexAt(position)
        entry=None
        if index.isValid():
            self.table.selectRow(index.row())
            item=self.table.item(index.row(),0)
            entry=item.data(Qt.ItemDataRole.UserRole) if item else None
        else:
            self.table.clearSelection()
            self.table.setCurrentCell(-1,-1)
        active=bool(self.client) and self.file_controls_enabled and not self.file_busy and not self.closing
        usable=bool(entry) and not entry.get('unsupported_name')
        kind=entry['kind'] if usable else None
        menu=QMenu(self)
        def group(name, label):
            child=menu.addMenu(icon(name),label)
            return child
        def action(parent,name,label,slot,enabled):
            item=parent.addAction(icon(name,'#ee9898' if name=='remove' else '#b9c9db'),label,slot)
            item.setEnabled(bool(enabled))
            return item
        regular=usable and entry.get('permissions','').startswith('-') and kind=='File'
        folder=usable and kind=='Directory'
        open_menu=group('view','Open / Edit')
        action(open_menu,'folder','Open folder',self.enter_selected_folder,active and kind in ('Directory','Link'))
        action(open_menu,'view','View file',self.preview.click,active and kind in ('File','Link'))
        action(open_menu,'edit','Edit file…',self.edit_file_button.click,active and kind=='File')
        action(open_menu,'activity','Tail log / follow…',self.show_log,active and regular)
        create_menu=group('new','Create')
        action(create_menu,'new','New file…',self.new_file_button.click,active)
        action(create_menu,'new-folder','New folder…',self.new_folder_button.click,active)
        transfer_menu=group('send','Transfer')
        action(transfer_menu,'download','Download…',self.get.click,active and kind in ('File','Link'))
        action(transfer_menu,'upload','Upload file…',self.put.click,active)
        action(transfer_menu,'send','Send to server (SCP)…',self.send_to_server,active and (regular or folder))
        archive_menu=group('archive','Archive')
        action(archive_menu,'archive','Create ZIP…',lambda:self.archive_selected('zip'),active and (regular or folder))
        action(archive_menu,'extract','Extract ZIP…',lambda:self.archive_selected('unzip'),
               active and regular and entry['name'].lower().endswith('.zip'))
        tools_menu=group('terminal','Tools')
        action(tools_menu,'play','Run shell script…',self.run_shell_script,
               active and regular and entry['name'].lower().endswith('.sh') and self.terminal.online)
        action(tools_menu,'terminal','Open terminal here',self.open_terminal_here,
               active and (not entry or usable) and self.terminal.online)
        action(tools_menu,'hash','SHA-256 checksum…',self.checksum_selected,active and regular)
        action(tools_menu,'info','Properties…',self.properties_selected,active and usable)
        copy_menu=group('copy','Copy')
        action(copy_menu,'copy','Copy path' if usable else 'Copy current folder path',self.copy_path_button.click,
               self.file_controls_enabled and (not entry or usable))
        action(copy_menu,'copy','Copy name',self.copy_remote_name,self.file_controls_enabled and usable)
        manage_menu=group('rename','Manage')
        action(manage_menu,'rename','Rename…',self.rename_selected,active and kind in ('File','Directory','Link'))
        action(manage_menu,'lock','Permissions…',self.permissions_button.click,active and kind in ('File','Directory'))
        manage_menu.addSeparator()
        action(manage_menu,'remove','Delete folder…' if kind=='Directory' else 'Delete file…',self.delete_button.click,
               active and kind in ('File','Link','Directory'))
        menu.addSeparator()
        action(menu,'refresh','Refresh',self.refresh_button.click,active)
        if entry and not usable:
            info=menu.addAction('Non-UTF-8 name: use the SSH terminal')
            info.setEnabled(False)
        return menu

    def file_context_menu(self,position):
        menu=self.make_file_menu(position)
        menu.exec(self.table.viewport().mapToGlobal(position))
        menu.deleteLater()

    def copy_remote_name(self):
        entry=self.selection()
        if entry:
            QApplication.clipboard().setText(entry['name'])
            self.file_status.setText('Remote name copied to clipboard.')

    def archive_selected(self,operation):
        if not self.client or self.file_busy or self.closing:return
        selected=self.selection()
        if not selected:return
        entry=dict(selected)
        client,worker=self.client,self.worker
        if operation=='zip':
            default=entry['name']+'.zip'
            title,label='Create ZIP','Archive name in '+posixpath.dirname(entry['path'])
        else:
            if entry['kind']!='File' or not entry['name'].lower().endswith('.zip'):return
            default=entry['name'][:-4]+'_extracted'
            title,label='Extract ZIP','New destination folder in '+posixpath.dirname(entry['path'])
        name,ok=QInputDialog.getText(self,title,label,text=default)
        if not ok:return
        try:archive_command(entry,operation,name)
        except ValueError as exc:
            QMessageBox.warning(self,'Invalid archive destination',str(exc));return
        if self.client is not client or self.worker is not worker or worker.stop_event.is_set():return
        stop=worker.stop_event
        parent=self.current_dir
        self.file_status.setText(title+' in progress on remote server…')
        def finished(result):
            self.browse(parent)
            self.file_status.setText(result['message'])
        self.task(lambda progress:archive_entry(client,entry,operation,name,stop),finished)

    def properties_selected(self):
        if not self.client or self.file_busy or self.closing:return
        entry=self.selection()
        if not entry:return
        client,stop=self.client,self.worker.stop_event
        def show(data):
            modified=datetime.fromtimestamp(data['mtime']).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z') if data['mtime'] else 'Unknown'
            lines=['Path: '+data['path'],'Permissions: {} ({:04o})'.format(data['permissions'],data['mode']),
                   'Size: {:,} bytes'.format(data['size']),'Modified (local time): '+modified,
                   'Owner UID: '+str(data['uid']),'Group GID: '+str(data['gid'])]
            if 'link_target' in data:lines.append('Link target: '+data['link_target'])
            if data['permissions'].startswith('d'):lines.append('Folder size is metadata size, not recursive contents.')
            box=QMessageBox(self);box.setWindowTitle('Remote properties');box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText('\n'.join(lines));box.exec()
        self.task(lambda progress:file_properties(client,dict(entry),stop),show)

    def checksum_selected(self):
        if not self.client or self.file_busy or self.closing:return
        entry=self.selection()
        if not entry:return
        client,stop=self.client,self.worker.stop_event
        self.file_status.setText('Calculating SHA-256 over SFTP…')
        def show(data):
            box=QMessageBox(self);box.setWindowTitle('SHA-256 checksum');box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(data['path']+'\n\n'+data['sha256'])
            copy=box.addButton('Copy checksum',QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is copy:QApplication.clipboard().setText(data['sha256'])
            self.file_status.setText('SHA-256 complete: '+data['path'])
        self.task(lambda progress:file_checksum(client,dict(entry),stop,progress),show)

    def open_terminal_here(self):
        if not self.client or self.file_busy or self.closing or not self.terminal.online:return
        entry=self.selection()
        path=entry['path'] if entry and entry['kind']=='Directory' else self.current_dir
        try:valid_path(path) if path!='/' else None
        except ValueError as exc:
            QMessageBox.warning(self,'Invalid folder',str(exc));return
        self.terminal_toggle.setChecked(True)
        self.send_bytes(('cd -- '+shlex.quote(path)+'\n').encode('utf-8'))
        self.terminal.setFocus()
        self.file_status.setText('Changed terminal folder. Use this action only when the terminal is at a shell prompt.')

    def run_shell_script(self):
        if not self.client or self.file_busy or self.closing or not self.terminal.online:return
        selected=self.selection()
        if not selected or selected['kind']!='File' or not selected['name'].lower().endswith('.sh'):return
        entry=dict(selected)
        client,worker=self.client,self.worker
        dialog=QDialog(self);dialog.setWindowTitle('Run remote shell script');dialog.resize(650,350)
        layout=QVBoxLayout(dialog)
        label=QLabel('Run '+entry['path']);label.setTextFormat(Qt.TextFormat.PlainText);label.setWordWrap(True);layout.addWidget(label)
        note=QLabel('Runs as the connected SSH user in the script’s folder. Output appears in the existing terminal. '
                    'Ensure it is at a shell prompt. Use Send Ctrl+C to interrupt; script changes are not rolled back.')
        note.setWordWrap(True);layout.addWidget(note)
        form=QFormLayout();interpreter=QComboBox();interpreter.addItems(['bash','sh'])
        arguments=QLineEdit();arguments.setPlaceholderText('Optional arguments; quote values containing spaces')
        form.addRow('Interpreter',interpreter);form.addRow('Arguments',arguments);layout.addLayout(form)
        preview=QPlainTextEdit();preview.setReadOnly(True);preview.setMaximumHeight(100);layout.addWidget(preview)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        run=buttons.button(QDialogButtonBox.StandardButton.Ok);run.setText('Run in terminal')
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        def update():
            try:
                command=shell_command(entry['path'],interpreter.currentText(),arguments.text())
                preview.setPlainText(command);run.setEnabled(True)
            except ValueError as exc:
                preview.setPlainText(str(exc));run.setEnabled(False)
        interpreter.currentTextChanged.connect(update);arguments.textChanged.connect(update);update()
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            dialog.deleteLater();return
        command=shell_command(entry['path'],interpreter.currentText(),arguments.text())
        dialog.deleteLater()
        if self.client is not client or self.worker is not worker or worker.stop_event.is_set():return
        def launch(unused):
            if self.client is not client or self.worker is not worker or not self.terminal.online:return
            self.terminal_toggle.setChecked(True)
            self.send_bytes((command+'\n').encode('utf-8'));self.terminal.setFocus()
            self.file_status.setText('Script command sent to terminal. Refresh files after it finishes.')
        self.task(lambda progress:verify_script(client,entry,worker.stop_event),launch)

    def rename_selected(self):
        if not self.client or self.file_busy or self.closing:return
        entry=self.selection()
        if not entry:return
        entry=dict(entry)
        name,ok=QInputDialog.getText(self,'Rename remote item','New name for '+entry['path'],text=entry['name'])
        if not ok or name==entry['name']:return
        client,stop=self.client,self.worker.stop_event
        parent=self.current_dir
        self.task(lambda progress:rename_entry(client,entry,name,stop),lambda path:self.browse(parent))

    def send_to_server(self):
        if not self.client or self.file_busy or self.closing:return
        entry=self.selection()
        if not entry:return
        dialog=TransferDialog(self,entry)
        dialog.exec()
        if dialog.result_message:self.file_status.setText(dialog.result_message)
        dialog.deleteLater()

    def show_log(self):
        if not self.client or self.file_busy or self.closing:return
        entry=self.selection()
        if not entry or entry['kind']!='File':return
        dialog=LogViewer(self,entry)
        dialog.exec();dialog.deleteLater()

    def new_folder(self):
        if not self.client or self.file_busy or self.closing:return
        parent=self.current_dir
        name,ok=QInputDialog.getText(self,'Create remote folder','Folder name in '+parent)
        if not ok:return
        if not name or name in ('.','..') or '/' in name or any(ord(c)<32 for c in name):
            QMessageBox.warning(self,'Invalid folder name','Enter a single folder name without slashes or control characters.')
            return
        if not self.client or not self.worker:return
        client,stop=self.client,self.worker.stop_event
        self.task(lambda progress:create_directory(client,parent,name,stop),lambda path:self.browse(parent))

    def edit_permissions(self):
        if not self.client or self.file_busy:return
        entry=self.selection()
        if not entry:
            self.file_status.setText('Select a file or folder to change its permissions.');return
        if entry['kind'] not in ('File','Directory'):
            self.file_status.setText('Permissions can be changed on files or folders, not symlinks.');return
        entry=dict(entry)
        client=self.client
        self.task(lambda progress:read_permissions(client,entry),self.show_permissions)

    def show_permissions(self,snapshot):
        dialog=PermissionsDialog(self,snapshot)
        dialog.exec()
        dialog.deleteLater()
        if self.client and not self.closing:self.browse(self.current_dir)

    def delete_selected(self):
        if not self.client or self.file_busy or self.closing:return
        selected=self.selection()
        if not selected:
            self.file_status.setText('Select a file or folder to delete.')
            return
        if selected['kind'] not in ('File','Link','Directory'):
            self.file_status.setText('Select a file, folder or symlink.')
            return
        entry=dict(selected)
        folder=entry['kind']=='Directory'
        question=QMessageBox(self)
        question.setWindowTitle('Delete remote folder' if folder else 'Delete remote file')
        question.setIcon(QMessageBox.Icon.Warning)
        question.setTextFormat(Qt.TextFormat.PlainText)
        suffix='Only the symlink will be removed; its target will be kept.' if entry['kind']=='Link' else 'This permanently removes the file; it cannot be undone from this app.'
        recursive=QCheckBox('Delete all contents, including files and subfolders') if folder else None
        if folder:
            question.setCheckBox(recursive)
            suffix='By default, only an empty folder is deleted. Check Delete all contents to remove a non-empty folder and its contents. This cannot be undone; cancellation or failure may leave partial deletion.'
        question.setText('Delete this remote {}?\n\n'.format('folder' if folder else 'file')+entry['path']+'\n\n'+suffix)
        question.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel)
        question.button(QMessageBox.StandardButton.Yes).setText('Delete folder' if folder else 'Delete file')
        question.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if question.exec()!=QMessageBox.StandardButton.Yes:return
        if not self.client or not self.worker:return
        client,stop=self.client,self.worker.stop_event
        if folder:
            include_contents=recursive.isChecked()
            self.task(lambda progress:delete_directory(client,entry,stop,include_contents,progress),
                      lambda message:self.browse(self.current_dir))
        else:self.task(lambda progress:delete_file(client,entry,stop),lambda message:self.browse(self.current_dir))

    def busy(self):
        return bool((self.worker and self.worker.isRunning()) or any(t.isRunning() for t in self.tasks))

    def connect_session(self):
        if self.reconnect_pending:return
        if self.worker and self.worker.isRunning():return
        if any(t.isRunning() for t in self.tasks):
            self.status.setText('Wait for the previous file operation to finish before reconnecting.')
            return
        mode = self.profile['auth']
        secret = None
        if mode != 'agent':
            label = 'SSH password' if mode=='password' else 'Private key passphrase (leave blank if unencrypted)'
            secret, ok = QInputDialog.getText(self, 'Connect to '+self.profile['name'], label, QLineEdit.EchoMode.Password)
            if not ok:return
        self.pending_secret = secret
        self.start_worker()

    def start_worker(self):
        if self.closing:return
        if self.worker and self.worker.isRunning():
            QTimer.singleShot(100,self.start_worker)
            return
        if self.worker:
            self.worker.deleteLater()
        self.waiting_for_trust = False
        self.status.setText('Connecting…')
        self.set_connection_state('connecting')
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(True)
        w = SSHSession(self.profile,self.pending_secret,self.store.known_hosts,self)
        self.worker = w
        w.ready.connect(lambda c,w=w:self.on_ready(w,c),QUEUED)
        w.data.connect(lambda data,w=w:self.feed_output(w,data),QUEUED)
        w.message.connect(self.show_status,QUEUED)
        w.unknown_host.connect(self.on_unknown,QUEUED)
        w.ended.connect(lambda w=w:self.on_ended(w),QUEUED)
        w.start()

    def show_status(self, text):
        if not self.closing:
            self.status.setText(text);self.status.setToolTip(text)

    def feed_output(self, worker, data):
        try:
            if self.worker is worker and not self.closing:
                self.terminal.feed(data)
        finally:
            worker.acknowledge_output()

    def on_unknown(self, hostname, key):
        if self.closing:return
        self.waiting_for_trust = True
        question = QMessageBox(self)
        question.setWindowTitle('Verify new SSH host key')
        question.setTextFormat(Qt.TextFormat.PlainText)
        question.setText('The SSH key for {} is not known.\n\nType: {}\nFingerprint: {}\n\nVerify this fingerprint with a trusted source. Trust and save it for this application?'.format(hostname,key.get_name(),fingerprint(key)))
        question.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        question.setDefaultButton(QMessageBox.StandardButton.No)
        if question.exec()==QMessageBox.StandardButton.Yes:
            try:
                trust_host(self.store.known_hosts,hostname,key)
                QTimer.singleShot(0,self.start_worker)
                return
            except Exception as exc:
                self.show_status(str(exc))
        self.pending_secret = None
        self.waiting_for_trust = False
        self.connect_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)

    def on_ready(self, worker, client):
        if self.worker is not worker or self.closing:
            worker.stop()
            return
        self.client = client
        self.pending_secret = None
        self.terminal.online = True
        self.interrupt_button.setEnabled(True)
        self.status.setText('Connected · Host key verified · '+self.profile['name'])
        self.set_connection_state('connected')
        self.file_controls(True)
        worker.resize(self.terminal.screen.columns,self.terminal.screen.lines)
        self.browse(self.current_dir)
        self.monitor.update_actions()
        if self.monitor_toggle.isChecked():self.monitor.reload()
        if self.terminal_panel.isVisible():self.terminal.setFocus()

    def on_ended(self, worker):
        if self.worker is not worker:return
        self.client = None
        self.set_connection_state('disconnected')
        self.terminal.online = False
        self.interrupt_button.setEnabled(False)
        self.file_controls(False)
        self.monitor.disconnected()
        self.connect_button.setEnabled(not self.closing and not self.reconnect_pending)
        self.disconnect_button.setEnabled(False)
        if not self.waiting_for_trust:self.pending_secret=None
        if self.status.text().startswith(('Connecting','Connected')):
            self.status.setText('Disconnected')

    def disconnect_session(self,keep_reconnect=False):
        if not keep_reconnect:
            self.reconnect_timer.stop();self.reconnect_pending=False;self.reconnect_button.setEnabled(True)
        self.pending_secret = None
        self.terminal.online = False
        self.interrupt_button.setEnabled(False)
        self.file_controls(False)
        if self.worker:self.worker.stop()
        self.monitor.disconnected()
        self.status.setText('Disconnecting…')
        self.set_connection_state('disconnecting' if self.worker and self.worker.isRunning() else 'disconnected')

    def stop(self):
        self.closing=True
        self.reconnect_timer.stop()
        self.reconnect_pending=False
        self.disconnect_session()

    def send_bytes(self, data):
        if self.worker and self.terminal.online:self.worker.send(data)

    def resize_terminal(self, width, height):
        if self.worker and self.terminal.online:self.worker.resize(width,height)

    def task(self, fn, callback, on_error=None):
        if self.file_busy or not self.client or self.closing:return
        self.file_busy=True
        self.file_controls(False)
        task = Task(fn,self)
        self.tasks.append(task)
        def done(value):
            self.file_busy=False
            if not self.closing:
                self.file_controls(bool(self.client))
                callback(value)
        def failed(message):
            self.file_busy=False
            if not self.closing:
                self.file_controls(bool(self.client))
                self.file_status.setText(message)
                if on_error:on_error(message)
        task.result.connect(done,QUEUED)
        task.failed.connect(failed,QUEUED)
        task.progress.connect(lambda n:self.file_status.setText('Progress: {}%'.format(n)),QUEUED)
        task.finished.connect(lambda t=task:self.cleanup_task(t),QUEUED)
        task.start()

    def cleanup_task(self, task):
        if task in self.tasks:self.tasks.remove(task)
        task.deleteLater()

    def browse(self, path, history_index=None):
        if not self.client or self.file_busy or self.closing:return
        client, stop = self.client,self.worker.stop_event
        self.file_status.setText('Loading directory…')
        self.task(lambda progress:list_directory(client,path,stop),
                  lambda data:self.show_directory(data,history_index))

    def go_to_path(self):
        entered=self.path.text()
        if entered != self.current_dir:
            self.browse(entered)
            return
        entry=self.selection()
        if entry and entry['kind'] in ('Directory','Link'):
            self.browse(entry['path'])
        else:
            self.browse(self.current_dir)

    def show_breadcrumbs(self, path):
        while self.crumb_layout.count():
            item=self.crumb_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
        parts=[('Root /','/')]
        current=''
        for component in path.split('/'):
            if component:
                current+='/'+component
                parts.append((component,current))
        for label,destination in parts:
            button=QPushButton(label)
            button.setProperty('role','crumb')
            button.setFixedHeight(21)
            button.setIcon(icon('folder'))
            button.setIconSize(QSize(12,12))
            button.setToolTip(destination)
            button.clicked.connect(lambda checked=False,p=destination:self.browse(p))
            self.crumb_layout.addWidget(button)
        self.crumb_layout.addStretch()

    def show_directory(self, data, history_index=None):
        self.current_dir=data['path']
        if history_index is not None:
            self.history_index=history_index
            self.history[history_index]=self.current_dir
        elif self.history_index<0 or self.history[self.history_index]!=self.current_dir:
            self.history=self.history[:self.history_index+1]+[self.current_dir]
            self.history=self.history[-200:]
            self.history_index=len(self.history)-1
        self.update_history_buttons()
        self.path.setText(data['path'])
        self.show_breadcrumbs(data['path'])
        self.entries=data['entries']
        self.list_truncated=data['truncated']
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.entries))
        for i,row in enumerate(self.entries):
            stamp=datetime.fromtimestamp(row['mtime']).strftime('%Y-%m-%d %H:%M') if row['mtime'] else '—'
            type_label=file_type_label(row)
            for col,text in enumerate((row['name'],format_bytes(row['size']),stamp,row['permissions'],type_label)):
                item=FileItem(text)
                sort_value=(row['name'].casefold(),row['size'],row['mtime'],row['permissions'],
                            (type_label.casefold(),row['name'].casefold()))[col]
                item.setData(Qt.ItemDataRole.UserRole+1,sort_value)
                item.setToolTip(row['name']+' · '+row['kind']+' · '+row['permissions'])
                if col==1:item.setToolTip('{:,} bytes · Folder size describes directory metadata, not recursive contents.'.format(row['size']) if row['kind']=='Directory' else '{:,} bytes'.format(row['size']))
                if col==0:
                    item.setIcon(entry_icon(row['kind']))
                    item.setData(Qt.ItemDataRole.UserRole,row)
                if col==1:item.setTextAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i,col,item)
        self.table.setSortingEnabled(True)
        self.apply_file_filter()

    def apply_file_filter(self, *args):
        if not hasattr(self,'file_status'):return
        query=self.file_filter.text().casefold()
        visible=0
        for row in range(self.table.rowCount()):
            item=self.table.item(row,0)
            matches=bool(item) and query in item.text().casefold()
            self.table.setRowHidden(row,not matches)
            visible+=int(matches)
        if self.table.currentRow()>=0 and self.table.isRowHidden(self.table.currentRow()):
            self.table.clearSelection()
            self.table.setCurrentCell(-1,-1)
        self.file_status.setText('{} of {} entries{} · Right-click for actions'.format(visible,len(self.entries),
            ' · Limited to first 5,000 entries' if self.list_truncated else ''))
        unsupported=sum(bool(entry.get('unsupported_name')) for entry in self.entries)
        if unsupported:self.file_status.setText(self.file_status.text()+' · {} non-UTF-8 name(s) are display-only; use the SSH terminal for those entries.'.format(unsupported))

    def selection(self):
        row=self.table.currentRow()
        item=self.table.item(row,0) if row>=0 and not self.table.isRowHidden(row) else None
        entry=item.data(Qt.ItemDataRole.UserRole) if item else None
        if entry and entry.get('unsupported_name'):
            self.file_status.setText('This filename is not UTF-8. It is display-only; use the SSH terminal to work with it.')
            return None
        return entry

    def new_file(self):
        if not self.client or self.file_busy:return
        name,ok=QInputDialog.getText(self,'Create remote file','File name in '+self.current_dir)
        if not ok:return
        if not name or name in ('.','..') or '/' in name or '\x00' in name:
            QMessageBox.warning(self,'Invalid file name','Enter a file name without a slash; use the folders to choose its directory.')
            return
        self.show_editor({'path':posixpath.join(self.current_dir,name),'text':'','version':None,'newline':'\n'})

    def edit_selected(self):
        entry=self.selection()
        if not entry:return
        if entry['kind']!='File':
            self.file_status.setText('Select a regular text file to edit. Symlinks and directories are not editable.')
            return
        client=self.client
        self.task(lambda progress:read_editable(client,entry['path']),self.show_editor)

    def show_editor(self, snapshot):
        dialog=RemoteEditor(self,snapshot)
        dialog.exec()
        dialog.deleteLater()
        if self.client and not self.closing:self.browse(self.current_dir)

    def open_item(self, row, column):
        entry=self.selection()
        if not entry:return
        if entry['kind']=='Directory':self.browse(entry['path'])
        elif entry['kind']=='Link':
            self.browse(entry['path'])
        else:self.view_selected()

    def view_selected(self):
        entry=self.selection()
        if not entry or entry['kind']=='Directory':return
        client=self.client
        self.task(lambda p:preview_file(client,entry['path']),self.show_preview)

    def show_preview(self,data):
        dialog=QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle('Read-only preview · '+data['path'])
        dialog.resize(850,600)
        layout=QVBoxLayout(dialog)
        label=QLabel('First 256 KB only; file was truncated.' if data['truncated'] else 'Read-only UTF-8 preview; the remote file is unchanged.')
        layout.addWidget(label)
        text=QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(data['text'])
        layout.addWidget(text)
        dialog.show()

    def download_selected(self):
        entry=self.selection()
        if not entry or entry['kind']=='Directory':return
        local,_=QFileDialog.getSaveFileName(self,'Download file',str(Path.home()/'Downloads'/entry['name']))
        if local:
            client,stop=self.client,self.worker.stop_event
            self.task(lambda p:download(client,entry['path'],local,stop,p),self.file_status.setText)

    def upload_file(self):
        local,_=QFileDialog.getOpenFileName(self,'Upload file')
        if not local:return
        remote=posixpath.join(self.current_dir,Path(local).name)
        box=QMessageBox(self)
        box.setWindowTitle('Upload file')
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText('Upload {} to {}?\nAn existing destination file will be replaced using atomic rename if supported.'.format(local,remote))
        box.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec()==QMessageBox.StandardButton.Yes:
            client,stop=self.client,self.worker.stop_event
            self.task(lambda p:upload(client,local,remote,stop,p),self.file_status.setText)


class Window(QMainWindow):
    def __init__(self, store=None):
        super().__init__()
        self.store=store or Store()
        self.setWindowTitle('SSH Workbench · Terminal & Files')
        self.setWindowIcon(icon('terminal','#66b9ff'))
        self.resize(1360,860)
        self.profiles=self.store.load()
        self.groups=self.store.load_groups()
        self.retired=[]
        self.shutting_down=False
        root=QWidget()
        layout=QVBoxLayout(root)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(0)
        toolbar=QHBoxLayout()
        toolbar.setContentsMargins(12,8,12,8)
        title=QLabel('SSH WORKBENCH')
        brand=QLabel()
        brand.setPixmap(icon('terminal','#66b9ff').pixmap(24,24))
        toolbar.addWidget(brand)
        title.setObjectName('appTitle')
        title.setMaximumHeight(54)
        toolbar.addWidget(title)
        toolbar.addStretch()
        self.connections_button=QPushButton('Connections')
        self.connections_button.setCheckable(True)
        self.connections_button.setChecked(True)
        self.connections_button.setToolTip('Show or hide saved connections')
        toolbar.addWidget(self.connections_button)
        layout.addLayout(toolbar)
        split=QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(2)
        sidebar=QWidget()
        self.sidebar=sidebar
        sidebar.setObjectName('connectionsPanel')
        sidebar.setMinimumWidth(220)
        self.connections_button.toggled.connect(sidebar.setVisible)
        side=QVBoxLayout(sidebar)
        heading=QLabel('SAVED CONNECTIONS')
        heading.setObjectName('panelTitle')
        side.addWidget(heading)
        self.search=QLineEdit()
        self.search.setPlaceholderText('Filter profiles…')
        self.search.textChanged.connect(self.refresh_profiles)
        side.addWidget(self.search)
        self.list=QTreeWidget()
        self.list.setHeaderHidden(True)
        self.list.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.list.setIndentation(16)
        self.list.setIconSize(QSize(14,14))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.connection_menu)
        self.rename_shortcut=QShortcut(QKeySequence('F2'),self.list)
        self.rename_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.rename_shortcut.activated.connect(self.rename_group)
        self.list.itemDoubleClicked.connect(lambda item,column:self.open_profile() if item.data(0,Qt.ItemDataRole.UserRole) else None)
        side.addWidget(self.list)
        self.connection_tools=QWidget()
        connection_row=QHBoxLayout(self.connection_tools)
        connection_row.setContentsMargins(0,2,0,2)
        connection_row.setSpacing(3)
        for text,fn,symbol in [('Add connection',self.add_profile,'plus'),('Edit selected',self.edit_profile,'edit'),
                               ('Rename group',self.rename_group,'rename'),('Move to group…',self.move_to_group,'move'),
                               ('Open session',self.open_profile,'connect'),('Remove profile',self.remove_profile,'remove')]:
            b=QToolButton()
            b.setText(text);b.setToolTip(text);b.setAccessibleName(text)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            b.setFixedSize(30,30)
            b.setIcon(icon(symbol,'#ffffff' if text in ('Add connection','Open session') else '#b9c9db'))
            b.setIconSize(QSize(14,14))
            b.clicked.connect(fn)
            connection_row.addWidget(b)
        connection_row.addStretch()
        side.addWidget(self.connection_tools)
        helptext=QLabel('Profiles store no passwords.\nDouble-click to open a session.\nSSH runs directly from this computer.')
        helptext.setObjectName('hintText')
        helptext.setWordWrap(True)
        side.addWidget(helptext)
        self.tabs=QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        welcome=QLabel('Your server workspace\n\nOrganise connections into groups. Browse files. Open a terminal when you need it.\n\nStart with Add connection, or open a saved server from the sidebar.')
        welcome.setWordWrap(True)
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome.setObjectName('welcomeText')
        self.tabs.addTab(welcome,'Welcome')
        self.decorate_tab(0)
        split.addWidget(sidebar)
        split.addWidget(self.tabs)
        split.setSizes([250,1110])
        split.setStretchFactor(0,0)
        split.setStretchFactor(1,1)
        layout.addWidget(split,1)
        self.setCentralWidget(root)
        self.statusBar().showMessage('Profiles: '+str(self.store.file))
        self.refresh_profiles()
        self.cleanup=QTimer(self)
        self.cleanup.timeout.connect(self.sweep)
        self.cleanup.start(150)

    def refresh_profiles(self):
        text=self.search.text().casefold()
        selected={item.data(0,Qt.ItemDataRole.UserRole) for item in self.list.selectedItems()}
        expanded={}
        def remember(item):
            path=item.data(0,Qt.ItemDataRole.UserRole+1)
            if path:expanded[path]=item.isExpanded()
            for i in range(item.childCount()):remember(item.child(i))
        for i in range(self.list.topLevelItemCount()):remember(self.list.topLevelItem(i))
        self.list.clear()
        self.groups=complete_groups(self.groups,self.profiles)
        visible=[p for p in sorted(self.profiles,key=lambda p:p['name'].casefold()) if text in ' '.join((p['name'],p['host'],p['username'],p['group'])).casefold()]
        paths=set(complete_groups([],visible)) if text else set(self.groups)
        if text:
            for path in self.groups:
                if text in path.casefold():paths.update(complete_groups([path],[]))
        nodes={}
        for path in self.groups:
            if path not in paths:continue
            parent_path=group_parent(path)
            parent=nodes.get(parent_path,self.list)
            count=sum(p['group']==path or p['group'].startswith(path+' / ') for p in visible)
            item=QTreeWidgetItem(parent,[path.rsplit(' / ',1)[-1]+'  ·  '+str(count)])
            item.setIcon(0,icon('folder','#66b9ff'));item.setData(0,Qt.ItemDataRole.UserRole+1,path)
            item.setToolTip(0,path);font=item.font(0);font.setBold(True);item.setFont(0,font)
            item.setForeground(0,QColor('#8ebff2'));item.setExpanded(bool(text) or expanded.get(path,True));nodes[path]=item
        for p in visible:
            item=QTreeWidgetItem(nodes[p['group']],[p['name']+'\n'+p['username']+'@'+p['host']])
            item.setData(0,Qt.ItemDataRole.UserRole,p['id'])
            item.setToolTip(0,p['name']+'\n'+p['username']+'@'+p['host']+':'+str(p['port'])+'\nGroup: '+p['group'])
            if p['id'] in selected:item.setSelected(True)
        self.update_connection_icons()

    def update_connection_icons(self):
        states={}
        for i in range(self.tabs.count()):
            page=self.tabs.widget(i)
            if isinstance(page,SessionPage):
                state=page.connection_state
                priority={'connected':3,'connecting':2,'disconnecting':1,'disconnected':0}
                existing=states.get(page.profile['id'],'disconnected')
                states[page.profile['id']]=state if priority[state]>=priority[existing] else existing
                self.tabs.setTabIcon(i,icon('status',{'connected':'#63ce91','connecting':'#e5b85b','disconnecting':'#e5b85b','disconnected':'#ef7979'}[state]))
        def visit(item):
            identity=item.data(0,Qt.ItemDataRole.UserRole)
            if identity:
                state=states.get(identity,'disconnected')
                item.setIcon(0,icon('server',{'connected':'#63ce91','connecting':'#e5b85b','disconnecting':'#e5b85b','disconnected':'#ef7979'}[state]))
                item.setToolTip(0,item.toolTip(0).split('\nSSH state:')[0]+'\nSSH state: '+state)
            for i in range(item.childCount()):visit(item.child(i))
        for i in range(self.list.topLevelItemCount()):visit(self.list.topLevelItem(i))

    def selected_profile(self):
        item=self.list.currentItem()
        return next((p for p in self.profiles if item and p['id']==item.data(0,Qt.ItemDataRole.UserRole)),None)

    def selected_group(self):
        item=self.list.currentItem()
        if not item:return None
        if item.data(0,Qt.ItemDataRole.UserRole):item=item.parent()
        return item.data(0,Qt.ItemDataRole.UserRole+1) if item else None

    def commit_group_rename(self,old,name):
        if old not in self.groups:raise ValueError('Select a saved group.')
        if not isinstance(name,str) or not name.strip() or '/' in name:raise ValueError('Enter one group name without /.')
        parent=group_parent(old);target=group_path((parent+' / ' if parent else '')+name)
        if target==old:return
        if any(path==target or path.startswith(target+' / ') for path in self.groups):
            raise ValueError('That group already exists. Use Move to group to merge connections, or choose another name.')
        def remap(path):return target+path[len(old):] if path==old or path.startswith(old+' / ') else path
        updated=[validate(dict(p,group=remap(p['group']))) for p in self.profiles]
        groups=[remap(path) for path in self.groups]
        self.store.save(updated,groups)
        self.profiles,self.groups=updated,groups
        self.refresh_profiles()

    def new_group(self,subgroup=False):
        parent=self.selected_group() if subgroup else ''
        if subgroup and not parent:return
        name,ok=QInputDialog.getText(self,'New sub-group' if subgroup else 'New group','Name'+(' under '+parent if parent else '')+':')
        if not ok:return
        try:
            if not name.strip() or '/' in name:raise ValueError('Enter one group name without /.')
            path=group_path((parent+' / ' if parent else '')+name)
            if path in self.groups:raise ValueError('That group already exists.')
            groups=complete_groups(self.groups+[path],self.profiles)
            self.store.save(self.profiles,groups);self.groups=groups;self.refresh_profiles()
        except Exception as exc:QMessageBox.warning(self,'Cannot create group',str(exc))

    def reorder_group(self,delta):
        path=self.selected_group()
        if not path:return
        siblings=[g for g in self.groups if group_parent(g)==group_parent(path)]
        index=siblings.index(path);destination=index+delta
        if not 0<=destination<len(siblings):return
        groups=list(self.groups);a,b=groups.index(path),groups.index(siblings[destination]);groups[a],groups[b]=groups[b],groups[a]
        try:self.store.save(self.profiles,groups)
        except Exception as exc:QMessageBox.warning(self,'Cannot reorder group',str(exc));return
        self.groups=groups;self.refresh_profiles()
        def select(item):
            if item.data(0,Qt.ItemDataRole.UserRole+1)==path:self.list.setCurrentItem(item)
            for i in range(item.childCount()):select(item.child(i))
        for i in range(self.list.topLevelItemCount()):select(self.list.topLevelItem(i))

    def remove_empty_group(self):
        path=self.selected_group()
        if not path:return
        if any(p['group']==path or p['group'].startswith(path+' / ') for p in self.profiles):
            QMessageBox.information(self,'Group contains connections','Move or remove its saved connections first.');return
        if QMessageBox.question(self,'Remove empty group','Remove '+path+' and its empty sub-groups?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        groups=[g for g in self.groups if g!=path and not g.startswith(path+' / ')]
        try:self.store.save(self.profiles,groups)
        except Exception as exc:QMessageBox.warning(self,'Cannot remove group',str(exc));return
        self.groups=groups;self.refresh_profiles()

    def rename_group(self):
        group=self.selected_group()
        if group is None:
            self.statusBar().showMessage('Select a group or a connection within the group to rename it.')
            return
        name,ok=QInputDialog.getText(self,'Rename connection group','Group name:',text=group.rsplit(' / ',1)[-1])
        if not ok:return
        try:self.commit_group_rename(group,name)
        except Exception as exc:QMessageBox.warning(self,'Cannot rename group',str(exc))

    def connection_menu(self, position):
        item=self.list.itemAt(position)
        if item:self.list.setCurrentItem(item)
        menu=QMenu(self)
        menu.addAction(icon('new-folder'),'New group…',lambda:self.new_group(False))
        if item:
            menu.addAction(icon('new-folder'),'New sub-group…',lambda:self.new_group(True))
            menu.addAction(icon('rename'),'Rename group…',self.rename_group)
            menu.addAction(icon('up'),'Move group up',lambda:self.reorder_group(-1))
            menu.addAction(icon('down'),'Move group down',lambda:self.reorder_group(1))
            menu.addAction(icon('remove'),'Remove empty group…',self.remove_empty_group)
            if item.data(0,Qt.ItemDataRole.UserRole):
                menu.addSeparator()
                menu.addAction(icon('connect'),'Open session',self.open_profile)
                menu.addAction(icon('edit'),'Edit connection…',self.edit_profile)
                menu.addAction(icon('move'),'Move to group…',self.move_to_group)
        menu.exec(self.list.viewport().mapToGlobal(position));menu.deleteLater()

    def move_to_group(self):
        selected={item.data(0,Qt.ItemDataRole.UserRole) for item in self.list.selectedItems()}
        profiles=[p for p in self.profiles if p['id'] in selected]
        if not profiles:return
        groups=sorted({'Dev','Cert','Prod','Ungrouped'}|set(self.groups),key=str.casefold)
        group,ok=QInputDialog.getItem(self,'Move connections to group','Select a group or type a new name:',groups,0,True)
        if not ok:return
        try:
            replacements={p['id']:validate(dict(p,group=group)) for p in profiles}
            updated=[replacements.get(p['id'],p) for p in self.profiles]
            self.store.save(updated)
            self.profiles=updated
            self.refresh_profiles()
        except Exception as exc:QMessageBox.warning(self,'Cannot update group',str(exc))

    def decorate_tab(self,index):
        page=self.tabs.widget(index)
        button=QToolButton()
        button.setObjectName('tabClose')
        button.setIcon(icon('close'))
        button.setIconSize(QSize(12,12))
        button.setToolTip('Close tab')
        button.setAccessibleName('Close tab')
        button.setFixedSize(20,20)
        button.clicked.connect(lambda:self.close_tab(self.tabs.indexOf(page)))
        self.tabs.tabBar().setTabButton(index,QTabBar.ButtonPosition.LeftSide,None)
        self.tabs.tabBar().setTabButton(index,QTabBar.ButtonPosition.RightSide,button)

    def add_profile(self):
        dialog=ProfileDialog(parent=self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.profiles.append(dialog.value)
            self.store.save(self.profiles)
            self.refresh_profiles()

    def edit_profile(self):
        p=self.selected_profile()
        if not p:return
        dialog=ProfileDialog(p,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.profiles[self.profiles.index(p)]=dialog.value
            self.store.save(self.profiles)
            self.refresh_profiles()

    def remove_profile(self):
        p=self.selected_profile()
        if p and QMessageBox.question(self,'Remove profile','Remove the selected saved connection profile?')==QMessageBox.StandardButton.Yes:
            self.profiles.remove(p)
            self.store.save(self.profiles)
            self.refresh_profiles()

    def open_profile(self):
        p=self.selected_profile()
        if not p:return
        page=SessionPage(p,self.store,self)
        index=self.tabs.addTab(page,p['name'])
        self.tabs.setTabToolTip(index,p.get('group','Ungrouped')+' · '+p['username']+'@'+p['host'])
        self.decorate_tab(index)
        page.connection_changed.connect(lambda state:self.update_connection_icons())
        self.update_connection_icons()
        self.tabs.setCurrentIndex(index)

    def close_tab(self,index):
        page=self.tabs.widget(index)
        if isinstance(page,SessionPage):
            if page.busy() and QMessageBox.question(self,'Close session','Disconnect this session and close its tab?')!=QMessageBox.StandardButton.Yes:return
            page.stop()
            self.retired.append(page)
        else:page.deleteLater()
        self.tabs.removeTab(index)
        self.update_connection_icons()

    def sweep(self):
        for p in list(self.retired):
            if not p.busy():
                self.retired.remove(p)
                p.deleteLater()
        if self.shutting_down and not self.retired:
            self.close()

    def closeEvent(self,event):
        if self.shutting_down:
            if self.retired:event.ignore()
            else:event.accept()
            return
        pages=[self.tabs.widget(i) for i in range(self.tabs.count()) if isinstance(self.tabs.widget(i),SessionPage)]
        if any(p.busy() for p in pages+self.retired):
            if QMessageBox.question(self,'Exit SSH Workbench','Disconnect open sessions and close the application?')!=QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        for p in pages:
            p.stop()
            self.retired.append(p)
        self.shutting_down=True
        self.centralWidget().setEnabled(False)
        self.statusBar().showMessage('Closing SSH sessions and transfers…')
        if self.retired:event.ignore()
        else:event.accept()


def main():
    app=QApplication(sys.argv)
    app.setApplicationName('SSH Workbench')
    apply_theme(app)
    try:
        window=Window()
    except Exception as exc:
        QMessageBox.critical(None,'Cannot load SSH Workbench',str(exc))
        return 1
    window.show()
    return app.exec()


if __name__=='__main__':
    sys.exit(main())

"""Remote text editing dialog; all network work stays in the session's worker tasks."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout

from backend import save_text


class RemoteEditor(QDialog):
    def __init__(self, session, snapshot):
        super().__init__(session)
        self.session, self.snapshot = session, snapshot
        self.saving = False
        self.setWindowTitle('Edit remote file · ' + snapshot['path'])
        self.resize(930, 670)
        layout = QVBoxLayout(self)
        label = QLabel(snapshot['path'])
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.text = QPlainTextEdit()
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setPlainText(snapshot['text'])
        self.text.document().setModified(False)
        layout.addWidget(self.text, 1)
        self.status = QLabel('New file · UTF-8 · Save creates it on the server.' if snapshot['version'] is None
                             else 'UTF-8 · Changes are saved only when you click Save.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch()
        self.save_button = QPushButton('Save to server')
        self.save_button.setProperty('role', 'primary')
        self.save_button.clicked.connect(self.save)
        self.close_button = QPushButton('Close')
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.save_button)
        row.addWidget(self.close_button)
        layout.addLayout(row)

    def save(self):
        session = self.session
        if not session.client or not session.worker or session.worker.stop_event.is_set():
            self.status.setText('Session disconnected. Copy your changes before closing.')
            return
        if session.file_busy:
            self.status.setText('Wait for the current file operation to finish.')
            return
        client, stop = session.client, session.worker.stop_event
        text = self.text.toPlainText()
        snapshot = dict(self.snapshot)
        self.saving = True
        self.text.setReadOnly(True)
        self.save_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.status.setText('Saving…')
        def finished(saved):
            self.snapshot = saved
            self.text.document().setModified(False)
            self.reset_controls()
            self.status.setText('Saved to server.')
        def failed(message):
            self.reset_controls()
            self.status.setText(message)
        session.task(lambda progress: save_text(client, snapshot['path'], text, snapshot['version'],
                                                stop, snapshot.get('newline', '\n')), finished, failed)

    def reset_controls(self):
        self.saving = False
        self.text.setReadOnly(False)
        self.save_button.setEnabled(True)
        self.close_button.setEnabled(True)

    def reject(self):
        if self.can_close():super().reject()

    def can_close(self):
        if self.saving:
            return False
        if self.text.document().isModified():
            answer = QMessageBox.question(self, 'Unsaved changes', 'Discard unsaved edits and close?',
                        QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return False
        return True

    def closeEvent(self, event):
        if self.can_close():
            event.accept()
            super().reject()
        else:event.ignore()

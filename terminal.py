"""Basic monochrome VT100 terminal with bounded scrollback."""
import pyte
from theme import TERMINAL_BACKGROUND, TERMINAL_TEXT
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFontDatabase, QKeySequence, QTextCursor
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QPlainTextEdit


class Terminal(QPlainTextEdit):
    send_bytes = Signal(bytes)
    dimensions = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setStyleSheet('QPlainTextEdit {background:'+TERMINAL_BACKGROUND+';color:'+TERMINAL_TEXT+';font-size:15px;border:0;border-radius:0;padding:12px;selection-background-color:#24415d;selection-color:#ffffff;}')
        self.screen = pyte.HistoryScreen(110, 30, history=1000)
        self.stream = pyte.ByteStream(self.screen)
        self.screen.write_process_input = lambda s: self.send_bytes.emit(s.encode())
        self.dirty = False
        self.online = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.paint_screen)
        self.timer.start(50)

    def feed(self, data):
        self.stream.feed(data)
        self.dirty = True

    def paint_screen(self):
        if not self.dirty:
            return
        self.dirty = False
        bar = self.verticalScrollBar()
        at_bottom, old = bar.value() >= bar.maximum() - 3, bar.value()
        history = [''.join(line[x].data for x in range(self.screen.columns)).rstrip() for line in self.screen.history.top]
        lines = history + self.screen.display
        self.setPlainText('\n'.join(lines))
        cursor = self.textCursor()
        row = len(history) + self.screen.cursor.y
        block = self.document().findBlockByNumber(row)
        cursor.setPosition(block.position() + min(self.screen.cursor.x, max(0, block.length() - 1)))
        self.setTextCursor(cursor)
        bar.setValue(bar.maximum() if at_bottom else old)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'screen'):
            return
        fm = self.fontMetrics()
        columns = max(30, min(240, int((self.viewport().width() - 24) / max(1, fm.horizontalAdvance('M')))))
        rows = max(8, min(80, int((self.viewport().height() - 24) / max(1, fm.lineSpacing()))))
        self.screen.resize(lines=rows, columns=columns)
        self.dimensions.emit(columns, rows)
        self.dirty = True

    def paste_remote(self):
        if not self.online:
            return
        text = QApplication.clipboard().text()
        if len(text.encode()) > 200000:
            QMessageBox.information(self, 'Paste limit', 'Paste less than 200 KB at a time.')
            return
        if '\n' in text or '\r' in text:
            if QMessageBox.question(self, 'Paste multiple lines?', 'This paste may execute several remote commands. Send it to the active session?',
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self.send_bytes.emit(text.replace('\r\n', '\n').replace('\n', '\r').encode())

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy) and self.textCursor().hasSelection():
            self.copy()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_remote()
            return
        if not self.online:
            return
        keys = {Qt.Key.Key_Return:b'\r', Qt.Key.Key_Enter:b'\r', Qt.Key.Key_Backspace:b'\x7f',
                Qt.Key.Key_Tab:b'\t', Qt.Key.Key_Escape:b'\x1b', Qt.Key.Key_Up:b'\x1b[A',
                Qt.Key.Key_Down:b'\x1b[B', Qt.Key.Key_Right:b'\x1b[C', Qt.Key.Key_Left:b'\x1b[D',
                Qt.Key.Key_Home:b'\x1b[H', Qt.Key.Key_End:b'\x1b[F', Qt.Key.Key_Delete:b'\x1b[3~',
                Qt.Key.Key_PageUp:b'\x1b[5~', Qt.Key.Key_PageDown:b'\x1b[6~'}
        mods = event.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier) and Qt.Key.Key_A <= event.key() <= Qt.Key.Key_Z:
            data = bytes([event.key() - Qt.Key.Key_A + 1])
        else:
            data = keys.get(event.key(), event.text().encode() if event.text() else b'')
            if mods & Qt.KeyboardModifier.AltModifier and data:
                data = b'\x1b' + data
        if data:
            self.send_bytes.emit(data)

    def inputMethodEvent(self, event):
        if self.online and event.commitString():
            self.send_bytes.emit(event.commitString().encode())
        event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction('Copy selection', self.copy).setEnabled(self.textCursor().hasSelection())
        menu.addAction('Paste to remote shell', self.paste_remote).setEnabled(self.online)
        menu.exec(event.globalPos())

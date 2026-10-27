import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox, QLineEdit, QComboBox
from file_actions import (archive_command, archive_entry, ARCHIVE_SCRIPT, file_checksum,
                          file_properties, shell_command, verify_script, valid_name, valid_path)
from test_file_manager import DiskSFTP
import test_context_menu as context


def entry(path):
    a = os.lstat(path)
    return dict(path=str(path), name=Path(path).name, size=a.st_size, mtime=int(a.st_mtime),
                permissions=stat.filemode(a.st_mode),
                kind='Directory' if stat.S_ISDIR(a.st_mode) else 'Link' if stat.S_ISLNK(a.st_mode) else 'File')


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_archive(self, source, operation, name, script=None):
        command = shlex.split(archive_command(entry(source), operation, name))
        command[0] = sys.executable
        if script is not None:
            command[2] = script
        return subprocess.run(command, capture_output=True, text=True, timeout=10)

    def test_zip_and_extract_folder_including_hidden_empty_and_executable_files(self):
        source = self.root / 'Project space'
        source.mkdir()
        (source / '.hidden').write_text('hidden')
        (source / 'empty').mkdir()
        (source / 'script.sh').write_text('echo hello\n')
        os.chmod(source / 'script.sh', 0o755)
        result = self.run_archive(source, 'zip', 'project.zip')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['path'], str(self.root / 'project.zip'))
        result = self.run_archive(self.root / 'project.zip', 'unzip', 'output')
        self.assertEqual(result.returncode, 0, result.stderr)
        out = self.root / 'output' / 'Project space'
        self.assertEqual((out / '.hidden').read_text(), 'hidden')
        self.assertTrue((out / 'empty').is_dir())
        self.assertEqual((out / 'script.sh').stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.root / 'output').stat().st_mode & 0o777, 0o700)
        self.assertEqual(list(self.root.glob('.ssh-workbench-zip-*')), [])

    def test_file_zip_handles_shell_metacharacters_without_executing_them(self):
        source = self.root / "data $(touch INJECTED);' file.txt"
        source.write_text('data')
        result = self.run_archive(source, 'zip', 'safe.zip')
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.root / 'safe.zip') as archive:
            self.assertEqual(archive.read(source.name), b'data')
        self.assertFalse((self.root / 'INJECTED').exists())

    def test_existing_file_or_folder_is_never_overwritten(self):
        source = self.root / 'input.txt'
        source.write_text('new')
        (self.root / 'keep.zip').write_text('keep')
        result = self.run_archive(source, 'zip', 'keep.zip')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'keep.zip').read_text(), 'keep')
        self.assertEqual(self.run_archive(source, 'zip', 'new.zip').returncode, 0)
        (self.root / 'existing').mkdir()
        result = self.run_archive(self.root / 'new.zip', 'unzip', 'existing')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list((self.root / 'existing').iterdir()), [])

    def test_zip_rejects_links_instead_of_following_targets(self):
        source = self.root / 'folder'; source.mkdir()
        (self.root / 'secret').write_text('secret')
        (source / 'link').symlink_to(self.root / 'secret')
        result = self.run_archive(source, 'zip', 'unsafe.zip')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'unsafe.zip').exists())
        self.assertEqual(list(self.root.glob('.ssh-workbench-zip-*')), [])

    def test_zip_rejects_symlink_directories_and_special_devices(self):
        source = self.root / 'folder'; source.mkdir()
        (source / 'linked-dir').symlink_to(self.root, target_is_directory=True)
        self.assertNotEqual(self.run_archive(source, 'zip', 'link.zip').returncode, 0)
        (source / 'linked-dir').unlink()
        os.mkfifo(source / 'pipe')
        self.assertNotEqual(self.run_archive(source, 'zip', 'pipe.zip').returncode, 0)

    def test_rejects_traversal_absolute_windows_and_duplicate_archive_names(self):
        for name in ('../escaped', '/tmp/escaped', 'folder/../../escaped', 'C:/escaped', 'a\\..\\escaped', './bad'):
            with self.subTest(name=name):
                archive = self.root / 'bad.zip'
                with zipfile.ZipFile(archive, 'w') as z:
                    z.writestr(name, 'bad')
                result = self.run_archive(archive, 'unzip', 'output')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / 'output').exists())
        with zipfile.ZipFile(self.root / 'bad.zip', 'w') as z:
            z.writestr('same', 'one')
            z.writestr('same', 'two')
        self.assertNotEqual(self.run_archive(self.root / 'bad.zip', 'unzip', 'output').returncode, 0)

    def test_extraction_rejects_links_and_removes_special_permission_bits(self):
        archive = self.root / 'link.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            info = zipfile.ZipInfo('link'); info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, '/etc/passwd')
        self.assertNotEqual(self.run_archive(archive, 'unzip', 'output').returncode, 0)
        with zipfile.ZipFile(archive, 'w') as z:
            info = zipfile.ZipInfo('program'); info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o6755) << 16
            z.writestr(info, 'data')
        self.assertEqual(self.run_archive(archive, 'unzip', 'output').returncode, 0)
        self.assertEqual((self.root / 'output/program').stat().st_mode & 0o7777, 0o755)

    def test_invalid_zip_and_limits_fail_without_publishing_an_archive(self):
        source = self.root / 'file.txt'; source.write_text('larger than five bytes')
        script = ARCHIVE_SCRIPT.replace('MAX_BYTES = 10 * 1024 * 1024 * 1024', 'MAX_BYTES = 5')
        result = self.run_archive(source, 'zip', 'too-large.zip', script)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'too-large.zip').exists())
        invalid = self.root / 'invalid.zip'; invalid.write_text('not a zip')
        self.assertNotEqual(self.run_archive(invalid, 'unzip', 'output').returncode, 0)
        with zipfile.ZipFile(invalid, 'w') as z:z.writestr('file', 'large data')
        self.assertNotEqual(self.run_archive(invalid, 'unzip', 'output', script).returncode, 0)
        self.assertFalse((self.root / 'output').exists())

    def test_stale_selection_is_rejected(self):
        source = self.root / 'file'; source.write_text('before')
        command = shlex.split(archive_command(entry(source), 'zip', 'out.zip'))
        source.write_text('changed length')
        command[0] = sys.executable
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('changed', result.stderr)
        self.assertFalse((self.root / 'out.zip').exists())

    def test_request_validation_and_disconnect(self):
        for name in ('', '.', '..', '../escape', 'x/y', 'bad\nname'):
            with self.assertRaises(ValueError):valid_name(name)
        for path in ('relative', '/', '/a/../b', '/bad\nname'):
            with self.assertRaises(ValueError):valid_path(path)
        source = self.root / 'file'; source.touch()
        with self.assertRaises(ValueError):archive_command(entry(source), 'zip', 'no-extension')
        with patch('file_actions.execute', return_value='{"path":"/tmp/x","message":"created"}') as execute:
            self.assertEqual(archive_entry(None, entry(source), 'zip', 'x.zip', threading.Event())['path'], '/tmp/x')
            self.assertEqual(execute.call_args.kwargs['timeout'], 600)
        stop = threading.Event(); stop.set()
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            archive_entry(MagicMock(), entry(source), 'zip', 'x.zip', stop)


class UtilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / 'job.sh'; self.file.write_text('echo hello\n')
        self.client = MagicMock(); self.client.open_sftp.return_value = DiskSFTP()
        self.stop = threading.Event()

    def test_checksum_and_fresh_properties(self):
        progress = []
        result = file_checksum(self.client, entry(self.file), self.stop, progress.append)
        self.assertEqual(result['sha256'], hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertEqual(progress[-1], 100)
        props = file_properties(self.client, entry(self.file), self.stop)
        self.assertEqual(props['size'], self.file.stat().st_size)
        self.assertEqual(props['uid'], os.getuid())

    def test_checksum_refuses_changed_file_links_and_cancel(self):
        original = entry(self.file); self.file.write_text('changed length')
        with self.assertRaises(ValueError):file_checksum(self.client, original, self.stop, lambda n:None)
        target = self.file.with_name('link'); target.symlink_to(self.file)
        with self.assertRaises(ValueError):file_checksum(self.client, entry(target), self.stop, lambda n:None)
        self.stop.set()
        with self.assertRaises(RuntimeError):file_checksum(self.client, entry(self.file), self.stop, lambda n:None)

    def test_script_command_quotes_every_argument_and_preserves_working_directory(self):
        path = "/tmp/space ' folder/job $(touch bad).sh"
        cmd = shell_command(path, 'bash', "'argument with spaces' '$(touch injected)' '; echo no'")
        tokens = shlex.split(cmd)
        self.assertEqual(tokens[:5], ['cd', '--', "/tmp/space ' folder", '&&', 'bash'])
        self.assertEqual(tokens[5:], ['--', path, 'argument with spaces', '$(touch injected)', '; echo no'])
        for args in ('bad\ncommand', "'unterminated"):
            with self.assertRaises(ValueError):shell_command('/tmp/a.sh', 'sh', args)
        with self.assertRaises(ValueError):shell_command('/tmp/a.txt', 'sh')
        with self.assertRaises(ValueError):shell_command('/tmp/a.sh', 'bash -c')

    def test_verify_script_refuses_stale_or_nonregular_file(self):
        self.assertEqual(verify_script(self.client, entry(self.file), self.stop)['name'], 'job.sh')
        original = entry(self.file); self.file.write_text('changed length')
        with self.assertRaises(ValueError):verify_script(self.client, original, self.stop)


class FileActionsMenuTests(unittest.TestCase):
    def setUp(self):
        context.ContextMenuTests.setUp(self)
        self.page.worker.resize = MagicMock()
    tearDown = context.ContextMenuTests.tearDown
    menu = context.ContextMenuTests.menu
    point_for = context.ContextMenuTests.point_for

    def test_grouped_menu_contains_existing_and_new_features(self):
        menu = self.page.make_file_menu(self.point_for('a.log')); self.menus.append(menu)
        self.assertEqual([a.text() for a in menu.actions() if a.menu()],
                         ['Open / Edit', 'Create', 'Transfer', 'Archive', 'Tools', 'Copy', 'Manage'])
        actions = self.menu(self.point_for('a.log'))
        for label in ('Create ZIP…', 'Properties…', 'SHA-256 checksum…', 'Copy name'):
            self.assertTrue(actions[label].isEnabled(), label)
        self.assertFalse(actions['Extract ZIP…'].isEnabled())
        self.assertFalse(actions['Run shell script…'].isEnabled())
        actions['Copy name'].trigger()
        self.assertEqual(QApplication.clipboard().text(), 'a.log')

    def test_archive_and_script_actions_are_type_specific(self):
        self.page.terminal.online = True
        for name,enabled in [('job.sh','Run shell script…'), ('data.ZIP','Extract ZIP…')]:
            row = dict(self.rows[0], name=name, path='/var/log/'+name)
            self.page.show_directory(dict(path='/var/log', entries=[row], truncated=False))
            actions = self.menu(self.point_for(name))
            self.assertTrue(actions[enabled].isEnabled())
        actions = self.menu(QPoint(5, self.page.table.viewport().height()-3))
        self.assertTrue(actions['Open terminal here'].isEnabled())
        for label in ('Create ZIP…', 'Extract ZIP…', 'SHA-256 checksum…', 'Properties…'):
            self.assertFalse(actions[label].isEnabled())

    def test_busy_and_link_entries_disable_mutating_archive_actions(self):
        self.page.file_busy = True
        actions = self.menu(self.point_for('a.log'))
        self.assertFalse(actions['Create ZIP…'].isEnabled())
        self.page.file_busy = False
        row = dict(self.rows[0], name='link.zip', path='/var/log/link.zip', kind='Link', permissions='lrwxrwxrwx')
        self.page.show_directory(dict(path='/var/log', entries=[row], truncated=False))
        actions = self.menu(self.point_for('link.zip'))
        self.assertFalse(actions['Extract ZIP…'].isEnabled())
        self.assertFalse(actions['Create ZIP…'].isEnabled())
        self.assertTrue(actions['Properties…'].isEnabled())

    def test_archive_cancel_and_success_refresh_same_parent(self):
        self.page.table.selectRow(0); self.page.task = MagicMock(); self.page.browse = MagicMock()
        with patch.object(QInputDialog, 'getText', return_value=('', False)):
            self.page.archive_selected('zip')
        self.page.task.assert_not_called()
        with patch.object(QInputDialog, 'getText', return_value=('logs.zip', True)):
            self.page.archive_selected('zip')
        fn,done = self.page.task.call_args.args
        with patch('app.archive_entry', return_value={'path':'/var/log/logs.zip','message':'Created ZIP'}) as archive:
            done(fn(None))
        self.assertEqual(archive.call_args.args[2:4], ('zip', 'logs.zip'))
        self.page.browse.assert_called_once_with('/var/log')

    def test_script_cancel_does_not_send_or_start_work(self):
        row = dict(self.rows[0], name='job.sh', path='/var/log/job.sh')
        self.page.show_directory(dict(path='/var/log', entries=[row], truncated=False))
        self.page.table.selectRow(0); self.page.terminal.online=True
        self.page.task=MagicMock(); self.page.send_bytes=MagicMock()
        with patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Rejected):self.page.run_shell_script()
        self.page.task.assert_not_called();self.page.send_bytes.assert_not_called()

    def test_script_accept_verifies_selection_and_runs_in_existing_terminal(self):
        row = dict(self.rows[0], name='job.sh', path='/var/log/job.sh')
        self.page.show_directory(dict(path='/var/log', entries=[row], truncated=False))
        self.page.table.selectRow(0); self.page.terminal.online=True
        self.page.task=MagicMock(); self.page.send_bytes=MagicMock()
        with patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Accepted):self.page.run_shell_script()
        fn,done=self.page.task.call_args.args
        with patch('app.verify_script',return_value=row):done(fn(None))
        self.page.send_bytes.assert_called_once_with(b'cd -- /var/log && bash -- /var/log/job.sh\n')
        self.assertTrue(self.page.terminal_toggle.isChecked())


if __name__ == '__main__':
    unittest.main()

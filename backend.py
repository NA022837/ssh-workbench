import base64
import hashlib
import os
from pathlib import Path
import posixpath
import queue
import socket
import stat
import threading
import uuid

import paramiko
from paramiko.sftp import CMD_OPENDIR,CMD_HANDLE,CMD_READDIR,CMD_NAME,CMD_CLOSE
from PySide6.QtCore import QThread, Signal

HOST_LOCK = threading.Lock()


def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


class UnknownHost(paramiko.SSHException):
    def __init__(self, hostname, key):
        self.hostname, self.key = hostname, key
        super().__init__('Host key needs verification')


class AskPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        raise UnknownHost(hostname, key)


def trust_host(path, hostname, key):
    """Only called after the UI receives an explicit trust decision."""
    with HOST_LOCK:
        keys = paramiko.HostKeys()
        if Path(path).exists():
            keys.load(str(path))
        existing = keys.lookup(hostname)
        if existing and key.get_name() in existing and existing[key.get_name()] != key:
            raise ValueError('A different key is already saved for this host. Verify the change independently.')
        keys.add(hostname, key.get_name(), key)
        temp = Path(path).with_name('known-hosts-' + uuid.uuid4().hex + '.tmp')
        fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            keys.save(str(temp))
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()


class SSHSession(QThread):
    ready = Signal(object)
    data = Signal(bytes)
    message = Signal(str)
    unknown_host = Signal(str, object)
    ended = Signal()

    def __init__(self, profile, secret, known_hosts, parent=None):
        super().__init__(parent)
        self.profile, self.secret, self.known_hosts = dict(profile), secret, known_hosts
        self.stop_event = threading.Event()
        self.inputs = queue.Queue(maxsize=256)
        self.output_slots = threading.BoundedSemaphore(8)
        self.client = None

    def send(self, data):
        if not self.stop_event.is_set():
            try:
                self.inputs.put_nowait(('send', data))
            except queue.Full:
                self.message.emit('Input queue is full. Wait for the server before sending more input.')

    def resize(self, width, height):
        try:
            self.inputs.put_nowait(('resize', (width, height)))
        except queue.Full:
            pass

    def stop(self):
        self.stop_event.set()

    def acknowledge_output(self):
        self.output_slots.release()

    def run(self):
        client = paramiko.SSHClient()
        self.client = client
        channel = None
        try:
            client.load_system_host_keys()
            if Path(self.known_hosts).exists():
                client.load_host_keys(str(self.known_hosts))
            client.set_missing_host_key_policy(AskPolicy())
            p = self.profile
            mode = p['auth']
            client.connect(hostname=p['host'], port=p['port'], username=p['username'],
                           password=self.secret if mode == 'password' else None,
                           key_filename=p['key_file'] if mode == 'key' else None,
                           passphrase=self.secret if mode == 'key' and self.secret else None,
                           allow_agent=mode == 'agent', look_for_keys=mode == 'agent',
                           timeout=10, banner_timeout=15, auth_timeout=20, channel_timeout=15)
            self.secret = None
            if self.stop_event.is_set():
                return
            client.get_transport().set_keepalive(30)
            channel = client.invoke_shell(term='vt100', width=110, height=30)
            channel.settimeout(5)
            self.ready.emit(client)
            while not self.stop_event.is_set() and not channel.closed:
                for _ in range(2):
                    if self.stop_event.is_set() or not channel.recv_ready():
                        break
                    if not self.output_slots.acquire(timeout=.1):
                        break
                    chunk = channel.recv(32768)
                    if not chunk:
                        self.output_slots.release()
                        return
                    self.data.emit(chunk)
                for _ in range(16):
                    try:
                        action, value = self.inputs.get_nowait()
                    except queue.Empty:
                        break
                    if action == 'send':
                        channel.sendall(value)
                    else:
                        channel.resize_pty(width=value[0], height=value[1])
                if channel.exit_status_ready():
                    break
                self.stop_event.wait(0.025)
        except UnknownHost as exc:
            if not self.stop_event.is_set():
                self.unknown_host.emit(exc.hostname, exc.key)
        except paramiko.BadHostKeyException:
            self.message.emit('HOST KEY CHANGED. Connection blocked. Verify the server key with its administrator; existing trust was not replaced.')
        except paramiko.AuthenticationException:
            self.message.emit('Authentication failed. Check the username, selected authentication method and credentials.')
        except (socket.timeout, TimeoutError):
            self.message.emit('Connection or SSH operation timed out.')
        except Exception as exc:
            if not self.stop_event.is_set():
                self.message.emit('{}: {}'.format(type(exc).__name__, str(exc)[:500]))
        finally:
            self.secret = None
            if channel is not None:
                channel.close()
            client.close()
            self.ended.emit()


class Task(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.result.emit(self.fn(self.progress.emit))
        except Exception as exc:
            self.failed.emit('{}: {}'.format(type(exc).__name__, str(exc)[:500]))
        finally:
            self.fn = None


def sftp_open(client):
    sftp = client.open_sftp()
    sftp.get_channel().settimeout(20)
    return sftp


def _directory_entry(real, attr, unsupported=False):
    mode=attr.st_mode or 0
    return {'name':attr.filename,'path':None if unsupported else posixpath.join(real,attr.filename),
            'kind':'Directory' if stat.S_ISDIR(mode) else 'Link' if stat.S_ISLNK(mode) else 'File',
            'size':attr.st_size or 0,'mtime':attr.st_mtime or 0,'permissions':stat.filemode(mode),
            'unsupported_name':unsupported}


def _raw_directory(client, path, stop):
    """Isolated fallback: ignore legacy longname encodings; never guess filename bytes."""
    with sftp_open(client) as sftp:
        real=sftp.normalize(path)
        kind,message=sftp._request(CMD_OPENDIR,real)
        if kind!=CMD_HANDLE:raise paramiko.SFTPError('Expected directory handle')
        handle=message.get_string()
        entries=[];truncated=False
        try:
            while True:
                if stop.is_set():raise RuntimeError('Session disconnected')
                try:kind,message=sftp._request(CMD_READDIR,handle)
                except EOFError:break
                if kind!=CMD_NAME:raise paramiko.SFTPError('Expected directory entries')
                count=message.get_int()
                if not count:break
                for _ in range(count):
                    if stop.is_set():raise RuntimeError('Session disconnected')
                    raw=message.get_string()
                    message.get_string()  # Human-readable longname is not needed for file operations.
                    attr=paramiko.SFTPAttributes._from_msg(message)
                    if raw in (b'.',b'..'):continue
                    if len(entries)>=5000:
                        truncated=True;break
                    try:
                        attr.filename=raw.decode('utf-8')
                        unsupported=False
                    except UnicodeDecodeError:
                        attr.filename=raw.decode('utf-8',errors='backslashreplace')+' [non-UTF-8 name]'
                        unsupported=True
                    entries.append(_directory_entry(real,attr,unsupported))
                if truncated:break
        finally:
            sftp._request(CMD_CLOSE,handle)
        entries.sort(key=lambda x:(x['kind']!='Directory',x['name'].casefold()))
        return {'path':real,'entries':entries,'truncated':truncated}


def _standard_directory(client, path, stop):
    with sftp_open(client) as sftp:
        real = sftp.normalize(path)
        entries = []
        truncated = False
        for attr in sftp.listdir_iter(real, read_aheads=10):
            if stop.is_set():
                raise RuntimeError('Session disconnected')
            if len(entries) >= 5000:
                truncated = True
                break
            entries.append(_directory_entry(real,attr))
        entries.sort(key=lambda x: (x['kind'] != 'Directory', x['name'].lower()))
        return {'path': real, 'entries': entries, 'truncated': truncated}


def list_directory(client, path, stop):
    try:return _standard_directory(client,path,stop)
    except UnicodeDecodeError:
        # The failed pipelined channel is closed before reopening; no global decoder patch.
        return _raw_directory(client,path,stop)


def preview_file(client, path):
    with sftp_open(client) as sftp:
        attrs = sftp.stat(path)
        if not stat.S_ISREG(attrs.st_mode or 0):
            raise ValueError('Preview supports regular files only, not directories or special devices.')
        with sftp.open(path, 'rb') as f:
            data = f.read(262145)
        truncated = len(data) > 262144
        data = data[:262144]
        if b'\x00' in data:
            raise ValueError('This looks like a binary file. Use Download instead.')
        return {'path': path, 'text': data.decode('utf-8', errors='replace'), 'truncated': truncated}


EDIT_LIMIT = 1024 * 1024


def rename_entry(client, entry, name, stop):
    """Rename within the same directory, using standard no-overwrite SFTP rename."""
    if not isinstance(name,str) or not name or name in ('.','..') or '/' in name or any(ord(c)<32 or ord(c)==127 for c in name):
        raise ValueError('Enter a single name without slashes or control characters.')
    source=entry['path']
    if not source or not source.startswith('/') or posixpath.normpath(source)!=source or source=='/':
        raise ValueError('Refresh and select a specific file or folder.')
    if stop.is_set():raise RuntimeError('Rename cancelled by disconnect')
    target=posixpath.join(posixpath.dirname(source),name)
    with sftp_open(client) as sftp:
        attrs=sftp.lstat(source)
        expected=(entry['size'],entry['mtime'],entry['permissions'])
        actual=(attrs.st_size or 0,attrs.st_mtime or 0,stat.filemode(attrs.st_mode or 0))
        if actual!=expected:raise ValueError('Item changed. Refresh and review before renaming.')
        if target==source:return target
        try:sftp.lstat(target)
        except FileNotFoundError:pass
        else:raise FileExistsError('That name already exists. Choose another name.')
        if stop.is_set():raise RuntimeError('Rename cancelled by disconnect')
        sftp.rename(source,target)
    return target


def create_directory(client, parent, name, stop):
    if not isinstance(name,str) or not name or name in ('.','..') or '/' in name or any(ord(c)<32 for c in name):
        raise ValueError('Enter a single folder name without slashes or control characters.')
    if stop.is_set():raise RuntimeError('Folder creation cancelled by disconnect')
    path=posixpath.join(parent,name)
    with sftp_open(client) as sftp:
        if stop.is_set():raise RuntimeError('Folder creation cancelled by disconnect')
        # mkdir refuses existing names; the server applies its normal umask/access rules.
        sftp.mkdir(path,mode=0o755)
    return path


def delete_file(client, entry, stop):
    """Delete exactly one confirmed file/link, never a directory or a link target."""
    if stop.is_set():raise RuntimeError('Delete cancelled by disconnect')
    with sftp_open(client) as sftp:
        attrs=sftp.lstat(entry['path'])
        mode=attrs.st_mode or 0
        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            raise ValueError('Delete supports regular files and symlinks only, not folders or special devices.')
        kind='Link' if stat.S_ISLNK(mode) else 'File'
        expected=(entry['kind'],entry['size'],entry['mtime'],entry['permissions'])
        actual=(kind,attrs.st_size or 0,attrs.st_mtime or 0,stat.filemode(mode))
        if actual!=expected:
            raise ValueError('File details changed since the listing was loaded. Refresh and review it before deleting.')
        if stop.is_set():raise RuntimeError('Delete cancelled by disconnect')
        sftp.remove(entry['path'])
        return 'Deleted '+entry['path']


def delete_directory(client, entry, stop, recursive=False, progress=None):
    """Plan a bounded tree before deleting; inspect links with lstat, never traverse them."""
    root=entry['path']
    if not isinstance(root,str) or not root.startswith('/') or posixpath.normpath(root)!=root or root=='/':
        raise ValueError('Choose a specific folder with a normalized absolute path; root deletion is blocked.')
    if stop.is_set():raise RuntimeError('Folder deletion cancelled')
    with sftp_open(client) as sftp:
        def check_parent(path):
            parent=posixpath.dirname(path)
            if sftp.normalize(parent)!=parent:
                raise ValueError('A parent folder now resolves through a different path. Deletion stopped; refresh and review.')
        def identity(attrs):return (attrs.st_mode,attrs.st_uid,attrs.st_gid)
        check_parent(root)
        attrs=sftp.lstat(root)
        if not stat.S_ISDIR(attrs.st_mode or 0):raise ValueError('Selected folder changed type. Refresh before deleting.')
        if (entry['kind'],entry['size'],entry['mtime'],entry['permissions']) != (
                'Directory',attrs.st_size or 0,attrs.st_mtime or 0,stat.filemode(attrs.st_mode)):
            raise ValueError('Folder details changed. Refresh and review before deleting.')
        if not recursive:
            if stop.is_set():raise RuntimeError('Folder deletion cancelled')
            sftp.rmdir(root)  # Server refuses a non-empty directory.
            return 'Deleted empty folder '+root
        plan=[]
        stack=[(root,attrs,False)]
        visited=0
        # No filesystem mutation occurs until the entire plan has been read successfully.
        try:
            while stack:
                if stop.is_set():raise RuntimeError('Folder deletion cancelled before any removal')
                path,expected,expanded=stack.pop()
                if expanded:
                    plan.append((path,expected));continue
                visited+=1
                if visited>50000:raise ValueError('Folder exceeds the 50,000-item delete limit; no items were deleted.')
                check_parent(path)
                current=sftp.lstat(path)
                if identity(current)!=identity(expected):raise ValueError('Folder contents changed while planning; no items were deleted.')
                mode=current.st_mode or 0
                if stat.S_ISDIR(mode):
                    if sftp.normalize(path)!=path:raise ValueError('Folder path changed; no items were deleted.')
                    stack.append((path,current,True))
                    for child in sftp.listdir_iter(path,read_aheads=1):
                        if stop.is_set():raise RuntimeError('Folder deletion cancelled before any removal')
                        name=child.filename
                        if name in ('.','..'):continue
                        if not name or '/' in name or '\x00' in name:raise ValueError('Invalid child filename; no items were deleted.')
                        if visited+len(stack)>50000:raise ValueError('Folder exceeds the 50,000-item delete limit; no items were deleted.')
                        stack.append((posixpath.join(path,name),child,False))
                elif stat.S_ISREG(mode) or stat.S_ISLNK(mode):plan.append((path,current))
                else:raise ValueError('Folder contains a special device or unsupported entry; no items were deleted.')
        except UnicodeDecodeError as exc:
            raise ValueError('This folder contains non-UTF-8 directory data. Recursive deletion was stopped before removing any items; use the SSH terminal.') from exc
        removed=0
        try:
            for path,expected in plan:
                if stop.is_set():raise RuntimeError('Folder deletion cancelled')
                check_parent(path)
                current=sftp.lstat(path)
                if identity(current)!=identity(expected):raise ValueError('An item changed type, ownership or permissions during deletion')
                if stat.S_ISDIR(current.st_mode):sftp.rmdir(path)
                else:
                    if (current.st_size,current.st_mtime)!=(expected.st_size,expected.st_mtime):
                        raise ValueError('A file changed during deletion')
                    sftp.remove(path)
                removed+=1
                if progress:progress(int(removed*100/max(len(plan),1)))
        except Exception as exc:
            raise RuntimeError('Folder deletion stopped after {} item(s). Partial deletion is possible; refresh the folder. {}'.format(removed,exc)) from exc
        return 'Deleted folder and {} item(s): {}'.format(removed-1,root)


def _permissions_snapshot(sftp, path):
    attrs=sftp.lstat(path)
    mode=attrs.st_mode or 0
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise ValueError('Permissions can be changed on regular files or folders, not symlinks or special devices.')
    return {'path':path,'mode':stat.S_IMODE(mode),'symbolic':stat.filemode(mode),
            'uid':attrs.st_uid,'gid':attrs.st_gid,'version':(mode,attrs.st_uid,attrs.st_gid)}


def read_permissions(client, entry):
    with sftp_open(client) as sftp:
        result=_permissions_snapshot(sftp,entry['path'])
        is_directory=stat.S_ISDIR(result['version'][0])
        if is_directory != (entry['kind']=='Directory'):
            raise ValueError('The selected item type changed. Refresh the folder before trying again.')
        return result


def change_permissions(client, snapshot, mode, stop):
    if isinstance(mode,bool) or not isinstance(mode,int) or not 0<=mode<=0o7777:
        raise ValueError('Permissions must be a mode between 0000 and 7777.')
    if stop.is_set():raise RuntimeError('Permission change cancelled by disconnect')
    with sftp_open(client) as sftp:
        current=_permissions_snapshot(sftp,snapshot['path'])
        if current['version']!=snapshot['version']:
            raise ValueError('Permissions or ownership changed on the server. Close and reopen Permissions to review the current values.')
        if stop.is_set():raise RuntimeError('Permission change cancelled by disconnect')
        sftp.chmod(snapshot['path'],mode)
        try:updated=_permissions_snapshot(sftp,snapshot['path'])
        except Exception as exc:
            raise RuntimeError('The chmod request completed, but the result could not be verified. Refresh the folder. '+str(exc)) from exc
        if updated['mode']!=mode:
            raise RuntimeError('Requested {:04o}, but the server reports {:04o}. Refresh and review the result.'.format(mode,updated['mode']))
        return updated


def _editable_snapshot(sftp, path):
    attrs = sftp.lstat(path)
    if not stat.S_ISREG(attrs.st_mode or 0):
        raise ValueError('Edit supports regular files only. Symlinks and special files are not editable.')
    if (attrs.st_size or 0) > EDIT_LIMIT:
        raise ValueError('Editor supports text files up to 1 MiB. Download larger files instead.')
    with sftp.open(path, 'rb') as src:
        data = src.read(EDIT_LIMIT + 1)
    if len(data) > EDIT_LIMIT or b'\x00' in data:
        raise ValueError('File is too large or binary; use Download instead.')
    # Strict decoding prevents a save from replacing invalid bytes with replacement characters.
    text = data.decode('utf-8')
    version = (hashlib.sha256(data).hexdigest(), attrs.st_mtime, attrs.st_mode,
               attrs.st_uid, attrs.st_gid)
    return {'path': path, 'text': text, 'version': version,
            'newline': '\r\n' if b'\r\n' in data and b'\n' not in data.replace(b'\r\n', b'') else '\n'}


def read_editable(client, path):
    with sftp_open(client) as sftp:
        return _editable_snapshot(sftp, path)


def save_text(client, path, text, expected, stop, newline='\n'):
    """Stage text and refuse stale edits; new files use a no-overwrite rename."""
    data = text.replace('\r\n', '\n').replace('\n', newline).encode('utf-8')
    if len(data) > EDIT_LIMIT or b'\x00' in data:
        raise ValueError('Editor supports UTF-8 text up to 1 MiB, without NUL bytes.')
    temp = posixpath.join(posixpath.dirname(path), '.ssh-workbench-edit-' + uuid.uuid4().hex)
    with sftp_open(client) as sftp:
        try:
            if stop.is_set():
                raise RuntimeError('Save cancelled by disconnect')
            if expected is not None:
                current = _editable_snapshot(sftp, path)
                if current['version'] != expected:
                    raise ValueError('File changed on the server. Close and reopen it before saving; your editor text is retained.')
            with sftp.open(temp, 'wx') as dst:
                sftp.chmod(temp, 0o600)
                dst.write(data)
            if expected is not None:
                # Preserve ownership as well as POSIX permissions, or fail before replacing.
                attrs = sftp.stat(temp)
                uid, gid = expected[3:5]
                if (attrs.st_uid, attrs.st_gid) != (uid, gid):
                    sftp.chown(temp, uid, gid)
                sftp.chmod(temp, stat.S_IMODE(expected[2]))
            saved = _editable_snapshot(sftp, temp)
            saved['path'] = path
            if expected is not None and _editable_snapshot(sftp, path)['version'] != expected:
                raise ValueError('File changed on the server during save. Reopen it before saving.')
            if stop.is_set():
                raise RuntimeError('Save cancelled by disconnect')
            if expected is None:
                # Standard SFTP rename must fail if the destination already exists.
                sftp.rename(temp, path)
            else:
                sftp.posix_rename(temp, path)
            return saved
        finally:
            try:
                sftp.remove(temp)
            except OSError:
                pass


def download(client, remote, local, stop, progress):
    local = Path(local)
    temp = local.with_name('.' + local.name + '.ssh-download-' + uuid.uuid4().hex)
    try:
        with sftp_open(client) as sftp:
            attrs = sftp.stat(remote)
            if not stat.S_ISREG(attrs.st_mode or 0):
                raise ValueError('Download supports regular files only.')
            size, done = attrs.st_size or 0, 0
            fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with sftp.open(remote, 'rb') as src, os.fdopen(fd, 'wb') as dst:
                while True:
                    if stop.is_set():
                        raise RuntimeError('Download cancelled by disconnect')
                    data = src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    done += len(data)
                    progress(min(100, int(done * 100 / max(size, 1))))
            os.replace(temp, local)
            return 'Downloaded to ' + str(local)
    finally:
        if temp.exists():
            temp.unlink()


def upload(client, local, remote, stop, progress):
    temp = posixpath.join(posixpath.dirname(remote), '.ssh-workbench-upload-' + uuid.uuid4().hex)
    with sftp_open(client) as sftp:
        try:
            size, done = Path(local).stat().st_size, 0
            with open(local, 'rb') as src, sftp.open(temp, 'wx') as dst:
                while True:
                    if stop.is_set():
                        raise RuntimeError('Upload cancelled by disconnect')
                    data = src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    done += len(data)
                    progress(min(100, int(done * 100 / max(size, 1))))
            # Never delete the old destination as a fallback for unsupported atomic rename.
            sftp.chmod(temp, stat.S_IMODE(Path(local).stat().st_mode) & 0o777)
            sftp.posix_rename(temp, remote)
            return 'Uploaded to ' + remote
        finally:
            try:
                sftp.remove(temp)
            except OSError:
                pass

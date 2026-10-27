"""Remote archive and file utilities; all work uses the current SSH identity."""
import base64
import hashlib
import json
import posixpath
import shlex
import stat

from backend import sftp_open
from system_backend import execute


def valid_path(path):
    if (not isinstance(path, str) or not path.startswith('/') or path == '/' or
            posixpath.normpath(path) != path or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        raise ValueError('Select a specific absolute path without control characters.')
    return path


def valid_name(name):
    if (not isinstance(name, str) or not name or name in ('.', '..') or '/' in name or
            any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError('Enter a single name without slashes or control characters.')
    return name


def checked_regular(sftp, entry):
    path = valid_path(entry['path'])
    attrs = sftp.lstat(path)
    if not stat.S_ISREG(attrs.st_mode or 0):
        raise ValueError('Select a regular file, not a symlink, folder or special device.')
    actual = (attrs.st_size or 0, attrs.st_mtime or 0, stat.filemode(attrs.st_mode or 0))
    if actual != (entry['size'], entry['mtime'], entry['permissions']):
        raise ValueError('File changed. Refresh and review before trying again.')
    return attrs


def verify_script(client, entry, stop):
    if stop.is_set():
        raise RuntimeError('Script launch cancelled by disconnect')
    with sftp_open(client) as sftp:
        checked_regular(sftp, entry)
    if stop.is_set():
        raise RuntimeError('Script launch cancelled by disconnect')
    return entry


def shell_command(path, interpreter, arguments=''):
    valid_path(path)
    if not path.lower().endswith('.sh'):
        raise ValueError('Select a .sh file.')
    if interpreter not in ('bash', 'sh'):
        raise ValueError('Choose bash or sh.')
    if any(ord(c) < 32 or ord(c) == 127 for c in arguments):
        raise ValueError('Arguments cannot contain control characters.')
    # Parse arguments as data; never pass command substitutions, pipelines or redirects to a shell.
    args = shlex.split(arguments)
    return 'cd -- {} && {} -- {}{}'.format(
        shlex.quote(posixpath.dirname(path)), interpreter, shlex.quote(path),
        ''.join(' ' + shlex.quote(arg) for arg in args))


def file_properties(client, entry, stop):
    if stop.is_set():
        raise RuntimeError('Properties request cancelled')
    path = valid_path(entry['path'])
    with sftp_open(client) as sftp:
        a = sftp.lstat(path)
        mode = a.st_mode or 0
        result = dict(path=path, size=a.st_size or 0, mtime=a.st_mtime or 0,
                      permissions=stat.filemode(mode), mode=stat.S_IMODE(mode),
                      uid=a.st_uid, gid=a.st_gid)
        if stat.S_ISLNK(mode):
            result['link_target'] = sftp.readlink(path)
    return result


def file_checksum(client, entry, stop, progress):
    if stop.is_set():
        raise RuntimeError('Checksum cancelled')
    digest = hashlib.sha256()
    with sftp_open(client) as sftp:
        attrs = checked_regular(sftp, entry)
        done = 0
        with sftp.open(entry['path'], 'rb') as source:
            while True:
                if stop.is_set():
                    raise RuntimeError('Checksum cancelled by disconnect')
                data = source.read(65536)
                if not data:
                    break
                digest.update(data)
                done += len(data)
                progress(min(100, int(done * 100 / max(attrs.st_size or 0, 1))))
        checked_regular(sftp, entry)
    return dict(path=entry['path'], sha256=digest.hexdigest())


# A self-contained standard-library program, also tested locally as a subprocess.
# Keeping the source as data works in PyInstaller builds without source-file lookups.
ARCHIVE_SCRIPT = r'''
import base64, json, ntpath, os, posixpath, shutil, stat, sys, tempfile, zipfile

MAX_ENTRIES = 100000
MAX_BYTES = 10 * 1024 * 1024 * 1024

def main():
    request = json.loads(base64.b64decode(sys.argv[1]).decode('utf-8'))
    source, target = request['source'], request['target']
    if (not source.startswith('/') or source == '/' or os.path.normpath(source) != source or
            os.path.dirname(target) != os.path.dirname(source) or target == source):
        raise ValueError('Invalid archive paths')
    a = os.lstat(source)
    actual = [a.st_size, int(a.st_mtime), stat.filemode(a.st_mode)]
    if actual != request['version']:
        raise ValueError('Item changed. Refresh and review before trying again.')
    if not (stat.S_ISREG(a.st_mode) or stat.S_ISDIR(a.st_mode)):
        raise ValueError('Archives support regular files and folders, not links or devices.')
    if os.path.lexists(target):
        raise FileExistsError('Destination already exists. Choose another name; nothing was overwritten.')
    if request['operation'] == 'zip':
        fd, temp = tempfile.mkstemp(prefix='.ssh-workbench-zip-', dir=os.path.dirname(target))
        os.close(fd)
        count, total = 0, 0
        def add(archive, path, name):
            nonlocal count, total
            if '\\' in name or ntpath.splitdrive(name)[0] or any(ord(c) < 32 or ord(c) == 127 for c in name):
                raise ValueError('Filename cannot be safely represented in a ZIP: ' + repr(name))
            count += 1
            if count > MAX_ENTRIES:
                raise ValueError('Archive exceeds the 100,000-entry limit.')
            mode = os.lstat(path).st_mode
            if stat.S_ISDIR(mode):
                info = zipfile.ZipInfo(name.rstrip('/') + '/')
                info.external_attr = (stat.S_IFDIR | stat.S_IMODE(mode)) << 16
                archive.writestr(info, b'')
            elif stat.S_ISREG(mode):
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(fd, 'rb') as src:
                    attrs = os.fstat(src.fileno())
                    if not stat.S_ISREG(attrs.st_mode):
                        raise ValueError('File type changed during ZIP creation.')
                    if total + attrs.st_size > MAX_BYTES:
                        raise ValueError('Archive input exceeds the 10 GiB limit.')
                    info = zipfile.ZipInfo.from_file(path, name)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with archive.open(info, 'w', force_zip64=True) as dst:
                        while True:
                            block = src.read(1024 * 1024)
                            if not block:
                                break
                            total += len(block)
                            if total > MAX_BYTES:
                                raise ValueError('Archive input exceeds the 10 GiB limit.')
                            dst.write(block)
                    after = os.fstat(src.fileno())
                    if (after.st_size, after.st_mtime_ns) != (attrs.st_size, attrs.st_mtime_ns):
                        raise ValueError('A file changed during ZIP creation. Retry after it stops changing.')
            else:
                raise ValueError('ZIP creation does not follow symbolic links or archive special files: ' + path)
        try:
            with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                parent = os.path.dirname(source)
                if stat.S_ISDIR(a.st_mode):
                    def walk_error(error):
                        raise error
                    for directory, folders, files in os.walk(source, followlinks=False, onerror=walk_error):
                        add(archive, directory, os.path.relpath(directory, parent))
                        for name in folders:
                            if os.path.islink(os.path.join(directory, name)):
                                raise ValueError('ZIP creation does not follow symbolic links: ' + name)
                        for name in files:
                            path = os.path.join(directory, name)
                            add(archive, path, os.path.relpath(path, parent))
                else:
                    add(archive, source, os.path.basename(source))
            # Publish without replacing any existing destination, even after a race.
            os.link(temp, target)
        finally:
            os.unlink(temp)
        return dict(path=target, message='Created ZIP: ' + target)
    if request['operation'] != 'unzip' or not stat.S_ISREG(a.st_mode):
        raise ValueError('Choose a regular ZIP file to extract.')
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES or sum(i.file_size for i in entries) > MAX_BYTES:
            raise ValueError('ZIP exceeds the 100,000-entry or 10 GiB extraction limit.')
        seen = set()
        for info in entries:
            name = info.orig_filename
            parts = name.rstrip('/').split('/')
            if (not name or name.startswith('/') or '\\' in name or '\x00' in name or
                    any(p in ('', '.', '..') for p in parts) or ntpath.splitdrive(name)[0] or
                    any(ord(c) < 32 or ord(c) == 127 for c in name)):
                raise ValueError('Unsafe archive member: ' + repr(name))
            normalized = '/'.join(parts)
            if normalized in seen:
                raise ValueError('Duplicate archive member: ' + name)
            seen.add(normalized)
            filetype = stat.S_IFMT(info.external_attr >> 16)
            if filetype not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError('ZIP links and special files are not extracted: ' + name)
            if info.flag_bits & 1:
                raise ValueError('Password-protected ZIP files are not supported.')
        os.mkdir(target, 0o700)
        total = 0
        try:
            for info in entries:
                destination = os.path.join(target, *info.filename.rstrip('/').split('/'))
                if info.is_dir():
                    os.makedirs(destination, mode=0o700, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(destination), mode=0o700, exist_ok=True)
                with archive.open(info) as src, open(destination, 'xb') as dst:
                    while True:
                        block = src.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > MAX_BYTES:
                            raise ValueError('Extracted data exceeds the 10 GiB limit.')
                        dst.write(block)
                mode = (info.external_attr >> 16) & 0o777
                os.chmod(destination, mode or 0o600)
        except Exception as exc:
            raise RuntimeError('Extraction failed; partial files may remain in ' + target + ': ' + str(exc))
    return dict(path=target, message='Extracted ZIP into new folder: ' + target)

try:
    print(json.dumps(main()))
except Exception as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(1)
'''


def archive_command(entry, operation, name):
    source = valid_path(entry['path'])
    valid_name(name)
    if operation not in ('zip', 'unzip'):
        raise ValueError('Unknown archive operation')
    if operation == 'zip' and not name.lower().endswith('.zip'):
        raise ValueError('The archive name must end in .zip.')
    target = posixpath.join(posixpath.dirname(source), name)
    if source == target:
        raise ValueError('Choose a destination different from the source.')
    request = dict(source=source, target=target, operation=operation,
                   version=[entry['size'], int(entry['mtime']), entry['permissions']])
    payload = base64.b64encode(json.dumps(request).encode('utf-8')).decode('ascii')
    return 'python3 -c {} {}'.format(shlex.quote(ARCHIVE_SCRIPT), shlex.quote(payload))


def archive_entry(client, entry, operation, name, stop):
    command = archive_command(entry, operation, name)
    try:
        result = execute(client, command, stop, timeout=600, limit=65536)
    except Exception as exc:
        raise RuntimeError('Archive operation failed. Python 3 is required on the remote server. '
                           'If interrupted, refresh before retrying; partial output may remain. ' + str(exc)) from exc
    return json.loads(result)

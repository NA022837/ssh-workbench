"""Local connection metadata. Credentials are never serialized."""
import json
import os
from pathlib import Path
import uuid

def group_path(value):
    if not isinstance(value,str) or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError('Group names must be text without control characters.')
    parts=[part.strip() for part in value.split('/')]
    if not parts or any(not part or len(part)>80 for part in parts) or len(parts)>8:
        raise ValueError('Use up to eight group levels, separated by /, with 1–80 characters per name.')
    return ' / '.join(parts)


def group_parent(path):return path.rsplit(' / ',1)[0] if ' / ' in path else ''


def complete_groups(groups,profiles):
    result=[]
    for path in list(groups)+sorted({p['group'] for p in profiles},key=str.casefold):
        path=group_path(path);parts=path.split(' / ')
        for i in range(1,len(parts)+1):
            prefix=' / '.join(parts[:i])
            if prefix not in result:result.append(prefix)
    return result


FIELDS = ('id', 'name', 'host', 'port', 'username', 'auth', 'key_file', 'start_dir', 'group')


def validate(profile):
    p = {k: profile.get(k, '') for k in FIELDS}
    p['id'] = str(p['id'] or uuid.uuid4().hex)
    for k in ('name', 'host', 'username'):
        if not isinstance(p[k], str) or not p[k].strip() or any(c in p[k] for c in '\r\n\x00'):
            raise ValueError('Provide a valid ' + k)
        p[k] = p[k].strip()
    if '://' in p['host'] or any(c.isspace() for c in p['host']):
        raise ValueError('Host must be a hostname or IP address, without a URL scheme or spaces.')
    p['port'] = int(p['port'] or 22)
    if not 1 <= p['port'] <= 65535:
        raise ValueError('Port must be between 1 and 65535.')
    if p['auth'] not in ('agent', 'password', 'key'):
        raise ValueError('Select SSH agent, password or private key authentication.')
    p['key_file'] = str(p['key_file'] or '')
    p['start_dir'] = str(p['start_dir'] or '.')
    p['group']=group_path(p['group'] or 'Ungrouped')
    if p['auth'] == 'key' and not p['key_file']:
        raise ValueError('Select a private key file.')
    if '\x00' in p['start_dir']:
        raise ValueError('Invalid directory path.')
    return p


class Store:
    def __init__(self, root=None):
        self.root = Path(root or Path.home() / '.ssh-workbench')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.file = self.root / 'profiles.json'
        self.known_hosts = self.root / 'known_hosts'

    def _read(self):
        if not self.file.exists():return [],[]
        data=json.loads(self.file.read_text(encoding='utf-8'))
        if isinstance(data,list):return [validate(p) for p in data],[]
        if not isinstance(data,dict) or data.get('version')!=2 or not isinstance(data.get('profiles'),list) or not isinstance(data.get('groups'),list):
            raise ValueError('Unrecognized connection configuration format.')
        profiles=[validate(p) for p in data['profiles']]
        return profiles,complete_groups(data['groups'],profiles)

    def load(self):return self._read()[0]

    def load_groups(self):
        profiles,groups=self._read()
        return complete_groups(groups,profiles)

    def save(self, profiles, groups=None):
        profiles=[validate(p) for p in profiles]
        if groups is None:groups=self._read()[1]
        data=dict(version=2,profiles=profiles,groups=complete_groups(groups,profiles))
        # Keep a credential-filtered backup for users upgrading a legacy list file.
        backup=self.file.with_name('profiles-v1-backup.json')
        if self.file.exists() and not backup.exists():
            original=json.loads(self.file.read_text(encoding='utf-8'))
            if isinstance(original,list):self._write(backup,[validate(p) for p in original])
        self._write(self.file,data)

    def _write(self,path,data):
        temp = path.with_name('profiles-' + uuid.uuid4().hex + '.tmp')
        fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp, path)
            path.chmod(0o600)
        finally:
            if temp.exists():
                temp.unlink()

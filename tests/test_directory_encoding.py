import stat
import threading
import unittest
from unittest.mock import MagicMock
import paramiko
from paramiko.sftp import CMD_OPENDIR,CMD_HANDLE,CMD_READDIR,CMD_NAME,CMD_CLOSE
from backend import list_directory


def message(data):return paramiko.Message(data.asbytes())


def listing(rows):
    msg=paramiko.Message();msg.add_int(len(rows))
    for name,longname in rows:
        msg.add_string(name);msg.add_string(longname)
        attr=paramiko.SFTPAttributes()
        attr.st_mode=stat.S_IFREG|0o640;attr.st_size=12;attr.st_uid=1000;attr.st_gid=1000
        attr.st_atime=1;attr.st_mtime=1;attr._pack(msg)
    return message(msg)


class DirectoryEncodingTests(unittest.TestCase):
    def setup_client(self,rows):
        client=MagicMock();normal=MagicMock();raw=MagicMock()
        for sftp in (normal,raw):
            sftp.__enter__.return_value=sftp
            sftp.normalize.return_value='/bppm/code/Sudha'
        normal.listdir_iter.side_effect=UnicodeDecodeError('utf-8',b'bad\xa0',3,4,'invalid start byte')
        client.open_sftp.side_effect=[normal,raw]
        self.read_count=0
        def request(command,*args):
            if command==CMD_OPENDIR:
                data=paramiko.Message();data.add_string(b'handle')
                return CMD_HANDLE,message(data)
            if command==CMD_READDIR:
                self.read_count+=1
                if self.read_count>1:raise EOFError()
                return CMD_NAME,listing(rows)
            if command==CMD_CLOSE:return 101,paramiko.Message()
            raise AssertionError(command)
        raw._request.side_effect=request
        return client,normal,raw

    def test_non_utf8_longname_does_not_break_valid_filename(self):
        client,normal,raw=self.setup_client([(b'notes.txt',b'owner\xa0group')])
        result=list_directory(client,'/bppm/code/Sudha',threading.Event())
        self.assertEqual(result['entries'][0]['path'],'/bppm/code/Sudha/notes.txt')
        self.assertFalse(result['entries'][0]['unsupported_name'])
        self.assertEqual(client.open_sftp.call_count,2)
        normal.__exit__.assert_called_once()
        self.assertTrue(any(call.args[0]==CMD_CLOSE for call in raw._request.call_args_list))

    def test_non_utf8_filename_is_visible_but_never_mapped_to_guessed_path(self):
        client,_,_=self.setup_client([(b'bad\xa0name',b'ignored'),('valid-ಕಾರ್ಯ.txt'.encode(),b'ignored')])
        result=list_directory(client,'/bppm/code/Sudha',threading.Event())
        unsupported=next(entry for entry in result['entries'] if entry['unsupported_name'])
        self.assertIn('\\xa0',unsupported['name'])
        self.assertIsNone(unsupported['path'])
        supported=next(entry for entry in result['entries'] if not entry['unsupported_name'])
        self.assertEqual(supported['path'],'/bppm/code/Sudha/valid-ಕಾರ್ಯ.txt')

    def test_compatibility_reader_keeps_listing_limit(self):
        client,_,_=self.setup_client([(str(i).encode(),b'ignored') for i in range(5001)])
        result=list_directory(client,'/folder',threading.Event())
        self.assertEqual(len(result['entries']),5000)
        self.assertTrue(result['truncated'])

    def test_dot_entries_are_ignored(self):
        client,_,_=self.setup_client([(b'.',b''),(b'..',b''),(b'normal',b'')])
        result=list_directory(client,'/folder',threading.Event())
        self.assertEqual([entry['name'] for entry in result['entries']],['normal'])

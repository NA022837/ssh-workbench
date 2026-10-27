#!/usr/bin/env python3
"""Build the real Windows x64 executable on Windows using Python 3.12."""
import argparse
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parent


def run(arguments):
    env={k:v for k,v in os.environ.items() if k.upper() not in ('PYTHONHOME','PYTHONPATH')}
    env['PYTHONNOUSERSITE']='1'
    subprocess.run([str(a) for a in arguments],cwd=ROOT,env=env,check=True)


def package_release(payload, mode):
    """Package only the generated payload, never workstation profiles or source folders."""
    release=ROOT/'release'
    release.mkdir(exist_ok=True)
    archive=release/('SSH-Workbench-Windows-x64-'+mode+'.zip')
    files=[payload] if payload.is_file() else sorted(p for p in payload.rglob('*') if p.is_file())
    if not files:raise RuntimeError('No executable payload was generated.')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as output:
        for file in files:
            name=file.name if payload.is_file() else str(Path(payload.name)/file.relative_to(payload))
            output.write(file,name)
        output.writestr('START-HERE.txt',
            'SSH Workbench for Windows x64\r\n\r\n'
            'Extract this ZIP completely, then open SSH Workbench.exe.\r\n'
            'For the onedir build, keep its entire folder together.\r\n'
            'Python is not required on the destination machine.\r\n'
            'Connection profiles are stored under your user home in .ssh-workbench.\r\n'
            'No server profiles or credentials are bundled.\r\n')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(digest+'  '+archive.name+'\n',encoding='ascii')
    return archive


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('onefile','onedir'),default='onefile')
    options=parser.parse_args()
    if sys.platform!='win32':
        parser.error('Run this builder on Windows, or run the supplied Windows GitHub Actions workflow.')
    if sys.version_info[:2]!=(3,12) or struct.calcsize('P')!=8:
        parser.error('Use 64-bit Python 3.12: py -3.12 build_windows.py')
    environment=ROOT/'.venv-windows312'
    run([sys.executable,'-m','venv',environment])
    python=environment/'Scripts'/'python.exe'
    run([python,'-m','pip','install','--upgrade','pip'])
    run([python,'-m','pip','install','-r',ROOT/'requirements.txt','pyinstaller'])
    run([python,'-c','import encodings, paramiko, pyte; from PySide6 import QtWidgets, QtSvg; import app'])
    work=ROOT/'build-windows'/options.mode
    work.mkdir(parents=True,exist_ok=True)
    dist=ROOT/'dist-windows'/options.mode
    run([python,'-m','PyInstaller','--noconfirm','--clean','--windowed','--'+options.mode,
         '--name','SSH Workbench','--distpath',dist,'--workpath',work/'cache','--specpath',work,
         '--collect-all','pyte','--collect-all','wcwidth',ROOT/'app.py'])
    executable=dist/'SSH Workbench.exe' if options.mode=='onefile' else dist/'SSH Workbench'/'SSH Workbench.exe'
    with executable.open('rb') as stream:
        if stream.read(2)!=b'MZ':raise RuntimeError('Output does not have a Windows executable header.')
    archive=package_release(executable if options.mode=='onefile' else executable.parent,options.mode)
    print('\nWindows executable created: '+str(executable))
    print('Shareable ZIP created: '+str(archive))
    print('Launch the executable on Windows and validate SSH/SFTP before distribution.')
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (subprocess.CalledProcessError,OSError,RuntimeError) as error:
        print('BUILD FAILED: '+str(error),file=sys.stderr)
        sys.exit(1)

"""Bounded Linux snapshots and explicit systemd actions over the existing SSH connection."""
import math
import re
import shlex
import time

MAX_OUTPUT=2*1024*1024
MAX_PROCESSES=3000
MAX_SERVICES=5000


def format_bytes(value):
    if value is None:return '—'
    number=max(0,float(value))
    for unit in ('B','KB','MB','GB','TB','PB'):
        if number<1024 or unit=='PB':
            return '{} {}'.format(int(number),unit) if unit=='B' else '{:.1f} {}'.format(number,unit)
        number/=1024


def execute(client,command,stop,timeout=25,limit=MAX_OUTPUT):
    """Drain both streams, cap bytes, close channel on timeout/disconnect. Never allocate a PTY."""
    if stop.is_set():raise RuntimeError('Request cancelled by disconnect')
    channel=client.get_transport().open_session(timeout=10)
    output=bytearray();error=bytearray();deadline=time.monotonic()+timeout
    try:
        channel.settimeout(10);channel.exec_command('LC_ALL=C SYSTEMD_COLORS=0 SYSTEMD_PAGER=cat '+command)
        while True:
            if stop.is_set():raise RuntimeError('Request cancelled by disconnect; action outcome may be unknown. Refresh after reconnecting.')
            if time.monotonic()>deadline:raise TimeoutError('Remote request timed out. A service action may still complete; refresh its status before retrying.')
            for ready,recv,buffer in ((channel.recv_ready,channel.recv,output),(channel.recv_stderr_ready,channel.recv_stderr,error)):
                if ready():
                    buffer.extend(recv(32768))
                    if len(output)+len(error)>limit:raise RuntimeError('Remote output limit exceeded; no complete snapshot is available.')
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():break
            if channel.closed:raise RuntimeError('SSH channel closed before completion; action outcome may be unknown.')
            stop.wait(.015)
        code=channel.recv_exit_status()
        text=output.decode('utf-8',errors='replace')
        if code!=0:
            detail=error.decode('utf-8',errors='replace').strip() or text.strip() or 'No diagnostic output'
            raise RuntimeError('Remote command failed (exit {}): {}'.format(code,detail[:700]))
        return text
    finally:channel.close()


def cpu_ticks(text):
    first=text.splitlines()[0].split()
    if not first or first[0]!='cpu' or len(first)<5:raise ValueError('CPU counters unavailable')
    values=[int(v) for v in first[1:9]] # guest counters already included in user/nice
    return sum(values),values[3]+(values[4] if len(values)>4 else 0)


def cpu_percent(before,after):
    total=after[0]-before[0];idle=after[1]-before[1]
    if total<=0 or idle<0:return None
    return max(0,min(100,100*(total-idle)/total))


def parse_meminfo(text):
    values={}
    for line in text.splitlines():
        parts=line.replace(':',' ').split()
        if len(parts)>=2:
            try:values[parts[0]]=int(parts[1])*1024
            except ValueError:continue
    total=values.get('MemTotal',0)
    if not total:raise ValueError('Linux memory counters unavailable')
    # MemAvailable includes reclaimable cache; raw MemFree alone is misleading.
    available=values.get('MemAvailable')
    estimated=available is None
    if available is None:
        available=sum(values.get(key,0) for key in ('MemFree','Buffers','Cached','SReclaimable'))-values.get('Shmem',0)
    available=max(0,min(total,available));swap=values.get('SwapTotal',0)
    swapfree=max(0,min(swap,values.get('SwapFree',0)))
    return dict(total=total,available=available,free=values.get('MemFree',0),used=total-available,
                percent=100*(total-available)/total,estimated=estimated,swap_total=swap,
                swap_used=swap-swapfree,swap_free=swapfree,swap_percent=100*(swap-swapfree)/swap if swap else 0)


def parse_disks(text):
    rows=[]
    for line in text.splitlines()[1:]:
        p=line.split(None,5)
        if len(p)!=6:continue
        try:
            rows.append(dict(device=p[0],total=int(p[1])*1024,used=int(p[2])*1024,
                             free=int(p[3])*1024,percent=float(p[4].rstrip('%')),mount=p[5]))
        except ValueError:continue
    return rows


METRICS_COMMAND="""sh -c 'printf "__CPU__\\n"; cat /proc/stat; printf "__MEM__\\n"; cat /proc/meminfo; printf "__UP__\\n"; cat /proc/uptime; printf "__LOAD__\\n"; cat /proc/loadavg; printf "__HOST__\\n"; uname -n; printf "__KERNEL__\\n"; uname -sr; printf "__END__\\n"'"""


def system_snapshot(client,stop):
    text=execute(client,METRICS_COMMAND,stop)
    parts={}
    for match in re.finditer(r'^__(CPU|MEM|UP|LOAD|HOST|KERNEL)__\n(.*?)(?=^__[A-Z]+__\n)',text,re.M|re.S):parts[match[1]]=match[2].strip()
    if any(key not in parts for key in ('CPU','MEM','UP','LOAD','HOST','KERNEL')):raise ValueError('Incomplete Linux metrics snapshot')
    before=cpu_ticks(parts['CPU'])
    if stop.wait(.3):raise RuntimeError('Metrics request cancelled')
    after=cpu_ticks(execute(client,'cat /proc/stat',stop))
    warnings=[]
    try:disks=parse_disks(execute(client,'df -P -k -l',stop,timeout=15))
    except Exception as exc:
        if stop.is_set():raise
        disks=[];warnings.append('Disk data unavailable: '+str(exc))
    return dict(cpu=cpu_percent(before,after),cores=sum(line.startswith('cpu') and line[3:4].isdigit() for line in parts['CPU'].splitlines()),
                memory=parse_meminfo(parts['MEM']),uptime=float(parts['UP'].split()[0]),load=parts['LOAD'].split()[:3],
                hostname=parts['HOST'],kernel=parts['KERNEL'],disks=disks,warnings=warnings)


def process_snapshot(client,stop):
    text=execute(client,'ps -eo pid=,user:32=,pcpu=,pmem=,rss=,etimes=,stat=,comm= --sort=-pcpu',stop)
    rows=[];skipped=0
    for line in text.splitlines():
        p=line.split(None,7)
        if len(p)!=8:skipped+=1;continue
        try:
            row=dict(pid=int(p[0]),user=p[1],cpu=float(p[2]),memory=float(p[3]),rss=int(p[4])*1024,elapsed=int(p[5]),state=p[6],name=p[7])
            if not all(math.isfinite(row[key]) and row[key]>=0 for key in ('cpu','memory','rss','elapsed')):raise ValueError()
            rows.append(row)
        except ValueError:skipped+=1
    if not rows and text.strip():raise ValueError('Process snapshot could not be parsed')
    return dict(rows=rows[:MAX_PROCESSES],truncated=len(rows)>MAX_PROCESSES,skipped=skipped,total=len(rows))


UNIT=re.compile(r'(?:[A-Za-z0-9_.:@-]|\\x[0-9A-Fa-f]{2})+\.service\Z')


def valid_unit(unit):
    return isinstance(unit,str) and len(unit)<=255 and bool(UNIT.fullmatch(unit)) and not unit.endswith('@.service')


def service_snapshot(client,stop):
    text=execute(client,'systemctl list-units --type=service --all --no-legend --no-pager --plain',stop)
    units={}
    for line in text.splitlines():
        p=line.strip().lstrip('●').strip().split(None,4)
        if len(p)>=4 and p[0].endswith('.service'):
            units[p[0]]=dict(unit=p[0],load=p[1],active=p[2],sub=p[3],description=p[4] if len(p)>4 else '',enabled='—')
    warnings=[]
    try:
        installed=execute(client,'systemctl list-unit-files --type=service --no-legend --no-pager',stop)
        for line in installed.splitlines():
            p=line.split()
            if len(p)>=2 and p[0].endswith('.service'):
                row=units.setdefault(p[0],dict(unit=p[0],load='not loaded',active='unknown',sub='—',description='',enabled='—'))
                row['enabled']=p[1]
    except Exception as exc:
        if stop.is_set():raise
        warnings.append('Installed unit files unavailable: '+str(exc))
    rows=sorted(units.values(),key=lambda r:(r['active']!='failed',r['unit'].casefold()))
    return dict(rows=rows[:MAX_SERVICES],truncated=len(rows)>MAX_SERVICES,total=len(rows),warnings=warnings)


def service_details(client,unit,stop):
    if not valid_unit(unit):raise ValueError('Select a concrete, valid .service unit; templates require an instance.')
    fields='Id,Description,LoadState,ActiveState,SubState,MainPID,ExecMainStatus,Result,ActiveEnterTimestamp,InactiveEnterTimestamp,MemoryCurrent,TasksCurrent,FragmentPath'
    return execute(client,'systemctl show --no-pager --property='+fields+' -- '+shlex.quote(unit),stop)


def service_action(client,unit,action,stop,use_sudo=False):
    if action not in ('start','stop','restart'):raise ValueError('Unsupported service action')
    if not valid_unit(unit):raise ValueError('Select a concrete, valid .service unit; templates require an instance.')
    # No shell expansion, patterns, enable/disable changes or interactive sudo prompts.
    command=('sudo -n -- ' if use_sudo else '')+'systemctl --no-ask-password --no-block '+action+' -- '+shlex.quote(unit)
    output=execute(client,command,stop,timeout=25)
    return '{} request accepted for {}. The service may still be transitioning; refresh to verify its actual state.{}'.format(action.capitalize(),unit,'\n'+output.strip() if output.strip() else '')

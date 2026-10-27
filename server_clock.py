"""Server-local clock, visible only for an established SSH session."""
from datetime import datetime,timedelta,timezone
import re,time
from PySide6.QtCore import QObject,QTimer,Signal,Qt
from backend import Task
from system_backend import execute


def read_server_time(client,stop):
    started=time.monotonic()
    text=execute(client,"date '+%s|%z|%Z'",stop,timeout=10,limit=4096).strip()
    finished=time.monotonic()
    match=re.fullmatch(r'(-?\d+)\|([+-])(\d{2})(\d{2})\|([^\r\n|]{0,32})',text)
    if not match:raise ValueError('Server date command returned an unsupported timestamp.')
    hours,minutes=int(match[3]),int(match[4])
    if hours>23 or minutes>59:raise ValueError('Invalid server timezone offset.')
    offset=(hours*60+minutes)*(1 if match[2]=='+' else -1)
    epoch=int(match[1])+(finished-started)/2
    # Validate before the value reaches the UI timer.
    datetime.fromtimestamp(epoch,timezone(timedelta(minutes=offset)))
    return dict(epoch=epoch,offset=offset,zone=match[5],anchor=finished)


def clock_text(sample,now=None):
    if now is None:now=time.monotonic()
    stamp=datetime.fromtimestamp(sample['epoch']+max(0,now-sample['anchor']),timezone(timedelta(minutes=sample['offset'])))
    offset=sample['offset'];sign='+' if offset>=0 else '-';hours,minutes=divmod(abs(offset),60)
    return 'Server time: {} {} (UTC{}{:02d}:{:02d})'.format(stamp.strftime('%d-%b-%Y %H:%M:%S').upper(),sample['zone'] or 'Local',sign,hours,minutes)


class ServerClock(QObject):
    changed=Signal(str)
    def __init__(self,page):
        super().__init__(page);self.page=page;self.connected=False;self.sample=None;self.job=None;self.generation=0
        self.ticker=QTimer(self);self.ticker.setInterval(1000);self.ticker.timeout.connect(self.tick)
        self.resync=QTimer(self);self.resync.setInterval(60000);self.resync.timeout.connect(self.sync)

    def state_changed(self,state):
        self.generation+=1;self.sample=None;self.changed.emit('')
        self.connected=state=='connected' and bool(self.page.client) and not self.page.closing
        self.ticker.stop();self.resync.stop()
        if self.connected:self.ticker.start();self.resync.start();self.sync()

    def tick(self):
        if self.connected and self.sample and self.page.connection_state=='connected' and not self.page.closing:
            self.changed.emit(clock_text(self.sample))
        else:self.changed.emit('')

    def sync(self):
        if not self.connected or self.page.closing or not self.page.client or self.job:return
        generation=self.generation;client=self.page.client;stop=self.page.worker.stop_event
        task=Task(lambda progress:read_server_time(client,stop),self)
        self.job=task;self.page.tasks.append(task)
        task.result.connect(lambda sample:self.accept_sample(generation,sample),Qt.ConnectionType.QueuedConnection)
        task.failed.connect(lambda message:self.failed_sample(generation),Qt.ConnectionType.QueuedConnection)
        task.finished.connect(lambda:self.finished_job(task),Qt.ConnectionType.QueuedConnection);task.start()

    def accept_sample(self,generation,sample):
        if self.connected and generation==self.generation and self.page.connection_state=='connected' and not self.page.closing:
            self.sample=sample;self.tick()

    def failed_sample(self,generation):
        if generation==self.generation:self.sample=None;self.changed.emit('')

    def finished_job(self,task):
        if task in self.page.tasks:self.page.tasks.remove(task)
        if self.job is task:self.job=None
        task.deleteLater()

"""On-call system dashboard. No remote commands until explicitly opened/refreshed."""
from datetime import datetime
from PySide6.QtCore import Qt,QTimer,QPointF,QRectF
from PySide6.QtGui import QColor,QPainter,QPainterPath,QPen
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
    QTabWidget,QTableWidget,QTableWidgetItem,QHeaderView,QLineEdit,QCheckBox,QMessageBox,
    QPlainTextEdit,QDialog,QFrame,QProgressBar,QStyledItemDelegate,QStyle)
from backend import Task
from icons import icon
from system_backend import format_bytes,system_snapshot,process_snapshot,service_snapshot,service_details,service_action,valid_unit


class NumberItem(QTableWidgetItem):
    def __lt__(self,other):return self.data(Qt.ItemDataRole.UserRole+1)<other.data(Qt.ItemDataRole.UserRole+1)


class BarDelegate(QStyledItemDelegate):
    """Readable percentage bars behind the normal, selectable numeric table cells."""
    def paint(self,painter,option,index):
        super().paint(painter,option,index)
        value=index.data(Qt.ItemDataRole.UserRole+1)
        if not isinstance(value,(int,float)):return
        painter.save()
        rect=option.rect.adjusted(6,option.rect.height()-5,-6,-2)
        color=QColor('#eea56b' if value>=85 else '#56a8ef');color.setAlpha(190)
        painter.fillRect(QRectF(rect.x(),rect.y(),rect.width()*max(0,min(value,100))/100,3),color)
        painter.restore()


class Trend(QWidget):
    def __init__(self):
        super().__init__();self.values=[];self.setMinimumHeight(92)
        self.setToolTip('CPU busy percentage for the last 60 successful overview refreshes. Samples are spaced evenly here, regardless of elapsed time.')
    def add(self,value):
        if value is not None:self.values=(self.values+[value])[-60:];self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        area=self.rect().adjusted(8,10,-8,-12)
        p.setPen(QPen(QColor('#34414f'),1))
        for fraction in (0,.5,1):p.drawLine(QPointF(area.left(),area.top()+area.height()*fraction),QPointF(area.right(),area.top()+area.height()*fraction))
        if not self.values:return
        points=[QPointF(area.left()+i*area.width()/max(1,len(self.values)-1),area.bottom()-v*area.height()/100) for i,v in enumerate(self.values)]
        path=QPainterPath(points[0])
        for point in points[1:]:path.lineTo(point)
        p.setPen(QPen(QColor('#66b9ff'),2));p.drawPath(path)
        p.setBrush(QColor('#66b9ff'));p.drawEllipse(points[-1],3,3)


class MetricCard(QFrame):
    def __init__(self,title):
        super().__init__();self.setObjectName('metricCard')
        layout=QVBoxLayout(self);layout.setContentsMargins(14,10,14,10)
        label=QLabel(title.upper());label.setObjectName('panelTitle');layout.addWidget(label)
        self.value=QLabel('—');self.value.setObjectName('metricValue');self.value.setMinimumHeight(30);layout.addWidget(self.value)
        self.detail=QLabel('Open Overview to load');self.detail.setWordWrap(True);self.detail.setMinimumHeight(36);layout.addWidget(self.detail)
        self.bar=QProgressBar();self.bar.setRange(0,1000);self.bar.setTextVisible(False);self.bar.setFixedHeight(5);layout.addWidget(self.bar)
    def show_value(self,value,detail,percent=0):
        self.value.setText(value);self.detail.setText(detail);self.bar.setValue(round(max(0,min(100,percent))*10))


def make_table(headers):
    table=QTableWidget(0,len(headers));table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True);table.setShowGrid(False);table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(29)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    for column in range(len(headers)):table.horizontalHeader().setSectionResizeMode(column,QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    table.setSortingEnabled(True)
    return table


def fill_table(table,rows,values,key):
    selected=None
    if table.currentRow()>=0:
        item=table.item(table.currentRow(),0)
        if item:selected=item.data(Qt.ItemDataRole.UserRole).get(key)
    table.setSortingEnabled(False);table.clearSelection();table.setCurrentCell(-1,-1)
    table.clearContents();table.setRowCount(len(rows))
    for i,row in enumerate(rows):
        for column,(text,sort) in enumerate(values(row)):
            item=NumberItem(str(text));item.setData(Qt.ItemDataRole.UserRole+1,sort)
            item.setData(Qt.ItemDataRole.UserRole,row)
            item.setToolTip(str(text));table.setItem(i,column,item)
            if selected==row.get(key) and column==0:table.setCurrentItem(item)
    table.setSortingEnabled(True)


def elapsed(seconds):
    seconds=max(0,int(seconds));days,seconds=divmod(seconds,86400);hours,seconds=divmod(seconds,3600);minutes,seconds=divmod(seconds,60)
    return ('{}d '.format(days) if days else '')+'{:02d}:{:02d}:{:02d}'.format(hours,minutes,seconds)


class SystemPanel(QWidget):
    def __init__(self,page):
        super().__init__(page);self.page=page;self.job=None;self.active=False;self.pending_refresh=False
        self.services_loaded=False;self.last_view=None;self.snapshots={};self.failed=False
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(10)
        head=QHBoxLayout();title=QLabel('SYSTEM MONITOR');title.setObjectName('panelTitle');head.addWidget(title);head.addStretch()
        self.interval=QComboBox()
        for text,seconds in [('Auto refresh off',0),('Every 15 seconds',15),('Every 30 seconds',30),('Every 60 seconds',60)]:self.interval.addItem(text,seconds)
        self.interval.setToolTip('Refreshes only the visible monitor tab while this server session is selected.');self.interval.currentIndexChanged.connect(self.update_timer)
        head.addWidget(self.interval)
        self.refresh=QPushButton('Refresh');self.refresh.setIcon(icon('refresh'));self.refresh.clicked.connect(self.reload)
        head.addWidget(self.refresh);layout.addLayout(head)
        self.host=QLabel('Linux snapshots over the existing SSH connection · No agent installation')
        self.host.setObjectName('hintText');self.host.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.host)
        self.tabs=QTabWidget();self.tabs.setDocumentMode(True);layout.addWidget(self.tabs,1)
        overview=QWidget();overview_layout=QVBoxLayout(overview)
        cards=QHBoxLayout();self.cpu=MetricCard('CPU busy');self.memory=MetricCard('RAM');self.swap=MetricCard('Swap');self.uptime=MetricCard('Uptime')
        for card in (self.cpu,self.memory,self.swap,self.uptime):cards.addWidget(card,1)
        overview_layout.addLayout(cards)
        trend_title=QLabel('CPU TREND · SUCCESSFUL REFRESH SAMPLES');trend_title.setObjectName('panelTitle');overview_layout.addWidget(trend_title)
        self.trend=Trend();overview_layout.addWidget(self.trend)
        disks_title=QLabel('LOCAL FILESYSTEMS · AVAILABLE SPACE');disks_title.setObjectName('panelTitle');overview_layout.addWidget(disks_title)
        self.disks=make_table(['Mount','Device','Total','Used','Available','Used %'])
        self.disks.setItemDelegateForColumn(5,BarDelegate(self.disks));overview_layout.addWidget(self.disks,1)
        self.tabs.addTab(overview,icon('activity'),'Overview')
        processes=QWidget();process_layout=QVBoxLayout(processes)
        self.process_filter=QLineEdit();self.process_filter.setPlaceholderText('Filter by process name, user or PID…');self.process_filter.setClearButtonEnabled(True)
        self.process_filter.textChanged.connect(self.filter_processes);process_layout.addWidget(self.process_filter)
        self.processes=make_table(['PID','Process','User','CPU %','RAM %','RAM (RSS)','Elapsed','State'])
        self.processes.horizontalHeader().setStretchLastSection(False)
        self.processes.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.processes.sortItems(3,Qt.SortOrder.DescendingOrder)
        self.processes.setItemDelegateForColumn(3,BarDelegate(self.processes));self.processes.setItemDelegateForColumn(4,BarDelegate(self.processes))
        process_layout.addWidget(self.processes,1)
        hint=QLabel('CPU % is the process lifetime average (100% = one CPU core). RAM is resident memory. Process names omit command arguments. Visibility follows server permissions.')
        hint.setWordWrap(True);hint.setObjectName('hintText');process_layout.addWidget(hint)
        self.tabs.addTab(processes,icon('server'),'Processes')
        services=QWidget();service_layout=QVBoxLayout(services)
        self.service_filter=QLineEdit();self.service_filter.setPlaceholderText('Filter by service, description or state…');self.service_filter.setClearButtonEnabled(True)
        self.service_filter.textChanged.connect(self.filter_services);service_layout.addWidget(self.service_filter)
        self.services=make_table(['Service','State','Substate','Load','Boot setting','Description'])
        self.services.sortItems(0,Qt.SortOrder.AscendingOrder)
        self.services.itemSelectionChanged.connect(self.update_actions);self.services.model().layoutChanged.connect(self.filter_services)
        service_layout.addWidget(self.services,1)
        actions=QHBoxLayout();self.sudo=QCheckBox('Use pre-authorized sudo');self.sudo.setToolTip('Uses sudo -n: requires an existing sudo authorization or a matching passwordless policy; never prompts for or saves a sudo password.')
        actions.addWidget(self.sudo);actions.addStretch()
        self.details=QPushButton('Details');self.details.setIcon(icon('view'));self.details.clicked.connect(self.show_details);actions.addWidget(self.details)
        self.action_buttons={}
        for name,symbol in [('start','play'),('stop','stop'),('restart','refresh')]:
            button=QPushButton(name.capitalize());button.setIcon(icon(symbol));button.setProperty('role','primary' if name=='start' else 'service')
            button.clicked.connect(lambda checked=False,a=name:self.change_service(a));self.action_buttons[name]=button;actions.addWidget(button)
        service_layout.addLayout(actions)
        self.service_note=QLabel('Select one systemd service. Start, Stop and Restart each require confirmation. Boot enable/disable settings are unchanged. Use the terminal if interactive sudo or a non-systemd service manager is required.')
        self.service_note.setWordWrap(True);self.service_note.setObjectName('hintText');service_layout.addWidget(self.service_note)
        self.tabs.addTab(services,icon('connect'),'Services')
        self.status=QLabel('Open the monitor on a connected server to load a snapshot.');self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True);layout.addWidget(self.status)
        self.timer=QTimer(self);self.timer.timeout.connect(self.auto_reload)
        self.tabs.currentChanged.connect(self.view_changed);self.update_actions()

    def available(self):return bool(self.page.client) and not self.page.closing and not self.page.worker.stop_event.is_set()

    def viewing(self):return self.active and self.isVisible() and self.available()

    def set_active(self,active):
        self.active=active;self.update_timer()
        if active and self.available():self.reload()

    def update_timer(self):
        self.timer.stop()
        if self.active and self.interval.currentData():self.timer.start(self.interval.currentData()*1000)

    def auto_reload(self):
        if self.viewing() and not self.job:self.reload()

    def view_changed(self):
        self.update_actions()
        if self.viewing():
            if self.job:self.pending_refresh=True
            else:self.reload()

    def reload(self):
        if not self.available():self.status.setText('Connect to this server to load system information.');return
        if self.job:return
        index=self.tabs.currentIndex();client,stop=self.page.client,self.page.worker.stop_event
        self.launch(lambda progress:(system_snapshot,process_snapshot,service_snapshot)[index](client,stop),lambda data:self.show_snapshot(index,data),'Loading {}…'.format(self.tabs.tabText(index)))

    def launch(self,fn,done,message):
        if self.job or not self.available():return
        self.status.setText(message);self.failed=False
        task=Task(fn,self);self.job=task;self.page.tasks.append(task);self.refresh.setEnabled(False);self.update_actions()
        task.result.connect(done,Qt.ConnectionType.QueuedConnection)
        task.failed.connect(self.on_error,Qt.ConnectionType.QueuedConnection)
        task.finished.connect(lambda:self.finished_job(task),Qt.ConnectionType.QueuedConnection)
        task.start()

    def on_error(self,message):
        self.failed=True;self.services_loaded=False
        self.status.setText(message+' · Previous values, if shown, are stale. No automatic action retry.')

    def disconnected(self):
        self.services_loaded=False
        self.status.setText('Disconnected · Previous snapshots are stale. Reconnect and refresh to load current values.')
        self.update_actions()

    def finished_job(self,task):
        if task in self.page.tasks:self.page.tasks.remove(task)
        if self.job is task:self.job=None
        task.deleteLater();self.refresh.setEnabled(True);self.update_actions()
        if self.pending_refresh and self.viewing():self.pending_refresh=False;self.reload()

    def show_snapshot(self,index,data):
        if self.page.closing:return
        stamp=datetime.now().astimezone().strftime('%d-%b-%Y %H:%M:%S %Z')
        self.snapshots[index]=stamp;self.last_view=index
        warnings=data.get('warnings',[])
        if index==0:
            mem=data['memory'];percent=data['cpu']
            self.host.setText(data['hostname']+' · '+data['kernel']+' · {} logical CPUs'.format(data['cores']))
            self.cpu.show_value('{:.1f}%'.format(percent) if percent is not None else '—','Load (1 / 5 / 15 min): '+' / '.join(data['load']),percent or 0)
            self.memory.show_value(format_bytes(mem['used'])+' used',format_bytes(mem['available'])+' available · '+format_bytes(mem['free'])+' free\n'+format_bytes(mem['total'])+' total',mem['percent'])
            self.memory.setToolTip('Used = total − available. Raw free: '+format_bytes(mem['free'])+('. Available is estimated on this kernel.' if mem['estimated'] else '. Available includes reclaimable cache.'))
            self.swap.show_value(format_bytes(mem['swap_used'])+' used' if mem['swap_total'] else 'Not configured',format_bytes(mem['swap_free'])+' free / '+format_bytes(mem['swap_total'])+' total',mem['swap_percent'])
            self.uptime.show_value(elapsed(data['uptime']),'Days / hours:minutes:seconds')
            self.trend.add(percent)
            fill_table(self.disks,data['disks'],lambda r:[(r['mount'],r['mount']),(r['device'],r['device'])]+[(format_bytes(r[k]),r[k]) for k in ('total','used','free')]+[('{:.1f}%'.format(r['percent']),r['percent'])],'mount')
        elif index==1:
            fill_table(self.processes,data['rows'],lambda r:[(r['pid'],r['pid']),(r['name'],r['name'].casefold()),(r['user'],r['user'].casefold()),
                ('{:.1f}%'.format(r['cpu']),r['cpu']),('{:.1f}%'.format(r['memory']),r['memory']),
                (format_bytes(r['rss']),r['rss']),(elapsed(r['elapsed']),r['elapsed']),(r['state'],r['state'])],'pid')
            self.filter_processes()
            if data['truncated']:warnings.append('Showing the first {} CPU-ranked processes of {}'.format(len(data['rows']),data['total']))
            if data['skipped']:warnings.append('{} process rows could not be parsed'.format(data['skipped']))
        else:
            self.services_loaded=True
            fill_table(self.services,data['rows'],lambda r:[(r[k],r[k].casefold()) for k in ('unit','active','sub','load','enabled','description')],'unit')
            for row in range(self.services.rowCount()):
                state=self.services.item(row,1)
                state.setForeground(QColor('#ef9a9a' if state.text()=='failed' else '#83d1ac' if state.text()=='active' else '#aab4bf'))
            self.filter_services();self.update_actions()
            if data['truncated']:warnings.append('Showing {} of {} services'.format(len(data['rows']),data['total']))
        self.status.setText('Last successful {} snapshot: {}{}'.format(self.tabs.tabText(index),stamp,' · '+' · '.join(warnings) if warnings else ''))

    def filter_rows(self,table,query):
        for row in range(table.rowCount()):
            show=any(query in table.item(row,column).text().casefold() for column in range(table.columnCount()) if table.item(row,column))
            table.setRowHidden(row,not show)
        if table.currentRow()>=0 and table.isRowHidden(table.currentRow()):table.clearSelection();table.setCurrentCell(-1,-1)

    def filter_processes(self):self.filter_rows(self.processes,self.process_filter.text().casefold())
    def filter_services(self):self.filter_rows(self.services,self.service_filter.text().casefold());self.update_actions()

    def selected_service(self):
        row=self.services.currentRow()
        if row<0 or self.services.isRowHidden(row) or not self.services.selectedItems():return None
        item=self.services.item(row,0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def update_actions(self):
        row=self.selected_service() if hasattr(self,'services') else None
        enabled=bool(row) and self.services_loaded and not self.job and self.available() and valid_unit(row['unit'])
        if hasattr(self,'details'):
            self.details.setEnabled(enabled)
            for name,button in self.action_buttons.items():
                allowed=enabled and (name!='start' or (row['active'] not in ('active','activating') and row['load']!='masked' and not row['enabled'].startswith('masked')))
                button.setEnabled(bool(allowed))

    def show_details(self):
        row=self.selected_service()
        if not row or not self.details.isEnabled():return
        client,stop,unit=self.page.client,self.page.worker.stop_event,row['unit']
        def display(text):
            dialog=QDialog(self);dialog.setWindowTitle('Service details · '+unit);dialog.resize(700,450)
            layout=QVBoxLayout(dialog);body=QPlainTextEdit();body.setReadOnly(True);body.setPlainText(text);layout.addWidget(body)
            close=QPushButton('Close');close.clicked.connect(dialog.accept);layout.addWidget(close);dialog.exec();dialog.deleteLater()
            self.status.setText('Service details retrieved for '+unit)
        self.launch(lambda progress:service_details(client,unit,stop),display,'Loading service details…')

    def change_service(self,action):
        row=self.selected_service()
        if not row or action not in self.action_buttons or not self.action_buttons[action].isEnabled():return
        unit=row['unit'];use_sudo=self.sudo.isChecked()
        box=QMessageBox(self);box.setWindowTitle(action.capitalize()+' service');box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText('{} {} on {}?\n\nConnected as {}{}.\nLast observed state: {} / {}.\n\nThis can interrupt application traffic or dependent services.'.format(action.capitalize(),unit,self.page.profile['host'],self.page.profile['username'],' using pre-authorized sudo' if use_sudo else '',row['active'],row['sub']))
        box.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel);box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec()!=QMessageBox.StandardButton.Yes:return
        client,stop=self.page.client,self.page.worker.stop_event
        self.launch(lambda progress:service_action(client,unit,action,stop,use_sudo),self.action_result,action.capitalize()+' request in progress…')

    def action_result(self,message):
        self.services_loaded=False
        self.status.setText(message)
        # State is intentionally refreshed by the user/next timed poll. A successful
        # --no-block request is not proof that the unit reached its target state.

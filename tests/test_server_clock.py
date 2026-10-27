"""Server-local time and connection lifecycle, without contacting remote hosts."""
import tempfile,threading,time,unittest
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from app import SessionPage
from profiles import Store,validate
from server_clock import read_server_time,clock_text
APP=QApplication.instance() or QApplication([])


class ClockFormat(unittest.TestCase):
    def test_server_offset_zone_and_monotonic_ticks(self):
        sample=dict(epoch=0,offset=330,zone='IST',anchor=100)
        self.assertEqual(clock_text(sample,102),'Server time: 01-JAN-1970 05:30:02 IST (UTC+05:30)')
        sample.update(offset=-300,zone='CDT')
        self.assertEqual(clock_text(sample,100),'Server time: 31-DEC-1969 19:00:00 CDT (UTC-05:00)')
    def test_date_query_parsing_and_malformed_values(self):
        with patch('server_clock.execute',return_value='1790000000|-0500|CDT\n') as execute:
            sample=read_server_time(MagicMock(),threading.Event())
        self.assertEqual(sample['offset'],-300);self.assertEqual(sample['zone'],'CDT')
        self.assertEqual(execute.call_args.args[1],"date '+%s|%z|%Z'")
        self.assertEqual(execute.call_args.kwargs['timeout'],10)
        for text in ('bad','1|+2460|BAD','1|+0500|BAD\nextra','1|+9999|BAD'):
            with patch('server_clock.execute',return_value=text),self.assertRaises(ValueError):read_server_time(None,threading.Event())


class ClockLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        p=validate(dict(name='Example',host='example.test',username='operator',auth='agent'))
        self.page=SessionPage(p,Store(self.tmp.name));self.clock=self.page.server_clock
        self.page.client=MagicMock();self.page.worker=SimpleNamespace(stop_event=threading.Event())
    def tearDown(self):
        self.clock.ticker.stop();self.clock.resync.stop()
        self.page.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.tmp.cleanup()
    def connect_without_network(self):
        with patch.object(self.clock,'sync'):self.page.set_connection_state('connected')
    def sample(self):return dict(epoch=1790000000,offset=-300,zone='CDT',anchor=time.monotonic())
    def test_hidden_until_connected_sample_and_cleared_on_disconnect(self):
        self.assertTrue(self.page.server_time.isHidden());self.assertEqual(self.page.server_time.text(),'')
        self.connect_without_network();self.assertTrue(self.page.server_time.isHidden())
        self.clock.accept_sample(self.clock.generation,self.sample())
        self.assertFalse(self.page.server_time.isHidden());self.assertIn('CDT (UTC-05:00)',self.page.server_time.text())
        self.page.set_connection_state('disconnected')
        self.assertTrue(self.page.server_time.isHidden());self.assertEqual(self.page.server_time.text(),'')
        self.assertFalse(self.clock.ticker.isActive());self.assertFalse(self.clock.resync.isActive())
    def test_reconnecting_and_old_async_samples_cannot_show_time(self):
        self.connect_without_network();old=self.clock.generation
        self.page.set_connection_state('connecting');self.clock.accept_sample(old,self.sample())
        self.assertEqual(self.page.server_time.text(),'');self.assertTrue(self.page.server_time.isHidden())
        self.connect_without_network();self.clock.accept_sample(old,self.sample());self.assertTrue(self.page.server_time.isHidden())
    def test_query_failure_hides_previous_value(self):
        self.connect_without_network();self.clock.accept_sample(self.clock.generation,self.sample())
        self.clock.failed_sample(self.clock.generation)
        self.assertEqual(self.page.server_time.text(),'');self.assertIsNone(self.clock.sample)
    def test_one_minute_sync_without_overlapping_jobs(self):
        self.connect_without_network();self.assertEqual(self.clock.resync.interval(),60000)
        self.assertEqual(self.clock.ticker.interval(),1000)
        self.clock.job=MagicMock()
        with patch('server_clock.read_server_time') as query:self.clock.sync();query.assert_not_called()
        self.clock.job=None
    def test_background_query_tracked_until_completion(self):
        with patch('server_clock.read_server_time',return_value=self.sample()):
            self.page.set_connection_state('connected')
            deadline=time.monotonic()+3
            while self.clock.job is not None and time.monotonic()<deadline:APP.processEvents();QTest.qWait(5)
        self.assertIsNone(self.clock.job);self.assertEqual(self.page.tasks,[])
        self.assertIn('Server time:',self.page.server_time.text())
        self.page.set_connection_state('disconnecting');self.assertTrue(self.page.server_time.isHidden())

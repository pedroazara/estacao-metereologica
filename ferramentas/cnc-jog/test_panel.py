"""Run: python3 -m unittest -v. All tests use an in-memory CNC; no USB access."""
import http.client
import json
import os
import pty
import select
import threading
import time
import unittest
from unittest.mock import patch

import serial
from simulator import Simulator

from server import Controller, make_server


class FastHandshakeTests(unittest.TestCase):
    """Short, deterministic handshake timing; no test uses a physical device."""
    def setUp(self):
        for name, value in (('STARTUP_SETTLE_SECONDS', 0.02),
                            ('CONNECT_TIMEOUT_SECONDS', 1.5)):
            override = patch('server.' + name, value)
            override.start()
            self.addCleanup(override.stop)


def wait_for(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError('Timed out waiting for simulated controller')


class MotionTests(FastHandshakeTests):
    def setUp(self):
        super().setUp()
        self.c = Controller()
        self.c.connect('SIMULADOR')
        wait_for(lambda: self.c.snapshot()['fresh'])
        self.client = 'test-client-one'

    def tearDown(self):
        self.c.disconnect()

    def request(self, **kw):
        return dict(client=self.client, epoch=self.c.control_epoch, axis='X', distance=0.1, feed=60, **kw)

    def test_connection_only_sends_queries(self):
        tx = [x['text'] for x in self.c.logs if x['direction'] == 'tx']
        self.assertEqual(tx, ['$I', '$G', '$$'])
        self.assertFalse(self.c.armed)

    def test_motion_requires_explicit_arm(self):
        with self.assertRaises(ValueError):
            self.c.jog(self.request())
        self.assertFalse(any(x['text'].startswith('$J') for x in self.c.logs))

    def test_jog_and_round_trip(self):
        self.c.arm(self.client)
        command = self.c.jog(self.request())
        self.assertEqual(command, '$J=G91 G21 X0.100 F60.0')
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.assertAlmostEqual(self.c.position[0], 0.1, places=3)
        request = self.request(); request['distance'] = -0.1
        self.c.jog(request)
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.assertAlmostEqual(self.c.position[0], 0, places=3)

    def test_cancel_stops_and_disarms(self):
        self.c.arm(self.client)
        request = self.request(); request['distance'] = 5
        self.c.jog(request)
        time.sleep(0.1)
        self.c.cancel()
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.assertFalse(self.c.armed)
        self.assertLess(self.c.position[0], 5)
        self.assertTrue(any('0x85' in x['text'] for x in self.c.logs))

    def test_no_command_queue(self):
        self.c.arm(self.client)
        request = self.request(); request['distance'] = 5
        self.c.jog(request)
        with self.assertRaises(ValueError):
            self.c.jog(request)

    def test_limits_and_injection(self):
        self.c.arm(self.client)
        bad = [dict(axis='X\nM3'), dict(axis='A'), dict(distance=0), dict(distance=6),
               dict(distance=float('nan')), dict(feed=float('inf')), dict(feed=True),
               dict(feed='60\nM3'), dict(feed=601), dict(feed=0),
               dict(axis='Z', distance=3), dict(axis='Z', feed=241)]
        for changes in bad:
            with self.subTest(changes=changes):
                request = self.request(); request.update(changes)
                with self.assertRaises(ValueError):
                    self.c.jog(request)
        self.assertFalse(any(x['text'].startswith('$J') for x in self.c.logs))

    def test_check_alarm_and_stale_block_jog(self):
        self.c.arm(self.client)
        with self.c.lock:
            for state in ('Check', 'Alarm', 'Hold', 'Run', 'Door'):
                self.c.machine = state
                with self.assertRaises(ValueError):
                    self.c.jog(self.request())
            self.c.machine = 'Idle'
            self.c.last_status = time.monotonic() - 3
            with self.assertRaises(ValueError):
                self.c.jog(self.request())

    def test_spindle_blocks_motion(self):
        with self.c.lock:
            self.c.rpm = 1000
            with self.assertRaises(ValueError):
                self.c.arm(self.client)

    def test_other_tab_cannot_move(self):
        self.c.arm(self.client)
        request = self.request(); request['client'] = 'another-tab'
        with self.assertRaises(ValueError):
            self.c.jog(request)
        with self.assertRaises(ValueError):
            self.c.arm('another-tab')

    def test_heartbeat_timeout_cancels(self):
        self.c.arm(self.client)
        request = self.request(); request['distance'] = 5
        self.c.jog(request)
        wait_for(lambda: not self.c.armed, timeout=3)
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.assertLess(self.c.position[0], 5)

    def test_disconnect_cancels_before_close(self):
        self.c.arm(self.client)
        request = self.request(); request['distance'] = 5
        self.c.jog(request)
        self.c.disconnect()
        self.assertFalse(self.c.connected)
        self.assertFalse(self.c.armed)
        self.assertTrue(any('0x85' in x['text'] for x in self.c.logs))

    def test_disconnect_never_leaves_stale_arm(self):
        self.c.arm(self.client)
        self.c.disconnect()
        self.c.connect('SIMULADOR')
        self.assertFalse(self.c.armed)

    def test_machine_reset_requires_reconnect(self):
        self.c.arm(self.client)
        self.c._line("GrblHAL 1.1f ['$' for help]")
        self.assertFalse(self.c.ready)
        self.assertFalse(self.c.armed)
        self.assertEqual(self.c.owner, '')
        self.assertEqual(self.c.info, {})
        self.assertEqual(self.c.settings, {})
        self.assertEqual(self.c.parser, '')
        self.assertIsNone(self.c.position)
        self.assertIsNone(self.c.rpm)
        # Further unsolicited reports cannot silently restore the session.
        self.c._line('<Idle|MPos:0,0,0|FS:0,0>')
        self.assertFalse(self.c.ready)
        with self.assertRaises(ValueError):
            self.c.arm(self.client)


class RecordingSimulator(Simulator):
    """Keep transport bytes separately from the controller's UI log."""
    def __init__(self):
        super().__init__()
        self.writes = []

    def write(self, data):
        self.writes.append(data)
        return super().write(data)


class HandshakeTests(FastHandshakeTests):
    def setUp(self):
        super().setUp()
        self.c = Controller()
        self.addCleanup(self.c.disconnect)

    def connect_with(self, fake):
        with patch('server.Simulator', return_value=fake):
            self.c.connect('SIMULADOR')

    def assert_read_only(self, fake):
        self.assertTrue(fake.writes)
        self.assertTrue(all(data in (b'$I\n', b'$G\n', b'$$\n', b'\x80')
                            for data in fake.writes), fake.writes)

    def test_native_status_query_and_ready_requires_live_status(self):
        fake = RecordingSimulator()
        self.connect_with(fake)
        self.assertIn(b'\x80', fake.writes)
        self.assertNotIn(b'?', fake.writes)
        self.assertTrue(self.c.ready)
        self.assertTrue(self.c.snapshot()['fresh'])
        self.assertEqual(self.c.position, [0, 0, 0])
        self.assertEqual(self.c.rpm, 0)
        self.assertFalse(self.c.armed)
        self.assert_read_only(fake)

    def test_native_usb_dtr_is_asserted_before_open_without_rts(self):
        class VirtualPort(RecordingSimulator):
            def __init__(self):
                super().__init__()
                self.dtr = None
                self.rts = None
                self.port = None
                self.open_configuration = None

            def open(self):
                self.open_configuration = (self.port, self.dtr, self.rts)

            def fileno(self):
                return -123  # Never passed to the OS: ioctl is mocked below.

        fake = VirtualPort()
        device = '/test-only-virtual-cnc'
        with patch('server.choices', return_value=[{'device': device}]), \
             patch('server.occupied', return_value=False), \
             patch('server.serial.Serial', return_value=fake) as constructor, \
             patch('fcntl.ioctl') as ioctl:
            self.c.connect(device)
        self.assertEqual(fake.open_configuration, (device, True, False))
        self.assertIsNone(constructor.call_args.kwargs['port'])
        self.assertTrue(constructor.call_args.kwargs['exclusive'])
        self.assertEqual(constructor.call_args.kwargs['baudrate'], 115200)
        self.assertEqual(ioctl.call_args.args[0], -123)
        self.assertTrue(self.c.ready)
        self.assertFalse(self.c.armed)
        self.assert_read_only(fake)

    def test_initial_welcome_after_settings_is_not_established_reset(self):
        class DelayedWelcome(RecordingSimulator):
            def __init__(self):
                super().__init__()
                self.sent_welcome = False

            def write(self, data):
                result = super().write(data)
                if data == b'$$\n' and not self.sent_welcome:
                    self.sent_welcome = True
                    # The final query's ok precedes the delayed startup banner.
                    self.emit("GrblHAL 1.1f ['$' for help]")
                return result

        fake = DelayedWelcome()
        self.connect_with(fake)
        self.assertTrue(self.c.ready)
        self.assertFalse(self.c.armed)
        self.assertEqual(self.c.owner, '')
        self.assertEqual(self.c.info.get('BOARD'), 'BTT SKR V1.4 Turbo')
        self.assertEqual(self.c.settings.get('13'), '0')
        self.assertGreaterEqual(fake.writes.count(b'$I\n'), 2)
        self.assert_read_only(fake)

    def test_reset_during_query_restarts_identification(self):
        class ResetOnce(RecordingSimulator):
            def __init__(self):
                super().__init__()
                self.did_reset = False

            def write(self, data):
                if data == b'$G\n' and not self.did_reset:
                    self.did_reset = True
                    self.writes.append(data)
                    # No ok for the query interrupted by the simulated reset.
                    self.emit("GrblHAL 1.1f ['$' for help]")
                    return len(data)
                return super().write(data)

        fake = ResetOnce()
        self.connect_with(fake)
        self.assertTrue(self.c.ready)
        self.assertFalse(self.c.armed)
        self.assertGreaterEqual(fake.writes.count(b'$I\n'), 2)
        self.assertIn('M5', self.c.parser.split())
        self.assertEqual(self.c.settings.get('13'), '0')
        self.assert_read_only(fake)

    def test_no_status_times_out_instead_of_becoming_ready(self):
        class NoStatus(RecordingSimulator):
            def write(self, data):
                if data in (b'?', b'\x80'):
                    self.writes.append(data)
                    return len(data)
                return super().write(data)

        fake = NoStatus()
        with patch('server.CONNECT_TIMEOUT_SECONDS', 0.3):
            with self.assertRaises(ValueError):
                self.connect_with(fake)
        self.assertFalse(self.c.ready)
        self.assertFalse(self.c.connected)
        self.assertFalse(self.c.armed)
        self.assertEqual(self.c.owner, '')
        self.assert_read_only(fake)

    def test_incomplete_or_invalid_status_never_makes_ready(self):
        class FixedStatus(RecordingSimulator):
            def __init__(self, status):
                super().__init__()
                self.status = status

            def write(self, data):
                if data in (b'?', b'\x80'):
                    self.writes.append(data)
                    self.emit(self.status)
                    return len(data)
                return super().write(data)

        for status in ('<Idle|MPos:0,0,0>', '<Idle|FS:0,0>',
                       '<Idle|MPos:nan,0,0|FS:0,0>',
                       '<Idle|MPos:0,0|FS:0,0>',
                       '<Idle|MPos:0,0,0|FS:0,inf>'):
            with self.subTest(status=status):
                fake = FixedStatus(status)
                with patch('server.CONNECT_TIMEOUT_SECONDS', 0.3):
                    with self.assertRaises(ValueError):
                        self.connect_with(fake)
                self.assertFalse(self.c.connected)
                self.assertFalse(self.c.ready)
                self.assertFalse(self.c.armed)
                self.assert_read_only(fake)

    def test_continuous_welcome_retries_are_bounded_and_fail_closed(self):
        class ContinuousWelcome(RecordingSimulator):
            def write(self, data):
                if data == b'$I\n':
                    self.writes.append(data)
                    self.emit("GrblHAL 1.1f ['$' for help]")
                    return len(data)
                return super().write(data)

        fake = ContinuousWelcome()
        with self.assertRaises(ValueError):
            self.connect_with(fake)
        self.assertLessEqual(fake.writes.count(b'$I\n'), 3)
        self.assertFalse(self.c.connected)
        self.assertFalse(self.c.ready)
        self.assertFalse(self.c.armed)
        self.assert_read_only(fake)

    def test_cannot_arm_while_waiting_for_first_status(self):
        allowed = threading.Event()
        queried = threading.Event()

        class GatedStatus(RecordingSimulator):
            def write(self, data):
                if data in (b'?', b'\x80'):
                    queried.set()
                    if not allowed.is_set():
                        self.writes.append(data)
                        return len(data)
                return super().write(data)

        fake = GatedStatus()
        errors = []

        def connect():
            try:
                self.connect_with(fake)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=connect, daemon=True)
        worker.start()
        try:
            self.assertTrue(queried.wait(0.8))
            self.assertFalse(self.c.ready)
            self.assertFalse(self.c.armed)
            with self.assertRaises(ValueError):
                self.c.arm('waiting-client')
        finally:
            allowed.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(self.c.ready)
        self.assertFalse(self.c.armed)
        self.assert_read_only(fake)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = make_server(0)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()

    def request(self, path, method='GET', body=None, **headers):
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = conn.getresponse()
        result = response.status, response.read().decode()
        conn.close()
        return result

    def test_local_page(self):
        status, html = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(self.http.token, html)

    def test_mutation_requires_token(self):
        status, _ = self.request('/api/arm', 'POST', {'client':'malicious-tab'})
        self.assertEqual(status, 403)

    def test_cross_origin_blocked(self):
        status, _ = self.request('/api/cancel', 'POST', {'token':self.http.token}, Origin='https://evil.example')
        self.assertEqual(status, 403)

    def test_dns_rebinding_blocked(self):
        status, _ = self.request('/', Host='evil.example')
        self.assertEqual(status, 403)

    def test_raw_command_endpoint_absent(self):
        status, _ = self.request('/api/send', 'POST', {'token':self.http.token,'command':'M3'})
        self.assertEqual(status, 404)

    def test_state_authenticated(self):
        status, _ = self.request('/api/state', **{'X-Panel-Token':self.http.token})
        self.assertEqual(status, 200)


class SerialTransportTests(FastHandshakeTests):
    """Exercise real pyserial/Linux TTY code through a virtual PTY, never USB."""
    def setUp(self):
        super().setUp()
        self.master, slave = pty.openpty()
        self.path = os.ttyname(slave)
        os.close(slave)
        self.done = threading.Event()
        self.fake = Simulator()
        self.worker = threading.Thread(target=self.emulate, daemon=True)
        self.worker.start()
        self.c = Controller()
        with patch('server.choices', return_value=[{'device':self.path}]):
            self.c.connect(self.path)
        wait_for(lambda: self.c.snapshot()['fresh'])

    def emulate(self):
        buf = b''
        while not self.done.is_set():
            try:
                ready, _, _ = select.select([self.master], [], [], 0.02)
                if ready:
                    for byte in os.read(self.master, 4096):
                        if byte in (ord('?'), 0x80, 0x85):
                            self.fake.write(bytes([byte]))
                        elif byte == 10:
                            self.fake.write(buf + b'\n')
                            buf = b''
                        else:
                            buf += bytes([byte])
                out = b''
                while not self.fake.q.empty():
                    out += self.fake.q.get_nowait()
                if out:
                    os.write(self.master, out)
            except OSError:
                self.done.wait(0.02)

    def tearDown(self):
        self.c.disconnect()
        self.done.set()
        self.worker.join(timeout=1)
        if self.master >= 0:
            os.close(self.master)

    def test_real_serial_path_handshake_and_jog(self):
        self.assertTrue(self.c.ready)
        self.assertFalse(self.c.simulation)
        self.c.arm('serial-test')
        self.c.jog(dict(client='serial-test',epoch=self.c.control_epoch,axis='Z',distance=-0.1,feed=60))
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.assertAlmostEqual(self.c.position[2], -0.1, places=3)

    def test_exclusive_port(self):
        with self.assertRaises(serial.SerialException):
            serial.Serial(self.path)

    def test_cable_loss_disarms(self):
        self.c.arm('serial-test')
        self.done.set()
        self.worker.join(timeout=1)
        os.close(self.master)
        self.master = -1
        wait_for(lambda: not self.c.snapshot()['connected'])
        self.assertFalse(self.c.armed)
        self.assertFalse(self.c.ready)


if __name__ == '__main__':
    unittest.main()

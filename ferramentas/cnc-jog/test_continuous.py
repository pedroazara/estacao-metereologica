"""Continuous-jog regressions: memory simulator and ephemeral HTTP only.

No test opens a physical serial device or talks to the running lab server.
Run from this directory: python3 -m unittest -v test_continuous
"""
import http.client
import json
import re
import threading
import time
import unittest
from unittest.mock import patch

import server
from simulator import Simulator


def wait_for(predicate, timeout=2.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.015)
    raise AssertionError('Timed out waiting for the in-memory simulator')


class RecordingSimulator(Simulator):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.block_sync = False
        self.defer_jog_ack = False
        self.in_jog_write = False

    def emit(self, text):
        if self.defer_jog_ack and self.in_jog_write and text == 'ok':
            return
        super().emit(text)

    def write(self, data):
        self.sent.append(data)
        if self.block_sync and data in (b'$G\n', b'?', server.STATUS_QUERY):
            return len(data)
        self.in_jog_write = data.startswith(b'$J=')
        try:
            return super().write(data)
        finally:
            self.in_jog_write = False


class ContinuousTests(unittest.TestCase):
    def setUp(self):
        self.sim_patch = patch.object(server, 'Simulator', RecordingSimulator)
        self.sim_patch.start()
        self.addCleanup(self.sim_patch.stop)
        self.c = server.Controller()
        self.addCleanup(self.c.disconnect)
        self.c.connect('SIMULADOR')
        self.link = self.c.ser
        self.client = 'continuous-test-client'
        self.c.arm(self.client)
        self.epoch = self.c.snapshot()['control_epoch']
        self.seq = 0
        self.gesture_number = 0
        self.gesture = self.new_gesture()

    def new_gesture(self):
        self.gesture_number += 1
        return f'gesture-test-{self.gesture_number}'

    def request(self, **changes):
        self.seq += 1
        data = dict(client=self.client, epoch=self.epoch, gesture=self.gesture,
                    seq=self.seq, axis='X', direction=1, feed=60)
        data.update(changes)
        return data

    def motion_commands(self):
        return [data.decode('ascii').strip() for data in self.link.sent
                if data.startswith(b'$J=')]

    def pulse_for(self, seconds, **changes):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and self.c.snapshot()['holding']:
            self.c.beat(self.client)
            try:
                self.c.hold_pulse(self.request(**changes))
            except ValueError:
                # A safety cap can disarm between the snapshot and request.
                if self.c.snapshot()['holding']:
                    raise
            time.sleep(0.075)

    def release(self):
        self.c.hold_stop(self.request())
        wait_for(lambda: not self.c.snapshot()['busy'])

    def assert_rejected_or_ignored(self, method, data):
        before = len(self.motion_commands())
        try:
            result = method(data)
        except ValueError:
            pass
        else:
            self.assertTrue(result and result.get('ignored'), result)
        self.assertEqual(len(self.motion_commands()), before)

    def test_connection_and_reconnection_never_arm_automatically(self):
        self.c.disconnect()
        self.c.connect('SIMULADOR')
        state = self.c.snapshot()
        self.assertFalse(state['armed'])
        self.assertFalse(state['holding'])
        self.assert_rejected_or_ignored(self.c.hold_start, self.request())

    def test_repeated_segments_move_only_while_pulses_continue(self):
        self.c.hold_start(self.request())
        self.pulse_for(0.8)
        self.release()
        commands = self.motion_commands()
        self.assertGreaterEqual(len(commands), 2)
        self.assertGreater(self.c.snapshot()['position'][0], 0.15)
        for command in commands:
            match = re.fullmatch(r'\$J=G91 G21 X([0-9.]+) F60\.0', command)
            self.assertIsNotNone(match, command)
            self.assertLessEqual(float(match[1]), server.HOLD_SEGMENT_SECONDS + 0.001)

    def test_release_stops_without_automatic_resume_and_preserves_arm(self):
        self.c.hold_start(self.request())
        self.pulse_for(0.3)
        self.release()
        state = self.c.snapshot()
        position = state['position'][0]
        count = len(self.motion_commands())
        self.assertFalse(state['holding'])
        self.assertTrue(state['armed'])
        self.assertIn(b'\x85', self.link.sent)
        self.assert_rejected_or_ignored(self.c.hold_pulse, self.request())
        time.sleep(0.2)
        self.assertEqual(len(self.motion_commands()), count)
        self.assertAlmostEqual(self.c.snapshot()['position'][0], position, places=3)

    def test_stop_before_start_blocks_delayed_start(self):
        delayed = self.request()
        self.c.hold_stop(self.request())
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.assert_rejected_or_ignored(self.c.hold_start, delayed)
        self.assert_rejected_or_ignored(self.c.hold_start, self.request())
        self.assertFalse(self.c.snapshot()['holding'])
        self.assertEqual(self.motion_commands(), [])

    def test_new_gesture_after_release_can_start(self):
        self.c.hold_start(self.request())
        self.release()
        self.gesture = self.new_gesture()
        self.c.hold_start(self.request(axis='Z', feed=30))
        self.pulse_for(0.2, axis='Z', feed=30)
        self.release()
        self.assertTrue(any(' Z' in command for command in self.motion_commands()))

    def test_delayed_old_stop_cannot_cancel_a_newer_gesture(self):
        self.c.hold_start(self.request())
        old_stop = self.request()
        self.c.hold_stop(old_stop)
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.gesture = self.new_gesture()
        self.c.hold_start(self.request())
        self.assert_rejected_or_ignored(self.c.hold_stop, old_stop)
        self.assertTrue(self.c.snapshot()['holding'])
        self.assertEqual(self.c.snapshot()['hold_gesture'], self.gesture)
        self.release()

    def test_pulse_without_start_cannot_create_motion(self):
        self.assert_rejected_or_ignored(self.c.hold_pulse, self.request())
        self.assertFalse(self.c.snapshot()['holding'])
        self.assertEqual(self.motion_commands(), [])

    def test_duplicate_and_older_sequence_cannot_update_direction(self):
        start = self.request()
        self.c.hold_start(start)
        self.assert_rejected_or_ignored(self.c.hold_pulse, {**start, 'direction': -1})
        self.assert_rejected_or_ignored(self.c.hold_pulse, {**start, 'seq': 0, 'direction': -1})
        self.assertEqual(self.c.snapshot()['hold_direction'], 1)
        self.release()

    def test_stale_epoch_cannot_start_after_rearm(self):
        old = self.request()
        self.c.cancel()
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.c.arm(self.client)
        self.assertNotEqual(self.c.snapshot()['control_epoch'], self.epoch)
        self.assert_rejected_or_ignored(self.c.hold_start, old)

    def test_other_owner_cannot_start_or_change_active_hold(self):
        self.assert_rejected_or_ignored(self.c.hold_start,
                                       self.request(client='different-test-client'))
        self.c.hold_start(self.request())
        self.assert_rejected_or_ignored(self.c.hold_pulse,
                                       self.request(client='different-test-client', direction=-1))
        self.assertEqual(self.c.snapshot()['hold_direction'], 1)
        self.release()

    def test_invalid_fields_do_not_send_motion(self):
        changes = [dict(axis='A'), dict(axis='X\nM3'), dict(axis=None),
                   dict(direction=0), dict(direction=2), dict(direction=True),
                   dict(direction=float('nan')), dict(direction='-1'),
                   dict(feed=True), dict(feed='60\nM3'), dict(feed=float('nan')),
                   dict(feed=float('inf')), dict(feed=0), dict(feed=601),
                   dict(axis='Z', feed=241), dict(axis='Z', feed=29),
                   dict(seq=True), dict(seq=-1), dict(seq=1.5), dict(seq=2 ** 53),
                   dict(gesture='x'), dict(gesture=None), dict(epoch='invalid-epoch')]
        for change in changes:
            with self.subTest(change=change):
                self.assert_rejected_or_ignored(self.c.hold_start, self.request(**change))
                self.assertFalse(self.c.snapshot()['holding'])
        self.assertEqual(self.motion_commands(), [])

    def test_direction_change_waits_for_current_segment(self):
        self.c.hold_start(self.request())
        self.pulse_for(0.35)
        wait_for(lambda: self.c.snapshot()['position'][0] > 0)
        self.c.hold_pulse(self.request(direction=-1))
        self.pulse_for(0.55, direction=-1)
        self.release()
        commands = self.motion_commands()
        self.assertTrue(any(' X-' in command for command in commands), commands)
        self.assertTrue(any(' X-' not in command for command in commands), commands)

    def test_absolute_distance_cap_disarms_xy(self):
        self.c.hold_start(self.request(feed=600))
        self.pulse_for(3.5, feed=600)
        wait_for(lambda: not self.c.snapshot()['holding'])
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.assertFalse(self.c.snapshot()['armed'])
        self.assertLessEqual(abs(self.c.snapshot()['position'][0]), 10.001)
        self.assertGreater(self.c.snapshot()['position'][0], 0)

    def test_absolute_distance_cap_disarms_z(self):
        self.c.hold_start(self.request(axis='Z', feed=240))
        self.pulse_for(3, axis='Z', feed=240)
        wait_for(lambda: not self.c.snapshot()['holding'])
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.assertFalse(self.c.snapshot()['armed'])
        self.assertLessEqual(abs(self.c.snapshot()['position'][2]), 3.001)
        self.assertGreater(self.c.snapshot()['position'][2], 0)

    def test_reversal_cannot_reset_the_absolute_travel_cap(self):
        self.c.hold_start(self.request(feed=600))
        self.pulse_for(0.4, feed=600)
        self.pulse_for(3, direction=-1, feed=600)
        wait_for(lambda: not self.c.snapshot()['holding'])
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.assertFalse(self.c.snapshot()['armed'])
        distances = [float(re.search(r' X(-?[0-9.]+)', command)[1])
                     for command in self.motion_commands()]
        self.assertTrue(any(value < 0 for value in distances), distances)
        self.assertTrue(any(value > 0 for value in distances), distances)
        self.assertLessEqual(sum(abs(value) for value in distances), 10.001)

    def test_time_cap_disarms_even_with_fresh_pulses(self):
        self.assertEqual(server.HOLD_MAX_SECONDS, 10)
        with patch.object(server, 'HOLD_MAX_SECONDS', 0.4):
            self.c.hold_start(self.request())
            self.pulse_for(0.9)
            wait_for(lambda: not self.c.snapshot()['holding'])
            self.assertFalse(self.c.snapshot()['armed'])

    def test_pulse_expiry_stops_even_when_page_heartbeat_is_fresh(self):
        self.c.hold_start(self.request())
        deadline = time.monotonic() + 1.4
        while time.monotonic() < deadline and self.c.snapshot()['holding']:
            self.c.beat(self.client)
            time.sleep(0.07)
        self.assertFalse(self.c.snapshot()['holding'])
        self.assertFalse(self.c.snapshot()['armed'])
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.assertLessEqual(self.c.snapshot()['position'][0], 0.8)

    def test_discrete_jog_and_continuous_mode_are_mutually_exclusive(self):
        self.c.hold_start(self.request())
        with self.assertRaises(ValueError):
            self.c.jog(dict(client=self.client, epoch=self.epoch, axis='X', distance=0.1, feed=60))
        self.release()
        self.c.jog(dict(client=self.client, epoch=self.epoch, axis='X', distance=1, feed=60))
        with self.assertRaises(ValueError):
            self.c.hold_start(self.request(gesture=self.new_gesture()))
        self.c.cancel()

    def test_escape_cancel_disarms_and_invalidates_gesture(self):
        self.c.hold_start(self.request())
        self.c.cancel()
        wait_for(lambda: not self.c.snapshot()['busy'])
        self.assertFalse(self.c.snapshot()['holding'])
        self.assertFalse(self.c.snapshot()['armed'])
        self.assert_rejected_or_ignored(self.c.hold_pulse, self.request())

    def test_reboot_invalidates_active_hold(self):
        self.c.hold_start(self.request())
        self.c._line("GrblHAL 1.1f ['$' for help]")
        state = self.c.snapshot()
        self.assertFalse(state['holding'])
        self.assertFalse(state['armed'])
        self.assertFalse(state['ready'])
        count = len(self.motion_commands())
        self.assert_rejected_or_ignored(self.c.hold_pulse, self.request())
        time.sleep(0.1)
        self.assertEqual(len(self.motion_commands()), count)

    def test_fault_invalidates_active_hold(self):
        self.c.hold_start(self.request())
        self.c._fault('simulated USB failure')
        state = self.c.snapshot()
        self.assertFalse(state['holding'])
        self.assertFalse(state['armed'])
        self.assertFalse(state['connected'])
        self.assert_rejected_or_ignored(self.c.hold_pulse, self.request())

    def test_spindle_status_during_hold_stops_and_disarms(self):
        self.c.hold_start(self.request())
        self.c._line('<Jog|MPos:0,0,0|FS:60,1000|A:S>')
        wait_for(lambda: not self.c.snapshot()['holding'])
        self.assertFalse(self.c.snapshot()['armed'])

    def test_cancel_requires_marker_ack_and_later_idle_not_old_acks(self):
        with self.c.lock:
            self.link.defer_jog_ack = True
            self.c.hold_start(self.request())
            self.link.block_sync = True
            self.c.hold_stop(self.request())
            self.assertTrue(self.c.snapshot()['busy'])
            self.c._line('ok')  # delayed acknowledgement for the canceled JOG
            self.c._line('<Idle|MPos:0,0,0|FS:0,0>')
            self.assertTrue(self.c.snapshot()['busy'])
            self.c._line('[GC:G0 G54 G17 G21 G90 G94 G49 M5 M9 T0 F0 S0]')
            self.c._line('ok')
            self.assertTrue(self.c.snapshot()['busy'])
            self.c._line('<Idle|MPos:0,0,0|FS:0,0>')
            self.assertFalse(self.c.snapshot()['busy'])
            self.assertTrue(self.c.snapshot()['armed'])
            self.link.block_sync = False

    def test_no_next_segment_until_acknowledgement_and_later_idle(self):
        with self.c.lock:
            self.link.defer_jog_ack = True
            self.link.block_sync = True
            self.c.hold_start(self.request())
        self.pulse_for(0.2)
        self.assertEqual(len(self.motion_commands()), 1)
        self.c._line('ok')
        self.pulse_for(0.2)
        self.assertEqual(len(self.motion_commands()), 1)
        with self.c.lock:
            self.link.update()
            position = ','.join(str(value) for value in self.link.position)
            self.c._line(f'<Idle|MPos:{position}|FS:0,0>')
        wait_for(lambda: len(self.motion_commands()) == 2)
        self.link.block_sync = False
        self.release()

    def test_early_idle_after_ack_does_not_complete_a_new_segment(self):
        with self.c.lock:
            self.link.defer_jog_ack = True
            self.link.block_sync = True
            self.c.hold_start(self.request())
            self.c._line('ok')
            # A queued pre-jog Idle can arrive after the acknowledgement.
            # Stretch the guard deterministically to avoid scheduler timing.
            self.c.motion_not_before = time.monotonic() + 5
            self.c._line('<Idle|MPos:0,0,0|FS:0,0>')
            self.assertTrue(self.c.snapshot()['busy'])
            self.assertEqual(len(self.motion_commands()), 1)
            self.link.block_sync = False
        # Cancellation must bypass that normal-completion duration guard.
        self.release()

    def test_cancel_sync_timeout_fails_closed(self):
        with patch.object(server, 'CANCEL_SYNC_SECONDS', 0.15):
            with self.c.lock:
                self.c.hold_start(self.request())
                self.link.block_sync = True
                self.c.hold_stop(self.request())
            wait_for(lambda: not self.c.snapshot()['ready'])
        state = self.c.snapshot()
        self.assertFalse(state['holding'])
        self.assertFalse(state['armed'])
        self.assert_rejected_or_ignored(self.c.hold_start,
                                       self.request(gesture=self.new_gesture()))

    def test_cancel_keeps_polling_through_deceleration_until_idle(self):
        with self.c.lock:
            self.c.hold_start(self.request())
            self.link.block_sync = True
            self.c.hold_stop(self.request())
            self.c._line('[GC:G0 G54 G17 G21 G90 G94 G49 M5 M9 T0 F0 S0]')
            self.c._line('ok')
            self.c._line('<Jog|MPos:0,0,0|FS:20,0>')
            self.assertTrue(self.c.snapshot()['busy'])
            status_requests = self.link.sent.count(server.STATUS_QUERY)
            self.link.block_sync = False
        wait_for(lambda: not self.c.snapshot()['busy'], timeout=1)
        self.assertGreater(self.link.sent.count(server.STATUS_QUERY), status_requests)
        self.assertTrue(self.c.snapshot()['ready'])
        self.assertTrue(self.c.snapshot()['armed'])


class ContinuousHTTPTests(unittest.TestCase):
    def setUp(self):
        self.http = server.make_server(0)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.http.controller.connect('SIMULADOR')
        self.client = 'http-continuous-client'
        self.http.controller.arm(self.client)
        self.data = dict(client=self.client, epoch=self.http.controller.snapshot()['control_epoch'],
                         gesture='http-gesture-test', seq=1, axis='Z', direction=1, feed=30)

    def tearDown(self):
        self.http.controller.disconnect()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=1)

    def post(self, path, data, token=True, origin=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Panel-Token'] = self.http.token
        if origin:
            headers['Origin'] = origin
        connection.request('POST', path, json.dumps(data), headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_hold_routes_accept_authenticated_local_requests(self):
        for seq, operation in enumerate(('start', 'pulse', 'stop'), start=1):
            status, result = self.post('/api/hold/' + operation, {**self.data, 'seq': seq})
            self.assertEqual(status, 200, result)
        wait_for(lambda: not self.http.controller.snapshot()['busy'])
        self.assertTrue(self.http.controller.snapshot()['armed'])

    def test_hold_routes_reject_missing_token_and_cross_origin(self):
        for operation in ('start', 'pulse', 'stop'):
            with self.subTest(operation=operation):
                status, _ = self.post('/api/hold/' + operation, self.data, token=False)
                self.assertEqual(status, 403)
                status, _ = self.post('/api/hold/' + operation, self.data,
                                      origin='https://untrusted.example')
                self.assertEqual(status, 403)
        self.assertFalse(self.http.controller.snapshot()['holding'])


if __name__ == '__main__':
    unittest.main()

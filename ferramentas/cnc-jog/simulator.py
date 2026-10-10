"""Small deterministic grblHAL simulator. Never opens a device or network port."""
import math
import queue
import re
import threading
import time


class Simulator:
    def __init__(self):
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.closed = False
        self.position = [0.0, 0.0, 0.0]
        self.move = None

    @property
    def in_waiting(self):
        return 0

    def emit(self, text):
        for byte in (text + '\r\n').encode('ascii'):
            self.q.put(bytes([byte]))

    def read(self, count=1):
        try:
            return self.q.get(timeout=0.1)
        except queue.Empty:
            return b''

    def update(self):
        if self.move:
            axis, start, delta, begin, duration, feed = self.move
            fraction = min(1, (time.monotonic() - begin) / duration)
            self.position[axis] = start + delta * fraction
            if fraction >= 1:
                self.move = None

    def write(self, data):
        with self.lock:
            if self.closed:
                raise OSError('Simulador desconectado.')
            self.update()
            if data in (b'?', b'\x80'):
                state = 'Jog' if self.move else 'Idle'
                pos = ','.join(f'{p:.3f}' for p in self.position)
                feed = self.move[-1] if self.move else 0
                self.emit(f'<{state}|MPos:{pos}|FS:{feed},0|Pn:HSP|H:0,0>')
            elif data == b'\x85':
                self.move = None
            else:
                command = data.decode('ascii').strip()
                if command == '$I':
                    self.emit('[VER:1.1f.SIMULADOR:]\n[FIRMWARE:grblHAL]\n[BOARD:BTT SKR V1.4 Turbo]\n[KINEMATICS:CoreXZ v2.02]\n[AXS:3:XYZ]\nok')
                elif command == '$G':
                    self.emit('[GC:G0 G54 G17 G21 G90 G94 G49 M5 M9 T0 F0 S0]\nok')
                elif command == '$$':
                    self.emit('$13=0\n$20=0\n$21=0\n$40=0\n$100=80.080\n$101=80.000\n$102=80.080\n$110=10000\n$111=10000\n$112=10000\nok')
                else:
                    match = re.fullmatch(r'\$J=G91 G21 ([XYZ])(-?[0-9.]+) F([0-9.]+)', command)
                    if not match or self.move:
                        self.emit('error:16')
                    else:
                        axis = 'XYZ'.index(match[1])
                        delta, feed = float(match[2]), float(match[3])
                        if not all(math.isfinite(n) for n in (delta, feed)) or feed <= 0:
                            self.emit('error:2')
                        else:
                            self.move = (axis, self.position[axis], delta, time.monotonic(), abs(delta) * 60 / feed, feed)
                            self.emit('ok')
        return len(data)

    def close(self):
        self.closed = True

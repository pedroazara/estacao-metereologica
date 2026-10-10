#!/usr/bin/env python3
"""Local, single-command jog pendant for the lab's grblHAL SKR.

Run: python3 server.py. No motion on startup or connection.
Only fixed read queries, validated $J moves, and realtime jog cancel are sent.
"""
import argparse
import glob
import json
import math
import os
from pathlib import Path
import secrets
import signal
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import serial
from serial.tools.list_ports import comports
from simulator import Simulator

ROOT = Path(__file__).resolve().parent
FRESH_SECONDS = 1.5
LEASE_SECONDS = 2.0
STATUS_QUERY = b'\x80'  # grblHAL native, non-printable realtime status request.
STARTUP_SETTLE_SECONDS = 1.2
CONNECT_TIMEOUT_SECONDS = 10.0
HOLD_LEASE_SECONDS = 0.6
HOLD_SEGMENT_SECONDS = 0.15
HOLD_MAX_SECONDS = 10.0
HOLD_MAX_XY = 10.0
HOLD_MAX_Z = 3.0
CANCEL_SYNC_SECONDS = 3.5


class StartupBanner(ValueError):
    """The USB welcome/restart invalidated an in-progress read-only handshake."""


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def ports():
    return [{"device": p.device, "description": p.description}
            for p in comports() if (p.vid, p.pid) == (0x1FC9, 0x0083)]


def choices():
    return ports() + [{'device': 'SIMULADOR', 'description': 'Demonstração — sem máquina física'}]


def occupied(device):
    """Check before TIOCEXCL: screen/other monitors may not use flock."""
    target = os.path.realpath(device)
    for fd in glob.glob('/proc/[0-9]*/fd/*'):
        try:
            if os.path.realpath(fd) == target:
                return True
        except OSError:
            pass
    return False


class Controller:
    def __init__(self):
        self.lock = threading.RLock()
        self.lifecycle = threading.Lock()
        self.ser = None
        self.stop = None
        self.threads = []
        self.logs = deque(maxlen=160)
        self.counter = 0
        self._clear()

    def _clear(self):
        self.connected = False
        self.ready = False
        self.initializing = False
        self.generation = 0
        self.status_event = threading.Event()
        self.device = ''
        self.simulation = False
        self.machine = 'Offline'
        self.last_status = 0.0
        self.position = None
        self.feed = 0.0
        self.rpm = None
        self.accessories = ''
        self.pins = ''
        self.info = {}
        self.settings = {}
        self.parser = ''
        self.homed = None
        self.armed = False
        self.owner = ''
        self.heartbeat = 0.0
        self.pending = None
        self.motion = False
        self.motion_ack = False
        self.motion_deadline = 0.0
        self.motion_not_before = 0.0
        self.control_epoch = ''
        self.hold = None
        self.hold_seq = -1
        self.hold_seen = set()
        self.hold_used_xy = 0.0
        self.hold_used_z = 0.0
        self.message = 'Conecte a placa para ler o estado.'

    def log(self, direction, line):
        with self.lock:
            self.counter += 1
            self.logs.append(dict(id=self.counter, time=time.strftime('%H:%M:%S'),
                                  direction=direction, text=line))

    def _write(self, data):
        # Caller holds self.lock, serial writes cannot interleave.
        if not self.connected or self.ser is None:
            raise ValueError('USB desconectado.')
        self.ser.write(data)
        if data not in (b'?', STATUS_QUERY):
            self.log('tx', 'Cancelar JOG [0x85]' if data == b'\x85' else data.decode('ascii').strip())

    def _disarm(self):
        self.armed = False
        self.owner = ''
        self.control_epoch = ''
        self.hold = None

    def _begin_cancel(self):
        """Fence old acknowledgements before allowing any subsequent motion.

        Realtime cancel has no acknowledgement. A following read-only $G gives
        an ordered [GC:] marker, its own ok, and then a newly requested Idle.
        An old jog's ok or an already-buffered Idle cannot release this fence.
        """
        if not self.connected:
            return
        if self.pending and self.pending['kind'] == 'cancel_sync':
            return
        self._write(b'\x85')
        if not self.ready or self.initializing:
            self._write(STATUS_QUERY)
            return
        if self.pending:
            self.pending['error'] = 'JOG cancelado.'
            self.pending['event'].set()
        self.pending = dict(event=threading.Event(), error=None,
                            kind='cancel_sync', marker=False, ack=False,
                            deadline=time.monotonic() + CANCEL_SYNC_SECONDS)
        self.motion_ack = False
        self._write(b'$G\n')

    def _cancel(self, reason):
        self._disarm()
        self.message = reason
        self._begin_cancel()

    def cancel(self, reason='JOG cancelado; controles bloqueados.'):
        with self.lock:
            self._cancel(reason)

    def _fault(self, reason):
        with self.lock:
            self.message = reason
            self._disarm()
            self.ready = False
            self.initializing = False
            self.connected = False
            self.status_event.set()
            if self.stop:
                self.stop.set()
            if self.pending:
                self.pending['error'] = reason
                self.pending['event'].set()
            self.log('event', reason)

    def connect(self, device):
        with self.lifecycle:
            if device not in {p['device'] for p in choices()}:
                raise ValueError('Selecione a porta NXP/SKR detectada neste computador.')
            with self.lock:
                if self.connected:
                    raise ValueError('A placa já está conectada.')
            self._close()
            if device != 'SIMULADOR' and occupied(device):
                raise ValueError('Porta em uso. Feche a conexão do monitor serial antes de conectar.')
            with self.lock:
                self._clear()
                self.simulation = device == 'SIMULADOR'
                if self.simulation:
                    link = Simulator()
                else:
                    link = serial.Serial(port=None, baudrate=115200, timeout=0.1,
                                         write_timeout=0.3, exclusive=True)
                    # Native LPC USB CDC uses DTR as "host connected". Leaving
                    # it low suppresses broadcast status even when $I/$$ work.
                    # Set it once; never pulse it or send a firmware reset.
                    link.dtr = True
                    link.rts = False
                    link.port = device
                    try:
                        link.open()
                        import fcntl
                        import termios
                        fcntl.ioctl(link.fileno(), termios.TIOCEXCL)
                    except Exception:
                        link.close()
                        raise
                self.ser = link
                self.connected = True
                self.initializing = True
                self.message = 'Inicializando comunicação USB; controles bloqueados.'
                self.device = device
                self.stop = threading.Event()
                self.threads = [threading.Thread(target=self._reader, args=(link, self.stop), daemon=True),
                                threading.Thread(target=self._watchdog, args=(self.stop,), daemon=True)]
                for thread in self.threads:
                    thread.start()
            try:
                deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
                # LPC firmware can send a welcome after the port opens. This
                # is not evidence of a physical reboot. Consume it before the
                # handshake; any later banner during initialization retries
                # all read queries instead of trusting pre-banner metadata.
                if not self.simulation:
                    self.stop.wait(STARTUP_SETTLE_SECONDS)
                self._handshake(deadline)
            except Exception as exc:
                self._close()
                with self.lock:
                    self.message = str(exc)
                    self.log('event', self.message)
                raise

    def _invalidate_metadata(self):
        self.info = {}
        self.settings = {}
        self.parser = ''
        self.position = None
        self.rpm = None
        self.feed = 0.0
        self.accessories = ''
        self.pins = ''
        self.homed = None
        self.last_status = 0.0
        self.machine = 'Offline'

    def _handshake(self, deadline):
        for _ in range(3):
            if time.monotonic() >= deadline:
                break
            with self.lock:
                if not self.connected:
                    raise ValueError(self.message)
                generation = self.generation
                self._invalidate_metadata()
            try:
                self._query('$I', generation, deadline)
                with self.lock:
                    if self.generation != generation:
                        raise StartupBanner()
                    if self.info.get('FIRMWARE', '').lower() != 'grblhal' or self.info.get('BOARD') != 'BTT SKR V1.4 Turbo':
                        raise ValueError('A identificação não corresponde à SKR grblHAL esperada.')
                self._query('$G', generation, deadline)
                self._query('$$', generation, deadline)
                with self.lock:
                    if self.generation != generation:
                        raise StartupBanner()
                    if not self.parser or '13' not in self.settings:
                        raise ValueError('Resposta de inicialização incompleta. Reconecte o painel.')
                    self.last_status = 0.0
                    self.position = None
                    self.rpm = None
                    self.status_event.clear()
                    self._write(STATUS_QUERY)
                # Only a parsed status received AFTER the queries can finish
                # initialization. No automatic enabling of motion, ever.
                while time.monotonic() < deadline:
                    self.status_event.wait(min(0.25, max(0, deadline - time.monotonic())))
                    with self.lock:
                        self.status_event.clear()
                        if not self.connected:
                            raise ValueError(self.message)
                        if self.generation != generation:
                            raise StartupBanner()
                        if (self.position is not None and self.rpm is not None and
                                0 < self.last_status and time.monotonic() - self.last_status < FRESH_SECONDS):
                            self.initializing = False
                            self.ready = True
                            self.message = 'Conectado. Confira a área e habilite os controles para mover.'
                            self.log('event', 'Comunicação validada; controles permanecem bloqueados.')
                            return
                raise ValueError('A placa respondeu às consultas, mas não confirmou o estado atual. Controles bloqueados; confira a conexão USB.')
            except StartupBanner:
                # Allow trailing replies from the invalidated query to drain.
                self.stop.wait(min(0.25, max(0, deadline - time.monotonic())))
        raise ValueError('A inicialização USB não estabilizou. Controles bloqueados; reconecte após verificar a máquina.')

    def _close(self):
        with self.lock:
            if self.connected and (self.motion or self.machine == 'Jog'):
                try:
                    self._cancel('Conexão encerrada; cancelamento de JOG enviado.')
                except Exception:
                    pass
            self._disarm()
            self.ready = False
            self.initializing = False
            self.connected = False
            self.status_event.set()
            if self.stop:
                self.stop.set()
            if self.pending:
                self.pending['error'] = 'Conexão encerrada.'
                self.pending['event'].set()
            link, threads = self.ser, self.threads
        for thread in threads:
            if thread != threading.current_thread():
                thread.join(timeout=1)
        with self.lock:
            if link:
                link.close()
            self.ser = None
            self.pending = None
            self.motion = False
            self.owner = ''
            self.machine = 'Offline'
            self.message = 'Desconectado.'

    def disconnect(self):
        with self.lifecycle:
            self._close()

    def _query(self, command, generation=None, deadline=None):
        assert command in ('$I', '$G', '$$')
        with self.lock:
            if generation is not None and self.generation != generation:
                raise StartupBanner()
            if self.pending:
                raise ValueError('Aguarde a resposta anterior.')
            pending = dict(event=threading.Event(), error=None, kind='query')
            self.pending = pending
            self._write((command + '\n').encode('ascii'))
        timeout = min(4, max(0, deadline - time.monotonic())) if deadline else 4
        if not pending['event'].wait(timeout):
            raise ValueError('A placa não respondeu à consulta. Confira o USB e o firmware.')
        with self.lock:
            if generation is not None and self.generation != generation:
                raise StartupBanner()
        if pending['error']:
            raise ValueError(pending['error'])

    def _reader(self, link, stop):
        buf = b''
        while not stop.is_set():
            try:
                buf += link.read(link.in_waiting or 1)
                while b'\n' in buf:
                    raw, buf = buf.split(b'\n', 1)
                    line = raw.decode('ascii', errors='replace').strip()
                    if line:
                        self._line(line)
                if len(buf) > 16384:
                    raise ValueError('Resposta serial inválida.')
            except (OSError, serial.SerialException, ValueError) as exc:
                if not stop.is_set():
                    self._fault('USB indisponível: ' + str(exc))
                break

    def _line(self, line):
        with self.lock:
            if line.startswith('<') and line.endswith('>'):
                parts = line[1:-1].split('|')
                previous = self.machine
                self.machine = parts[0].split(':')[0]
                fields = dict(p.split(':', 1) for p in parts[1:] if ':' in p)
                if 'MPos' in fields:
                    position = [float(v) for v in fields['MPos'].split(',')]
                    if len(position) != 3 or not all(math.isfinite(v) for v in position):
                        raise ValueError('Posição serial inválida.')
                    self.position = position
                if 'FS' in fields:
                    fs = fields['FS'].split(',')
                    if len(fs) < 2:
                        raise ValueError('Estado do spindle ausente.')
                    self.feed, self.rpm = float(fs[0]), float(fs[1])
                    if not all(math.isfinite(v) and v >= 0 for v in (self.feed, self.rpm)):
                        raise ValueError('Avanço/rotação serial inválidos.')
                if previous != self.machine or not self.last_status:
                    self.log('rx', line)
                self.last_status = time.monotonic()
                self.status_event.set()
                self.accessories = fields.get('A', '')
                self.pins = fields.get('Pn', '')
                if 'H' in fields:
                    self.homed = fields['H']
                syncing = self.pending and self.pending['kind'] == 'cancel_sync'
                if syncing and self.pending['ack'] and self.machine == 'Idle':
                    self.pending['event'].set()
                    self.pending = None
                    self.motion = False
                    self.motion_ack = False
                    if self.armed and not self.hold:
                        self.message = 'Parada confirmada. Segure novamente para mover.'
                elif (not syncing and self.motion and self.motion_ack and self.machine == 'Idle'
                      and self.last_status >= self.motion_not_before):
                    self.motion = False
                    self.motion_ack = False
                if self.machine not in ('Idle', 'Jog') and self.armed:
                    self._cancel('Estado ' + self.machine + ': controles bloqueados.')
                elif self.armed and not self._spindle_off():
                    self._cancel('Spindle não confirmado desligado; controles bloqueados.')
                return
            self.log('rx', line)
            if line.startswith('[GC:'):
                self.parser = line[4:-1]
                if self.pending and self.pending['kind'] == 'cancel_sync':
                    self.pending['marker'] = True
                if self.armed and not self._spindle_off():
                    self._cancel('Spindle não confirmado desligado; controles bloqueados.')
            elif line.startswith('[') and ':' in line and line.endswith(']'):
                key, value = line[1:-1].split(':', 1)
                self.info[key] = value
            elif line.startswith('$') and '=' in line:
                key, value = line[1:].split('=', 1)
                if key.isdigit():
                    self.settings[key] = value
            if line.startswith('Grbl'):
                self.generation += 1
                self.ready = False
                self._disarm()
                self.motion = False
                self.motion_ack = False
                self._invalidate_metadata()
                if self.pending:
                    self.pending['error'] = 'A sessão USB mudou durante a consulta.'
                    self.pending['event'].set()
                    self.pending = None
                self.status_event.set()
                self.message = ('Mensagem inicial recebida; validando novamente a comunicação.' if self.initializing else
                                'A sessão da placa reiniciou ou mudou. Desconecte e reconecte o painel; controles bloqueados.')
            if line.startswith('ALARM:'):
                self.machine = 'Alarm'
                self._cancel(line + ' — verifique a máquina.')
            if line == 'ok' or line.startswith('error:'):
                pending = self.pending
                if pending:
                    if pending['kind'] == 'cancel_sync':
                        # Ignore the cancelled jog's trailing response. Only
                        # the response after $G's marker belongs to the fence.
                        if not pending['marker']:
                            return
                        if line != 'ok':
                            self._cancel_sync_failed('Cancelamento sem confirmação: ' + line)
                        elif not pending['ack']:
                            pending['ack'] = True
                            self._write(STATUS_QUERY)
                        return
                    pending['error'] = line if line != 'ok' else None
                    self.pending = None
                    if pending['kind'] == 'jog':
                        if pending['error']:
                            self.motion = False
                            self._cancel('Comando recusado: ' + line)
                        else:
                            self.motion_ack = True
                            self._write(STATUS_QUERY)
                    pending['event'].set()

    def _watchdog(self, stop):
        while not stop.wait(0.05):
            with self.lock:
                if not self.connected:
                    return
                try:
                    now = time.monotonic()
                    if self.armed and (now - self.heartbeat > LEASE_SECONDS or now - self.last_status > FRESH_SECONDS):
                        self._cancel('Painel ou USB sem resposta. JOG cancelado; habilite novamente.')
                    if self.hold and now - self.hold['pulse'] > HOLD_LEASE_SECONDS:
                        self._cancel('Controle contínuo sem sinal. JOG cancelado; habilite novamente.')
                    if self.hold and now - self.hold['started'] >= HOLD_MAX_SECONDS:
                        self._cancel('Limite de 10 segundos atingido. Solte o controle e habilite novamente.')
                    syncing = self.pending and self.pending['kind'] == 'cancel_sync'
                    if syncing and now > self.pending['deadline']:
                        self._cancel_sync_failed('Cancelamento não confirmado. Confira a máquina e reconecte.')
                    if self.motion and not syncing and now > self.motion_deadline:
                        self._cancel('Movimento sem confirmação final. Reconecte após verificar a máquina.')
                        self.ready = False
                    if self.hold and not self.motion and not self.pending:
                        self._advance_hold()
                    # Poll after $G's fence acknowledgement too: a real axis
                    # may still be decelerating when its first status arrives.
                    # No polling while an ordinary command awaits its ack.
                    if not self.pending or (self.pending['kind'] == 'cancel_sync' and self.pending['ack']):
                        self._write(STATUS_QUERY)
                except (OSError, serial.SerialException, ValueError) as exc:
                    self._fault('Falha na comunicação: ' + str(exc))
                    return

    def _cancel_sync_failed(self, reason):
        self._disarm()
        self.ready = False
        self.message = reason
        if self.pending:
            self.pending['error'] = reason
            self.pending['event'].set()
            self.pending = None
        self.motion = False
        self.motion_ack = False
        self.log('event', reason)

    def _spindle_off(self):
        return self.rpm == 0 and not any(c in self.accessories for c in 'SC') and 'M5' in self.parser.split()

    def _can_move(self, within_hold=False):
        if not self.connected or not self.ready:
            raise ValueError('Conecte e identifique a placa primeiro.')
        if time.monotonic() - self.last_status > FRESH_SECONDS:
            raise ValueError('Estado da placa desatualizado. Aguarde a comunicação.')
        if self.settings.get('13') != '0':
            raise ValueError('Relato de posição não está em mm. Revise $13 fora deste painel.')
        if self.machine != 'Idle' or self.motion or self.pending or (self.hold and not within_hold):
            raise ValueError('Aguarde a máquina ficar Idle antes de outro movimento.')
        if not self._spindle_off():
            raise ValueError('O firmware não confirma spindle desligado.')

    def arm(self, client):
        with self.lock:
            self._can_move()
            if not isinstance(client, str) or not 8 <= len(client) <= 80:
                raise ValueError('Identificação do painel inválida.')
            if self.armed and self.owner != client:
                raise ValueError('Outra aba está controlando a CNC. Cancele antes de assumir.')
            self.owner = client
            self.heartbeat = time.monotonic()
            self.armed = True
            self.control_epoch = secrets.token_urlsafe(24)
            self.hold_seq = -1
            self.hold_seen = set()
            self.message = 'JOG habilitado. Confira o modo de controle antes de mover.'
            return self.control_epoch

    def beat(self, client, epoch=None):
        with self.lock:
            if self.armed and self.owner == client and (epoch is None or epoch == self.control_epoch):
                self.heartbeat = time.monotonic()

    def _hold_identity(self, data):
        if (not self.armed or self.owner != data.get('client') or
                not self.control_epoch or self.control_epoch != data.get('epoch')):
            raise ValueError('Sessão de controle inválida. Habilite os controles nesta aba.')
        gesture, seq = data.get('gesture'), data.get('seq')
        if not isinstance(gesture, str) or not 8 <= len(gesture) <= 80:
            raise ValueError('Identificação do gesto inválida.')
        if isinstance(seq, bool) or not isinstance(seq, int) or not 0 <= seq <= 9007199254740991:
            raise ValueError('Sequência de controle inválida.')
        return gesture, seq

    def _hold_config(self, data):
        axis, direction, feed = data.get('axis'), data.get('direction'), data.get('feed')
        if axis not in ('X', 'Y', 'Z'):
            raise ValueError('Eixo inválido.')
        if isinstance(direction, bool) or not isinstance(direction, (int, float)) or direction not in (-1, 1):
            raise ValueError('Direção deve ser +1 ou -1.')
        if not finite_number(feed):
            raise ValueError('Velocidade deve ser um número finito.')
        minimum, maximum = (30, 240) if axis == 'Z' else (60, 600)
        if not minimum <= feed <= maximum:
            raise ValueError(f'Velocidade em {axis}: {minimum}–{maximum} mm/min.')
        # Quantize before calculating segment distance, so the formatted
        # command cannot take longer than the 150 ms target at a rounded feed.
        return axis, int(direction), round(feed, 1)

    def _hold_result(self, ignored=False):
        return dict(holding=self.hold is not None, ignored=ignored,
                    gesture=self.hold['gesture'] if self.hold else '', seq=self.hold_seq)

    def hold_start(self, data):
        with self.lock:
            gesture, seq = self._hold_identity(data)
            axis, direction, feed = self._hold_config(data)
            if seq <= self.hold_seq:
                return self._hold_result(ignored=True)
            self.hold_seq = seq
            if gesture in self.hold_seen:
                return self._hold_result(ignored=True)
            # A start is a one-use request, even if it arrived while another
            # movement was ending. Retrying requires a new physical gesture.
            self.hold_seen.add(gesture)
            self._can_move()
            now = time.monotonic()
            if now - self.heartbeat > LEASE_SECONDS:
                raise ValueError('Habilite os controles novamente antes de mover.')
            self.heartbeat = now
            self.hold_used_xy = 0.0
            self.hold_used_z = 0.0
            self.hold = dict(gesture=gesture, axis=axis, direction=direction,
                             feed=feed, started=now, pulse=now)
            self.message = 'Movimento contínuo: solte para parar. Um eixo por vez.'
            self._advance_hold()
            return self._hold_result()

    def hold_pulse(self, data):
        with self.lock:
            gesture, seq = self._hold_identity(data)
            axis, direction, feed = self._hold_config(data)
            if seq <= self.hold_seq:
                return self._hold_result(ignored=True)
            self.hold_seq = seq
            # A pulse can only renew an already accepted gesture. A delayed
            # pulse after release can never start or replace any movement.
            if not self.hold or gesture != self.hold['gesture']:
                return self._hold_result(ignored=True)
            now = time.monotonic()
            if now - self.hold['pulse'] > HOLD_LEASE_SECONDS:
                self._cancel('Controle contínuo sem sinal. JOG cancelado; habilite novamente.')
                return self._hold_result(ignored=True)
            if now - self.hold['started'] >= HOLD_MAX_SECONDS:
                self._cancel('Limite de 10 segundos atingido. Solte o controle e habilite novamente.')
                return self._hold_result(ignored=True)
            if not self.ready or now - self.last_status > FRESH_SECONDS or not self._spindle_off():
                self._cancel('Estado da máquina não confirmado. Controles bloqueados.')
                return self._hold_result(ignored=True)
            self.hold.update(axis=axis, direction=direction, feed=feed, pulse=now)
            self.heartbeat = now
            return self._hold_result()

    def hold_stop(self, data):
        with self.lock:
            gesture, seq = self._hold_identity(data)
            if seq <= self.hold_seq:
                return self._hold_result(ignored=True)
            self.hold_seq = seq
            # Tombstone even when stop beats start across HTTP connections.
            # A delayed start for this gesture remains invalid forever in the
            # current arm epoch, including if it has a larger sequence.
            self.hold_seen.add(gesture)
            if not self.hold or gesture != self.hold['gesture']:
                return self._hold_result(ignored=True)
            if time.monotonic() - self.hold['pulse'] > HOLD_LEASE_SECONDS:
                self._cancel('Controle contínuo sem sinal. JOG cancelado; habilite novamente.')
                return self._hold_result()
            if time.monotonic() - self.hold['started'] >= HOLD_MAX_SECONDS:
                self._cancel('Limite de 10 segundos atingido. Solte o controle e habilite novamente.')
                return self._hold_result()
            self.hold = None
            self.message = 'Controle solto. Aguardando confirmação de parada.'
            self._begin_cancel()
            return self._hold_result()

    def _advance_hold(self):
        """Send at most one short segment; called only under self.lock."""
        if not self.hold:
            return
        try:
            self._can_move(within_hold=True)
        except ValueError:
            self._cancel('Movimento contínuo interrompido: estado da máquina não confirmado.')
            return
        axis, feed = self.hold['axis'], self.hold['feed']
        seconds_left = HOLD_MAX_SECONDS - (time.monotonic() - self.hold['started'])
        if seconds_left <= 0:
            self._cancel('Limite de 10 segundos atingido. Solte o controle e habilite novamente.')
            return
        remaining = (HOLD_MAX_Z - self.hold_used_z if axis == 'Z'
                     else HOLD_MAX_XY - self.hold_used_xy)
        distance = math.floor(min(feed / 60 * min(HOLD_SEGMENT_SECONDS, seconds_left), remaining) * 1000 + 1e-9) / 1000
        if distance < 0.001:
            self._cancel('Limite de percurso contínuo atingido. Solte o controle e habilite novamente.')
            return
        if axis == 'Z':
            self.hold_used_z = round(self.hold_used_z + distance, 3)
        else:
            self.hold_used_xy = round(self.hold_used_xy + distance, 3)
        self._send_jog(axis, distance * self.hold['direction'], feed)

    def _send_jog(self, axis, distance, feed):
        command = f'$J=G91 G21 {axis}{distance:.3f} F{feed:.1f}'
        self.pending = dict(event=threading.Event(), error=None, kind='jog')
        self.motion = True
        self.motion_ack = False
        # A queued pre-command Idle cannot complete a new movement just after
        # its ok. Even at full feed the commanded travel takes this long; wait
        # for a later status as well as the acknowledgement before advancing.
        self.motion_not_before = time.monotonic() + abs(distance) / feed * 60
        self.motion_deadline = self.motion_not_before + 4
        try:
            self._write((command + '\n').encode('ascii'))
        except Exception:
            self._fault('Falha ao enviar JOG. Confira a máquina antes de reconectar.')
            raise
        return command

    def jog(self, data):
        with self.lock:
            self._can_move()
            if (not self.armed or self.owner != data.get('client') or
                    not self.control_epoch or data.get('epoch') != self.control_epoch or
                    time.monotonic() - self.heartbeat > LEASE_SECONDS):
                raise ValueError('Habilite os controles nesta aba antes de mover.')
            axis = data.get('axis')
            if axis not in ('X', 'Y', 'Z'):
                raise ValueError('Eixo inválido.')
            distance, feed = data.get('distance'), data.get('feed')
            if not all(finite_number(v) for v in (distance, feed)):
                raise ValueError('Distância e velocidade devem ser números finitos.')
            max_distance, max_feed, min_feed = (2, 240, 30) if axis == 'Z' else (5, 600, 60)
            if not 0.01 <= abs(distance) <= max_distance or not min_feed <= feed <= max_feed:
                raise ValueError(f'Limites do painel em {axis}: até {max_distance} mm; {min_feed}–{max_feed} mm/min.')
            return self._send_jog(axis, distance, feed)

    def snapshot(self):
        with self.lock:
            age = time.monotonic() - self.last_status if self.last_status else None
            return dict(connected=self.connected, ready=self.ready, initializing=self.initializing, device=self.device, simulation=self.simulation,
                        state=self.machine, fresh=age is not None and age < FRESH_SECONDS,
                        position=self.position, feed=self.feed, rpm=self.rpm, pins=self.pins,
                        homed=self.homed, info=dict(self.info), settings=dict(self.settings),
                        armed=self.armed, owner=self.owner, busy=self.motion or self.pending is not None,
                        control_epoch=self.control_epoch, holding=self.hold is not None,
                        hold_gesture=self.hold['gesture'] if self.hold else '', hold_seq=self.hold_seq,
                        hold_axis=self.hold['axis'] if self.hold else '',
                        hold_direction=self.hold['direction'] if self.hold else 0,
                        hold_used_xy=self.hold_used_xy, hold_used_z=self.hold_used_z,
                        hold_limits=dict(seconds=HOLD_MAX_SECONDS, xy_mm=HOLD_MAX_XY,
                                         z_mm=HOLD_MAX_Z, lease_ms=HOLD_LEASE_SECONDS * 1000),
                        message=self.message, parser=self.parser, logs=list(self.logs))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def allowed(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        origin = self.headers.get('Origin')
        return (self.headers.get('Host') in hosts and
                (not origin or origin in {f'http://{h}' for h in hosts}) and
                self.headers.get('Sec-Fetch-Site') != 'cross-site')

    def send(self, status, body, mime='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {'error': 'Acesso somente pelo painel local.'})
        if self.path == '/':
            html = (ROOT / 'index.html').read_text().replace('__TOKEN__', self.server.token)
            return self.send(200, html, 'text/html; charset=utf-8')
        files = {'/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css'),
                 '/report.css': ('report.css', 'text/css'),
                 '/layout.css': ('layout.css', 'text/css'),
                 '/continuous.css': ('continuous.css', 'text/css'),
                 '/relatorio.html': ('relatorio.html', 'text/html')}
        if self.path in files:
            filename, mime = files[self.path]
            return self.send(200, (ROOT / filename).read_bytes(), mime + '; charset=utf-8')
        if self.headers.get('X-Panel-Token') != self.server.token:
            return self.send(403, {'error': 'Reabra a página do painel.'})
        if self.path == '/api/state':
            return self.send(200, self.server.controller.snapshot())
        if self.path == '/api/ports':
            return self.send(200, choices())
        return self.send(404, {'error': 'Página não encontrada.'})

    def do_POST(self):
        if not self.allowed():
            return self.send(403, {'error': 'Origem não autorizada.'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 4096:
                raise ValueError('Pedido inválido.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Pedido inválido.')
            token = self.headers.get('X-Panel-Token') or data.get('token')
            if token != self.server.token:
                return self.send(403, {'error': 'Reabra a página do painel.'})
            c = self.server.controller
            result = None
            if self.path == '/api/connect':
                c.connect(data.get('device'))
            elif self.path == '/api/disconnect':
                c.disconnect()
            elif self.path == '/api/arm':
                c.arm(data.get('client'))
            elif self.path == '/api/heartbeat':
                c.beat(data.get('client'), data.get('epoch'))
            elif self.path == '/api/cancel':
                c.cancel()
            elif self.path == '/api/jog':
                result = c.jog(data)
            elif self.path == '/api/hold/start':
                result = c.hold_start(data)
            elif self.path == '/api/hold/pulse':
                result = c.hold_pulse(data)
            elif self.path == '/api/hold/stop':
                result = c.hold_stop(data)
            else:
                return self.send(404, {'error': 'Operação não permitida.'})
            self.send(200, {'ok': True, 'command': result, 'result': result,
                            'control_epoch': c.snapshot()['control_epoch']})
        except (ValueError, TypeError, OSError, serial.SerialException) as exc:
            self.send(409, {'error': str(exc)})


def make_server(port=8765):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.token = secrets.token_urlsafe(32)
    server.controller = Controller()
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    http = make_server(args.port)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=http.shutdown, daemon=True).start())
    print(f'Painel CNC disponível em http://127.0.0.1:{http.server_port}', flush=True)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http.controller.disconnect()
        http.server_close()


if __name__ == '__main__':
    main()

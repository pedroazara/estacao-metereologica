#!/usr/bin/env python3
"""
Painel unificado da estacao movel: mapa GPS e monitor do IMU no mesmo site,
em abas, alimentados por uma unica conexao com o sensor.

    python3 painel.py --porta /dev/ttyUSB0

Abre em http://localhost:8770

Sem o sensor conectado, para ver a interface:
    python3 painel.py --simular

O IMU e lido rapido (~10 Hz) e o GPS mais devagar (~1 Hz), intercalados na
mesma porta serial - Modbus e uma transacao por vez.
"""

import argparse
import csv
import json
import math
import os
import random
import signal
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from wtgahrs3 import (ENDERECO_PADRAO, ESCALA_ACC_G, ESCALA_ANGULO,
                          ESCALA_GYRO_DPS, REG_AX, REG_LONL, REG_SVNUM,
                          WTGAHRS3, ddmm_para_graus, s16, s32)
except ImportError:
    WTGAHRS3 = None
    ENDERECO_PADRAO = 0x50
    ESCALA_ACC_G, ESCALA_GYRO_DPS, ESCALA_ANGULO = 16.0, 2000.0, 180.0

from filtro_kalman import (SINAL_ROLL, CalibracaoInicial, DetectorPassos,
                           EstimadorRumo, FiltroCV, MediaParado,
                           NavegacaoInercial)

estado = {
    # --- comum ---
    "conectado": False, "mensagem": "iniciando...", "taxa": 0.0, "temp": 0.0,
    # --- GPS ---
    "fix": False, "lat": None, "lon": None, "alt": 0.0, "vel": 0.0,
    "vel_max": 0.0, "sat": 0, "hdop": 0.0, "distancia_m": 0.0, "rumo_gps": 0.0,
    "pontos": [], "inicio": None, "descartados": 0,
    # --- Kalman (GPS + acelerometro) ---
    "kf_lat": None, "kf_lon": None, "kf_vel": 0.0, "kf_sigma": 0.0,
    "kf_pontos": [], "kf_distancia_m": 0.0, "kf_parado": True,
    # --- rumo geografico (yaw + COG do GPS; sem magnetometro) ---
    "rumo": None, "rumo_confiavel": False, "rumo_incerteza": 180.0,
    "rumo_ancoragens": 0,
    # --- media de fixes em repouso ---
    "med_lat": None, "med_lon": None, "med_n": 0, "med_sigma": 0.0,
    # --- passos e calibracao inicial ---
    "passos": 0, "cadencia": 0.0, "passo_m": 0.65,
    "imu_desvio_g": 0.0, "giro_rms_dps": 0.0,
    "gps_movendo": False, "gps_parado": False,
    "cal_fase": "satelites", "cal_instrucao": "", "cal_progresso": 0.0,
    "cal_pronto": False, "cal_retidao": 0.0, "cal_dist": 0.0,
    # --- acelerometro puro, so comparacao ---
    "dr_lat": None, "dr_lon": None, "dr_pontos": [], "dr_erro_m": 0.0,
    "dr_calibrando": True,
    # --- IMU ---
    "ax": 0.0, "ay": 0.0, "az": 0.0, "gx": 0.0, "gy": 0.0, "gz": 0.0,
    "roll": 0.0, "roll_exib": 0.0, "pitch": 0.0, "yaw": 0.0, "g_total": 0.0,
    "pico_ax": 0.0, "pico_ay": 0.0, "pico_az": 0.0, "pico_total": 0.0,
    "pico_giro": 0.0, "rastro": [], "amostras_imu": 0,
}
_lock = threading.Lock()
_parar = False
_zero = {"ax": 0.0, "ay": 0.0, "az": 0.0}
_kf = FiltroCV()
_dr = NavegacaoInercial()
_rumo = EstimadorRumo()
_media = MediaParado()
_passos = DetectorPassos()
_cal = CalibracaoInicial()


def registrar_ponto_kf(lat, lon, args):
    with _lock:
        pts = estado["kf_pontos"]
        if not pts:
            pts.append([lat, lon]); return
        d = haversine_m(pts[-1], (lat, lon))
        if d > args.max_salto or d < args.min_dist:
            return
        pts.append([lat, lon])
        estado["kf_distancia_m"] += d


def registrar_ponto_dr(lat, lon):
    """Sem filtro de jitter: aqui a deriva e justamente o que se quer ver."""
    with _lock:
        pts = estado["dr_pontos"]
        if not pts or haversine_m(pts[-1], (lat, lon)) > 1.0:
            pts.append([lat, lon])
            if len(pts) > 3000:
                del pts[0]
        if estado["lat"] is not None:
            estado["dr_erro_m"] = haversine_m((lat, lon),
                                              (estado["lat"], estado["lon"]))


def passo_fusao(dt):
    """Predicao do Kalman e do inercial puro, a cada amostra do IMU."""
    with _lock:
        ax, ay, az = estado["ax"], estado["ay"], estado["az"]
        gx, gy, gz = estado["gx"], estado["gy"], estado["gz"]
        roll, pitch, yaw = estado["roll"], estado["pitch"], estado["yaw"]

    # PASSOS: a entrada do acelerometro na posicao. Nao integramos aceleracao -
    # contamos passos e usamos cadencia x comprimento como MEDIDA DE VELOCIDADE.
    # Erro cresce com a distancia (poucos %), nao com t^2.
    houve_passo = _passos.amostra(ax, ay, az, dt)
    passos_ativos = _passos.andando()

    # O FiltroCV nao recebe yaw: o modelo de velocidade constante nao precisa
    # de rumo. O giro, os passos e o GPS entram somente no detector de estado,
    # com histerese, para decidir entre propagacao e ZUPT.
    _kf.prever(ax, ay, az, roll, pitch, dt, gx, gy, gz,
               passo_detectado=houve_passo, passos_ativos=passos_ativos)
    if _kf.entrou_parado:
        _passos.encerrar_movimento()

    # gz cru corrigido pelo bias, nao o yaw do firmware. Em repouso a integral
    # fica congelada e o proprio periodo parado refina lentamente o zero.
    _rumo.passo(gz, dt, parado=_kf.parado)
    _cal.passo_imu(gz, _kf.parado, time.monotonic())
    if (houve_passo and _passos.andando() and not _kf.parado
            and _cal.pronto and _rumo.rumo is not None):
        _kf.corrigir_pdr(_passos.cadencia_hz, _cal.comprimento_passo,
                         _rumo.rumo, _rumo.incerteza)

    _dr.passo(ax, ay, az, roll, pitch, yaw, dt)
    if _kf.pronto:
        klat, klon = _kf.posicao
        with _lock:
            estado["kf_lat"], estado["kf_lon"] = klat, klon
            estado["kf_vel"] = _kf.velocidade_kmh
            estado["kf_sigma"] = _kf.sigma_pos
            estado["kf_parado"] = _kf.parado
    with _lock:
        estado["rumo"] = _rumo.rumo
        estado["rumo_confiavel"] = _rumo.confiavel
        estado["rumo_incerteza"] = _rumo.incerteza
        estado["rumo_ancoragens"] = _rumo.ancoragens
        estado["passos"] = _passos.passos
        estado["cadencia"] = _passos.cadencia_hz
        estado["passo_m"] = _cal.comprimento_passo
        estado["imu_desvio_g"] = _kf.detector.desvio_acc_g
        estado["giro_rms_dps"] = _kf.detector.giro_rms_dps
        estado["gps_movendo"] = _kf.detector.gps_movendo
        estado["gps_parado"] = _kf.detector.gps_parado
        estado["cal_fase"] = _cal.fase
        estado["cal_instrucao"] = _cal.instrucao()
        estado["cal_progresso"] = _cal.progresso(time.monotonic())
        estado["cal_pronto"] = _cal.pronto
        estado["cal_retidao"] = _cal.retidao
        estado["cal_dist"] = _cal.dist_reto
    if _dr.pronto and not _dr.calibrando:
        dlat, dlon = _dr.posicao
        with _lock:
            estado["dr_lat"], estado["dr_lon"] = dlat, dlon
        registrar_ponto_dr(dlat, dlon)
    with _lock:
        estado["dr_calibrando"] = _dr.calibrando


def fusao_gps(lat, lon, hdop, vel, rumo, args):
    """Correcao do Kalman quando chega um fix."""
    agora = time.monotonic()
    if not _kf.pronto:
        _kf.iniciar(lat, lon)
        _dr.iniciar(lat, lon)
        _kf.atualizar_movimento_gps(agora, lat, lon, vel)
        return

    # Movimento e rumo usam a posicao GNSS original. Nunca use a saida do
    # proprio Kalman aqui: ela pode conter PDR e criaria realimentacao circular.
    n_gps, e_gps = _kf.graus_para_metros(lat, lon)
    _kf.atualizar_movimento_gps(agora, lat, lon, vel)
    _kf.corrigir_gps(lat, lon, hdop=hdop)
    _kf.corrigir_velocidade(vel, rumo)

    # Ancoragem pela DIRECAO DO DESLOCAMENTO sobre uma base de segundos, e nao
    # pelo COG instantaneo: a pe o COG tem cauda de 16 graus por epoca, e chega
    # a acusar 149 graus de curva em 2 s de caminhada quase reta.
    if _kf.pronto:
        if _cal.pronto:
            _rumo.ancorar_por_deslocamento(
                agora, n_gps, e_gps, movendo=_kf.detector.gps_movendo)
        with _lock:
            sat = estado["sat"]
        mudou = _cal.passo_gps(
            agora, n_gps, e_gps, sat, hdop, _passos.passos, _kf.parado)
        # Ao fechar a fase RETO temos o primeiro rumo ABSOLUTO confiavel: o
        # trecho foi reto, entao a direcao do deslocamento e a direcao do
        # corpo. E o zero que o giroscopio sozinho nunca teria.
        if mudou and _cal.pronto and _cal.rumo_inicial is not None:
            _rumo.definir_vies_gz(_cal.vies_gz)
            _rumo.fixar_rumo(_cal.rumo_inicial)
    _dr.ancorar_rumo(estado["yaw"], rumo)

    # Media de fixes em repouso: o ganho de precisao que este hardware permite.
    _media.amostra(lat, lon, _kf.parado)
    mlat, mlon = _media.posicao
    with _lock:
        estado["med_lat"], estado["med_lon"] = mlat, mlon
        estado["med_n"] = _media.n
        estado["med_sigma"] = _media.sigma_m if _media.n >= 2 else 0.0

    klat, klon = _kf.posicao
    registrar_ponto_kf(klat, klon, args)


def haversine_m(a, b) -> float:
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    x = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(x))


def registrar_ponto(lat, lon, args, gravador=None) -> bool:
    """Filtra jitter (deslocamento minimo) e glitches (salto absurdo)."""
    with _lock:
        pts = estado["pontos"]
        if not pts:
            pts.append([lat, lon])
            estado["inicio"] = estado["inicio"] or time.time()
            if gravador:
                gravador(lat, lon)
            return True
        d = haversine_m(pts[-1], (lat, lon))
        if d > args.max_salto:
            estado["descartados"] += 1
            estado["mensagem"] = f"salto de {d/1000:.1f} km descartado"
            return False
        if d < args.min_dist:
            return False
        pts.append([lat, lon])
        estado["distancia_m"] += d
        if gravador:
            gravador(lat, lon)
        return True


def _aplica_imu(ax, ay, az, gx, gy, gz, roll, pitch, yaw, temp, rastro):
    with _lock:
        ax -= _zero["ax"]; ay -= _zero["ay"]; az -= _zero["az"]
        total = math.sqrt(ax*ax + ay*ay + az*az)
        # "roll" fica com o valor CRU do sensor, para o log e para o filtro,
        # que aplica SINAL_ROLL por conta propria. "roll_exib" e o valor com o
        # sinal ja corrigido, que e o que o horizonte artificial deve mostrar -
        # este exemplar reporta o roll invertido.
        estado.update({"ax": ax, "ay": ay, "az": az, "gx": gx, "gy": gy, "gz": gz,
                       "roll": roll, "roll_exib": SINAL_ROLL * roll,
                       "pitch": pitch, "yaw": yaw,
                       "temp": temp, "g_total": total})
        estado["pico_ax"] = max(estado["pico_ax"], abs(ax))
        estado["pico_ay"] = max(estado["pico_ay"], abs(ay))
        estado["pico_az"] = max(estado["pico_az"], abs(az))
        estado["pico_total"] = max(estado["pico_total"], total)
        estado["pico_giro"] = max(estado["pico_giro"], abs(gx), abs(gy), abs(gz))
        estado["amostras_imu"] += 1
        rastro.append([round(ay, 3), round(ax, 3)])
        estado["rastro"] = list(rastro)


def thread_sensor(args):
    gravador = None
    arq = None
    if args.csv:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        novo = not os.path.exists(args.csv) or os.path.getsize(args.csv) == 0
        arq = open(args.csv, "a", newline="", encoding="utf-8")
        w = csv.writer(arq)
        if novo:
            w.writerow(["timestamp", "latitude", "longitude"])

        def gravador(la, lo):
            w.writerow([datetime.now().isoformat(timespec="seconds"), la, lo])
            arq.flush()

    if args.simular:
        _simular(args, gravador)
        return

    rastro = deque(maxlen=args.rastro)
    sensor = None
    intervalo = 1.0 / args.hz
    ciclos_gps = max(1, int(args.hz / max(args.hz_gps, 0.1)))
    ciclo = 0
    marca, contagem = time.monotonic(), 0

    while not _parar:
        if sensor is None:
            try:
                sensor = WTGAHRS3(args.porta, endereco=int(args.endereco, 0),
                                  baudrate=args.baud, timeout=0.3)
                if not sensor.esta_respondendo():
                    raise RuntimeError("sem resposta")
                with _lock:
                    estado["conectado"] = True
                    estado["mensagem"] = "conectado"
            except Exception as e:
                with _lock:
                    estado["conectado"] = False
                    estado["mensagem"] = f"sem conexao: {e}"
                sensor = None
                time.sleep(1.5)
                continue

        t0 = time.monotonic()
        try:
            # --- IMU: 0x34..0x40 numa transacao (acc, giro, mag, angulos, temp)
            v = [s16(x) for x in sensor._ler(REG_AX, 13)]
            _aplica_imu(
                v[0]/32768*ESCALA_ACC_G, v[1]/32768*ESCALA_ACC_G, v[2]/32768*ESCALA_ACC_G,
                v[3]/32768*ESCALA_GYRO_DPS, v[4]/32768*ESCALA_GYRO_DPS,
                v[5]/32768*ESCALA_GYRO_DPS,
                v[9]/32768*ESCALA_ANGULO, v[10]/32768*ESCALA_ANGULO,
                v[11]/32768*ESCALA_ANGULO, v[12]/100.0, rastro)

            passo_fusao(intervalo)

            # --- GPS: mais devagar
            ciclo += 1
            if ciclo % ciclos_gps == 0:
                g = sensor._ler(REG_LONL, 8)
                d = sensor._ler(REG_SVNUM, 4)
                lon = ddmm_para_graus(s32(g[0], g[1]))
                lat = ddmm_para_graus(s32(g[2], g[3]))
                sat, hdop = d[0], d[2] / 100.0
                fix = sat >= 4 and hdop > 0
                with _lock:
                    estado["sat"] = sat
                    estado["hdop"] = hdop
                    estado["fix"] = fix
                    estado["alt"] = s16(g[4]) / 10.0
                    estado["rumo_gps"] = s16(g[5]) / 100.0   # rumo sobre o solo
                    estado["vel"] = s32(g[6], g[7]) / 1000.0
                    estado["vel_max"] = max(estado["vel_max"], estado["vel"])
                if fix and hdop <= args.hdop_max:
                    with _lock:
                        estado["lat"], estado["lon"] = lat, lon
                        estado["mensagem"] = "rastreando"
                    registrar_ponto(lat, lon, args, gravador)
                    fusao_gps(lat, lon, hdop, estado["vel"],
                              estado["rumo_gps"], args)
                elif not fix:
                    with _lock:
                        estado["mensagem"] = f"aguardando fix ({sat} satelites)"

            contagem += 1
            agora = time.monotonic()
            if agora - marca >= 1.0:
                with _lock:
                    estado["taxa"] = contagem / (agora - marca)
                marca, contagem = agora, 0

        except Exception as e:
            with _lock:
                estado["conectado"] = False
                estado["mensagem"] = f"falha: {e}"
            try:
                sensor.fechar()
            except Exception:
                pass
            sensor = None
            continue

        resto = intervalo - (time.monotonic() - t0)
        if resto > 0:
            time.sleep(resto)

    if arq:
        arq.close()


def _simular(args, gravador):
    rastro = deque(maxlen=args.rastro)
    lat, lon = -21.225671, -44.977374
    rumo, t = random.uniform(0, 6.28), 0.0
    # O yaw simulado precisa imitar o do hardware para o EstimadorRumo ser
    # testado de verdade: acompanha a direcao real de deslocamento, mas com um
    # zero arbitrario e uma deriva lenta - as duas coisas que o firmware faz e
    # que tornam o rumo inutil sem ancoragem no GPS. Deriva de 0,27 graus/s e a
    # medida nesta bancada (32 graus em 120 s).
    yaw_zero = random.uniform(0, 360)
    yaw_deriva = 0.27
    with _lock:
        estado["conectado"] = True
        estado["mensagem"] = "MODO SIMULACAO (sem sensor real)"
    ciclos_gps = max(1, int(args.hz / max(args.hz_gps, 0.1)))
    ciclo = 0
    dt_sim = 1.0 / args.hz
    taxa_giro = 0.0        # graus/s de rotacao real da caminhada simulada
    # ROTEIRO, para o procedimento de partida ser exercitado de ponta a ponta:
    # 15 s parado (fase PARADO), 45 s em linha reta (fase RETO), depois solto.
    T_PARADO, T_RETO = 15.0, 60.0
    CADENCIA = 1.55        # passos/s, a mediana medida na caminhada real
    AMPL_PASSO = 0.07      # g de oscilacao vertical; medido 0,0656 g
    while not _parar:
        t += dt_sim
        parado_sim = t < T_PARADO
        reto_sim = T_PARADO <= t < T_RETO

        # A caminhada vira de forma continua, e o gz simulado e o NEGATIVO
        # dessa taxa - a mesma convencao medida no hardware (giro anti-horario
        # positivo, azimute horario positivo). Sem isso o gz nao tem relacao
        # com o movimento e o EstimadorRumo nao e testado por nada.
        if parado_sim or reto_sim:
            taxa_giro = 0.0 if parado_sim else random.gauss(0, 0.8)
        else:
            taxa_giro += random.gauss(0, 4.0) * dt_sim
            taxa_giro = max(-40.0, min(40.0, taxa_giro * 0.98))
        rumo += math.radians(taxa_giro) * dt_sim
        gz_sim = 0.0 if parado_sim else -taxa_giro + random.gauss(0, 1.5)

        # Oscilacao do passo: e ela que o DetectorPassos procura. Parado, so
        # ruido - do tamanho medido na bancada (0,0005 g).
        if parado_sim:
            osc = random.gauss(0, .0005)
            bal = 0.0
        else:
            osc = AMPL_PASSO*math.sin(2*math.pi*CADENCIA*t) + random.gauss(0, .008)
            bal = 0.05*math.sin(math.pi*CADENCIA*t)
        _aplica_imu(
            bal + random.gauss(0, .01),
            0.5*bal + random.gauss(0, .01),
            1.00 + osc,
            40*math.sin(t*1.3) if not parado_sim else 0.0,
            30*math.cos(t*0.7) if not parado_sim else 0.0, gz_sim,
            0.0 if parado_sim else 18*math.sin(t*0.6),
            0.0 if parado_sim else 12*math.cos(t*0.5),
            (math.degrees(rumo) + yaw_zero + yaw_deriva * t) % 360, 31.5, rastro)
        ciclo += 1
        if ciclo % ciclos_gps == 0:
            passo = 0.0 if parado_sim else 1.4 / 111320.0
            lat += passo*math.cos(rumo)
            lon += passo*math.sin(rumo)/math.cos(math.radians(lat))
            lat_r = lat + random.gauss(0, 3.0)/111320.0
            lon_r = lon + random.gauss(0, 3.0)/(111320.0*math.cos(math.radians(lat)))
            with _lock:
                estado.update({"fix": True, "lat": lat_r, "lon": lon_r, "alt": 919.0,
                               "sat": random.randint(12, 20),
                               "hdop": round(random.uniform(.8, 1.6), 2),
                               "rumo_gps": (math.degrees(rumo) % 360),
                               "vel": 0.0 if parado_sim else round(random.uniform(4, 6), 2)})
                estado["vel_max"] = max(estado["vel_max"], estado["vel"])
            registrar_ponto(lat_r, lon_r, args, gravador)
            fusao_gps(lat_r, lon_r, estado["hdop"], estado["vel"],
                      estado["rumo_gps"], args)
        passo_fusao(1.0 / args.hz)
        with _lock:
            estado["taxa"] = args.hz
        time.sleep(1.0 / args.hz)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _envia(self, corpo, tipo="application/json"):
        d = corpo.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{tipo}; charset=utf-8")
        self.send_header("Content-Length", str(len(d)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(d)

    def do_GET(self):
        p = self.path
        if p.startswith("/dados"):
            with _lock:
                d = dict(estado)
                d["decorrido"] = time.time() - estado["inicio"] if estado["inicio"] else 0
                d["n_pontos"] = len(estado["pontos"])
                d["kf_n_pontos"] = len(estado["kf_pontos"])
                d["dr_n_pontos"] = len(estado["dr_pontos"])
            self._envia(json.dumps(d))
        elif p.startswith("/limpar"):
            with _lock:
                estado.update({"pontos": [], "distancia_m": 0.0, "vel_max": 0.0,
                               "kf_pontos": [], "kf_distancia_m": 0.0,
                               "dr_pontos": [], "dr_erro_m": 0.0,
                               "inicio": time.time()})
                lat, lon = estado["lat"], estado["lon"]
            if lat is not None:
                _kf.iniciar(lat, lon); _dr.iniciar(lat, lon)
            self._envia('{"ok":true}')
        elif p.startswith("/picos"):
            with _lock:
                for k in ("pico_ax", "pico_ay", "pico_az", "pico_total", "pico_giro"):
                    estado[k] = 0.0
                estado["rastro"] = []
            self._envia('{"ok":true}')
        elif p.startswith("/zerar"):
            with _lock:
                _zero["ax"] += estado["ax"]
                _zero["ay"] += estado["ay"]
                _zero["az"] += estado["az"]
            self._envia('{"ok":true}')
        elif p.startswith("/restaurar"):
            with _lock:
                _zero.update({"ax": 0.0, "ay": 0.0, "az": 0.0})
            self._envia('{"ok":true}')
        else:
            self._envia(PAGINA, "text/html")


PAGINA = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Estacao Movel - Painel</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root{--bg:#0a0e13;--card:#141b23;--borda:#232d38;--txt:#e6edf3;--txt2:#7d8794;
        --x:#f85149;--y:#3fb950;--z:#58a6ff;--alerta:#d29922}
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
            background:var(--bg);color:var(--txt);overflow:hidden}
  #topo{height:52px;display:flex;align-items:center;gap:14px;padding:0 16px;
        background:var(--card);border-bottom:1px solid var(--borda);position:relative;z-index:2000}
  #dot{width:9px;height:9px;border-radius:50%;background:var(--x);flex:none}
  #dot.on{background:var(--y);box-shadow:0 0 8px var(--y)}
  #marca{font-size:14px;font-weight:600;white-space:nowrap}
  #msg{font-size:11px;color:var(--txt2)}
  #abas{display:flex;gap:6px;margin-left:18px}
  .aba{background:transparent;border:1px solid var(--borda);color:var(--txt2);
       border-radius:7px;padding:7px 16px;font-size:12px;cursor:pointer;
       font-family:inherit}
  .aba.on{background:var(--z);border-color:var(--z);color:#07121f;font-weight:600}
  #resumo{margin-left:auto;display:flex;gap:16px;font-size:11px;color:var(--txt2);
          font-family:ui-monospace,Menlo,monospace;white-space:nowrap}
  #resumo b{color:var(--txt);font-weight:600}
  #corpo{position:absolute;top:52px;left:0;right:0;bottom:0}
  .pane{position:absolute;inset:0;display:none}
  .pane.on{display:block}
  #pane_imu{overflow-y:auto;padding:14px}

  #mapa{position:absolute;inset:0;z-index:1}
  #painelmapa{position:absolute;top:12px;left:12px;z-index:1000;width:270px;
    background:rgba(20,27,35,.95);backdrop-filter:blur(8px);border:1px solid var(--borda);
    border-radius:12px;padding:15px;box-shadow:0 8px 32px rgba(0,0,0,.5)}
  .grande{font-size:36px;font-weight:700;line-height:1;letter-spacing:-1px}
  .unidade{font-size:14px;color:var(--txt2);margin-left:4px}
  .rotulo{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--txt2);
          margin-bottom:6px}
  #coords{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--txt2);
          margin-top:10px;line-height:1.6}
  #aviso{position:absolute;bottom:12px;left:12px;z-index:1000;font-size:11px;
         color:var(--txt2);background:rgba(20,27,35,.9);padding:6px 10px;border-radius:6px;
         border:1px solid var(--borda);display:none}

  .grade{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
  .card{background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:16px}
  .tit{font-size:10px;text-transform:uppercase;letter-spacing:1.2px;color:var(--txt2);
       margin-bottom:12px}
  canvas{display:block;margin:0 auto;max-width:100%}
  .nums{display:grid;grid-template-columns:1fr 1fr;gap:9px}
  .n{background:#0a0e13;border:1px solid var(--borda);border-radius:8px;padding:9px}
  .n .v{font-size:17px;font-weight:600;font-family:ui-monospace,Menlo,monospace}
  .n .r{font-size:9px;text-transform:uppercase;letter-spacing:.8px;color:var(--txt2);
        margin-top:2px}
  .barra{margin-bottom:14px}
  .barra-topo{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px}
  .barra-nome{font-size:12px;font-weight:600}
  .barra-val{font-family:ui-monospace,Menlo,monospace;font-size:15px;font-weight:600}
  .trilho{position:relative;height:22px;background:#0a0e13;border-radius:5px;
          border:1px solid var(--borda);overflow:hidden}
  .centro{position:absolute;left:50%;top:0;bottom:0;width:1px;background:#39424e}
  .preenche{position:absolute;top:2px;bottom:2px;border-radius:3px;transition:.06s linear}
  .pico{position:absolute;top:0;bottom:0;width:2px;background:var(--alerta)}
  .escala{display:flex;justify-content:space-between;font-size:9px;color:var(--txt2);
          margin-top:3px;font-family:ui-monospace,Menlo,monospace}
  .legenda{margin-top:12px;display:flex;flex-direction:column;gap:5px}
  .lg{display:flex;align-items:center;gap:7px;font-size:10.5px;color:var(--txt2)}
  .lg b{color:var(--txt);font-weight:500}
  .sw{width:16px;height:3px;border-radius:2px;flex:none}
  .botoes{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
  button.b{flex:1;min-width:88px;background:#1c242e;color:var(--txt);
           border:1px solid var(--borda);border-radius:7px;padding:9px;font-size:11px;
           cursor:pointer;font-family:inherit}
  button.b:hover{background:#26303c}
  button.b.ativo{background:var(--z);border-color:var(--z);color:#07121f;font-weight:600}
  @media(max-width:760px){
    #resumo{display:none}
    #painelmapa{width:auto;left:8px;right:8px;padding:12px}
  }
</style>
</head>
<body>

<div id="topo">
  <div id="dot"></div>
  <div>
    <div id="marca">Estacao Movel</div>
    <div id="msg">conectando...</div>
  </div>
  <div id="abas">
    <button class="aba on" data-p="mapa">Mapa GPS</button>
    <button class="aba" data-p="imu">Acelerometro</button>
  </div>
  <div id="resumo">
    <span>sat <b id="r_sat">0</b></span>
    <span>hdop <b id="r_hdop">--</b></span>
    <span><b id="r_g">0,00</b> G</span>
    <span><b id="r_taxa">--</b> Hz</span>
  </div>
</div>

<div id="corpo">

  <div class="pane on" id="pane_mapa">
    <div id="mapa"></div>
    <div id="painelmapa">
      <div class="rotulo">Distancia percorrida</div>
      <div style="margin-bottom:14px">
        <span class="grande" id="dist">0</span><span class="unidade" id="dist_un">m</span>
      </div>
      <div class="nums">
        <div class="n"><div class="v" id="m_vel">0,0</div><div class="r">km/h</div></div>
        <div class="n"><div class="v" id="m_vmax">0,0</div><div class="r">max km/h</div></div>
        <div class="n"><div class="v" id="m_tempo">0:00</div><div class="r">duracao</div></div>
        <div class="n"><div class="v" id="m_alt">--</div><div class="r">altitude m</div></div>
        <div class="n"><div class="v" id="m_kdist">0</div><div class="r">dist. Kalman m</div></div>
        <div class="n"><div class="v" id="m_ksig">--</div><div class="r">incerteza &plusmn;m</div></div>
        <div class="n" style="grid-column:1/-1"><div class="v" id="m_drerro">--</div>
          <div class="r">deriva do acelerometro puro</div></div>
      </div>
      <div class="legenda">
        <div class="lg"><span class="sw" style="background:#3fb950"></span>
          <b>Kalman</b> &mdash; GPS + acelerometro</div>
        <div class="lg"><span class="sw" style="background:#58a6ff"></span>
          <b>GPS bruto</b> &mdash; sem tratamento</div>
        <div class="lg"><span class="sw" style="background:#f85149"></span>
          <b>Acelerometro</b> &mdash; integracao pura</div>
      </div>
      <div id="cal_banner" style="display:none;background:#1c2530;border:1px solid #d29922;
           border-radius:6px;padding:10px 12px;margin-bottom:10px">
        <div style="font-size:11px;color:#8b949e;letter-spacing:.5px" id="cal_fase">PARTIDA</div>
        <div style="font-size:14px;color:#e6edf3;margin:4px 0 8px" id="cal_txt">...</div>
        <div style="height:6px;background:#0d1117;border-radius:3px;overflow:hidden">
          <div id="cal_barra" style="height:100%;width:0%;background:#d29922;transition:width .3s"></div>
        </div>
        <div style="font-size:11px;color:#8b949e;margin-top:6px" id="cal_extra"></div>
      </div>
      <div id="coords">aguardando posicao...</div>
      <div class="botoes">
        <button class="b ativo" id="b_seguir">Seguir</button>
        <button class="b" id="b_limpar">Limpar</button>
        <button class="b" id="b_gpx">GPX</button>
        <button class="b" id="b_rumo">Rumo: fundido</button>
        <button class="b ativo" id="b_kalman">Kalman</button>
      </div>
    </div>
    <div id="aviso"></div>
  </div>

  <div class="pane" id="pane_imu">
    <div class="grade">

      <div class="card">
        <div class="tit">Circulo de forca G</div>
        <canvas id="gmeter" width="300" height="300"></canvas>
        <div class="nums" style="margin-top:12px">
          <div class="n"><div class="v" id="gtot">0,00</div><div class="r">G resultante</div></div>
          <div class="n"><div class="v" id="gpico">0,00</div><div class="r">pico G</div></div>
        </div>
      </div>

      <div class="card">
        <div class="tit">Aceleracao por eixo</div>
        <div class="barra">
          <div class="barra-topo"><span class="barra-nome" style="color:var(--x)">X longitudinal</span>
            <span class="barra-val" id="vax">0,00 g</span></div>
          <div class="trilho"><div class="centro"></div>
            <div class="preenche" id="bax" style="background:var(--x)"></div>
            <div class="pico" id="pax"></div></div>
        </div>
        <div class="barra">
          <div class="barra-topo"><span class="barra-nome" style="color:var(--y)">Y lateral</span>
            <span class="barra-val" id="vay">0,00 g</span></div>
          <div class="trilho"><div class="centro"></div>
            <div class="preenche" id="bay" style="background:var(--y)"></div>
            <div class="pico" id="pay"></div></div>
        </div>
        <div class="barra">
          <div class="barra-topo"><span class="barra-nome" style="color:var(--z)">Z vertical</span>
            <span class="barra-val" id="vaz">0,00 g</span></div>
          <div class="trilho"><div class="centro"></div>
            <div class="preenche" id="baz" style="background:var(--z)"></div>
            <div class="pico" id="paz"></div></div>
          <div class="escala"><span id="e1">-2 g</span><span>0</span><span id="e2">+2 g</span></div>
        </div>
        <div class="botoes">
          <button class="b" id="b_zerar">Zerar eixos</button>
          <button class="b" id="b_restaurar">Restaurar</button>
          <button class="b" id="b_picos">Zerar picos</button>
          <button class="b" id="b_escala">Escala 2g</button>
        </div>
      </div>

      <div class="card">
        <div class="tit">Atitude</div>
        <canvas id="horizonte" width="260" height="200"></canvas>
        <div class="nums" style="margin-top:12px">
          <div class="n"><div class="v" id="vroll">0,0</div><div class="r">roll (graus)</div></div>
          <div class="n"><div class="v" id="vpitch">0,0</div><div class="r">pitch (graus)</div></div>
          <div class="n"><div class="v" id="vdesvioacc">0,000</div><div class="r">ruido |a| (g)</div></div>
          <div class="n"><div class="v" id="vgrms">0,0</div><div class="r">giro RMS (graus/s)</div></div>
        </div>
      </div>

      <div class="card">
        <div class="tit">Rumo geografico</div>
        <div id="rumo_aviso" style="font-size:12px;color:#d29922;margin:-4px 0 8px">
          sem magnetometro: o rumo so fica valido depois de andar
        </div>
        <canvas id="bussola" width="220" height="220"></canvas>
        <div class="nums" style="margin-top:12px">
          <div class="n"><div class="v" id="vyaw">0,0</div><div class="r">rumo (graus)</div></div>
          <div class="n"><div class="v" id="vrumoinc">--</div><div class="r">incerteza (&deg;)</div></div>
          <div class="n"><div class="v" id="vyawbruto">0,0</div><div class="r">yaw cru</div></div>
          <div class="n"><div class="v" id="vancoras">0</div><div class="r">ancoragens</div></div>
          <div class="n"><div class="v" id="vtemp">--</div><div class="r">temp chip (C)</div></div>
        </div>
        <div class="botoes">
          <button class="b" id="b_inverter">Inverter sentido</button>
          <button class="b" id="b_girar">Girar +90&deg;</button>
          <button class="b" id="b_norte">Aqui e o Norte</button>
        </div>
      </div>

      <div class="card">
        <div class="tit">Giroscopio (graus/s)</div>
        <div class="barra">
          <div class="barra-topo"><span class="barra-nome" style="color:var(--x)">Giro X</span>
            <span class="barra-val" id="vgx">0</span></div>
          <div class="trilho"><div class="centro"></div>
            <div class="preenche" id="bgx" style="background:var(--x)"></div></div>
        </div>
        <div class="barra">
          <div class="barra-topo"><span class="barra-nome" style="color:var(--y)">Giro Y</span>
            <span class="barra-val" id="vgy">0</span></div>
          <div class="trilho"><div class="centro"></div>
            <div class="preenche" id="bgy" style="background:var(--y)"></div></div>
        </div>
        <div class="barra">
          <div class="barra-topo"><span class="barra-nome" style="color:var(--z)">Giro Z</span>
            <span class="barra-val" id="vgz">0</span></div>
          <div class="trilho"><div class="centro"></div>
            <div class="preenche" id="bgz" style="background:var(--z)"></div></div>
          <div class="escala"><span>-250</span><span>0</span><span>+250</span></div>
        </div>
        <div class="nums" style="margin-top:8px">
          <div class="n"><div class="v" id="gyropico">0</div><div class="r">pico giro</div></div>
          <div class="n"><div class="v" id="amostras">0</div><div class="r">amostras</div></div>
        </div>
      </div>

    </div>
  </div>

</div>

<script>
const br = (n,d) => n.toFixed(d).replace('.',',');
let abaAtual = 'mapa';

// ---------------- abas ----------------
document.querySelectorAll('.aba').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('.aba').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    abaAtual = b.dataset.p;
    document.querySelectorAll('.pane').forEach(p => p.classList.remove('on'));
    document.getElementById('pane_' + abaAtual).classList.add('on');
    // o Leaflet precisa recalcular o tamanho ao voltar de uma aba escondida,
    // senao os tiles ficam desalinhados
    if(abaAtual === 'mapa') setTimeout(() => mapa.invalidateSize(), 60);
  };
});

// ---------------- mapa ----------------
const mapa = L.map('mapa', {zoomControl:false, maxZoom:22}).setView([-21.2257,-44.9774], 17);
L.control.zoom({position:'bottomright'}).addTo(mapa);
const ruas = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:22, maxNativeZoom:19, attribution:'&copy; OpenStreetMap'}).addTo(mapa);
const satelite = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  {maxZoom:22, maxNativeZoom:19, attribution:'&copy; Esri'});
L.control.layers({'Ruas':ruas,'Satelite':satelite}, null, {position:'bottomright'}).addTo(mapa);
mapa.options.zoomSnap = 0.25;
mapa.options.wheelPxPerZoomLevel = 100;
document.addEventListener('keydown', e => {
  if(abaAtual !== 'mapa') return;
  if(e.key === '+' || e.key === '=') mapa.zoomIn(1);
  if(e.key === '-' || e.key === '_') mapa.zoomOut(1);
});

// Painel proprio para o cone de direcao, com z-index abaixo do overlayPane (400)
// para que ele fique SOB o ponto de posicao e sob o rastro, como no Google Maps.
mapa.createPane('conePane');
mapa.getPane('conePane').style.zIndex = 350;
mapa.getPane('conePane').style.pointerEvents = 'none';

// O cone tem tamanho fixo em pixels (nao escala com o zoom), igual ao do
// Google Maps. Uma cunha de 60 graus com gradiente radial que some na ponta.
const CONE_SVG = `
<div class="cone-rot" style="width:140px;height:140px;transition:transform .15s linear">
  <svg width="140" height="140" viewBox="0 0 140 140">
    <defs>
      <radialGradient id="gcone" cx="50%" cy="50%" r="50%">
        <stop offset="0%"   stop-color="#58a6ff" stop-opacity=".65"/>
        <stop offset="55%"  stop-color="#58a6ff" stop-opacity=".28"/>
        <stop offset="100%" stop-color="#58a6ff" stop-opacity="0"/>
      </radialGradient>
    </defs>
    <path d="M 70 70 L 35.5 10.2 A 69 69 0 0 1 104.5 10.2 Z" fill="url(#gcone)"/>
  </svg>
</div>`;

const cone = L.marker([0,0], {
  pane:'conePane', interactive:false,
  icon: L.divIcon({className:'', html:CONE_SVG, iconSize:[140,140], iconAnchor:[70,70]})
}).addTo(mapa);
let coneEl = null;

const rastroMapa = L.polyline([], {color:'#58a6ff', weight:2, opacity:.4}).addTo(mapa);
const rastroKF = L.polyline([], {color:'#3fb950', weight:4, opacity:.9}).addTo(mapa);
const rastroDR = L.polyline([], {color:'#f85149', weight:2, opacity:.7, dashArray:'5,4'}).addTo(mapa);
const marcadorDR = L.circleMarker([0,0],
  {radius:5,color:'#f85149',weight:2,fillColor:'#0a0e13',fillOpacity:1,opacity:0});
let kalmanOn = true;
const marcador = L.circleMarker([0,0],
  {radius:8, color:'#fff', weight:3, fillColor:'#58a6ff', fillOpacity:1}).addTo(mapa);
let marcadorInicio = null, seguir = true, primeiro = true, ultimosPontos = [];

// O rumo vive em 0..360, mas o CSS interpola o valor literal: indo de 359 para
// 1 ele gira 358 graus para tras em vez de 2 para frente. Para evitar esse
// "pulo", acumulamos um angulo continuo, que pode passar de 360 ou ficar
// negativo, sempre somando o menor deslocamento (-180..+180).
let anguloCone = null;
function caminhoCurto(alvo, atual){
  if(atual === null) return alvo;
  const delta = ((alvo - atual) % 360 + 540) % 360 - 180;
  return atual + delta;
}

// Fonte do rumo. NAO existe bussola neste sensor (confirmado com ima), entao
// as opcoes sao: 'fundido' = yaw do IMU ancorado no COG do GPS, que responde
// na hora e sobrevive a paradas curtas; ou 'gps' = COG puro, absoluto mas so
// valido em movimento e ruidoso a pe.
let FONTE_RUMO = 'fundido';
document.getElementById('b_rumo').onclick = function(){
  FONTE_RUMO = FONTE_RUMO === 'fundido' ? 'gps' : 'fundido';
  this.textContent = FONTE_RUMO === 'fundido' ? 'Rumo: fundido' : 'Rumo: GPS';
};

document.getElementById('b_kalman').onclick = function(){
  kalmanOn = !kalmanOn;
  this.classList.toggle('ativo', kalmanOn);
  rastroKF.setStyle({opacity: kalmanOn ? .9 : 0});
  rastroMapa.setStyle({weight: kalmanOn ? 2 : 4, opacity: kalmanOn ? .4 : .85});
  marcador.setStyle({fillColor: kalmanOn ? '#3fb950' : '#58a6ff'});
};
document.getElementById('b_seguir').onclick = function(){
  seguir = !seguir; this.classList.toggle('ativo', seguir);
};
document.getElementById('b_limpar').onclick = async function(){
  if(!confirm('Apagar o rastro e zerar a distancia?')) return;
  await fetch('/limpar');
  rastroMapa.setLatLngs([]);
  if(marcadorInicio){ mapa.removeLayer(marcadorInicio); marcadorInicio = null; }
};
document.getElementById('b_gpx').onclick = function(){
  if(!ultimosPontos.length){ alert('Nenhum ponto gravado ainda.'); return; }
  let g = '<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="EstacaoMovel" ';
  g += 'xmlns="http://www.topografix.com/GPX/1/1">\n<trk><name>Trajeto</name><trkseg>\n';
  for(const p of ultimosPontos) g += '<trkpt lat="'+p[0]+'" lon="'+p[1]+'"></trkpt>\n';
  g += '</trkseg></trk></gpx>';
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([g],{type:'application/gpx+xml'}));
  a.download = 'trajeto_' + Date.now() + '.gpx'; a.click();
};
function duracao(s){
  s = Math.floor(s);
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60;
  return h>0 ? h+':'+String(m).padStart(2,'0')+':'+String(x).padStart(2,'0')
             : m+':'+String(x).padStart(2,'0');
}

// ---------------- IMU ----------------
let ESCALA = 2.0;
const ESCALAS = [1,2,4,8,16];
let INVERTER = false, OFFSET = 0, yawCru = 0;
const corrigeRumo = y => (((INVERTER ? -y : y) + OFFSET) % 360 + 360) % 360;

document.getElementById('b_zerar').onclick     = () => fetch('/zerar');
document.getElementById('b_restaurar').onclick = () => fetch('/restaurar');
document.getElementById('b_picos').onclick     = () => fetch('/picos');
document.getElementById('b_escala').onclick = function(){
  ESCALA = ESCALAS[(ESCALAS.indexOf(ESCALA)+1) % ESCALAS.length];
  this.textContent = 'Escala ' + ESCALA + 'g';
  document.getElementById('e1').textContent = '-'+ESCALA+' g';
  document.getElementById('e2').textContent = '+'+ESCALA+' g';
};
document.getElementById('b_inverter').onclick = function(){
  INVERTER = !INVERTER; this.classList.toggle('ativo', INVERTER);
};
document.getElementById('b_girar').onclick = () => { OFFSET = (OFFSET+90)%360; };
document.getElementById('b_norte').onclick = () => {
  OFFSET = (OFFSET - corrigeRumo(yawCru)%360 + 720)%360;
};

function barra(idB, idP, valor, pico, escala){
  const el = document.getElementById(idB);
  const f = Math.max(-1, Math.min(1, valor/escala));
  if(f >= 0){ el.style.left = '50%'; el.style.width = (f*50)+'%'; }
  else       { el.style.left = (50+f*50)+'%'; el.style.width = (-f*50)+'%'; }
  if(idP){
    const p = document.getElementById(idP);
    p.style.left = (50 + Math.min(1,pico/escala)*50)+'%';
    p.style.display = pico > 0.01 ? 'block' : 'none';
  }
}

const gc = document.getElementById('gmeter').getContext('2d');
function desenhaG(ay, ax, rastro, pico){
  const W=300,H=300,cx=W/2,cy=H/2,R=125;
  gc.clearRect(0,0,W,H);
  gc.strokeStyle='#232d38'; gc.fillStyle='#7d8794'; gc.font='9px ui-monospace';
  for(let i=1;i<=4;i++){
    const r=R*i/4; gc.beginPath(); gc.arc(cx,cy,r,0,7); gc.stroke();
    gc.fillText(br(ESCALA*i/4,1)+'g', cx+4, cy-r+11);
  }
  gc.beginPath(); gc.moveTo(cx-R,cy); gc.lineTo(cx+R,cy);
  gc.moveTo(cx,cy-R); gc.lineTo(cx,cy+R); gc.stroke();
  gc.fillStyle='#7d8794'; gc.font='10px system-ui'; gc.textAlign='center';
  gc.fillText('FRENTE',cx,cy-R-6); gc.fillText('TRAS',cx,cy+R+14);
  gc.textAlign='left';  gc.fillText('DIR',cx+R+4,cy+4);
  gc.textAlign='right'; gc.fillText('ESQ',cx-R-4,cy+4);
  gc.textAlign='start';
  const px = v => cx + Math.max(-1,Math.min(1,v/ESCALA))*R;
  const py = v => cy - Math.max(-1,Math.min(1,v/ESCALA))*R;
  if(rastro && rastro.length>1){
    for(let i=1;i<rastro.length;i++){
      gc.strokeStyle='rgba(88,166,255,'+(0.05+0.5*i/rastro.length)+')';
      gc.lineWidth=1.5; gc.beginPath();
      gc.moveTo(px(rastro[i-1][0]), py(rastro[i-1][1]));
      gc.lineTo(px(rastro[i][0]),   py(rastro[i][1])); gc.stroke();
    }
  }
  if(pico > 0.01){
    gc.strokeStyle='#d29922'; gc.lineWidth=1; gc.setLineDash([3,3]);
    gc.beginPath(); gc.arc(cx,cy,Math.min(1,pico/ESCALA)*R,0,7); gc.stroke();
    gc.setLineDash([]);
  }
  const x=px(ay), y=py(ax);
  gc.strokeStyle='rgba(88,166,255,.35)'; gc.lineWidth=1;
  gc.beginPath(); gc.moveTo(cx,cy); gc.lineTo(x,y); gc.stroke();
  gc.fillStyle='#58a6ff'; gc.beginPath(); gc.arc(x,y,7,0,7); gc.fill();
  gc.strokeStyle='#fff'; gc.lineWidth=2; gc.stroke();
}

const hc = document.getElementById('horizonte').getContext('2d');
function desenhaHorizonte(roll, pitch){
  const W=260,H=200,cx=W/2,cy=H/2,R=82;
  hc.clearRect(0,0,W,H); hc.save();
  hc.beginPath(); hc.arc(cx,cy,R,0,7); hc.clip();
  hc.translate(cx,cy); hc.rotate(-roll*Math.PI/180);
  const desl = pitch*2.2;
  hc.fillStyle='#2b6ca3'; hc.fillRect(-200,-200+desl,400,200);
  hc.fillStyle='#6b4a2f'; hc.fillRect(-200,desl,400,200);
  hc.strokeStyle='#fff'; hc.lineWidth=2;
  hc.beginPath(); hc.moveTo(-200,desl); hc.lineTo(200,desl); hc.stroke();
  hc.strokeStyle='rgba(255,255,255,.6)'; hc.lineWidth=1;
  hc.font='8px ui-monospace'; hc.fillStyle='#fff'; hc.textAlign='center';
  for(let p=-30;p<=30;p+=10){
    if(p===0) continue;
    const y=desl-p*2.2, w=(p%20===0)?26:14;
    hc.beginPath(); hc.moveTo(-w,y); hc.lineTo(w,y); hc.stroke();
    if(p%20===0) hc.fillText(Math.abs(p), w+11, y+3);
  }
  hc.restore();
  hc.strokeStyle='#232d38'; hc.lineWidth=2;
  hc.beginPath(); hc.arc(cx,cy,R,0,7); hc.stroke();
  hc.strokeStyle='#d29922'; hc.lineWidth=3; hc.beginPath();
  hc.moveTo(cx-34,cy); hc.lineTo(cx-11,cy); hc.moveTo(cx+11,cy); hc.lineTo(cx+34,cy);
  hc.moveTo(cx,cy-4); hc.lineTo(cx,cy+4); hc.stroke();
}

const bc = document.getElementById('bussola').getContext('2d');
function desenhaBussola(yaw){
  const W=220,H=220,cx=W/2,cy=H/2,R=88;
  bc.clearRect(0,0,W,H); bc.save();
  bc.translate(cx,cy); bc.rotate(-yaw*Math.PI/180);
  bc.strokeStyle='#232d38'; bc.lineWidth=2;
  bc.beginPath(); bc.arc(0,0,R,0,7); bc.stroke();
  bc.textAlign='center'; bc.textBaseline='middle';
  for(let a=0;a<360;a+=15){
    const rad=(a-90)*Math.PI/180, gr=(a%45===0);
    bc.strokeStyle = gr?'#7d8794':'#2c3742'; bc.lineWidth = gr?2:1;
    bc.beginPath();
    bc.moveTo(Math.cos(rad)*R, Math.sin(rad)*R);
    bc.lineTo(Math.cos(rad)*(R-(gr?13:7)), Math.sin(rad)*(R-(gr?13:7)));
    bc.stroke();
  }
  for(const [nome,ang] of [['N',0],['L',90],['S',180],['O',270]]){
    const rad=(ang-90)*Math.PI/180, d=R-30;
    bc.save(); bc.translate(Math.cos(rad)*d, Math.sin(rad)*d);
    bc.rotate(yaw*Math.PI/180);
    bc.fillStyle = nome==='N' ? '#f85149' : '#e6edf3';
    bc.font = (nome==='N'?'bold ':'')+'14px system-ui';
    bc.fillText(nome,0,0); bc.restore();
  }
  bc.restore();
  bc.fillStyle='#d29922'; bc.beginPath();
  bc.moveTo(cx,cy-R-2); bc.lineTo(cx-7,cy-R+13); bc.lineTo(cx+7,cy-R+13);
  bc.closePath(); bc.fill();
  bc.fillStyle='#7d8794'; bc.beginPath(); bc.arc(cx,cy,3,0,7); bc.fill();
}

// ---------------- ciclo ----------------
async function atualizar(){
  let d;
  try { d = await (await fetch('/dados')).json(); }
  catch(e){ document.getElementById('msg').textContent='servidor offline'; return; }

  document.getElementById('dot').className = d.conectado ? 'on' : '';
  document.getElementById('msg').textContent = d.mensagem;
  document.getElementById('r_sat').textContent  = d.sat;
  document.getElementById('r_hdop').textContent = d.hdop ? br(d.hdop,1) : '--';
  document.getElementById('r_g').textContent    = br(d.g_total,2);
  document.getElementById('r_taxa').textContent = br(d.taxa,0);

  // o rumo e usado nas duas abas (cone do mapa e bussola), entao calculamos sempre.
  // d.rumo vem do EstimadorRumo (yaw + COG do GPS, reancorado a cada fix util).
  // Enquanto ele nunca ancorou nao existe rumo geografico - sem magnetometro,
  // parado e sem nunca ter andado, a direcao do Norte e desconhecida. Nesse caso
  // caimos no yaw cru mais o ajuste manual, que e relativo e nao aponta o Norte.
  // Banner do procedimento de partida. Some sozinho quando calibra.
  const cb = document.getElementById('cal_banner');
  if(d.cal_pronto){ cb.style.display='none'; }
  else {
    cb.style.display='block';
    document.getElementById('cal_fase').textContent =
      'PARTIDA - ' + (d.cal_fase||'').toUpperCase();
    document.getElementById('cal_txt').textContent = d.cal_instrucao||'';
    document.getElementById('cal_barra').style.width =
      Math.round((d.cal_progresso||0)*100) + '%';
    let extra = '';
    if(d.cal_fase === 'satelites') extra = d.sat + ' satelites, HDOP ' + br(d.hdop,1);
    else if(d.cal_fase === 'parado') extra = d.kf_parado
      ? 'parado, contando...'
      : 'movimento - ruido |a| ' + br(d.imu_desvio_g,3) + ' g | giro RMS ' +
        br(d.giro_rms_dps,1) + ' graus/s | GPS ' + (d.gps_movendo ? 'movendo' : 'quieto');
    else if(d.cal_fase === 'reto') extra = br(d.cal_dist,1) + ' m retos (precisa 15) | retidao ' +
      br(d.cal_retidao,2) + ' (precisa 0,90) | ' + d.passos + ' passos';
    document.getElementById('cal_extra').textContent = extra;
  }

  yawCru = d.yaw;
  const rumo = d.rumo !== null && d.rumo !== undefined ? d.rumo : corrigeRumo(d.yaw);
  const rumoCone = FONTE_RUMO === 'gps' ? d.rumo_gps : rumo;

  if(abaAtual === 'mapa'){
    document.getElementById('dist').textContent =
      d.distancia_m < 1000 ? Math.round(d.distancia_m) : br(d.distancia_m/1000,2);
    document.getElementById('dist_un').textContent = d.distancia_m<1000 ? 'm' : 'km';
    document.getElementById('m_vel').textContent   = br(d.vel,1);
    document.getElementById('m_vmax').textContent  = br(d.vel_max,1);
    document.getElementById('m_tempo').textContent = duracao(d.decorrido);
    document.getElementById('m_alt').textContent   = d.fix ? Math.round(d.alt) : '--';

    const av = document.getElementById('aviso');
    if(d.descartados > 0){
      av.style.display='block';
      av.textContent = d.descartados + ' ponto(s) descartado(s) por salto anormal';
    } else av.style.display='none';

    document.getElementById('m_kdist').textContent =
      d.kf_distancia_m < 1000 ? Math.round(d.kf_distancia_m) : br(d.kf_distancia_m/1000,2);
    document.getElementById('m_ksig').textContent = d.kf_sigma ? br(d.kf_sigma,1) : '--';
    document.getElementById('m_drerro').textContent =
      d.dr_calibrando ? 'medindo vies...' : (d.dr_erro_m ? br(d.dr_erro_m,0)+' m' : '--');

    if(d.lat !== null && d.lon !== null){
      const usarKF = kalmanOn && d.kf_lat !== null;
      const plat = usarKF ? d.kf_lat : d.lat, plon = usarKF ? d.kf_lon : d.lon;
      document.getElementById('coords').innerHTML =
        'lat '+plat.toFixed(6)+'<br>lon '+plon.toFixed(6)+'<br>'+
        d.n_pontos+' brutos / '+d.kf_n_pontos+' Kalman';
      if(d.kf_pontos.length) rastroKF.setLatLngs(d.kf_pontos);
      if(d.dr_pontos.length){
        rastroDR.setLatLngs(d.dr_pontos);
        if(!mapa.hasLayer(marcadorDR)) marcadorDR.addTo(mapa);
        marcadorDR.setLatLng([d.dr_lat,d.dr_lon]).setStyle({opacity:1});
      }
      marcador.setLatLng([plat,plon]);
      cone.setLatLng([plat,plon]);
      if(!coneEl && cone.getElement())
        coneEl = cone.getElement().querySelector('.cone-rot');
      anguloCone = caminhoCurto(rumoCone, anguloCone);
      if(coneEl) coneEl.style.transform = 'rotate(' + anguloCone + 'deg)';
      if(primeiro){ mapa.setView([plat,plon],19); primeiro=false; }
      if(seguir) mapa.panTo([plat,plon],{animate:true,duration:.4});
    }
    if(d.pontos.length){
      ultimosPontos = d.pontos;
      rastroMapa.setLatLngs(d.pontos);
      if(!marcadorInicio){
        marcadorInicio = L.circleMarker(d.pontos[0],
          {radius:6,color:'#3fb950',weight:2,fillColor:'#0a0e13',fillOpacity:1})
          .addTo(mapa).bindTooltip('Inicio');
      }
    }
  } else {
    document.getElementById('vax').textContent = br(d.ax,3)+' g';
    document.getElementById('vay').textContent = br(d.ay,3)+' g';
    document.getElementById('vaz').textContent = br(d.az,3)+' g';
    barra('bax','pax',d.ax,d.pico_ax,ESCALA);
    barra('bay','pay',d.ay,d.pico_ay,ESCALA);
    barra('baz','paz',d.az,d.pico_az,ESCALA);
    document.getElementById('gtot').textContent  = br(d.g_total,3);
    document.getElementById('gpico').textContent = br(d.pico_total,3);
    document.getElementById('vgx').textContent = br(d.gx,1);
    document.getElementById('vgy').textContent = br(d.gy,1);
    document.getElementById('vgz').textContent = br(d.gz,1);
    barra('bgx',null,d.gx,0,250);
    barra('bgy',null,d.gy,0,250);
    barra('bgz',null,d.gz,0,250);
    document.getElementById('gyropico').textContent = br(d.pico_giro,0);
    document.getElementById('amostras').textContent = d.amostras_imu;
    document.getElementById('vroll').textContent  = br(d.roll,1);
    document.getElementById('vpitch').textContent = br(d.pitch,1);
    document.getElementById('vdesvioacc').textContent = br(d.imu_desvio_g,3);
    document.getElementById('vgrms').textContent = br(d.giro_rms_dps,1);
    document.getElementById('vtemp').textContent  = br(d.temp,1);
    document.getElementById('vyaw').textContent      = br(rumo,1);
    document.getElementById('vyawbruto').textContent = br(d.yaw,1);
    document.getElementById('vancoras').textContent  = d.rumo_ancoragens;
    // Sem ancoragem nenhuma o numero exibido e relativo, nao aponta o Norte.
    document.getElementById('vrumoinc').textContent =
      d.rumo === null || d.rumo === undefined ? '--' : br(d.rumo_incerteza,0);
    const av = document.getElementById('rumo_aviso');
    if(d.rumo === null || d.rumo === undefined){
      av.textContent = 'sem magnetometro: ande alguns metros para o rumo virar geografico';
      av.style.color = '#d29922';
    } else if(!d.rumo_confiavel){
      av.textContent = 'rumo ancorado, mas ainda impreciso (' + br(d.rumo_incerteza,0) + '° de incerteza)';
      av.style.color = '#d29922';
    } else {
      av.textContent = 'rumo geografico valido (±' + br(d.rumo_incerteza,0) + '°)';
      av.style.color = '#3fb950';
    }
    desenhaG(d.ay,d.ax,d.rastro,d.pico_total);
    desenhaHorizonte(d.roll_exib,d.pitch);
    desenhaBussola(rumo);
  }
}

atualizar();
setInterval(atualizar, 80);
</script>
</body>
</html>
"""


def main() -> int:
    global _parar
    p = argparse.ArgumentParser(description="Painel unificado da estacao movel")
    p.add_argument("--porta", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--endereco", default=hex(ENDERECO_PADRAO))
    p.add_argument("--hz", type=float, default=10.0, help="taxa de leitura do IMU")
    p.add_argument("--hz-gps", type=float, default=1.0, dest="hz_gps",
                   help="taxa de leitura do GPS")
    p.add_argument("--rastro", type=int, default=120)
    p.add_argument("--porta-web", type=int, default=8770, dest="porta_web")
    p.add_argument("--csv", default="../data/trajeto.csv", help="'' desativa")
    p.add_argument("--min-dist", type=float, default=3.0, dest="min_dist")
    p.add_argument("--max-salto", type=float, default=500.0, dest="max_salto")
    p.add_argument("--hdop-max", type=float, default=5.0, dest="hdop_max")
    p.add_argument("--simular", action="store_true")
    p.add_argument("--sem-navegador", action="store_true", dest="sem_navegador")
    args = p.parse_args()

    if not args.simular and WTGAHRS3 is None:
        print("ERRO: rode a partir da pasta src/.")
        return 1

    threading.Thread(target=thread_sensor, args=(args,), daemon=True).start()

    servidor = HTTPServer(("127.0.0.1", args.porta_web), Handler)
    url = f"http://localhost:{args.porta_web}"
    print("=" * 58)
    print("  PAINEL DA ESTACAO MOVEL" + ("  [SIMULACAO]" if args.simular else ""))
    print("=" * 58)
    print(f"  Navegador: {url}")
    print(f"  IMU {args.hz:.0f} Hz  |  GPS {args.hz_gps:.0f} Hz")
    if args.csv:
        print(f"  Trajeto em: {args.csv}")
    print("  Ctrl+C para encerrar")
    print("=" * 58)

    if not args.sem_navegador:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    def parar(*a):
        global _parar
        _parar = True
        print("\nEncerrando...")
        threading.Thread(target=servidor.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, parar)
    try:
        servidor.serve_forever()
    finally:
        with _lock:
            print(f"Distancia total: {estado['distancia_m']:.0f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

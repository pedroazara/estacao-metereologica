#!/usr/bin/env python3
"""
Painel de verificacao do WT901C485 - foco no magnetometro.

Le a saida USB do ESP32 rodando o sketch ESP32_Arduino.ino (que ja imprime
acc / gyro / angulo / mag / temperatura em texto) e serve um painel web local
que responde a pergunta pratica: o magnetometro funciona?

    python3 painel_wt901.py --porta /dev/ttyUSB0

Abre em http://localhost:8771

Sem o sensor conectado, para ver a interface:
    python3 painel_wt901.py --simular

IMPORTANTE: o Serial Monitor da IDE do Arduino segura /dev/ttyUSB0 sozinho.
Feche o Serial Monitor antes de rodar isto, ou o painel fica esperando a porta
liberar (ele avisa na tela e continua tentando).

O teste do magnetometro tem tres partes, todas visiveis no painel:

  1. os eixos crus HX/HY/HZ mudam quando voce gira o sensor;
  2. o modulo |H| se mantem aproximadamente constante durante o giro
     (se oscila muito, ha ferro/ima/motor por perto ou o eixo esta morto);
  3. girando o sensor 360 graus deitado, a dispersao XY desenha um circulo.
     Circulo deslocado do centro = hard iron, e normal e corrigivel.
     Borrao sem forma = magnetometro nao esta medindo campo de verdade.
"""

import argparse
import json
import math
import re
import signal
import threading
import urllib.parse
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import serial
except ImportError:
    serial = None


PORTA_HTTP = 8771
BAUD_USB = 115200

# quantos pontos da dispersao XY guardamos para desenhar
MAX_DISPERSAO = 1500
# historico do modulo |H| para o grafico de estabilidade
MAX_HISTORICO_H = 240
# o giro e considerado coberto quando visitamos estes setores de 10 graus
N_SETORES = 36


_lock = threading.Lock()
_parar = threading.Event()

# porta serial aberta, para os botoes conseguirem escrever de volta no sketch.
# O painel ocupa /dev/ttyUSB0 sozinho, entao sem isto os comandos de calibracao
# ficariam inacessiveis enquanto o painel roda.
_serial = {"s": None}
_lock_escrita = threading.Lock()

# so estas teclas existem no sketch (ver processCommand no ESP32_Arduino.ino)
COMANDOS = {
    "a": "calibracao do acelerometro iniciada",
    "m": "calibracao do magnetometro iniciada - gire o sensor nos tres eixos",
    "e": "calibracao do magnetometro encerrada",
    "u": "largura de banda em 5 Hz",
    "U": "largura de banda em 256 Hz",
    "b": "sensor e ESP32 em 9600 baud",
    "B": "sensor e ESP32 em 115200 baud",
    "h": "ajuda pedida ao sketch",
}


def envia_comando(c):
    """Escreve uma tecla de comando na USB do ESP32. Retorna (ok, mensagem)."""
    if c not in COMANDOS:
        return False, f"comando '{c}' nao existe no sketch"
    with _lock_escrita:
        s = _serial["s"]
        if s is None:
            return False, "sem porta serial aberta (simulacao ou porta ocupada)"
        try:
            s.write(c.encode("ascii"))
            s.flush()
        except Exception as e:
            return False, f"falha ao escrever na serial: {e}"
    return True, COMANDOS[c]

estado = {
    "conectado": False,
    "mensagem": "iniciando",
    "porta": "",
    "amostras": 0,
    "taxa_hz": 0.0,
    "ultimo_dado": 0.0,

    "ax": 0.0, "ay": 0.0, "az": 0.0,
    "gx": 0.0, "gy": 0.0, "gz": 0.0,
    "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
    "hx": 0, "hy": 0, "hz": 0,
    "temp": 0.0,

    # magnetometro derivado
    "h_modulo": 0.0,
    "h_modulo_min": None,
    "h_modulo_max": None,
    "rumo_mag": None,          # atan2 simples, assume sensor nivelado
    "rumo_mag_tilt": None,     # compensado por roll/pitch
    "offset": {"x": 0.0, "y": 0.0, "z": 0.0},
    "raio": 0.0,
    "circularidade": None,     # desvio dos raios / raio medio; baixo = circulo bom
    "cobertura": 0,            # 0..36 setores visitados
    "dispersao": [],           # [[x, y], ...] ja sem offset
    "historico_h": [],
    "mag_vivo": False,         # os eixos realmente variam?

    "linhas": [],              # ultimas linhas cruas, para diagnostico
}

# limites por eixo para estimar hard iron (min/max)
_lim = {"x": [None, None], "y": [None, None], "z": [None, None]}
_setores = set()
_dispersao = deque(maxlen=MAX_DISPERSAO)
_historico_h = deque(maxlen=MAX_HISTORICO_H)
_linhas = deque(maxlen=12)
# amplitude observada em cada eixo, para decidir se o magnetometro esta vivo
_amplitude = {"x": 0, "y": 0, "z": 0}
_marcas_tempo = deque(maxlen=40)


# --------------------------------------------------------------------------
# parsing da saida do sketch
# --------------------------------------------------------------------------
# acc[g]: 0.001 0.002 0.998
# gyro[graus/s]: -0.061 0.122 0.000
# angulo[graus]: 0.11 -0.34 128.75
# mag: 123 -456 789
# temperatura[C]: 28.50
RE_ACC = re.compile(r"acc\[g\]:\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)")
RE_GYRO = re.compile(r"gyro\[graus/s\]:\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)")
RE_ANG = re.compile(r"angulo\[graus\]:\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)")
RE_MAG = re.compile(r"mag:\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)")
RE_TEMP = re.compile(r"temperatura\[C\]:\s*(-?[\d.]+)")


def _registra_mag(hx, hy, hz):
    """Atualiza tudo que deriva do magnetometro: offset, dispersao, rumo.

    A dispersao guarda os pontos CRUS. O offset (hard iron) so melhora conforme
    o giro avanca, entao os pontos sao recorrigidos a cada amostra com o offset
    corrente - senao os primeiros pontos ficariam presos a um centro errado.

    Roda inteira sob _lock: o handler HTTP le _dispersao segurando o mesmo lock,
    e iterar um deque enquanto outra thread faz append levanta RuntimeError.
    """
    with _lock:
        for eixo, v in (("x", hx), ("y", hy), ("z", hz)):
            lo, hi = _lim[eixo]
            _lim[eixo][0] = v if lo is None else min(lo, v)
            _lim[eixo][1] = v if hi is None else max(hi, v)
            _amplitude[eixo] = _lim[eixo][1] - _lim[eixo][0]

        off = {}
        for eixo in ("x", "y", "z"):
            lo, hi = _lim[eixo]
            off[eixo] = (lo + hi) / 2.0 if lo is not None else 0.0

        modulo = math.sqrt(hx * hx + hy * hy + hz * hz)
        _dispersao.append([hx, hy])
        _historico_h.append(modulo)

        # recorrige tudo com o offset corrente e refaz raio, cobertura e circulo
        _setores.clear()
        raios = []
        for px, py in _dispersao:
            qx, qy = px - off["x"], py - off["y"]
            r = math.hypot(qx, qy)
            raios.append(r)
            if r > 1.0:
                _setores.add(int((math.degrees(math.atan2(qy, qx)) % 360) // 10))

        raio = sum(raios) / len(raios) if raios else 0.0
        if raio > 0 and len(raios) > 4:
            desvio = math.sqrt(sum((r - raio) ** 2 for r in raios) / len(raios))
            circularidade = desvio / raio
        else:
            circularidade = None

        cx, cy, cz = hx - off["x"], hy - off["y"], hz - off["z"]
        raio_xy = math.hypot(cx, cy)

        # rumo magnetico com o sensor deitado
        rumo = math.degrees(math.atan2(-cy, cx)) % 360 if raio_xy > 0.5 else None

        # rumo compensado por inclinacao, usando roll/pitch do proprio sensor
        r = math.radians(estado["roll"])
        p = math.radians(estado["pitch"])
        mx = cx * math.cos(p) + cz * math.sin(p)
        my = (cx * math.sin(r) * math.sin(p) + cy * math.cos(r)
              - cz * math.sin(r) * math.cos(p))
        rumo_tilt = math.degrees(math.atan2(-my, mx)) % 360 if (mx or my) else None

        # o magnetometro so e "vivo" se algum eixo variou de verdade
        vivo = (max(_amplitude.values()) > 20) and modulo > 0

        estado["hx"], estado["hy"], estado["hz"] = hx, hy, hz
        estado["h_modulo"] = modulo
        lo = estado["h_modulo_min"]
        hi = estado["h_modulo_max"]
        estado["h_modulo_min"] = modulo if lo is None else min(lo, modulo)
        estado["h_modulo_max"] = modulo if hi is None else max(hi, modulo)
        estado["offset"] = off
        estado["raio"] = raio
        estado["circularidade"] = circularidade
        estado["cobertura"] = len(_setores)
        estado["rumo_mag"] = rumo
        estado["rumo_mag_tilt"] = rumo_tilt
        estado["mag_vivo"] = vivo


def processa_linha(linha):
    """Le uma linha de texto do sketch e atualiza o estado. Retorna True se era dado."""
    linha = linha.strip()
    if not linha:
        return False

    achou = False

    m = RE_ACC.search(linha)
    if m:
        with _lock:
            estado["ax"], estado["ay"], estado["az"] = (float(g) for g in m.groups())
        achou = True

    m = RE_GYRO.search(linha)
    if m:
        with _lock:
            estado["gx"], estado["gy"], estado["gz"] = (float(g) for g in m.groups())
        achou = True

    m = RE_ANG.search(linha)
    if m:
        with _lock:
            estado["roll"], estado["pitch"], estado["yaw"] = (float(g) for g in m.groups())
        achou = True

    m = RE_TEMP.search(linha)
    if m:
        with _lock:
            estado["temp"] = float(m.group(1))
        achou = True

    # o magnetometro vem por ultimo porque usa roll/pitch ja atualizados
    m = RE_MAG.search(linha)
    if m:
        _registra_mag(*(int(g) for g in m.groups()))
        achou = True
        # cada leitura do sensor imprime 5 linhas; so a do mag conta como amostra,
        # senao a taxa sai 5x maior do que a real
        agora = time.time()
        _marcas_tempo.append(agora)
        with _lock:
            estado["amostras"] += 1
            if len(_marcas_tempo) > 3:
                intervalo = _marcas_tempo[-1] - _marcas_tempo[0]
                if intervalo > 0:
                    estado["taxa_hz"] = (len(_marcas_tempo) - 1) / intervalo

    if achou:
        with _lock:
            estado["ultimo_dado"] = time.time()
    else:
        # texto do sketch (ajuda, "Sensor encontrado...", erros) vai para o log
        with _lock:
            _linhas.append(linha)
            estado["linhas"] = list(_linhas)

    return achou


# --------------------------------------------------------------------------
# leitura da serial
# --------------------------------------------------------------------------
def thread_serial(porta, baud):
    if serial is None:
        with _lock:
            estado["mensagem"] = "pyserial nao instalado (pip install pyserial)"
        return

    while not _parar.is_set():
        s = None
        try:
            with _lock:
                estado["mensagem"] = f"abrindo {porta}..."
            s = serial.Serial(porta, baud, timeout=0.5)
            time.sleep(0.3)
            s.reset_input_buffer()
            with _lock_escrita:
                _serial["s"] = s
            with _lock:
                estado["conectado"] = True
                estado["porta"] = porta
                estado["mensagem"] = f"lendo {porta} @ {baud}"

            buf = b""
            while not _parar.is_set():
                pedaco = s.read(256)
                if pedaco:
                    buf += pedaco
                    while b"\n" in buf:
                        linha, buf = buf.split(b"\n", 1)
                        processa_linha(linha.decode("utf-8", "replace"))
                elif time.time() - estado["ultimo_dado"] > 5 and estado["amostras"]:
                    with _lock:
                        estado["mensagem"] = "porta aberta, mas o sensor parou de enviar"

        except Exception as e:
            texto = str(e)
            if "Device or resource busy" in texto or "Errno 16" in texto:
                aviso = (f"{porta} ocupada - feche o Serial Monitor da IDE "
                         f"do Arduino e o painel conecta sozinho")
            elif "No such file" in texto or "Errno 2" in texto:
                aviso = f"{porta} nao existe - o ESP32 esta plugado?"
            elif "Permission denied" in texto or "Errno 13" in texto:
                aviso = f"sem permissao em {porta} - falta estar no grupo dialout"
            else:
                aviso = f"erro na serial: {texto}"
            with _lock:
                estado["conectado"] = False
                estado["mensagem"] = aviso
        finally:
            with _lock_escrita:
                _serial["s"] = None
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass

        _parar.wait(2.0)


def thread_simulacao():
    """Gera dados plausiveis: sensor girando devagar, com hard iron proposital."""
    with _lock:
        estado["conectado"] = True
        estado["porta"] = "simulacao"
        estado["mensagem"] = "modo simulacao - nenhum sensor real conectado"

    campo = 780.0          # intensidade do campo, em contagens
    off_x, off_y, off_z = 120.0, -60.0, 35.0   # hard iron artificial
    inclinacao = math.radians(-35.0)
    t0 = time.time()

    while not _parar.is_set():
        t = time.time() - t0
        yaw = (t * 24.0) % 360.0
        rad = math.radians(yaw)
        roll = 2.0 * math.sin(t * 0.7)
        pitch = 1.5 * math.cos(t * 0.5)

        h = campo * math.cos(inclinacao)
        hx = h * math.cos(rad) + off_x
        hy = -h * math.sin(rad) + off_y
        hz = campo * math.sin(inclinacao) + off_z

        processa_linha(f"acc[g]: {0.01 * math.sin(t):.3f} "
                       f"{0.01 * math.cos(t):.3f} {0.998:.3f}")
        processa_linha(f"gyro[graus/s]: {0.2 * math.sin(t * 3):.3f} "
                       f"{0.2 * math.cos(t * 2):.3f} {24.0:.3f}")
        processa_linha(f"angulo[graus]: {roll:.2f} {pitch:.2f} {yaw:.2f}")
        processa_linha(f"mag: {int(hx)} {int(hy)} {int(hz)}")
        processa_linha(f"temperatura[C]: {28.4 + 0.3 * math.sin(t / 20):.2f}")

        _parar.wait(0.1)


# --------------------------------------------------------------------------
# servidor
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
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
                ox, oy = estado["offset"]["x"], estado["offset"]["y"]
                d["dispersao"] = [[px - ox, py - oy] for px, py in _dispersao]
                d["historico_h"] = list(_historico_h)
                d["idade"] = (time.time() - estado["ultimo_dado"]
                              if estado["ultimo_dado"] else 999)
            self._envia(json.dumps(d))
        elif p.startswith("/comando"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(p).query)
            c = (q.get("c") or [""])[0]
            ok, msg = envia_comando(c)
            with _lock:
                _linhas.append(("> " if ok else "! ") + msg)
                estado["linhas"] = list(_linhas)
            self._envia(json.dumps({"ok": ok, "msg": msg}))
        elif p.startswith("/recalibrar"):
            with _lock:
                for eixo in ("x", "y", "z"):
                    _lim[eixo] = [None, None]
                    _amplitude[eixo] = 0
                _setores.clear()
                _dispersao.clear()
                _historico_h.clear()
                estado["h_modulo_min"] = None
                estado["h_modulo_max"] = None
                estado["cobertura"] = 0
                estado["raio"] = 0.0
                estado["circularidade"] = None
                estado["mag_vivo"] = False
            self._envia('{"ok":true}')
        else:
            self._envia(PAGINA, "text/html")


PAGINA = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WT901C485 - Verificacao do Magnetometro</title>
<style>
  :root{--bg:#0a0e13;--card:#141b23;--borda:#232d38;--txt:#e6edf3;--txt2:#7d8794;
        --x:#f85149;--y:#3fb950;--z:#58a6ff;--alerta:#d29922}
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{min-height:100%;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
            background:var(--bg);color:var(--txt)}
  #topo{height:52px;display:flex;align-items:center;gap:14px;padding:0 16px;
        background:var(--card);border-bottom:1px solid var(--borda);
        position:sticky;top:0;z-index:10}
  #dot{width:9px;height:9px;border-radius:50%;background:var(--x);flex:none}
  #dot.on{background:var(--y);box-shadow:0 0 8px var(--y)}
  #marca{font-size:14px;font-weight:600;white-space:nowrap}
  #msg{font-size:11px;color:var(--txt2)}
  #resumo{margin-left:auto;display:flex;gap:16px;font-size:11px;color:var(--txt2);
          font-family:ui-monospace,Menlo,monospace;white-space:nowrap}
  #resumo b{color:var(--txt);font-weight:600}
  #grade{display:grid;gap:14px;padding:14px;
         grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
         max-width:1500px;margin:0 auto}
  .cartao{background:var(--card);border:1px solid var(--borda);border-radius:12px;
          padding:15px}
  .rotulo{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--txt2);
          margin-bottom:10px}
  .grande{font-size:34px;font-weight:700;line-height:1;letter-spacing:-1px;
          font-variant-numeric:tabular-nums}
  .unidade{font-size:14px;color:var(--txt2);margin-left:4px}
  canvas{display:block;width:100%;background:#0d141c;border-radius:8px;
         border:1px solid var(--borda)}
  table{width:100%;border-collapse:collapse;font-family:ui-monospace,Menlo,monospace;
        font-size:13px}
  td{padding:5px 0;border-bottom:1px solid #1b232c}
  tr:last-child td{border-bottom:none}
  td.k{color:var(--txt2);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
  td.v{text-align:right;font-variant-numeric:tabular-nums}
  .tri{display:flex;gap:10px;margin-top:4px}
  .tri div{flex:1;text-align:center;background:#0d141c;border-radius:7px;padding:8px 4px;
           border:1px solid var(--borda)}
  .tri span{display:block;font-size:10px;color:var(--txt2);margin-bottom:3px}
  .tri b{font-size:16px;font-variant-numeric:tabular-nums;font-weight:600}
  .diag{display:flex;gap:9px;align-items:flex-start;padding:9px 0;
        border-bottom:1px solid #1b232c;font-size:12.5px;line-height:1.45}
  .diag:last-child{border-bottom:none}
  .bolha{width:8px;height:8px;border-radius:50%;flex:none;margin-top:5px}
  .ok{background:var(--y)} .ruim{background:var(--x)} .esperando{background:var(--txt2)}
  .aviso{background:var(--alerta)}
  .diag i{font-style:normal;color:var(--txt2);display:block;font-size:11px;margin-top:2px}
  button{background:transparent;border:1px solid var(--borda);color:var(--txt2);
         border-radius:7px;padding:7px 14px;font-size:12px;cursor:pointer;
         font-family:inherit;margin-top:12px}
  button:hover{border-color:var(--z);color:var(--z)}
  button:disabled{opacity:.4;cursor:not-allowed}
  button:disabled:hover{border-color:var(--borda);color:var(--txt2)}
  .subgrupo{font-size:10px;text-transform:uppercase;letter-spacing:1px;
            color:var(--txt2);margin-top:14px;opacity:.7}
  .subgrupo:first-of-type{margin-top:0}
  .botoes{display:flex;flex-wrap:wrap;gap:8px}
  .botoes button{margin-top:6px;display:flex;align-items:center;gap:8px}
  .botoes button i{font-style:normal;font-family:ui-monospace,Menlo,monospace;
       background:#0d141c;border:1px solid var(--borda);border-radius:4px;
       padding:1px 6px;font-size:11px;color:var(--txt)}
  #cmd_msg{font-size:11.5px;margin-top:14px;min-height:16px;color:var(--txt2);
           font-family:ui-monospace,Menlo,monospace}
  #cmd_msg.bom{color:var(--y)} #cmd_msg.mau{color:var(--x)}
  .nota{font-size:11px;color:var(--txt2);margin-top:10px;line-height:1.5;
        padding-top:10px;border-top:1px solid #1b232c}
  #log{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--txt2);
       white-space:pre-wrap;word-break:break-word;max-height:150px;overflow-y:auto;
       line-height:1.5}
  .barra{height:6px;background:#0d141c;border-radius:3px;overflow:hidden;margin-top:8px;
         border:1px solid var(--borda)}
  .barra div{height:100%;background:var(--z);width:0;transition:width .2s}
</style>
</head>
<body>

<div id="topo">
  <div id="dot"></div>
  <div>
    <div id="marca">WT901C485 - verificacao do magnetometro</div>
    <div id="msg">conectando...</div>
  </div>
  <div id="resumo">
    <span>taxa <b id="r_hz">-</b> Hz</span>
    <span>amostras <b id="r_n">0</b></span>
    <span>temp <b id="r_t">-</b> C</span>
  </div>
</div>

<div id="grade">

  <div class="cartao">
    <div class="rotulo">Bussola</div>
    <canvas id="c_bussola" width="640" height="640" style="max-width:340px;margin:0 auto"></canvas>
    <div class="tri">
      <div><span>rumo magnetico</span><b id="v_rumo" style="color:var(--z)">-</b></div>
      <div><span>yaw do sensor</span><b id="v_yaw" style="color:var(--alerta)">-</b></div>
      <div><span>diferenca</span><b id="v_dif">-</b></div>
    </div>
  </div>

  <div class="cartao">
    <div class="rotulo">Dispersao XY - gire o sensor deitado 360 graus</div>
    <canvas id="c_disp" width="640" height="640" style="max-width:340px;margin:0 auto"></canvas>
    <div class="barra"><div id="b_cob"></div></div>
    <table style="margin-top:10px">
      <tr><td class="k">cobertura do giro</td><td class="v" id="v_cob">0 / 36 setores</td></tr>
      <tr><td class="k">raio medio</td><td class="v" id="v_raio">-</td></tr>
      <tr><td class="k">desvio do circulo</td><td class="v" id="v_circ">-</td></tr>
      <tr><td class="k">hard iron X / Y / Z</td><td class="v" id="v_off">-</td></tr>
    </table>
    <button onclick="recalibrar()">Reiniciar coleta</button>
  </div>

  <div class="cartao">
    <div class="rotulo">Modulo do campo |H| - valor cru, sem tirar hard iron</div>
    <div><span class="grande" id="v_mod">-</span><span class="unidade">contagens</span></div>
    <canvas id="c_hist" width="640" height="200" style="margin-top:12px"></canvas>
    <table style="margin-top:10px">
      <tr><td class="k">minimo observado</td><td class="v" id="v_hmin">-</td></tr>
      <tr><td class="k">maximo observado</td><td class="v" id="v_hmax">-</td></tr>
      <tr><td class="k">variacao</td><td class="v" id="v_hvar">-</td></tr>
    </table>
  </div>

  <div class="cartao">
    <div class="rotulo">Diagnostico</div>
    <div id="diagnostico"></div>
  </div>

  <div class="cartao">
    <div class="rotulo">Comandos do sketch</div>
    <div class="subgrupo">Calibracao</div>
    <div class="botoes">
      <button onclick="cmd('a')">Calibrar acelerometro<i>a</i></button>
      <button onclick="cmd('m')">Iniciar cali. magnetometro<i>m</i></button>
      <button onclick="cmd('e')">Encerrar cali. magnetometro<i>e</i></button>
    </div>
    <div class="subgrupo">Largura de banda</div>
    <div class="botoes">
      <button onclick="cmd('u')">5 Hz - suave<i>u</i></button>
      <button onclick="cmd('U')">256 Hz - rapido<i>U</i></button>
    </div>
    <div class="subgrupo">Baud do RS485</div>
    <div class="botoes">
      <button onclick="cmd('b')">9600<i>b</i></button>
      <button onclick="cmd('B')">115200<i>B</i></button>
      <button onclick="cmd('h')">Ajuda<i>h</i></button>
    </div>
    <div id="cmd_msg"></div>
    <div class="nota">O sketch nao grava nada na flash: a calibracao e o baud
      voltam ao anterior quando o sensor e desligado.</div>
  </div>

  <div class="cartao">
    <div class="rotulo">Leituras cruas</div>
    <table>
      <tr><td class="k">magnetometro X Y Z</td><td class="v" id="v_mag">-</td></tr>
      <tr><td class="k">acelerometro (g)</td><td class="v" id="v_acc">-</td></tr>
      <tr><td class="k">giroscopio (graus/s)</td><td class="v" id="v_gyro">-</td></tr>
      <tr><td class="k">roll / pitch / yaw</td><td class="v" id="v_ang">-</td></tr>
      <tr><td class="k">temperatura</td><td class="v" id="v_temp">-</td></tr>
      <tr><td class="k">rumo compensado</td><td class="v" id="v_rumot">-</td></tr>
    </table>
  </div>

  <div class="cartao">
    <div class="rotulo">Texto do sketch</div>
    <div id="log">aguardando...</div>
  </div>

</div>

<script>
// ---------------------------------------------------------------- utilitarios
const $ = id => document.getElementById(id);
const f = (v, n = 2) => (v === null || v === undefined) ? '-' : Number(v).toFixed(n);

function ajusta(cv){
  // canvas nitido em tela hidpi, sem depender de biblioteca
  const r = window.devicePixelRatio || 1;
  const l = cv.clientWidth, a = cv.clientHeight || cv.clientWidth;
  if(cv.width !== l * r || cv.height !== a * r){
    cv.width = l * r; cv.height = a * r;
  }
  const ctx = cv.getContext('2d');
  ctx.setTransform(r, 0, 0, r, 0, 0);
  return {ctx, l, a};
}

// ------------------------------------------------------------------- bussola
function desenhaBussola(rumo, yaw){
  const cv = $('c_bussola');
  cv.style.height = cv.clientWidth + 'px';
  const {ctx, l} = ajusta(cv);
  const cx = l / 2, cy = l / 2, R = l * 0.42;

  ctx.clearRect(0, 0, l, l);

  ctx.strokeStyle = '#232d38'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(cx, cy, R * 0.72, 0, Math.PI * 2); ctx.stroke();

  // marcas de grau
  for(let g = 0; g < 360; g += 15){
    const a = (g - 90) * Math.PI / 180;
    const grande = (g % 45 === 0);
    const r1 = R * (grande ? 0.88 : 0.94);
    ctx.strokeStyle = grande ? '#7d8794' : '#232d38';
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
    ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
    ctx.stroke();
  }

  // rosa dos ventos
  ctx.fillStyle = '#7d8794';
  ctx.font = '600 ' + Math.round(l * 0.045) + 'px system-ui';
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  [['N', 0], ['L', 90], ['S', 180], ['O', 270]].forEach(([t, g]) => {
    const a = (g - 90) * Math.PI / 180;
    ctx.fillStyle = (t === 'N') ? '#f85149' : '#7d8794';
    ctx.fillText(t, cx + Math.cos(a) * R * 0.82, cy + Math.sin(a) * R * 0.82);
  });

  function agulha(g, cor, comp, larg){
    if(g === null || g === undefined) return;
    const a = (g - 90) * Math.PI / 180;
    ctx.strokeStyle = cor; ctx.lineWidth = larg; ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(cx - Math.cos(a) * R * 0.12, cy - Math.sin(a) * R * 0.12);
    ctx.lineTo(cx + Math.cos(a) * R * comp, cy + Math.sin(a) * R * comp);
    ctx.stroke();
  }

  agulha(yaw, '#d29922', 0.60, 3);    // yaw do proprio sensor
  agulha(rumo, '#58a6ff', 0.78, 4);   // rumo calculado do magnetometro

  ctx.fillStyle = '#e6edf3';
  ctx.beginPath(); ctx.arc(cx, cy, 4, 0, Math.PI * 2); ctx.fill();
}

// ----------------------------------------------------------------- dispersao
function desenhaDispersao(pts, raio){
  const cv = $('c_disp');
  cv.style.height = cv.clientWidth + 'px';
  const {ctx, l} = ajusta(cv);
  const cx = l / 2, cy = l / 2;

  ctx.clearRect(0, 0, l, l);

  // escala: o maior raio observado ocupa 80% do quadro
  let maxr = raio * 1.4;
  for(const [x, y] of pts) maxr = Math.max(maxr, Math.hypot(x, y));
  if(!(maxr > 0)) maxr = 1;
  const k = (l * 0.42) / maxr;

  // eixos
  ctx.strokeStyle = '#232d38'; ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cx, l * 0.05); ctx.lineTo(cx, l * 0.95);
  ctx.moveTo(l * 0.05, cy); ctx.lineTo(l * 0.95, cy);
  ctx.stroke();

  // circulo de referencia no raio medio
  if(raio > 0){
    ctx.strokeStyle = '#3fb95055';
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.arc(cx, cy, raio * k, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([]);
  }

  // pontos, mais recentes mais brilhantes
  const n = pts.length;
  for(let i = 0; i < n; i++){
    const [x, y] = pts[i];
    const idade = i / Math.max(1, n - 1);
    ctx.fillStyle = `rgba(88,166,255,${0.15 + 0.65 * idade})`;
    ctx.beginPath();
    ctx.arc(cx + x * k, cy - y * k, idade > 0.98 ? 4 : 1.7, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.fillStyle = '#7d8794';
  ctx.font = '11px ui-monospace,monospace';
  ctx.textAlign = 'left'; ctx.textBaseline = 'top';
  ctx.fillText('HX ->', l - 46, cy + 6);
  ctx.fillText('HY ^', cx + 6, 6);
}

// ------------------------------------------------------------- historico |H|
function desenhaHistorico(h){
  const cv = $('c_hist');
  cv.style.height = '110px';
  const {ctx, l, a} = ajusta(cv);
  ctx.clearRect(0, 0, l, a);
  if(h.length < 2) return;

  let lo = Math.min(...h), hi = Math.max(...h);
  const meio = (lo + hi) / 2;
  // nunca deixa a escala colapsar, senao ruido vira montanha
  if(hi - lo < meio * 0.08){ lo = meio * 0.96; hi = meio * 1.04; }
  const pad = (hi - lo) * 0.15;
  lo -= pad; hi += pad;

  ctx.strokeStyle = '#232d38';
  ctx.beginPath(); ctx.moveTo(0, a / 2); ctx.lineTo(l, a / 2); ctx.stroke();

  ctx.strokeStyle = '#58a6ff'; ctx.lineWidth = 1.8; ctx.lineJoin = 'round';
  ctx.beginPath();
  h.forEach((v, i) => {
    const x = (i / (h.length - 1)) * l;
    const y = a - ((v - lo) / (hi - lo)) * a;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
}

// ---------------------------------------------------------------- diagnostico
function diagnostica(d){
  const itens = [];
  const chegando = d.idade < 3 && d.taxa_hz > 0.2;

  itens.push(chegando
    ? ['ok', 'Dados chegando do sensor', `${f(d.taxa_hz, 1)} amostras por segundo`]
    : ['ruim', 'Nenhum dado chegando', d.mensagem]);

  if(!chegando){
    itens.push(['esperando', 'Testes do magnetometro aguardando dados', '']);
  } else {
    const amp = Math.max(
      Math.abs(d.hx), Math.abs(d.hy), Math.abs(d.hz));

    if(d.hx === 0 && d.hy === 0 && d.hz === 0){
      itens.push(['ruim', 'Os tres eixos estao em zero',
        'o magnetometro nao esta respondendo - registradores 0x3A-0x3C zerados']);
    } else if(!d.mag_vivo){
      itens.push(['esperando', 'Eixos ainda quase parados',
        'gire o sensor para ver se os valores mudam de verdade']);
    } else {
      itens.push(['ok', 'Os eixos variam quando o sensor gira',
        `leitura atual ${d.hx} / ${d.hy} / ${d.hz}`]);
    }

    // cobertura do giro
    if(d.cobertura >= 30){
      itens.push(['ok', 'Giro completo coberto',
        'a dispersao XY ja pode ser lida como circulo']);
    } else {
      itens.push(['esperando', `Giro incompleto: ${d.cobertura} de 36 setores`,
        'gire devagar o sensor deitado ate fechar a volta']);
    }

    // formato do circulo - este e o teste que de fato decide se o sensor mede campo
    if(d.circularidade === null || d.cobertura < 12){
      itens.push(['esperando', 'Formato da dispersao ainda indefinido',
        'precisa de mais giro para julgar']);
    } else {
      const c = d.circularidade * 100;
      if(c < 15){
        itens.push(['ok', `Dispersao desenha um circulo (desvio ${f(c, 0)}%)`,
          'assinatura de um magnetometro medindo o campo da Terra de verdade']);
      } else if(c < 35){
        itens.push(['aviso', `Circulo deformado (desvio ${f(c, 0)}%)`,
          'soft iron, giro fora do plano horizontal, ou metal por perto']);
      } else {
        itens.push(['ruim', `Sem forma de circulo (desvio ${f(c, 0)}%)`,
          'os eixos nao estao medindo um campo coerente - suspeite do sensor '
          + 'ou de interferencia forte']);
      }
    }

    // hard iron
    const off = Math.hypot(d.offset.x, d.offset.y);
    if(d.cobertura >= 20 && d.raio > 0){
      const rel = off / d.raio;
      if(rel < 0.25){
        itens.push(['ok', 'Hard iron pequeno',
          `centro deslocado ${f(off, 0)} contra raio ${f(d.raio, 0)}`]);
      } else {
        itens.push(['aviso', 'Hard iron significativo',
          `centro deslocado ${f(off, 0)} contra raio ${f(d.raio, 0)} - `
          + 'calibre o magnetometro com a tecla m / e no Serial Monitor']);
      }
    }

    // concordancia com o yaw do proprio sensor
    if(d.rumo_mag !== null && d.cobertura >= 8){
      let dif = Math.abs(((d.rumo_mag - d.yaw + 540) % 360) - 180);
      itens.push(dif < 25
        ? ['ok', 'Rumo calculado bate com o yaw do sensor',
           `diferenca de ${f(dif, 0)} graus`]
        : ['aviso', 'Rumo calculado difere do yaw do sensor',
           `diferenca de ${f(dif, 0)} graus - convencao de eixo/sinal pode `
           + 'estar invertida, ou o yaw ainda nao esta fundido com a bussola']);
    }
  }

  $('diagnostico').innerHTML = itens.map(([cls, t, sub]) =>
    `<div class="diag"><div class="bolha ${cls}"></div><div>${t}`
    + (sub ? `<i>${sub}</i>` : '') + `</div></div>`).join('');
}

// ---------------------------------------------------------------------- ciclo
function recalibrar(){ fetch('/recalibrar'); }

async function cmd(c){
  const alvo = $('cmd_msg');
  alvo.className = '';
  alvo.textContent = `enviando '${c}'...`;
  try{
    const r = await (await fetch('/comando?c=' + encodeURIComponent(c))).json();
    alvo.className = r.ok ? 'bom' : 'mau';
    alvo.textContent = (r.ok ? "enviado '" + c + "': " : 'falhou: ') + r.msg;
    // depois de mexer no magnetometro os pontos antigos nao valem mais
    if(r.ok && (c === 'm' || c === 'e')) fetch('/recalibrar');
  }catch(e){
    alvo.className = 'mau';
    alvo.textContent = 'sem contato com o processo python';
  }
}

async function tick(){
  let d;
  try{
    d = await (await fetch('/dados')).json();
  }catch(e){
    $('msg').textContent = 'painel sem contato com o processo python';
    $('dot').classList.remove('on');
    return;
  }

  const vivo = d.idade < 3 && d.taxa_hz > 0.2;
  $('dot').classList.toggle('on', vivo);
  $('msg').textContent = d.mensagem;

  // sem porta serial real nao ha para onde mandar comando
  const podeCmd = d.conectado && d.porta && d.porta !== 'simulacao';
  document.querySelectorAll('.botoes button').forEach(b => b.disabled = !podeCmd);
  $('r_hz').textContent = f(d.taxa_hz, 1);
  $('r_n').textContent = d.amostras;
  $('r_t').textContent = f(d.temp, 1);

  desenhaBussola(d.rumo_mag, d.yaw);
  desenhaDispersao(d.dispersao, d.raio);
  desenhaHistorico(d.historico_h);

  $('v_rumo').textContent = d.rumo_mag === null ? '-' : f(d.rumo_mag, 0) + '°';
  $('v_yaw').textContent = f(d.yaw, 0) + '°';
  if(d.rumo_mag !== null){
    const dif = Math.abs(((d.rumo_mag - d.yaw + 540) % 360) - 180);
    $('v_dif').textContent = f(dif, 0) + '°';
  } else $('v_dif').textContent = '-';

  $('v_cob').textContent = d.cobertura + ' / 36 setores';
  $('b_cob').style.width = (d.cobertura / 36 * 100) + '%';
  $('v_raio').textContent = f(d.raio, 0);
  $('v_circ').textContent = d.circularidade === null
    ? '-' : f(d.circularidade * 100, 0) + '%';
  $('v_off').textContent = `${f(d.offset.x, 0)} / ${f(d.offset.y, 0)} / ${f(d.offset.z, 0)}`;

  $('v_mod').textContent = f(d.h_modulo, 0);
  $('v_hmin').textContent = f(d.h_modulo_min, 0);
  $('v_hmax').textContent = f(d.h_modulo_max, 0);
  if(d.h_modulo_min !== null && d.h_modulo_max !== null){
    const media = (d.h_modulo_min + d.h_modulo_max) / 2;
    $('v_hvar').textContent = media > 0
      ? f((d.h_modulo_max - d.h_modulo_min) / media * 100, 0) + '%' : '-';
  }

  $('v_mag').textContent = `${d.hx}  ${d.hy}  ${d.hz}`;
  $('v_acc').textContent = `${f(d.ax, 3)}  ${f(d.ay, 3)}  ${f(d.az, 3)}`;
  $('v_gyro').textContent = `${f(d.gx, 2)}  ${f(d.gy, 2)}  ${f(d.gz, 2)}`;
  $('v_ang').textContent = `${f(d.roll, 1)}  ${f(d.pitch, 1)}  ${f(d.yaw, 1)}`;
  $('v_temp').textContent = f(d.temp, 2) + ' C';
  $('v_rumot').textContent = d.rumo_mag_tilt === null
    ? '-' : f(d.rumo_mag_tilt, 0) + '°';

  $('log').textContent = (d.linhas || []).join('\n') || 'nenhuma mensagem do sketch';

  diagnostica(d);
}

setInterval(tick, 150);
tick();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(
        description="Painel de verificacao do magnetometro do WT901C485")
    ap.add_argument("--porta", default="/dev/ttyUSB0",
                    help="porta serial do ESP32 (padrao /dev/ttyUSB0)")
    ap.add_argument("--baud", type=int, default=BAUD_USB,
                    help=f"baud da USB do ESP32 (padrao {BAUD_USB})")
    ap.add_argument("--http", type=int, default=PORTA_HTTP,
                    help=f"porta do painel web (padrao {PORTA_HTTP})")
    ap.add_argument("--simular", action="store_true",
                    help="gera dados falsos, sem sensor")
    ap.add_argument("--sem-navegador", action="store_true",
                    help="nao abre o navegador sozinho")
    args = ap.parse_args()

    t = (threading.Thread(target=thread_simulacao, daemon=True) if args.simular
         else threading.Thread(target=thread_serial,
                               args=(args.porta, args.baud), daemon=True))
    t.start()

    srv = HTTPServer(("0.0.0.0", args.http), Handler)
    url = f"http://localhost:{args.http}"

    def encerra(*_):
        _parar.set()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, encerra)
    signal.signal(signal.SIGTERM, encerra)

    print(f"painel em {url}")
    if args.simular:
        print("modo simulacao - nenhum sensor real sendo lido")
    else:
        print(f"lendo {args.porta} @ {args.baud}")
        print("se a porta estiver ocupada, feche o Serial Monitor da IDE do Arduino")
    print("Ctrl+C para sair")

    if not args.sem_navegador:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    finally:
        _parar.set()
        print("\nencerrado")


if __name__ == "__main__":
    main()

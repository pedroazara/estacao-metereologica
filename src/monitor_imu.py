#!/usr/bin/env python3
"""
Monitor do acelerometro / IMU do WTGAHRS3, sem GPS.

Painel no navegador estilo telemetria: circulo de forca G, barras por eixo,
horizonte artificial, bussola, giroscopio e retencao de picos.

    python3 monitor_imu.py --porta /dev/ttyUSB0

Para testar a interface sem o sensor:
    python3 monitor_imu.py --simular
"""

import argparse
import json
import math
import random
import signal
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from wtgahrs3 import ENDERECO_PADRAO, ESCALA_ACC_G, ESCALA_ANGULO, ESCALA_GYRO_DPS
    from wtgahrs3 import REG_AX, WTGAHRS3, s16
except ImportError:
    WTGAHRS3 = None
    ENDERECO_PADRAO = 0x50
    ESCALA_ACC_G, ESCALA_GYRO_DPS, ESCALA_ANGULO = 16.0, 2000.0, 180.0

estado = {
    "conectado": False,
    "ax": 0.0, "ay": 0.0, "az": 0.0,
    "gx": 0.0, "gy": 0.0, "gz": 0.0,
    "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
    "temp": 0.0,
    "g_total": 0.0,
    "pico_ax": 0.0, "pico_ay": 0.0, "pico_az": 0.0, "pico_total": 0.0,
    "pico_giro": 0.0,
    "rastro": [],          # ultimos pontos [ay, ax] para o rastro do circulo
    "taxa": 0.0,
    "amostras": 0,
    "mensagem": "iniciando...",
}
_lock = threading.Lock()
_parar = False
_zero = {"ax": 0.0, "ay": 0.0, "az": 0.0}


def _atualiza(ax, ay, az, gx, gy, gz, roll, pitch, yaw, temp, rastro):
    with _lock:
        ax -= _zero["ax"]
        ay -= _zero["ay"]
        az -= _zero["az"]
        total = math.sqrt(ax * ax + ay * ay + az * az)

        estado.update({
            "ax": ax, "ay": ay, "az": az,
            "gx": gx, "gy": gy, "gz": gz,
            "roll": roll, "pitch": pitch, "yaw": yaw,
            "temp": temp, "g_total": total,
        })
        estado["pico_ax"] = max(estado["pico_ax"], abs(ax))
        estado["pico_ay"] = max(estado["pico_ay"], abs(ay))
        estado["pico_az"] = max(estado["pico_az"], abs(az))
        estado["pico_total"] = max(estado["pico_total"], total)
        estado["pico_giro"] = max(estado["pico_giro"], abs(gx), abs(gy), abs(gz))
        estado["amostras"] += 1

        rastro.append([round(ay, 3), round(ax, 3)])
        estado["rastro"] = list(rastro)


def thread_sensor(args):
    if args.simular:
        _simular(args)
        return

    rastro = deque(maxlen=args.rastro)
    sensor = None
    intervalo = 1.0 / args.hz
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
                    estado["mensagem"] = "lendo"
            except Exception as e:
                with _lock:
                    estado["conectado"] = False
                    estado["mensagem"] = f"sem conexao: {e}"
                sensor = None
                time.sleep(1.5)
                continue

        t0 = time.monotonic()
        try:
            # 0x34..0x40 numa unica transacao: acc, giro, mag, angulos, temp
            r = sensor._ler(REG_AX, 13)
            v = [s16(x) for x in r]
            _atualiza(
                v[0] / 32768 * ESCALA_ACC_G, v[1] / 32768 * ESCALA_ACC_G,
                v[2] / 32768 * ESCALA_ACC_G,
                v[3] / 32768 * ESCALA_GYRO_DPS, v[4] / 32768 * ESCALA_GYRO_DPS,
                v[5] / 32768 * ESCALA_GYRO_DPS,
                v[9] / 32768 * ESCALA_ANGULO, v[10] / 32768 * ESCALA_ANGULO,
                v[11] / 32768 * ESCALA_ANGULO,
                v[12] / 100.0, rastro,
            )
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


def _simular(args):
    """Movimento ficticio, para conferir a interface sem hardware."""
    rastro = deque(maxlen=args.rastro)
    t = 0.0
    with _lock:
        estado["conectado"] = True
        estado["mensagem"] = "MODO SIMULACAO (sem sensor real)"
    while not _parar:
        t += 1.0 / args.hz
        ax = 0.45 * math.sin(t * 0.9) + random.gauss(0, 0.02)
        ay = 0.60 * math.cos(t * 0.6) + random.gauss(0, 0.02)
        az = 1.00 + 0.10 * math.sin(t * 2.1) + random.gauss(0, 0.02)
        _atualiza(ax, ay, az,
                  40 * math.sin(t * 1.3), 30 * math.cos(t * 0.7), 60 * math.sin(t * 0.4),
                  18 * math.sin(t * 0.6), 12 * math.cos(t * 0.5), (t * 12) % 360,
                  31.5, rastro)
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
        if self.path.startswith("/dados"):
            with _lock:
                self._envia(json.dumps(dict(estado)))
        elif self.path.startswith("/picos"):
            with _lock:
                for k in ("pico_ax", "pico_ay", "pico_az", "pico_total", "pico_giro"):
                    estado[k] = 0.0
                estado["rastro"] = []
            self._envia('{"ok":true}')
        elif self.path.startswith("/zerar"):
            with _lock:
                _zero["ax"] += estado["ax"]
                _zero["ay"] += estado["ay"]
                _zero["az"] += estado["az"]
            self._envia('{"ok":true}')
        elif self.path.startswith("/restaurar"):
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
<title>Monitor IMU - Estacao Movel</title>
<style>
  :root{
    --bg:#0a0e13; --card:#141b23; --borda:#232d38; --txt:#e6edf3; --txt2:#7d8794;
    --x:#f85149; --y:#3fb950; --z:#58a6ff; --alerta:#d29922;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:var(--bg);
       color:var(--txt);padding:14px;min-height:100vh}
  header{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}
  #dot{width:9px;height:9px;border-radius:50%;background:var(--x)}
  #dot.on{background:var(--y);box-shadow:0 0 8px var(--y)}
  h1{font-size:15px;font-weight:600}
  #msg{font-size:11px;color:var(--txt2)}
  #taxa{margin-left:auto;font-size:11px;color:var(--txt2);
        font-family:ui-monospace,Menlo,monospace}
  .grade{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
  .card{background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:16px}
  .tit{font-size:10px;text-transform:uppercase;letter-spacing:1.2px;color:var(--txt2);
       margin-bottom:12px}
  canvas{display:block;margin:0 auto;max-width:100%}

  .barra{margin-bottom:14px}
  .barra-topo{display:flex;justify-content:space-between;align-items:baseline;
              margin-bottom:5px}
  .barra-nome{font-size:12px;font-weight:600}
  .barra-val{font-family:ui-monospace,Menlo,monospace;font-size:15px;font-weight:600}
  .trilho{position:relative;height:22px;background:#0a0e13;border-radius:5px;
          border:1px solid var(--borda);overflow:hidden}
  .centro{position:absolute;left:50%;top:0;bottom:0;width:1px;background:#39424e}
  .preenche{position:absolute;top:2px;bottom:2px;border-radius:3px;transition:.06s linear}
  .pico{position:absolute;top:0;bottom:0;width:2px;background:var(--alerta);opacity:.9}
  .escala{display:flex;justify-content:space-between;font-size:9px;color:var(--txt2);
          margin-top:3px;font-family:ui-monospace,Menlo,monospace}

  .nums{display:grid;grid-template-columns:1fr 1fr;gap:9px}
  .n{background:#0a0e13;border:1px solid var(--borda);border-radius:8px;padding:9px}
  .n .v{font-size:17px;font-weight:600;font-family:ui-monospace,Menlo,monospace}
  .n .r{font-size:9px;text-transform:uppercase;letter-spacing:.8px;color:var(--txt2);
        margin-top:2px}
  .botoes{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
  button{flex:1;min-width:92px;background:#1c242e;color:var(--txt);
         border:1px solid var(--borda);border-radius:7px;padding:9px;font-size:11px;
         cursor:pointer;font-family:inherit}
  button:hover{background:#26303c}
  button.ativo{background:#58a6ff;border-color:#58a6ff;color:#07121f;font-weight:600}
</style>
</head>
<body>

<header>
  <div id="dot"></div>
  <div>
    <h1>Monitor IMU</h1>
    <div id="msg">conectando...</div>
  </div>
  <div id="taxa">-- Hz</div>
</header>

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
      <div class="barra-topo"><span class="barra-nome" style="color:var(--x)">X &nbsp;longitudinal</span>
        <span class="barra-val" id="vax">0,00 g</span></div>
      <div class="trilho"><div class="centro"></div>
        <div class="preenche" id="bax" style="background:var(--x)"></div>
        <div class="pico" id="pax"></div></div>
    </div>
    <div class="barra">
      <div class="barra-topo"><span class="barra-nome" style="color:var(--y)">Y &nbsp;lateral</span>
        <span class="barra-val" id="vay">0,00 g</span></div>
      <div class="trilho"><div class="centro"></div>
        <div class="preenche" id="bay" style="background:var(--y)"></div>
        <div class="pico" id="pay"></div></div>
    </div>
    <div class="barra">
      <div class="barra-topo"><span class="barra-nome" style="color:var(--z)">Z &nbsp;vertical</span>
        <span class="barra-val" id="vaz">0,00 g</span></div>
      <div class="trilho"><div class="centro"></div>
        <div class="preenche" id="baz" style="background:var(--z)"></div>
        <div class="pico" id="paz"></div></div>
      <div class="escala"><span id="e1">-2 g</span><span>0</span><span id="e2">+2 g</span></div>
    </div>
    <div class="botoes">
      <button id="b_zerar">Zerar eixos</button>
      <button id="b_restaurar">Restaurar</button>
      <button id="b_picos">Zerar picos</button>
      <button id="b_escala">Escala 2g</button>
    </div>
  </div>

  <div class="card">
    <div class="tit">Atitude</div>
    <canvas id="horizonte" width="260" height="200"></canvas>
    <div class="nums" style="margin-top:12px">
      <div class="n"><div class="v" id="vroll">0,0</div><div class="r">roll (graus)</div></div>
      <div class="n"><div class="v" id="vpitch">0,0</div><div class="r">pitch (graus)</div></div>
    </div>
  </div>

  <div class="card">
    <div class="tit">Bussola / rumo</div>
    <canvas id="bussola" width="220" height="220"></canvas>
    <div class="nums" style="margin-top:12px">
      <div class="n"><div class="v" id="vyaw">0,0</div><div class="r">rumo corrigido</div></div>
      <div class="n"><div class="v" id="vyawbruto">0,0</div><div class="r">yaw cru do sensor</div></div>
      <div class="n"><div class="v" id="vtemp">--</div><div class="r">temp chip (C)</div></div>
      <div class="n"><div class="v" id="vcorr">0&deg;</div><div class="r">correcao aplicada</div></div>
    </div>
    <div class="botoes">
      <button id="b_inverter">Inverter sentido</button>
      <button id="b_girar">Girar +90&deg;</button>
      <button id="b_norte">Aqui e o Norte</button>
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

<script>
let ESCALA = 2.0;                 // fundo de escala das barras, em g
const ESCALAS = [1, 2, 4, 8, 16];
const br = (n,d) => n.toFixed(d).replace('.',',');

// --- correcao do rumo ---------------------------------------------------
// A bussola conta no sentido HORARIO a partir do norte (N=0, L=90, S=180,
// O=270). Muitos IMUs entregam o yaw no sentido matematico (anti-horario) e
// com o zero em outro lugar, dependendo de como o sensor foi montado.
// Estes dois ajustes resolvem sem mexer no codigo.
let INVERTER = false;   // troca o sentido de giro
let OFFSET   = 0;       // rotacao fixa, em graus

const corrigeRumo = y => (((INVERTER ? -y : y) + OFFSET) % 360 + 360) % 360;

let yawCru = 0;

document.getElementById('b_inverter').onclick = function(){
  INVERTER = !INVERTER;
  this.classList.toggle('ativo', INVERTER);
};
document.getElementById('b_girar').onclick = () => { OFFSET = (OFFSET + 90) % 360; };
document.getElementById('b_norte').onclick = () => {
  // aponta o sensor para o norte de verdade e clica: zera o rumo aqui
  OFFSET = (OFFSET - corrigeRumo(yawCru) % 360 + 720) % 360;
};

document.getElementById('b_zerar').onclick     = () => fetch('/zerar');
document.getElementById('b_restaurar').onclick = () => fetch('/restaurar');
document.getElementById('b_picos').onclick     = () => fetch('/picos');
document.getElementById('b_escala').onclick = function(){
  ESCALA = ESCALAS[(ESCALAS.indexOf(ESCALA)+1) % ESCALAS.length];
  this.textContent = 'Escala ' + ESCALA + 'g';
  document.getElementById('e1').textContent = '-' + ESCALA + ' g';
  document.getElementById('e2').textContent = '+' + ESCALA + ' g';
};

function barra(idBarra, idPico, valor, pico, escala){
  const el = document.getElementById(idBarra);
  const frac = Math.max(-1, Math.min(1, valor/escala));
  const meio = 50;
  if(frac >= 0){ el.style.left = meio+'%'; el.style.width = (frac*50)+'%'; }
  else         { el.style.left = (meio+frac*50)+'%'; el.style.width = (-frac*50)+'%'; }
  if(idPico){
    const p = document.getElementById(idPico);
    const pf = Math.min(1, pico/escala);
    p.style.left = (meio + pf*50) + '%';
    p.style.display = pico > 0.01 ? 'block' : 'none';
  }
}

// ---------- circulo de forca G ----------
const gc = document.getElementById('gmeter').getContext('2d');
function desenhaG(ay, ax, rastro, pico){
  const W=300, H=300, cx=W/2, cy=H/2, R=125;
  gc.clearRect(0,0,W,H);

  // aneis de referencia
  gc.strokeStyle='#232d38'; gc.fillStyle='#7d8794'; gc.font='9px ui-monospace';
  for(let i=1;i<=4;i++){
    const r = R*i/4;
    gc.beginPath(); gc.arc(cx,cy,r,0,7); gc.stroke();
    gc.fillText(br(ESCALA*i/4,1)+'g', cx+4, cy-r+11);
  }
  gc.beginPath(); gc.moveTo(cx-R,cy); gc.lineTo(cx+R,cy);
  gc.moveTo(cx,cy-R); gc.lineTo(cx,cy+R); gc.stroke();

  gc.fillStyle='#7d8794'; gc.font='10px system-ui'; gc.textAlign='center';
  gc.fillText('FRENTE', cx, cy-R-6);
  gc.fillText('TRAS',   cx, cy+R+14);
  gc.textAlign='left';   gc.fillText('DIR', cx+R+4, cy+4);
  gc.textAlign='right';  gc.fillText('ESQ', cx-R-4, cy+4);
  gc.textAlign='start';

  const px = v => cx + Math.max(-1,Math.min(1, v/ESCALA))*R;
  const py = v => cy - Math.max(-1,Math.min(1, v/ESCALA))*R;

  // rastro
  if(rastro && rastro.length > 1){
    for(let i=1;i<rastro.length;i++){
      gc.strokeStyle = 'rgba(88,166,255,' + (0.05 + 0.5*i/rastro.length) + ')';
      gc.lineWidth = 1.5;
      gc.beginPath();
      gc.moveTo(px(rastro[i-1][0]), py(rastro[i-1][1]));
      gc.lineTo(px(rastro[i][0]),   py(rastro[i][1]));
      gc.stroke();
    }
  }

  // anel do pico
  if(pico > 0.01){
    gc.strokeStyle='#d29922'; gc.lineWidth=1; gc.setLineDash([3,3]);
    gc.beginPath(); gc.arc(cx,cy, Math.min(1,pico/ESCALA)*R, 0, 7); gc.stroke();
    gc.setLineDash([]);
  }

  // ponto atual
  const x=px(ay), y=py(ax);
  gc.strokeStyle='rgba(88,166,255,.35)'; gc.lineWidth=1;
  gc.beginPath(); gc.moveTo(cx,cy); gc.lineTo(x,y); gc.stroke();
  gc.fillStyle='#58a6ff'; gc.beginPath(); gc.arc(x,y,7,0,7); gc.fill();
  gc.strokeStyle='#fff'; gc.lineWidth=2; gc.stroke();
}

// ---------- horizonte artificial ----------
const hc = document.getElementById('horizonte').getContext('2d');
function desenhaHorizonte(roll, pitch){
  const W=260,H=200,cx=W/2,cy=H/2,R=82;
  hc.clearRect(0,0,W,H);
  hc.save();
  hc.beginPath(); hc.arc(cx,cy,R,0,7); hc.clip();
  hc.translate(cx,cy); hc.rotate(-roll*Math.PI/180);
  const desl = pitch*2.2;
  hc.fillStyle='#2b6ca3'; hc.fillRect(-200,-200+desl,400,200);
  hc.fillStyle='#6b4a2f'; hc.fillRect(-200,desl,400,200);
  hc.strokeStyle='#fff'; hc.lineWidth=2;
  hc.beginPath(); hc.moveTo(-200,desl); hc.lineTo(200,desl); hc.stroke();
  hc.strokeStyle='rgba(255,255,255,.6)'; hc.lineWidth=1; hc.font='8px ui-monospace';
  hc.fillStyle='#fff'; hc.textAlign='center';
  for(let p=-30;p<=30;p+=10){
    if(p===0) continue;
    const y = desl - p*2.2, w = (p%20===0)?26:14;
    hc.beginPath(); hc.moveTo(-w,y); hc.lineTo(w,y); hc.stroke();
    if(p%20===0) hc.fillText(Math.abs(p), w+11, y+3);
  }
  hc.restore();
  hc.strokeStyle='#232d38'; hc.lineWidth=2;
  hc.beginPath(); hc.arc(cx,cy,R,0,7); hc.stroke();
  hc.strokeStyle='#d29922'; hc.lineWidth=3;
  hc.beginPath();
  hc.moveTo(cx-34,cy); hc.lineTo(cx-11,cy); hc.moveTo(cx+11,cy); hc.lineTo(cx+34,cy);
  hc.moveTo(cx,cy-4); hc.lineTo(cx,cy+4);
  hc.stroke();
}

// ---------- bussola ----------
const bc = document.getElementById('bussola').getContext('2d');
function desenhaBussola(yaw){
  const W=220,H=220,cx=W/2,cy=H/2,R=88;
  bc.clearRect(0,0,W,H);
  bc.save(); bc.translate(cx,cy); bc.rotate(-yaw*Math.PI/180);
  bc.strokeStyle='#232d38'; bc.lineWidth=2;
  bc.beginPath(); bc.arc(0,0,R,0,7); bc.stroke();
  const pontos=[['N',0],['L',90],['S',180],['O',270]];
  bc.textAlign='center'; bc.textBaseline='middle';
  for(let a=0;a<360;a+=15){
    const rad=(a-90)*Math.PI/180, grande=(a%45===0);
    bc.strokeStyle = grande ? '#7d8794' : '#2c3742'; bc.lineWidth = grande?2:1;
    bc.beginPath();
    bc.moveTo(Math.cos(rad)*R, Math.sin(rad)*R);
    bc.lineTo(Math.cos(rad)*(R-(grande?13:7)), Math.sin(rad)*(R-(grande?13:7)));
    bc.stroke();
  }
  for(const [nome,ang] of pontos){
    const rad=(ang-90)*Math.PI/180, d=R-30;
    bc.save(); bc.translate(Math.cos(rad)*d, Math.sin(rad)*d); bc.rotate(yaw*Math.PI/180);
    bc.fillStyle = nome==='N' ? '#f85149' : '#e6edf3';
    bc.font = (nome==='N'?'bold ':'') + '14px system-ui';
    bc.fillText(nome,0,0); bc.restore();
  }
  bc.restore();
  bc.fillStyle='#d29922'; bc.beginPath();
  bc.moveTo(cx,cy-R-2); bc.lineTo(cx-7,cy-R+13); bc.lineTo(cx+7,cy-R+13);
  bc.closePath(); bc.fill();
  bc.fillStyle='#7d8794'; bc.beginPath(); bc.arc(cx,cy,3,0,7); bc.fill();
}

async function atualizar(){
  let d;
  try { d = await (await fetch('/dados')).json(); }
  catch(e){ document.getElementById('msg').textContent='servidor offline'; return; }

  document.getElementById('dot').className = d.conectado ? 'on' : '';
  document.getElementById('msg').textContent = d.mensagem;
  document.getElementById('taxa').textContent = br(d.taxa,1) + ' Hz';

  document.getElementById('vax').textContent = br(d.ax,3)+' g';
  document.getElementById('vay').textContent = br(d.ay,3)+' g';
  document.getElementById('vaz').textContent = br(d.az,3)+' g';
  barra('bax','pax', d.ax, d.pico_ax, ESCALA);
  barra('bay','pay', d.ay, d.pico_ay, ESCALA);
  barra('baz','paz', d.az, d.pico_az, ESCALA);

  document.getElementById('gtot').textContent  = br(d.g_total,3);
  document.getElementById('gpico').textContent = br(d.pico_total,3);

  document.getElementById('vgx').textContent = br(d.gx,1);
  document.getElementById('vgy').textContent = br(d.gy,1);
  document.getElementById('vgz').textContent = br(d.gz,1);
  barra('bgx',null,d.gx,0,250);
  barra('bgy',null,d.gy,0,250);
  barra('bgz',null,d.gz,0,250);
  document.getElementById('gyropico').textContent = br(d.pico_giro,0);
  document.getElementById('amostras').textContent = d.amostras;

  document.getElementById('vroll').textContent  = br(d.roll,1);
  document.getElementById('vpitch').textContent = br(d.pitch,1);
  document.getElementById('vtemp').textContent  = br(d.temp,1);

  yawCru = d.yaw;
  const rumo = corrigeRumo(d.yaw);
  document.getElementById('vyaw').textContent      = br(rumo,1);
  document.getElementById('vyawbruto').textContent = br(d.yaw,1);
  document.getElementById('vcorr').textContent =
    (INVERTER ? 'inv ' : '') + OFFSET + '°';

  desenhaG(d.ay, d.ax, d.rastro, d.pico_total);
  desenhaHorizonte(d.roll, d.pitch);
  desenhaBussola(rumo);
}

atualizar();
setInterval(atualizar, 60);
</script>
</body>
</html>
"""


def main() -> int:
    global _parar
    p = argparse.ArgumentParser(description="Monitor do acelerometro / IMU")
    p.add_argument("--porta", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--endereco", default=hex(ENDERECO_PADRAO))
    p.add_argument("--hz", type=float, default=15.0, help="leituras por segundo")
    p.add_argument("--rastro", type=int, default=120, help="pontos no rastro do circulo G")
    p.add_argument("--porta-web", type=int, default=8766, dest="porta_web")
    p.add_argument("--simular", action="store_true")
    p.add_argument("--sem-navegador", action="store_true", dest="sem_navegador")
    args = p.parse_args()

    if not args.simular and WTGAHRS3 is None:
        print("ERRO: rode a partir da pasta src/.")
        return 1

    threading.Thread(target=thread_sensor, args=(args,), daemon=True).start()

    servidor = HTTPServer(("127.0.0.1", args.porta_web), Handler)
    url = f"http://localhost:{args.porta_web}"
    print("=" * 56)
    print("  MONITOR IMU" + ("  [SIMULACAO]" if args.simular else ""))
    print("=" * 56)
    print(f"  Navegador: {url}")
    print(f"  Taxa alvo: {args.hz} Hz")
    print("  Ctrl+C para encerrar")
    print("=" * 56)

    if not args.sem_navegador:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    def parar(*a):
        global _parar
        _parar = True
        print("\nEncerrando...")
        threading.Thread(target=servidor.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, parar)
    servidor.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

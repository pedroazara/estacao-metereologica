#!/usr/bin/env python3
"""
Servidor de telemetria: assina o broker MQTT, roda o filtro de Kalman nos
dados que o ESP32 publica e serve um painel web.

    python3 servidor_telemetria.py                 # broker em localhost
    python3 servidor_telemetria.py --broker 192.168.0.111

Painel em http://localhost:8771

Precisa do Mosquitto rodando - ver servidor/mosquitto-estacao.conf e a secao
"Broker MQTT" em docs/04-telemetria.md.

O ESP32 (firmware/painel_wifi) publica em tres topicos, separados por taxa:

    estacao/<dev>/pos      1 Hz    posicao GPS
    estacao/<dev>/imu      1 Hz    lote de N amostras do IMU
    estacao/<dev>/amb      0.1 Hz  temperatura e umidade
    estacao/<dev>/status   retained + LWT (o broker avisa quando o ESP32 cai)

Aqui esses topicos alimentam o FiltroCV de filtro_kalman.py - o mesmo filtro
ja validado do lado Python, sem porte para C++ - e viram CSV mais painel.

Limitacao conhecida: o carimbo de tempo absoluto vem do relogio do PC, na
chegada da mensagem. O dt usado pelo filtro vem do proprio ESP32 (campo "dt"
do lote de IMU), que e imune a latencia da rede. Quando entrar o buffer
offline no ESP32, o carimbo tera de vir do GPS - ver docs/04-telemetria.md.
"""

import argparse
import csv
import json
import math
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import paho.mqtt.client as mqtt

from filtro_kalman import DetectorPassos, FiltroCV
from processar_sessao import processar_sessao

# ---------------------------------------------------------------------------
# Estado compartilhado entre o receptor MQTT e o painel
# ---------------------------------------------------------------------------
_lock = threading.Lock()

estado = {
    # --- ligacao ---
    "online": False, "broker": False, "ultimo_pacote": 0.0, "dev": None,
    "msgs": 0, "perdidos": 0,
    # --- ambiente ---
    "temp": None, "umid": None,
    # --- GPS cru ---
    "fix": False, "lat": None, "lon": None, "alt": 0.0, "vel": 0.0,
    "sat": 0, "hdop": 0.0, "rumo": 0.0, "pontos": [],
    # --- Kalman ---
    "kf_lat": None, "kf_lon": None, "kf_vel": 0.0, "kf_sigma": 0.0,
    "kf_parado": True, "kf_pontos": [],
    # --- IMU (ultima amostra do lote) ---
    "ax": 0.0, "ay": 0.0, "az": 1.0, "gx": 0.0, "gy": 0.0, "gz": 0.0,
    "roll": 0.0, "pitch": 0.0, "yaw": 0.0, "amostras_imu": 0, "passos": 0,
}

_kf = FiltroCV()
_passos = DetectorPassos()
_seq_anterior = {}      # ultimo seq por (dev, tipo), para detectar perda
_csv = None
_csv_arquivo = None

# Qual estacao este servidor esta seguindo. Definido por --dev ou travado na
# primeira que publicar dado. Sem isso, duas estacoes no ar (o ESP32 real e o
# simulador, por exemplo) misturariam trajetos no mesmo mapa e no mesmo filtro.
_dev_alvo = None
_devs_ignorados = set()

MAX_PONTOS = 5000
TOPICO_RAIZ = "estacao"


def haversine_m(a, b):
    """Distancia em metros entre dois pares (lat, lon)."""
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def registrar_ponto(chave, lat, lon, min_dist=1.0):
    """Guarda o ponto so se andou o suficiente - evita borrao de ruido parado."""
    pts = estado[chave]
    if not pts or haversine_m(pts[-1], (lat, lon)) > min_dist:
        pts.append([lat, lon])
        if len(pts) > MAX_PONTOS:
            del pts[0]


def contar_sequencia(dev, tipo, seq):
    """
    Detecta mensagem perdida pela lacuna no contador do ESP32.

    A chave inclui o dev: cada estacao tem o proprio contador, e duas
    publicando no mesmo tipo de topico contariam perda falsa uma da outra.
    """
    if seq is None:
        return
    chave = (dev, tipo)
    anterior = _seq_anterior.get(chave)
    # Contador menor que o anterior = o ESP32 reiniciou; recomeca sem acusar perda.
    if anterior is not None and anterior < seq <= anterior + 10000:
        with _lock:
            estado["perdidos"] += seq - anterior - 1
    _seq_anterior[chave] = seq


# ---------------------------------------------------------------------------
# Processamento por topico
# ---------------------------------------------------------------------------
def processar_amb(dev, m):
    with _lock:
        estado["temp"] = m.get("temp")
        estado["umid"] = m.get("umid")


def processar_imu(dev, m):
    """
    Predicao do Kalman com o lote de amostras.

    O dt vem do ESP32 (esp_timer), nao do relogio do PC: a latencia do Wi-Fi
    varia de dezenas a centenas de ms e contaminaria a predicao do filtro.
    """
    lote = m.get("m") or []
    dt = m.get("dt")
    if not lote or not dt or not (0.0 < dt < 1.0):
        return

    for amostra in lote:
        ax = amostra.get("ax", 0.0)
        ay = amostra.get("ay", 0.0)
        az = amostra.get("az", 1.0)
        gx = amostra.get("gx", 0.0)
        gy = amostra.get("gy", 0.0)
        gz = amostra.get("gz", 0.0)
        roll = amostra.get("roll", 0.0)
        pitch = amostra.get("pitch", 0.0)

        houve_passo = _passos.amostra(ax, ay, az, dt)
        _kf.prever(ax, ay, az, roll, pitch, dt, gx, gy, gz,
                   passo_detectado=houve_passo, passos_ativos=_passos.andando())
        if _kf.entrou_parado:
            _passos.encerrar_movimento()

    ultima = lote[-1]
    with _lock:
        estado.update(ax=ultima.get("ax", 0.0), ay=ultima.get("ay", 0.0),
                      az=ultima.get("az", 1.0), gx=ultima.get("gx", 0.0),
                      gy=ultima.get("gy", 0.0), gz=ultima.get("gz", 0.0),
                      roll=ultima.get("roll", 0.0), pitch=ultima.get("pitch", 0.0),
                      yaw=ultima.get("yaw", 0.0), passos=_passos.passos)
        estado["amostras_imu"] += len(lote)
        if _kf.pronto:
            estado["kf_parado"] = _kf.parado


def processar_pos(dev, m):
    """Correcao do Kalman quando chega um fix."""
    if not m.get("fix"):
        with _lock:
            estado["fix"] = False
        return

    lat, lon = m["lat"], m["lon"]
    hdop = m.get("hdop") or 1.0
    vel = m.get("vel", 0.0)
    rumo = m.get("rumo", 0.0)

    with _lock:
        estado.update(fix=True, lat=lat, lon=lon, alt=m.get("alt", 0.0),
                      vel=vel, sat=m.get("sat", 0), hdop=hdop, rumo=rumo)
        registrar_ponto("pontos", lat, lon)

    agora = time.monotonic()
    if not _kf.pronto:
        _kf.iniciar(lat, lon)
        _kf.atualizar_movimento_gps(agora, lat, lon, vel)
        return

    _kf.atualizar_movimento_gps(agora, lat, lon, vel)
    _kf.corrigir_gps(lat, lon, hdop=hdop)
    _kf.corrigir_velocidade(vel, rumo)

    klat, klon = _kf.posicao
    with _lock:
        estado["kf_lat"], estado["kf_lon"] = klat, klon
        estado["kf_vel"] = _kf.velocidade_kmh
        estado["kf_sigma"] = _kf.sigma_pos
        registrar_ponto("kf_pontos", klat, klon)


def processar_status(dev, m):
    with _lock:
        estado["online"] = bool(m.get("online"))


# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------
def ao_conectar(cliente, dados, flags, codigo, propriedades=None):
    if codigo == 0:
        alvo = f"{TOPICO_RAIZ}/+/+"
        cliente.subscribe(alvo, qos=1)
        with _lock:
            estado["broker"] = True
        print(f"conectado ao broker, assinando {alvo}")
    else:
        print(f"falha ao conectar no broker (codigo {codigo})")


def ao_desconectar(cliente, dados, flags, codigo, propriedades=None):
    with _lock:
        estado["broker"] = False
    print("desconectado do broker - tentando reconectar...")


def ao_receber(cliente, dados, msg):
    global _dev_alvo

    partes = msg.topic.split("/")
    if len(partes) != 3:
        return
    _, dev, tipo = partes

    try:
        m = json.loads(msg.payload)
    except json.JSONDecodeError:
        return

    # O "status" e retained: o broker entrega o de TODA estacao que ja passou
    # por aqui assim que assinamos. Nao serve para escolher quem seguir.
    if tipo != "status":
        if _dev_alvo is None:
            _dev_alvo = dev
            print(f"\nseguindo a estacao '{dev}'")
        elif dev != _dev_alvo:
            if dev not in _devs_ignorados:
                _devs_ignorados.add(dev)
                print(f"\nignorando '{dev}' (seguindo '{_dev_alvo}'; "
                      f"use --dev para trocar)")
            return
    elif _dev_alvo is not None and dev != _dev_alvo:
        return

    with _lock:
        estado["dev"] = dev
        estado["msgs"] += 1
        if tipo != "status":
            estado["ultimo_pacote"] = time.time()

    contar_sequencia(dev, tipo, m.get("seq"))

    try:
        if tipo == "pos":
            processar_pos(dev, m)
        elif tipo == "imu":
            processar_imu(dev, m)
        elif tipo == "amb":
            processar_amb(dev, m)
        elif tipo == "status":
            processar_status(dev, m)
        else:
            return
    except Exception as erro:        # um payload ruim nao pode derrubar o servidor
        print(f"erro processando {msg.topic}: {erro}")
        return

    if tipo in ("pos", "amb"):
        gravar_csv()


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
# "dev" e a PRIMEIRA coluna de proposito: e a procedencia do dado, e sem ela um
# CSV gerado pelo src/simular_estacao.py (dev "esp32-sim") fica indistinguivel
# de uma coleta real na mesma pasta, com o mesmo padrao de nome. Meses depois
# ninguem separa os dois - e um instrumento de pesquisa nao pode ter dado de
# origem duvidosa no acervo.
CABECALHO = ["dev", "hora", "lat", "lon", "alt", "vel", "sat", "hdop",
             "kf_lat", "kf_lon", "kf_vel", "kf_sigma", "kf_parado",
             "temp", "umid", "ax", "ay", "az", "roll", "pitch", "yaw", "passos"]


def abrir_csv(caminho):
    global _csv, _csv_arquivo
    novo = not os.path.exists(caminho)
    _csv_arquivo = open(caminho, "a", newline="")
    _csv = csv.writer(_csv_arquivo)
    if novo:
        _csv.writerow(CABECALHO)
        _csv_arquivo.flush()
    print(f"gravando em {caminho}")


def gravar_csv():
    if _csv is None:
        return
    with _lock:
        linha = [
            estado["dev"] or "?",
            datetime.now().isoformat(timespec="milliseconds"),
            estado["lat"], estado["lon"], estado["alt"], estado["vel"],
            estado["sat"], estado["hdop"],
            estado["kf_lat"], estado["kf_lon"], estado["kf_vel"],
            estado["kf_sigma"], estado["kf_parado"],
            estado["temp"], estado["umid"],
            estado["ax"], estado["ay"], estado["az"],
            estado["roll"], estado["pitch"], estado["yaw"], estado["passos"],
        ]
    _csv.writerow(linha)
    _csv_arquivo.flush()


# ---------------------------------------------------------------------------
# Pagina
# ---------------------------------------------------------------------------
PAGINA = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Base da Estacao</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--borda:#232d38;--txt:#e6edf3;--txt2:#8b949e;
        --verde:#3fb950;--azul:#58a6ff;--amarelo:#d29922;--vermelho:#f85149}
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
            background:var(--bg);color:var(--txt)}
  #topo{height:56px;display:flex;align-items:center;gap:12px;padding:0 18px;
        background:var(--card);border-bottom:1px solid var(--borda)}
  #topo h1{font-size:15px;font-weight:600}
  .pilula{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--txt2);
          background:#0d1117;border:1px solid var(--borda);border-radius:999px;padding:5px 11px}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--txt2)}
  .dot.on{background:var(--verde);box-shadow:0 0 7px var(--verde)}
  .dot.off{background:var(--vermelho)}
  #corpo{display:flex;height:calc(100% - 56px)}
  #lista{width:300px;border-right:1px solid var(--borda);overflow-y:auto;flex:none;
         background:var(--card)}
  .cab{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--txt2);
       padding:14px 16px 8px}
  .sessao{padding:12px 16px;border-bottom:1px solid var(--borda);cursor:pointer}
  .sessao:hover{background:#1c2430}
  .sessao.sel{background:#132132;border-left:3px solid var(--azul)}
  .sessao .nome{font-size:12.5px;font-family:ui-monospace,monospace;word-break:break-all}
  .sessao .sub{font-size:11px;color:var(--txt2);margin-top:3px}
  #vazio{padding:24px 16px;font-size:12.5px;color:var(--txt2);line-height:1.6}
  #direita{flex:1;display:flex;flex-direction:column;min-width:0}
  #mapa{flex:1;background:#0a0e13;margin:14px;border-radius:12px;border:1px solid var(--borda)}
  #resumo{display:flex;gap:22px;padding:0 20px 16px;flex-wrap:wrap}
  .n .v{font-size:19px;font-weight:650;font-family:ui-monospace,monospace}
  .n .r{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--txt2)}
  #placeholder{flex:1;display:flex;align-items:center;justify-content:center;
               color:var(--txt2);font-size:13px;text-align:center;padding:30px}
</style>
</head>
<body>
<div id="topo">
  <h1>Base da Estacao</h1>
  <span class="pilula"><span class="dot" id="d_base"></span><span id="t_base">base</span></span>
  <span class="pilula"><span class="dot" id="d_esp"></span><span id="t_esp">estacao ao vivo</span></span>
  <span class="pilula"><span id="t_sess">0 sessoes</span></span>
  <span style="margin-left:auto;font-size:11px;color:var(--txt2)" id="relogio"></span>
</div>
<div id="corpo">
  <div id="lista">
    <div class="cab">Coletas recebidas</div>
    <div id="sessoes"></div>
    <div id="vazio" style="display:none">
      Nenhuma coleta ainda.<br><br>
      No app do celular, aba <b>Sessoes</b>, envie uma coleta para esta base.
      Ela chega aqui ja processada pelo Kalman.
    </div>
  </div>
  <div id="direita">
    <div id="placeholder">Selecione uma coleta a esquerda para ver o trajeto.</div>
    <canvas id="mapa" style="display:none"></canvas>
    <div id="resumo" style="display:none"></div>
  </div>
</div>
<script>
const $ = s => document.querySelector(s);
let sel = null, traj = null;

function fmtData(iso){
  if(!iso) return '';
  return iso.replace('T',' ').slice(0,16);
}

async function carregarSessoes(){
  try{
    const d = await (await fetch('/sessoes',{cache:'no-store'})).json();
    const lista = d.sessoes || [];
    $('#t_sess').textContent = lista.length + (lista.length===1?' sessao':' sessoes');
    $('#vazio').style.display = lista.length ? 'none':'block';
    const box = $('#sessoes');
    box.innerHTML = lista.map(s => `
      <div class="sessao ${s.sessao===sel?'sel':''}" data-n="${s.sessao}">
        <div class="nome">${s.sessao}</div>
        <div class="sub">${s.n_derivado} pontos &middot; ${fmtData(s.processada_em)}</div>
      </div>`).join('');
    box.querySelectorAll('.sessao').forEach(el =>
      el.onclick = () => selecionar(el.dataset.n));
    // seleciona a primeira automaticamente
    if(!sel && lista.length) selecionar(lista[0].sessao);
  }catch(e){}
}

async function carregarEstado(){
  const on = (id,ok) => { $(id).className = 'dot '+(ok?'on':'off'); };
  try{
    const d = await (await fetch('/dados',{cache:'no-store'})).json();
    on('#d_base', true); $('#t_base').textContent = 'base no ar';
    // Presenca ao vivo do ESP pelo MQTT (caminho legado, secundario).
    on('#d_esp', d.recebendo);
    $('#t_esp').textContent = d.recebendo ? ('estacao publicando ('+(d.dev||'esp32')+')')
                                          : 'sem estacao ao vivo';
  }catch(e){
    on('#d_base', false); $('#t_base').textContent = 'base fora do ar';
  }
}

async function selecionar(nome){
  sel = nome;
  document.querySelectorAll('.sessao').forEach(el =>
    el.classList.toggle('sel', el.dataset.n===nome));
  traj = await (await fetch('/sessao?nome='+encodeURIComponent(nome),{cache:'no-store'})).json();
  $('#placeholder').style.display='none';
  $('#mapa').style.display='block';
  $('#resumo').style.display='flex';
  const r = traj.resumo||{};
  $('#resumo').innerHTML = [
    ['pontos', r.n||0], ['vel final', (r.vel??0).toFixed(1)+' km/h'],
    ['incerteza', '±'+(r.sigma??0).toFixed(1)+' m'],
    ['temp', r.temp!=null? r.temp.toFixed(1)+' C':'--'],
    ['umid', r.umid!=null? r.umid.toFixed(1)+' %':'--'],
  ].map(([k,v])=>`<div class="n"><div class="v">${v}</div><div class="r">${k}</div></div>`).join('');
  desenhar();
}

function desenhar(){
  const c = $('#mapa'), ctx = c.getContext('2d');
  const rect = c.getBoundingClientRect();
  c.width = rect.width; c.height = rect.height;
  ctx.clearRect(0,0,c.width,c.height);
  if(!traj || !traj.cru.length){
    ctx.fillStyle='#8b949e'; ctx.font='13px system-ui'; ctx.textAlign='center';
    ctx.fillText('Esta coleta nao tem pontos de GPS com fix.', c.width/2, c.height/2);
    return;
  }
  const lat0 = traj.cru[0][0], lon0 = traj.cru[0][1];
  const cosLat = Math.cos(lat0*Math.PI/180);
  const m = p => [ (p[1]-lon0)*111320*cosLat, -(p[0]-lat0)*111320 ];
  const todos = traj.cru.concat(traj.kf).map(m);
  let minX=1e9,maxX=-1e9,minY=1e9,maxY=-1e9;
  for(const [x,y] of todos){ minX=Math.min(minX,x);maxX=Math.max(maxX,x);
                              minY=Math.min(minY,y);maxY=Math.max(maxY,y); }
  const mg=12; minX-=mg;maxX+=mg;minY-=mg;maxY+=mg;
  // Piso de escala: parado, o GPS oscila poucos metros; sem um span minimo o
  // ajuste amplia o ruido ate encher a tela. 50 m deixa o aglomerado parado
  // como um borrao pequeno no centro, em vez de rabisco.
  const SPAN_MIN = 50;
  const esc = Math.min(c.width/Math.max(maxX-minX,SPAN_MIN), c.height/Math.max(maxY-minY,SPAN_MIN));
  const cx=(minX+maxX)/2, cy=(minY+maxY)/2;
  const T = ([x,y]) => [ c.width/2+(x-cx)*esc, c.height/2+(y-cy)*esc ];
  const linha = (pts,cor,larg) => {
    if(pts.length<2) return;
    ctx.strokeStyle=cor; ctx.lineWidth=larg; ctx.lineJoin='round'; ctx.lineCap='round';
    ctx.beginPath();
    pts.forEach((p,i)=>{ const [x,y]=T(m(p)); i?ctx.lineTo(x,y):ctx.moveTo(x,y); });
    ctx.stroke();
  };
  linha(traj.cru, 'rgba(88,166,255,.55)', 2);
  linha(traj.kf, '#3fb950', 3.5);
  const [ax,ay]=T(m(traj.kf[traj.kf.length-1]||traj.cru[traj.cru.length-1]));
  ctx.fillStyle='#3fb950'; ctx.beginPath(); ctx.arc(ax,ay,6,0,7); ctx.fill();
  ctx.strokeStyle='#fff'; ctx.lineWidth=2; ctx.stroke();
  // legenda
  ctx.font='11px ui-monospace'; ctx.textAlign='left';
  ctx.fillStyle='#3fb950'; ctx.fillRect(12,12,16,3); ctx.fillStyle='#e6edf3';
  ctx.fillText('Kalman', 34, 16);
  ctx.fillStyle='rgba(88,166,255,.7)'; ctx.fillRect(12,28,16,3); ctx.fillStyle='#e6edf3';
  ctx.fillText('GPS cru', 34, 32);
}

window.addEventListener('resize', desenhar);
setInterval(()=>{ $('#relogio').textContent = new Date().toLocaleTimeString('pt-BR'); }, 1000);
carregarEstado(); carregarSessoes();
setInterval(carregarEstado, 3000);
setInterval(carregarSessoes, 5000);
</script>
</body>
</html>
"""


def _limpar_nome(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)[:40] or "x"


def _contar_linhas(caminho):
    try:
        with open(caminho, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _pasta_sincronizadas():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pasta = os.path.join(raiz, "data", "sincronizadas")
    os.makedirs(pasta, exist_ok=True)
    return pasta


def _caminho_banco():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(raiz, "data", "estacao.db")


def _listar_sessoes():
    """Coletas ja recebidas e processadas, da mais recente para a mais antiga.
    Le do SQLite; se o banco ainda nao existe, devolve lista vazia sem erro."""
    import sqlite3
    db = _caminho_banco()
    if not os.path.exists(db):
        return {"sessoes": []}
    con = sqlite3.connect(db)
    try:
        linhas = con.execute(
            "SELECT sessao, dev, processada_em, n_derivado FROM sessoes "
            "ORDER BY processada_em DESC").fetchall()
    except sqlite3.Error:
        return {"sessoes": []}
    finally:
        con.close()
    return {"sessoes": [
        {"sessao": s, "dev": d, "processada_em": p, "n_derivado": n}
        for (s, d, p, n) in linhas]}


def _trajeto_sessao(nome):
    """Trajeto cru e filtrado de uma sessao, para o mapa do painel."""
    import sqlite3
    nome = os.path.basename(nome or "")
    db = _caminho_banco()
    if not nome or not os.path.exists(db):
        return {"cru": [], "kf": []}
    con = sqlite3.connect(db)
    try:
        linhas = con.execute(
            "SELECT lat, lon, kf_lat, kf_lon, kf_vel, kf_sigma, temp, umid "
            "FROM derivado WHERE sessao = ? ORDER BY ts", (nome,)).fetchall()
    except sqlite3.Error:
        return {"cru": [], "kf": []}
    finally:
        con.close()
    cru = [[la, lo] for (la, lo, _kla, _klo, _v, _s, _t, _u) in linhas]
    kf = [[kla, klo] for (_la, _lo, kla, klo, _v, _s, _t, _u) in linhas]
    ult = linhas[-1] if linhas else None
    resumo = {}
    if ult:
        resumo = {"vel": ult[4], "sigma": ult[5], "temp": ult[6],
                  "umid": ult[7], "n": len(linhas)}
    return {"cru": cru, "kf": kf, "resumo": resumo}


class Manipulador(BaseHTTPRequestHandler):
    def log_message(self, formato, *args):
        pass  # silencia o log por requisicao - o console mostra o resumo

    def do_POST(self):
        # Recepcao das coletas que o celular guardou em campo. Enquanto o ESP32
        # nao tem cartao SD, o telefone e a custodia; aqui a base recebe a copia.
        #
        # MANIFESTO por nome de arquivo: se a sessao ja foi recebida, a base NAO
        # regrava e responde "ja_tinha". E o que evita duplicata quando uma
        # transferencia cai na metade e o celular reenvia - duplicata em serie
        # temporal e veneno (docs/04-telemetria.md).
        if self.path != "/sync":
            self.send_error(404)
            return
        dev = self.headers.get("X-Dev", "desconhecido")
        sessao = os.path.basename(self.headers.get("X-Sessao", ""))
        if not sessao or "/" in sessao or "\\" in sessao:
            self._json(400, {"ok": False, "erro": "cabecalho X-Sessao invalido"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            corpo = self.rfile.read(n) if n > 0 else b""
        except (ValueError, OSError) as e:
            self._json(400, {"ok": False, "erro": f"corpo ilegivel: {e}"})
            return

        pasta_dev = os.path.join(_pasta_sincronizadas(), _limpar_nome(dev))
        os.makedirs(pasta_dev, exist_ok=True)
        destino = os.path.join(pasta_dev, sessao)
        if os.path.exists(destino):
            self._json(200, {"ok": True, "ja_tinha": True,
                             "linhas": _contar_linhas(destino)})
            return

        # Grava primeiro num arquivo temporario e so entao renomeia: uma queda
        # no meio da escrita nao deixa um arquivo pela metade passar por sessao
        # completa no proximo manifesto.
        tmp = destino + ".parcial"
        try:
            with open(tmp, "wb") as f:
                f.write(corpo)
            os.replace(tmp, destino)
        except OSError as e:
            self._json(500, {"ok": False, "erro": str(e)})
            return

        linhas = _contar_linhas(destino)
        print(f"\nsync: recebida {dev}/{sessao} - {linhas} registros")

        # Processa em lote na chegada: roda o Kalman e grava o derivado no banco.
        # Uma falha aqui NAO perde a coleta - o cru ja esta salvo em `destino`, e
        # a sessao pode ser reprocessada depois. Por isso o except so avisa.
        n_deriv, erro_proc = 0, None
        try:
            raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            n_deriv = processar_sessao(destino, os.path.join(raiz, "data", "estacao.db"))
            print(f"sync: processada {sessao} - {n_deriv} pontos no banco")
        except Exception as e:      # noqa: BLE001 - custodia acima de tudo
            erro_proc = str(e)
            print(f"sync: cru salvo, mas o processamento falhou: {e}")

        self._json(200, {"ok": True, "ja_tinha": False, "linhas": linhas,
                         "derivados": n_deriv, "erro_processamento": erro_proc})

    def _json(self, codigo, obj):
        corpo = json.dumps(obj).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path == "/":
            corpo = PAGINA.encode()
            tipo = "text/html; charset=utf-8"
        elif self.path == "/dados":
            with _lock:
                instantaneo = dict(estado)
                # O ESP32 e dado como ausente depois de 5 s sem publicar.
                instantaneo["recebendo"] = (time.time() - estado["ultimo_pacote"]) < 5.0
            corpo = json.dumps(instantaneo).encode()
            tipo = "application/json"
        elif self.path.startswith("/sessoes"):
            corpo = json.dumps(_listar_sessoes()).encode()
            tipo = "application/json"
        elif self.path.startswith("/sessao"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            nome = (q.get("nome") or [""])[0]
            corpo = json.dumps(_trajeto_sessao(nome)).encode()
            tipo = "application/json"
        else:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--broker", default="localhost", help="endereco do broker MQTT")
    p.add_argument("--porta-broker", type=int, default=1883)
    p.add_argument("--porta", type=int, default=8771, help="porta do painel web")
    p.add_argument("--dev", default=None,
                   help="id da estacao a seguir (padrao: a primeira que publicar)")
    p.add_argument("--csv", default=None,
                   help="arquivo CSV de saida (padrao: data/telemetria-<data>.csv)")
    args = p.parse_args()

    global _dev_alvo
    if args.dev:
        _dev_alvo = args.dev
        print(f"seguindo apenas a estacao '{args.dev}'")

    caminho = args.csv
    if caminho is None:
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pasta = os.path.join(raiz, "data")
        os.makedirs(pasta, exist_ok=True)
        caminho = os.path.join(
            pasta, f"telemetria-{datetime.now():%Y%m%d-%H%M%S}.csv")
    abrir_csv(caminho)

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                          client_id="servidor-telemetria")
    cliente.on_connect = ao_conectar
    cliente.on_disconnect = ao_desconectar
    cliente.on_message = ao_receber

    print(f"broker:  {args.broker}:{args.porta_broker}")
    try:
        cliente.connect(args.broker, args.porta_broker, keepalive=30)
    except OSError as erro:
        print(f"\nnao consegui falar com o broker: {erro}")
        print("O Mosquitto esta rodando?   systemctl status mosquitto")
        return
    cliente.loop_start()

    servidor = HTTPServer(("0.0.0.0", args.porta), Manipulador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    print(f"painel:  http://localhost:{args.porta}")
    print("Ctrl+C para parar.\n")

    ultimo = -1
    try:
        while True:
            time.sleep(1.0)
            with _lock:
                n, perdidos = estado["msgs"], estado["perdidos"]
                vivo = (time.time() - estado["ultimo_pacote"]) < 5.0
                temp, sigma = estado["temp"], estado["kf_sigma"]
            if n != ultimo:
                ultimo = n
                t = f"{temp:.1f} C" if temp is not None else "--"
                print(f"\r{'ok ' if vivo else '...'} {n} msgs  "
                      f"{perdidos} perdidas  T={t}  sigma={sigma:.1f} m    ",
                      end="", flush=True)
    except KeyboardInterrupt:
        print("\nencerrando.")
    finally:
        cliente.loop_stop()
        cliente.disconnect()
        if _csv_arquivo:
            _csv_arquivo.close()


if __name__ == "__main__":
    main()

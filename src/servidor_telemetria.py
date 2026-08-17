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
CABECALHO = ["hora", "lat", "lon", "alt", "vel", "sat", "hdop",
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
PAGINA = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telemetria da estacao</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root { --fundo:#f4f5f7; --cartao:#fff; --texto:#16181d; --fraco:#6b7280;
          --borda:#e3e5e9; --temp:#e0533d; --umid:#2f7fd1; --cru:#9099a8; --kf:#2f9e57; }
  @media (prefers-color-scheme: dark) {
    :root { --fundo:#111317; --cartao:#1a1d23; --texto:#f0f2f5; --fraco:#9099a8;
            --borda:#2a2e36; --temp:#ff7a5e; --umid:#63aef5; --cru:#6b7280; --kf:#4fd07a; }
  }
  * { box-sizing:border-box; }
  body { margin:0; padding:20px 16px 40px; background:var(--fundo); color:var(--texto);
         font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  .env { max-width:760px; margin:0 auto; }
  header { display:flex; align-items:baseline; justify-content:space-between;
           gap:12px; margin-bottom:18px; flex-wrap:wrap; }
  h1 { font-size:1.05rem; font-weight:600; margin:0; }
  .estado { font-size:.8rem; color:var(--fraco); display:flex; align-items:center; gap:6px; }
  .bolinha { width:8px; height:8px; border-radius:50%; background:#35c46a; }
  .bolinha.ruim { background:#e04b4b; }
  .grade { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }
  @media (max-width:640px) { .grade { grid-template-columns:repeat(2,1fr); } }
  .cartao { background:var(--cartao); border:1px solid var(--borda);
            border-radius:14px; padding:16px 18px; margin-top:12px; }
  .grade .cartao { margin-top:0; }
  .rotulo { font-size:.72rem; text-transform:uppercase; letter-spacing:.06em;
            color:var(--fraco); margin-bottom:6px; }
  .valor { font-size:1.8rem; font-weight:650; line-height:1; font-variant-numeric:tabular-nums; }
  .valor span { font-size:.9rem; font-weight:500; color:var(--fraco); margin-left:3px; }
  #mapa { width:100%; height:420px; border-radius:10px; z-index:0; }
  .leaflet-container { background:var(--fundo); font:inherit; }
  /* Os tiles do OSM sao claros; no tema escuro, suaviza para nao ofuscar. */
  @media (prefers-color-scheme: dark) {
    .leaflet-tile { filter:brightness(.75) contrast(1.05); }
  }
  .cabecalho-mapa { display:flex; justify-content:space-between; align-items:center;
                    gap:10px; margin-bottom:6px; flex-wrap:wrap; }
  .seguir { font-size:.72rem; color:var(--fraco); display:flex; align-items:center;
            gap:5px; cursor:pointer; text-transform:none; letter-spacing:0; }
  .semfix { font-size:.8rem; color:var(--fraco); padding:24px 0; text-align:center; }
  .legenda { display:flex; gap:16px; font-size:.75rem; color:var(--fraco); margin-top:8px; flex-wrap:wrap; }
  .legenda i { display:inline-block; width:10px; height:3px; border-radius:2px;
               margin-right:5px; vertical-align:middle; }
  table { width:100%; border-collapse:collapse; font-size:.85rem;
          font-variant-numeric:tabular-nums; }
  td { padding:5px 0; border-bottom:1px solid var(--borda); }
  td:last-child { text-align:right; font-weight:600; }
  tr:last-child td { border-bottom:none; }
  footer { margin-top:20px; font-size:.72rem; color:var(--fraco); text-align:center; }
</style>
</head>
<body>
<div class="env">
  <header>
    <h1>Telemetria da estacao</h1>
    <div class="estado"><span class="bolinha" id="bolinha"></span><span id="conn">iniciando</span></div>
  </header>

  <div class="grade">
    <div class="cartao"><div class="rotulo">Temperatura</div>
      <div class="valor" id="vt" style="color:var(--temp)">--<span>&deg;C</span></div></div>
    <div class="cartao"><div class="rotulo">Umidade</div>
      <div class="valor" id="vu" style="color:var(--umid)">--<span>%</span></div></div>
    <div class="cartao"><div class="rotulo">Velocidade</div>
      <div class="valor" id="vv">--<span>km/h</span></div></div>
    <div class="cartao"><div class="rotulo">Satelites</div>
      <div class="valor" id="vs">--</div></div>
  </div>

  <div class="cartao">
    <div class="cabecalho-mapa">
      <span class="rotulo" style="margin:0">Trajeto</span>
      <label class="seguir"><input type="checkbox" id="seguir" checked>seguir a estacao</label>
    </div>
    <div id="semfix" class="semfix">esperando o primeiro fix do GPS...</div>
    <div id="mapa" style="display:none"></div>
    <div class="legenda">
      <span><i style="background:var(--cru)"></i>GPS cru</span>
      <span><i style="background:var(--kf)"></i>Kalman</span>
    </div>
  </div>

  <div class="cartao">
    <div class="rotulo">Detalhes</div>
    <table id="tab"></table>
  </div>

  <footer><span id="rodape">&nbsp;</span></footer>
</div>

<script>
// --- Mapa (Leaflet + tiles do OpenStreetMap) -------------------------------
// Os tiles vem da internet: o navegador que abre este painel precisa de rede.
// O painel do proprio ESP32 (firmware/painel_wifi) segue com rastro em canvas
// justamente porque em campo pode nao haver internet.
let mapa = null, traceCru = null, traceKf = null, marcador = null;
let primeiroEnquadre = true;

function corDe(nome) {
  return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
}

function iniciarMapa(centro) {
  mapa = L.map('mapa', { zoomControl: true }).setView(centro, 18);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  }).addTo(mapa);

  traceCru = L.polyline([], { color: corDe('--cru'), weight: 3, opacity: .8 }).addTo(mapa);
  traceKf  = L.polyline([], { color: corDe('--kf'),  weight: 4 }).addTo(mapa);
  marcador = L.circleMarker(centro, {
    radius: 7, color: '#fff', weight: 2,
    fillColor: corDe('--kf'), fillOpacity: 1
  }).addTo(mapa);

  // Arrastar o mapa desliga o seguimento automatico - senao o proximo fix
  // puxaria a vista de volta e voce nunca conseguiria olhar em volta.
  mapa.on('dragstart', () => { document.getElementById('seguir').checked = false; });
}

function atualizarMapa(d) {
  const cru = d.pontos || [], kf = d.kf_pontos || [];
  // Preferimos a saida do Kalman para o marcador; sem ela, o fix cru serve.
  const atual = kf.length ? kf[kf.length-1] : (cru.length ? cru[cru.length-1] : null);
  if (!atual) return;

  if (typeof L === 'undefined') {
    document.getElementById('semfix').textContent =
      'nao foi possivel carregar o mapa - este computador esta sem internet?';
    return;
  }

  document.getElementById('semfix').style.display = 'none';
  document.getElementById('mapa').style.display = 'block';

  if (!mapa) {
    iniciarMapa(atual);
    mapa.invalidateSize();   // o div estava escondido; sem isto os tiles saem tortos
  }

  traceCru.setLatLngs(cru);
  traceKf.setLatLngs(kf);
  marcador.setLatLng(atual);

  if (primeiroEnquadre && (cru.length > 1 || kf.length > 1)) {
    const todos = cru.concat(kf);
    mapa.fitBounds(L.latLngBounds(todos), { padding: [30, 30], maxZoom: 18 });
    primeiroEnquadre = false;
  } else if (document.getElementById('seguir').checked) {
    mapa.panTo(atual, { animate: true, duration: .5 });
  }
}

function linha(r, v) { return '<tr><td>' + r + '</td><td>' + v + '</td></tr>'; }

async function atualizar() {
  try {
    const d = await (await fetch('/dados', {cache:'no-store'})).json();
    const vivo = d.broker && d.recebendo;
    document.getElementById('bolinha').className = 'bolinha' + (vivo ? '' : ' ruim');
    document.getElementById('conn').textContent =
      !d.broker ? 'sem broker MQTT'
                : (d.recebendo ? ('recebendo de ' + (d.dev || 'esp32'))
                               : 'broker ok, ESP32 sem publicar');

    const n = (v, c) => v === null || v === undefined ? '--' : v.toFixed(c);
    document.getElementById('vt').innerHTML = n(d.temp,1) + '<span>&deg;C</span>';
    document.getElementById('vu').innerHTML = n(d.umid,1) + '<span>%</span>';
    document.getElementById('vv').innerHTML = n(d.kf_vel,1) + '<span>km/h</span>';
    document.getElementById('vs').textContent = d.sat;

    document.getElementById('tab').innerHTML =
      linha('lat / lon (GPS)', d.lat === null ? '--' : n(d.lat,6) + ', ' + n(d.lon,6)) +
      linha('lat / lon (Kalman)', d.kf_lat === null ? '--' : n(d.kf_lat,6) + ', ' + n(d.kf_lon,6)) +
      linha('incerteza do Kalman', n(d.kf_sigma,1) + ' m') +
      linha('altitude', n(d.alt,1) + ' m') +
      linha('HDOP', n(d.hdop,2)) +
      linha('estado', d.kf_parado ? 'parado' : 'em movimento') +
      linha('passos', d.passos) +
      linha('roll / pitch / yaw', n(d.roll,1)+' / '+n(d.pitch,1)+' / '+n(d.yaw,1)) +
      linha('amostras de IMU', d.amostras_imu);

    atualizarMapa(d);
    document.getElementById('rodape').textContent =
      d.msgs + ' mensagens | ' + d.perdidos + ' perdidas | ' +
      d.pontos.length + ' pontos GPS | ' + d.kf_pontos.length + ' pontos Kalman';
  } catch (e) {
    document.getElementById('bolinha').className = 'bolinha ruim';
    document.getElementById('conn').textContent = 'servidor fora do ar';
  }
}
atualizar();
setInterval(atualizar, 1000);
</script>
</body>
</html>
"""


class Manipulador(BaseHTTPRequestHandler):
    def log_message(self, formato, *args):
        pass  # silencia o log por requisicao - o console mostra o resumo

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

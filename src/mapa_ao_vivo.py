#!/usr/bin/env python3
"""
Mapa ao vivo: le a posicao do WTGAHRS3 e mostra num mapa no navegador,
desenhando o rastro e calculando a distancia percorrida.

    python3 mapa_ao_vivo.py --porta /dev/ttyUSB0

Abre o navegador sozinho em http://localhost:8765

Para testar sem o sensor (gera um trajeto simulado a pe):
    python3 mapa_ao_vivo.py --simular
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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from wtgahrs3 import ENDERECO_PADRAO, WTGAHRS3
except ImportError:
    ENDERECO_PADRAO = 0x50
    WTGAHRS3 = None


# ---------------------------------------------------------------------------
# Estado compartilhado entre a thread do sensor e o servidor web
# ---------------------------------------------------------------------------
estado = {
    "conectado": False,
    "fix": False,
    "lat": None,
    "lon": None,
    "alt": 0.0,
    "vel": 0.0,
    "vel_max": 0.0,
    "sat": 0,
    "hdop": 0.0,
    "distancia_m": 0.0,
    "pontos": [],          # [[lat, lon], ...]
    "inicio": None,
    "amostras": 0,
    "descartados": 0,
    "mensagem": "iniciando...",
}
_lock = threading.Lock()
_parar = False


def haversine_m(a, b) -> float:
    """Distancia em metros entre dois pares (lat, lon)."""
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def registrar_ponto(lat, lon, args, gravador=None):
    """
    Acrescenta um ponto ao rastro, aplicando os filtros de qualidade.

    Dois filtros importantes:

    1. Deslocamento minimo. Um GPS parado oscila alguns metros o tempo todo.
       Sem filtro, a "distancia percorrida" cresce sozinha com o aparelho
       imovel. So contamos o ponto se ele estiver a mais de --min-dist do
       anterior.

    2. Guarda de teletransporte. O firmware deste sensor tem um bug de sinal
       conhecido (ver docs/02) que pode jogar a posicao mais de 100 km para o
       lado. Qualquer salto acima de --max-salto e descartado como glitch.
    """
    with _lock:
        pontos = estado["pontos"]
        if not pontos:
            pontos.append([lat, lon])
            estado["inicio"] = estado["inicio"] or time.time()
            if gravador:
                gravador(lat, lon)
            return True

        d = haversine_m(pontos[-1], (lat, lon))

        if d > args.max_salto:
            estado["descartados"] += 1
            estado["mensagem"] = f"salto de {d/1000:.1f} km descartado (glitch)"
            return False

        if d < args.min_dist:
            return False

        pontos.append([lat, lon])
        estado["distancia_m"] += d
        if gravador:
            gravador(lat, lon)
        return True


# ---------------------------------------------------------------------------
# Leitura do sensor
# ---------------------------------------------------------------------------
def thread_sensor(args):
    global _parar

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

    sensor = None
    intervalo = 1.0 / args.hz if args.hz > 0 else 1.0

    while not _parar:
        if sensor is None:
            try:
                sensor = WTGAHRS3(args.porta, endereco=int(args.endereco, 0),
                                  baudrate=args.baud)
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
                time.sleep(2)
                continue

        inicio = time.monotonic()
        try:
            pos = sensor.ler_posicao()
            with _lock:
                estado["amostras"] += 1
                estado["sat"] = pos.satelites
                estado["hdop"] = pos.hdop
                estado["alt"] = pos.altitude_gps_m
                estado["vel"] = pos.velocidade_kmh
                estado["vel_max"] = max(estado["vel_max"], pos.velocidade_kmh)
                estado["fix"] = pos.tem_fix

            if pos.tem_fix and pos.hdop <= args.hdop_max:
                with _lock:
                    estado["lat"], estado["lon"] = pos.latitude, pos.longitude
                    estado["mensagem"] = "rastreando"
                registrar_ponto(pos.latitude, pos.longitude, args, gravador)
            elif not pos.tem_fix:
                with _lock:
                    estado["mensagem"] = f"aguardando fix ({pos.satelites} satelites)"
            else:
                with _lock:
                    estado["mensagem"] = f"HDOP {pos.hdop:.1f} alto - ponto ignorado"

        except Exception as e:
            with _lock:
                estado["conectado"] = False
                estado["mensagem"] = f"leitura falhou: {e}"
            try:
                sensor.fechar()
            except Exception:
                pass
            sensor = None
            continue

        resto = intervalo - (time.monotonic() - inicio)
        if resto > 0:
            time.sleep(resto)

    if arq:
        arq.close()


def _simular(args, gravador):
    """Trajeto ficticio saindo de Lavras/MG, para testar sem hardware."""
    lat, lon = -21.225671, -44.977374
    rumo = random.uniform(0, 2 * math.pi)
    with _lock:
        estado["conectado"] = True
        estado["mensagem"] = "MODO SIMULACAO (sem sensor real)"

    while not _parar:
        rumo += random.uniform(-0.3, 0.3)
        passo = 1.4 / 111320.0          # ~1,4 m/s, caminhada
        lat += passo * math.cos(rumo)
        lon += passo * math.sin(rumo) / math.cos(math.radians(lat))
        with _lock:
            estado["fix"] = True
            estado["lat"], estado["lon"] = lat, lon
            estado["sat"] = random.randint(12, 20)
            estado["hdop"] = round(random.uniform(0.8, 1.6), 2)
            estado["alt"] = 919.0
            estado["vel"] = round(random.uniform(4.0, 6.0), 2)
            estado["vel_max"] = max(estado["vel_max"], estado["vel"])
            estado["amostras"] += 1
        registrar_ponto(lat, lon, args, gravador)
        time.sleep(1.0 / max(args.hz, 0.1))


# ---------------------------------------------------------------------------
# Servidor web
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass   # silencia o log de cada requisicao

    def _envia(self, corpo, tipo="application/json"):
        dados = corpo.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{tipo}; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        if self.path.startswith("/dados"):
            with _lock:
                decorrido = time.time() - estado["inicio"] if estado["inicio"] else 0
                payload = {
                    "conectado": estado["conectado"],
                    "fix": estado["fix"],
                    "lat": estado["lat"],
                    "lon": estado["lon"],
                    "alt": estado["alt"],
                    "vel": estado["vel"],
                    "vel_max": estado["vel_max"],
                    "sat": estado["sat"],
                    "hdop": estado["hdop"],
                    "distancia_m": estado["distancia_m"],
                    "pontos": estado["pontos"],
                    "n_pontos": len(estado["pontos"]),
                    "amostras": estado["amostras"],
                    "descartados": estado["descartados"],
                    "decorrido": decorrido,
                    "mensagem": estado["mensagem"],
                }
            self._envia(json.dumps(payload))
        elif self.path.startswith("/limpar"):
            with _lock:
                estado["pontos"] = []
                estado["distancia_m"] = 0.0
                estado["vel_max"] = 0.0
                estado["inicio"] = time.time()
            self._envia('{"ok":true}')
        else:
            self._envia(PAGINA, "text/html")


PAGINA = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Estacao Movel - Mapa ao vivo</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root{
    --bg:#0f1419; --painel:#1a2129; --borda:#2a3441;
    --txt:#e6edf3; --txt2:#8b949e; --ok:#3fb950; --alerta:#d29922; --erro:#f85149;
    --destaque:#58a6ff;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
            background:var(--bg);color:var(--txt);overflow:hidden}
  #mapa{position:absolute;inset:0;z-index:1}
  #painel{
    position:absolute;top:12px;left:12px;z-index:1000;width:290px;
    background:rgba(26,33,41,.95);backdrop-filter:blur(8px);
    border:1px solid var(--borda);border-radius:12px;padding:16px;
    box-shadow:0 8px 32px rgba(0,0,0,.5)
  }
  #cabecalho{display:flex;align-items:center;gap:8px;margin-bottom:14px}
  #ponto{width:10px;height:10px;border-radius:50%;background:var(--erro);
         box-shadow:0 0 8px currentColor;flex:none}
  #ponto.ok{background:var(--ok)}
  #ponto.esperando{background:var(--alerta)}
  #titulo{font-size:13px;font-weight:600;letter-spacing:.3px}
  #msg{font-size:11px;color:var(--txt2);margin-top:2px}
  .grande{font-size:38px;font-weight:700;line-height:1;letter-spacing:-1px}
  .unidade{font-size:15px;color:var(--txt2);font-weight:400;margin-left:4px}
  .rotulo{font-size:10px;text-transform:uppercase;letter-spacing:1px;
          color:var(--txt2);margin-bottom:6px}
  .bloco{margin-bottom:16px}
  .grade{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .celula{background:#0d1117;border:1px solid var(--borda);border-radius:8px;padding:9px}
  .celula .v{font-size:17px;font-weight:600}
  .celula .r{font-size:9px;text-transform:uppercase;letter-spacing:.8px;
             color:var(--txt2);margin-top:2px}
  #coords{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:11px;
          color:var(--txt2);margin-top:10px;line-height:1.6}
  .botoes{display:flex;gap:8px;margin-top:14px}
  button{flex:1;background:#21262d;color:var(--txt);border:1px solid var(--borda);
         border-radius:7px;padding:8px;font-size:11px;cursor:pointer;
         font-family:inherit;transition:.15s}
  button:hover{background:#30363d;border-color:#484f58}
  button.ativo{background:var(--destaque);border-color:var(--destaque);color:#07121f;
               font-weight:600}
  #aviso{position:absolute;bottom:12px;left:12px;z-index:1000;font-size:11px;
         color:var(--txt2);background:rgba(26,33,41,.9);padding:6px 10px;
         border-radius:6px;border:1px solid var(--borda);display:none}
  @media(max-width:600px){
    #painel{width:auto;left:8px;right:8px;top:8px;padding:12px}
    .grande{font-size:30px}
  }
</style>
</head>
<body>
<div id="mapa"></div>

<div id="painel">
  <div id="cabecalho">
    <div id="ponto"></div>
    <div>
      <div id="titulo">Estacao Movel</div>
      <div id="msg">conectando...</div>
    </div>
  </div>

  <div class="bloco">
    <div class="rotulo">Distancia percorrida</div>
    <div><span class="grande" id="dist">0</span><span class="unidade" id="dist_un">m</span></div>
  </div>

  <div class="grade">
    <div class="celula"><div class="v" id="vel">0,0</div><div class="r">km/h</div></div>
    <div class="celula"><div class="v" id="vmax">0,0</div><div class="r">max km/h</div></div>
    <div class="celula"><div class="v" id="tempo">0:00</div><div class="r">duracao</div></div>
    <div class="celula"><div class="v" id="alt">--</div><div class="r">altitude m</div></div>
    <div class="celula"><div class="v" id="sat">0</div><div class="r">satelites</div></div>
    <div class="celula"><div class="v" id="hdop">--</div><div class="r">hdop</div></div>
  </div>

  <div id="coords">aguardando posicao...</div>

  <div class="botoes">
    <button id="btn_seguir" class="ativo">Seguir</button>
    <button id="btn_limpar">Limpar</button>
    <button id="btn_gpx">GPX</button>
  </div>
</div>

<div id="aviso"></div>

<script>
// maxZoom 22 com maxNativeZoom 19: o servidor de tiles so fornece ate o
// nivel 19, entao o Leaflet amplia a propria imagem alem disso. Fica menos
// nitido, mas permite chegar bem perto - util para ver o rastro em detalhe.
const mapa = L.map('mapa', {zoomControl:false, maxZoom:22})
              .setView([-21.2257,-44.9774], 17);
L.control.zoom({position:'bottomright'}).addTo(mapa);

const ruas = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom:22, maxNativeZoom:19, attribution:'&copy; OpenStreetMap'
}).addTo(mapa);

const satelite = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
  maxZoom:22, maxNativeZoom:19, attribution:'&copy; Esri'
});

L.control.layers({'Ruas':ruas, 'Satelite':satelite}, null,
                 {position:'bottomright'}).addTo(mapa);

// scroll do mouse com passo menor, para ajuste fino do zoom
mapa.options.zoomSnap = 0.25;
mapa.options.wheelPxPerZoomLevel = 100;

// teclas + e - tambem funcionam
document.addEventListener('keydown', e => {
  if(e.key === '+' || e.key === '=') mapa.zoomIn(1);
  if(e.key === '-' || e.key === '_') mapa.zoomOut(1);
});

const rastro = L.polyline([], {color:'#58a6ff', weight:4, opacity:.85}).addTo(mapa);
const marcador = L.circleMarker([0,0], {
  radius:8, color:'#fff', weight:3, fillColor:'#58a6ff', fillOpacity:1
}).addTo(mapa);
let marcadorInicio = null;

let seguir = true;
let primeiro = true;
let ultimosPontos = [];

document.getElementById('btn_seguir').onclick = function(){
  seguir = !seguir;
  this.classList.toggle('ativo', seguir);
};
document.getElementById('btn_limpar').onclick = async function(){
  if(!confirm('Apagar o rastro e zerar a distancia?')) return;
  await fetch('/limpar');
  rastro.setLatLngs([]);
  if(marcadorInicio){ mapa.removeLayer(marcadorInicio); marcadorInicio=null; }
};
document.getElementById('btn_gpx').onclick = function(){
  if(!ultimosPontos.length){ alert('Nenhum ponto gravado ainda.'); return; }
  let g = '<?xml version="1.0" encoding="UTF-8"?>\n';
  g += '<gpx version="1.1" creator="EstacaoMovel" xmlns="http://www.topografix.com/GPX/1/1">\n';
  g += '<trk><name>Trajeto ' + new Date().toISOString().slice(0,19) + '</name><trkseg>\n';
  for(const p of ultimosPontos){ g += '<trkpt lat="'+p[0]+'" lon="'+p[1]+'"></trkpt>\n'; }
  g += '</trkseg></trk></gpx>';
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([g], {type:'application/gpx+xml'}));
  a.download = 'trajeto_' + Date.now() + '.gpx';
  a.click();
};

function duracao(s){
  s = Math.floor(s);
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60;
  return h > 0 ? h+':'+String(m).padStart(2,'0')+':'+String(x).padStart(2,'0')
               : m+':'+String(x).padStart(2,'0');
}
const br = (n,d) => n.toFixed(d).replace('.',',');

async function atualizar(){
  let d;
  try { d = await (await fetch('/dados')).json(); }
  catch(e){
    document.getElementById('ponto').className = '';
    document.getElementById('msg').textContent = 'servidor offline';
    return;
  }

  const ponto = document.getElementById('ponto');
  ponto.className = !d.conectado ? '' : (d.fix ? 'ok' : 'esperando');
  document.getElementById('msg').textContent = d.mensagem;

  document.getElementById('dist').textContent =
    d.distancia_m < 1000 ? Math.round(d.distancia_m) : br(d.distancia_m/1000, 2);
  document.getElementById('dist_un').textContent = d.distancia_m < 1000 ? 'm' : 'km';

  document.getElementById('vel').textContent   = br(d.vel, 1);
  document.getElementById('vmax').textContent  = br(d.vel_max, 1);
  document.getElementById('tempo').textContent = duracao(d.decorrido);
  document.getElementById('alt').textContent   = d.fix ? Math.round(d.alt) : '--';
  document.getElementById('sat').textContent   = d.sat;
  document.getElementById('hdop').textContent  = d.hdop ? br(d.hdop,1) : '--';

  const aviso = document.getElementById('aviso');
  if(d.descartados > 0){
    aviso.style.display = 'block';
    aviso.textContent = d.descartados + ' ponto(s) descartado(s) por salto anormal';
  } else { aviso.style.display = 'none'; }

  if(d.lat !== null && d.lon !== null){
    document.getElementById('coords').innerHTML =
      'lat ' + d.lat.toFixed(6) + '<br>lon ' + d.lon.toFixed(6) +
      '<br>' + d.n_pontos + ' pontos / ' + d.amostras + ' amostras';
    marcador.setLatLng([d.lat, d.lon]);
    if(primeiro){ mapa.setView([d.lat, d.lon], 19); primeiro = false; }
    if(seguir) mapa.panTo([d.lat, d.lon], {animate:true, duration:.4});
  }

  if(d.pontos.length){
    ultimosPontos = d.pontos;
    rastro.setLatLngs(d.pontos);
    if(!marcadorInicio){
      marcadorInicio = L.circleMarker(d.pontos[0], {
        radius:6, color:'#3fb950', weight:2, fillColor:'#0f1419', fillOpacity:1
      }).addTo(mapa).bindTooltip('Inicio');
    }
  }
}

atualizar();
setInterval(atualizar, 1000);
</script>
</body>
</html>
"""


def main() -> int:
    global _parar

    p = argparse.ArgumentParser(description="Mapa ao vivo do WTGAHRS3")
    p.add_argument("--porta", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--endereco", default=hex(ENDERECO_PADRAO))
    p.add_argument("--hz", type=float, default=1.0, help="leituras por segundo")
    p.add_argument("--porta-web", type=int, default=8765, dest="porta_web")
    p.add_argument("--csv", default="../data/trajeto.csv", help="'' desativa a gravacao")
    p.add_argument("--min-dist", type=float, default=3.0, dest="min_dist",
                   help="deslocamento minimo em metros para contar um ponto")
    p.add_argument("--max-salto", type=float, default=500.0, dest="max_salto",
                   help="salto acima disso e descartado como glitch, em metros")
    p.add_argument("--hdop-max", type=float, default=5.0, dest="hdop_max",
                   help="ignora pontos com HDOP acima disso")
    p.add_argument("--simular", action="store_true",
                   help="gera trajeto ficticio, para testar sem o sensor")
    p.add_argument("--sem-navegador", action="store_true", dest="sem_navegador")
    args = p.parse_args()

    if not args.simular and WTGAHRS3 is None:
        print("ERRO: wtgahrs3.py nao encontrado. Rode a partir da pasta src/.")
        return 1

    t = threading.Thread(target=thread_sensor, args=(args,), daemon=True)
    t.start()

    servidor = HTTPServer(("127.0.0.1", args.porta_web), Handler)
    url = f"http://localhost:{args.porta_web}"

    print("=" * 58)
    print("  MAPA AO VIVO" + ("  [SIMULACAO]" if args.simular else ""))
    print("=" * 58)
    print(f"  Abra no navegador: {url}")
    print(f"  Filtros: min {args.min_dist:.0f} m entre pontos, "
          f"salto max {args.max_salto:.0f} m, HDOP max {args.hdop_max:.1f}")
    if args.csv:
        print(f"  Gravando em: {args.csv}")
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
        _parar = True
        with _lock:
            print(f"Distancia total: {estado['distancia_m']:.0f} m "
                  f"em {len(estado['pontos'])} pontos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

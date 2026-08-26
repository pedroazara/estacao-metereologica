#!/usr/bin/env python3
"""
Base da estacao: servidor HTTP que RECEBE as coletas do celular, guarda o cru,
roda o filtro de Kalman em lote e mostra o resultado.

    python3 servidor_telemetria.py            # porta 8771

Painel em http://localhost:8771

--------------------------------------------------------------------------
POR QUE E SO ISSO (simplificado em 26/08/2026)

A base deixou de monitorar o ESP ao vivo. O fluxo virou um so:

    ESP --BLE--> celular (coleta e guarda) --HTTP--> base (guarda + processa)

Nao ha mais MQTT, nem broker, nem CSV ao vivo, nem filtro rodando em stream.
A base tem tres deveres, todos em lote, na hora que a coleta chega:

    1. guardar o cru (JSONL imutavel, custodia)
    2. rodar o Kalman e gravar o derivado (SQLite, recomputavel)
    3. mostrar as sessoes e o trajeto (painel web)

O caminho ao vivo saiu inteiro - com ele, sumiu a dependencia de paho-mqtt e
uma categoria inteira de bug (jitter de rede no dt, amostra fora de ordem).
Ver docs/04-telemetria.md.
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from processar_sessao import processar_sessao


# ---------------------------------------------------------------------------
# Caminhos e banco
# ---------------------------------------------------------------------------
def _raiz():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pasta_sincronizadas():
    pasta = os.path.join(_raiz(), "data", "sincronizadas")
    os.makedirs(pasta, exist_ok=True)
    return pasta


def _caminho_banco():
    return os.path.join(_raiz(), "data", "estacao.db")


def _limpar_nome(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)[:40] or "x"


def _contar_linhas(caminho):
    try:
        with open(caminho, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _listar_sessoes():
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
    nome = os.path.basename(nome or "")
    db = _caminho_banco()
    if not nome or not os.path.exists(db):
        return {"cru": [], "kf": []}
    con = sqlite3.connect(db)
    try:
        linhas = con.execute(
            "SELECT ts, lat, lon, kf_lat, kf_lon, kf_vel, kf_sigma, temp, umid "
            "FROM derivado WHERE sessao = ? ORDER BY ts", (nome,)).fetchall()
    except sqlite3.Error:
        return {"cru": [], "kf": [], "pontos": []}
    finally:
        con.close()
    cru = [[la, lo] for (_ts, la, lo, _a, _b, _v, _s, _t, _u) in linhas]
    kf = [[a, b] for (_ts, _la, _lo, a, b, _v, _s, _t, _u) in linhas]
    # Um ponto por fix, com o que medir nele: e isso que o mapa colore por
    # temperatura e mostra ao passar o mouse. Usa a posicao filtrada (kf) para
    # o ponto ficar sobre o trajeto limpo, nao sobre o jitter cru.
    pontos = [{"lat": a, "lon": b, "temp": t, "umid": u, "vel": v}
              for (_ts, _la, _lo, a, b, v, _s, t, u) in linhas]
    temps = [t for (*_, t, _u) in linhas if t is not None]
    ult = linhas[-1] if linhas else None
    resumo = {"vel": ult[5], "sigma": ult[6], "temp": ult[7], "umid": ult[8],
              "n": len(linhas),
              "temp_min": min(temps) if temps else None,
              "temp_max": max(temps) if temps else None} if ult else {}
    return {"cru": cru, "kf": kf, "pontos": pontos, "resumo": resumo}


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------
PAGINA = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Base da Estacao</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--borda:#232d38;--txt:#e6edf3;--txt2:#8b949e;
        --verde:#3fb950;--azul:#58a6ff}
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
            background:var(--bg);color:var(--txt)}
  #topo{height:56px;display:flex;align-items:center;gap:12px;padding:0 18px;
        background:var(--card);border-bottom:1px solid var(--borda)}
  #topo h1{font-size:15px;font-weight:600}
  .pilula{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--txt2);
          background:#0d1117;border:1px solid var(--borda);border-radius:999px;padding:5px 11px}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--verde);box-shadow:0 0 7px var(--verde)}
  #corpo{display:flex;height:calc(100% - 56px)}
  #lista{width:300px;border-right:1px solid var(--borda);overflow-y:auto;flex:none;background:var(--card)}
  .cab{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--txt2);padding:14px 16px 8px}
  .sessao{padding:12px 16px;border-bottom:1px solid var(--borda);cursor:pointer}
  .sessao:hover{background:#1c2430}
  .sessao.sel{background:#132132;border-left:3px solid var(--azul)}
  .sessao .nome{font-size:12.5px;font-family:ui-monospace,monospace;word-break:break-all}
  .sessao .sub{font-size:11px;color:var(--txt2);margin-top:3px}
  #vazio{padding:24px 16px;font-size:12.5px;color:var(--txt2);line-height:1.6}
  #direita{flex:1;display:flex;flex-direction:column;min-width:0;position:relative}
  #mapa{flex:1;background:#0a0e13;margin:14px;border-radius:12px;border:1px solid var(--borda);cursor:crosshair}
  #tip{position:absolute;pointer-events:none;display:none;z-index:5;
       background:#0d1117ee;border:1px solid var(--borda);border-radius:8px;
       padding:8px 10px;font-size:12px;font-family:ui-monospace,monospace;white-space:nowrap}
  #tip b{color:#ff7a5e}
  #escala{position:absolute;top:24px;right:26px;z-index:4;background:#0d1117cc;
          border:1px solid var(--borda);border-radius:8px;padding:8px 10px;font-size:10px;color:var(--txt2)}
  #escala .barra{width:14px;height:90px;border-radius:3px;margin:4px auto;
       background:linear-gradient(to top,#58a6ff,#3fb950,#d29922,#f85149)}
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
  <span class="pilula"><span class="dot"></span>base no ar</span>
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
    <div id="tip"></div>
    <div id="escala" style="display:none">
      <div style="text-align:center" id="esc_max">--</div>
      <div class="barra"></div>
      <div style="text-align:center" id="esc_min">--</div>
      <div style="text-align:center;margin-top:4px">°C</div>
    </div>
    <div id="resumo" style="display:none"></div>
  </div>
</div>
<script>
const $ = s => document.querySelector(s);
let sel = null, traj = null;
const fmtData = iso => iso ? iso.replace('T',' ').slice(0,16) : '';

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
    box.querySelectorAll('.sessao').forEach(el => el.onclick = () => selecionar(el.dataset.n));
    if(!sel && lista.length) selecionar(lista[0].sessao);
  }catch(e){}
}

async function selecionar(nome){
  sel = nome;
  document.querySelectorAll('.sessao').forEach(el => el.classList.toggle('sel', el.dataset.n===nome));
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
  const rect = c.getBoundingClientRect(); c.width = rect.width; c.height = rect.height;
  ctx.clearRect(0,0,c.width,c.height);
  if(!traj || !traj.cru.length){
    ctx.fillStyle='#8b949e'; ctx.font='13px system-ui'; ctx.textAlign='center';
    ctx.fillText('Esta coleta nao tem pontos de posicao.', c.width/2, c.height/2); return;
  }
  const lat0 = traj.cru[0][0], lon0 = traj.cru[0][1];
  const cosLat = Math.cos(lat0*Math.PI/180);
  const m = p => [ (p[1]-lon0)*111320*cosLat, -(p[0]-lat0)*111320 ];
  const todos = traj.cru.concat(traj.kf).map(m);
  let minX=1e9,maxX=-1e9,minY=1e9,maxY=-1e9;
  for(const [x,y] of todos){ minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y); }
  const mg=12; minX-=mg;maxX+=mg;minY-=mg;maxY+=mg;
  // Piso de escala: parado, o GPS oscila poucos metros; sem minimo o ajuste
  // amplia o ruido ate encher a tela.
  const SPAN_MIN = 50;
  const esc = Math.min(c.width/Math.max(maxX-minX,SPAN_MIN), c.height/Math.max(maxY-minY,SPAN_MIN));
  const cx=(minX+maxX)/2, cy=(minY+maxY)/2;
  const T = ([x,y]) => [ c.width/2+(x-cx)*esc, c.height/2+(y-cy)*esc ];
  const linha = (pts,cor,larg) => {
    if(pts.length<2) return;
    ctx.strokeStyle=cor; ctx.lineWidth=larg; ctx.lineJoin='round'; ctx.lineCap='round'; ctx.beginPath();
    pts.forEach((p,i)=>{ const [x,y]=T(m(p)); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }); ctx.stroke();
  };
  // O trajeto filtrado como linha fina de fundo; a temperatura vem nos pontos.
  linha(traj.kf.length>1?traj.kf:traj.cru, 'rgba(63,185,80,.5)', 2.5);

  // Pontos coloridos pela temperatura. Escala sequencial azul->verde->amarelo->
  // vermelho, mapeada na faixa da propria sessao (senao uma variacao de 0,5 C
  // ficaria toda de uma cor). Guarda a posicao de tela para o hover achar.
  const r = traj.resumo||{};
  const tmin = r.temp_min, tmax = r.temp_max;
  const faixa = (tmax!=null && tmax> tmin) ? tmax - tmin : 1;
  pontosTela = [];
  for(const p of (traj.pontos||[])){
    const [x,y] = T(m([p.lat,p.lon]));
    pontosTela.push({x,y,p});
    ctx.beginPath(); ctx.arc(x,y,5,0,7);
    ctx.fillStyle = p.temp==null ? '#7d8794' : corTemp((p.temp-tmin)/faixa);
    ctx.fill();
    ctx.strokeStyle='rgba(13,17,23,.8)'; ctx.lineWidth=1; ctx.stroke();
  }
  // Marca o inicio e o fim do percurso.
  if(pontosTela.length){
    const A=pontosTela[0], B=pontosTela[pontosTela.length-1];
    ctx.font='10px ui-monospace'; ctx.fillStyle='#e6edf3'; ctx.textAlign='center';
    ctx.fillText('inicio', A.x, A.y-9); ctx.fillText('fim', B.x, B.y-9);
  }
  // Legenda da escala de temperatura.
  const esc2=$('#escala');
  if(tmin!=null){ esc2.style.display='block';
    $('#esc_max').textContent=tmax.toFixed(1); $('#esc_min').textContent=tmin.toFixed(1);
  } else esc2.style.display='none';
}

// Sequencial azul->verde->amarelo->vermelho para t em [0,1].
function corTemp(t){
  t = Math.max(0, Math.min(1, t));
  const paradas = [[88,166,255],[63,185,80],[210,153,34],[248,81,73]];
  const f = t*(paradas.length-1); const i = Math.floor(f); const k = f-i;
  const a = paradas[i], b = paradas[Math.min(i+1,paradas.length-1)];
  return `rgb(${a[0]+(b[0]-a[0])*k|0},${a[1]+(b[1]-a[1])*k|0},${a[2]+(b[2]-a[2])*k|0})`;
}

// Hover: acha o ponto mais proximo e mostra temperatura/umidade/velocidade.
let pontosTela = [];
$('#mapa').addEventListener('mousemove', ev => {
  const c=$('#mapa'), rect=c.getBoundingClientRect();
  const mx=ev.clientX-rect.left, my=ev.clientY-rect.top;
  let perto=null, dmin=14*14;
  for(const pt of pontosTela){ const dx=pt.x-mx, dy=pt.y-my, d=dx*dx+dy*dy; if(d<dmin){dmin=d;perto=pt;} }
  const tip=$('#tip');
  if(!perto){ tip.style.display='none'; return; }
  const p=perto.p;
  tip.innerHTML = `<b>${p.temp!=null?p.temp.toFixed(1)+' °C':'temp --'}</b>`
    + `  ·  ${p.umid!=null?p.umid.toFixed(0)+' %':'--'}`
    + `<br>${(p.vel||0).toFixed(1)} km/h`;
  tip.style.display='block';
  tip.style.left=Math.min(perto.x+12, rect.width-140)+'px';
  tip.style.top=(perto.y+12)+'px';
});
$('#mapa').addEventListener('mouseleave', ()=>{ $('#tip').style.display='none'; });

window.addEventListener('resize', desenhar);
setInterval(()=>{ $('#relogio').textContent = new Date().toLocaleTimeString('pt-BR'); }, 1000);
carregarSessoes();
setInterval(carregarSessoes, 5000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Servidor HTTP
# ---------------------------------------------------------------------------
class Manipulador(BaseHTTPRequestHandler):
    def log_message(self, formato, *args):
        pass

    def _json(self, codigo, obj):
        corpo = json.dumps(obj).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_POST(self):
        # Recepcao das coletas do celular. Manifesto por nome de arquivo: se a
        # sessao ja chegou, nao regrava (evita duplicata quando o envio repete).
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
        # Manifesto por TAMANHO, nao so por nome: se a sessao ja chegou mas o
        # novo envio e MAIOR, e uma versao mais completa da mesma coleta (o caso
        # de ter sincronizado durante a gravacao e reenviado depois de parar).
        # Aceita a maior e reprocessa; um reenvio igual ou menor e no-op.
        if os.path.exists(destino) and len(corpo) <= os.path.getsize(destino):
            self._json(200, {"ok": True, "ja_tinha": True,
                             "linhas": _contar_linhas(destino)})
            return

        # Grava em arquivo temporario e renomeia: uma queda no meio da escrita
        # nao deixa um arquivo pela metade passar por sessao completa.
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

        # Processa na chegada. Uma falha aqui NAO perde a coleta - o cru ja esta
        # salvo, a sessao pode ser reprocessada depois.
        n_deriv, erro = 0, None
        try:
            n_deriv = processar_sessao(destino, _caminho_banco())
            print(f"sync: processada {sessao} - {n_deriv} pontos no banco")
        except Exception as e:      # noqa: BLE001 - custodia acima de tudo
            erro = str(e)
            print(f"sync: cru salvo, processamento falhou: {e}")

        self._json(200, {"ok": True, "ja_tinha": False, "linhas": linhas,
                         "derivados": n_deriv, "erro_processamento": erro})

    def do_GET(self):
        if self.path == "/":
            corpo, tipo = PAGINA.encode(), "text/html; charset=utf-8"
        elif self.path.startswith("/sessoes"):
            corpo, tipo = json.dumps(_listar_sessoes()).encode(), "application/json"
        elif self.path.startswith("/sessao"):
            q = parse_qs(urlparse(self.path).query)
            nome = (q.get("nome") or [""])[0]
            corpo, tipo = json.dumps(_trajeto_sessao(nome)).encode(), "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--porta", type=int, default=8771, help="porta do painel web")
    args = p.parse_args()

    os.makedirs(os.path.join(_raiz(), "data"), exist_ok=True)
    servidor = HTTPServer(("0.0.0.0", args.porta), Manipulador)
    print(f"base no ar em http://localhost:{args.porta}")
    print("recebendo coletas em POST /sync   ·   Ctrl+C para parar")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrando.")


if __name__ == "__main__":
    main()

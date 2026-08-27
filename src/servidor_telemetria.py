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
import planejamento


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


def _validar_tracos(tracos):
    """Confere o formato de `tracos`: lista de {"raio_m": numero (0,500],
    "pontos": [[lat,lon], ...] nao vazia}. Devolve a mensagem de erro, ou None
    se estiver tudo certo. Cada pincelada carrega o proprio raio - e o que
    permite mudar o tamanho do pincel sem afetar o que ja foi pintado."""
    if not isinstance(tracos, list) or not tracos:
        return "tracos precisa ser uma lista nao vazia"
    for t in tracos:
        if not isinstance(t, dict):
            return "cada traco precisa ser {raio_m, pontos}"
        raio_m = t.get("raio_m")
        pontos = t.get("pontos")
        if not isinstance(raio_m, (int, float)) or raio_m <= 0 or raio_m > 500:
            return "raio_m de um traco fora da faixa (0-500 m)"
        if not isinstance(pontos, list) or not pontos:
            return "um traco esta sem pontos"
    return None


def _limpar_nome(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)[:40] or "x"


def _contar_linhas(caminho):
    try:
        with open(caminho, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _ler_corpo(handler):
    """Le o corpo do POST aceitando as DUAS formas de envio.

    O HttpClient do Dart, quando o Content-Length nao e definido, manda o corpo
    em "chunked" (Transfer-Encoding), sem Content-Length. A leitura antiga so
    olhava Content-Length e recebia 0 bytes - era o bug de "chega 0 dados"
    apesar de o arquivo no telefone ter tudo. Agora: se ha Content-Length, le
    n bytes; senao, se for chunked, decodifica os pedacos ate o de tamanho 0.
    """
    cl = handler.headers.get("Content-Length")
    if cl is not None:
        n = int(cl)
        return handler.rfile.read(n) if n > 0 else b""

    te = (handler.headers.get("Transfer-Encoding") or "").lower()
    if "chunked" in te:
        partes = []
        while True:
            linha = handler.rfile.readline().strip()
            if not linha:
                continue
            tamanho = int(linha.split(b";")[0], 16)
            if tamanho == 0:
                handler.rfile.readline()   # consome o CRLF final
                break
            partes.append(handler.rfile.read(tamanho))
            handler.rfile.readline()        # CRLF apos cada pedaco
        return b"".join(partes)

    return b""


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
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root{--bg:#0d1117;--card:#161b22;--borda:#232d38;--txt:#e6edf3;--txt2:#8b949e;
        --verde:#3fb950;--azul:#58a6ff}
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
            background:var(--bg);color:var(--txt)}
  #topo{height:56px;display:flex;align-items:center;gap:12px;padding:0 18px;
        background:var(--card);border-bottom:1px solid var(--borda);position:relative;z-index:1100}
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
  #mapa{flex:1;background:#0a0e13;margin:14px;border-radius:12px;border:1px solid var(--borda);z-index:0}
  .leaflet-container{background:#0a0e13;font:inherit}
  @media (prefers-color-scheme: dark){.leaflet-tile{filter:brightness(.8) contrast(1.05)}}
  #escala{position:absolute;top:24px;right:26px;z-index:1000;background:#0d1117cc;
          border:1px solid var(--borda);border-radius:8px;padding:8px 10px;font-size:10px;color:var(--txt2)}
  #escala .barra{width:14px;height:90px;border-radius:3px;margin:4px auto;
       background:linear-gradient(to top,#58a6ff,#3fb950,#d29922,#f85149)}
  #resumo{display:flex;gap:22px;padding:0 20px 16px;flex-wrap:wrap}
  .n .v{font-size:19px;font-weight:650;font-family:ui-monospace,monospace}
  .n .r{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--txt2)}
  #placeholder{flex:1;display:flex;align-items:center;justify-content:center;
               color:var(--txt2);font-size:13px;text-align:center;padding:30px}
  .pontotemp{border-radius:50%;border:1.5px solid rgba(13,17,23,.85)}
  .leaflet-popup-content-wrapper{background:#161b22;color:var(--txt);border-radius:8px}
  .leaflet-popup-tip{background:#161b22}
  .leaflet-popup-content{font-family:ui-monospace,monospace;font-size:12px;margin:8px 10px}
  .leaflet-popup-content b{color:#ff7a5e}

  .aba{background:transparent;border:1px solid var(--borda);color:var(--txt2);
       padding:5px 12px;border-radius:999px;font-size:12px;cursor:pointer;font-family:inherit}
  .aba.on{background:var(--azul);border-color:var(--azul);color:#07121f;font-weight:600}
  .btn{background:#1c242e;color:var(--txt);border:1px solid var(--borda);border-radius:7px;
       padding:7px 12px;font-size:11.5px;cursor:pointer;font-family:inherit}
  .btn:hover{background:#26303c}
  .btn.primario{background:var(--verde);border-color:var(--verde);color:#07120d;font-weight:600}
  .btn.perigo{color:#f85149;border-color:#4a2325}
  #corpo_planos{display:none;height:calc(100% - 56px)}
  #painel_novo{padding:14px 16px}
  #barra_pincel{display:none;align-items:center;gap:10px;padding:10px 16px;
                background:var(--card);border-bottom:1px solid var(--borda);flex-wrap:wrap}
  #barra_pincel label{font-size:11px;color:var(--txt2)}
  #barra_pincel input[type=text]{background:#0d1117;border:1px solid var(--borda);color:var(--txt);
                                  border-radius:6px;padding:5px 8px;font-size:12px;width:90px}
  #raio_val{font-family:ui-monospace,monospace;font-size:12px;min-width:44px;display:inline-block}
  #resumo_plano{display:none;padding:14px 20px}
  #acoes_plano{display:flex;gap:8px;padding:0 20px 14px;flex-wrap:wrap}
</style>
</head>
<body>
<div id="topo">
  <h1>Base da Estacao</h1>
  <button class="aba on" data-aba="sessoes">Sessoes</button>
  <button class="aba" data-aba="planos">Planejar area</button>
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
      Ela chega aqui ja processada.
    </div>
  </div>
  <div id="direita">
    <div id="placeholder">Selecione uma coleta a esquerda para ver o trajeto.</div>
    <div id="mapa" style="display:none"></div>
    <div id="escala" style="display:none">
      <div style="text-align:center" id="esc_max">--</div>
      <div class="barra"></div>
      <div style="text-align:center" id="esc_min">--</div>
      <div style="text-align:center;margin-top:4px">°C</div>
    </div>
    <div id="resumo" style="display:none"></div>
  </div>
</div>

<div id="corpo_planos">
  <div id="lista">
    <div class="cab">Areas planejadas</div>
    <div id="painel_novo">
      <button class="btn primario" id="btn_novo_plano" style="width:100%">+ Nova area</button>
    </div>
    <div id="planos"></div>
    <div id="vazio_planos" style="display:none;padding:0 16px 16px;font-size:12.5px;
         color:var(--txt2);line-height:1.6">
      Nenhuma area planejada ainda.<br><br>
      Toque <b>+ Nova area</b>, escolha o tamanho do pincel e arraste no mapa
      para pintar onde o usuario deve medir.
    </div>
  </div>
  <div id="direita">
    <div id="barra_pincel">
      <label>pincel</label>
      <input type="range" id="raio_pincel" min="3" max="60" value="15">
      <span id="raio_val">15 m</span>
      <label style="margin-left:10px">estacao</label>
      <input type="text" id="dev_plano" value="esp32-01">
      <button class="btn primario" id="btn_modo_pintar">Pincel</button>
      <button class="btn" id="btn_modo_apagar">Borracha</button>
      <button class="btn" id="btn_desfazer">Desfazer</button>
      <button class="btn" id="btn_limpar">Limpar</button>
      <button class="btn primario" id="btn_salvar_plano">Salvar</button>
      <button class="btn" id="btn_cancelar_plano">Cancelar</button>
    </div>
    <div id="placeholder_planos" style="flex:1;display:flex;align-items:center;justify-content:center;
         color:var(--txt2);font-size:13px;text-align:center;padding:30px">
      Selecione uma area a esquerda, ou toque <b>+ Nova area</b> para pintar uma nova.
    </div>
    <div id="mapa_planos" style="display:none;flex:1;background:#0a0e13;margin:14px;
         border-radius:12px;border:1px solid var(--borda)"></div>
    <div id="resumo_plano"></div>
    <div id="acoes_plano">
      <button class="btn primario" id="btn_ativar_plano" style="display:none">Ativar para o medidor</button>
      <button class="btn" id="btn_editar_plano" style="display:none">Editar desenho</button>
      <button class="btn perigo" id="btn_apagar_plano" style="display:none">Apagar area</button>
    </div>
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

// --- Mapa (Leaflet + tiles do OpenStreetMap, buscados pelo NAVEGADOR de quem
// abre o painel - nao pelo Pi, que so serve esta pagina). Igual ao mapa do
// app, para o pesquisador ver a mesma coisa nos dois lugares.
let mapa = null, trilha = null;
let marcadores = [];

function iniciarMapa(centro){
  mapa = L.map('mapa', {zoomControl:true}).setView(centro, 17);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  }).addTo(mapa);
  trilha = L.polyline([], {color:'#3fb950', weight:4, opacity:.85}).addTo(mapa);
}

// Sequencial azul->verde->amarelo->vermelho para t em [0,1].
function corTemp(t){
  t = Math.max(0, Math.min(1, t));
  const paradas = [[88,166,255],[63,185,80],[210,153,34],[248,81,73]];
  const f = t*(paradas.length-1); const i = Math.floor(f); const k = f-i;
  const a = paradas[i], b = paradas[Math.min(i+1,paradas.length-1)];
  return `rgb(${a[0]+(b[0]-a[0])*k|0},${a[1]+(b[1]-a[1])*k|0},${a[2]+(b[2]-a[2])*k|0})`;
}

function desenhar(){
  if(!traj || !traj.cru.length){
    if(mapa){ mapa.remove(); mapa=null; }
    $('#mapa').innerHTML = '';
    $('#escala').style.display = 'none';
    const p = document.createElement('div');
    p.style.cssText = 'display:flex;align-items:center;justify-content:center;height:100%;color:#8b949e;font-size:13px';
    p.textContent = 'Esta coleta nao tem pontos de posicao.';
    $('#mapa').appendChild(p);
    return;
  }

  const pontos = (traj.pontos&&traj.pontos.length) ? traj.pontos
               : traj.cru.map(([la,lo])=>({lat:la,lon:lo,temp:null,umid:null,vel:null}));
  const linhaPts = (traj.kf&&traj.kf.length>1 ? traj.kf : traj.cru).map(([la,lo])=>[la,lo]);

  if(!mapa){
    iniciarMapa([pontos[0].lat, pontos[0].lon]);
  } else {
    marcadores.forEach(m => mapa.removeLayer(m));
  }
  marcadores = [];
  trilha.setLatLngs(linhaPts);

  const r = traj.resumo||{};
  const tmin = r.temp_min, tmax = r.temp_max;
  const faixa = (tmax!=null && tmax>tmin) ? tmax-tmin : 1;

  for(const p of pontos){
    const cor = p.temp==null ? '#7d8794' : corTemp((p.temp-tmin)/faixa);
    const marcador = L.circleMarker([p.lat, p.lon], {
      radius: 6, className: 'pontotemp', color: 'rgba(13,17,23,.85)',
      weight: 1.5, fillColor: cor, fillOpacity: .95,
    }).addTo(mapa);
    marcador.bindPopup(
      `<b>${p.temp!=null?p.temp.toFixed(1)+' °C':'temp --'}</b>`
      + `  ·  ${p.umid!=null?p.umid.toFixed(0)+' %':'--'}<br>`
      + `${(p.vel||0).toFixed(1)} km/h`);
    marcadores.push(marcador);
  }
  // Marcadores de inicio/fim, por cima dos pontos de temperatura.
  const ini = L.circleMarker([pontos[0].lat, pontos[0].lon],
    {radius:9, color:'#fff', weight:2, fillColor:'#58a6ff', fillOpacity:1}).addTo(mapa);
  ini.bindTooltip('inicio', {permanent:false});
  const fim = L.circleMarker([pontos[pontos.length-1].lat, pontos[pontos.length-1].lon],
    {radius:9, color:'#fff', weight:2, fillColor:'#f85149', fillOpacity:1}).addTo(mapa);
  fim.bindTooltip('fim', {permanent:false});
  marcadores.push(ini, fim);

  mapa.invalidateSize();
  mapa.fitBounds(L.latLngBounds(linhaPts.length>1?linhaPts:pontos.map(p=>[p.lat,p.lon])),
    {padding:[30,30], maxZoom:19});

  const esc2=$('#escala');
  if(tmin!=null){ esc2.style.display='block';
    $('#esc_max').textContent=tmax.toFixed(1); $('#esc_min').textContent=tmin.toFixed(1);
  } else esc2.style.display='none';
}

window.addEventListener('resize', () => { if(mapa) mapa.invalidateSize(); if(mapaP) mapaP.invalidateSize(); });
setInterval(()=>{ $('#relogio').textContent = new Date().toLocaleTimeString('pt-BR'); }, 1000);
carregarSessoes();
setInterval(carregarSessoes, 5000);

// =============================================================================
// PLANEJAMENTO: PINTAR uma area no mapa (pincel) e salvar - so isso, o
// PRE-processamento. Nao gera nem mostra nenhum ponto-alvo aqui: o usuario
// sai e mede do jeito que sempre mediu, sem nenhum passo especial. Quando
// esta tela reabre um plano, o POS-processamento roda na hora (GET
// /plano/pontos) - cruza a area contra as amostras que ja existem no banco
// (planejamento.pontos_do_plano) e monta o Voronoi com o raio de
// abrangencia que o usuario escolher NAQUELE MOMENTO, nao gravado.
//
// Da pra ter VARIOS planos por medidor; "Ativar para o medidor" so marca
// qual deles fica disponivel para aquele `dev` (o app busca isso em
// GET /plano/ativo?dev= - fase seguinte, fora desta rodada, mas o dado ja
// fica pronto).
// =============================================================================
let ultimoDev = 'esp32-01';

document.querySelectorAll('.aba').forEach(b => b.onclick = () => mudarAba(b.dataset.aba));
function mudarAba(aba){
  document.querySelectorAll('.aba').forEach(b => b.classList.toggle('on', b.dataset.aba===aba));
  $('#corpo').style.display = aba==='sessoes' ? 'flex' : 'none';
  $('#corpo_planos').style.display = aba==='planos' ? 'flex' : 'none';
  if(aba==='planos'){
    if(mapaP) mapaP.invalidateSize();
    carregarPlanos();
  }
}

let mapaP=null, camadaPintura=null, camadaPontos=null, cursorPincel=null;
let pintando=false, arrastando=false, tracoAtual=null, tracosNovo=[];
let planoSelId=null, planoSel=null, ultimaAmostra=null;
let modo='pintar';  // 'pintar' ou 'apagar'

function iniciarMapaPlanos(centro){
  mapaP = L.map('mapa_planos', {zoomControl:true}).setView(centro, 17);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; OpenStreetMap'
  }).addTo(mapaP);

  // Pane propria para a pintura, entre os tiles (z~200) e os overlays normais
  // (z~400, onde ficam os pontos calculados). A transparencia vai NELA, nao
  // em cada disco: os discos sao pintados OPACOS (ver aplicarPincel) e so a
  // pane inteira fica translucida por cima do mapa. Se a transparencia fosse
  // por disco, dois discos sobrepostos ficariam mais escuros que um so - a
  // pincelada sairia com manchas mais fortes onde o gesto se cruzou, o
  // oposto de "tinta continua".
  mapaP.createPane('pinturaPane');
  mapaP.getPane('pinturaPane').style.zIndex = 350;
  mapaP.getPane('pinturaPane').style.opacity = 0.45;
  mapaP.getPane('pinturaPane').style.pointerEvents = 'none';

  // Pane separada para o desenho de um plano JA SALVO (desenharPlano) -
  // opacidade 100% (mancha bem solida, nao um verde lavado), mas com
  // mix-blend-mode:multiply em vez de so opacity: opacity sozinha clareia
  // tudo por baixo (ruas, predios, rotulos somem debaixo do verde); multiply
  // MULTIPLICA a cor por cima do que ja esta no tile, entao o traco escuro
  // de uma rua ou contorno de predio continua aparecendo, so tingido de
  // verde - a area fica evidente sem apagar o mapa por baixo.
  mapaP.createPane('planoPane');
  mapaP.getPane('planoPane').style.zIndex = 350;
  mapaP.getPane('planoPane').style.opacity = 1;
  mapaP.getPane('planoPane').style.mixBlendMode = 'multiply';
  mapaP.getPane('planoPane').style.pointerEvents = 'none';

  camadaPintura = L.layerGroup().addTo(mapaP);
  camadaPontos = L.layerGroup().addTo(mapaP);

  // So o botao ESQUERDO pinta/apaga. Qualquer outro botao (o do meio, ex.)
  // nao mexe em `dragging` e cai no comportamento padrao do Leaflet - que e
  // mover o mapa. E assim que da pra navegar sem sair do modo de desenho.
  mapaP.on('mousedown', ev => {
    if(!pintando || ev.originalEvent.button !== 0) return;
    arrastando = true; ultimaAmostra = null;
    mapaP.dragging.disable();
    if(modo==='pintar') tracoAtual = {raio_m: Number($('#raio_pincel').value), pontos:[]};
    aplicarPincel(ev.latlng);
  });
  mapaP.on('mousemove', ev => {
    moverCursorPincel(ev.latlng);
    if(pintando && arrastando) aplicarPincel(ev.latlng);
  });
  const soltarPincel = () => {
    if(!pintando || !arrastando) return;
    arrastando = false;
    mapaP.dragging.enable();
    if(modo==='pintar' && tracoAtual && tracoAtual.pontos.length) tracosNovo.push(tracoAtual);
    tracoAtual = null;
  };
  mapaP.on('mouseup', soltarPincel);
  // Rede de seguranca: se o botao for solto fora do mapa, o mouseup do
  // Leaflet as vezes nao dispara - sem isto a pincelada ficaria "presa".
  window.addEventListener('mouseup', soltarPincel);
  mapaP.on('mouseout', () => { if(cursorPincel) mapaP.removeLayer(cursorPincel); cursorPincel=null; });
}

// Circulo fantasma seguindo o cursor, do tamanho real do pincel - para o
// usuario ver ANTES de clicar o quanto ele vai pintar (ou apagar).
function moverCursorPincel(latlng){
  if(!pintando) return;
  const raio = Number($('#raio_pincel').value);
  const cor = modo==='apagar' ? '#f85149' : '#3fb950';
  if(!cursorPincel){
    cursorPincel = L.circle(latlng, {radius:raio, color:cor, weight:2, dashArray:'4,4',
      fill:false, interactive:false}).addTo(mapaP);
  } else {
    cursorPincel.setLatLng(latlng).setRadius(raio).setStyle({color:cor});
  }
}

function aplicarPincel(latlng){
  const raio = Number($('#raio_pincel').value);
  // Amostra mais junta que antes (raio*0.28 em vez de *0.5): com o circulo
  // SEM borda, sao os discos se sobrepondo bastante que dao a leitura de
  // tinta continua - amostra rala deixava "furos" entre as bolinhas.
  if(ultimaAmostra && mapaP.distance(ultimaAmostra, latlng) < raio*0.28) return;
  ultimaAmostra = latlng;
  if(modo==='pintar'){
    tracoAtual.pontos.push([latlng.lat, latlng.lng]);
    // Disco OPACO (fillOpacity:1) e sem contorno, na pinturaPane - a
    // transparencia e da pane inteira (ver iniciarMapaPlanos), entao discos
    // que se sobrepoem continuam com a MESMA cor solida, sem escurecer onde
    // o gesto se cruzou. E o que da a leitura de tinta continua.
    L.circle(latlng, {radius:raio, weight:0, stroke:false, pane:'pinturaPane',
      fillColor:'#3fb950', fillOpacity:1}).addTo(camadaPintura);
  } else {
    apagarPerto(latlng, raio);
  }
}

// Borracha: remove, das pinceladas AINDA NAO SALVAS desta sessao de edicao,
// qualquer amostra dentro do raio do cursor. So opera em tracosNovo - editar
// uma area ja salva e outra funcionalidade, fora desta rodada.
function apagarPerto(latlng, raio){
  let mudou = false;
  tracosNovo = tracosNovo
    .map(t => {
      const pontos = t.pontos.filter(([lat,lon]) => {
        const manter = mapaP.distance(latlng, [lat,lon]) > raio;
        if(!manter) mudou = true;
        return manter;
      });
      return {raio_m: t.raio_m, pontos};
    })
    .filter(t => t.pontos.length > 0);
  if(mudou) redesenharPintura();
}

// Redesenha a camada de pintura do zero, cada pincelada com o RAIO QUE ELA
// TINHA quando foi pintada - mexer no slider depois nao muda o que ja esta
// no mapa, so vale para a proxima pincelada.
function redesenharPintura(){
  camadaPintura.clearLayers();
  for(const t of tracosNovo) for(const [lat,lon] of t.pontos)
    L.circle([lat,lon], {radius:t.raio_m, weight:0, stroke:false, pane:'pinturaPane',
      fillColor:'#3fb950', fillOpacity:1}).addTo(camadaPintura);
}

$('#raio_pincel').addEventListener('input', () => {
  $('#raio_val').textContent = $('#raio_pincel').value + ' m';
  // O circulo fantasma acompanha o slider na hora, mesmo sem mover o mouse.
  if(cursorPincel) cursorPincel.setRadius(Number($('#raio_pincel').value));
});

function setModo(m){
  modo = m;
  $('#btn_modo_pintar').classList.toggle('primario', m==='pintar');
  $('#btn_modo_apagar').classList.toggle('primario', m==='apagar');
}
$('#btn_modo_pintar').onclick = () => setModo('pintar');
$('#btn_modo_apagar').onclick = () => setModo('apagar');

// `editandoId`: null quando pintando uma area NOVA; o id do plano quando
// "Editar desenho" foi tocado num plano ja salvo - e o que faz "Salvar"
// decidir entre criar (POST /plano) ou atualizar (POST /plano/atualizar)
// o desenho por cima do que ja existia, sem virar um plano duplicado.
let editandoId = null;

$('#btn_novo_plano').onclick = () => {
  planoSelId = null; planoSel = null; editandoId = null;
  tracosNovo = []; tracoAtual = null;
  setModo('pintar');
  $('#placeholder_planos').style.display = 'none';
  $('#mapa_planos').style.display = 'block';
  if(!mapaP) iniciarMapaPlanos(ultimoCentro());
  mapaP.invalidateSize();
  camadaPintura.clearLayers(); camadaPontos.clearLayers();
  pintando = true;
  $('#dev_plano').value = ultimoDev;
  $('#barra_pincel').style.display = 'flex';
  $('#resumo_plano').style.display = 'none';
  $('#btn_apagar_plano').style.display = 'none';
  $('#btn_ativar_plano').style.display = 'none';
  $('#btn_editar_plano').style.display = 'none';
  document.querySelectorAll('#planos .sessao').forEach(el => el.classList.remove('sel'));
};

$('#btn_editar_plano').onclick = () => {
  if(!planoSelId || !planoSel) return;
  editandoId = planoSelId;
  // Clona os tracos existentes para tracosNovo - dá pra continuar pintando
  // ou apagar pedacos com a borracha, igual numa area nova.
  tracosNovo = JSON.parse(JSON.stringify(planoSel.tracos || []));
  tracoAtual = null;
  setModo('pintar');
  redesenharPintura();
  camadaPontos.clearLayers();
  pintando = true;
  $('#dev_plano').value = planoSel.dev;
  $('#barra_pincel').style.display = 'flex';
};

// Desfazer so cobre PINCELADAS (o que a borracha tira nao entra num
// historico - "Limpar" e o reset confiavel se a borracha passar do ponto).
$('#btn_desfazer').onclick = () => { tracosNovo.pop(); redesenharPintura(); };
$('#btn_limpar').onclick = () => { tracosNovo=[]; redesenharPintura(); };
$('#btn_cancelar_plano').onclick = () => {
  pintando = false;
  editandoId = null;
  $('#barra_pincel').style.display = 'none';
  if(cursorPincel){ mapaP.removeLayer(cursorPincel); cursorPincel=null; }
  camadaPintura.clearLayers(); camadaPontos.clearLayers();
  if(planoSelId) selecionarPlano(planoSelId);
  else { $('#mapa_planos').style.display='none'; $('#placeholder_planos').style.display='flex'; }
};
$('#btn_salvar_plano').onclick = async () => {
  if(!tracosNovo.length){ alert('Pinte pelo menos uma area antes de salvar.'); return; }
  let r;
  if(editandoId){
    r = await (await fetch('/plano/atualizar', {method:'POST',
      body: JSON.stringify({id: editandoId, tracos:tracosNovo})})).json();
  } else {
    const nome = prompt('Nome desta area:');
    if(!nome) return;
    const dev = $('#dev_plano').value.trim() || 'esp32-01';
    r = await (await fetch('/plano', {method:'POST',
      body: JSON.stringify({nome, dev, tracos:tracosNovo})})).json();
  }
  if(!r.ok){ alert('Erro ao salvar: ' + r.erro); return; }
  const idFinal = editandoId || r.id;
  pintando = false;
  editandoId = null;
  $('#barra_pincel').style.display = 'none';
  if(cursorPincel){ mapaP.removeLayer(cursorPincel); cursorPincel=null; }
  await carregarPlanos();
  selecionarPlano(idFinal);
};
$('#btn_apagar_plano').onclick = async () => {
  if(!planoSelId) return;
  if(!confirm('Apagar esta area planejada?')) return;
  await fetch('/plano/apagar', {method:'POST', body: JSON.stringify({id: planoSelId})});
  planoSelId = null; planoSel = null;
  camadaPontos.clearLayers(); camadaPintura.clearLayers();
  $('#resumo_plano').style.display = 'none';
  $('#btn_apagar_plano').style.display = 'none';
  $('#btn_ativar_plano').style.display = 'none';
  $('#btn_editar_plano').style.display = 'none';
  $('#mapa_planos').style.display = 'none';
  $('#placeholder_planos').style.display = 'flex';
  carregarPlanos();
};
$('#btn_ativar_plano').onclick = async () => {
  if(!planoSelId) return;
  if(planoSel.ativo){
    await fetch('/plano/desativar', {method:'POST', body: JSON.stringify({id: planoSelId})});
  } else {
    await fetch('/plano/ativar', {method:'POST', body: JSON.stringify({id: planoSelId})});
  }
  planoSel = await (await fetch('/plano?id='+planoSelId, {cache:'no-store'})).json();
  atualizarBotaoAtivar();
  carregarPlanos();
};
function atualizarBotaoAtivar(){
  const btn = $('#btn_ativar_plano');
  btn.textContent = planoSel.ativo ? 'Ativo para o medidor ✓' : 'Ativar para o medidor';
  btn.classList.toggle('primario', !planoSel.ativo);
}

function ultimoCentro(){
  if(planoSel && planoSel.tracos && planoSel.tracos.length && planoSel.tracos[0].pontos.length)
    return planoSel.tracos[0].pontos[0];
  if(traj && traj.cru && traj.cru.length) return traj.cru[0];
  return [-21.2257, -44.9774];
}

async function carregarPlanos(){
  let r;
  try{ r = await (await fetch('/planos', {cache:'no-store'})).json(); }
  catch(e){ return; }
  if(r.length) ultimoDev = r[0].dev;
  $('#vazio_planos').style.display = r.length ? 'none' : 'block';
  const box = $('#planos');
  box.innerHTML = r.map(p => `
    <div class="sessao ${p.id===planoSelId?'sel':''}" data-id="${p.id}">
      <div class="nome">${p.nome} ${p.ativo?'<span style="color:var(--verde)">&#9679; ativo</span>':''}</div>
      <div class="sub">${p.dev} &middot; ${fmtData(p.criado_em)}</div>
    </div>`).join('');
  box.querySelectorAll('.sessao').forEach(el =>
    el.onclick = () => selecionarPlano(Number(el.dataset.id)));
}

async function selecionarPlano(id){
  pintando = false;
  $('#barra_pincel').style.display = 'none';
  planoSelId = id;
  document.querySelectorAll('#planos .sessao').forEach(el =>
    el.classList.toggle('sel', Number(el.dataset.id)===id));
  planoSel = await (await fetch('/plano?id='+id, {cache:'no-store'})).json();
  desenharPlano();
}

function desenharPlano(){
  if(!planoSel || !planoSel.tracos) return;
  $('#placeholder_planos').style.display = 'none';
  $('#mapa_planos').style.display = 'block';
  if(!mapaP) iniciarMapaPlanos(ultimoCentro());
  mapaP.invalidateSize();

  camadaPintura.clearLayers();

  // A area pintada - e so isso que o plano guarda nesta fase (so
  // planejamento de rota/area, sem nada de cobertura/Voronoi ainda - isso
  // fica para depois, quando decidirmos o tamanho da area de abrangencia).
  // Opaca (fillOpacity:1) na planoPane (tambem opacidade 1, ver
  // iniciarMapaPlanos) para ficar uma mancha solida e bem visivel, nao um
  // fundo apagado.
  const todosLatLng = [];
  for(const t of planoSel.tracos)
    for(const [lat,lon] of t.pontos){
      todosLatLng.push([lat,lon]);
      L.circle([lat,lon], {radius: t.raio_m, weight:0, stroke:false,
        pane:'planoPane', fillColor:'#3fb950', fillOpacity:1}).addTo(camadaPintura);
    }
  if(todosLatLng.length)
    mapaP.fitBounds(L.latLngBounds(todosLatLng), {padding:[30,30], maxZoom:19});

  $('#resumo_plano').style.display = 'block';
  $('#resumo_plano').innerHTML = `<b>${planoSel.nome}</b> &mdash; ${planoSel.dev}`;
  $('#btn_apagar_plano').style.display = 'inline-block';
  $('#btn_ativar_plano').style.display = 'inline-block';
  $('#btn_editar_plano').style.display = 'inline-block';
  atualizarBotaoAtivar();
}
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
        if self.path == "/plano":
            self._post_plano()
            return
        if self.path == "/plano/atualizar":
            self._post_plano_atualizar()
            return
        if self.path == "/plano/apagar":
            self._post_plano_apagar()
            return
        if self.path == "/plano/ativar":
            self._post_plano_ativar()
            return
        if self.path == "/plano/desativar":
            self._post_plano_desativar()
            return
        if self.path != "/sync":
            self.send_error(404)
            return

        # Recepcao das coletas do celular. Manifesto por nome de arquivo: se a
        # sessao ja chegou, nao regrava (evita duplicata quando o envio repete).
        dev = self.headers.get("X-Dev", "desconhecido")
        sessao = os.path.basename(self.headers.get("X-Sessao", ""))
        if not sessao or "/" in sessao or "\\" in sessao:
            self._json(400, {"ok": False, "erro": "cabecalho X-Sessao invalido"})
            return
        try:
            corpo = _ler_corpo(self)
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
        # salvo, a sessao pode ser reprocessada depois. Planos NAO entram
        # aqui mais - o cruzamento com a area pintada e pos-processamento sob
        # demanda (planejamento.pontos_do_plano), nao um passo do /sync.
        n_deriv, erro = 0, None
        try:
            n_deriv = processar_sessao(destino, _caminho_banco())
            print(f"sync: processada {sessao} - {n_deriv} pontos no banco")
        except Exception as e:      # noqa: BLE001 - custodia acima de tudo
            erro = str(e)
            print(f"sync: cru salvo, processamento falhou: {e}")

        self._json(200, {"ok": True, "ja_tinha": False, "linhas": linhas,
                         "derivados": n_deriv, "erro_processamento": erro})

    def _corpo_json(self):
        corpo = _ler_corpo(self)
        return json.loads(corpo.decode("utf-8")) if corpo else {}

    def _post_plano(self):
        try:
            j = self._corpo_json()
        except (ValueError, OSError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "erro": f"corpo invalido: {e}"})
            return
        nome = str(j.get("nome") or "").strip()
        dev = str(j.get("dev") or "").strip()
        tracos = j.get("tracos")
        if not nome or not dev:
            self._json(400, {"ok": False, "erro": "esperado {nome, dev, tracos}"})
            return
        erro = _validar_tracos(tracos)
        if erro:
            self._json(400, {"ok": False, "erro": erro})
            return
        plano_id = planejamento.criar_plano(_caminho_banco(), nome, dev, tracos)
        p = planejamento.obter_plano(_caminho_banco(), plano_id)
        print(f"\nplano: criado '{nome}' ({dev})")
        self._json(200, {"ok": True, "id": plano_id, "plano": p})

    def _post_plano_atualizar(self):
        """Reedita o desenho de um plano ja salvo (substitui os tracos) -
        nome/dev/ativo continuam os mesmos, so a area pintada muda."""
        try:
            j = self._corpo_json()
            plano_id = int(j.get("id"))
        except (TypeError, ValueError, OSError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "erro": f"esperado {{id, tracos}}: {e}"})
            return
        tracos = j.get("tracos")
        erro = _validar_tracos(tracos)
        if erro:
            self._json(400, {"ok": False, "erro": erro})
            return
        ok = planejamento.atualizar_plano(_caminho_banco(), plano_id, tracos)
        if not ok:
            self._json(404, {"ok": False, "erro": "plano nao encontrado"})
            return
        p = planejamento.obter_plano(_caminho_banco(), plano_id)
        self._json(200, {"ok": True, "plano": p})

    def _post_plano_apagar(self):
        try:
            j = self._corpo_json()
        except (ValueError, OSError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "erro": f"corpo invalido: {e}"})
            return
        try:
            plano_id = int(j.get("id"))
        except (TypeError, ValueError):
            self._json(400, {"ok": False, "erro": "esperado {id}"})
            return
        planejamento.apagar_plano(_caminho_banco(), plano_id)
        self._json(200, {"ok": True})

    def _post_plano_ativar(self):
        """Marca um plano como o ativo do seu medidor - so guarda o dado.
        Quem vai LER isto (GET /plano/ativo?dev=) e o app, fase seguinte;
        nesta rodada so deixamos a base pronta para quando isso acontecer."""
        try:
            j = self._corpo_json()
            plano_id = int(j.get("id"))
        except (TypeError, ValueError, OSError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "erro": f"esperado {{id}}: {e}"})
            return
        ok = planejamento.ativar_plano(_caminho_banco(), plano_id)
        if not ok:
            self._json(404, {"ok": False, "erro": "plano nao encontrado"})
            return
        self._json(200, {"ok": True})

    def _post_plano_desativar(self):
        try:
            j = self._corpo_json()
            plano_id = int(j.get("id"))
        except (TypeError, ValueError, OSError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "erro": f"esperado {{id}}: {e}"})
            return
        planejamento.desativar_plano(_caminho_banco(), plano_id)
        self._json(200, {"ok": True})

    def do_GET(self):
        if self.path == "/":
            corpo, tipo = PAGINA.encode(), "text/html; charset=utf-8"
        elif self.path.startswith("/sessoes"):
            corpo, tipo = json.dumps(_listar_sessoes()).encode(), "application/json"
        elif self.path.startswith("/sessao"):
            q = parse_qs(urlparse(self.path).query)
            nome = (q.get("nome") or [""])[0]
            corpo, tipo = json.dumps(_trajeto_sessao(nome)).encode(), "application/json"
        elif self.path.startswith("/planos"):
            corpo = json.dumps(
                planejamento.listar_planos(_caminho_banco())).encode()
            tipo = "application/json"
        elif self.path.startswith("/plano/ativo"):
            # Endpoint pronto para o APP consumir (fase seguinte, fora desta
            # rodada) - devolve o plano ativo daquele medidor, ou {} se
            # nenhum plano estiver marcado ativo para ele.
            q = parse_qs(urlparse(self.path).query)
            dev = (q.get("dev") or [""])[0]
            p = planejamento.plano_ativo(_caminho_banco(), dev)
            corpo = json.dumps(p if p else {}).encode()
            tipo = "application/json"
        elif self.path.startswith("/plano/pontos"):
            # POS-processamento sob demanda: cruza a area do plano contra as
            # amostras que ja existem no banco, na hora - nada pre-calculado
            # (ver planejamento.pontos_do_plano).
            q = parse_qs(urlparse(self.path).query)
            try:
                plano_id = int((q.get("id") or ["0"])[0])
            except ValueError:
                plano_id = 0
            corpo = json.dumps(
                planejamento.pontos_do_plano(_caminho_banco(), plano_id)).encode()
            tipo = "application/json"
        elif self.path.startswith("/plano"):
            q = parse_qs(urlparse(self.path).query)
            try:
                plano_id = int((q.get("id") or ["0"])[0])
            except ValueError:
                plano_id = 0
            p = planejamento.obter_plano(_caminho_banco(), plano_id)
            corpo = json.dumps(p if p else {}).encode()
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

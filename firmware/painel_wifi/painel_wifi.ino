/*
 * Painel Wi-Fi da estacao - ESP32 como ponto de acesso.
 *
 * O ESP32 cria a propria rede Wi-Fi (nao precisa de roteador nem internet).
 * Qualquer celular que conectar nela e abrir o navegador ve temperatura e
 * umidade ao vivo, com grafico dos ultimos minutos.
 *
 *   rede:  EstacaoMeteorologica
 *   senha: estacao2026
 *   pagina: http://192.168.4.1
 *
 * Ha um servidor DNS respondendo tudo para 192.168.4.1, entao na maioria dos
 * celulares a pagina abre sozinha ao conectar (aquele aviso de "faca login na
 * rede"). Se nao abrir, e so digitar o endereco no navegador.
 *
 * Sensor: transmissor RS485 de temperatura e umidade, mapa confirmado na
 * bancada (ver ../README.md):
 *      endereco Modbus 1, 9600 8N1, funcao 0x03
 *      0x0000  temperatura  /10  C (int16 com sinal)
 *      0x0001  umidade      /10  %RH
 *
 * Ligacao: igual aos outros sketches - RO->GPIO16, DI->GPIO17, DE+RE->GPIO4.
 */

#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>

// ---------------------------------------------------------------------------
// Configuracao
// ---------------------------------------------------------------------------
static const char *SSID_AP  = "EstacaoMeteorologica";
static const char *SENHA_AP = "estacao2026";   // minimo 8 caracteres
static const uint8_t MAX_CLIENTES = 8;

static const int8_t PINO_RX = 16;   // RO do transceptor
static const int8_t PINO_TX = 17;   // DI do transceptor
static const int8_t PINO_DE = 4;    // DE+RE; -1 se o modulo for automatico

static const uint32_t BAUD_SENSOR = 9600;
static const uint8_t  ENDERECO     = 1;
static const uint8_t  REG_TEMP     = 0x00;
static const uint8_t  REG_UMID     = 0x01;

static const uint16_t INTERVALO_LEITURA_MS = 2000;
static const uint16_t TIMEOUT_MS = 200;
static const uint16_t GAP_MS     = 10;

// ---------------------------------------------------------------------------
// Estado compartilhado entre a leitura e o servidor
// ---------------------------------------------------------------------------
float    temperatura   = 0.0f;
float    umidade       = 0.0f;
bool     leituraValida = false;
uint32_t ultimaLeitura = 0;   // millis() da ultima leitura boa
uint32_t totalLeituras = 0;
uint32_t totalFalhas   = 0;

IPAddress ipAP(192, 168, 4, 1);
WebServer servidor(80);
DNSServer dns;

// ---------------------------------------------------------------------------
// Modbus RTU
// ---------------------------------------------------------------------------
uint16_t crc16(const uint8_t *dados, size_t tamanho) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < tamanho; i++) {
    crc ^= dados[i];
    for (uint8_t bit = 0; bit < 8; bit++) {
      if (crc & 0x0001) crc = (crc >> 1) ^ 0xA001;
      else              crc >>= 1;
    }
  }
  return crc;
}

bool lerRegistradores(uint8_t reg, uint8_t quantidade, uint16_t *destino) {
  uint8_t pedido[8] = {ENDERECO, 0x03, 0x00, reg, 0x00, quantidade, 0x00, 0x00};
  uint16_t crc = crc16(pedido, 6);
  pedido[6] = crc & 0xFF;
  pedido[7] = (crc >> 8) & 0xFF;

  while (Serial2.available()) Serial2.read();

  if (PINO_DE >= 0) digitalWrite(PINO_DE, HIGH);
  Serial2.write(pedido, sizeof(pedido));
  Serial2.flush();
  delayMicroseconds(300);
  if (PINO_DE >= 0) digitalWrite(PINO_DE, LOW);

  uint8_t resp[32];
  size_t n = 0;
  uint32_t limite = millis() + TIMEOUT_MS;
  uint32_t ultimo = 0;
  while (millis() < limite) {
    if (Serial2.available()) {
      if (n < sizeof(resp)) resp[n++] = (uint8_t)Serial2.read();
      else                  Serial2.read();
      ultimo = millis();
    } else if (n > 0 && millis() - ultimo > GAP_MS) {
      break;
    }
  }

  if (n < 5) return false;
  uint16_t crcRecebido = ((uint16_t)resp[n - 1] << 8) | resp[n - 2];
  if (crcRecebido != crc16(resp, n - 2)) return false;
  if (resp[1] != 0x03 || resp[2] != 2 * quantidade) return false;

  for (uint8_t i = 0; i < quantidade; i++) {
    destino[i] = ((uint16_t)resp[3 + 2 * i] << 8) | resp[4 + 2 * i];
  }
  return true;
}

void lerSensor() {
  uint16_t v[2];
  if (lerRegistradores(REG_TEMP, 2, v)) {
    temperatura   = (int16_t)v[REG_TEMP] / 10.0f;
    umidade       = v[REG_UMID] / 10.0f;
    leituraValida = true;
    ultimaLeitura = millis();
    totalLeituras++;
  } else {
    leituraValida = false;
    totalFalhas++;
  }
}

// ---------------------------------------------------------------------------
// Pagina
// ---------------------------------------------------------------------------
static const char PAGINA[] PROGMEM = R"HTML(<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Estacao Meteorologica</title>
<style>
  :root {
    --fundo: #f4f5f7; --cartao: #fff; --texto: #16181d; --fraco: #6b7280;
    --borda: #e3e5e9; --temp: #e0533d; --umid: #2f7fd1;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --fundo: #111317; --cartao: #1a1d23; --texto: #f0f2f5; --fraco: #9099a8;
      --borda: #2a2e36; --temp: #ff7a5e; --umid: #63aef5;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 20px 16px 40px;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    background: var(--fundo); color: var(--texto);
  }
  .env { max-width: 560px; margin: 0 auto; }
  header { display: flex; align-items: baseline; justify-content: space-between;
           gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }
  h1 { font-size: 1.05rem; font-weight: 600; margin: 0; letter-spacing: .01em; }
  .estado { font-size: .8rem; color: var(--fraco); display: flex;
            align-items: center; gap: 6px; }
  .bolinha { width: 8px; height: 8px; border-radius: 50%; background: #35c46a; }
  .bolinha.ruim { background: #e04b4b; }
  .grade { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  @media (max-width: 380px) { .grade { grid-template-columns: 1fr; } }
  .cartao { background: var(--cartao); border: 1px solid var(--borda);
            border-radius: 14px; padding: 16px 18px; }
  .rotulo { font-size: .78rem; text-transform: uppercase; letter-spacing: .06em;
            color: var(--fraco); margin-bottom: 6px; }
  .valor { font-size: 2.6rem; font-weight: 650; line-height: 1;
           font-variant-numeric: tabular-nums; }
  .valor span { font-size: 1.1rem; font-weight: 500; color: var(--fraco);
                margin-left: 3px; }
  #vt { color: var(--temp); } #vu { color: var(--umid); }
  .extremos { font-size: .75rem; color: var(--fraco); margin-top: 8px;
              font-variant-numeric: tabular-nums; }
  .grafico { margin-top: 12px; }
  canvas { width: 100%; height: 140px; display: block; }
  .legenda { display: flex; gap: 16px; font-size: .75rem; color: var(--fraco);
             margin-top: 6px; }
  .legenda i { display: inline-block; width: 10px; height: 3px;
               border-radius: 2px; margin-right: 5px; vertical-align: middle; }
  footer { margin-top: 20px; font-size: .72rem; color: var(--fraco);
           text-align: center; line-height: 1.6; }
</style>
</head>
<body>
<div class="env">
  <header>
    <h1>Estacao Meteorologica</h1>
    <div class="estado"><span class="bolinha" id="bolinha"></span><span id="estado">conectando</span></div>
  </header>

  <div class="grade">
    <div class="cartao">
      <div class="rotulo">Temperatura</div>
      <div class="valor" id="vt">--<span>&deg;C</span></div>
      <div class="extremos" id="et">&nbsp;</div>
    </div>
    <div class="cartao">
      <div class="rotulo">Umidade</div>
      <div class="valor" id="vu">--<span>%</span></div>
      <div class="extremos" id="eu">&nbsp;</div>
    </div>
  </div>

  <div class="cartao grafico">
    <div class="rotulo">Ultimos minutos</div>
    <canvas id="tela"></canvas>
    <div class="legenda">
      <span><i style="background:var(--temp)"></i>temperatura</span>
      <span><i style="background:var(--umid)"></i>umidade</span>
    </div>
  </div>

  <footer>
    <span id="rodape">&nbsp;</span>
  </footer>
</div>

<script>
const MAX_PONTOS = 180;      // 180 x 2 s = 6 minutos
let serieT = [], serieU = [];
let minT = null, maxT = null, minU = null, maxU = null;

function fmt(v, casas) { return v.toFixed(casas); }

function desenhar() {
  const tela = document.getElementById('tela');
  const r = tela.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  tela.width = r.width * dpr;
  tela.height = r.height * dpr;
  const c = tela.getContext('2d');
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, r.width, r.height);
  if (serieT.length < 2) return;

  const estilo = getComputedStyle(document.documentElement);
  const cores = [estilo.getPropertyValue('--temp'), estilo.getPropertyValue('--umid')];
  const pad = 6;

  [serieT, serieU].forEach((serie, i) => {
    let lo = Math.min(...serie), hi = Math.max(...serie);
    if (hi - lo < 0.5) { const m = (hi + lo) / 2; lo = m - 0.25; hi = m + 0.25; }
    const passo = (r.width - pad * 2) / (MAX_PONTOS - 1);
    c.beginPath();
    serie.forEach((v, k) => {
      const x = pad + k * passo;
      const y = pad + (r.height - pad * 2) * (1 - (v - lo) / (hi - lo));
      k ? c.lineTo(x, y) : c.moveTo(x, y);
    });
    c.strokeStyle = cores[i].trim();
    c.lineWidth = 2;
    c.lineJoin = 'round';
    c.stroke();
  });
}

function estado(bom, texto) {
  document.getElementById('bolinha').className = 'bolinha' + (bom ? '' : ' ruim');
  document.getElementById('estado').textContent = texto;
}

async function atualizar() {
  try {
    const r = await fetch('/dados', { cache: 'no-store' });
    const d = await r.json();
    if (!d.ok) { estado(false, 'sensor sem resposta'); return; }

    document.getElementById('vt').innerHTML = fmt(d.t, 1) + '<span>&deg;C</span>';
    document.getElementById('vu').innerHTML = fmt(d.ur, 1) + '<span>%</span>';

    minT = minT === null ? d.t : Math.min(minT, d.t);
    maxT = maxT === null ? d.t : Math.max(maxT, d.t);
    minU = minU === null ? d.ur : Math.min(minU, d.ur);
    maxU = maxU === null ? d.ur : Math.max(maxU, d.ur);
    document.getElementById('et').textContent = 'min ' + fmt(minT, 1) + '  max ' + fmt(maxT, 1);
    document.getElementById('eu').textContent = 'min ' + fmt(minU, 1) + '  max ' + fmt(maxU, 1);

    serieT.push(d.t); serieU.push(d.ur);
    if (serieT.length > MAX_PONTOS) { serieT.shift(); serieU.shift(); }
    desenhar();

    const min = Math.floor(d.uptime / 60000);
    document.getElementById('rodape').textContent =
      d.clientes + ' aparelho(s) conectado(s) | no ar ha ' + min + ' min | ' +
      d.falhas + ' falha(s) de leitura';
    estado(true, 'ao vivo');
  } catch (e) {
    estado(false, 'sem conexao com a estacao');
  }
}

atualizar();
setInterval(atualizar, 2000);
window.addEventListener('resize', desenhar);
</script>
</body>
</html>
)HTML";

// ---------------------------------------------------------------------------
// Rotas
// ---------------------------------------------------------------------------
void rotaRaiz() {
  servidor.send_P(200, "text/html; charset=utf-8", PAGINA);
}

void rotaDados() {
  char json[160];
  snprintf(json, sizeof(json),
           "{\"ok\":%s,\"t\":%.1f,\"ur\":%.1f,\"uptime\":%lu,"
           "\"clientes\":%d,\"leituras\":%lu,\"falhas\":%lu}",
           leituraValida ? "true" : "false", temperatura, umidade,
           (unsigned long)millis(), WiFi.softAPgetStationNum(),
           (unsigned long)totalLeituras, (unsigned long)totalFalhas);
  servidor.sendHeader("Cache-Control", "no-store");
  servidor.send(200, "application/json", json);
}

// Qualquer outro endereco cai na pagina - e o que faz o celular abrir sozinho.
void rotaPerdida() {
  servidor.sendHeader("Location", "http://192.168.4.1/", true);
  servidor.send(302, "text/plain", "");
}

// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println(F("Painel Wi-Fi da estacao"));

  if (PINO_DE >= 0) {
    pinMode(PINO_DE, OUTPUT);
    digitalWrite(PINO_DE, LOW);
  }
  Serial2.begin(BAUD_SENSOR, SERIAL_8N1, PINO_RX, PINO_TX);
  delay(100);
  lerSensor();

  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(ipAP, ipAP, IPAddress(255, 255, 255, 0));
  WiFi.softAP(SSID_AP, SENHA_AP, 1, 0, MAX_CLIENTES);

  dns.setTTL(0);
  dns.start(53, "*", ipAP);   // tudo aponta para a estacao

  servidor.on("/", rotaRaiz);
  servidor.on("/dados", rotaDados);
  servidor.onNotFound(rotaPerdida);
  servidor.begin();

  Serial.print(F("rede:  "));
  Serial.println(SSID_AP);
  Serial.print(F("senha: "));
  Serial.println(SENHA_AP);
  Serial.print(F("abra:  http://"));
  Serial.println(WiFi.softAPIP());
}

void loop() {
  dns.processNextRequest();
  servidor.handleClient();

  static uint32_t proxima = 0;
  if (millis() >= proxima) {
    proxima = millis() + INTERVALO_LEITURA_MS;
    lerSensor();
    Serial.print(leituraValida ? F("ok   ") : F("falha"));
    Serial.print(F("  T="));
    Serial.print(temperatura, 1);
    Serial.print(F(" C  UR="));
    Serial.print(umidade, 1);
    Serial.print(F(" %   aparelhos: "));
    Serial.println(WiFi.softAPgetStationNum());
  }
}

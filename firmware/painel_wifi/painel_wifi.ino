/*
 * Painel Wi-Fi da estacao - ESP32 conectado ao roteador, com portal de
 * configuracao para escolher a rede.
 *
 * PRIMEIRA VEZ (ou se a rede salva nao for encontrada): o ESP32 sobe um
 * ponto de acesso temporario so para configuracao:
 *
 *   rede:  EstacaoMeteorologica-Config
 *   senha: estacao2026
 *   pagina: http://192.168.4.1
 *
 * Conecte nessa rede, escolha o Wi-Fi de verdade na lista (ou digite o nome
 * manualmente), informe a senha e salve. O ESP32 grava a credencial na
 * memoria nao volatil (sobrevive a queda de energia) e reinicia direto na
 * rede escolhida.
 *
 * DEPOIS DE CONFIGURADO: o ESP32 liga direto na rede salva e serve o painel
 * nela — acesse por http://estacao.local ou pelo IP que aparece no monitor
 * serial. Se a conexao falhar (rede fora do ar, senha errada), ele volta
 * sozinho para o modo de configuracao, para nao ficar inacessivel.
 *
 * Pelo proprio painel da tambem para trocar de rede depois (botao "trocar
 * rede Wi-Fi" no rodape), sem precisar regravar o firmware.
 *
 * TELEMETRIA MQTT: alem do painel local, o ESP32 publica num broker Mosquitto
 * rodando na base (Raspberry Pi), onde src/servidor_telemetria.py roda o Kalman e
 * grava CSV. Topicos separados por taxa:
 *
 *     estacao/<dev>/pos      1 Hz     posicao GPS
 *     estacao/<dev>/imu      1 Hz     lote de 10 amostras do IMU (10 Hz)
 *     estacao/<dev>/amb      0.5 Hz   temperatura e umidade
 *     estacao/<dev>/status   retained + LWT
 *
 * Ajuste MQTT_HOST para o nome mDNS da base. Deixe "" para desligar o envio e usar
 * so o painel local. Precisa da biblioteca PubSubClient (Gerenciador de
 * Bibliotecas da IDE) — a unica dependencia externa do firmware.
 *
 * NOTA sobre o "trajeto": e um rastro relativo (metros a partir do primeiro
 * fix GPS), desenhado em canvas — nao um mapa de ruas. Com o ESP32 na rede
 * do roteador (com internet), da para trocar por um mapa de verdade depois
 * (o navegador do celular busca os tiles direto, o ESP32 nao precisa mediar
 * isso) — nao implementado ainda, ver docs/04-telemetria.md.
 *
 * Sensores, mesmo barramento RS485 (par A/B compartilhado, enderecos Modbus
 * diferentes — ver ../README.md e ../estacao_esp32/estacao_esp32.ino):
 *   WTGAHRS3-485    endereco 0x50   GPS (posicao)
 *   ambiental RS485 endereco 0x01   temperatura/umidade
 *
 * Ligacao: RO->GPIO16, DI->GPIO17, DE+RE->GPIO4.
 */

#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <ESPmDNS.h>
#include <PubSubClient.h>
#include <math.h>

#include "wtgahrs3.h"
#include "ambiente_rs485.h"

// ---------------------------------------------------------------------------
// Configuracao
// ---------------------------------------------------------------------------
static const char *AP_CONFIG_SSID  = "EstacaoMeteorologica-Config";
static const char *AP_CONFIG_SENHA = "estacao2026";   // minimo 8 caracteres
static const uint8_t MAX_CLIENTES = 8;
static const uint32_t TIMEOUT_CONEXAO_MS = 15000;  // espera pela rede salva antes de cair no portal

static const int8_t PINO_RX = 16;   // RO do transceptor
static const int8_t PINO_TX = 17;   // DI do transceptor
static const int8_t PINO_DE = 4;    // DE+RE; -1 se o modulo for automatico

static const uint32_t BAUD_SENSOR = 9600;

static const uint16_t INTERVALO_PUBLICACAO_MS = 1000;

// -- escalonador do barramento -----------------------------------------------
// Modbus e UMA transacao por vez, e os tres sensores dividem a mesma UART. Com
// um temporizador independente por sensor eles competem: quando dois vencem no
// mesmo instante, um atrasa o outro, e o mais raro (o ambiental) e sempre o
// prejudicado.
//
// Aqui o tempo e fatiado em slots de 100 ms, num ciclo de 20 slots (2 s), e
// cada slot tem dono definido. Ninguem disputa nada.
//
// Orcamento a 9600 baud: IMU ~38 ms, GPS ~52 ms, ambiente ~23 ms (com o
// silencio t3.5). Todos cabem folgados em 100 ms, com margem ate para uma
// leitura que va a timeout.
//
//   slot  4      -> ambiente   (0,5 Hz)
//   slot  9, 19  -> GPS        (1 Hz)
//   demais       -> IMU        (17 leituras por ciclo, ~8,5 Hz)
static const uint16_t SLOT_MS      = 100;
static const uint8_t  SLOTS_CICLO  = 20;
static const uint8_t  SLOT_AMBIENTE = 4;
static const uint8_t  SLOT_GPS_A    = 9;
static const uint8_t  SLOT_GPS_B    = 19;

// -- MQTT --------------------------------------------------------------------
// Nome mDNS da BASE: o Raspberry Pi que roda o Mosquitto e o
// src/servidor_telemetria.py. Sem o sufixo ".local" — o MDNS.queryHost recebe
// so o nome do host. Deixe vazio ("") para desligar o envio MQTT e usar so o
// painel local.
//
// POR QUE NOME E NAO IP: a base pega endereco por DHCP. Um IP fixo gravado no
// firmware quebra em SILENCIO quando o roteador entrega outro — o ESP32 publica
// no vazio, o painel local continua mostrando os sensores normalmente, e a
// falha so aparece quando alguem for procurar o dado que nao chegou. Resolvendo
// o nome a cada tentativa de conexao, mudanca de IP se conserta sozinha.
static const char *MQTT_HOST = "raspberrypi";
static const uint16_t MQTT_PORTA = 1883;
static const char *DEVICE_ID = "esp32-01";

// Lote de amostras do IMU por mensagem: 10 amostras a 10 Hz = 1 mensagem/s.
// Uma mensagem MQTT por amostra seria 10x o overhead de protocolo para o
// mesmo dado — ver docs/04-telemetria.md.
static const uint8_t IMU_LOTE = 10;

// O PubSubClient usa buffer de 256 bytes por padrao, e o lote de IMU passa de
// 1 KB. Sem isto a publicacao falha em silencio: publish() so devolve false.
static const uint16_t MQTT_BUFFER = 1536;

// Metros por grau de latitude — aproximacao valida em area pequena (< ~50 km).
static const double METROS_POR_GRAU_LAT = 111320.0;

// ---------------------------------------------------------------------------
// Sensores
// ---------------------------------------------------------------------------
WTGAHRS3      sensor(Serial2, PINO_DE, ENDERECO_PADRAO);              // 0x50, GPS
AmbienteRS485 ambiente(Serial2, PINO_DE, ENDERECO_AMBIENTAL_PADRAO);  // 0x01, T/UR

// ---------------------------------------------------------------------------
// Estado
// ---------------------------------------------------------------------------
bool modoConfig = false;  // true = portal de configuracao Wi-Fi, false = painel normal

float    temperatura   = 0.0f;
float    umidade       = 0.0f;
bool     leituraValida = false;
uint32_t totalLeituras = 0;
uint32_t totalFalhas   = 0;

// Diagnostico do barramento: os tres sensores dividem a mesma UART, entao uma
// leitura que da timeout (200 ms) atrasa todas as outras. Estes contadores
// dizem quem esta gastando o tempo.
uint32_t falhasImu = 0, falhasGps = 0;
uint32_t tempoImuUs = 0, tempoGpsUs = 0, tempoAmbUs = 0;
uint32_t lidasImu = 0, lidasGps = 0;

Posicao  pos;
bool     posValida  = false;
bool     origemDefinida = false;
double   origemLat = 0.0, origemLon = 0.0;
float    xMetros = 0.0f, yMetros = 0.0f;

Imu      imu;
bool     imuValida = false;

// Lote de amostras do IMU esperando publicacao.
Imu      loteImu[IMU_LOTE];
uint8_t  loteN = 0;
uint32_t loteInicioUs = 0;   // esp_timer no inicio do lote, para o dt real

IPAddress ipAP(192, 168, 4, 1);
WebServer servidor(80);
DNSServer dns;
Preferences prefs;

WiFiClient clienteWifi;
PubSubClient mqtt(clienteWifi);
uint32_t seqPos = 0, seqImu = 0, seqAmb = 0;
uint32_t publicadas = 0, falhasPublicacao = 0;

// ---------------------------------------------------------------------------
// Leitura dos sensores
// ---------------------------------------------------------------------------
void lerAmbiente() {
  uint32_t t0 = (uint32_t)esp_timer_get_time();
  AmbienteRS485Leitura leitura;
  if (ambiente.ler(leitura)) {
    temperatura   = leitura.temperaturaC;
    umidade       = leitura.umidadeRH;
    leituraValida = true;
    totalLeituras++;
  } else {
    leituraValida = false;
    totalFalhas++;
  }
  tempoAmbUs = (uint32_t)esp_timer_get_time() - t0;
}

void lerGps() {
  uint32_t t0 = (uint32_t)esp_timer_get_time();
  posValida = sensor.lerPosicao(pos);
  tempoGpsUs = (uint32_t)esp_timer_get_time() - t0;
  lidasGps++;
  if (!posValida) falhasGps++;
  if (!posValida || !pos.temFix()) return;

  if (!origemDefinida) {
    origemLat = pos.latitude;
    origemLon = pos.longitude;
    origemDefinida = true;
  }
  double escalaLon = METROS_POR_GRAU_LAT * cos(origemLat * PI / 180.0);
  xMetros = (float)((pos.longitude - origemLon) * escalaLon);
  yMetros = (float)((pos.latitude  - origemLat) * METROS_POR_GRAU_LAT);
}

void lerImu() {
  uint32_t t0 = (uint32_t)esp_timer_get_time();
  imuValida = sensor.lerImu(imu);
  tempoImuUs = (uint32_t)esp_timer_get_time() - t0;
  lidasImu++;
  if (!imuValida) { falhasImu++; return; }
  if (loteN == 0) loteInicioUs = t0;
  if (loteN < IMU_LOTE) loteImu[loteN++] = imu;
}

// ---------------------------------------------------------------------------
// MQTT
// ---------------------------------------------------------------------------
bool mqttLigado() {
  return MQTT_HOST[0] != '\0';
}

// Endereco da base, resolvido por mDNS e guardado entre tentativas para nao
// repetir a consulta a cada publicacao. Volta a zero sempre que uma conexao
// falha, e e isso que faz o ESP32 redescobrir a base quando ela troca de IP.
IPAddress ipBase;

String topico(const char *tipo) {
  return String("estacao/") + DEVICE_ID + "/" + tipo;
}

/**
 * Conecta no broker deixando um Last Will registrado: se o ESP32 cair sem
 * avisar, o proprio broker publica {"online":false} em estacao/<dev>/status.
 * E deteccao de queda sem heartbeat manual.
 */
bool conectarMqtt() {
  if (!mqttLigado() || WiFi.status() != WL_CONNECTED) return false;

  // A consulta mDNS bloqueia ate o timeout abaixo. Isso so acontece enquanto o
  // MQTT esta desconectado, no maximo uma vez a cada 5 s (ver o loop()), e o
  // proprio mqtt.connect() ja bloqueia de forma parecida — nao e uma classe
  // nova de pausa. Ainda assim o timeout e curto de proposito: a prioridade do
  // firmware e nao perder amostra, e a rede vem depois disso.
  if (ipBase == INADDR_NONE) {
    ipBase = MDNS.queryHost(MQTT_HOST, 1500);
    if (ipBase == INADDR_NONE) {
      Serial.print(F("mDNS nao resolveu "));
      Serial.print(MQTT_HOST);
      Serial.println(F(".local — a base esta ligada e na mesma rede?"));
      return false;
    }
    Serial.print(F("base encontrada em "));
    Serial.println(ipBase);
    mqtt.setServer(ipBase, MQTT_PORTA);
  }

  String topicoStatus = topico("status");
  bool ok = mqtt.connect(DEVICE_ID,
                         nullptr, nullptr,                 // sem usuario/senha
                         topicoStatus.c_str(), 1, true,    // LWT: QoS 1, retained
                         "{\"online\":false}");
  if (ok) {
    mqtt.publish(topicoStatus.c_str(), "{\"online\":true}", true);
    Serial.println(F("mqtt conectado"));
  } else {
    // Esquece o endereco: se a base mudou de IP, insistir no antigo nunca
    // conectaria. A proxima tentativa refaz o mDNS.
    ipBase = INADDR_NONE;
  }
  return ok;
}

void publicarAmbiente() {
  if (!mqtt.connected() || !leituraValida) return;
  char json[96];
  snprintf(json, sizeof(json), "{\"seq\":%lu,\"temp\":%.1f,\"umid\":%.1f}",
           (unsigned long)seqAmb++, temperatura, umidade);
  if (mqtt.publish(topico("amb").c_str(), json)) publicadas++;
  else                                           falhasPublicacao++;
}

void publicarPosicao() {
  if (!mqtt.connected() || !posValida) return;
  char json[220];
  snprintf(json, sizeof(json),
           "{\"seq\":%lu,\"fix\":%s,\"lat\":%.7f,\"lon\":%.7f,\"alt\":%.1f,"
           "\"vel\":%.2f,\"sat\":%u,\"hdop\":%.2f,\"rumo\":%.1f}",
           (unsigned long)seqPos++, pos.temFix() ? "true" : "false",
           pos.latitude, pos.longitude, pos.altitudeGpsM,
           pos.velocidadeKmh, pos.satelites, pos.hdop, pos.rumoGpsDeg);
  if (mqtt.publish(topico("pos").c_str(), json)) publicadas++;
  else                                           falhasPublicacao++;
}

/**
 * Publica o lote de IMU com o dt REAL medido pelo esp_timer, nao o nominal.
 * O filtro de Kalman do servidor usa esse dt na predicao — o intervalo
 * nominal mente, porque cada transacao Modbus tem jitter proprio.
 */
void publicarImu() {
  if (!mqtt.connected() || loteN == 0) return;

  uint32_t agoraUs = (uint32_t)esp_timer_get_time();
  float dt = ((agoraUs - loteInicioUs) / 1e6f) / (float)loteN;
  if (dt <= 0.0f || dt >= 1.0f) { loteN = 0; return; }

  // Monta o array manualmente: ArduinoJson seria outra dependencia, e o
  // formato aqui e fixo.
  String json = "{\"seq\":" + String(seqImu++) + ",\"dt\":" + String(dt, 4) + ",\"m\":[";
  for (uint8_t i = 0; i < loteN; i++) {
    char amostra[150];
    snprintf(amostra, sizeof(amostra),
             "%s{\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,"
             "\"gx\":%.2f,\"gy\":%.2f,\"gz\":%.2f,"
             "\"roll\":%.2f,\"pitch\":%.2f,\"yaw\":%.2f}",
             i ? "," : "",
             loteImu[i].accX, loteImu[i].accY, loteImu[i].accZ,
             loteImu[i].gyroX, loteImu[i].gyroY, loteImu[i].gyroZ,
             loteImu[i].roll, loteImu[i].pitch, loteImu[i].yaw);
    json += amostra;
  }
  json += "]}";

  if (mqtt.publish(topico("imu").c_str(), json.c_str())) publicadas++;
  else                                                   falhasPublicacao++;
  loteN = 0;
}

// ---------------------------------------------------------------------------
// Portal de configuracao Wi-Fi
// ---------------------------------------------------------------------------
String escaparHtml(const String &entrada) {
  String s = entrada;
  s.replace("&", "&amp;");
  s.replace("<", "&lt;");
  s.replace(">", "&gt;");
  s.replace("\"", "&quot;");
  s.replace("'", "&#39;");
  return s;
}

struct RedeEncontrada {
  String ssid;
  int32_t rssi;
  bool aberta;
};

void rotaConfigRaiz() {
  int total = WiFi.scanNetworks();

  static RedeEncontrada redes[20];
  uint8_t nRedes = 0;
  for (int i = 0; i < total && nRedes < 20; i++) {
    String ssid = WiFi.SSID(i);
    if (ssid.length() == 0) continue;

    bool jaTem = false;
    for (uint8_t k = 0; k < nRedes; k++) {
      if (redes[k].ssid == ssid) {
        jaTem = true;
        if (WiFi.RSSI(i) > redes[k].rssi) redes[k].rssi = WiFi.RSSI(i);
        break;
      }
    }
    if (!jaTem) {
      redes[nRedes].ssid   = ssid;
      redes[nRedes].rssi   = WiFi.RSSI(i);
      redes[nRedes].aberta = (WiFi.encryptionType(i) == WIFI_AUTH_OPEN);
      nRedes++;
    }
  }
  // ordena por sinal, mais forte primeiro (poucas redes — insertion sort basta)
  for (uint8_t i = 1; i < nRedes; i++) {
    RedeEncontrada chave = redes[i];
    int8_t j = i - 1;
    while (j >= 0 && redes[j].rssi < chave.rssi) { redes[j + 1] = redes[j]; j--; }
    redes[j + 1] = chave;
  }
  WiFi.scanDelete();

  String html =
      "<!DOCTYPE html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
      "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
      "<title>Configurar Wi-Fi</title><style>"
      "*{box-sizing:border-box} body{margin:0;padding:20px 16px 40px;"
      "font-family:system-ui,-apple-system,sans-serif;background:#f4f5f7;color:#16181d}"
      ".env{max-width:420px;margin:0 auto}"
      "h1{font-size:1.05rem;margin:0 0 4px}"
      "p.sub{font-size:.8rem;color:#6b7280;margin:0 0 18px}"
      ".cartao{background:#fff;border:1px solid #e3e5e9;border-radius:14px;padding:16px 18px;margin-bottom:14px}"
      ".rede{display:flex;justify-content:space-between;align-items:center;padding:10px 4px;"
      "border-bottom:1px solid #e3e5e9;font-size:.9rem;cursor:pointer}"
      ".rede:last-child{border-bottom:none}"
      ".rede input{margin-right:10px}"
      ".sinal{color:#6b7280;font-size:.78rem;font-variant-numeric:tabular-nums}"
      "label{display:block;font-size:.78rem;color:#6b7280;margin:12px 0 4px;text-transform:uppercase;letter-spacing:.04em}"
      "input[type=text],input[type=password]{width:100%;padding:10px 12px;border:1px solid #e3e5e9;"
      "border-radius:10px;font-size:1rem;background:#f4f5f7;color:#16181d}"
      "button{width:100%;padding:12px;margin-top:16px;border:none;border-radius:10px;"
      "background:#2f7fd1;color:#fff;font-size:1rem;font-weight:600;cursor:pointer}"
      "</style></head><body><div class=\"env\">"
      "<h1>Configurar Wi-Fi da estacao</h1>"
      "<p class=\"sub\">Escolha a rede para a estacao se conectar. A senha fica salva no ESP32.</p>"
      "<form method=\"POST\" action=\"/salvar\">"
      "<div class=\"cartao\">";

  if (nRedes == 0) {
    html += "<p style=\"font-size:.85rem;color:#6b7280;margin:4px\">Nenhuma rede encontrada. Digite o nome manualmente abaixo.</p>";
  }
  for (uint8_t i = 0; i < nRedes; i++) {
    String ssidEsc = escaparHtml(redes[i].ssid);
    html += "<label class=\"rede\"><span><input type=\"radio\" name=\"ssid\" value=\"" + ssidEsc + "\"" +
            (i == 0 ? " checked" : "") + ">" + ssidEsc + (redes[i].aberta ? " (aberta)" : "") + "</span>"
            "<span class=\"sinal\">" + String(redes[i].rssi) + " dBm</span></label>";
  }
  html +=
      "</div>"
      "<label>ou digite o nome da rede</label>"
      "<input type=\"text\" name=\"ssidManual\" placeholder=\"nome da rede (opcional)\">"
      "<label>senha</label>"
      "<input type=\"password\" name=\"senha\" placeholder=\"deixe em branco se a rede for aberta\">"
      "<button type=\"submit\">Salvar e conectar</button>"
      "</form></div></body></html>";

  servidor.send(200, "text/html; charset=utf-8", html);
}

void rotaConfigSalvar() {
  String ssid = servidor.arg("ssidManual");
  if (ssid.length() == 0) ssid = servidor.arg("ssid");
  String senha = servidor.arg("senha");

  if (ssid.length() == 0) {
    servidor.send(400, "text/plain; charset=utf-8", "Escolha ou digite uma rede.");
    return;
  }

  prefs.putString("ssid", ssid);
  prefs.putString("senha", senha);

  servidor.send(200, "text/html; charset=utf-8",
      "<!DOCTYPE html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
      "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"></head>"
      "<body style=\"font-family:system-ui,sans-serif;text-align:center;padding-top:80px\">"
      "<p>Salvo. Reiniciando e conectando em <b>" + escaparHtml(ssid) + "</b>...</p>"
      "<p style=\"color:#6b7280;font-size:.85rem\">Se nao conectar em alguns segundos, "
      "o ponto de acesso de configuracao volta sozinho.</p></body></html>");
  delay(1000);
  ESP.restart();
}

void rotaConfigNaoEncontrada() {
  servidor.sendHeader("Location", "http://192.168.4.1/", true);
  servidor.send(302, "text/plain", "");
}

void iniciarPortalConfig() {
  modoConfig = true;
  WiFi.mode(WIFI_AP_STA);  // AP para o celular conectar, STA ligado so para permitir scan
  WiFi.softAPConfig(ipAP, ipAP, IPAddress(255, 255, 255, 0));
  WiFi.softAP(AP_CONFIG_SSID, AP_CONFIG_SENHA, 1, 0, MAX_CLIENTES);

  dns.setTTL(0);
  dns.start(53, "*", ipAP);

  servidor.on("/", rotaConfigRaiz);
  servidor.on("/salvar", HTTP_POST, rotaConfigSalvar);
  servidor.onNotFound(rotaConfigNaoEncontrada);
  servidor.begin();

  Serial.println(F("Portal de configuracao Wi-Fi:"));
  Serial.print(F("  rede:  "));
  Serial.println(AP_CONFIG_SSID);
  Serial.print(F("  senha: "));
  Serial.println(AP_CONFIG_SENHA);
  Serial.println(F("  pagina: http://192.168.4.1"));
}

/** Tenta conectar na rede salva. Bloqueia ate TIMEOUT_CONEXAO_MS ou sucesso. */
bool conectarRedeSalva(const String &ssid, const String &senha) {
  Serial.print(F("Conectando em \""));
  Serial.print(ssid);
  Serial.println(F("\"..."));

  WiFi.mode(WIFI_STA);
  if (senha.length() == 0) WiFi.begin(ssid.c_str());
  else                     WiFi.begin(ssid.c_str(), senha.c_str());

  uint32_t limite = millis() + TIMEOUT_CONEXAO_MS;
  while (WiFi.status() != WL_CONNECTED && millis() < limite) {
    delay(300);
    Serial.print('.');
  }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}

// ---------------------------------------------------------------------------
// Painel (modo normal, conectado na rede)
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
    --borda: #e3e5e9; --temp: #e0533d; --umid: #2f7fd1; --pos: #2f9e57;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --fundo: #111317; --cartao: #1a1d23; --texto: #f0f2f5; --fraco: #9099a8;
      --borda: #2a2e36; --temp: #ff7a5e; --umid: #63aef5; --pos: #4fd07a;
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
            border-radius: 14px; padding: 16px 18px; margin-top: 12px; }
  .cartao:first-child, .grade { margin-top: 0; }
  .rotulo { font-size: .78rem; text-transform: uppercase; letter-spacing: .06em;
            color: var(--fraco); margin-bottom: 6px; display: flex;
            justify-content: space-between; align-items: center; }
  .valor { font-size: 2.6rem; font-weight: 650; line-height: 1;
           font-variant-numeric: tabular-nums; }
  .valor span { font-size: 1.1rem; font-weight: 500; color: var(--fraco);
                margin-left: 3px; }
  #vt { color: var(--temp); } #vu { color: var(--umid); }
  .extremos { font-size: .75rem; color: var(--fraco); margin-top: 8px;
              font-variant-numeric: tabular-nums; }
  canvas { width: 100%; height: 140px; display: block; }
  .legenda { display: flex; gap: 16px; font-size: .75rem; color: var(--fraco);
             margin-top: 6px; flex-wrap: wrap; }
  .legenda i { display: inline-block; width: 10px; height: 3px;
               border-radius: 2px; margin-right: 5px; vertical-align: middle; }
  .gps-grade { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
               font-variant-numeric: tabular-nums; margin-bottom: 10px; }
  .gps-grade div { font-size: .8rem; }
  .gps-grade b { display: block; font-size: 1rem; font-weight: 650; }
  .aviso { font-size: .72rem; color: var(--fraco); margin-top: 8px; }
  footer { margin-top: 20px; font-size: .72rem; color: var(--fraco);
           text-align: center; line-height: 1.6; }
  footer button { margin-top: 8px; padding: 6px 14px; border-radius: 8px;
           border: 1px solid var(--borda); background: var(--cartao);
           color: var(--fraco); font-size: .72rem; cursor: pointer; }
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

  <div class="cartao">
    <div class="rotulo">Ultimos minutos</div>
    <canvas id="tela"></canvas>
    <div class="legenda">
      <span><i style="background:var(--temp)"></i>temperatura</span>
      <span><i style="background:var(--umid)"></i>umidade</span>
    </div>
  </div>

  <div class="cartao">
    <div class="rotulo">
      <span>Posicao GPS</span>
      <span class="estado"><span class="bolinha" id="bolinhaGps"></span><span id="estadoGps">sem fix</span></span>
    </div>
    <div class="gps-grade">
      <div>lat<b id="lat">--</b></div>
      <div>lon<b id="lon">--</b></div>
      <div>alt<b id="alt">-- m</b></div>
      <div>vel<b id="vel">-- km/h</b></div>
    </div>
    <div class="extremos" id="sathdop">sat -- &nbsp; hdop --</div>
  </div>

  <div class="cartao">
    <div class="rotulo">Trajeto</div>
    <canvas id="mapa"></canvas>
    <div class="legenda">
      <span><i style="background:var(--pos)"></i>rastro</span>
    </div>
    <div class="aviso">
      Rastro relativo ao primeiro fix, em metros — nao e um mapa real.
    </div>
  </div>

  <footer>
    <span id="rodape">&nbsp;</span><br>
    <form method="POST" action="/wifi/esquecer" onsubmit="return confirm('Esquecer a rede Wi-Fi salva e reiniciar no modo de configuracao?')">
      <button type="submit">trocar rede Wi-Fi</button>
    </form>
  </footer>
</div>

<script>
const MAX_PONTOS = 180;      // 180 x 2 s = 6 minutos
let serieT = [], serieU = [];
let minT = null, maxT = null, minU = null, maxU = null;

const MAX_PONTOS_POS = 600;  // 600 x ~2 s = 20 minutos
let trilha = [];

function fmt(v, casas) { return v.toFixed(casas); }

function prepararTela(id) {
  const tela = document.getElementById(id);
  const r = tela.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  tela.width = r.width * dpr;
  tela.height = r.height * dpr;
  const c = tela.getContext('2d');
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, r.width, r.height);
  return { c, w: r.width, h: r.height };
}

function desenhar() {
  const { c, w, h } = prepararTela('tela');
  if (serieT.length < 2) return;

  const estilo = getComputedStyle(document.documentElement);
  const cores = [estilo.getPropertyValue('--temp'), estilo.getPropertyValue('--umid')];
  const pad = 6;

  [serieT, serieU].forEach((serie, i) => {
    let lo = Math.min(...serie), hi = Math.max(...serie);
    if (hi - lo < 0.5) { const m = (hi + lo) / 2; lo = m - 0.25; hi = m + 0.25; }
    const passo = (w - pad * 2) / (MAX_PONTOS - 1);
    c.beginPath();
    serie.forEach((v, k) => {
      const x = pad + k * passo;
      const y = pad + (h - pad * 2) * (1 - (v - lo) / (hi - lo));
      k ? c.lineTo(x, y) : c.moveTo(x, y);
    });
    c.strokeStyle = cores[i].trim();
    c.lineWidth = 2;
    c.lineJoin = 'round';
    c.stroke();
  });
}

// Autoescala mantendo proporcao 1:1 entre X e Y — senao o rastro fica
// distorcido (um circulo pareceria uma elipse).
function desenharMapa() {
  const { c, w, h } = prepararTela('mapa');
  if (trilha.length < 1) return;

  const estilo = getComputedStyle(document.documentElement).getPropertyValue('--pos').trim();
  const pad = 12;

  let xs = trilha.map(p => p.x), ys = trilha.map(p => p.y);
  let minX = Math.min(...xs), maxX = Math.max(...xs);
  let minY = Math.min(...ys), maxY = Math.max(...ys);
  let rangeX = Math.max(maxX - minX, 2);
  let rangeY = Math.max(maxY - minY, 2);
  const escala = Math.min((w - pad * 2) / rangeX, (h - pad * 2) / rangeY);
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;

  const px = (x) => w / 2 + (x - cx) * escala;
  const py = (y) => h / 2 - (y - cy) * escala;  // Y cresce para cima (norte)

  if (trilha.length > 1) {
    c.beginPath();
    trilha.forEach((p, k) => {
      k ? c.lineTo(px(p.x), py(p.y)) : c.moveTo(px(p.x), py(p.y));
    });
    c.strokeStyle = estilo;
    c.lineWidth = 2;
    c.lineJoin = 'round';
    c.stroke();
  }

  c.beginPath();
  c.arc(px(trilha[0].x), py(trilha[0].y), 3, 0, 2 * Math.PI);
  c.fillStyle = estilo;
  c.globalAlpha = 0.4;
  c.fill();
  c.globalAlpha = 1;

  const atual = trilha[trilha.length - 1];
  c.beginPath();
  c.arc(px(atual.x), py(atual.y), 5, 0, 2 * Math.PI);
  c.fillStyle = estilo;
  c.fill();
}

function estado(bom, texto) {
  document.getElementById('bolinha').className = 'bolinha' + (bom ? '' : ' ruim');
  document.getElementById('estado').textContent = texto;
}

async function atualizar() {
  try {
    const r = await fetch('/dados', { cache: 'no-store' });
    const d = await r.json();
    if (!d.ok) { estado(false, 'sensor sem resposta'); }
    else {
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
      estado(true, 'ao vivo');
    }

    const bolinhaGps = document.getElementById('bolinhaGps');
    const estadoGps = document.getElementById('estadoGps');
    if (d.posOk && d.fix) {
      bolinhaGps.className = 'bolinha';
      estadoGps.textContent = 'fix';
      document.getElementById('lat').textContent = d.lat.toFixed(6);
      document.getElementById('lon').textContent = d.lon.toFixed(6);
      document.getElementById('alt').textContent = fmt(d.alt, 1) + ' m';
      document.getElementById('vel').textContent = fmt(d.vel, 1) + ' km/h';
      document.getElementById('sathdop').textContent =
        'sat ' + d.sat + '   hdop ' + fmt(d.hdop, 2);

      trilha.push({ x: d.x, y: d.y });
      if (trilha.length > MAX_PONTOS_POS) trilha.shift();
      desenharMapa();
    } else {
      bolinhaGps.className = 'bolinha ruim';
      estadoGps.textContent = d.posOk ? 'sem fix' : 'sensor sem resposta';
    }

    const min = Math.floor(d.uptime / 60000);
    document.getElementById('rodape').textContent =
      'sinal Wi-Fi ' + d.rssi + ' dBm | no ar ha ' + min + ' min | ' +
      d.falhas + ' falha(s) de leitura ambiental';
  } catch (e) {
    estado(false, 'sem conexao com a estacao');
  }
}

atualizar();
setInterval(atualizar, 2000);
window.addEventListener('resize', () => { desenhar(); desenharMapa(); });
</script>
</body>
</html>
)HTML";

void rotaRaiz() {
  servidor.send_P(200, "text/html; charset=utf-8", PAGINA);
}

void rotaDados() {
  char json[380];
  snprintf(json, sizeof(json),
           "{\"ok\":%s,\"t\":%.1f,\"ur\":%.1f,"
           "\"posOk\":%s,\"fix\":%s,\"lat\":%.6f,\"lon\":%.6f,\"alt\":%.1f,"
           "\"vel\":%.1f,\"sat\":%u,\"hdop\":%.2f,\"x\":%.1f,\"y\":%.1f,"
           "\"uptime\":%lu,\"rssi\":%d,\"leituras\":%lu,\"falhas\":%lu}",
           leituraValida ? "true" : "false", temperatura, umidade,
           posValida ? "true" : "false", (posValida && pos.temFix()) ? "true" : "false",
           pos.latitude, pos.longitude, pos.altitudeGpsM,
           pos.velocidadeKmh, pos.satelites, pos.hdop, xMetros, yMetros,
           (unsigned long)millis(), WiFi.RSSI(),
           (unsigned long)totalLeituras, (unsigned long)totalFalhas);
  servidor.sendHeader("Cache-Control", "no-store");
  servidor.send(200, "application/json", json);
}

void rotaEsquecerRede() {
  servidor.send(200, "text/html; charset=utf-8",
      "<!DOCTYPE html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
      "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"></head>"
      "<body style=\"font-family:system-ui,sans-serif;text-align:center;padding-top:80px\">"
      "<p>Rede esquecida. Reiniciando no modo de configuracao...</p></body></html>");
  prefs.remove("ssid");
  prefs.remove("senha");
  delay(1000);
  ESP.restart();
}

void rotaNaoEncontrada() {
  servidor.send(404, "text/plain; charset=utf-8", "nao encontrado");
}

// ---------------------------------------------------------------------------
void iniciarPainel() {
  modoConfig = false;

  if (MDNS.begin("estacao")) {
    Serial.println(F("mDNS ativo: http://estacao.local"));
  }

  sensor.iniciar(BAUD_SENSOR, PINO_RX, PINO_TX);  // abre a UART e o pino DE, compartilhados
  lerAmbiente();
  lerGps();

  if (mqttLigado()) {
    // O setServer fica no conectarMqtt(), depois do mDNS resolver o endereco.
    mqtt.setBufferSize(MQTT_BUFFER);   // o padrao de 256 B nao cabe o lote de IMU
    mqtt.setKeepAlive(30);
    Serial.print(F("base MQTT: "));
    Serial.print(MQTT_HOST);
    Serial.print(F(".local:"));
    Serial.println(MQTT_PORTA);
    conectarMqtt();   // se a base estiver fora, o loop() tenta de novo a cada 5 s
  } else {
    Serial.println(F("MQTT desligado (MQTT_HOST vazio) - so painel local"));
  }

  servidor.on("/", rotaRaiz);
  servidor.on("/dados", rotaDados);
  servidor.on("/wifi/esquecer", HTTP_POST, rotaEsquecerRede);
  servidor.onNotFound(rotaNaoEncontrada);
  servidor.begin();

  Serial.print(F("conectado, IP: "));
  Serial.println(WiFi.localIP());
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println(F("Estacao Meteorologica - painel Wi-Fi"));

  prefs.begin("wifi", false);
  String ssidSalvo  = prefs.getString("ssid", "");
  String senhaSalva = prefs.getString("senha", "");

  if (ssidSalvo.length() == 0) {
    Serial.println(F("nenhuma rede salva."));
    iniciarPortalConfig();
    return;
  }

  if (conectarRedeSalva(ssidSalvo, senhaSalva)) {
    iniciarPainel();
  } else {
    Serial.println(F("nao conseguiu conectar na rede salva - abrindo portal de configuracao."));
    iniciarPortalConfig();
  }
}

void loop() {
  if (modoConfig) {
    dns.processNextRequest();
    servidor.handleClient();
    return;
  }

  servidor.handleClient();

  // Reconexao do MQTT sem bloquear a leitura dos sensores: tenta a cada 5 s.
  if (mqttLigado()) {
    if (mqtt.connected()) {
      mqtt.loop();
    } else {
      static uint32_t proximaTentativa = 0;
      if (millis() >= proximaTentativa) {
        proximaTentativa = millis() + 5000;
        conectarMqtt();
      }
    }
  }

  // Escalonador do barramento: um slot por vez, dono definido. Nunca ha duas
  // transacoes Modbus disputando a UART.
  static uint32_t proximoSlot = 0;
  static uint8_t  slot = 0;
  if (millis() >= proximoSlot) {
    proximoSlot = millis() + SLOT_MS;
    switch (slot) {
      case SLOT_AMBIENTE: lerAmbiente(); break;
      case SLOT_GPS_A:
      case SLOT_GPS_B:    lerGps();      break;
      default:            lerImu();      break;
    }
    slot = (slot + 1) % SLOTS_CICLO;
  }

  // Publicacao e relato, uma vez por segundo.
  static uint32_t proximaPublicacao = 0;
  if (millis() >= proximaPublicacao) {
    proximaPublicacao = millis() + INTERVALO_PUBLICACAO_MS;
    publicarPosicao();
    publicarImu();
    publicarAmbiente();

    Serial.print(leituraValida ? F("amb ok ") : F("amb -- "));
    Serial.print(F(" T="));
    Serial.print(temperatura, 1);
    Serial.print(F("C UR="));
    Serial.print(umidade, 1);
    if (posValida && pos.temFix()) {
      Serial.print(F("%  gps "));
      Serial.print(pos.latitude, 6);
      Serial.print(' ');
      Serial.print(pos.longitude, 6);
    } else {
      Serial.print(F("%  gps sem fix"));
    }
    Serial.print(F("  mqtt "));
    if (!mqttLigado())            Serial.print(F("off"));
    else if (mqtt.connected())    Serial.print(publicadas);
    else                          Serial.print(F("desconectado"));
    if (falhasPublicacao) {
      Serial.print(F(" ("));
      Serial.print(falhasPublicacao);
      Serial.print(F(" falhas pub)"));
    }
    Serial.println();

    // Diagnostico do barramento, uma vez por segundo. "ms" e o tempo da ULTIMA
    // leitura de cada sensor; falhas sao acumuladas desde o boot. Timeout do
    // Modbus e 200 ms: leitura perto disso significa que o sensor nao
    // respondeu e o barramento ficou parado esperando.
    Serial.print(F("   barramento: imu "));
    Serial.print(tempoImuUs / 1000.0f, 1);
    Serial.print(F(" ms ("));
    Serial.print(falhasImu);
    Serial.print('/');
    Serial.print(lidasImu);
    Serial.print(F(" falhas)   gps "));
    Serial.print(tempoGpsUs / 1000.0f, 1);
    Serial.print(F(" ms ("));
    Serial.print(falhasGps);
    Serial.print('/');
    Serial.print(lidasGps);
    Serial.print(F(" falhas)   amb "));
    Serial.print(tempoAmbUs / 1000.0f, 1);
    Serial.print(F(" ms ("));
    Serial.print(totalFalhas);
    Serial.print('/');
    Serial.print(totalLeituras + totalFalhas);
    Serial.print(F(" falhas)   lote imu "));
    Serial.println(loteN);
  }
}

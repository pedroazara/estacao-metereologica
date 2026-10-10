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
 *     estacao/<dev>/amb      0.5 Hz   temp/umidade RS485, ruido (dB, Leq), as
 *                                     duas temperaturas auxiliares (KY-028/KY-013,
 *                                     pra deteccao de variacao - ver docs do Kalman)
 *                                     e luminosidade (lux, GY-302/BH1750)
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
 *
 * KY-038 (microfone, ruido ambiente): AO->GPIO32 atraves de um divisor
 * resistivo 10k/10k (o modulo e alimentado em 5V para ter ganho decente, mas
 * o ADC do ESP32 nao aguenta mais que 3,3V - ver firmware/teste_ky038/).
 *
 * KY-028 e KY-013 (termistores NTC, temperatura auxiliar pro Kalman):
 * AO->GPIO35 e AO->GPIO39 respectivamente (GPIO33 e GPIO34 nao responderam
 * nesta placa - fiacao, nao os pinos; evite tambem GPIO26/outro ADC2, que
 * conflita com o Wi-Fi), 3V3, calibrados contra o RS485 (ver
 * src/calibrar_ky028.py e firmware/teste_ky028/, firmware/teste_ky013/).
 *
 * GY-302 (chip BH1750, luminosidade): SDA->GPIO21, SCL->GPIO22, 3V3,
 * ADDR->GND (endereco 0x23). Digital via I2C, ja calibrado de fabrica em
 * lux - sem divisor resistivo nem calibracao contra referencia, ao contrario
 * dos NTCs (ver firmware/teste_gy302/).
 */

#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <ESPmDNS.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <math.h>

#include "wtgahrs3.h"
#include "ambiente_rs485.h"
#include "ble_estacao.h"

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

// -- KY-038 (microfone) -------------------------------------------------------
static const uint8_t  PINO_KY038_AO    = 32;  // AO, apos o divisor 10k/10k
static const uint16_t SOM_SUBJANELA_MS = 20;  // janela curta pra pegar a amplitude do audio
// Coeficientes da calibracao (ver src/calibrar_ky038.py): dB = a*log10(amplitude) + b.
// Dependem do ganho fixado no potenciometro do modulo - recalibrar se ele for
// reajustado.
static const float SOM_COEF_A = 26.475f;
static const float SOM_COEF_B = 21.926f;

// -- KY-028 / KY-013 (termistores NTC) -----------------------------------------
// Leitura instantanea, sem janela - ao contrario do som, a temperatura nao
// oscila rapido, um analogRead() por ciclo de publicacao basta.
static const uint8_t PINO_KY028_AO = 35;
static const uint8_t PINO_KY013_AO = 39;  // GPIO33 e GPIO34 nao responderam nessa placa (fiacao) - 39 e "SN"/"VN" na serigrafia

// Coeficientes de src/calibrar_ky028.py (mesma equacao pros dois termistores,
// so os coeficientes mudam): 1/T = a*ln((1-x)/x) + b, x = adc/4095.
static const float KY028_CAL_A = -0.000196f;
static const float KY028_CAL_B = 0.003763f;
static const float KY013_CAL_A = -0.000289f;
static const float KY013_CAL_B = 0.004235f;

// -- GY-302 / BH1750 (luminosidade, I2C) --------------------------------------
static const uint8_t PINO_SDA = 21;
static const uint8_t PINO_SCL = 22;
static const uint8_t BH1750_ENDERECO = 0x23;        // ADDR em GND; 0x5C se ADDR em VCC
static const uint8_t BH1750_CONTINUO_H_RES = 0x10;  // 1 lx/contagem, ~120 ms de conversao

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

// Acumulador do Leq (nivel medio de ruido) desde a ultima publicacao. So a
// media sai no "amb" - o dB instantaneo de cada subjanela nao e guardado,
// entao a integracao tem que ser feita aqui, aos poucos, sem bloquear o
// loop() (ver amostrarSom()).
uint16_t somMinSubjanela     = 4095, somMaxSubjanela = 0;
uint32_t somProximaSubjanela = 0;
double   somSomaPotencia     = 0.0;  // soma de 10^(dB/10) das subjanelas do ciclo
uint32_t somAmostras         = 0;

bool bh1750Disponivel = false;  // false = segue publicando os outros sensores sem lux

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
// Configuracao ajustavel pelo app (caracteristica BLE `cfg`)
// ---------------------------------------------------------------------------
// Persiste em Preferences: um instrumento que esquece o ajuste a cada queda de
// energia obriga a reconfigurar em campo, que e onde menos se quer mexer nisso.
struct Config {
  bool     usarGps  = true;
  bool     usarImu  = true;
  bool     usarAmb  = true;
  bool     usarMqtt = true;
  uint16_t pubMs    = INTERVALO_PUBLICACAO_MS;
  uint8_t  lote     = IMU_LOTE;
};
Config cfg;
Preferences prefsCfg;

void carregarConfig() {
  prefsCfg.begin("cfg", false);
  cfg.usarGps  = prefsCfg.getBool("gps", true);
  cfg.usarImu  = prefsCfg.getBool("imu", true);
  cfg.usarAmb  = prefsCfg.getBool("amb", true);
  cfg.usarMqtt = prefsCfg.getBool("mqtt", true);
  cfg.pubMs    = prefsCfg.getUShort("pubms", INTERVALO_PUBLICACAO_MS);
  cfg.lote     = prefsCfg.getUChar("lote", IMU_LOTE);
  // Limites de sanidade: o app e a fonte, mas um valor absurdo gravado por
  // engano deixaria a estacao inutil ate alguem regravar o firmware.
  if (cfg.pubMs < 200)   cfg.pubMs = 200;
  if (cfg.pubMs > 60000) cfg.pubMs = 60000;
  if (cfg.lote < 1)         cfg.lote = 1;
  if (cfg.lote > IMU_LOTE)  cfg.lote = IMU_LOTE;   // o array tem esse tamanho
}

void salvarConfig() {
  prefsCfg.putBool("gps", cfg.usarGps);
  prefsCfg.putBool("imu", cfg.usarImu);
  prefsCfg.putBool("amb", cfg.usarAmb);
  prefsCfg.putBool("mqtt", cfg.usarMqtt);
  prefsCfg.putUShort("pubms", cfg.pubMs);
  prefsCfg.putUChar("lote", cfg.lote);
}

String configJson() {
  char j[224];
  snprintf(j, sizeof(j),
           "{\"dev\":\"%s\",\"gps\":%s,\"imu\":%s,\"amb\":%s,\"mqtt\":%s,"
           "\"pubms\":%u,\"lote\":%u,\"lotemax\":%u}",
           DEVICE_ID,
           cfg.usarGps ? "true" : "false", cfg.usarImu ? "true" : "false",
           cfg.usarAmb ? "true" : "false", cfg.usarMqtt ? "true" : "false",
           (unsigned)cfg.pubMs, (unsigned)cfg.lote, (unsigned)IMU_LOTE);
  return String(j);
}

// Extratores minimos de JSON. ArduinoJson seria uma segunda dependencia
// externa para um schema fixo de seis campos - o mesmo criterio que o resto do
// firmware ja usa ao montar JSON na mao.
bool extrairBool(const String &j, const char *chave, bool atual) {
  int i = j.indexOf(String("\"") + chave + "\"");
  if (i < 0) return atual;
  int c = j.indexOf(':', i);
  if (c < 0) return atual;
  while (c + 1 < (int)j.length() && j[c + 1] == ' ') c++;
  return j.startsWith("true", c + 1);
}

long extrairInt(const String &j, const char *chave, long atual) {
  int i = j.indexOf(String("\"") + chave + "\"");
  if (i < 0) return atual;
  int c = j.indexOf(':', i);
  if (c < 0) return atual;
  return j.substring(c + 1).toInt();
}

double extrairDouble(const String &j, const char *chave, double atual) {
  int i = j.indexOf(String("\"") + chave + "\"");
  if (i < 0) return atual;
  int c = j.indexOf(':', i);
  if (c < 0) return atual;
  return j.substring(c + 1).toDouble();
}

// ---------------------------------------------------------------------------
// Localizacao vinda do telefone (a fonte de posicao atual). O ESP so recebe e
// re-emite como `pos`, com o proprio seq - unificando a linha de tempo.
// ---------------------------------------------------------------------------
double   telLat = 0.0, telLon = 0.0;
float    telVel = 0.0f, telRumo = 0.0f;
uint32_t telQuandoMs = 0;   // millis() da ultima escrita; 0 = nunca

void aplicarLocalizacaoPendente() {
  String j = BleEstacao::localizacaoPendente();
  if (j.length() == 0) return;
  telLat = extrairDouble(j, "lat", telLat);
  telLon = extrairDouble(j, "lon", telLon);
  telVel = (float)extrairDouble(j, "vel", telVel);
  telRumo = (float)extrairDouble(j, "rumo", telRumo);
  telQuandoMs = millis();
}

// Posicao do telefone e "fresca" se chegou nos ultimos 8 s. Depois disso,
// para de ser publicada como pos - melhor sem posicao do que com uma velha.
bool telFresca() {
  return telQuandoMs != 0 && (millis() - telQuandoMs) < 8000;
}

/** Aplica o que o app escreveu. Roda no loop(), nunca na task do BLE. */
void aplicarConfigPendente() {
  String j = BleEstacao::configPendente();
  if (j.length() == 0) return;

  cfg.usarGps  = extrairBool(j, "gps", cfg.usarGps);
  cfg.usarImu  = extrairBool(j, "imu", cfg.usarImu);
  cfg.usarAmb  = extrairBool(j, "amb", cfg.usarAmb);
  cfg.usarMqtt = extrairBool(j, "mqtt", cfg.usarMqtt);
  cfg.pubMs    = (uint16_t)constrain(extrairInt(j, "pubms", cfg.pubMs), 200, 60000);
  cfg.lote     = (uint8_t)constrain(extrairInt(j, "lote", cfg.lote), 1, IMU_LOTE);

  salvarConfig();
  // Devolve o estado REAL depois dos limites, nao o que foi pedido: o app tem
  // de mostrar o que a estacao realmente ficou, nao ecoar o proprio pedido.
  BleEstacao::publicarCfg(configJson());
  Serial.print(F("cfg aplicada: "));
  Serial.println(configJson());
}

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
  // cfg.lote e limitado a IMU_LOTE no carregarConfig/aplicarConfigPendente, que
  // e o tamanho real do array - o limite aqui nao pode vir so da configuracao.
  if (loteN < cfg.lote) loteImu[loteN++] = imu;
}

// ---------------------------------------------------------------------------
// MQTT
// ---------------------------------------------------------------------------
bool mqttLigado() {
  return MQTT_HOST[0] != '\0' && cfg.usarMqtt;
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

// O BLE e notificado ANTES do teste de MQTT em todas as funcoes abaixo. Nao e
// detalhe de ordem: em campo o cenario normal e nao haver Wi-Fi nenhum, e era
// o `return` por MQTT desconectado que deixaria o app do celular sem dado
// exatamente quando ele e a unica janela para a estacao.
// Chamada a cada passagem do loop() - so um analogRead() e algumas comparacoes,
// custa quase nada. So faz conta pesada (log10/pow) quando uma subjanela de
// SOM_SUBJANELA_MS fecha, e so entao acumula na soma de potencia do ciclo.
//
// POR QUE NAO BLOQUEAR feito o sketch de teste (while(millis()<fim)): esse
// loop() tem o escalonador do RS485 e o BleEstacao::manter() rodando na mesma
// volta, ambos com timing proprio. Um bloqueio de 20 ms aqui atrasaria os dois.
void amostrarSom() {
  uint16_t v = analogRead(PINO_KY038_AO);
  if (v < somMinSubjanela) somMinSubjanela = v;
  if (v > somMaxSubjanela) somMaxSubjanela = v;

  if (millis() < somProximaSubjanela) return;
  somProximaSubjanela = millis() + SOM_SUBJANELA_MS;

  uint16_t amplitude = somMaxSubjanela - somMinSubjanela;
  if (amplitude < 1) amplitude = 1;  // log10(0) nao existe
  float db = SOM_COEF_A * log10f(amplitude) + SOM_COEF_B;
  if (db < 0) db = 0;

  somSomaPotencia += pow(10.0, db / 10.0);
  somAmostras++;

  somMinSubjanela = 4095;
  somMaxSubjanela = 0;
}

// Fecha o Leq do ciclo (media de POTENCIA, nao do dB direto - ver
// src/media_ruido_ky038.py) e zera o acumulador para o proximo.
float fecharLeq() {
  float leq = somAmostras > 0 ? 10.0f * log10f(somSomaPotencia / somAmostras) : 0.0f;
  somSomaPotencia = 0.0;
  somAmostras     = 0;
  return leq;
}

// Le um termistor NTC calibrado (KY-028 ou KY-013) - so um analogRead(), sem
// janela, ao contrario do som: temperatura nao oscila rapido o bastante pra
// precisar de envelope.
float lerTemperaturaNTC(uint8_t pino, float calA, float calB) {
  uint16_t adc = analogRead(pino);
  float x = adc / 4095.0f;
  x = constrain(x, 0.0001f, 0.9999f);
  float ratio = (1.0f - x) / x;
  float tempK = 1.0f / (calA * logf(ratio) + calB);
  return tempK - 273.15f;
}

// GY-302 / BH1750: driver minimo por I2C, sem biblioteca externa (ver
// firmware/teste_gy302/). iniciarBh1750() e chamado uma vez no setup().
bool iniciarBh1750() {
  Wire.begin(PINO_SDA, PINO_SCL);

  Wire.beginTransmission(BH1750_ENDERECO);
  Wire.write(0x01);  // power on
  if (Wire.endTransmission() != 0) return false;

  Wire.beginTransmission(BH1750_ENDERECO);
  Wire.write(BH1750_CONTINUO_H_RES);
  if (Wire.endTransmission() != 0) return false;

  delay(180);  // primeira conversao no modo H-res
  return true;
}

bool lerBh1750(float &luxSaida) {
  if (Wire.requestFrom((int)BH1750_ENDERECO, 2) != 2) return false;
  uint16_t bruto = (uint16_t)Wire.read() << 8 | Wire.read();
  luxSaida = bruto / 1.2f;  // fator fixo do datasheet no modo H-res
  return true;
}

void publicarAmbiente() {
  if (!leituraValida) return;
  float dB   = fecharLeq();
  float t028 = lerTemperaturaNTC(PINO_KY028_AO, KY028_CAL_A, KY028_CAL_B);
  float t013 = lerTemperaturaNTC(PINO_KY013_AO, KY013_CAL_A, KY013_CAL_B);
  float lux  = -1.0f;  // -1 = sem leitura valida (sensor ausente ou falha)
  if (bh1750Disponivel) lerBh1750(lux);
  char json[224];
  snprintf(json, sizeof(json),
           "{\"seq\":%lu,\"temp\":%.1f,\"umid\":%.1f,\"db\":%.1f,\"t28\":%.1f,\"t13\":%.1f,\"lux\":%.1f}",
           (unsigned long)seqAmb++, temperatura, umidade, dB, t028, t013, lux);
  BleEstacao::publicarAmb(json);
  if (!mqtt.connected()) return;
  if (mqtt.publish(topico("amb").c_str(), json)) publicadas++;
  else                                           falhasPublicacao++;
}

void publicarPosicao() {
  char json[220];
  // Prioridade para a localizacao do TELEFONE: e a fonte de posicao atual. O
  // "src":"tel" deixa a base e o app saberem a origem. So cai para o GPS do
  // ESP se nao houver posicao fresca do telefone E o GPS do ESP tiver fix.
  if (telFresca()) {
    snprintf(json, sizeof(json),
             "{\"seq\":%lu,\"src\":\"tel\",\"fix\":true,\"lat\":%.7f,\"lon\":%.7f,"
             "\"vel\":%.2f,\"rumo\":%.1f}",
             (unsigned long)seqPos++, telLat, telLon, telVel, telRumo);
  } else if (posValida) {
    snprintf(json, sizeof(json),
             "{\"seq\":%lu,\"src\":\"esp\",\"fix\":%s,\"lat\":%.7f,\"lon\":%.7f,"
             "\"alt\":%.1f,\"vel\":%.2f,\"sat\":%u,\"hdop\":%.2f,\"rumo\":%.1f}",
             (unsigned long)seqPos++, pos.temFix() ? "true" : "false",
             pos.latitude, pos.longitude, pos.altitudeGpsM,
             pos.velocidadeKmh, pos.satelites, pos.hdop, pos.rumoGpsDeg);
  } else {
    return;
  }
  BleEstacao::publicarPos(json);
  if (!mqtt.connected()) return;
  if (mqtt.publish(topico("pos").c_str(), json)) publicadas++;
  else                                           falhasPublicacao++;
}

/**
 * Publica o lote de IMU com o dt REAL medido pelo esp_timer, nao o nominal.
 * O filtro de Kalman do servidor usa esse dt na predicao — o intervalo
 * nominal mente, porque cada transacao Modbus tem jitter proprio.
 */
void publicarImu() {
  if (loteN == 0) return;

  uint32_t agoraUs = (uint32_t)esp_timer_get_time();
  float dt = ((agoraUs - loteInicioUs) / 1e6f) / (float)loteN;
  if (dt <= 0.0f || dt >= 1.0f) { loteN = 0; return; }

  // O seq avanca aqui, fora dos dois ramos: e o contador do LOTE, nao da
  // entrega. Se so incrementasse no ramo do MQTT, uma sessao de campo sem rede
  // mandaria todos os pedacos BLE com seq 0 e o celular nao teria como ordenar
  // nem detectar furo. Avancando sempre, um salto no seq denuncia exatamente o
  // que nao foi entregue - que e para o que ele serve.
  const uint32_t seqLote = seqImu++;

  // --- BLE: lote CRU, fatiado ----------------------------------------------
  // Enquanto o ESP32 nao tem cartao SD, o celular e a custodia da coleta - a
  // unica copia do cru ate chegar na base. Por isso vai o dado inteiro e nao um
  // resumo: media nao se reprocessa (ver ble_estacao.h e docs/04-telemetria.md).
  //
  // Cada pedaco e um envelope completo e independente, com o mesmo `seq` do
  // lote mais `p`/`np`. O app grava cada um direto na fila; se um se perder, o
  // furo fica visivel no par (seq, p) sem estragar os outros.
  if (BleEstacao::conectado()) {
    const uint8_t POR_PEDACO = 3;   // 3 x ~120 B + cabecalho ~= 400 B < 512
    const uint8_t pedacos = (loteN + POR_PEDACO - 1) / POR_PEDACO;
    for (uint8_t p = 0; p < pedacos; p++) {
      String j = "{\"seq\":" + String(seqLote) + ",\"dt\":" + String(dt, 4)
               + ",\"p\":" + String(p) + ",\"np\":" + String(pedacos) + ",\"m\":[";
      for (uint8_t k = 0; k < POR_PEDACO; k++) {
        uint8_t i = p * POR_PEDACO + k;
        if (i >= loteN) break;
        char amostra[150];
        snprintf(amostra, sizeof(amostra),
                 "%s{\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,"
                 "\"gx\":%.2f,\"gy\":%.2f,\"gz\":%.2f,"
                 "\"roll\":%.2f,\"pitch\":%.2f,\"yaw\":%.2f}",
                 k ? "," : "",
                 loteImu[i].accX, loteImu[i].accY, loteImu[i].accZ,
                 loteImu[i].gyroX, loteImu[i].gyroY, loteImu[i].gyroZ,
                 loteImu[i].roll, loteImu[i].pitch, loteImu[i].yaw);
        j += amostra;
      }
      j += "]}";
      BleEstacao::publicarImu(j);
      // A fila de transmissao do Bluedroid e curta: quatro notificacoes
      // seguidas sem respiro sao descartadas em silencio, e o sintoma seria
      // "o app so recebe o primeiro pedaco".
      delay(12);
    }
  }

  // --- MQTT: lote cru ------------------------------------------------------
  if (mqtt.connected()) {
    // Monta o array manualmente: ArduinoJson seria outra dependencia, e o
    // formato aqui e fixo.
    String json = "{\"seq\":" + String(seqLote) + ",\"dt\":" + String(dt, 4) + ",\"m\":[";
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
  }

  // Fora do if do MQTT: sem isto, uma sessao sem rede encheria o lote uma vez
  // e congelaria nele para sempre, porque lerImu() so acumula ate IMU_LOTE.
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

  // BLE sobe ANTES do Wi-Fi, e de proposito fora dos dois ramos abaixo: o
  // cenario do app de campo e justamente nao haver rede. Se o BLE dependesse do
  // Wi-Fi ter conectado, uma estacao longe do roteador cairia no portal de
  // configuracao e ficaria invisivel para o celular - que e quando o app mais
  // faz falta.
  carregarConfig();          // antes do BLE: o app le a cfg assim que conecta
  BleEstacao::iniciar(DEVICE_ID);
  BleEstacao::publicarCfg(configJson());

  Serial.println(F("GY-302: iniciando BH1750..."));
  bh1750Disponivel = iniciarBh1750();
  Serial.println(bh1750Disponivel ? F("  respondeu.") : F("  sem resposta - seguindo sem lux."));

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
  // Antes do desvio do modo de configuracao: o anuncio BLE tem de voltar
  // mesmo com a estacao sem Wi-Fi, que e quando o app do celular importa.
  BleEstacao::manter();
  aplicarConfigPendente();
  aplicarLocalizacaoPendente();

  if (modoConfig) {
    dns.processNextRequest();
    servidor.handleClient();
    return;
  }

  servidor.handleClient();
  amostrarSom();  // barato (1 analogRead por volta) - ver o comentario na definicao

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
    // Sensor desligado apenas cede o slot; o ciclo nao e reorganizado. Dar os
    // slots livres do GPS para o IMU aumentaria a taxa dele, e ai o `dt` do
    // lote mudaria junto com a configuracao - efeito colateral escondido num
    // botao que so dizia "desligar GPS".
    switch (slot) {
      case SLOT_AMBIENTE: if (cfg.usarAmb) lerAmbiente(); break;
      case SLOT_GPS_A:
      case SLOT_GPS_B:    if (cfg.usarGps) lerGps();      break;
      default:            if (cfg.usarImu) lerImu();      break;
    }
    slot = (slot + 1) % SLOTS_CICLO;
  }

  // Publicacao e relato, uma vez por segundo.
  static uint32_t proximaPublicacao = 0;
  if (millis() >= proximaPublicacao) {
    proximaPublicacao = millis() + cfg.pubMs;

    // Status vai primeiro: e a caracteristica que responde "a estacao esta
    // viva e os sensores respondem?", e ela precisa chegar mesmo quando as
    // outras nao tem o que publicar - GPS sem fix, ambiente sem leitura.
    if (BleEstacao::conectado()) {
      char st[176];
      snprintf(st, sizeof(st),
               "{\"dev\":\"%s\",\"up\":%lu,\"wifi\":%s,\"mqtt\":%s,"
               "\"amb\":%s,\"gps\":%s,\"fix\":%s,\"sat\":%u}",
               DEVICE_ID, (unsigned long)(millis() / 1000),
               WiFi.status() == WL_CONNECTED ? "true" : "false",
               mqtt.connected() ? "true" : "false",
               leituraValida ? "true" : "false",
               posValida ? "true" : "false",
               (posValida && pos.temFix()) ? "true" : "false",
               posValida ? pos.satelites : 0);
      BleEstacao::publicarStatus(st);
    }

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

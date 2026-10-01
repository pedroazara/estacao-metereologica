/*
 * Teste de bancada - sensores baratos (KY-013, KY-028, KY-038, GY-302).
 *
 * NAO e o firmware definitivo: e para testar cada modulo isolado, ver os
 * numeros crus no monitor serial e calibrar os dois termistores ANTES de
 * decidir como eles entram no firmware de verdade. Nada aqui sobe pra
 * producao ainda.
 *
 * Sensores:
 *   KY-013  termistor NTC (so saida analogica)              -> calibrar
 *   KY-028  termistor NTC + comparador LM393 (AO e DO)       -> calibrar
 *   KY-038  microfone + comparador LM393 (AO e DO)           -> so referencia
 *   GY-302  BH1750, luminosidade por I2C (0-65535 lux)       -> pronto de fabrica
 *
 * Para calibrar os termistores uso como referencia o sensor ambiental RS485
 * ja validado na bancada (ver ../estacao_esp32/README e ambiente_rs485.h) -
 * mesmo driver, copiado para esta pasta. Os dois termistores sao NTC
 * genericos e o desenho do divisor de tensao varia de lote pra lote destes
 * clones chineses, entao o sketch imprime as DUAS hipoteses de divisor (ver
 * calcularTemperaturaNTC()) lado a lado com a leitura do RS485: voce olha
 * qual das duas colunas acompanha o valor de referencia e fixa
 * DIVISOR_INVERTIDO_* de acordo. So depois disso os graus impressos valem
 * alguma coisa.
 *
 * ATENCAO A TENSAO: alimentar os 4 modulos em 3V3, nao em 5V. O pino AO
 * desses modulos e a propria tensao do divisor/comparador - em 5V ele passa
 * dos 3,3 V que o ADC do ESP32 aguenta (nao tolerante a 5V) e pode danificar
 * o pino. Em 3V3 o LM393 e o divisor NTC funcionam normalmente.
 *
 * Ligacao:
 *   RS485 (sensor ambiental, ja calibrado) - ver estacao_esp32/README:
 *     A/B do transceptor -> A/B do sensor
 *     RO -> GPIO16 (RX2)   DI -> GPIO17 (TX2)   DE+RE -> GPIO4
 *   KY-013:  AO -> GPIO34                    VCC 3V3, GND
 *   KY-028:  AO -> GPIO35   DO -> GPIO25      VCC 3V3, GND
 *   KY-038:  AO -> GPIO32   DO -> GPIO33      VCC 3V3, GND
 *   GY-302:  SDA -> GPIO21  SCL -> GPIO22     VCC 3V3, GND, ADDR -> GND (endereco 0x23)
 *
 * Comandos pelo monitor serial (115200 baud):
 *   espaco -> forca uma leitura extra na hora (fora do intervalo)
 */

#include <Wire.h>
#include "ambiente_rs485.h"

// -- RS485 (referencia de temperatura, ja calibrada) -------------------------
static const int8_t  PINO_RX = 16;
static const int8_t  PINO_TX = 17;
static const int8_t  PINO_DE = 4;
static const uint32_t BAUD_RS485 = 9600;

AmbienteRS485 ambiente(Serial2, PINO_DE, ENDERECO_AMBIENTAL_PADRAO);
bool rs485Disponivel = false;  // false = segue testando os outros sensores sem travar no RS485

// -- KY-013 / KY-028 (termistores NTC) ----------------------------------------
static const uint8_t PINO_KY013_AO = 34;
static const uint8_t PINO_KY028_AO = 35;
static const uint8_t PINO_KY028_DO = 25;

// Constantes do termistor - genericas ate calibrar. Datasheet costuma trazer
// B25/50; sem ele, 3950 e o valor mais comum nesses clones de 10k.
static const float NTC_R_FIXO = 10000.0f;  // resistor fixo do divisor, ohms
static const float NTC_R0     = 10000.0f;  // resistencia nominal em 25 C
static const float NTC_BETA   = 3950.0f;
static const float NTC_T0_K   = 298.15f;   // 25 C em kelvin

// -- KY-038 (microfone) -------------------------------------------------------
static const uint8_t PINO_KY038_AO = 32;
static const uint8_t PINO_KY038_DO = 33;
static const uint16_t SOM_JANELA_MS = 50;  // tempo de amostragem pra pegar o pico do sinal AC

// -- GY-302 / BH1750 (luminosidade, I2C) --------------------------------------
static const uint8_t BH1750_ENDERECO = 0x23;  // ADDR em GND; 0x5C se ADDR em VCC
static const uint8_t BH1750_CONTINUO_H_RES = 0x10;  // 1 lx/contagem, ~120 ms de conversao
bool bh1750Disponivel = false;

// -- ritmo ---------------------------------------------------------------------
static const uint32_t INTERVALO_MS = 1000;

// ---------------------------------------------------------------------------
// NTC: converte ADC (0..4095, 0..3V3) em temperatura, nas duas hipoteses de
// divisor. So uma das duas bate com a referencia - a outra e pra descartar.
// ---------------------------------------------------------------------------
struct LeituraNTC {
  float tempTermistorNoTopo;   // hipotese A: NTC entre AO e VCC, fixo entre AO e GND
  float tempTermistorEmbaixo;  // hipotese B: NTC entre AO e GND, fixo entre AO e VCC
};

float resistenciaParaTemperaturaC(float resistenciaOhms) {
  float tempK = 1.0f / (1.0f / NTC_T0_K + logf(resistenciaOhms / NTC_R0) / NTC_BETA);
  return tempK - 273.15f;
}

LeituraNTC calcularTemperaturaNTC(uint16_t adc) {
  LeituraNTC saida;
  float x = adc / 4095.0f;  // fracao de Vcc no pino AO
  x = constrain(x, 0.0001f, 0.9999f);  // evita divisao por zero nas pontas

  float rTopo    = NTC_R_FIXO * (1.0f - x) / x;  // hipotese A
  float rEmbaixo = NTC_R_FIXO * x / (1.0f - x);  // hipotese B

  saida.tempTermistorNoTopo  = resistenciaParaTemperaturaC(rTopo);
  saida.tempTermistorEmbaixo = resistenciaParaTemperaturaC(rEmbaixo);
  return saida;
}

// ---------------------------------------------------------------------------
// KY-038: amostra alguns ms pra pegar a amplitude do sinal AC do microfone.
// Um analogRead() unico nao diz nada - o sinal oscila rapido.
// ---------------------------------------------------------------------------
struct LeituraSom {
  uint16_t minimo;
  uint16_t maximo;
  uint16_t amplitude;
  bool     estourou;  // DO do LM393 (limiar ajustado no potenciometro do modulo)
};

LeituraSom lerSom() {
  LeituraSom saida;
  saida.minimo = 4095;
  saida.maximo = 0;

  uint32_t fim = millis() + SOM_JANELA_MS;
  while (millis() < fim) {
    uint16_t v = analogRead(PINO_KY038_AO);
    if (v < saida.minimo) saida.minimo = v;
    if (v > saida.maximo) saida.maximo = v;
  }
  saida.amplitude = saida.maximo - saida.minimo;
  saida.estourou = (digitalRead(PINO_KY038_DO) == LOW);  // LM393 costuma puxar pra LOW ao estourar o limiar
  return saida;
}

// ---------------------------------------------------------------------------
// GY-302 / BH1750: driver minimo por I2C, sem biblioteca externa - so 3
// comandos (ligar, modo continuo, ler 2 bytes) e conversao unica pro dado.
// ---------------------------------------------------------------------------
bool bh1750Iniciar() {
  Wire.begin(21, 22);

  Wire.beginTransmission(BH1750_ENDERECO);
  Wire.write(0x01);  // power on
  if (Wire.endTransmission() != 0) return false;

  Wire.beginTransmission(BH1750_ENDERECO);
  Wire.write(BH1750_CONTINUO_H_RES);
  if (Wire.endTransmission() != 0) return false;

  delay(180);  // primeira conversao no modo H-res
  return true;
}

bool bh1750Ler(float &luxSaida) {
  if (Wire.requestFrom((int)BH1750_ENDERECO, 2) != 2) return false;
  uint16_t bruto = (uint16_t)Wire.read() << 8 | Wire.read();
  luxSaida = bruto / 1.2f;  // fator fixo do datasheet no modo H-res
  return true;
}

// ---------------------------------------------------------------------------
void lerETudo() {
  Serial.println(F("----------------------------------------------------------"));

  // -- referencia RS485 --
  if (rs485Disponivel) {
    AmbienteRS485Leitura amb;
    if (ambiente.ler(amb)) {
      Serial.print(F("RS485 (referencia): temp "));
      Serial.print(amb.temperaturaC, 1);
      Serial.print(F(" C   umid "));
      Serial.print(amb.umidadeRH, 1);
      Serial.println(F(" %RH"));
    } else {
      Serial.println(F("RS485 (referencia): falha na leitura"));
    }
  } else {
    Serial.println(F("RS485 (referencia): sensor nao respondeu no boot - comparar sem referencia"));
  }

  // -- KY-013 --
  uint16_t adc013 = analogRead(PINO_KY013_AO);
  LeituraNTC t013 = calcularTemperaturaNTC(adc013);
  Serial.print(F("KY-013 : adc "));
  Serial.print(adc013);
  Serial.print(F("   hipotese A(topo) "));
  Serial.print(t013.tempTermistorNoTopo, 1);
  Serial.print(F(" C   hipotese B(embaixo) "));
  Serial.print(t013.tempTermistorEmbaixo, 1);
  Serial.println(F(" C"));

  // -- KY-028 --
  uint16_t adc028 = analogRead(PINO_KY028_AO);
  LeituraNTC t028 = calcularTemperaturaNTC(adc028);
  bool do028 = (digitalRead(PINO_KY028_DO) == LOW);
  Serial.print(F("KY-028 : adc "));
  Serial.print(adc028);
  Serial.print(F("   hipotese A(topo) "));
  Serial.print(t028.tempTermistorNoTopo, 1);
  Serial.print(F(" C   hipotese B(embaixo) "));
  Serial.print(t028.tempTermistorEmbaixo, 1);
  Serial.print(F(" C   DO(limiar) "));
  Serial.println(do028 ? F("estourou") : F("normal"));

  // -- KY-038 --
  LeituraSom som = lerSom();
  Serial.print(F("KY-038 : adc min/max "));
  Serial.print(som.minimo);
  Serial.print('/');
  Serial.print(som.maximo);
  Serial.print(F("   amplitude "));
  Serial.print(som.amplitude);
  Serial.print(F("   DO(limiar) "));
  Serial.println(som.estourou ? F("estourou") : F("normal"));

  // -- GY-302 --
  if (bh1750Disponivel) {
    float lux;
    if (bh1750Ler(lux)) {
      Serial.print(F("GY-302 : "));
      Serial.print(lux, 1);
      Serial.println(F(" lx"));
    } else {
      Serial.println(F("GY-302 : falha na leitura"));
    }
  } else {
    Serial.println(F("GY-302 : nao respondeu no boot - conferir SDA/SCL/ADDR/alimentacao"));
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println(F("Teste de bancada - KY-013, KY-028, KY-038, GY-302"));
  Serial.println(F("(nao e o firmware definitivo - so calibracao e conferencia de sinal)"));

  // RS485 - mesma inicializacao que WTGAHRS3::iniciar() faz, mas sem
  // depender daquele driver: aqui so o sensor ambiental esta no barramento.
  if (PINO_DE >= 0) {
    pinMode(PINO_DE, OUTPUT);
    digitalWrite(PINO_DE, LOW);
  }
  Serial2.begin(BAUD_RS485, SERIAL_8N1, PINO_RX, PINO_TX);
  delay(50);

  Serial.print(F("RS485: procurando sensor ambiental em 0x"));
  Serial.print(ENDERECO_AMBIENTAL_PADRAO, HEX);
  Serial.println(F("..."));
  rs485Disponivel = ambiente.estaRespondendo();
  Serial.println(rs485Disponivel ? F("  respondeu.") : F("  sem resposta - seguindo sem referencia."));

  pinMode(PINO_KY028_DO, INPUT);
  pinMode(PINO_KY038_DO, INPUT);
  analogReadResolution(12);  // 0..4095, padrao do ESP32 mas explicito

  Serial.println(F("GY-302: iniciando BH1750..."));
  bh1750Disponivel = bh1750Iniciar();
  Serial.println(bh1750Disponivel ? F("  respondeu.") : F("  sem resposta - conferir fiacao I2C."));

  Serial.println();
  Serial.println(F("Para calibrar os termistores: compare as colunas 'hipotese A' e"));
  Serial.println(F("'hipotese B' de cada modulo com a temperatura do RS485. A que"));
  Serial.println(F("acompanhar o RS485 e a certa - a outra e artefato do calculo."));
}

void loop() {
  if (Serial.available() && Serial.read() == ' ') lerETudo();

  static uint32_t proxima = 0;
  if (millis() >= proxima) {
    lerETudo();
    proxima = millis() + INTERVALO_MS;
  }
}

/*
 * Teste de bancada - KY-013 (termistor NTC simples, so AO - sem comparador),
 * com o sensor ambiental RS485 como referencia pra calibrar.
 *
 * ATENCAO A TENSAO: alimentar o KY-013 em 3V3, nao em 5V - o AO e a propria
 * tensao do divisor, e o ADC do ESP32 nao e tolerante a 5V.
 *
 * Ligacao:
 *   KY-013:  AO -> GPIO39 (rotulado "SN"/"VN" na serigrafia de varias placas ESP32)
 *            VCC 3V3, GND
 *   RS485 (referencia): RO -> GPIO16 (RX2)   DI -> GPIO17 (TX2)   DE+RE -> GPIO4
 *   (DE+RE = -1 se o transceptor for automatico, sem esse pino)
 *
 * PINO GPIO39: tentamos GPIO33 e depois GPIO34 antes, mas nenhum dos dois
 * respondeu nesta placa (fiacao/protoboard, nao os pinos em si - o 34 ja
 * tinha funcionado com este mesmo sensor antes). NAO usar pino ADC2 (26 e
 * outros) - conflita com o Wi-Fi.
 *
 * CALIBRADO em 03/09/2026 com src/calibrar_ky028.py (mesma matematica do
 * KY-028), 2 pontos: ambiente (23,9C) e mao fechada (~30,8C, reaproveitando
 * a referencia medida com o RS485 na calibracao do KY-028 - aproximacao, nao
 * foi medido com os dois sensores juntos desta vez). Se recalibrar, atualiza
 * CAL_A/CAL_B abaixo.
 */

#include "ambiente_rs485.h"

// -- RS485 (referencia) -------------------------------------------------------
static const int8_t  PINO_RX = 16;
static const int8_t  PINO_TX = 17;
static const int8_t  PINO_DE = -1;
static const uint32_t BAUD_RS485 = 9600;

AmbienteRS485 ambiente(Serial2, PINO_DE, ENDERECO_AMBIENTAL_PADRAO);
bool rs485Disponivel = false;

// -- KY-013 --------------------------------------------------------------------
static const uint8_t PINO_KY013_AO = 39;

// Coeficientes de src/calibrar_ky028.py: 1/T = CAL_A*ln((1-x)/x) + CAL_B
static const float CAL_A = -0.000289f;
static const float CAL_B = 0.004235f;

static const uint32_t INTERVALO_MS = 1000;

float lerTemperaturaC() {
  uint16_t adc = analogRead(PINO_KY013_AO);
  float x = adc / 4095.0f;
  x = constrain(x, 0.0001f, 0.9999f);
  float ratio = (1.0f - x) / x;
  float tempK = 1.0f / (CAL_A * logf(ratio) + CAL_B);
  return tempK - 273.15f;
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println(F("Teste de bancada - KY-013 com referencia RS485"));

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
  Serial.println();
}

void loop() {
  // -- RS485 (referencia) --
  if (rs485Disponivel) {
    AmbienteRS485Leitura amb;
    if (ambiente.ler(amb)) {
      Serial.print(F("RS485 (referencia): "));
      Serial.print(amb.temperaturaC, 1);
      Serial.print(F(" C   umid "));
      Serial.print(amb.umidadeRH, 1);
      Serial.print(F(" %RH   |   "));
    } else {
      Serial.print(F("RS485 (referencia): falha na leitura   |   "));
    }
  } else {
    Serial.print(F("RS485 (referencia): sem resposta   |   "));
  }

  // -- KY-013 --
  float tempC = lerTemperaturaC();
  Serial.print(F("KY-013 (calibrado) = "));
  Serial.print(tempC, 1);
  Serial.println(F(" C"));

  delay(INTERVALO_MS);
}

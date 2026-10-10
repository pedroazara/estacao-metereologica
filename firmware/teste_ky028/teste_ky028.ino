/*
 * Teste de bancada - KY-028 (termistor NTC + comparador), so o AO, com o
 * sensor ambiental RS485 como referencia pra calibrar.
 *
 * ATENCAO A TENSAO: alimentar o KY-028 em 3V3, nao em 5V - o AO e a propria
 * tensao do divisor, e o ADC do ESP32 nao e tolerante a 5V.
 *
 * Ligacao:
 *   KY-028:  AO -> GPIO35                       VCC 3V3, GND
 *   RS485 (referencia): RO -> GPIO16 (RX2)   DI -> GPIO17 (TX2)   DE+RE -> GPIO4
 *
 * CALIBRADO em 03/09/2026 com o src/calibrar_ky028.py, 2 pontos (23C e 31C)
 * contra o RS485. Se recalibrar (ex: com um terceiro ponto pra validar, ou
 * trocar de modulo), atualiza CAL_A/CAL_B abaixo com a saida do script.
 */

#include "ambiente_rs485.h"

// -- RS485 (referencia) -------------------------------------------------------
static const int8_t  PINO_RX = 16;
static const int8_t  PINO_TX = 17;
static const int8_t  PINO_DE = -1;
static const uint32_t BAUD_RS485 = 9600;

AmbienteRS485 ambiente(Serial2, PINO_DE, ENDERECO_AMBIENTAL_PADRAO);
bool rs485Disponivel = false;

// -- KY-028 --------------------------------------------------------------------
static const uint8_t PINO_KY028_AO = 35;

// Coeficientes de src/calibrar_ky028.py: 1/T = CAL_A*ln((1-x)/x) + CAL_B
static const float CAL_A = -0.000196f;
static const float CAL_B = 0.003763f;

static const uint32_t INTERVALO_MS = 1000;

float lerTemperaturaC() {
  uint16_t adc = analogRead(PINO_KY028_AO);
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
  Serial.println(F("Teste de bancada - KY-028 com referencia RS485"));

  // Mesma inicializacao que WTGAHRS3::iniciar() faz, sem depender daquele
  // driver: aqui so o sensor ambiental esta no barramento.
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

  // -- KY-028 --
  float tempC = lerTemperaturaC();
  Serial.print(F("KY-028 (calibrado) = "));
  Serial.print(tempC, 1);
  Serial.println(F(" C"));

  delay(INTERVALO_MS);
}

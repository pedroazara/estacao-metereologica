/*
 * Estacao Meteorologica Movel - firmware ESP32.
 *
 * Le posicao, IMU e ambiente do WitMotion WTGAHRS3-485 por Modbus RTU sobre
 * RS485 e imprime no monitor serial (115200 baud).
 *
 * Equivalente embarcado do src/ler_posicao.py do lado Python.
 * Ligacao e cuidados de nivel logico: ver ../README.md
 */

#include "wtgahrs3.h"

// -- pinos -------------------------------------------------------------------
static const int8_t PINO_RX = 16;  // RO do transceptor -> RX2 do ESP32
static const int8_t PINO_TX = 17;  // DI do transceptor <- TX2 do ESP32
static const int8_t PINO_DE = 4;   // DE+RE do transceptor; use -1 se for automatico

// -- sensor ------------------------------------------------------------------
static const uint32_t BAUD_SENSOR = 9600;              // padrao de fabrica
static const uint8_t  ENDERECO    = ENDERECO_PADRAO;   // 0x50

// -- ritmo de leitura --------------------------------------------------------
static const uint32_t INTERVALO_MS = 1000;

WTGAHRS3 sensor(Serial2, PINO_DE, ENDERECO);

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println(F("Estacao Meteorologica Movel - ESP32"));

  sensor.iniciar(BAUD_SENSOR, PINO_RX, PINO_TX);

  Serial.print(F("Procurando o sensor em 0x"));
  Serial.print(ENDERECO, HEX);
  Serial.print(F(" @ "));
  Serial.print(BAUD_SENSOR);
  Serial.println(F(" baud..."));

  while (!sensor.estaRespondendo()) {
    Serial.println(F("  sem resposta - conferir fiacao, alimentacao, A/B e o pino DE"));
    delay(1000);
  }
  Serial.println(F("  sensor respondendo."));
}

void loop() {
  Posicao pos;
  Imu imu;
  Ambiente amb;

  if (sensor.lerPosicao(pos)) {
    Serial.print(F("lat "));
    Serial.print(pos.latitude, 6);
    Serial.print(F("  lon "));
    Serial.print(pos.longitude, 6);
    Serial.print(F("  alt "));
    Serial.print(pos.altitudeGpsM, 1);
    Serial.print(F(" m  vel "));
    Serial.print(pos.velocidadeKmh, 1);
    Serial.print(F(" km/h  sat "));
    Serial.print(pos.satelites);
    Serial.print(F("  hdop "));
    Serial.print(pos.hdop, 2);
    Serial.println(pos.temFix() ? F("  [fix]") : F("  [sem fix]"));
  } else {
    Serial.println(F("falha ao ler posicao"));
  }

  if (sensor.lerImu(imu)) {
    Serial.print(F("  acc "));
    Serial.print(imu.accX, 2); Serial.print(' ');
    Serial.print(imu.accY, 2); Serial.print(' ');
    Serial.print(imu.accZ, 2); Serial.print(F(" g   rpy "));
    Serial.print(imu.roll, 1); Serial.print(' ');
    Serial.print(imu.pitch, 1); Serial.print(' ');
    Serial.println(imu.yaw, 1);
  }

  if (sensor.lerAmbiente(amb)) {
    Serial.print(F("  "));
    Serial.print(amb.pressaoPa);
    Serial.print(F(" Pa   alt.baro "));
    Serial.print(amb.altitudeBaroM, 1);
    Serial.print(F(" m   temp "));
    Serial.print(amb.temperaturaC, 2);
    Serial.println(F(" C"));
  }

  delay(INTERVALO_MS);
}

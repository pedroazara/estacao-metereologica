/*
 * Teste de bancada: ENS160 + AHT21 com ESP32 DevKit / WROOM-32.
 * Monitor Serial: 115200 baud. Sem Wi-Fi ou outros sensores.
 *
 * Ligacao: SDA -> GPIO21, SCL -> GPIO22, GND -> GND.
 * Se a placa expuser CS e ADD/MISO: CS -> 3V3 do ESP, ADD/MISO -> GND
 * seleciona I2C no endereco 0x52. INT fica sem ligacao.
 * Alimente o MODULO conforme sua especificacao. Use 3V3 somente se a
 * entrada indicada admitir 3,3 V. SDA/SCL devem trabalhar em 3,3 V.
 * Nao confunda a alimentacao da plaquinha com a do chip ENS160 (1,8 V).
 *
 * Bibliotecas no Gerenciador da IDE Arduino:
 * - SparkFun Indoor Air Quality Sensor - ENS160
 * - Adafruit AHTX0 (e dependencias Adafruit BusIO / Unified Sensor)
 *
 * ENS160: eCO2 ESTIMADO a partir de gases, TVOC e AQI-UBA (1 a 5).
 * AHT21: temperatura e umidade, usadas na compensacao do ENS160.
 * Validade: 0 normal, 1 aquecimento (~3 min), 2 inicio (~1 h), 3 invalida.
 * O fim da inicializacao so e persistido apos 24 h continuas de operacao.
 *
 * Referencias:
 * https://www.sciosense.com/wp-content/uploads/2023/12/ENS160-Datasheet.pdf
 * https://github.com/sparkfun/SparkFun_Indoor_Air_Quality_Sensor-ENS160_Arduino_Library
 * https://github.com/adafruit/Adafruit_AHTX0
 */

#include <Wire.h>
#include <Adafruit_AHTX0.h>
#include <SparkFun_ENS160.h>
#include <math.h>

constexpr int PINO_SDA = 21;
constexpr int PINO_SCL = 22;

Adafruit_AHTX0 aht;
SparkFun_ENS160 ens;
bool ahtPronto = false;
bool ensPronto = false;

void varrerI2C() {
  Serial.println("Procurando dispositivos I2C...");
  int encontrados = 0;
  for (uint8_t endereco = 0x08; endereco <= 0x77; endereco++) {
    Wire.beginTransmission(endereco);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  Encontrado: 0x%02X\n", endereco);
      encontrados++;
    }
  }
  if (encontrados == 0) Serial.println("  Nenhum dispositivo respondeu.");
  Serial.println("Esperado: AHT21 em 0x38 e ENS160 em 0x53 ou 0x52.");
}

const char *nomeValidade(uint8_t validade) {
  switch (validade) {
    case 0: return "normal";
    case 1: return "aquecimento (~3 min): leitura provisoria";
    case 2: return "inicializacao (~1 h): leitura provisoria";
    default: return "saida invalida";
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== Teste ENS160 + AHT21 ===");
  Serial.println("eCO2 e uma estimativa; nao e medicao direta de CO2.");

  Wire.begin(PINO_SDA, PINO_SCL);
  Wire.setClock(100000);
  varrerI2C();

  ahtPronto = aht.begin(&Wire);
  Serial.println(ahtPronto ? "AHT21 iniciado." : "AHT21 nao iniciou: confira a ligacao.");

  uint8_t enderecoEns = 0x53;
  ensPronto = ens.begin(Wire, enderecoEns);
  if (!ensPronto) {
    enderecoEns = 0x52;
    ensPronto = ens.begin(Wire, enderecoEns);
  }
  if (ensPronto) {
    ensPronto = ens.setOperatingMode(SFE_ENS160_IDLE);
    delay(20);
    if (ensPronto) ensPronto = ens.setOperatingMode(SFE_ENS160_STANDARD);
    delay(100);
    if (ensPronto) ensPronto = ens.getOperatingMode() == SFE_ENS160_STANDARD;
  }

  if (ensPronto) {
    Serial.printf("ENS160 iniciado em 0x%02X. Aguarde a estabilizacao.\n", enderecoEns);
  } else {
    Serial.println("ENS160 nao iniciou: confira alimentacao, SDA e SCL.");
  }
  Serial.println("Apos corrigir a ligacao com a placa desligada, reinicie o ESP32.");
}

void loop() {
  delay(2000);
  Serial.printf("\nTempo ligado: %lu s\n", millis() / 1000UL);

  if (ahtPronto) {
    sensors_event_t umidade, temperatura;
    if (aht.getEvent(&umidade, &temperatura) &&
        isfinite(temperatura.temperature) && isfinite(umidade.relative_humidity) &&
        umidade.relative_humidity >= 0 && umidade.relative_humidity <= 100) {
      Serial.printf("AHT21: %.1f C | %.1f %% UR\n",
                    temperatura.temperature, umidade.relative_humidity);
      if (ensPronto) {
        bool tempOk = ens.setTempCompensationCelsius(temperatura.temperature);
        bool umidOk = ens.setRHCompensationFloat(umidade.relative_humidity);
        if (!tempOk || !umidOk) Serial.println("Falha ao atualizar compensacao do ENS160.");
      }
    } else {
      Serial.println("Falha na leitura do AHT21; compensacao nao atualizada.");
    }
  } else {
    Serial.println("AHT21 indisponivel; ENS160 sem compensacao medida de T/UR.");
  }

  if (!ensPronto) {
    Serial.println("ENS160 indisponivel.");
    return;
  }

  // Leia o status uma vez para distinguir falta de dados de erro no I2C.
  uint8_t status = 0;
  if (ens.readRegisterRegion(SFE_ENS160_DEVICE_STATUS, &status, 1) != 0) {
    Serial.println("Falha de comunicacao com ENS160.");
    return;
  }
  uint8_t validade = (status >> 2) & 0x03;
  Serial.printf("ENS160: estado %u (%s)\n", validade, nomeValidade(validade));
  if ((status & 0x40) || !(status & 0x80)) {
    Serial.println("ENS160 com erro de operacao ou modo de medicao inativo.");
    return;
  }
  if (validade == 3) return;
  if (!(status & 0x02)) {
    Serial.println("Aguardando nova leitura do ENS160.");
    return;
  }

  // AQI, TVOC e eCO2 ocupam cinco bytes consecutivos; confira o erro de leitura.
  uint8_t dados[5] = {};
  if (ens.readRegisterRegion(SFE_ENS160_DATA_AQI, dados, sizeof(dados)) != 0) {
    Serial.println("Falha ao ler as medidas do ENS160.");
    return;
  }
  uint8_t aqi = dados[0] & 0x07;
  uint16_t tvoc = dados[1] | (uint16_t(dados[2]) << 8);
  uint16_t eco2 = dados[3] | (uint16_t(dados[4]) << 8);
  if (aqi < 1 || aqi > 5 || eco2 < 400) {
    Serial.println("ENS160 retornou valores fora da faixa esperada; aguarde e confira a ligacao.");
    return;
  }
  Serial.printf("eCO2 estimado: %u ppm | TVOC: %u ppb | AQI-UBA: %u/5\n",
                eco2, tvoc, aqi);
}

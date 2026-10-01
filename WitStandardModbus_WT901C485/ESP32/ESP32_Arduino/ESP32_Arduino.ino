#include <Arduino.h>
#include "wit_c_sdk.h"

/*
 * WT901C485 + ESP32
 *
 * Ligacoes para um conversor RS485 com direcao automatica:
 *   ESP32 GPIO 4 (TX) -> DI do conversor
 *   ESP32 GPIO 5 (RX) <- RO do conversor
 *   ESP32 GND          -> GND do conversor e da fonte do sensor
 *   Conversor A        -> A do WT901C485
 *   Conversor B        -> B do WT901C485
 *
 * O ESP32 trabalha com sinais de 3,3 V. Use um transceptor RS485 compativel
 * com 3,3 V (por exemplo, MAX3485) ou um conversor com adequacao de nivel.
 */

namespace {

constexpr uint32_t USB_BAUD_RATE = 115200;
constexpr int8_t SENSOR_RX_PIN = 16;
constexpr int8_t SENSOR_TX_PIN = 17;
constexpr uint8_t SENSOR_MODBUS_ADDRESS = 0x50;
constexpr uint32_t READ_INTERVAL_MS = 500;

// Para conversor automatico, deixe -1. Para um MAX3485 com DE e /RE unidos,
// informe aqui o GPIO ligado a esses pinos (por exemplo, 21).
constexpr int8_t RS485_DIRECTION_PIN = -1;

constexpr uint8_t ACC_UPDATE = 0x01;
constexpr uint8_t GYRO_UPDATE = 0x02;
constexpr uint8_t ANGLE_UPDATE = 0x04;
constexpr uint8_t MAG_UPDATE = 0x08;
constexpr uint8_t TEMP_UPDATE = 0x10;

const uint32_t SENSOR_BAUD_RATES[] = {
    4800, 9600, 19200, 38400, 57600,
    115200, 230400, 460800, 921600,
};

HardwareSerial SensorSerial(1);
volatile uint8_t dataUpdate = 0;
uint32_t sensorBaudRate = 9600;
uint32_t lastReadAt = 0;

void setRs485Transmit(bool transmit) {
  if (RS485_DIRECTION_PIN >= 0) {
    digitalWrite(RS485_DIRECTION_PIN, transmit ? HIGH : LOW);
  }
}

void beginSensorSerial(uint32_t baudRate) {
  SensorSerial.end();
  SensorSerial.begin(baudRate, SERIAL_8N1, SENSOR_RX_PIN, SENSOR_TX_PIN);
  sensorBaudRate = baudRate;
  setRs485Transmit(false);

  while (SensorSerial.available()) {
    SensorSerial.read();
  }
}

void sensorUartSend(uint8_t *data, uint32_t size) {
  setRs485Transmit(true);
  SensorSerial.write(data, size);
  SensorSerial.flush();
  setRs485Transmit(false);
}

void sdkDelay(uint16_t milliseconds) {
  delay(milliseconds);
}

void processSensorBytes() {
  while (SensorSerial.available() > 0) {
    WitSerialDataIn(static_cast<uint8_t>(SensorSerial.read()));
  }
}

void waitForSensorResponse(uint32_t timeoutMs) {
  const uint32_t startedAt = millis();
  while (millis() - startedAt < timeoutMs) {
    processSensorBytes();
    if (dataUpdate != 0) {
      return;
    }
    delay(1);
  }
}

void sensorDataUpdated(uint32_t firstRegister, uint32_t registerCount) {
  for (uint32_t index = 0; index < registerCount; ++index) {
    switch (firstRegister + index) {
      case AZ:
        dataUpdate |= ACC_UPDATE;
        break;
      case GZ:
        dataUpdate |= GYRO_UPDATE;
        break;
      case HZ:
        dataUpdate |= MAG_UPDATE;
        break;
      case Yaw:
        dataUpdate |= ANGLE_UPDATE;
        break;
      case TEMP:
        dataUpdate |= TEMP_UPDATE;
        break;
      default:
        break;
    }
  }
}

void showHelp() {
  Serial.println();
  Serial.println(F("Comandos do Monitor Serial:"));
  Serial.println(F("  a - iniciar calibracao do acelerometro"));
  Serial.println(F("  m - iniciar calibracao do magnetometro"));
  Serial.println(F("  e - encerrar calibracao do magnetometro"));
  Serial.println(F("  U - largura de banda de 256 Hz"));
  Serial.println(F("  u - largura de banda de 5 Hz"));
  Serial.println(F("  B - alterar sensor e ESP32 para 115200 baud"));
  Serial.println(F("  b - alterar sensor e ESP32 para 9600 baud"));
  Serial.println(F("  h - mostrar esta ajuda"));
  Serial.println();
}

bool autoScanSensor() {
  Serial.println(F("Procurando o WT901C485..."));

  for (const uint32_t baudRate : SENSOR_BAUD_RATES) {
    beginSensorSerial(baudRate);
    delay(50);

    for (uint8_t attempt = 0; attempt < 2; ++attempt) {
      dataUpdate = 0;
      WitReadReg(AX, 3);
      waitForSensorResponse(150);

      if (dataUpdate != 0) {
        dataUpdate = 0;
        Serial.printf("Sensor encontrado em %lu baud.\r\n",
                      static_cast<unsigned long>(baudRate));
        return true;
      }
    }
  }

  beginSensorSerial(9600);
  Serial.println(F("Sensor nao encontrado."));
  Serial.println(F("Confira alimentacao, GND comum, fios A/B e pinos RX/TX."));
  return false;
}

void printMeasurements(uint8_t updates) {
  const float accelerationX = sReg[AX] / 32768.0f * 16.0f;
  const float accelerationY = sReg[AY] / 32768.0f * 16.0f;
  const float accelerationZ = sReg[AZ] / 32768.0f * 16.0f;
  const float gyroX = sReg[GX] / 32768.0f * 2000.0f;
  const float gyroY = sReg[GY] / 32768.0f * 2000.0f;
  const float gyroZ = sReg[GZ] / 32768.0f * 2000.0f;
  const float roll = sReg[Roll] / 32768.0f * 180.0f;
  const float pitch = sReg[Pitch] / 32768.0f * 180.0f;
  const float yaw = sReg[Yaw] / 32768.0f * 180.0f;
  const float temperature = sReg[TEMP] / 100.0f;

  if (updates & ACC_UPDATE) {
    Serial.printf("acc[g]: %.3f %.3f %.3f\r\n",
                  accelerationX, accelerationY, accelerationZ);
  }
  if (updates & GYRO_UPDATE) {
    Serial.printf("gyro[graus/s]: %.3f %.3f %.3f\r\n", gyroX, gyroY, gyroZ);
  }
  if (updates & ANGLE_UPDATE) {
    Serial.printf("angulo[graus]: %.3f %.3f %.3f\r\n", roll, pitch, yaw);
  }
  if (updates & MAG_UPDATE) {
    Serial.printf("mag: %d %d %d\r\n", sReg[HX], sReg[HY], sReg[HZ]);
  }
  if (updates & TEMP_UPDATE) {
    Serial.printf("temperatura[C]: %.2f\r\n", temperature);
  }
}

void changeSensorBaudRate(int32_t witBaudIndex, uint32_t baudRate) {
  if (WitSetUartBaud(witBaudIndex) != WIT_HAL_OK) {
    Serial.println(F("Nao foi possivel alterar o baud rate."));
    return;
  }

  delay(100);
  beginSensorSerial(baudRate);
  Serial.printf("Baud rate alterado para %lu.\r\n",
                static_cast<unsigned long>(baudRate));
}

void processCommand(char command) {
  switch (command) {
    case 'a':
      if (WitStartAccCali() != WIT_HAL_OK) {
        Serial.println(F("Erro ao iniciar calibracao do acelerometro."));
      }
      break;
    case 'm':
      if (WitStartMagCali() != WIT_HAL_OK) {
        Serial.println(F("Erro ao iniciar calibracao do magnetometro."));
      }
      break;
    case 'e':
      if (WitStopMagCali() != WIT_HAL_OK) {
        Serial.println(F("Erro ao encerrar calibracao do magnetometro."));
      }
      break;
    case 'u':
      if (WitSetBandwidth(BANDWIDTH_5HZ) != WIT_HAL_OK) {
        Serial.println(F("Erro ao alterar largura de banda."));
      }
      break;
    case 'U':
      if (WitSetBandwidth(BANDWIDTH_256HZ) != WIT_HAL_OK) {
        Serial.println(F("Erro ao alterar largura de banda."));
      }
      break;
    case 'B':
      changeSensorBaudRate(WIT_BAUD_115200, 115200);
      break;
    case 'b':
      changeSensorBaudRate(WIT_BAUD_9600, 9600);
      break;
    case 'h':
    case 'H':
    case '?':
      showHelp();
      break;
    case '\r':
    case '\n':
    case ' ':
    case '\t':
      break;
    default:
      Serial.printf("Comando desconhecido: %c\r\n", command);
      showHelp();
      break;
  }
}

void processUsbSerial() {
  while (Serial.available() > 0) {
    processCommand(static_cast<char>(Serial.read()));
  }
}

}  // namespace

void setup() {
  Serial.begin(USB_BAUD_RATE);
  const uint32_t serialWaitStartedAt = millis();
  while (!Serial && millis() - serialWaitStartedAt < 2000) {
    delay(10);
  }

  if (RS485_DIRECTION_PIN >= 0) {
    pinMode(RS485_DIRECTION_PIN, OUTPUT);
    setRs485Transmit(false);
  }

  WitInit(WIT_PROTOCOL_MODBUS, SENSOR_MODBUS_ADDRESS);
  WitSerialWriteRegister(sensorUartSend);
  WitRegisterCallBack(sensorDataUpdated);
  WitDelayMsRegister(sdkDelay);

  Serial.println();
  Serial.println(F("WT901C485 + ESP32 (Arduino)"));
  autoScanSensor();
  showHelp();
  lastReadAt = millis() - READ_INTERVAL_MS;
}

void loop() {
  processSensorBytes();
  processUsbSerial();

  const uint32_t now = millis();
  if (now - lastReadAt >= READ_INTERVAL_MS) {
    lastReadAt = now;
    WitReadReg(AX, 13);
  }

  if (dataUpdate != 0) {
    const uint8_t updates = dataUpdate;
    dataUpdate = 0;
    printMeasurements(updates);
  }

  delay(1);
}

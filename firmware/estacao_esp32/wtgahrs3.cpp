#include "wtgahrs3.h"

#include <math.h>

// ---------------------------------------------------------------------------
// Conversoes
// ---------------------------------------------------------------------------
int16_t s16(uint16_t v) {
  return (int16_t)v;  // complemento de dois, mesma representacao
}

int32_t s32(uint16_t baixo, uint16_t alto) {
  return (int32_t)(((uint32_t)alto << 16) | (uint32_t)baixo);
}

double ddmmParaGraus(int32_t bruto) {
  double dd = floor(bruto / 10000000.0);            // parte de graus, com sinal
  double mm = (bruto - dd * 10000000.0) / 100000.0; // minutos, sempre positivos
  double sinal = (dd < 0) ? -1.0 : 1.0;
  return sinal * (fabs(dd) + mm / 60.0);
}

// ---------------------------------------------------------------------------
// Ciclo de vida
// ---------------------------------------------------------------------------
WTGAHRS3::WTGAHRS3(HardwareSerial &serial, int8_t pinoDE, uint8_t endereco)
    : _serial(serial), _pinoDE(pinoDE), _endereco(endereco) {}

void WTGAHRS3::iniciar(uint32_t baudrate, int8_t pinoRX, int8_t pinoTX) {
  if (_pinoDE >= 0) {
    pinMode(_pinoDE, OUTPUT);
    digitalWrite(_pinoDE, LOW);  // comeca em recepcao
  }
  _serial.begin(baudrate, SERIAL_8N1, pinoRX, pinoTX);
  delay(50);
  limparEntrada();
}

bool WTGAHRS3::estaRespondendo() {
  uint16_t r;
  return lerRegistradores(REG_TEMP, 1, &r);
}

// ---------------------------------------------------------------------------
// Leituras
// ---------------------------------------------------------------------------
bool WTGAHRS3::lerPosicao(Posicao &saida) {
  // 0x49..0x50 -> LonL, LonH, LatL, LatH, GPSHeight, GPSYaw, GPSVL, GPSVH
  uint16_t g[8];
  if (!lerRegistradores(REG_LONL, 8, g)) return false;

  // 0x55..0x58 -> SVNUM, PDOP, HDOP, VDOP
  uint16_t d[4];
  if (!lerRegistradores(REG_SVNUM, 4, d)) return false;

  saida.lonBruto      = s32(g[0], g[1]);
  saida.latBruto      = s32(g[2], g[3]);
  saida.longitude     = ddmmParaGraus(saida.lonBruto);
  saida.latitude      = ddmmParaGraus(saida.latBruto);
  saida.altitudeGpsM  = s16(g[4]) / 10.0f;
  saida.rumoGpsDeg    = s16(g[5]) / 100.0f;
  saida.velocidadeKmh = s32(g[6], g[7]) / 1000.0f;
  saida.satelites     = d[0];
  saida.pdop          = d[1] / 100.0f;
  saida.hdop          = d[2] / 100.0f;
  saida.vdop          = d[3] / 100.0f;
  return true;
}

bool WTGAHRS3::lerAmbiente(Ambiente &saida) {
  uint16_t p[4];  // PressureL, PressureH, HeightL, HeightH
  if (!lerRegistradores(REG_PRESSUREL, 4, p)) return false;

  uint16_t t;
  if (!lerRegistradores(REG_TEMP, 1, &t)) return false;

  saida.pressaoPa     = s32(p[0], p[1]);
  saida.altitudeBaroM = s32(p[2], p[3]) / 100.0f;  // registrador vem em cm
  saida.temperaturaC  = s16(t) / 100.0f;
  return true;
}

bool WTGAHRS3::lerImu(Imu &saida) {
  uint16_t r[12];  // 0x34..0x3F
  if (!lerRegistradores(REG_AX, 12, r)) return false;

  int16_t v[12];
  for (uint8_t i = 0; i < 12; i++) v[i] = s16(r[i]);

  saida.accX  = v[0] / 32768.0f * ESCALA_ACC_G;
  saida.accY  = v[1] / 32768.0f * ESCALA_ACC_G;
  saida.accZ  = v[2] / 32768.0f * ESCALA_ACC_G;
  saida.gyroX = v[3] / 32768.0f * ESCALA_GYRO_DPS;
  saida.gyroY = v[4] / 32768.0f * ESCALA_GYRO_DPS;
  saida.gyroZ = v[5] / 32768.0f * ESCALA_GYRO_DPS;
  saida.magX  = v[6];
  saida.magY  = v[7];
  saida.magZ  = v[8];
  saida.roll  = v[9]  / 32768.0f * ESCALA_ANGULO;
  saida.pitch = v[10] / 32768.0f * ESCALA_ANGULO;
  saida.yaw   = v[11] / 32768.0f * ESCALA_ANGULO;
  return true;
}

// ---------------------------------------------------------------------------
// Configuracao
// ---------------------------------------------------------------------------
bool WTGAHRS3::desbloquear() {
  if (!escreverRegistrador(REG_KEY, CHAVE_DESBLOQUEIO)) return false;
  delay(100);
  return true;
}

bool WTGAHRS3::salvar() {
  if (!escreverRegistrador(REG_SAVE, 0x0000)) return false;
  delay(200);
  return true;
}

bool WTGAHRS3::definirFusoHorario(int8_t utcOffset) {
  if (utcOffset < -12 || utcOffset > 12) return false;
  if (!desbloquear()) return false;
  if (!escreverRegistrador(REG_TIMEZONE, (uint16_t)(utcOffset + 12))) return false;
  return salvar();
}

// ---------------------------------------------------------------------------
// Modbus RTU
// ---------------------------------------------------------------------------
bool WTGAHRS3::lerRegistradores(uint8_t enderecoRegistrador, uint8_t quantidade, uint16_t *destino) {
  uint8_t pedido[8] = {
      _endereco, 0x03, 0x00, enderecoRegistrador, 0x00, quantidade, 0x00, 0x00};
  uint16_t crc = crc16(pedido, 6);
  pedido[6] = crc & 0xFF;         // CRC vai com o byte baixo primeiro
  pedido[7] = (crc >> 8) & 0xFF;

  limparEntrada();
  enviarQuadro(pedido, sizeof(pedido));

  // resposta: endereco + funcao + contagem + dados + CRC
  const size_t esperado = 5 + 2 * (size_t)quantidade;
  uint8_t resposta[5 + 2 * 32];
  if (quantidade > 32) return false;
  if (!receberQuadro(resposta, esperado)) return false;

  if (resposta[0] != _endereco || resposta[1] != 0x03) return false;
  if (resposta[2] != 2 * quantidade) return false;

  uint16_t crcRecebido = (uint16_t)resposta[esperado - 1] << 8 | resposta[esperado - 2];
  if (crcRecebido != crc16(resposta, esperado - 2)) return false;

  for (uint8_t i = 0; i < quantidade; i++) {
    destino[i] = ((uint16_t)resposta[3 + 2 * i] << 8) | resposta[4 + 2 * i];
  }
  return true;
}

bool WTGAHRS3::escreverRegistrador(uint8_t enderecoRegistrador, uint16_t valor) {
  uint8_t pedido[8] = {_endereco,
                       0x06,
                       0x00,
                       enderecoRegistrador,
                       (uint8_t)(valor >> 8),
                       (uint8_t)(valor & 0xFF),
                       0x00,
                       0x00};
  uint16_t crc = crc16(pedido, 6);
  pedido[6] = crc & 0xFF;
  pedido[7] = (crc >> 8) & 0xFF;

  limparEntrada();
  enviarQuadro(pedido, sizeof(pedido));

  // o eco da funcao 0x06 tem o mesmo tamanho do pedido
  uint8_t resposta[8];
  if (!receberQuadro(resposta, sizeof(resposta))) return false;

  uint16_t crcRecebido = (uint16_t)resposta[7] << 8 | resposta[6];
  if (crcRecebido != crc16(resposta, 6)) return false;
  return resposta[0] == _endereco && resposta[1] == 0x06;
}

void WTGAHRS3::enviarQuadro(const uint8_t *quadro, size_t tamanho) {
  if (_pinoDE >= 0) digitalWrite(_pinoDE, HIGH);  // transmissao
  _serial.write(quadro, tamanho);
  _serial.flush();               // espera esvaziar a FIFO de transmissao
  delayMicroseconds(200);        // margem para o ultimo bit sair da linha
  if (_pinoDE >= 0) digitalWrite(_pinoDE, LOW);   // volta a recepcao
}

bool WTGAHRS3::receberQuadro(uint8_t *buffer, size_t esperado) {
  size_t lidos = 0;
  uint32_t limite = millis() + _timeoutMs;
  while (lidos < esperado && millis() < limite) {
    if (_serial.available()) {
      buffer[lidos++] = (uint8_t)_serial.read();
    }
  }
  return lidos == esperado;
}

void WTGAHRS3::limparEntrada() {
  while (_serial.available()) _serial.read();
}

uint16_t WTGAHRS3::crc16(const uint8_t *dados, size_t tamanho) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < tamanho; i++) {
    crc ^= dados[i];
    for (uint8_t bit = 0; bit < 8; bit++) {
      if (crc & 0x0001) {
        crc = (crc >> 1) ^ 0xA001;
      } else {
        crc >>= 1;
      }
    }
  }
  return crc;
}

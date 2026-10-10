#include "ambiente_rs485.h"

AmbienteRS485::AmbienteRS485(HardwareSerial &serial, int8_t pinoDE, uint8_t endereco)
    : _serial(serial), _pinoDE(pinoDE), _endereco(endereco) {}

bool AmbienteRS485::estaRespondendo() {
  uint16_t r;
  return lerRegistradores(0x0000, 1, &r);
}

bool AmbienteRS485::ler(AmbienteRS485Leitura &saida) {
  uint16_t r[2];  // 0x0000 temperatura, 0x0001 umidade
  if (!lerRegistradores(0x0000, 2, r)) return false;

  saida.temperaturaC = (int16_t)r[0] / 10.0f;
  saida.umidadeRH    = (int16_t)r[1] / 10.0f;
  return true;
}

// ---------------------------------------------------------------------------
// Modbus RTU — mesma implementacao do driver WTGAHRS3, endereco de
// registrador de 16 bits porque o sketch de diagnostico varreu ate 0x01FF.
// ---------------------------------------------------------------------------
bool AmbienteRS485::lerRegistradores(uint16_t enderecoRegistrador, uint8_t quantidade, uint16_t *destino) {
  uint8_t pedido[8] = {
      _endereco, 0x03,
      (uint8_t)(enderecoRegistrador >> 8), (uint8_t)(enderecoRegistrador & 0xFF),
      0x00, quantidade, 0x00, 0x00};
  uint16_t crc = crc16(pedido, 6);
  pedido[6] = crc & 0xFF;
  pedido[7] = (crc >> 8) & 0xFF;

  // SILENCIO ENTRE QUADROS (t3.5). O Modbus RTU delimita um quadro pela pausa
  // de 3,5 caracteres antes dele - a 9600 8N1 (10 bits por byte) sao 3,65 ms.
  // Sem essa pausa o sensor trata o pedido como continuacao do quadro anterior
  // e descarta em silencio.
  //
  // Isto so passou a importar quando o WTGAHRS3 entrou no mesmo barramento a
  // 10 Hz: antes o ambiental era lido a cada 2 s, com a linha vazia, e a pausa
  // acontecia sozinha. O WTGAHRS3 tolera a falta dela; este sensor nao.
  delay(SILENCIO_QUADRO_MS);

  limparEntrada();
  enviarQuadro(pedido, sizeof(pedido));

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

void AmbienteRS485::enviarQuadro(const uint8_t *quadro, size_t tamanho) {
  if (_pinoDE >= 0) digitalWrite(_pinoDE, HIGH);
  _serial.write(quadro, tamanho);
  _serial.flush();
  delayMicroseconds(200);
  if (_pinoDE >= 0) digitalWrite(_pinoDE, LOW);
}

/**
 * Recebe um quadro SINCRONIZANDO no inicio, em vez de aceitar como primeiro
 * byte o que quer que esteja na linha.
 *
 * O barramento e compartilhado com o WTGAHRS3. Uma resposta dele que chegue
 * atrasada - depois do timeout da propria leitura - fica na linha e seria
 * lida aqui como o primeiro byte da resposta deste sensor. O quadro inteiro
 * sai deslocado, o CRC falha e a leitura e descartada. Com o WTGAHRS3 sendo
 * lido a 10 Hz isso acontecia quase toda vez.
 *
 * A regra: byte que nao for o endereco deste sensor, seguido da funcao 0x03,
 * e lixo de outra transacao - descarta e continua procurando.
 */
bool AmbienteRS485::receberQuadro(uint8_t *buffer, size_t esperado) {
  size_t lidos = 0;
  uint32_t limite = millis() + _timeoutMs;
  while (lidos < esperado && millis() < limite) {
    if (!_serial.available()) continue;
    uint8_t b = (uint8_t)_serial.read();

    if (lidos == 0 && b != _endereco) continue;   // ainda nao e o nosso quadro
    if (lidos == 1 && b != 0x03) {
      // Endereco casou por acaso num byte de dado alheio: recomeca a busca,
      // reaproveitando este byte caso ele seja o endereco de verdade.
      lidos = 0;
      if (b == _endereco) buffer[lidos++] = b;
      continue;
    }
    buffer[lidos++] = b;
  }
  return lidos == esperado;
}

void AmbienteRS485::limparEntrada() {
  while (_serial.available()) _serial.read();
}

uint16_t AmbienteRS485::crc16(const uint8_t *dados, size_t tamanho) {
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

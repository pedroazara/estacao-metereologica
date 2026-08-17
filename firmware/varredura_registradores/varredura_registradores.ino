/*
 * Varredura larga de registradores Modbus - caca a pressao atmosferica.
 *
 * O sketch teste_sensor_ambiental achou temperatura em 0x0000 e umidade em
 * 0x0001, e mais nada util ate 0x0013. Este aqui responde a pergunta que
 * sobrou: existe pressao (ou TVOC, ou eCO2) em algum outro endereco?
 *
 * Varre registrador a registrador, um de cada vez, nas funcoes 0x03 e 0x04,
 * numa faixa larga. Imprime:
 *   - uma linha de resumo por bloco de 16 enderecos (quantos existem, quantos
 *     devolvem excecao, quantos ficam mudos);
 *   - individualmente, apenas os registradores com valor DIFERENTE DE ZERO -
 *     que sao os candidatos a grandeza real.
 *
 * Registrador que existe e le 0 quase sempre significa "sensor nao instalado
 * nesta variante". Registrador mudo significa que o endereco nao existe.
 *
 * Ligacao e parametros: iguais aos do teste_sensor_ambiental.
 */

// ---------------------------------------------------------------------------
#if defined(ESP32)
  static const int8_t PINO_RX = 16;
  static const int8_t PINO_TX = 17;
  #define PORTA_RS485 Serial2
  #define TEM_UART_DEDICADA 1
#else
  #include <SoftwareSerial.h>
  static const uint8_t PINO_RX = 10;
  static const uint8_t PINO_TX = 11;
  SoftwareSerial portaSoftware(PINO_RX, PINO_TX);
  #define PORTA_RS485 portaSoftware
  #define TEM_UART_DEDICADA 0
#endif

static const int8_t PINO_DE = 4;   // DE+RE do transceptor; -1 se for automatico

// Parametros ja confirmados na bancada
static const uint32_t BAUD      = 9600;
static const uint8_t  ENDERECO  = 1;

// Faixa a varrer
static const uint16_t REG_INICIO = 0x0000;
static const uint16_t REG_FIM    = 0x01FF;   // inclusive

// Timeout curto: a 9600 baud a resposta chega em ~15 ms; 120 ms e folga
// suficiente e mantem a varredura inteira em pouco mais de um minuto.
static const uint16_t TIMEOUT_MS = 120;
static const uint16_t GAP_MS     = 10;

enum Resultado { RESP_OK, RESP_MUDO, RESP_SUJO, RESP_EXCECAO };

uint8_t ultimaExcecao = 0;

// ---------------------------------------------------------------------------
uint16_t crc16(const uint8_t *dados, size_t tamanho) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < tamanho; i++) {
    crc ^= dados[i];
    for (uint8_t bit = 0; bit < 8; bit++) {
      if (crc & 0x0001) crc = (crc >> 1) ^ 0xA001;
      else              crc >>= 1;
    }
  }
  return crc;
}

void enviar(const uint8_t *quadro, size_t tamanho) {
  if (PINO_DE >= 0) digitalWrite(PINO_DE, HIGH);
  PORTA_RS485.write(quadro, tamanho);
#if TEM_UART_DEDICADA
  PORTA_RS485.flush();
#endif
  delayMicroseconds(300);
  if (PINO_DE >= 0) digitalWrite(PINO_DE, LOW);
}

size_t receber(uint8_t *buffer, size_t maximo) {
  size_t n = 0;
  uint32_t limite = millis() + TIMEOUT_MS;
  uint32_t ultimo = 0;
  while (millis() < limite) {
    if (PORTA_RS485.available()) {
      if (n < maximo) buffer[n++] = (uint8_t)PORTA_RS485.read();
      else            PORTA_RS485.read();
      ultimo = millis();
    } else if (n > 0 && millis() - ultimo > GAP_MS) {
      break;
    }
  }
  return n;
}

Resultado lerUm(uint8_t funcao, uint16_t reg, uint16_t &valor) {
  uint8_t pedido[8] = {ENDERECO,
                       funcao,
                       (uint8_t)(reg >> 8),
                       (uint8_t)(reg & 0xFF),
                       0x00,
                       0x01,
                       0x00,
                       0x00};
  uint16_t crc = crc16(pedido, 6);
  pedido[6] = crc & 0xFF;
  pedido[7] = (crc >> 8) & 0xFF;

  while (PORTA_RS485.available()) PORTA_RS485.read();
  enviar(pedido, sizeof(pedido));

  uint8_t resp[16];
  size_t n = receber(resp, sizeof(resp));
  if (n == 0) return RESP_MUDO;
  if (n < 5)  return RESP_SUJO;

  uint16_t crcRecebido = ((uint16_t)resp[n - 1] << 8) | resp[n - 2];
  if (crcRecebido != crc16(resp, n - 2)) return RESP_SUJO;

  if (resp[1] & 0x80) {
    ultimaExcecao = resp[2];
    return RESP_EXCECAO;
  }
  if (resp[1] != funcao || resp[2] != 2) return RESP_SUJO;

  valor = ((uint16_t)resp[3] << 8) | resp[4];
  return RESP_OK;
}

// ---------------------------------------------------------------------------
void varrerFuncao(uint8_t funcao) {
  Serial.println();
  Serial.print(F("===== funcao 0x0"));
  Serial.print(funcao);
  Serial.print(funcao == 3 ? F(" (holding), ") : F(" (input), "));
  Serial.print(F("faixa 0x"));
  Serial.print(REG_INICIO, HEX);
  Serial.print(F("..0x"));
  Serial.print(REG_FIM, HEX);
  Serial.println(F(" ====="));

  uint32_t inicio = millis();
  uint16_t totalOk = 0, totalNaoZero = 0, totalExcecao = 0, totalMudo = 0;

  for (uint16_t base = REG_INICIO; base <= REG_FIM; base += 16) {
    uint8_t nOk = 0, nExc = 0, nMudo = 0, nSujo = 0;
    uint16_t achados[16];
    uint16_t enderecos[16];
    uint8_t nAchados = 0;

    for (uint16_t i = 0; i < 16 && (base + i) <= REG_FIM; i++) {
      uint16_t reg = base + i;
      uint16_t v = 0;
      switch (lerUm(funcao, reg, v)) {
        case RESP_OK:
          nOk++;
          if (v != 0) {
            enderecos[nAchados] = reg;
            achados[nAchados] = v;
            nAchados++;
          }
          break;
        case RESP_EXCECAO: nExc++;  break;
        case RESP_MUDO:    nMudo++; break;
        case RESP_SUJO:    nSujo++; break;
      }
    }

    totalOk      += nOk;
    totalExcecao += nExc;
    totalMudo    += nMudo;
    totalNaoZero += nAchados;

    // Bloco totalmente mudo: uma linha enxuta, senao a saida vira ruido.
    Serial.print(F("0x"));
    if (base < 0x1000) Serial.print('0');
    if (base < 0x100)  Serial.print('0');
    if (base < 0x10)   Serial.print('0');
    Serial.print(base, HEX);
    Serial.print(F("  existe "));
    Serial.print(nOk);
    Serial.print(F("  excecao "));
    Serial.print(nExc);
    Serial.print(F("  mudo "));
    Serial.print(nMudo);
    if (nSujo) {
      Serial.print(F("  sujo "));
      Serial.print(nSujo);
    }
    Serial.println();

    for (uint8_t i = 0; i < nAchados; i++) {
      Serial.print(F("    -> 0x"));
      if (enderecos[i] < 0x1000) Serial.print('0');
      if (enderecos[i] < 0x100)  Serial.print('0');
      if (enderecos[i] < 0x10)   Serial.print('0');
      Serial.print(enderecos[i], HEX);
      Serial.print(F(" = "));
      Serial.print(achados[i]);
      Serial.print(F("   /10 "));
      Serial.print((int16_t)achados[i] / 10.0f, 1);
      Serial.print(F("   /100 "));
      Serial.println((int16_t)achados[i] / 100.0f, 2);
    }
  }

  Serial.print(F("resumo funcao 0x0"));
  Serial.print(funcao);
  Serial.print(F(": existem "));
  Serial.print(totalOk);
  Serial.print(F(" (nao-zero "));
  Serial.print(totalNaoZero);
  Serial.print(F("), excecao "));
  Serial.print(totalExcecao);
  Serial.print(F(", mudo "));
  Serial.print(totalMudo);
  Serial.print(F("  ["));
  Serial.print((millis() - inicio) / 1000.0f, 1);
  Serial.println(F(" s]"));
}

// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println(F("Varredura larga de registradores - procurando pressao"));
  Serial.print(F("endereco "));
  Serial.print(ENDERECO);
  Serial.print(F(", "));
  Serial.print(BAUD);
  Serial.println(F(" baud"));

  if (PINO_DE >= 0) {
    pinMode(PINO_DE, OUTPUT);
    digitalWrite(PINO_DE, LOW);
  }

#if TEM_UART_DEDICADA
  PORTA_RS485.begin(BAUD, SERIAL_8N1, PINO_RX, PINO_TX);
#else
  PORTA_RS485.begin(BAUD);
#endif
  delay(100);

  varrerFuncao(0x03);
  varrerFuncao(0x04);

  Serial.println();
  Serial.println(F("== como ler =="));
  Serial.println(F("  'existe' e le 0  -> endereco valido, sensor nao instalado"));
  Serial.println(F("  'mudo'           -> endereco nao existe neste equipamento"));
  Serial.println(F("  pressao seria ~9000-10200 (0,1 hPa) ou 900-1020 (hPa),"));
  Serial.println(F("  ou um par de registradores somando ~90000-101325 (Pa)"));
  Serial.println();
  Serial.println(F("varredura concluida."));
}

void loop() {
  delay(1000);
}

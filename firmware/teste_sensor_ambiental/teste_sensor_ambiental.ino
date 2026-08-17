/*
 * Teste de sinal - sensor ambiental RS485 multiparametro.
 * Temperatura / umidade / pressao atmosferica / TVOC / eCO2, Modbus RTU.
 *
 * NAO e um driver: e ferramenta de diagnostico para descobrir como ESTE sensor
 * fala, antes de escrever o driver definitivo. O anuncio nao publica o mapa de
 * registradores e o modelo vem em 4 variantes ("Type 1..4") com conjuntos de
 * sensores diferentes, entao o sketch descobre em vez de supor:
 *
 *   1. varre baud rates e enderecos Modbus ate alguem responder;
 *   2. despeja os registradores 0x0000..0x0013 nas funcoes 0x03 e 0x04;
 *   3. imprime cada registrador em TODAS as escalas usuais (bruto, /10, /100 e
 *      a codificacao de negativo por offset 10000), para voce bater o olho e
 *      ver qual coluna parece temperatura de sala;
 *   4. interpreta os registradores pelo mapa confirmado abaixo;
 *   5. depois fica monitorando em loop.
 *
 * MAPA CONFIRMADO NA BANCADA (14/08/2026, unidade "Type 2"):
 *
 *      endereco Modbus 1, 9600 8N1, funcao 0x03 (holding registers)
 *      0x0000  temperatura  /10  C, int16 com sinal
 *      0x0001  umidade      /10  %RH
 *      0x0002..0x000F       existem e leem 0
 *      0x0010 em diante     nao respondem
 *      funcao 0x04 (input registers): sem resposta nenhuma
 *
 * A hipotese inicial vinha do protocolo da familia "N em 1" publicado em
 * modbus.cn/25909.html (eCO2 em 0x0000, TVOC em 0x0001, temperatura /100 em
 * 0x0005). O despejo bruto derrubou ela: os dois unicos registradores com dado
 * sao temperatura e umidade, em decimos. NAO ha pressao, TVOC nem eCO2 nesta
 * faixa de registradores - ver o item aberto no README da pasta.
 *
 * Comandos pelo monitor serial (115200 baud):
 *   r  -> varrer de novo
 *   d  -> despejar os registradores de novo
 *
 * Ligacao: transceptor RS485 nos A/B do sensor. Se o modulo tiver DE/RE, ligue
 * os dois juntos num GPIO e ponha o numero em PINO_DE; se for de direcao
 * automatica, deixe PINO_DE = -1.
 */

// ---------------------------------------------------------------------------
// Porta serial do RS485 - detecta a placa sozinho
// ---------------------------------------------------------------------------
#if defined(ESP32)
  static const int8_t PINO_RX = 16;   // RO do transceptor
  static const int8_t PINO_TX = 17;   // DI do transceptor
  #define PORTA_RS485 Serial2
  #define TEM_UART_DEDICADA 1
#else
  // Arduino Uno/Nano sem UART sobrando (a unica e a do USB): SoftwareSerial.
  #include <SoftwareSerial.h>
  static const uint8_t PINO_RX = 10;  // RO do transceptor -> pino 10
  static const uint8_t PINO_TX = 11;  // DI do transceptor <- pino 11
  SoftwareSerial portaSoftware(PINO_RX, PINO_TX);
  #define PORTA_RS485 portaSoftware
  #define TEM_UART_DEDICADA 0
#endif

static const int8_t PINO_DE = 4;   // DE+RE do transceptor; -1 se for automatico

// ---------------------------------------------------------------------------
// Parametros da varredura
// ---------------------------------------------------------------------------
static const uint32_t BAUDS[]      = {9600, 4800, 19200, 38400};
static const uint8_t  QTD_BAUDS    = sizeof(BAUDS) / sizeof(BAUDS[0]);
static const uint8_t  END_MIN      = 1;     // enderecos Modbus validos: 1..247
static const uint8_t  END_MAX      = 32;    // sobe se nao achar (247 leva ~2 min)
static const uint8_t  QTD_DESPEJO  = 20;    // 0x0000..0x0013, cobre a pressao
static const uint16_t INTERVALO_MS = 2000;  // ritmo do monitoramento

// Mapa confirmado na bancada
static const uint8_t REG_TEMP  = 0x00;   // /10 C
static const uint8_t REG_UMID  = 0x01;   // /10 %RH
static const uint8_t QTD_BLOCO = 0x02;   // so estes dois carregam dado

// Estado descoberto na varredura
uint32_t baudAtual     = 0;
uint8_t  enderecoAtual = 0;
uint8_t  funcaoAtual   = 0;   // 0x03 (holding) ou 0x04 (input)

/*
 * Resultado de uma tentativa de leitura. Precisa estar definido ANTES da
 * primeira funcao do arquivo: a IDE gera os prototipos automaticamente e os
 * injeta logo acima dela - um tipo declarado depois disso nao existiria ainda.
 */
enum Resultado {
  RESP_OK,             // resposta valida com dados
  RESP_SEM_RESPOSTA,   // ninguem falou
  RESP_CRC_INVALIDO,   // chegou algo corrompido - quase sempre baud errado
  RESP_EXCECAO         // o sensor RESPONDEU recusando: ele EXISTE nesse endereco
};

uint8_t ultimaExcecao = 0;

// Prototipos (a IDE gera sozinha, mas explicito evita surpresa de ordem)
bool varrer();
void despejar();
void interpretarHipotese();
void monitorar();

// ---------------------------------------------------------------------------
// Camada Modbus RTU (minima, sem biblioteca externa)
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

void iniciarPorta(uint32_t baud) {
#if TEM_UART_DEDICADA
  PORTA_RS485.begin(baud, SERIAL_8N1, PINO_RX, PINO_TX);
#else
  PORTA_RS485.begin(baud);
#endif
  delay(20);
  while (PORTA_RS485.available()) PORTA_RS485.read();
}

void enviar(const uint8_t *quadro, size_t tamanho) {
  if (PINO_DE >= 0) digitalWrite(PINO_DE, HIGH);   // transmissao
  PORTA_RS485.write(quadro, tamanho);
#if TEM_UART_DEDICADA
  // Espera a FIFO de transmissao esvaziar. Em SoftwareSerial isso nao existe:
  // write() e bit-banged e so retorna com o ultimo bit ja na linha - e flush()
  // la significa "descartar o buffer de RECEPCAO", que seria o oposto do que
  // queremos. Por isso a chamada fica dentro do #if.
  PORTA_RS485.flush();
#endif
  delayMicroseconds(300);                          // margem do ultimo bit
  if (PINO_DE >= 0) digitalWrite(PINO_DE, LOW);    // volta a escutar
}

/**
 * Le a resposta ate o silencio entre quadros; devolve quantos bytes chegaram.
 * Le por tamanho variavel de proposito: em diagnostico voce quer VER a resposta
 * torta, nao descarta-la por nao bater com o tamanho esperado.
 */
static const uint16_t TIMEOUT_MS = 300;  // espera maxima pela resposta
static const uint16_t GAP_MS     = 12;   // silencio que marca o fim do quadro

size_t receber(uint8_t *buffer, size_t maximo) {
  size_t n = 0;
  uint32_t limite = millis() + TIMEOUT_MS;
  uint32_t ultimoByte = 0;

  while (millis() < limite) {
    if (PORTA_RS485.available()) {
      if (n < maximo) buffer[n++] = (uint8_t)PORTA_RS485.read();
      else            PORTA_RS485.read();          // estouro: descarta o resto
      ultimoByte = millis();
    } else if (n > 0 && millis() - ultimoByte > GAP_MS) {
      break;                                       // fim do quadro
    }
  }
  return n;
}

Resultado modbusLer(uint8_t endereco, uint8_t funcao, uint16_t reg, uint8_t quantidade,
                    uint16_t *destino) {
  uint8_t pedido[8] = {endereco,
                       funcao,
                       (uint8_t)(reg >> 8),
                       (uint8_t)(reg & 0xFF),
                       0x00,
                       quantidade,
                       0x00,
                       0x00};
  uint16_t crc = crc16(pedido, 6);
  pedido[6] = crc & 0xFF;          // o CRC vai com o byte baixo primeiro
  pedido[7] = (crc >> 8) & 0xFF;

  while (PORTA_RS485.available()) PORTA_RS485.read();
  enviar(pedido, sizeof(pedido));

  uint8_t resposta[64];
  size_t n = receber(resposta, sizeof(resposta));
  if (n == 0) return RESP_SEM_RESPOSTA;
  if (n < 5)  return RESP_CRC_INVALIDO;

  uint16_t crcRecebido = ((uint16_t)resposta[n - 1] << 8) | resposta[n - 2];
  if (crcRecebido != crc16(resposta, n - 2)) return RESP_CRC_INVALIDO;

  if (resposta[1] & 0x80) {        // funcao com o bit alto = excecao Modbus
    ultimaExcecao = resposta[2];
    return RESP_EXCECAO;
  }
  if (resposta[1] != funcao || resposta[2] != 2 * quantidade) return RESP_CRC_INVALIDO;

  for (uint8_t i = 0; i < quantidade; i++) {
    destino[i] = ((uint16_t)resposta[3 + 2 * i] << 8) | resposta[4 + 2 * i];
  }
  return RESP_OK;
}

/** Le um bloco; se o sensor recusar o bloco inteiro, tenta registrador a registrador. */
bool lerBloco(uint8_t funcao, uint8_t quantidade, uint16_t *destino, bool *validos) {
  if (modbusLer(enderecoAtual, funcao, 0x0000, quantidade, destino) == RESP_OK) {
    for (uint8_t i = 0; i < quantidade; i++) validos[i] = true;
    return true;
  }
  bool algum = false;
  for (uint8_t i = 0; i < quantidade; i++) {
    uint16_t u[1];
    validos[i] = (modbusLer(enderecoAtual, funcao, i, 1, u) == RESP_OK);
    destino[i] = validos[i] ? u[0] : 0;
    algum |= validos[i];
    delay(25);
  }
  return algum;
}

// ---------------------------------------------------------------------------
// Varredura
// ---------------------------------------------------------------------------
bool varrer() {
  Serial.println();
  Serial.println(F("== varredura =="));

  for (uint8_t b = 0; b < QTD_BAUDS; b++) {
    iniciarPorta(BAUDS[b]);
    Serial.print(F("  "));
    Serial.print(BAUDS[b]);
    Serial.print(F(" baud: "));

    for (uint8_t end = END_MIN; end <= END_MAX; end++) {
      uint16_t v[1];
      for (uint8_t f = 0; f < 2; f++) {
        uint8_t funcao = (f == 0) ? 0x03 : 0x04;
        Resultado r = modbusLer(end, funcao, 0x0000, 1, v);

        if (r == RESP_OK || r == RESP_EXCECAO) {
          baudAtual     = BAUDS[b];
          enderecoAtual = end;
          funcaoAtual   = funcao;
          Serial.println();
          Serial.print(F("  ACHOU -> endereco "));
          Serial.print(end);
          Serial.print(F(" (0x"));
          Serial.print(end, HEX);
          Serial.print(F("), "));
          Serial.print(BAUDS[b]);
          Serial.print(F(" baud, funcao 0x0"));
          Serial.print(funcao);
          if (r == RESP_EXCECAO) {
            Serial.print(F("  [excecao "));
            Serial.print(ultimaExcecao);
            Serial.print(F(": o sensor esta ai, mas o registrador 0 nao vale]"));
          }
          Serial.println();
          return true;
        }
        if (r == RESP_CRC_INVALIDO) Serial.print('?');  // resposta suja: baud errado
      }
      Serial.print('.');
    }
    Serial.println(F(" nada"));
  }

  Serial.println(F("  nenhum sensor respondeu."));
  Serial.println(F("  conferir: A/B trocados, alimentacao, GND comum, pino DE,"));
  Serial.print(F("  e enderecos acima de "));
  Serial.print(END_MAX);
  Serial.println(F(" (aumentar END_MAX)."));
  return false;
}

// ---------------------------------------------------------------------------
// Despejo dos registradores
// ---------------------------------------------------------------------------
void imprimirLinha(uint8_t reg, uint16_t bruto) {
  int16_t comSinal = (int16_t)bruto;

  // Codificacao usada por varios fabricantes chineses para temperatura
  // negativa: valor >= 10000 significa -(valor - 10000)/10.
  float offset10000 = (bruto >= 10000) ? -((float)(bruto - 10000)) / 10.0f
                                       : (float)bruto / 10.0f;

  Serial.print(F("  0x00"));
  if (reg < 0x10) Serial.print('0');
  Serial.print(reg, HEX);
  Serial.print(F("  bruto "));
  for (uint16_t casa = 10000; casa > 1; casa /= 10) if (bruto < casa) Serial.print(' ');
  Serial.print(bruto);
  Serial.print(F("  0x"));
  for (uint16_t casa = 0x1000; casa > 1; casa /= 0x10) if (bruto < casa) Serial.print('0');
  Serial.print(bruto, HEX);
  Serial.print(F("   /10 "));
  Serial.print(comSinal / 10.0f, 1);
  Serial.print(F("   /100 "));
  Serial.print(comSinal / 100.0f, 2);
  Serial.print(F("   off10000 "));
  Serial.println(offset10000, 1);
}

void despejar() {
  if (enderecoAtual == 0) {
    Serial.println(F("sem sensor localizado - rode a varredura ('r')"));
    return;
  }

  for (uint8_t f = 0; f < 2; f++) {
    uint8_t funcao = (f == 0) ? 0x03 : 0x04;
    Serial.println();
    Serial.print(F("== registradores 0x0000..0x00"));
    Serial.print(QTD_DESPEJO - 1, HEX);
    Serial.print(F(", funcao 0x0"));
    Serial.print(funcao);
    Serial.println(f == 0 ? F(" (holding) ==") : F(" (input) =="));

    uint16_t v[QTD_DESPEJO];
    bool ok[QTD_DESPEJO];
    if (!lerBloco(funcao, QTD_DESPEJO, v, ok)) {
      Serial.println(F("  (sem resposta nesta funcao)"));
      continue;
    }
    for (uint8_t i = 0; i < QTD_DESPEJO; i++) {
      if (ok[i]) imprimirLinha(i, v[i]);
    }
  }

  Serial.println();
  Serial.println(F("== o que procurar =="));
  Serial.println(F("  temperatura: coluna /100 entre 15 e 35, ou /10 na mesma faixa"));
  Serial.println(F("  umidade:     mesma logica, entre 30 e 90"));
  Serial.println(F("  eCO2:        bruto 400-2000 ppm (400 = ar livre; sopre e veja subir)"));
  Serial.println(F("  TVOC:        bruto 0-1000; encoste alcool perto e veja disparar"));
  Serial.println(F("  pressao:     em Pa ocupa 2 registradores, ~90000-101325."));
  Serial.println(F("               ao nivel do mar 101325 Pa; cai ~12 Pa a cada 1 m"));
  Serial.println(F("               de altitude, entao a ~900 m espere ~91000 Pa"));
  Serial.println();
  interpretarHipotese();
}

// ---------------------------------------------------------------------------
// Leitura pelo mapa confirmado
// ---------------------------------------------------------------------------
void interpretarHipotese() {
  uint16_t v[QTD_BLOCO];
  bool ok[QTD_BLOCO];
  if (!lerBloco(funcaoAtual, QTD_BLOCO, v, ok)) {
    Serial.println(F("== leitura: sem resposta =="));
    return;
  }

  Serial.println(F("== leitura pelo mapa confirmado =="));
  if (ok[REG_TEMP]) {
    Serial.print(F("  temperatura  "));
    Serial.print((int16_t)v[REG_TEMP] / 10.0f, 1);
    Serial.println(F(" C"));
  }
  if (ok[REG_UMID]) {
    Serial.print(F("  umidade      "));
    Serial.print(v[REG_UMID] / 10.0f, 1);
    Serial.println(F(" %RH"));
  }
  Serial.println();
}

// ---------------------------------------------------------------------------
// Monitoramento continuo
// ---------------------------------------------------------------------------
void monitorar() {
  uint16_t v[QTD_BLOCO];
  bool ok[QTD_BLOCO];
  if (!lerBloco(funcaoAtual, QTD_BLOCO, v, ok)) {
    Serial.println(F("falha na leitura"));
    return;
  }

  for (uint8_t i = 0; i < QTD_BLOCO; i++) {
    if (!ok[i]) continue;
    Serial.print(F("["));
    Serial.print(i, HEX);
    Serial.print(F("]"));
    Serial.print(v[i]);
    Serial.print(' ');
  }
  if (ok[REG_TEMP] && ok[REG_UMID]) {
    Serial.print(F(" | T="));
    Serial.print((int16_t)v[REG_TEMP] / 10.0f, 1);
    Serial.print(F("C  UR="));
    Serial.print(v[REG_UMID] / 10.0f, 1);
    Serial.print('%');
  }
  Serial.println();
}

// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println(F("Teste de sinal - sensor ambiental RS485"));
#if TEM_UART_DEDICADA
  Serial.println(F("placa com UART dedicada (Serial2, pinos 16/17)"));
#else
  Serial.println(F("placa sem UART sobrando - SoftwareSerial nos pinos 10/11"));
#endif

  if (PINO_DE >= 0) {
    pinMode(PINO_DE, OUTPUT);
    digitalWrite(PINO_DE, LOW);
  }

  if (varrer()) despejar();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'r') varrer();
    if (c == 'd') despejar();
  }

  if (enderecoAtual != 0) monitorar();
  delay(INTERVALO_MS);
}

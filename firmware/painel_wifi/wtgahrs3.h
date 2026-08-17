/*
 * Driver Modbus RTU para o sensor WitMotion WTGAHRS3-485, para ESP32.
 *
 * Porte do driver Python em src/wtgahrs3.py. Mesmo mapa de registradores,
 * mesmas formulas de conversao. Baseado no "WIT Standard Modbus Protocol":
 * https://wit-motion.gitbook.io/witmotion-sdk/wit-standard-modbus-protocol/modbus-communication-protocol
 *
 * Padroes de fabrica:
 *   - Endereco Modbus (slave id): 0x50 (80 decimal)
 *   - 9600 baud, 8 bits, sem paridade, 1 stop bit (8N1)
 *   - Funcao de leitura: 0x03 (read holding registers)
 *   - Funcao de escrita: 0x06 (write single register)
 *
 * Nao depende de biblioteca externa: o quadro Modbus e o CRC16 sao montados aqui.
 */

#pragma once

#include <Arduino.h>

// ---------------------------------------------------------------------------
// Mapa de registradores (endereco hexadecimal)
// ---------------------------------------------------------------------------
static const uint8_t REG_SAVE      = 0x00;  // salvar / reiniciar / restaurar fabrica
static const uint8_t REG_CALSW     = 0x01;  // modo de calibracao
static const uint8_t REG_BAUD      = 0x04;  // baud rate da porta serial
static const uint8_t REG_YYMM      = 0x30;  // data/hora (0x30..0x33)
static const uint8_t REG_AX        = 0x34;  // bloco IMU (0x34..0x3F)
static const uint8_t REG_TEMP      = 0x40;  // temperatura do chip
static const uint8_t REG_PRESSUREL = 0x45;  // pressao + altitude barometrica (0x45..0x48)
static const uint8_t REG_LONL      = 0x49;  // longitude/latitude (0x49..0x4C)
static const uint8_t REG_SVNUM     = 0x55;  // satelites + PDOP/HDOP/VDOP (0x55..0x58)
static const uint8_t REG_KEY       = 0x69;  // registrador de desbloqueio
static const uint8_t REG_TIMEZONE  = 0x6B;  // fuso horario do GPS

static const uint16_t CHAVE_DESBLOQUEIO = 0xB588;
static const uint8_t  ENDERECO_PADRAO   = 0x50;

// Fundo de escala padrao de fabrica
static const float ESCALA_ACC_G    = 16.0f;    // +/- 16 g
static const float ESCALA_GYRO_DPS = 2000.0f;  // +/- 2000 graus/s
static const float ESCALA_ANGULO   = 180.0f;   // +/- 180 graus

// ---------------------------------------------------------------------------
// Estruturas de dados
// ---------------------------------------------------------------------------

/** Bloco de posicao lido dos registradores 0x49..0x58. */
struct Posicao {
  double  latitude;        // graus decimais (negativo = sul)
  double  longitude;       // graus decimais (negativo = oeste)
  float   altitudeGpsM;    // metros, do receptor GNSS
  float   rumoGpsDeg;      // graus, direcao de deslocamento
  float   velocidadeKmh;   // km/h sobre o solo
  uint16_t satelites;      // numero de satelites em uso
  float   pdop;            // diluicao de precisao (posicao)
  float   hdop;            // diluicao de precisao (horizontal)
  float   vdop;            // diluicao de precisao (vertical)
  int32_t latBruto;        // valor int32 cru, util para depuracao
  int32_t lonBruto;

  /** True quando o GPS ja travou em satelites suficientes. */
  bool temFix() const { return satelites >= 4 && hdop > 0.0f; }
};

/** Pressao e altitude barometrica (0x45..0x48) + temperatura do chip (0x40). */
struct Ambiente {
  int32_t pressaoPa;
  float   altitudeBaroM;
  float   temperaturaC;
};

/** Bloco inercial (registradores 0x34..0x3F). */
struct Imu {
  float accX, accY, accZ;      // g
  float gyroX, gyroY, gyroZ;   // graus/s
  int16_t magX, magY, magZ;    // unidades brutas do magnetometro
  float roll, pitch, yaw;      // graus
};

// ---------------------------------------------------------------------------
// Conversoes (expostas para teste e depuracao)
// ---------------------------------------------------------------------------

/** Interpreta um registrador de 16 bits como inteiro com sinal. */
int16_t s16(uint16_t v);

/** Junta dois registradores (baixo, alto) num inteiro de 32 bits com sinal. */
int32_t s32(uint16_t baixo, uint16_t alto);

/**
 * Converte o int32 de coordenada da WitMotion para graus decimais.
 *
 * ATENCAO - a formula do manual oficial esta INCOMPLETA para o hemisferio
 * sul/oeste. O manual manda truncar o modulo, o que so funciona com
 * coordenadas positivas. O firmware do sensor monta o inteiro como
 *
 *     valor = graus_COM_SINAL * 1e7 + minutos_SEM_SINAL * 1e5
 *
 * ou seja, aplica o sinal do hemisferio so na parte dos graus. No Brasil os
 * minutos entram somados em vez de subtraidos e a formula do manual devolve
 * "minutos" acima de 60. A correcao e usar floor() na parte de graus.
 *
 * Exemplo real medido em Lavras/MG: bruto -208645974
 *     formula do manual -> -21.4412  (136 km errado)
 *     formula correta   -> -21.2255  (36 m do GPS do celular)
 *
 * LIMITACAO: coordenadas com |graus| < 1 sao ambiguas, porque o sinal se perde
 * quando a parte de graus e zero. Nao afeta o Brasil.
 */
double ddmmParaGraus(int32_t bruto);

// ---------------------------------------------------------------------------
// Driver
// ---------------------------------------------------------------------------
class WTGAHRS3 {
 public:
  /**
   * @param serial      UART ligada ao transceptor RS485 (use Serial2 no ESP32).
   * @param pinoDE      GPIO ligado a DE+RE do transceptor. Use -1 quando o
   *                    modulo faz o controle de direcao sozinho.
   * @param endereco    endereco Modbus do sensor (padrao de fabrica 0x50).
   */
  WTGAHRS3(HardwareSerial &serial, int8_t pinoDE, uint8_t endereco = ENDERECO_PADRAO);

  /** Abre a UART e configura o pino de direcao. Chamar dentro do setup(). */
  void iniciar(uint32_t baudrate = 9600, int8_t pinoRX = 16, int8_t pinoTX = 17);

  /** Leitura curta so para confirmar que o sensor responde. */
  bool estaRespondendo();

  // -- leituras -------------------------------------------------------------
  bool lerPosicao(Posicao &saida);
  bool lerAmbiente(Ambiente &saida);
  bool lerImu(Imu &saida);

  // -- configuracao ---------------------------------------------------------
  /** Destrava o sensor para aceitar escritas. Ele volta a travar em ~10 s. */
  bool desbloquear();

  /** Grava a configuracao atual na memoria nao volatil. */
  bool salvar();

  /**
   * Ajusta o fuso do GPS. Brasilia = UTC-3 -> utcOffset = -3.
   * O registrador usa 0 = UTC-12, entao grava (offset + 12).
   */
  bool definirFusoHorario(int8_t utcOffset);

  /** Tempo maximo de espera pela resposta, em milissegundos. */
  void definirTimeout(uint16_t ms) { _timeoutMs = ms; }

 private:
  HardwareSerial &_serial;
  int8_t   _pinoDE;
  uint8_t  _endereco;
  uint16_t _timeoutMs = 200;

  /** Le `quantidade` registradores consecutivos com a funcao 0x03. */
  bool lerRegistradores(uint8_t enderecoRegistrador, uint8_t quantidade, uint16_t *destino);

  /** Escreve um registrador com a funcao 0x06. */
  bool escreverRegistrador(uint8_t enderecoRegistrador, uint16_t valor);

  void enviarQuadro(const uint8_t *quadro, size_t tamanho);
  bool receberQuadro(uint8_t *buffer, size_t esperado);
  void limparEntrada();

  static uint16_t crc16(const uint8_t *dados, size_t tamanho);
};

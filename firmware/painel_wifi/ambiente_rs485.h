/*
 * Driver Modbus RTU para o sensor ambiental RS485 (temperatura/umidade),
 * para ESP32.
 *
 * Mapa de registradores confirmado na bancada (14/08/2026, unidade "Type 2"),
 * documentado em ../README.md:
 *
 *   endereco Modbus 1, 9600 8N1, funcao 0x03 (holding registers)
 *   0x0000  temperatura  /10  C, int16 com sinal
 *   0x0001  umidade      /10  %RH
 *
 * Este driver compartilha a UART e o pino DE com o WTGAHRS3 (mesmo par A/B,
 * enderecos Modbus diferentes) — nao chama serial.begin() nem configura o
 * pino DE; quem faz isso e WTGAHRS3::iniciar(), chamado antes deste driver
 * ser usado. Ver estacao_esp32.ino.
 *
 * Nao depende de biblioteca externa: o quadro Modbus e o CRC16 sao montados
 * aqui, igual ao driver do WTGAHRS3.
 */

#pragma once

#include <Arduino.h>

static const uint8_t ENDERECO_AMBIENTAL_PADRAO = 0x01;

/**
 * Silencio exigido antes de cada quadro (t3.5 do Modbus RTU).
 *
 * O protocolo delimita quadros pela pausa de 3,5 caracteres: a 9600 8N1
 * (10 bits por byte) sao 3,65 ms. 5 ms da margem. Sem essa pausa o sensor
 * trata o pedido como continuacao do quadro anterior e descarta calado.
 */
static const uint8_t SILENCIO_QUADRO_MS = 5;

/** Leitura do sensor ambiental (registradores 0x0000..0x0001). */
struct AmbienteRS485Leitura {
  float temperaturaC;
  float umidadeRH;
};

class AmbienteRS485 {
 public:
  /**
   * @param serial   UART ja iniciada (compartilhada com outro sensor no mesmo
   *                 barramento — ver WTGAHRS3::iniciar()).
   * @param pinoDE   mesmo pino DE+RE do transceptor; -1 se for automatico.
   * @param endereco endereco Modbus do sensor (padrao de fabrica 1).
   */
  AmbienteRS485(HardwareSerial &serial, int8_t pinoDE,
                uint8_t endereco = ENDERECO_AMBIENTAL_PADRAO);

  /** Leitura curta so para confirmar que o sensor responde. */
  bool estaRespondendo();

  /** Le temperatura e umidade. */
  bool ler(AmbienteRS485Leitura &saida);

  /** Tempo maximo de espera pela resposta, em milissegundos. */
  void definirTimeout(uint16_t ms) { _timeoutMs = ms; }

 private:
  HardwareSerial &_serial;
  int8_t   _pinoDE;
  uint8_t  _endereco;
  uint16_t _timeoutMs = 200;

  bool lerRegistradores(uint16_t enderecoRegistrador, uint8_t quantidade, uint16_t *destino);
  void enviarQuadro(const uint8_t *quadro, size_t tamanho);
  bool receberQuadro(uint8_t *buffer, size_t esperado);
  void limparEntrada();

  static uint16_t crc16(const uint8_t *dados, size_t tamanho);
};

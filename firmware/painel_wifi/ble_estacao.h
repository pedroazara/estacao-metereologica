/**
 * ble_estacao.h - Servico BLE da estacao, para o app de campo no celular.
 *
 * POR QUE BLE E NAO Wi-Fi. O app so precisa de telemetria ao vivo, que e
 * pouquissimo dado. Com o ESP32 servindo um AP sem internet, Android e iOS
 * tentam voltar para os dados moveis e roteiam o trafego para o lugar errado -
 * a pior dor de cabeca dessa topologia. BLE nao tem esse problema. O despejo
 * dos ~30 MB da coleta continua indo para a base por Wi-Fi, nao para o celular.
 * Ver docs/04-telemetria.md, secao "App nativo".
 *
 * O QUE TRAFEGA AQUI: o dado CRU, igual ao que vai no MQTT. Enquanto nao ha
 * cartao SD no ESP32, o celular e a CUSTODIA da coleta - a unica copia ate
 * chegar na base. Por isso nao pode ser resumo: docs/04-telemetria.md exige que
 * o cru sobreviva, porque "filtro que roda ao vivo e descarta o cru e
 * irreversivel". Uma sessao guardada so como media viraria dado que nunca mais
 * pode ser reprocessado com outro Q/R.
 *
 * O lote de IMU (~1230 B) nao cabe numa notificacao, entao vai FATIADO: cada
 * pedaco e um envelope JSON completo e independente, com `p` (indice) e `np`
 * (total). Nao ha protocolo de remontagem - cada pedaco se basta, e o app pode
 * gravar direto na fila sem esperar os irmaos.
 *
 * ATENCAO, DOIS REQUISITOS DE GRAVACAO:
 *
 *   1. PARTICAO. A pilha BLE custa ~730 KB de flash. Com a particao padrao o
 *      sketch nao cabe (1,75 MB contra 1,31 MB disponiveis) e o upload falha.
 *      Na IDE: Ferramentas > Partition Scheme > "Huge APP (3MB No OTA/1MB SPIFFS)".
 *      Pelo arduino-cli: --fqbn esp32:esp32:esp32:PartitionScheme=huge_app
 *
 *   2. MTU. O padrao do BLE e 23 bytes (20 uteis), pequeno demais para tudo
 *      menos o `amb`. O CLIENTE precisa PEDIR um MTU maior - no nRF Connect e
 *      a opcao "Request MTU", no flutter_blue_plus e requestMtu(517). Sem isso
 *      a notificacao chega truncada e o pedaco de IMU se perde inteiro. O
 *      firmware aceita ate 517, que e o teto do Bluedroid.
 */

#ifndef BLE_ESTACAO_H
#define BLE_ESTACAO_H

#include <Arduino.h>

namespace BleEstacao {

// UUIDs proprios da estacao. Nao use UUID de servico padrao (Nordic UART e
// afins): o app precisa distinguir esta estacao de qualquer outro periferico.
static const char *UUID_SERVICO = "e5710000-b100-4a1e-9d2f-7c3a5b8e1f40";
static const char *UUID_STATUS  = "e5710001-b100-4a1e-9d2f-7c3a5b8e1f40";
static const char *UUID_POS     = "e5710002-b100-4a1e-9d2f-7c3a5b8e1f40";
static const char *UUID_AMB     = "e5710003-b100-4a1e-9d2f-7c3a5b8e1f40";
static const char *UUID_IMU     = "e5710004-b100-4a1e-9d2f-7c3a5b8e1f40";
// A unica caracteristica gravavel: e por ela que o app configura a estacao.
static const char *UUID_CFG     = "e5710005-b100-4a1e-9d2f-7c3a5b8e1f40";
// Localizacao vinda do TELEFONE (write). O celular escreve aqui o proprio GPS e
// a estacao passa a emitir isso como `pos`, com a linha de tempo dela. Assim ha
// UMA fonte de dados so - o ESP -, com IMU, ambiente e posicao no mesmo `seq`.
static const char *UUID_LOC     = "e5710006-b100-4a1e-9d2f-7c3a5b8e1f40";

/** Sobe a pilha BLE e comeca a anunciar. Chame uma vez, depois do Wi-Fi. */
void iniciar(const char *nomeAnunciado);

/** true enquanto houver ao menos um celular conectado. */
bool conectado();

/**
 * Envia o JSON na caracteristica correspondente.
 *
 * Sem cliente conectado nao faz nada - notificar no vazio so gasta CPU. As
 * caracteristicas tambem sao legiveis (READ), entao um cliente que acabou de
 * conectar ve o ultimo valor sem esperar a proxima notificacao.
 */
void publicarStatus(const String &json);
void publicarPos(const String &json);
void publicarAmb(const String &json);
void publicarImu(const String &json);
void publicarCfg(const String &json);

/**
 * Devolve a configuracao escrita pelo app desde a ultima chamada, ou "" se
 * nada chegou.
 *
 * POR QUE NAO UM CALLBACK. O onWrite do BLE roda na task da pilha Bluetooth.
 * Aplicar a configuracao ali mexeria no escalonador do RS485 no meio de uma
 * transacao Modbus, que roda na task do loop() - corrida classica, e o sintoma
 * seria leitura corrompida esporadica, quase impossivel de rastrear. Aqui o
 * onWrite so guarda a string; quem aplica e o loop(), no seu proprio tempo.
 */
String configPendente();

/** Ultima localizacao escrita pelo telefone desde a ultima chamada, ou "". */
String localizacaoPendente();

/**
 * Chame no loop(). Existe por causa de uma armadilha do BLE: quando o cliente
 * desconecta, o anuncio PARA. Sem religar, a estacao vira invisivel para
 * sempre depois da primeira desconexao - e o sintoma ("funcionou uma vez e
 * nunca mais") parece defeito de hardware.
 */
void manter();

}  // namespace BleEstacao

#endif  // BLE_ESTACAO_H

#include "ble_estacao.h"

#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

namespace BleEstacao {
namespace {

BLEServer         *servidor   = nullptr;
BLECharacteristic *carStatus  = nullptr;
BLECharacteristic *carPos     = nullptr;
BLECharacteristic *carAmb     = nullptr;
BLECharacteristic *carImu     = nullptr;
BLECharacteristic *carCfg     = nullptr;
BLECharacteristic *carLoc     = nullptr;

volatile bool clienteConectado = false;
volatile bool precisaReanunciar = false;

// Escrito pela task do BLE, lido e limpo pela task do loop(). O portMUX
// protege a troca: sao tasks diferentes, possivelmente em nucleos diferentes.
portMUX_TYPE muxCfg = portMUX_INITIALIZER_UNLOCKED;
String cfgRecebida;
portMUX_TYPE muxLoc = portMUX_INITIALIZER_UNLOCKED;
String locRecebida;

// Teto do Bluedroid. O cliente ainda precisa PEDIR (ver o comentario no .h);
// isto so garante que o pedido nao seja recusado por limite nosso. Com 517
// negociados sobram 512 bytes por notificacao, que e o que dimensiona o
// tamanho do pedaco de IMU no .ino.
const uint16_t MTU_PREFERIDO = 517;

class Callbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *) override {
    clienteConectado = true;
    Serial.println(F("ble: celular conectado"));
  }

  // Nao chame startAdvertising() aqui: este callback roda na task do BLE, e
  // religar o anuncio de dentro dela pode correr com o desligamento da conexao.
  // Marca a bandeira e deixa o manter(), que roda no loop(), fazer o trabalho.
  void onDisconnect(BLEServer *) override {
    clienteConectado = false;
    precisaReanunciar = true;
    Serial.println(F("ble: celular desconectou"));
  }
};

Callbacks callbacks;

class CallbacksCfg : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *c) override {
    String v = c->getValue();
    if (v.length() == 0) return;
    portENTER_CRITICAL(&muxCfg);
    cfgRecebida = v;
    portEXIT_CRITICAL(&muxCfg);
  }
};

CallbacksCfg callbacksCfg;

class CallbacksLoc : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *c) override {
    String v = c->getValue();
    if (v.length() == 0) return;
    portENTER_CRITICAL(&muxLoc);
    locRecebida = v;
    portEXIT_CRITICAL(&muxLoc);
  }
};

CallbacksLoc callbacksLoc;

BLECharacteristic *criar(BLEService *servico, const char *uuid) {
  BLECharacteristic *c = servico->createCharacteristic(
      uuid, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  // O 2902 e o descritor que deixa o cliente LIGAR as notificacoes. Sem ele o
  // app consegue ler, mas nunca recebe nada sozinho - e o sintoma engana,
  // porque o servico aparece certinho no scanner.
  c->addDescriptor(new BLE2902());
  return c;
}

void notificar(BLECharacteristic *c, const String &json) {
  if (c == nullptr) return;
  c->setValue(json);            // vale mesmo sem cliente: deixa o valor legivel
  if (clienteConectado) c->notify();
}

}  // namespace

void iniciar(const char *nomeAnunciado) {
  BLEDevice::init(String(nomeAnunciado));
  BLEDevice::setMTU(MTU_PREFERIDO);

  servidor = BLEDevice::createServer();
  servidor->setCallbacks(&callbacks);

  BLEService *servico = servidor->createService(UUID_SERVICO);
  carStatus = criar(servico, UUID_STATUS);
  carPos    = criar(servico, UUID_POS);
  carAmb    = criar(servico, UUID_AMB);
  carImu    = criar(servico, UUID_IMU);

  // A de configuracao e a unica com WRITE. WRITE (com resposta) e nao
  // WRITE_NR de proposito: configuracao que se perde em silencio e pior que
  // configuracao que falha com erro.
  carCfg = servico->createCharacteristic(
      UUID_CFG, BLECharacteristic::PROPERTY_READ |
                BLECharacteristic::PROPERTY_WRITE |
                BLECharacteristic::PROPERTY_NOTIFY);
  carCfg->addDescriptor(new BLE2902());
  carCfg->setCallbacks(&callbacksCfg);

  // Localizacao do telefone: write puro (sem notify/read - o app so escreve).
  carLoc = servico->createCharacteristic(
      UUID_LOC, BLECharacteristic::PROPERTY_WRITE);
  carLoc->setCallbacks(&callbacksLoc);

  servico->start();

  BLEAdvertising *anuncio = BLEDevice::getAdvertising();
  anuncio->addServiceUUID(UUID_SERVICO);
  // Sem o UUID no pacote de anuncio o app teria de conectar em cada periferico
  // so para descobrir se e a estacao. Com ele, o scan filtra direto.
  anuncio->setScanResponse(true);
  BLEDevice::startAdvertising();

  Serial.print(F("ble: anunciando como '"));
  Serial.print(nomeAnunciado);
  Serial.println(F("'"));
}

bool conectado() {
  return clienteConectado;
}

void publicarStatus(const String &json) { notificar(carStatus, json); }
void publicarPos(const String &json)    { notificar(carPos, json); }
void publicarAmb(const String &json)    { notificar(carAmb, json); }
void publicarImu(const String &json)    { notificar(carImu, json); }
void publicarCfg(const String &json)    { notificar(carCfg, json); }

String configPendente() {
  String v;
  portENTER_CRITICAL(&muxCfg);
  v = cfgRecebida;
  cfgRecebida = "";
  portEXIT_CRITICAL(&muxCfg);
  return v;
}

String localizacaoPendente() {
  String v;
  portENTER_CRITICAL(&muxLoc);
  v = locRecebida;
  locRecebida = "";
  portEXIT_CRITICAL(&muxLoc);
  return v;
}

void manter() {
  if (!precisaReanunciar) return;
  precisaReanunciar = false;
  // A pilha precisa de um instante para fechar a conexao antes de reanunciar.
  delay(50);
  BLEDevice::startAdvertising();
  Serial.println(F("ble: anunciando de novo"));
}

}  // namespace BleEstacao

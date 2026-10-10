/// Contrato BLE da estacao — o espelho exato de firmware/painel_wifi/ble_estacao.h.
///
/// Se um UUID mudar de um lado, tem que mudar do outro. Esta e a unica fonte
/// desses valores no app: nada de UUID solto no meio de um widget.
library;

class Contrato {
  /// Nome anunciado pelo ESP32 (o DEVICE_ID do firmware). O scan filtra por
  /// UUID de servico, nao por nome — o nome e so o que o usuario le na lista.
  static const nomePadrao = 'esp32-01';

  static const servico = 'e5710000-b100-4a1e-9d2f-7c3a5b8e1f40';
  static const status = 'e5710001-b100-4a1e-9d2f-7c3a5b8e1f40';
  static const pos = 'e5710002-b100-4a1e-9d2f-7c3a5b8e1f40';
  static const amb = 'e5710003-b100-4a1e-9d2f-7c3a5b8e1f40';
  static const imu = 'e5710004-b100-4a1e-9d2f-7c3a5b8e1f40';
  static const cfg = 'e5710005-b100-4a1e-9d2f-7c3a5b8e1f40';
  // Escrita: o telefone manda o proprio GPS, e o ESP re-emite como `pos`.
  static const loc = 'e5710006-b100-4a1e-9d2f-7c3a5b8e1f40';

  /// MTU pedido logo apos conectar.
  ///
  /// NAO E OPCIONAL. O padrao do BLE e 23 bytes (20 uteis) e um pedaco de IMU
  /// tem ~400. Sem subir o MTU, as notificacoes de `pos` e `imu` chegam
  /// truncadas no meio do JSON e o parse falha em toda amostra — o app pareceria
  /// funcionar (conecta, mostra o ambiente) e perderia justamente o dado que
  /// veio guardar. 517 e o teto do Bluedroid, que e o que o ESP32 usa.
  static const mtu = 517;
}

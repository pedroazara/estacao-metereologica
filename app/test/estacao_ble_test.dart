import 'dart:async';

import 'package:estacao_movel/ble/contrato.dart';
import 'package:estacao_movel/ble/estacao_ble.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
// Interface do plugin ja resolvida pelo flutter_blue_plus: simula apenas o
// radio, mantendo o timeout e os streams reais da biblioteca no teste.
// ignore: depend_on_referenced_packages
import 'package:flutter_blue_plus_platform_interface/flutter_blue_plus_platform_interface.dart';
import 'package:flutter_test/flutter_test.dart';

final class _RadioSimulado extends FlutterBluePlusPlatform {
  final adaptador = StreamController<BmBluetoothAdapterState>.broadcast();
  final anuncios = StreamController<BmScanResponse>.broadcast();
  final ligacao = StreamController<BmConnectionStateResponse>.broadcast();
  int buscas = 0;
  int paradas = 0;
  int conexoes = 0;

  @override
  Stream<BmBluetoothAdapterState> get onAdapterStateChanged => adaptador.stream;

  @override
  Stream<BmScanResponse> get onScanResponse => anuncios.stream;

  @override
  Stream<BmConnectionStateResponse> get onConnectionStateChanged =>
      ligacao.stream;

  @override
  Future<bool> isSupported(BmIsSupportedRequest request) async {
    adaptador.add(BmBluetoothAdapterState(adapterState: BmAdapterStateEnum.on));
    return true;
  }

  @override
  Future<bool> startScan(BmScanSettings request) async {
    expectSync(request.withServices, [Guid(Contrato.servico)]);
    buscas++;
    return true;
  }

  @override
  Future<bool> stopScan(BmStopScanRequest request) async {
    paradas++;
    return true;
  }

  @override
  Future<bool> connect(BmConnectRequest request) async {
    conexoes++;
    // Basta observar que a descoberta chegou a tentar a conexao. GATT e
    // notificacoes dependem do dispositivo e nao sao o alvo desta regressao.
    throw StateError('conexao simulada');
  }

  void anunciarEstacao() {
    anuncios.add(
      BmScanResponse(
        advertisements: [
          BmScanAdvertisement(
            remoteId: const DeviceIdentifier('00:11:22:33:44:55'),
            platformName: 'esp32-01',
            advName: 'esp32-01',
            connectable: true,
            txPowerLevel: null,
            appearance: null,
            manufacturerData: {},
            serviceData: {},
            serviceUuids: [Guid(Contrato.servico)],
            rssi: -50,
          ),
        ],
        success: true,
        errorCode: 0,
        errorString: '',
      ),
    );
  }
}

// O cancelamento dos streams do plugin conclui na zona real do runtime.
// Libera essa fila antes de conferir o resultado com o relogio simulado.
Future<void> _avancar(WidgetTester tester, [Duration? duracao]) async {
  await tester.pump(duracao);
  await tester.runAsync(() => Future<void>.delayed(Duration.zero));
  await tester.pump();
}

void main() {
  final radio = _RadioSimulado();

  setUpAll(() {
    FlutterBluePlusPlatform.instance = radio;
  });

  setUp(() {
    radio.buscas = 0;
    radio.paradas = 0;
    radio.conexoes = 0;
  });

  testWidgets('busca vazia termina depois de 15 segundos', (tester) async {
    final ble = EstacaoBle();
    final tentativa = ble.procurarEConectar();
    await _avancar(tester);
    expect(ble.estado, EstadoLigacao.procurando, reason: ble.mensagem);
    expect(radio.buscas, 1);

    await _avancar(tester, const Duration(seconds: 15));
    await _avancar(tester);
    expect(
      ble.estado,
      EstadoLigacao.desligado,
      reason:
          '${ble.mensagem}; scanner=${FlutterBluePlus.isScanningNow}; paradas=${radio.paradas}',
    );
    expect(ble.mensagem, contains('estacao nao encontrada'));
    expect(FlutterBluePlus.isScanningNow, isFalse);
    await tentativa;
    ble.dispose();
  });

  testWidgets('tenta novamente e encontra estacao ligada depois', (
    tester,
  ) async {
    final ble = EstacaoBle();
    ble.iniciarAutoConexao();
    await _avancar(tester);
    await _avancar(tester, const Duration(seconds: 15));
    await _avancar(tester, const Duration(seconds: 5));
    expect(radio.buscas, 2);
    expect(ble.estado, EstadoLigacao.procurando);

    radio.anunciarEstacao();
    await _avancar(tester);
    expect(radio.conexoes, 1);
    expect(FlutterBluePlus.isScanningNow, isFalse);
    ble.dispose();
    await _avancar(tester);
  });

  testWidgets('desconectar cancela uma busca pendente', (tester) async {
    final ble = EstacaoBle();
    final tentativa = ble.procurarEConectar();
    await _avancar(tester);
    final desconexao = ble.desconectar();
    await _avancar(tester);
    await desconexao;
    await tentativa;
    radio.anunciarEstacao();
    await _avancar(tester);
    expect(ble.estado, EstadoLigacao.desligado);
    expect(ble.mensagem, 'desconectado');
    expect(radio.conexoes, 0);
    expect(FlutterBluePlus.isScanningNow, isFalse);
    ble.dispose();
  });

  testWidgets('fechar cancela scanner e tentativas automaticas', (
    tester,
  ) async {
    final ble = EstacaoBle();
    ble.iniciarAutoConexao();
    await _avancar(tester);
    ble.dispose();
    await _avancar(tester);
    radio.anunciarEstacao();
    await _avancar(tester, const Duration(seconds: 30));
    expect(radio.buscas, 1);
    expect(radio.conexoes, 0);
    expect(FlutterBluePlus.isScanningNow, isFalse);
  });
}

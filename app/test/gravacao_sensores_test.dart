import 'dart:async';
import 'dart:convert';

import 'package:estacao_movel/armazenamento/gravador.dart';
import 'package:estacao_movel/ble/contrato.dart';
import 'package:estacao_movel/ble/estacao_ble.dart';
import 'package:estacao_movel/gps/gps_telefone.dart';
import 'package:estacao_movel/modelo/sensores.dart';
import 'package:flutter_test/flutter_test.dart';
// ignore: depend_on_referenced_packages
import 'package:flutter_blue_plus_platform_interface/flutter_blue_plus_platform_interface.dart';

final class _RadioAusente extends FlutterBluePlusPlatform {
  @override
  Future<bool> isSupported(BmIsSupportedRequest request) async => false;
  @override
  Future<bool> stopScan(BmStopScanRequest request) async => true;
}

class _Gravador extends Gravador {
  bool aberto = false;
  Completer<void>? espera;
  final linhasSalvas = <Map<String, dynamic>>[];
  @override
  bool get gravando => aberto;
  @override
  Future<void> iniciar(String dev) async {
    aberto = true;
    await espera?.future;
  }

  @override
  void anexar(String origem, Map<String, dynamic> corpo) {
    if (aberto) linhasSalvas.add({'src': origem, 'm': Map.of(corpo)});
  }

  @override
  Future<void> encerrar() async => aberto = false;
}

class _Gps extends GpsTelefone {
  void emitir({double latitude = -23, DateTime? instante}) {
    lat = latitude;
    lon = -46;
    precisaoM = 3;
    velKmh = 0;
    rumo = 0;
    ultimaLeitura = instante ?? DateTime.now();
    aoReceber?.call(lat!, lon!, precisaoM!, velKmh!, rumo!);
    notifyListeners();
  }
}

void _amb(EstacaoBle ble, Map<String, dynamic> pacote) =>
    ble.processarPacote(Contrato.amb, utf8.encode(jsonEncode(pacote)));

void main() {
  setUpAll(() => FlutterBluePlusPlatform.instance = _RadioAusente());

  late _Gravador gravador;
  late _Gps gps;
  late EstacaoBle ble;
  setUp(() {
    gravador = _Gravador();
    gps = _Gps();
    ble = EstacaoBle(gravador: gravador, gps: gps);
  });
  tearDown(() => ble.dispose());

  test('salva metadados antes do GPS e mantém snapshot da seleção', () async {
    gps.emitir();
    final selecionados = {SensorId.gpsCelular, SensorId.ambienteRs485};
    await ble.iniciarGravacao(sensores: selecionados);
    selecionados
      ..clear()
      ..add(SensorId.luminosidadeGy302);
    _amb(ble, {'seq': 2, 'temp': 25.5, 'umid': 60, 'lux': 80, 'db': 55});
    gps.emitir(latitude: -23.001);

    expect(gravador.linhasSalvas.first, {
      'src': 'selecao',
      'm': {
        'sensores': ['gpsCelular', 'ambienteRs485'],
      },
    });
    expect(gravador.linhasSalvas[1]['src'], 'gps');
    expect(gravador.linhasSalvas[2], {
      'src': 'amb',
      'm': {'seq': 2, 'temp': 25.5, 'umid': 60},
    });
    expect(ble.trilha.length, 2);
  });

  test(
    'não grava GPS nem cria trilha quando GPS não foi selecionado',
    () async {
      gps.emitir();
      await ble.iniciarGravacao(sensores: {SensorId.ruidoKy038});
      gps.emitir(latitude: -23.1);
      _amb(ble, {'seq': 1, 'temp': 30, 'db': 45.2});
      expect(
        ble.telLat,
        -23.1,
        reason: 'prévia continua independente da coleta',
      );
      expect(ble.trilha, isEmpty);
      expect(
        gravador.linhasSalvas.where((linha) => linha['src'] == 'gps'),
        isEmpty,
      );
      expect(gravador.linhasSalvas.last['m'], {'seq': 1, 'db': 45.2});
    },
  );

  test(
    'coleta somente com sensores do celular não inclui pacotes ambientais',
    () async {
      await ble.iniciarGravacao(sensores: {SensorId.somCelular});
      _amb(ble, {'seq': 1, 'temp': 30, 'umid': 50, 'db': 40});
      expect(gravador.linhasSalvas.length, 1);
      expect(ble.camposAmbienteValidos, {'temp', 'umid', 'db'});
      expect(ble.ultimaLeituraAmbiente, isNotNull);
    },
  );

  test('filtra campos ausentes, não numéricos e lux indisponível sem inventar zeros', () async {
    await ble.iniciarGravacao(
      sensores: {
        SensorId.ambienteRs485,
        SensorId.luminosidadeGy302,
        SensorId.temperaturaKy013,
      },
    );
    _amb(ble, {'seq': 3, 'temp': 0, 'umid': '50', 'lux': -1, 't13': -5});
    expect(ble.camposAmbienteValidos, {'temp', 't13'});
    expect(gravador.linhasSalvas.last['m'], {'seq': 3, 'temp': 0, 't13': -5});
    expect(() => ble.camposAmbienteValidos.add('lux'), throwsUnsupportedError);

    _amb(ble, {'seq': 4, 'lux': 0});
    expect(ble.camposAmbienteValidos, {'lux'});
    expect(gravador.linhasSalvas.last['m'], {'seq': 4, 'lux': 0});
    _amb(ble, {'seq': 5});
    expect(ble.camposAmbienteValidos, isEmpty);
    expect(gravador.linhasSalvas.length, 3);
  });

  test('valor não finito recebido não entra nos campos válidos', () async {
    await ble.iniciarGravacao(sensores: {SensorId.ambienteRs485});
    ble.processarPacote(
      Contrato.amb,
      utf8.encode('{"seq":1,"temp":1e999,"umid":42}'),
    );
    expect(ble.camposAmbienteValidos, {'umid'});
    expect(gravador.linhasSalvas.last['m'], {'seq': 1, 'umid': 42});
  });

  test(
    'chamada legada continua gravando GPS e pacote ambiental completo',
    () async {
      gps.emitir();
      await ble.iniciarGravacao();
      final pacote = {'seq': 1, 'temp': 24, 'umid': 50, 'extra': 123};
      _amb(ble, pacote);
      expect(gravador.linhasSalvas[1]['src'], 'gps');
      expect(gravador.linhasSalvas.last['m'], pacote);
    },
  );

  test('fix antigo em cache não vira ponto inicial de uma coleta', () async {
    gps.emitir(instante: DateTime.now().subtract(const Duration(seconds: 20)));
    await ble.iniciarGravacao(sensores: {SensorId.gpsCelular});
    expect(ble.trilha, isEmpty);
    expect(gravador.linhasSalvas.length, 1);
    gps.emitir();
    expect(ble.trilha.length, 1);
  });

  test(
    'pacotes durante abertura não ultrapassam os metadados de seleção',
    () async {
      gravador.espera = Completer<void>();
      final inicio = ble.iniciarGravacao(
        sensores: {SensorId.gpsCelular, SensorId.ambienteRs485},
      );
      gps.emitir();
      _amb(ble, {'seq': 1, 'temp': 20, 'umid': 40});
      expect(gravador.linhasSalvas, isEmpty);
      gravador.espera!.complete();
      await inicio;
      expect(gravador.linhasSalvas.first['src'], 'selecao');
      expect(gravador.linhasSalvas[1]['src'], 'gps');
    },
  );

  test('parar durante abertura impede iniciar uma gravação depois', () async {
    gravador.espera = Completer<void>();
    final inicio = ble.iniciarGravacao(sensores: {SensorId.gpsCelular});
    await ble.pararGravacao();
    gravador.espera!.complete();
    await inicio;
    expect(gravador.gravando, isFalse);
    expect(gravador.linhasSalvas, isEmpty);
  });

  test('desconectar elimina leituras ambientais antigas da prévia', () async {
    _amb(ble, {'seq': 1, 'temp': 20, 'umid': 40});
    expect(ble.ultimaLeituraAmbiente, isNotNull);
    await ble.desconectar();
    expect(ble.ultimaLeituraAmbiente, isNull);
    expect(ble.camposAmbienteValidos, isEmpty);
    expect(ble.ambiente, isNull);
  });
}

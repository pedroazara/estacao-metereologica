import 'dart:async';

import 'package:estacao_movel/gps/gps_telefone.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:geolocator/geolocator.dart';

class _Localizacao extends GeolocatorPlatform {
  final posicoes = StreamController<Position>.broadcast(sync: true);
  Completer<LocationPermission>? permissao;
  bool habilitado = true;
  int streams = 0;

  @override
  Future<bool> isLocationServiceEnabled() async => habilitado;
  @override
  Future<LocationPermission> checkPermission() async => permissao == null
      ? LocationPermission.whileInUse
      : await permissao!.future;
  @override
  Stream<Position> getPositionStream({LocationSettings? locationSettings}) {
    streams++;
    return posicoes.stream;
  }
}

Position _posicao(DateTime instante, {double latitude = -23}) => Position(
  longitude: -46,
  latitude: latitude,
  timestamp: instante,
  accuracy: 3,
  altitude: 0,
  altitudeAccuracy: 0,
  heading: 0,
  headingAccuracy: 0,
  speed: 0,
  speedAccuracy: 0,
);

void main() {
  late GeolocatorPlatform anterior;
  late _Localizacao plataforma;
  late GpsTelefone gps;
  setUp(() {
    anterior = GeolocatorPlatform.instance;
    plataforma = _Localizacao();
    GeolocatorPlatform.instance = plataforma;
    gps = GpsTelefone();
  });
  tearDown(() async {
    gps.dispose();
    await plataforma.posicoes.close();
    GeolocatorPlatform.instance = anterior;
  });

  test(
    'freshness só existe após fix e respeita a data original da posição',
    () async {
      await gps.iniciar();
      expect(gps.ultimaLeitura, isNull);
      final antigo = DateTime.now().subtract(const Duration(seconds: 20));
      plataforma.posicoes.add(_posicao(antigo));
      expect(gps.ultimaLeitura, antigo);
      final atual = DateTime.now();
      plataforma.posicoes.add(_posicao(atual));
      expect(gps.ultimaLeitura, atual);
      expect(gps.lat, -23);
    },
  );

  test(
    'erro e parada limpam posição e freshness sem impedir recuperação',
    () async {
      await gps.iniciar();
      plataforma.posicoes.add(_posicao(DateTime.now()));
      plataforma.posicoes.addError(StateError('sinal perdido'));
      expect(gps.ultimaLeitura, isNull);
      expect(gps.lat, isNull);
      plataforma.posicoes.add(_posicao(DateTime.now()));
      expect(gps.ultimaLeitura, isNotNull);
      await gps.parar();
      expect(gps.ultimaLeitura, isNull);
      expect(gps.lat, isNull);
      expect(gps.ativo, isFalse);
      plataforma.posicoes.add(_posicao(DateTime.now()));
      expect(gps.ultimaLeitura, isNull);
    },
  );

  test(
    'posição inválida não gera callback nem aparenta estar disponível',
    () async {
      var recebidos = 0;
      gps.aoReceber = (_, _, _, _, _) => recebidos++;
      await gps.iniciar();
      plataforma.posicoes.add(_posicao(DateTime.now(), latitude: double.nan));
      expect(recebidos, 0);
      expect(gps.ultimaLeitura, isNull);
      expect(gps.lat, isNull);
    },
  );

  test(
    'parar enquanto aguarda permissão impede abrir stream posteriormente',
    () async {
      plataforma.permissao = Completer<LocationPermission>();
      final inicio = gps.iniciar();
      await Future<void>.delayed(Duration.zero);
      await gps.parar();
      plataforma.permissao!.complete(LocationPermission.whileInUse);
      await inicio;
      expect(plataforma.streams, 0);
      expect(gps.ativo, isFalse);
    },
  );

  test(
    'pedidos simultâneos de início compartilham uma única captura',
    () async {
      await Future.wait([gps.iniciar(), gps.iniciar()]);
      expect(plataforma.streams, 1);
    },
  );
}

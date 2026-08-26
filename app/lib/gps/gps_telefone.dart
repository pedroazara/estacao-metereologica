import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:geolocator/geolocator.dart';

/// GPS do proprio telefone — a fonte de posicao atual do projeto.
///
/// POR QUE O DO TELEFONE. Decisao de 25/08/2026: o GPS do WTGAHRS3 fica dormente
/// (desligavel pelo interruptor da aba Estacao) e a posicao vem do celular, que
/// pega fix mais rapido com A-GPS. O do ESP continua sendo GRAVADO quando ligado,
/// para a comparacao de drift ESP-vs-telefone mais adiante — ver docs/04.
///
/// Este servico so captura e entrega. Quem funde com o IMU (Kalman), grava e
/// desenha e o EstacaoBle, para manter uma fonte de verdade so.
class GpsTelefone extends ChangeNotifier {
  double? lat, lon, precisaoM, velKmh, rumo;
  bool permitido = false;
  String mensagem = 'GPS do telefone desligado';

  StreamSubscription<Position>? _sub;

  /// Callback para o EstacaoBle: cada posicao nova entra no Kalman e na gravacao.
  void Function(double lat, double lon, double precisaoM, double velKmh,
      double rumo)? aoReceber;

  bool get ativo => _sub != null;

  Future<void> iniciar() async {
    if (_sub != null) return;
    if (!await _garantirPermissao()) return;
    permitido = true;
    mensagem = 'GPS do telefone ligado';

    // distanceFilter 0: queremos toda atualizacao, inclusive parado, para o
    // Kalman ver o jitter e o ZUPT poder agir. accuracy best: e a fonte de
    // posicao, vale o custo de bateria enquanto o app esta aberto.
    const cfg = LocationSettings(
        accuracy: LocationAccuracy.bestForNavigation, distanceFilter: 0);
    _sub = Geolocator.getPositionStream(locationSettings: cfg).listen((p) {
      lat = p.latitude;
      lon = p.longitude;
      precisaoM = p.accuracy;
      velKmh = p.speed * 3.6; // m/s -> km/h
      rumo = p.heading;
      aoReceber?.call(p.latitude, p.longitude, p.accuracy, velKmh!, p.heading);
      notifyListeners();
    }, onError: (e) {
      mensagem = 'erro no GPS: $e';
      notifyListeners();
    });
    notifyListeners();
  }

  Future<bool> _garantirPermissao() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      mensagem = 'ligue a localizacao do telefone';
      notifyListeners();
      return false;
    }
    var p = await Geolocator.checkPermission();
    if (p == LocationPermission.denied) {
      p = await Geolocator.requestPermission();
    }
    if (p == LocationPermission.denied ||
        p == LocationPermission.deniedForever) {
      mensagem = 'permissao de localizacao negada';
      notifyListeners();
      return false;
    }
    return true;
  }

  Future<void> parar() async {
    await _sub?.cancel();
    _sub = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }
}

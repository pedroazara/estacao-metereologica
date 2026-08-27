import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
// AndroidSettings vem reexportado por este pacote - nao precisa importar
// geolocator_android separado.
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
    try {
      if (!await _garantirPermissao()) return;
    } catch (_) {
      // Sem plataforma de localizacao (ex.: ambiente de teste): nao e fatal, o
      // app segue funcionando so sem GPS do telefone.
      mensagem = 'localizacao indisponivel';
      notifyListeners();
      return;
    }
    permitido = true;
    mensagem = 'GPS do telefone ligado';

    // distanceFilter 0: queremos toda atualizacao, inclusive parado, para o
    // ZUPT (deteccao de repouso) e o traçado terem o jitter real. accuracy
    // best: e a fonte de posicao, vale o custo de bateria enquanto o app
    // esta aberto.
    //
    // intervalDuration EXPLICITO: sem isso, o proprio pacote geolocator_android
    // documenta que aplica 5000ms de intervalo por padrao. Era essa a causa da
    // "atualizacao lenta" e da trajetoria com poucos pontos — o GPS so avisava
    // a cada 5 s, entao andando a pe (~1,4 m/s) cada ponto ficava a uns 7 m do
    // anterior, e a linha saia em degraus em vez de acompanhar a rua.
    final cfg = Platform.isAndroid
        ? AndroidSettings(
            accuracy: LocationAccuracy.bestForNavigation,
            distanceFilter: 0,
            intervalDuration: const Duration(milliseconds: 1000),
          )
        : const LocationSettings(
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

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
  DateTime? ultimaLeitura;
  bool permitido = false;
  String mensagem = 'GPS do telefone desligado';

  StreamSubscription<Position>? _sub;
  int _geracao = 0;
  bool _iniciando = false;
  bool _vivo = true;

  /// Callback para o EstacaoBle: cada posicao nova entra no Kalman e na gravacao.
  void Function(
    double lat,
    double lon,
    double precisaoM,
    double velKmh,
    double rumo,
  )?
  aoReceber;

  bool get ativo => _sub != null;

  Future<void> iniciar() async {
    if (_sub != null || _iniciando || !_vivo) return;
    _iniciando = true;
    final geracao = ++_geracao;
    try {
      if (!await _garantirPermissao(geracao)) return;
      if (!_vivo || geracao != _geracao) return;
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
              accuracy: LocationAccuracy.bestForNavigation,
              distanceFilter: 0,
            );
      _sub = Geolocator.getPositionStream(locationSettings: cfg).listen(
        (p) {
          if (!_vivo || geracao != _geracao) return;
          if (!p.latitude.isFinite ||
              !p.longitude.isFinite ||
              p.latitude.abs() > 90 ||
              p.longitude.abs() > 180 ||
              !p.accuracy.isFinite ||
              p.accuracy < 0) {
            _limparFix();
            mensagem = 'o GPS retornou uma posição inválida';
            notifyListeners();
            return;
          }
          lat = p.latitude;
          lon = p.longitude;
          precisaoM = p.accuracy;
          velKmh = p.speed * 3.6; // m/s -> km/h
          rumo = p.heading;
          final agora = DateTime.now();
          ultimaLeitura = p.timestamp.isAfter(agora) ? agora : p.timestamp;
          mensagem = 'GPS do telefone ligado';
          aoReceber?.call(
            p.latitude,
            p.longitude,
            p.accuracy,
            velKmh!,
            p.heading,
          );
          notifyListeners();
        },
        onError: (e) {
          if (!_vivo || geracao != _geracao) return;
          _limparFix();
          mensagem = 'erro no GPS: $e';
          notifyListeners();
        },
        onDone: () {
          if (!_vivo || geracao != _geracao) return;
          _sub = null;
          _limparFix();
          mensagem = 'GPS do telefone desligado';
          notifyListeners();
        },
      );
      notifyListeners();
    } catch (_) {
      if (!_vivo || geracao != _geracao) return;
      _limparFix();
      mensagem = 'localizacao indisponivel';
      notifyListeners();
    } finally {
      if (geracao == _geracao) _iniciando = false;
    }
  }

  Future<bool> _garantirPermissao(int geracao) async {
    final habilitado = await Geolocator.isLocationServiceEnabled();
    if (!_vivo || geracao != _geracao) return false;
    if (!habilitado) {
      permitido = false;
      _limparFix();
      mensagem = 'ligue a localizacao do telefone';
      notifyListeners();
      return false;
    }
    var p = await Geolocator.checkPermission();
    if (!_vivo || geracao != _geracao) return false;
    if (p == LocationPermission.denied) {
      p = await Geolocator.requestPermission();
      if (!_vivo || geracao != _geracao) return false;
    }
    if (p == LocationPermission.denied ||
        p == LocationPermission.deniedForever) {
      permitido = false;
      _limparFix();
      mensagem = 'permissao de localizacao negada';
      notifyListeners();
      return false;
    }
    return true;
  }

  Future<void> parar() async {
    _geracao++;
    _iniciando = false;
    final assinatura = _sub;
    _sub = null;
    _limparFix();
    mensagem = 'GPS do telefone desligado';
    notifyListeners();
    await assinatura?.cancel();
  }

  void _limparFix() {
    ultimaLeitura = null;
    lat = null;
    lon = null;
    precisaoM = null;
    velKmh = null;
    rumo = null;
  }

  @override
  void notifyListeners() {
    if (_vivo) super.notifyListeners();
  }

  @override
  void dispose() {
    _vivo = false;
    _geracao++;
    _limparFix();
    _sub?.cancel();
    super.dispose();
  }
}

import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';

import 'sincronizador.dart';

/// Verifica, de tempos em tempos, se a base esta alcancavel pela rede.
///
/// E so um sinal para a UI ("da para sincronizar agora?"). Nao segura nada: a
/// coleta por BLE e a gravacao no telefone funcionam com a base fora de
/// alcance, que e o caso normal em campo. O verde aqui significa "voce esta na
/// rede da base", nao "a estacao esta funcionando".
class BaseStatus extends ChangeNotifier {
  bool alcancavel = false;
  bool pausado = false; // true = gravando/em campo: nao fala com a base
  String host = 'raspberrypi.local';
  bool _verificando = false;
  Timer? _timer;

  void iniciar() {
    _carregarHost();
    _verificar();
    _timer = Timer.periodic(const Duration(seconds: 10), (_) => _verificar());
  }

  /// Em campo (gravando) o telefone nao deve procurar a base: economiza bateria
  /// e reflete a realidade — ele saiu do alcance do laboratorio. Idempotente.
  void pausar() {
    if (pausado) return;
    pausado = true;
    alcancavel = false;
    notifyListeners();
  }

  void retomar() {
    if (!pausado) return;
    pausado = false;
    notifyListeners();
    _verificar();
  }

  Future<void> _carregarHost() async {
    host = await Sincronizador.carregarBase();
    notifyListeners();
  }

  /// Chamada quando o usuario troca o endereco da base na tela de Sessoes, para
  /// o indicador refletir o host novo sem esperar o proximo ciclo.
  Future<void> agora() async {
    await _carregarHost();
    await _verificar();
  }

  Future<void> _verificar() async {
    if (_verificando || pausado) return;
    _verificando = true;
    final antes = alcancavel;
    final cliente = HttpClient()
      ..connectionTimeout = const Duration(seconds: 3);
    try {
      final req = await cliente
          .getUrl(Uri.parse('http://$host:8771/dados'))
          .timeout(const Duration(seconds: 3));
      final resp = await req.close().timeout(const Duration(seconds: 3));
      await resp.drain<void>();
      alcancavel = resp.statusCode == 200;
    } catch (_) {
      alcancavel = false;
    } finally {
      cliente.close(force: true);
      _verificando = false;
      if (alcancavel != antes) notifyListeners();
    }
  }

  bool _vivo = true;

  @override
  void notifyListeners() {
    if (_vivo) super.notifyListeners();
  }

  @override
  void dispose() {
    _vivo = false;
    _timer?.cancel();
    super.dispose();
  }
}

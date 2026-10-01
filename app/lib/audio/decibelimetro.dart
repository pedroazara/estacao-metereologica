import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';

import 'medicao_sonora.dart';

abstract class CapturaSom {
  Stream<Map<dynamic, dynamic>> get eventos;
  Future<Map<dynamic, dynamic>> iniciar();
  Future<void> parar();
  Future<void> abrirPermissoes();
}

class MicrofoneAndroid implements CapturaSom {
  static const _metodos = MethodChannel('skylog/decibelimetro');
  static const _amostras = EventChannel('skylog/decibelimetro/amostras');

  @override
  Stream<Map<dynamic, dynamic>> get eventos => _amostras
      .receiveBroadcastStream()
      .map((e) => Map<dynamic, dynamic>.from(e as Map));

  @override
  Future<Map<dynamic, dynamic>> iniciar() async {
    if (defaultTargetPlatform != TargetPlatform.android || kIsWeb) {
      throw PlatformException(
        code: 'unsupported',
        message: 'O decibelímetro está disponível no Android.',
      );
    }
    return await _metodos.invokeMapMethod<dynamic, dynamic>('start') ?? {};
  }

  @override
  Future<void> parar() => _metodos.invokeMethod<void>('stop');

  @override
  Future<void> abrirPermissoes() => _metodos.invokeMethod<void>('openSettings');
}

class CalibracaoSom {
  final double ajuste;
  final String deviceKey;
  final DateTime data;
  const CalibracaoSom({
    required this.ajuste,
    required this.deviceKey,
    required this.data,
  });

  Map<String, dynamic> toJson() => {
    'version': 1,
    'offset': ajuste,
    'deviceKey': deviceKey,
    'date': data.toIso8601String(),
  };

  factory CalibracaoSom.fromJson(Map<String, dynamic> j) {
    final ajuste = j['offset'];
    if (j['version'] != 1 ||
        ajuste is! num ||
        !ajuste.isFinite ||
        j['deviceKey'] is! String ||
        (j['deviceKey'] as String).isEmpty) {
      throw const FormatException('Calibração inválida');
    }
    return CalibracaoSom(
      ajuste: ajuste.toDouble(),
      deviceKey: j['deviceKey'] as String,
      data: DateTime.parse(j['date'] as String),
    );
  }
}

abstract class RepositorioCalibracao {
  Future<CalibracaoSom?> ler();
  Future<void> salvar(CalibracaoSom? calibracao);
}

class ArquivoCalibracao implements RepositorioCalibracao {
  Future<File> _arquivo() async => File(
    '${(await getApplicationDocumentsDirectory()).path}/microfone_calibracao.json',
  );

  @override
  Future<CalibracaoSom?> ler() async {
    final f = await _arquivo();
    if (!await f.exists()) return null;
    return CalibracaoSom.fromJson(
      jsonDecode(await f.readAsString()) as Map<String, dynamic>,
    );
  }

  @override
  Future<void> salvar(CalibracaoSom? calibracao) async {
    final f = await _arquivo();
    if (calibracao == null) {
      if (await f.exists()) await f.delete();
      return;
    }
    final temporario = File('${f.path}.tmp');
    await temporario.writeAsString(
      jsonEncode(calibracao.toJson()),
      flush: true,
    );
    await temporario.rename(f.path);
  }
}

/// Mantém a captura independente da estação e não armazena áudio.
class Decibelimetro extends ChangeNotifier {
  final CapturaSom captura;
  final RepositorioCalibracao repositorio;
  final medicao = MedicaoSonora();
  StreamSubscription<Map<dynamic, dynamic>>? _assinatura;
  CalibracaoSom? calibracao;
  String? deviceKey, fonte, erro;
  String? aviso;
  int? sampleRate;
  bool medindo = false, iniciando = false, carregando = true;
  bool permissaoBloqueada = false;
  bool _vivo = true;
  int _geracao = 0;
  late final Future<void> pronto;

  Decibelimetro({CapturaSom? captura, RepositorioCalibracao? repositorio})
    : captura = captura ?? MicrofoneAndroid(),
      repositorio = repositorio ?? ArquivoCalibracao() {
    pronto = _carregar();
  }

  bool get calibrado =>
      calibracao != null && calibracao!.deviceKey == deviceKey;
  double get ajuste => calibrado ? calibracao!.ajuste : 0;
  String get unidade => calibrado ? 'dB SPL estimado' : 'dBFS';
  bool get podeCalibrar =>
      medindo && medicao.referenciaDbfs != null && deviceKey != null;
  // O piso numérico para ausência de sinal nunca é um nível acústico.
  double? corrigir(double? dbfs) =>
      dbfs == null || (calibrado && dbfs <= -119.999999) ? null : dbfs + ajuste;

  Future<void> _carregar() async {
    try {
      calibracao = await repositorio.ler();
    } catch (_) {
      aviso = 'Não foi possível carregar a calibração. Você pode calibrar novamente.';
    }
    carregando = false;
    _notificar();
  }

  Future<void> iniciar() async {
    if (medindo || iniciando || !_vivo) return;
    iniciando = true;
    erro = null;
    aviso = null;
    permissaoBloqueada = false;
    final geracao = ++_geracao;
    _notificar();
    await pronto;
    if (!_vivo || geracao != _geracao) return;
    medicao.limpar();
    deviceKey = null;
    try {
      await _assinatura?.cancel();
      if (!_vivo || geracao != _geracao) return;
      _assinatura = captura.eventos.listen(_receber, onError: _falhar);
      final info = await captura.iniciar();
      if (!_vivo || geracao != _geracao) return;
      if (info['deviceKey'] is! String ||
          (info['deviceKey'] as String).isEmpty) {
        throw const FormatException('Microfone sem identificação');
      }
      deviceKey = info['deviceKey'] as String;
      fonte = info['source'] as String?;
      sampleRate = (info['sampleRate'] as num?)?.toInt();
      medindo = true;
      if (calibracao != null && !calibrado) {
        aviso = 'O microfone ou modo de captura mudou. Calibre novamente para usar dB SPL.';
      }
    } catch (e) {
      if (_vivo && geracao == _geracao) _falhar(e);
    } finally {
      if (_vivo && geracao == _geracao) {
        iniciando = false;
        _notificar();
      }
    }
  }

  void _receber(Map<dynamic, dynamic> e) {
    if (!_vivo) return;
    if (e['type'] == 'stopped') {
      medindo = false;
      iniciando = false;
      _geracao++;
      aviso = 'Medição encerrada ao sair da tela. Toque em Iniciar para medir novamente.';
      _notificar();
    } else if (e['type'] == 'sample' && medindo) {
      try {
        final amostra = AmostraSom.deEvento(e);
        if (amostra.deviceKey != deviceKey) {
          throw const FormatException(
            'O microfone mudou durante a medição. Inicie novamente.',
          );
        }
        medicao.adicionar(amostra);
        _notificar();
      } catch (e) {
        _falhar(e);
      }
    }
  }

  void _falhar(Object e) {
    if (!_vivo) return;
    _geracao++;
    medindo = false;
    iniciando = false;
    if (e is PlatformException) {
      permissaoBloqueada = e.code == 'permission_denied_forever';
      erro = e.message ?? 'Não foi possível acessar o microfone.';
    } else if (e is MissingPluginException) {
      erro =
          'Reinstale a versão atual do app no Android para usar o microfone.';
    } else {
      erro = 'Não foi possível continuar a medição. Tente iniciar novamente.';
    }
    unawaited(_pararCaptura());
    _notificar();
  }

  Future<void> _pararCaptura() async {
    try {
      await captura.parar();
    } catch (_) {
      // A captura pode já ter sido encerrada pelo ciclo de vida nativo.
    }
  }

  Future<void> parar({bool segundoPlano = false}) async {
    final estavaMedindo = medindo || iniciando;
    _geracao++;
    medindo = false;
    iniciando = false;
    if (segundoPlano && estavaMedindo) {
      aviso = 'Medição encerrada ao sair da tela.';
    }
    _notificar();
    await _pararCaptura();
  }

  /// Separa o teste anterior da coleta sem reabrir o microfone.
  void reiniciarEstatisticas() {
    medicao.limpar();
    _notificar();
  }

  Future<void> calibrar(double referenciaSpl) async {
    final referencia = medicao.referenciaDbfs;
    if (!podeCalibrar ||
        referencia == null ||
        !referenciaSpl.isFinite ||
        referenciaSpl < 0 ||
        referenciaSpl > 160) {
      throw const FormatException(
        'Mantenha uma leitura válida e informe a referência entre 0 e 160 dB.',
      );
    }
    final nova = CalibracaoSom(
      ajuste: referenciaSpl - referencia,
      deviceKey: deviceKey!,
      data: DateTime.now(),
    );
    await repositorio.salvar(nova);
    if (!_vivo) return;
    calibracao = nova;
    aviso = 'Calibração salva para este microfone e modo de captura.';
    _notificar();
  }

  Future<void> removerCalibracao() async {
    await repositorio.salvar(null);
    if (!_vivo) return;
    calibracao = null;
    aviso = 'Calibração removida. As leituras estão em dBFS.';
    _notificar();
  }

  Future<void> abrirPermissoes() async {
    try {
      await captura.abrirPermissoes();
    } catch (_) {
      erro = 'Abra as configurações do Android e permita o microfone para o Skylog.';
      _notificar();
    }
  }

  void _notificar() {
    if (_vivo) notifyListeners();
  }

  @override
  void dispose() {
    _vivo = false;
    _geracao++;
    unawaited(_assinatura?.cancel());
    unawaited(_pararCaptura());
    super.dispose();
  }
}

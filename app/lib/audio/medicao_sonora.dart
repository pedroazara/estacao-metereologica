import 'dart:math' as math;

/// Potência média e pico absolutos de um bloco PCM normalizado para [-1, 1].
///
/// Estes números são níveis digitais. Converter para pressão sonora exige
/// uma referência própria para a rota de áudio usada na captura.
class AmostraSom {
  final double meanSquare;
  final double peak;
  final int count;
  final int sampleRate;
  final String deviceKey;

  const AmostraSom({
    required this.meanSquare,
    required this.peak,
    required this.count,
    required this.sampleRate,
    required this.deviceKey,
  });

  factory AmostraSom.deEvento(Map<dynamic, dynamic> evento) {
    final chave = evento['deviceKey'];
    if (chave is! String || chave.trim().isEmpty) {
      throw const FormatException('Rota de áudio ausente ou inválida.');
    }
    return AmostraSom(
      meanSquare: _numeroUnitario(evento['meanSquare'], 'meanSquare'),
      peak: _numeroUnitario(evento['peak'], 'peak'),
      count: _inteiroPositivo(evento['count'], 'count'),
      sampleRate: _inteiroPositivo(evento['sampleRate'], 'sampleRate'),
      deviceKey: chave,
    );
  }

  double get duracao => count / sampleRate;

  void _validar() {
    _numeroUnitario(meanSquare, 'meanSquare');
    _numeroUnitario(peak, 'peak');
    _inteiroPositivo(count, 'count');
    _inteiroPositivo(sampleRate, 'sampleRate');
    if (deviceKey.trim().isEmpty) {
      throw const FormatException('Rota de áudio ausente ou inválida.');
    }
  }
}

class PontoSom {
  final double segundos;
  final double dbfs;

  const PontoSom({required this.segundos, required this.dbfs});
}

/// Estatísticas da captura, independentes de Flutter e do dispositivo nativo.
///
/// As médias integram potência pelo tempo efetivamente amostrado. Mínimo e
/// máximo são níveis RMS dos blocos recebidos, não picos acústicos instantâneos.
class MedicaoSonora {
  static const _janelaHistorico = 30.0;
  static const _limiteHistorico = 400;
  static const _janelaReferencia = 3.0;
  static const _minimoReferencia = 2.0;
  static const _toleranciaTempo = 1e-9;

  final List<_BlocoSom> _recentes = [];
  double _segundos = 0;
  double _energia = 0;
  int _blocos = 0;
  String? _deviceKey;
  AmostraSom? _ultima;
  double? _atualDbfs;
  double? _minimoDbfs;
  double? _maximoDbfs;

  double get segundos => _segundos;
  int get blocos => _blocos;
  double? get atualDbfs => _atualDbfs;
  double? get minimoDbfs => _minimoDbfs;
  double? get maximoDbfs => _maximoDbfs;
  double? get mediaDbfs => _blocos == 0 ? null : _dbfs(_energia / _segundos);
  bool get saturado => (_ultima?.peak ?? 0) >= 0.999;
  bool get semSinal => _ultima?.meanSquare == 0;

  /// Pontos no fim de cada bloco; o tempo conta somente áudio capturado.
  List<PontoSom> get historico => List<PontoSom>.unmodifiable(
    _recentes.map(
      (bloco) =>
          PontoSom(segundos: bloco.fim, dbfs: _dbfs(bloco.amostra.meanSquare)),
    ),
  );

  /// Média energética dos últimos três segundos, recortando o bloco inicial.
  ///
  /// Só oferece uma referência com pelo menos dois segundos e quando toda a
  /// janela está acima do silêncio numérico e sem saturação. Não é calibração
  /// por si só: o usuário ainda precisa de um nível de referência conhecido.
  double? get referenciaDbfs {
    var tempo = 0.0;
    var energia = 0.0;
    for (final bloco in _recentes.reversed) {
      final amostra = bloco.amostra;
      if (amostra.peak >= 0.999 || amostra.meanSquare <= 1e-10) return null;
      final parte = math.min(amostra.duracao, _janelaReferencia - tempo);
      energia += amostra.meanSquare * parte;
      tempo += parte;
      if (tempo >= _janelaReferencia - _toleranciaTempo) break;
    }
    if (tempo < _minimoReferencia - _toleranciaTempo) return null;
    return _dbfs(energia / tempo);
  }

  void adicionar(AmostraSom amostra) {
    amostra._validar();
    if (_deviceKey != null && amostra.deviceKey != _deviceKey) {
      throw StateError('Limpe a medição antes de trocar a rota de áudio.');
    }
    _deviceKey = amostra.deviceKey;
    _ultima = amostra;
    _segundos += amostra.duracao;
    _energia += amostra.meanSquare * amostra.duracao;
    _blocos++;

    final nivel = _dbfs(amostra.meanSquare);
    _atualDbfs = nivel;
    _minimoDbfs = _minimoDbfs == null ? nivel : math.min(_minimoDbfs!, nivel);
    _maximoDbfs = _maximoDbfs == null ? nivel : math.max(_maximoDbfs!, nivel);
    _recentes.add(_BlocoSom(amostra, _segundos));

    final inicio = _segundos - _janelaHistorico;
    _recentes.removeWhere((bloco) => bloco.fim <= inicio);
    if (_recentes.length > _limiteHistorico) {
      _recentes.removeRange(0, _recentes.length - _limiteHistorico);
    }
  }

  void limpar() {
    _recentes.clear();
    _segundos = 0;
    _energia = 0;
    _blocos = 0;
    _deviceKey = null;
    _ultima = null;
    _atualDbfs = null;
    _minimoDbfs = null;
    _maximoDbfs = null;
  }
}

class _BlocoSom {
  final AmostraSom amostra;
  final double fim;
  const _BlocoSom(this.amostra, this.fim);
}

// -120 dBFS é apenas o piso de apresentação para zero e valores quase zero.
// A potência original continua intacta na integração das médias.
double _dbfs(double meanSquare) =>
    10 * math.log(math.max(meanSquare, 1e-12)) / math.ln10;

double _numeroUnitario(dynamic valor, String nome) {
  if (valor is! num || !valor.isFinite || valor < 0 || valor > 1) {
    throw FormatException('$nome deve estar entre 0 e 1 e ser finito.');
  }
  return valor.toDouble();
}

int _inteiroPositivo(dynamic valor, String nome) {
  if (valor is! num || !valor.isFinite || valor <= 0 || valor % 1 != 0) {
    throw FormatException('$nome deve ser um inteiro positivo.');
  }
  return valor.toInt();
}

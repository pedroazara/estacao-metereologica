import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

enum SensorId {
  gpsCelular,
  somCelular,
  ambienteRs485,
  luminosidadeGy302,
  temperaturaKy028,
  temperaturaKy013,
  ruidoKy038,
}

extension InformacoesSensor on SensorId {
  String get titulo => switch (this) {
    SensorId.gpsCelular => 'Localização',
    SensorId.somCelular => 'Som do celular',
    SensorId.ambienteRs485 => 'Temperatura e umidade',
    SensorId.luminosidadeGy302 => 'Luminosidade',
    SensorId.temperaturaKy028 => 'Temperatura auxiliar KY-028',
    SensorId.temperaturaKy013 => 'Temperatura auxiliar KY-013',
    SensorId.ruidoKy038 => 'Ruído da estação',
  };

  String get origem => switch (this) {
    SensorId.gpsCelular => 'GPS do celular',
    SensorId.somCelular => 'Microfone do celular',
    SensorId.ambienteRs485 => 'Estação · RS485',
    SensorId.luminosidadeGy302 => 'Estação · GY-302',
    SensorId.temperaturaKy028 => 'Estação · KY-028',
    SensorId.temperaturaKy013 => 'Estação · KY-013',
    SensorId.ruidoKy038 => 'Estação · KY-038',
  };

  String get descricao => switch (this) {
    SensorId.gpsCelular => 'Registra a posição e o percurso usando o telefone.',
    SensorId.somCelular =>
      'Registra níveis sonoros calculados no telefone, sem guardar áudio.',
    SensorId.ambienteRs485 =>
      'Registra temperatura e umidade recebidas da estação por Bluetooth.',
    SensorId.luminosidadeGy302 =>
      'Registra a iluminação em lux recebida da estação.',
    SensorId.temperaturaKy028 =>
      'Registra a leitura independente do sensor de temperatura KY-028.',
    SensorId.temperaturaKy013 =>
      'Registra a leitura independente do sensor de temperatura KY-013.',
    SensorId.ruidoKy038 => 'Registra a estimativa de ruído do KY-038; não usa o microfone do celular.',
  };

  bool get daEstacao =>
      this != SensorId.gpsCelular && this != SensorId.somCelular;

  Set<String> get camposAmbiente => switch (this) {
    SensorId.ambienteRs485 => const {'temp', 'umid'},
    SensorId.luminosidadeGy302 => const {'lux'},
    SensorId.temperaturaKy028 => const {'t28'},
    SensorId.temperaturaKy013 => const {'t13'},
    SensorId.ruidoKy038 => const {'db'},
    SensorId.gpsCelular || SensorId.somCelular => const {},
  };
}

abstract class RepositorioSelecaoSensores {
  Future<Set<SensorId>> ler();
  Future<void> salvar(Set<SensorId> sensores);
}

class ArquivoSelecaoSensores implements RepositorioSelecaoSensores {
  Future<File> _arquivo() async => File(
    '${(await getApplicationDocumentsDirectory()).path}/selecao_sensores.json',
  );

  @override
  Future<Set<SensorId>> ler() async {
    final arquivo = await _arquivo();
    if (!await arquivo.exists()) return {};
    final json = jsonDecode(await arquivo.readAsString());
    if (json is! Map || json['version'] != 1 || json['sensores'] is! List) {
      throw const FormatException('Arquivo de seleção de sensores inválido.');
    }
    final resultado = <SensorId>{};
    for (final nome in json['sensores'] as List) {
      final encontrados = SensorId.values.where(
        (sensor) => sensor.name == nome,
      );
      if (encontrados.isEmpty) {
        throw const FormatException('Sensor desconhecido na seleção salva.');
      }
      resultado.add(encontrados.single);
    }
    return resultado;
  }

  @override
  Future<void> salvar(Set<SensorId> sensores) async {
    final arquivo = await _arquivo();
    final temporario = File('${arquivo.path}.tmp');
    await temporario.writeAsString(
      jsonEncode({
        'version': 1,
        'sensores': [
          for (final sensor in SensorId.values)
            if (sensores.contains(sensor)) sensor.name,
        ],
      }),
      flush: true,
    );
    await temporario.rename(arquivo.path);
  }
}

/// Escolha para a próxima coleta. A coleta em andamento usa seu próprio snapshot.
class SelecaoSensores extends ChangeNotifier {
  final RepositorioSelecaoSensores repositorio;
  Set<SensorId> _selecionados = const {};
  bool _carregando = true;
  bool _bloqueada = false;
  bool _vivo = true;
  int _pendentes = 0;
  String? _erro;
  Future<void> _fila = Future<void>.value();
  late final Future<void> pronto;

  SelecaoSensores({RepositorioSelecaoSensores? repositorio})
    : repositorio = repositorio ?? ArquivoSelecaoSensores() {
    pronto = _carregar();
  }

  Set<SensorId> get selecionados => _selecionados;
  bool get carregando => _carregando;
  bool get salvando => _pendentes > 0;
  bool get bloqueada => _bloqueada;
  String? get erro => _erro;

  Future<void> _carregar() async {
    try {
      final salvo = await repositorio.ler();
      if (_vivo && !_bloqueada) _selecionados = Set.unmodifiable(salvo);
    } catch (_) {
      _erro = 'Não foi possível carregar a seleção salva. Escolha os sensores novamente.';
    } finally {
      _carregando = false;
      _notificar();
    }
  }

  /// Serializa gravações para que uma resposta lenta não restaure uma escolha antiga.
  Future<void> definir(SensorId sensor, bool selecionado) {
    if (!_vivo || _bloqueada) return Future<void>.value();
    _pendentes++;
    _notificar();
    final tarefa = _fila.then((_) async {
      try {
        await pronto;
        if (!_vivo || _bloqueada) return;
        if (_selecionados.contains(sensor) == selecionado && _erro == null) {
          return;
        }
        final nova = Set<SensorId>.of(_selecionados);
        selecionado ? nova.add(sensor) : nova.remove(sensor);
        _selecionados = Set.unmodifiable(nova);
        _erro = null;
        _notificar();
        try {
          await repositorio.salvar(_selecionados);
        } catch (_) {
          // A escolha continua disponível nesta execução, e repetir a ação tenta
          // salvar novamente. Nunca alegamos que ela foi persistida com sucesso.
          _erro = 'A escolha foi mantida, mas não foi salva no celular. Tente selecionar novamente.';
        }
      } finally {
        _pendentes--;
        _notificar();
      }
    });
    _fila = tarefa;
    return tarefa;
  }

  void bloquear(bool valor) {
    if (!_vivo || _bloqueada == valor) return;
    _bloqueada = valor;
    _notificar();
  }

  void _notificar() {
    if (_vivo) notifyListeners();
  }

  @override
  void dispose() {
    _vivo = false;
    super.dispose();
  }
}

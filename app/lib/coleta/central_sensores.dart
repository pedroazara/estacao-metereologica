import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';

import '../audio/decibelimetro.dart';
import '../ble/estacao_ble.dart';
import '../modelo/sensores.dart';

class ConferenciaSensor {
  final SensorId id;
  final bool selecionado, pronto;
  final String estado, detalhe;
  final String? valor;
  final DateTime? atualizadoEm;

  const ConferenciaSensor({
    required this.id,
    required this.selecionado,
    required this.pronto,
    required this.estado,
    required this.detalhe,
    this.valor,
    this.atualizadoEm,
  });
}

/// Coordena a preparação, a conferência e o conjunto fixo de uma coleta.
class CentralSensores extends ChangeNotifier {
  final EstacaoBle ble;
  final SelecaoSensores selecao;
  final Decibelimetro som;
  final DateTime Function() _agora;
  Timer? _relogio;
  DateTime? _ultimoSom;
  int _blocoSom = 0, _geracao = 0;
  bool _vivo = true, _sessaoAtiva = false;
  bool ocupado = false;
  String? mensagem;
  Set<SensorId> _sensoresDaColeta = {};

  CentralSensores({
    required this.ble,
    SelecaoSensores? selecao,
    Decibelimetro? som,
    DateTime Function()? agora,
    bool atualizarRelogio = true,
  }) : selecao = selecao ?? SelecaoSensores(),
       som = som ?? Decibelimetro(),
       _agora = agora ?? DateTime.now {
    ble.addListener(_aoBle);
    this.selecao.addListener(_notificar);
    this.som.addListener(_aoSom);
    if (atualizarRelogio) {
      _relogio = Timer.periodic(
        const Duration(seconds: 1),
        (_) => _notificar(),
      );
    }
  }

  bool get gravando => ble.gravador.gravando;
  int get selecionados => selecao.selecionados.length;
  int get prontos =>
      selecao.selecionados.where((s) => conferir(s).pronto).length;
  int get registros => ble.gravador.linhas;
  Duration get duracao => ble.gravador.inicio == null
      ? Duration.zero
      : _agora().difference(ble.gravador.inicio!);
  bool get podeIniciar =>
      !ocupado &&
      !gravando &&
      !selecao.carregando &&
      !selecao.salvando &&
      selecionados > 0 &&
      prontos == selecionados;

  bool _recente(DateTime? quando, Duration prazo) =>
      quando != null &&
      !_agora().isBefore(quando) &&
      _agora().difference(quando) <= prazo;

  ConferenciaSensor conferir(SensorId id) {
    final marcado = selecao.selecionados.contains(id);
    ConferenciaSensor resultado(
      String estado,
      String detalhe, {
      bool pronto = false,
      String? valor,
      DateTime? atualizado,
    }) => ConferenciaSensor(
      id: id,
      selecionado: marcado,
      pronto: pronto,
      estado: estado,
      detalhe: detalhe,
      valor: valor,
      atualizadoEm: atualizado,
    );

    if (id == SensorId.gpsCelular) {
      final gps = ble.gps;
      final recente = _recente(gps.ultimaLeitura, const Duration(seconds: 5));
      if (!gps.ativo || gps.lat == null || gps.lon == null) {
        return resultado('Aguardando localização', gps.mensagem);
      }
      return resultado(
        recente ? 'Recebendo dados' : 'Sem atualização',
        recente
            ? 'Posição para registrar o percurso.'
            : 'A última posição ficou antiga. Aguarde um novo sinal.',
        pronto: recente,
        atualizado: gps.ultimaLeitura,
        valor: recente
            ? '${gps.lat!.toStringAsFixed(5)}, ${gps.lon!.toStringAsFixed(5)}'
                  '${gps.precisaoM == null ? '' : ' · ±${gps.precisaoM!.toStringAsFixed(0)} m'}'
            : null,
      );
    }
    if (id == SensorId.somCelular) {
      if (som.iniciando) {
        return resultado(
          'Abrindo microfone',
          'Aguarde a permissão e o início da captura.',
        );
      }
      if (som.erro != null) {
        return resultado('Microfone indisponível', som.erro!);
      }
      if (!som.medindo) {
        return resultado(
          'Aguardando teste',
          'Toque em Testar microfone para conferir o som.',
        );
      }
      if (!_recente(_ultimoSom, const Duration(seconds: 2))) {
        return resultado(
          'Aguardando leitura',
          'O microfone ainda não enviou uma leitura recente.',
        );
      }
      if (som.medicao.semSinal) {
        return resultado(
          'Sem sinal',
          'O microfone está retornando silêncio digital.',
        );
      }
      if (som.medicao.saturado) {
        return resultado(
          'Som saturado',
          'O sinal está acima da capacidade do microfone.',
        );
      }
      final nivel = som.corrigir(som.medicao.atualDbfs);
      return resultado(
        'Recebendo dados',
        som.calibrado
            ? 'Microfone com ajuste de referência.'
            : 'Sem calibração: nível digital em dBFS.',
        pronto: nivel != null,
        atualizado: _ultimoSom,
        valor: nivel == null ? null : '${_numero(nivel)} ${som.unidade}',
      );
    }

    if (!ble.conectado) {
      return resultado(
        'Estação desconectada',
        'Ligue a estação e aproxime o celular para receber este sensor.',
      );
    }
    final prazo = Duration(
      milliseconds: math.max(5000, (ble.config?.pubMs ?? 1000) * 3),
    );
    final recente = _recente(ble.ultimaLeituraAmbiente, prazo);
    if (!recente) {
      return resultado(
        ble.ultimaLeituraAmbiente == null
            ? 'Aguardando leitura'
            : 'Sem atualização',
        'A estação está conectada, mas não há leitura recente deste sensor.',
        atualizado: ble.ultimaLeituraAmbiente,
      );
    }
    if (!id.camposAmbiente.every(ble.camposAmbienteValidos.contains)) {
      return resultado(
        'Sem leitura válida',
        'O último envio não trouxe todos os valores deste sensor.',
        atualizado: ble.ultimaLeituraAmbiente,
      );
    }
    final a = ble.ambiente!;
    final valor = switch (id) {
      SensorId.ambienteRs485 =>
        '${_numero(a.temperatura)} °C · ${_numero(a.umidade)} %',
      SensorId.luminosidadeGy302 => '${a.luminosidade.toStringAsFixed(0)} lx',
      SensorId.temperaturaKy028 => '${_numero(a.temperaturaAux28)} °C',
      SensorId.temperaturaKy013 => '${_numero(a.temperaturaAux13)} °C',
      SensorId.ruidoKy038 => '${_numero(a.ruidoDb)} dB estimados',
      _ => null,
    };
    return resultado(
      'Recebendo dados',
      'Última leitura recebida da estação.',
      pronto: true,
      valor: valor,
      atualizado: ble.ultimaLeituraAmbiente,
    );
  }

  Future<void> alterar(SensorId id, bool valor) async {
    if (gravando || ocupado) return;
    mensagem = null;
    await selecao.definir(id, valor);
    if (id == SensorId.somCelular && !selecao.selecionados.contains(id)) {
      await som.parar();
      _ultimoSom = null;
    }
    _notificar();
  }

  Future<void> testarMicrofone() async {
    if (gravando ||
        ocupado ||
        !selecao.selecionados.contains(SensorId.somCelular)) {
      return;
    }
    _ultimoSom = null;
    _blocoSom = 0;
    await som.iniciar();
    _notificar();
  }

  Future<void> testarGps() async {
    await ble.iniciarGpsTelefone();
    _notificar();
  }

  Future<void> pararTesteMicrofone() async {
    if (gravando || ocupado) return;
    await som.parar();
    _ultimoSom = null;
    _notificar();
  }

  Future<void> iniciarColeta() async {
    if (!podeIniciar) {
      mensagem = selecionados == 0
          ? 'Selecione ao menos um sensor antes de começar.'
          : 'Confira os sensores selecionados: todos precisam ter uma leitura recente.';
      _notificar();
      return;
    }
    ocupado = true;
    final geracao = ++_geracao;
    selecao.bloquear(true);
    _sensoresDaColeta = Set.unmodifiable(selecao.selecionados);
    _blocoSom = som.medicao.blocos;
    mensagem = null;
    _notificar();
    var confirmada = false;
    try {
      await ble.iniciarGravacao(sensores: _sensoresDaColeta);
      if (!_vivo || geracao != _geracao || !gravando) {
        mensagem = 'O início da coleta foi cancelado ao sair do aplicativo.';
        return;
      }
      if (!_sensoresDaColeta.every((id) => conferir(id).pronto)) {
        mensagem = 'Um sensor parou de responder antes do início. Confira as leituras e tente novamente.';
        return;
      }
      if (_sensoresDaColeta.contains(SensorId.somCelular)) {
        som.reiniciarEstatisticas();
        _blocoSom = 0;
      }
      _sessaoAtiva = true;
      confirmada = true;
    } catch (_) {
      mensagem =
          'Não foi possível criar a coleta no telefone. Tente novamente.';
    } finally {
      if (!confirmada) {
        try {
          await ble.pararGravacao();
        } catch (_) {
          mensagem = 'Não foi possível iniciar a coleta. Confira o armazenamento do telefone.';
        }
        _sessaoAtiva = false;
        selecao.bloquear(false);
        _sensoresDaColeta = {};
      }
      ocupado = false;
      _notificar();
    }
  }

  Future<void> encerrarColeta({String? motivo}) async {
    if (ocupado) return;
    ocupado = true;
    _sessaoAtiva = false;
    _geracao++;
    _notificar();
    try {
      await ble.pararGravacao();
      mensagem = motivo ?? 'Coleta encerrada e salva no celular.';
    } catch (_) {
      mensagem = 'A coleta foi interrompida. Confira o arquivo em Coletas antes de sair.';
    } finally {
      if (_sensoresDaColeta.contains(SensorId.somCelular)) {
        await som.parar();
      }
      _sensoresDaColeta = {};
      selecao.bloquear(false);
      ocupado = false;
      _notificar();
    }
  }

  /// O microfone nativo só captura em primeiro plano. Não deixar uma coleta
  /// que o inclui parecer completa após bloquear o celular.
  Future<void> suspender() async {
    if (ocupado && !_sessaoAtiva) {
      _geracao++;
      selecao.bloquear(false);
    }
    if (_sessaoAtiva && _sensoresDaColeta.contains(SensorId.somCelular)) {
      await encerrarColeta(
        motivo:
            'Coleta salva ao sair do aplicativo: o microfone foi interrompido.',
      );
    } else {
      await som.parar(segundoPlano: true);
    }
    _ultimoSom = null;
    _notificar();
  }

  void _aoBle() {
    if (_sessaoAtiva && !gravando && !ocupado) {
      unawaited(
        encerrarColeta(
          motivo: 'A coleta foi encerrada. Os dados recebidos ficaram salvos.',
        ),
      );
    }
    _notificar();
  }

  void _aoSom() {
    final novoBloco = som.medindo && som.medicao.blocos > _blocoSom;
    if (novoBloco) _ultimoSom = _agora();
    _blocoSom = som.medicao.blocos;
    if (_sessaoAtiva && _sensoresDaColeta.contains(SensorId.somCelular)) {
      if (!som.medindo && !som.iniciando && !ocupado) {
        unawaited(
          encerrarColeta(
            motivo: 'Coleta salva: a captura do microfone foi interrompida.',
          ),
        );
      } else if (novoBloco && gravando) {
        ble.gravador.anexar('som_celular', {
          'nivel_dbfs': som.medicao.semSinal ? null : som.medicao.atualDbfs,
          'nivel_db_spl_estimado': som.calibrado && !som.medicao.semSinal
              ? som.corrigir(som.medicao.atualDbfs)
              : null,
          'calibrado': som.calibrado,
          'ajuste_db': som.calibrado ? som.ajuste : null,
          'janela_ms': 100,
          'saturado': som.medicao.saturado,
          'sem_sinal': som.medicao.semSinal,
          'microfone': som.deviceKey,
        });
      }
    }
    _notificar();
  }

  void _notificar() {
    if (_vivo) notifyListeners();
  }

  @override
  void dispose() {
    _vivo = false;
    _geracao++;
    _relogio?.cancel();
    ble.removeListener(_aoBle);
    selecao.removeListener(_notificar);
    som.removeListener(_aoSom);
    selecao.dispose();
    som.dispose();
    super.dispose();
  }
}

String _numero(double valor) => valor.toStringAsFixed(1).replaceAll('.', ',');

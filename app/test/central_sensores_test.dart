import 'dart:async';
import 'dart:convert';

import 'package:estacao_movel/armazenamento/gravador.dart';
import 'package:estacao_movel/audio/decibelimetro.dart';
import 'package:estacao_movel/ble/contrato.dart';
import 'package:estacao_movel/ble/estacao_ble.dart';
import 'package:estacao_movel/coleta/central_sensores.dart';
import 'package:estacao_movel/gps/gps_telefone.dart';
import 'package:estacao_movel/modelo/sensores.dart';
import 'package:flutter_test/flutter_test.dart';

class MemoriaSelecao extends RepositorioSelecaoSensores {
  Set<SensorId> valor = {};
  @override
  Future<Set<SensorId>> ler() async => valor;
  @override
  Future<void> salvar(Set<SensorId> sensores) async {
    valor = Set.of(sensores);
  }
}

class MemoriaCalibracao extends RepositorioCalibracao {
  @override
  Future<CalibracaoSom?> ler() async => null;
  @override
  Future<void> salvar(CalibracaoSom? calibracao) async {}
}

class AudioTeste extends CapturaSom {
  final stream = StreamController<Map<dynamic, dynamic>>.broadcast(sync: true);
  @override
  Stream<Map<dynamic, dynamic>> get eventos => stream.stream;
  @override
  Future<Map<dynamic, dynamic>> iniciar() async => {
    'deviceKey': 'celular/teste',
    'sampleRate': 48000,
    'source': 'UNPROCESSED',
  };
  @override
  Future<void> parar() async {}
  @override
  Future<void> abrirPermissoes() async {}
  void emitir({double energia = .0001}) => stream.add({
    'type': 'sample',
    'meanSquare': energia,
    'peak': .1,
    'count': 4800,
    'sampleRate': 48000,
    'deviceKey': 'celular/teste',
  });
}

class GravadorMemoria extends Gravador {
  bool aberto = false;
  bool falharAoAbrir = false, falharAoFechar = false;
  Completer<void>? abertura;
  final registros = <Map<String, dynamic>>[];
  @override
  bool get gravando => aberto;
  @override
  int get linhas => registros.length;
  @override
  Future<void> iniciar(String dev) async {
    if (abertura != null) await abertura!.future;
    if (falharAoAbrir) throw StateError('falha ao abrir');
    aberto = true;
    registros.clear();
  }

  @override
  Future<void> encerrar() async {
    aberto = false;
    if (falharAoFechar) throw StateError('falha ao fechar');
  }

  @override
  void anexar(String origem, Map<String, dynamic> corpo) {
    if (aberto) registros.add({'src': origem, 'm': Map.of(corpo)});
  }
}

class GpsTeste extends GpsTelefone {
  @override
  bool get ativo => true;
  @override
  Future<void> iniciar() async {}
  @override
  Future<void> parar() async {}
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late DateTime agora;
  late EstacaoBle ble;
  late GravadorMemoria gravador;
  late AudioTeste audio;
  late CentralSensores central;

  setUp(() async {
    agora = DateTime.now();
    gravador = GravadorMemoria();
    ble = EstacaoBle(gravador: gravador, gps: GpsTeste());
    audio = AudioTeste();
    central = CentralSensores(
      ble: ble,
      selecao: SelecaoSensores(repositorio: MemoriaSelecao()),
      som: Decibelimetro(captura: audio, repositorio: MemoriaCalibracao()),
      agora: () => agora,
      atualizarRelogio: false,
    );
    await central.selecao.pronto;
    await central.som.pronto;
  });
  tearDown(() async {
    central.dispose();
    ble.dispose();
    await audio.stream.close();
  });

  void ambiente(Map<String, dynamic> valores) {
    ble.estado = EstadoLigacao.ligado;
    ble.processarPacote(Contrato.amb, utf8.encode(jsonEncode(valores)));
    agora = DateTime.now();
  }

  test('coleta exige seleção e todos os sensores prontos', () async {
    expect(central.podeIniciar, isFalse);
    await central.alterar(SensorId.luminosidadeGy302, true);
    expect(central.podeIniciar, isFalse);
    ambiente({'lux': 100});
    expect(central.podeIniciar, isTrue);
    await central.alterar(SensorId.ambienteRs485, true);
    expect(central.podeIniciar, isFalse);
    await central.iniciarColeta();
    expect(gravador.aberto, isFalse);
    expect(central.mensagem, contains('Confira'));
  });

  test('campo ausente ou antigo não é confundido com zero válido', () async {
    await central.alterar(SensorId.luminosidadeGy302, true);
    ambiente({'temp': 25});
    expect(central.conferir(SensorId.luminosidadeGy302).pronto, isFalse);
    ambiente({'lux': -1});
    expect(central.conferir(SensorId.luminosidadeGy302).pronto, isFalse);
    ambiente({'lux': 0});
    expect(central.conferir(SensorId.luminosidadeGy302).pronto, isTrue);
    expect(central.conferir(SensorId.luminosidadeGy302).valor, '0 lx');
    agora = agora.add(const Duration(seconds: 6));
    expect(
      central.conferir(SensorId.luminosidadeGy302).estado,
      'Sem atualização',
    );
    expect(central.podeIniciar, isFalse);
  });

  test(
    'som de conferência não é salvo e coleta guarda só níveis posteriores',
    () async {
      await central.alterar(SensorId.somCelular, true);
      await central.testarMicrofone();
      for (var i = 0; i < 5; i++) {
        audio.emitir();
      }
      expect(gravador.registros, isEmpty);
      expect(central.podeIniciar, isTrue);
      await central.iniciarColeta();
      expect(central.gravando, isTrue);
      expect(central.selecao.bloqueada, isTrue);
      expect(central.som.medicao.blocos, 0);
      audio.emitir();
      final sons = gravador.registros
          .where((r) => r['src'] == 'som_celular')
          .toList();
      expect(sons, hasLength(1));
      expect(sons.single['m']['nivel_dbfs'], closeTo(-40, 1e-7));
      expect(sons.single['m']['nivel_db_spl_estimado'], isNull);
      expect(
        gravador.registros.where((r) => r['src'] == 'gps' || r['src'] == 'amb'),
        isEmpty,
      );
      await central.alterar(SensorId.gpsCelular, true);
      expect(central.selecao.selecionados, {SensorId.somCelular});
      await central.encerrarColeta();
      expect(central.gravando, isFalse);
      expect(central.som.medindo, isFalse);
      expect(central.selecao.bloqueada, isFalse);
    },
  );

  test(
    'silêncio digital impede início e leitura velha perde prontidão',
    () async {
      await central.alterar(SensorId.somCelular, true);
      await central.testarMicrofone();
      audio.emitir(energia: 0);
      expect(central.conferir(SensorId.somCelular).estado, 'Sem sinal');
      audio.emitir();
      expect(central.podeIniciar, isTrue);
      agora = agora.add(const Duration(seconds: 3));
      expect(central.podeIniciar, isFalse);
    },
  );

  test(
    'sair do app encerra a coleta com som e desbloqueia a seleção',
    () async {
      await central.alterar(SensorId.somCelular, true);
      await central.testarMicrofone();
      audio.emitir();
      await central.iniciarColeta();
      audio.emitir();
      await central.suspender();
      expect(central.gravando, isFalse);
      expect(central.som.medindo, isFalse);
      expect(central.selecao.bloqueada, isFalse);
      expect(central.mensagem, contains('salva'));
    },
  );

  test(
    'áudio interrompido durante abertura cancela o início da coleta',
    () async {
      await central.alterar(SensorId.somCelular, true);
      await central.testarMicrofone();
      audio.emitir();
      gravador.abertura = Completer<void>();
      final partida = central.iniciarColeta();
      await Future<void>.delayed(Duration.zero);
      audio.stream.add({'type': 'stopped', 'reason': 'background'});
      gravador.abertura!.complete();
      await partida;
      expect(central.gravando, isFalse);
      expect(central.selecao.bloqueada, isFalse);
      expect(central.ocupado, isFalse);
      expect(central.mensagem, contains('parou de responder'));
    },
  );

  test(
    'falha de fechamento também para o microfone e libera seleção',
    () async {
      await central.alterar(SensorId.somCelular, true);
      await central.testarMicrofone();
      audio.emitir();
      await central.iniciarColeta();
      gravador.falharAoFechar = true;
      await central.encerrarColeta();
      expect(central.som.medindo, isFalse);
      expect(central.selecao.bloqueada, isFalse);
      expect(central.ocupado, isFalse);
      expect(central.mensagem, contains('interrompida'));
      gravador.falharAoFechar = false;
    },
  );

  test('falha no início e no rollback não deixa seleção bloqueada', () async {
    await central.alterar(SensorId.luminosidadeGy302, true);
    ambiente({'lux': 40});
    gravador.falharAoAbrir = true;
    gravador.falharAoFechar = true;
    await central.iniciarColeta();
    expect(central.selecao.bloqueada, isFalse);
    expect(central.ocupado, isFalse);
    expect(central.gravando, isFalse);
    gravador.falharAoFechar = false;
  });

  test(
    'perda nativa do microfone encerra e mantém registros recebidos',
    () async {
      await central.alterar(SensorId.somCelular, true);
      await central.testarMicrofone();
      audio.emitir();
      await central.iniciarColeta();
      audio.emitir();
      audio.stream.add({'type': 'stopped', 'reason': 'background'});
      await Future<void>.delayed(Duration.zero);
      expect(central.gravando, isFalse);
      expect(
        gravador.registros.where((r) => r['src'] == 'som_celular'),
        hasLength(1),
      );
    },
  );
}

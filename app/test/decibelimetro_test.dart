import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:estacao_movel/audio/decibelimetro.dart';
import 'package:estacao_movel/telas/tela_decibelimetro.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

class _Microfone extends CapturaSom {
  final stream = StreamController<Map<dynamic, dynamic>>.broadcast(sync: true);
  String chave = 'interno/raw/48000';
  int inicios = 0, paradas = 0;
  Object? falha;
  Completer<Map<dynamic, dynamic>>? pendente;

  @override
  Stream<Map<dynamic, dynamic>> get eventos => stream.stream;
  @override
  Future<Map<dynamic, dynamic>> iniciar() async {
    inicios++;
    if (falha != null) throw falha!;
    if (pendente != null) return pendente!.future;
    return {'deviceKey': chave, 'sampleRate': 48000, 'source': 'UNPROCESSED'};
  }

  void amostra({double energia = .0001, double pico = .1, String? rota}) {
    stream.add({
      'type': 'sample',
      'meanSquare': energia,
      'peak': pico,
      'count': 4800,
      'sampleRate': 48000,
      'deviceKey': rota ?? chave,
    });
  }

  @override
  Future<void> parar() async {
    paradas++;
  }

  @override
  Future<void> abrirPermissoes() async {}
}

class _Memoria extends RepositorioCalibracao {
  CalibracaoSom? valor;
  bool falhar = false;
  @override
  Future<CalibracaoSom?> ler() async => valor;
  @override
  Future<void> salvar(CalibracaoSom? calibracao) async {
    if (falhar) throw const FileSystemException('sem espaço');
    valor = calibracao;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late _Microfone microfone;
  late _Memoria memoria;
  late Decibelimetro som;

  setUp(() {
    microfone = _Microfone();
    memoria = _Memoria();
    som = Decibelimetro(captura: microfone, repositorio: memoria);
  });
  tearDown(() async {
    // Os testes de widget possuem controlador próprio, descartado pela tela.
    som.dispose();
    await microfone.stream.close();
  });

  test('abrir serviço não liga o microfone; sem referência usa dBFS', () async {
    await som.pronto;
    expect(microfone.inicios, 0);
    await som.iniciar();
    microfone.amostra();
    expect(som.medindo, isTrue);
    expect(som.unidade, 'dBFS');
    expect(som.medicao.atualDbfs, closeTo(-40, 1e-8));
    expect(som.calibrado, isFalse);
  });

  test(
    'calibra pela energia recente e restaura apenas para a mesma rota',
    () async {
      await som.iniciar();
      for (var i = 0; i < 30; i++) {
        microfone.amostra();
      }
      await som.calibrar(70);
      expect(memoria.valor!.ajuste, closeTo(110, 1e-8));
      expect(som.corrigir(som.medicao.mediaDbfs), closeTo(70, 1e-8));
      final roundTrip = CalibracaoSom.fromJson(memoria.valor!.toJson());
      expect(roundTrip.deviceKey, microfone.chave);
      await som.parar();
      microfone.chave = 'interno/voice/44100';
      await som.iniciar();
      expect(som.calibrado, isFalse);
      expect(som.unidade, 'dBFS');
      expect(som.aviso, contains('mudou'));
    },
  );

  test('falha ao salvar não aplica calibração fictícia', () async {
    await som.iniciar();
    for (var i = 0; i < 25; i++) {
      microfone.amostra();
    }
    memoria.falhar = true;
    await expectLater(som.calibrar(70), throwsA(isA<FileSystemException>()));
    expect(som.calibrado, isFalse);
  });

  test('nega calibração com microfone saturado', () async {
    await som.iniciar();
    for (var i = 0; i < 25; i++) {
      microfone.amostra(pico: 1);
    }
    expect(som.podeCalibrar, isFalse);
    await expectLater(som.calibrar(80), throwsFormatException);
  });

  test('silêncio digital não vira valor acústico após calibração', () async {
    await som.iniciar();
    for (var i = 0; i < 25; i++) {
      microfone.amostra();
    }
    await som.calibrar(70);
    microfone.amostra(energia: 0, pico: 0);
    expect(som.corrigir(som.medicao.atualDbfs), isNull);
    expect(som.corrigir(som.medicao.minimoDbfs), isNull);
  });

  test(
    'permissão bloqueada oferece configurações e não continua medindo',
    () async {
      microfone.falha = PlatformException(
        code: 'permission_denied_forever',
        message: 'Permita o microfone.',
      );
      await som.iniciar();
      expect(som.medindo, isFalse);
      expect(som.iniciando, isFalse);
      expect(som.permissaoBloqueada, isTrue);
      expect(som.erro, 'Permita o microfone.');
      expect(microfone.paradas, greaterThan(0));
    },
  );

  test(
    'parar cancela início pendente mesmo se a permissão chegar depois',
    () async {
      microfone.pendente = Completer<Map<dynamic, dynamic>>();
      final inicio = som.iniciar();
      await Future<void>.delayed(Duration.zero);
      await som.parar();
      microfone.pendente!.complete({
        'deviceKey': microfone.chave,
        'source': 'UNPROCESSED',
        'sampleRate': 48000,
      });
      await inicio;
      microfone.amostra();
      expect(som.medindo, isFalse);
      expect(som.iniciando, isFalse);
      expect(som.medicao.blocos, 0);
    },
  );

  test(
    'interrupção em segundo plano preserva resumo e ignora áudio posterior',
    () async {
      await som.iniciar();
      microfone.amostra();
      microfone.stream.add({'type': 'stopped', 'reason': 'background'});
      microfone.amostra();
      expect(som.medindo, isFalse);
      expect(som.medicao.blocos, 1);
      expect(som.aviso, contains('encerrada'));
    },
  );

  test(
    'mudança de microfone durante coleta para sem misturar medidas',
    () async {
      await som.iniciar();
      microfone.amostra();
      microfone.amostra(rota: 'externo');
      expect(som.medindo, isFalse);
      expect(som.medicao.blocos, 1);
      expect(som.erro, isNotNull);
    },
  );

  testWidgets('tela inicia por ação explícita, exibe leituras e encerra', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(412, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final audio = _Microfone();
    final controle = Decibelimetro(captura: audio, repositorio: _Memoria());
    final chave = GlobalKey();
    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(useMaterial3: true),
        home: RepaintBoundary(
          key: chave,
          child: TelaDecibelimetro(controlador: controle),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(audio.inicios, 0);
    await tester.ensureVisible(find.text('Iniciar medição'));
    await tester.tap(find.text('Iniciar medição'));
    await tester.pumpAndSettle();
    for (var i = 0; i < 100; i++) {
      audio.amostra(energia: .0001 * (1 + (i % 7) / 10));
    }
    await tester.pump();
    expect(find.text('MEDINDO AGORA'), findsOneWidget);
    expect(find.text('Média energética'), findsOneWidget);
    expect(find.text('dBFS'), findsWidgets);
    expect(tester.takeException(), isNull);
    await tester.tap(find.text('Encerrar medição'));
    await tester.pumpAndSettle();
    expect(controle.medindo, isFalse);
    expect(controle.medicao.blocos, 100);
    // Captura opcional para a revisão visual, sem afetar o teste normal.
    if (Platform.environment['SKYLOG_UI_CAPTURE'] case final String caminho) {
      await tester.scrollUntilVisible(find.text('MICROFONE DO CELULAR'), -300);
      await tester.pumpAndSettle();
      await tester.runAsync(() async {
        final boundary =
            chave.currentContext!.findRenderObject()! as RenderRepaintBoundary;
        final imagem = await boundary.toImage(pixelRatio: 2);
        final bytes = await imagem.toByteData(format: ui.ImageByteFormat.png);
        await File(caminho).writeAsBytes(bytes!.buffer.asUint8List());
        imagem.dispose();
      });
    }
    await tester.pumpWidget(const SizedBox());
    await audio.stream.close();
  });

  testWidgets('layout suporta telefone estreito e fonte ampliada', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final audio = _Microfone();
    final controle = Decibelimetro(captura: audio, repositorio: _Memoria());
    await tester.pumpWidget(
      MaterialApp(
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context)
              .copyWith(textScaler: const TextScaler.linear(1.6)),
          child: child!,
        ),
        home: TelaDecibelimetro(controlador: controle),
      ),
    );
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Iniciar medição'));
    await tester.tap(find.text('Iniciar medição'));
    await tester.pumpAndSettle();
    audio.amostra();
    await tester.pump();
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    expect(audio.paradas, greaterThan(0));
    await audio.stream.close();
  });
}

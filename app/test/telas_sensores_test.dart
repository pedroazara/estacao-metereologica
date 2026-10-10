import 'dart:async';
import 'dart:convert';

import 'package:estacao_movel/armazenamento/gravador.dart';
import 'package:estacao_movel/audio/decibelimetro.dart';
import 'package:estacao_movel/ble/contrato.dart';
import 'package:estacao_movel/ble/estacao_ble.dart';
import 'package:estacao_movel/coleta/central_sensores.dart';
import 'package:estacao_movel/gps/gps_telefone.dart';
import 'package:estacao_movel/modelo/sensores.dart';
import 'package:estacao_movel/telas/tela_painel.dart';
import 'package:estacao_movel/telas/tela_sensores.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _SelecaoMemoria extends RepositorioSelecaoSensores {
  Set<SensorId> sensores = {};
  @override
  Future<Set<SensorId>> ler() async => Set.of(sensores);
  @override
  Future<void> salvar(Set<SensorId> sensores) async {
    this.sensores = Set.of(sensores);
  }
}

class _CalibracaoMemoria extends RepositorioCalibracao {
  @override
  Future<CalibracaoSom?> ler() async => null;
  @override
  Future<void> salvar(CalibracaoSom? calibracao) async {}
}

class _Microfone extends CapturaSom {
  final stream = StreamController<Map<dynamic, dynamic>>.broadcast(sync: true);
  int inicios = 0;
  int paradas = 0;
  @override
  Stream<Map<dynamic, dynamic>> get eventos => stream.stream;
  @override
  Future<Map<dynamic, dynamic>> iniciar() async {
    inicios++;
    return {
      'deviceKey': 'celular/teste',
      'sampleRate': 48000,
      'source': 'UNPROCESSED',
    };
  }

  void amostra() => stream.add({
    'type': 'sample',
    'meanSquare': .0001,
    'peak': .1,
    'count': 4800,
    'sampleRate': 48000,
    'deviceKey': 'celular/teste',
  });

  @override
  Future<void> parar() async {
    paradas++;
  }

  @override
  Future<void> abrirPermissoes() async {}
}

class _GravadorMemoria extends Gravador {
  bool aberto = false;
  DateTime? comeco;
  final registros = <Map<String, dynamic>>[];
  @override
  bool get gravando => aberto;
  @override
  int get linhas => registros.length;
  @override
  DateTime? get inicio => comeco;
  @override
  Future<void> iniciar(String dev) async {
    aberto = true;
    comeco = DateTime.now();
    registros.clear();
  }

  @override
  Future<void> encerrar() async {
    aberto = false;
  }

  @override
  void anexar(String origem, Map<String, dynamic> corpo) {
    if (aberto) registros.add({'src': origem, 'm': Map.of(corpo)});
  }
}

class _Gps extends GpsTelefone {
  @override
  bool get ativo => true;
  @override
  Future<void> iniciar() async {}
  @override
  Future<void> parar() async {}
}

Widget _app(Widget tela, {double fonte = 1}) => MaterialApp(
  theme: ThemeData(
    useMaterial3: true,
    colorScheme: ColorScheme.fromSeed(
      seedColor: const Color(0xFF2F9E57),
      brightness: Brightness.dark,
    ),
  ),
  builder: (context, child) => MediaQuery(
    data: MediaQuery.of(context).copyWith(textScaler: TextScaler.linear(fonte)),
    child: child!,
  ),
  home: Scaffold(body: SafeArea(child: tela)),
);

Finder _seletor(String titulo) => find.descendant(
  of: find.ancestor(of: find.text(titulo), matching: find.byType(Card)),
  matching: find.byType(Switch),
);

void _telefone(WidgetTester tester, {Size tamanho = const Size(412, 900)}) {
  tester.view.physicalSize = tamanho;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

Future<void> _revelar(WidgetTester tester, Finder alvo) async {
  // Rola como o usuário: os cartões pertencem a uma única coluna na ListView,
  // e a presença do widget na árvore não garante que ele esteja na tela.
  for (var tentativa = 0; tentativa < 40; tentativa++) {
    if (alvo.hitTestable().evaluate().isNotEmpty) return;
    final centro = tester.getCenter(alvo);
    await tester.drag(
      find.byType(ListView).first,
      Offset(0, centro.dy < 0 ? 350 : -350),
    );
    await tester.pumpAndSettle();
  }
  fail('O controle não ficou visível após rolar a tela: $alvo');
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late EstacaoBle ble;
  late _Microfone microfone;
  late _GravadorMemoria gravador;
  late CentralSensores central;

  Future<void> preparar() async {
    microfone = _Microfone();
    gravador = _GravadorMemoria();
    ble = EstacaoBle(gravador: gravador, gps: _Gps());
    central = CentralSensores(
      ble: ble,
      som: Decibelimetro(captura: microfone, repositorio: _CalibracaoMemoria()),
      selecao: SelecaoSensores(repositorio: _SelecaoMemoria()),
      atualizarRelogio: false,
    );
    await central.selecao.pronto;
    await central.som.pronto;
  }

  tearDown(() async {
    if (central.gravando) await central.encerrarColeta();
    central.dispose();
    ble.dispose();
    await microfone.stream.close();
  });

  TelaSensores sensores() => TelaSensores(
    central: central,
    abrirDecibelimetro: () {},
    abrirAjustes: () {},
  );

  testWidgets('início orienta a seleção e deixa leituras na tela de sensores', (
    tester,
  ) async {
    _telefone(tester);
    await preparar();
    await central.alterar(SensorId.luminosidadeGy302, true);
    ble.estado = EstadoLigacao.ligado;
    ble.processarPacote(Contrato.amb, utf8.encode(jsonEncode({'lux': 1234})));
    expect(central.conferir(SensorId.luminosidadeGy302).valor, '1234 lx');
    var aberturas = 0;
    await tester.pumpWidget(
      _app(
        TelaPainel(
          central: central,
          abrirSensores: () => aberturas++,
          abrirSessoes: () {},
          abrirMapa: () {},
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Skylog'), findsOneWidget);
    expect(find.text('Prepare sua coleta'), findsOneWidget);
    expect(find.text('Luminosidade'), findsOneWidget);
    expect(find.text('1234 lx'), findsNothing);
    await _revelar(tester, find.text('Selecionar sensores'));
    await tester.tap(find.text('Selecionar sensores'));
    expect(aberturas, 1);
    expect(microfone.inicios, 0);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('selecionar som inclui o sensor sem ligar o microfone', (
    tester,
  ) async {
    _telefone(tester);
    await preparar();
    await tester.pumpWidget(_app(sensores()));
    await tester.pumpAndSettle();
    final seletor = _seletor('Som do celular');
    await _revelar(tester, seletor);
    await tester.tap(seletor);
    await tester.pumpAndSettle();
    expect(central.selecao.selecionados, {SensorId.somCelular});
    expect(tester.widget<Switch>(seletor).value, isTrue);
    expect(microfone.inicios, 0);
    expect(central.som.medindo, isFalse);
    expect(find.text('Testar microfone'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Iniciar coleta'),
          )
          .onPressed,
      isNull,
    );
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('teste explícito e leitura recente liberam o início da coleta', (
    tester,
  ) async {
    _telefone(tester);
    await preparar();
    await central.alterar(SensorId.somCelular, true);
    await tester.pumpWidget(_app(sensores()));
    await tester.pumpAndSettle();
    final iniciar = find.widgetWithText(FilledButton, 'Iniciar coleta');
    expect(tester.widget<FilledButton>(iniciar).onPressed, isNull);
    await _revelar(tester, find.text('Testar microfone'));
    await tester.tap(find.text('Testar microfone'));
    await tester.pumpAndSettle();
    expect(microfone.inicios, 1);
    expect(tester.widget<FilledButton>(iniciar).onPressed, isNull);
    microfone.amostra();
    await tester.pump();
    expect(find.text('-40,0 dBFS'), findsOneWidget);
    expect(gravador.registros, isEmpty);
    expect(tester.widget<FilledButton>(iniciar).onPressed, isNotNull);
    await _revelar(tester, iniciar);
    await tester.tap(iniciar);
    await tester.pumpAndSettle();
    expect(central.gravando, isTrue);
    expect(find.text('Encerrar coleta'), findsOneWidget);
    microfone.amostra();
    await tester.pump();
    expect(
      gravador.registros.where((r) => r['src'] == 'som_celular'),
      hasLength(1),
    );
    await _revelar(tester, find.text('Encerrar coleta'));
    await tester.tap(find.text('Encerrar coleta'));
    await tester.pumpAndSettle();
    expect(central.gravando, isFalse);
    expect(central.som.medindo, isFalse);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('durante a coleta os sete seletores ficam bloqueados', (
    tester,
  ) async {
    _telefone(tester);
    await preparar();
    await central.alterar(SensorId.somCelular, true);
    await central.testarMicrofone();
    microfone.amostra();
    await central.iniciarColeta();
    await tester.pumpWidget(_app(sensores()));
    await tester.pumpAndSettle();
    final seletores = tester.widgetList<Switch>(find.byType(Switch)).toList();
    expect(seletores, hasLength(SensorId.values.length));
    expect(seletores.every((seletor) => seletor.onChanged == null), isTrue);
    final gps = _seletor('Localização');
    await _revelar(tester, gps);
    await tester.tap(gps);
    await tester.pump();
    expect(central.selecao.selecionados, {SensorId.somCelular});
    expect(
      find.textContaining('A seleção fica bloqueada durante a coleta'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
    await central.encerrarColeta();
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('início e sensores cabem em 360 por 800 com fonte ampliada', (
    tester,
  ) async {
    _telefone(tester, tamanho: const Size(360, 800));
    await preparar();
    // Inclui os títulos longos, as ações de teste e todos os estados pendentes.
    for (final id in SensorId.values) {
      await central.alterar(id, true);
    }
    await tester.pumpWidget(
      _app(
        TelaPainel(
          central: central,
          abrirSensores: () {},
          abrirSessoes: () {},
          abrirMapa: () {},
        ),
        fonte: 1.6,
      ),
    );
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await _revelar(tester, find.text('Minhas coletas'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(_app(sensores(), fonte: 1.6));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await _revelar(tester, find.text('Testar microfone'));
    await tester.tap(find.text('Testar microfone'));
    await tester.pumpAndSettle();
    microfone.amostra();
    await tester.pump();
    expect(find.text('-40,0 dBFS'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await _revelar(tester, find.text('Temperatura auxiliar KY-028'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await _revelar(tester, find.text('Iniciar coleta'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
}

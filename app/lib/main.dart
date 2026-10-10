import 'dart:async';

import 'package:flutter/material.dart';

import 'ble/estacao_ble.dart';
import 'coleta/central_sensores.dart';
import 'rede/base_status.dart';
import 'rede/plano_atual.dart';
import 'telas/tela_config.dart';
import 'telas/tela_decibelimetro.dart';
import 'telas/tela_mapa.dart';
import 'telas/tela_painel.dart';
import 'telas/tela_planos.dart';
import 'telas/tela_sessoes.dart';
import 'telas/tela_sensores.dart';

void main() => runApp(const AppEstacao());

class AppEstacao extends StatefulWidget {
  const AppEstacao({super.key});

  @override
  State<AppEstacao> createState() => _AppEstacaoState();
}

class _AppEstacaoState extends State<AppEstacao> with WidgetsBindingObserver {
  // Uma unica instancia para o app inteiro: a conexao BLE nao pode ser
  // recriada ao trocar de aba, senao mudar de tela derrubaria a coleta.
  final _ble = EstacaoBle();
  final _base = BaseStatus();
  late final _central = CentralSensores(ble: _ble);
  late final _planos = PlanoAtual(ble: _ble, base: _base);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _base.iniciar();
    _ble.iniciarGpsTelefone();
    _ble.iniciarAutoConexao(); // conecta sozinho quando a estacao estiver perto
    // Em campo (gravando) o telefone nao fala com a base; no laboratorio
    // (parado) fala. A gravacao e o proprio sinal de "sai para medir".
    _ble.addListener(_coordenarBase);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden ||
        state == AppLifecycleState.detached) {
      unawaited(_central.suspender());
    }
  }

  void _coordenarBase() {
    if (_ble.gravador.gravando) {
      _base.pausar();
    } else {
      _base.retomar();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _ble.removeListener(_coordenarBase);
    _central.dispose();
    _ble.dispose();
    _base.dispose();
    _planos.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Skylog',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF2F9E57),
          brightness: Brightness.dark,
        ),
      ),
      home: Casca(ble: _ble, central: _central, planos: _planos),
    );
  }
}

class Casca extends StatefulWidget {
  final EstacaoBle ble;
  final CentralSensores central;
  final PlanoAtual planos;
  const Casca({
    super.key,
    required this.ble,
    required this.central,
    required this.planos,
  });

  @override
  State<Casca> createState() => _CascaState();
}

class _CascaState extends State<Casca> {
  int _aba = 0;

  void _abrirDecibelimetro() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => TelaDecibelimetro(
          controlador: widget.central.som,
          gerenciarControlador: false,
          coletaEmAndamento: () => widget.central.gravando,
        ),
      ),
    );
  }

  void _abrirAjustes() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => Scaffold(
          appBar: AppBar(title: const Text('Ajustes da estação')),
          body: SafeArea(child: TelaConfig(ble: widget.ble)),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final telas = [
      TelaPainel(
        central: widget.central,
        abrirSensores: () => setState(() => _aba = 1),
        abrirSessoes: () => setState(() => _aba = 4),
        abrirMapa: () => setState(() => _aba = 2),
      ),
      TelaSensores(
        central: widget.central,
        abrirDecibelimetro: _abrirDecibelimetro,
        abrirAjustes: _abrirAjustes,
      ),
      TelaMapa(ble: widget.ble, planos: widget.planos),
      TelaPlanos(planos: widget.planos),
      const TelaSessoes(),
    ];

    return Scaffold(
      body: SafeArea(child: telas[_aba]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _aba,
        onDestinationSelected: (i) => setState(() => _aba = i),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.home_outlined),
            selectedIcon: Icon(Icons.home),
            label: 'Início',
          ),
          NavigationDestination(
            icon: Icon(Icons.sensors_outlined),
            selectedIcon: Icon(Icons.sensors),
            label: 'Sensores',
          ),
          NavigationDestination(
            icon: Icon(Icons.map_outlined),
            selectedIcon: Icon(Icons.map),
            label: 'Mapa',
          ),
          NavigationDestination(
            icon: Icon(Icons.pin_drop_outlined),
            selectedIcon: Icon(Icons.pin_drop),
            label: 'Planos',
          ),
          NavigationDestination(
            icon: Icon(Icons.folder_outlined),
            selectedIcon: Icon(Icons.folder),
            label: 'Coletas',
          ),
        ],
      ),
    );
  }
}

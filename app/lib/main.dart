import 'package:flutter/material.dart';

import 'ble/estacao_ble.dart';
import 'rede/base_status.dart';
import 'telas/tela_config.dart';
import 'telas/tela_mapa.dart';
import 'telas/tela_painel.dart';
import 'telas/tela_sensores.dart';
import 'telas/tela_sessoes.dart';

void main() => runApp(const AppEstacao());

class AppEstacao extends StatefulWidget {
  const AppEstacao({super.key});

  @override
  State<AppEstacao> createState() => _AppEstacaoState();
}

class _AppEstacaoState extends State<AppEstacao> {
  // Uma unica instancia para o app inteiro: a conexao BLE nao pode ser
  // recriada ao trocar de aba, senao mudar de tela derrubaria a coleta.
  final _ble = EstacaoBle();
  final _base = BaseStatus();

  @override
  void initState() {
    super.initState();
    _base.iniciar();
    _ble.iniciarGpsTelefone();
    _ble.iniciarAutoConexao(); // conecta sozinho quando a estacao estiver perto
    // Em campo (gravando) o telefone nao fala com a base; no laboratorio
    // (parado) fala. A gravacao e o proprio sinal de "sai para medir".
    _ble.addListener(_coordenarBase);
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
    _ble.removeListener(_coordenarBase);
    _ble.dispose();
    _base.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Estacao Movel',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF2F9E57),
          brightness: Brightness.dark,
        ),
      ),
      home: Casca(ble: _ble, base: _base),
    );
  }
}

class Casca extends StatefulWidget {
  final EstacaoBle ble;
  final BaseStatus base;
  const Casca({super.key, required this.ble, required this.base});

  @override
  State<Casca> createState() => _CascaState();
}

class _CascaState extends State<Casca> {
  int _aba = 0;

  @override
  Widget build(BuildContext context) {
    final telas = [
      TelaPainel(ble: widget.ble, base: widget.base),
      TelaMapa(ble: widget.ble),
      TelaSensores(ble: widget.ble),
      TelaConfig(ble: widget.ble),
      const TelaSessoes(),
    ];

    return Scaffold(
      body: SafeArea(child: telas[_aba]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _aba,
        onDestinationSelected: (i) => setState(() => _aba = i),
        destinations: const [
          NavigationDestination(
              icon: Icon(Icons.monitor_heart_outlined),
              selectedIcon: Icon(Icons.monitor_heart),
              label: 'Painel'),
          NavigationDestination(
              icon: Icon(Icons.map_outlined),
              selectedIcon: Icon(Icons.map),
              label: 'Mapa'),
          NavigationDestination(
              icon: Icon(Icons.speed_outlined),
              selectedIcon: Icon(Icons.speed),
              label: 'Sensores'),
          NavigationDestination(
              icon: Icon(Icons.tune_outlined),
              selectedIcon: Icon(Icons.tune),
              label: 'Estacao'),
          NavigationDestination(
              icon: Icon(Icons.folder_outlined),
              selectedIcon: Icon(Icons.folder),
              label: 'Sessoes'),
        ],
      ),
    );
  }
}

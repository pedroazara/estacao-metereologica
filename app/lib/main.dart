import 'package:flutter/material.dart';

import 'ble/estacao_ble.dart';
import 'telas/tela_config.dart';
import 'telas/tela_painel.dart';
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

  @override
  void dispose() {
    _ble.dispose();
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
      home: Casca(ble: _ble),
    );
  }
}

class Casca extends StatefulWidget {
  final EstacaoBle ble;
  const Casca({super.key, required this.ble});

  @override
  State<Casca> createState() => _CascaState();
}

class _CascaState extends State<Casca> {
  int _aba = 0;

  @override
  Widget build(BuildContext context) {
    final telas = [
      TelaPainel(ble: widget.ble),
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

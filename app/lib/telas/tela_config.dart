import 'package:flutter/material.dart';

import '../ble/estacao_ble.dart';
import '../modelo/leituras.dart';

/// Ajustes da estacao — versao simples: liga/desliga o sensor ambiental e o
/// intervalo de envio. A tela mostra o que a estacao CONFIRMOU (ela aplica os
/// limites dela e responde), com feedback imediato no toque.
class TelaConfig extends StatefulWidget {
  final EstacaoBle ble;
  const TelaConfig({super.key, required this.ble});

  @override
  State<TelaConfig> createState() => _TelaConfigState();
}

class _TelaConfigState extends State<TelaConfig> {
  Configuracao? _local;
  bool _enviando = false;

  @override
  void initState() {
    super.initState();
    _local = widget.ble.config;
    widget.ble.addListener(_aoMudar);
  }

  @override
  void dispose() {
    widget.ble.removeListener(_aoMudar);
    super.dispose();
  }

  void _aoMudar() {
    final v = widget.ble.config;
    if (v == null) return;
    setState(() {
      _local = v;
      _enviando = false;
    });
  }

  Future<void> _mudar(Configuracao nova) async {
    setState(() {
      _local = nova;
      _enviando = true;
    });
    final ok = await widget.ble.enviarConfig(nova);
    if (!mounted) return;
    if (!ok) {
      setState(() {
        _local = widget.ble.config;
        _enviando = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('nao consegui escrever na estacao')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;
    if (!widget.ble.conectado) {
      return _aviso(context, Icons.link_off,
          'Conecte na estacao para ver e mudar os ajustes.');
    }
    final c = _local;
    if (c == null) {
      return _aviso(context, Icons.hourglass_empty, 'lendo a configuracao...');
    }

    return Stack(children: [
      ListView(padding: const EdgeInsets.all(16), children: [
        Text('Sensor ambiental', style: TextStyle(fontSize: 12, color: fraco)),
        const SizedBox(height: 6),
        Card(
          child: SwitchListTile(
            title: const Text('Temperatura e umidade'),
            subtitle: const Text('medida pela estacao e enviada por Bluetooth'),
            value: c.amb,
            onChanged: (v) => _mudar(c.copiaCom(amb: v)),
          ),
        ),
        const SizedBox(height: 24),
        Text('Intervalo de envio', style: TextStyle(fontSize: 12, color: fraco)),
        const SizedBox(height: 6),
        Card(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 10),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                const Text('A cada'),
                const Spacer(),
                Text(
                    '${(c.pubMs / 1000).toStringAsFixed(c.pubMs % 1000 == 0 ? 0 : 1)} s',
                    style: const TextStyle(
                        fontFamily: 'monospace', fontWeight: FontWeight.w600)),
              ]),
              Slider(
                value: c.pubMs.clamp(500, 10000).toDouble(),
                min: 500,
                max: 10000,
                divisions: 19,
                label: '${(c.pubMs / 1000).toStringAsFixed(1)} s',
                onChanged: (v) =>
                    setState(() => _local = c.copiaCom(pubMs: v.round())),
                onChangeEnd: (v) => _mudar(c.copiaCom(pubMs: v.round())),
              ),
              Text('entre 0,5 e 10 segundos',
                  style: TextStyle(fontSize: 11, color: fraco)),
            ]),
          ),
        ),
        const SizedBox(height: 24),
        Card(
          color: Theme.of(context).colorScheme.surfaceContainerHighest,
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Row(children: [
              Icon(Icons.info_outline, size: 18, color: fraco),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  'A posicao vem do GPS do proprio telefone, nao da estacao. '
                  'Os ajustes ficam gravados na estacao e sobrevivem a queda de '
                  'energia.',
                  style: TextStyle(fontSize: 12, color: fraco),
                ),
              ),
            ]),
          ),
        ),
      ]),
      if (_enviando)
        const Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: LinearProgressIndicator(minHeight: 2)),
    ]);
  }

  static Widget _aviso(BuildContext c, IconData icone, String texto) {
    final fraco = Theme.of(c).colorScheme.outline;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          Icon(icone, size: 48, color: fraco),
          const SizedBox(height: 14),
          Text(texto, textAlign: TextAlign.center, style: TextStyle(color: fraco)),
        ]),
      ),
    );
  }
}

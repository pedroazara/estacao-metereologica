import 'package:flutter/material.dart';

import '../ble/estacao_ble.dart';
import '../modelo/leituras.dart';

/// Ajustes da estacao — grava na caracteristica `cfg` do ESP32.
///
/// FEEDBACK OTIMISTA. O toque muda a tela NA HORA e marca "enviando"; quando a
/// estacao confirma (ela responde por notify com o estado REAL, ja com os
/// limites aplicados), a tela reconcilia com o que a estacao de fato ficou. Sem
/// isso, o controle parecia "voltar sozinho" no toque e so mudar 1 s depois,
/// quando a resposta chegava — parecia quebrado.
class TelaConfig extends StatefulWidget {
  final EstacaoBle ble;
  const TelaConfig({super.key, required this.ble});

  @override
  State<TelaConfig> createState() => _TelaConfigState();
}

class _TelaConfigState extends State<TelaConfig> {
  Configuracao? _local; // o que a tela mostra (otimista)
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
    // A estacao confirmou (ou reconectou): reconcilia a tela com a verdade.
    final vindo = widget.ble.config;
    if (vindo == null) return;
    setState(() {
      _local = vindo;
      _enviando = false;
    });
  }

  Future<void> _mudar(Configuracao nova) async {
    setState(() {
      _local = nova; // otimista: aparece na hora
      _enviando = true;
    });
    final ok = await widget.ble.enviarConfig(nova);
    if (!mounted) return;
    if (!ok) {
      setState(() {
        _local = widget.ble.config; // desfaz para o real
        _enviando = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('nao consegui escrever na estacao')),
      );
    }
    // Em caso de sucesso, _aoMudar reconcilia quando a notify chegar.
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.ble.conectado) {
      return _aviso(context, Icons.link_off,
          'Conecte na estacao para ver e mudar os ajustes.');
    }
    final c = _local;
    if (c == null) {
      return _aviso(context, Icons.hourglass_empty, 'lendo a configuracao...');
    }
    final fraco = Theme.of(context).colorScheme.outline;

    return Stack(children: [
      ListView(padding: const EdgeInsets.all(16), children: [
        Text('Sensores', style: TextStyle(fontSize: 12, color: fraco)),
        const SizedBox(height: 6),
        Card(
          child: Column(children: [
            SwitchListTile(
              title: const Text('GPS'),
              subtitle: const Text('posicao, velocidade, satelites'),
              value: c.gps,
              onChanged: (v) => _mudar(c.copiaCom(gps: v)),
            ),
            SwitchListTile(
              title: const Text('IMU'),
              subtitle: const Text('aceleracao, giro e atitude'),
              value: c.imu,
              onChanged: (v) => _mudar(c.copiaCom(imu: v)),
            ),
            SwitchListTile(
              title: const Text('Ambiental'),
              subtitle: const Text('temperatura e umidade'),
              value: c.amb,
              onChanged: (v) => _mudar(c.copiaCom(amb: v)),
            ),
          ]),
        ),
        const SizedBox(height: 8),
        Text(
          'Desligar um sensor apenas libera o tempo dele no barramento; a taxa '
          'dos outros nao muda.',
          style: TextStyle(fontSize: 12, color: fraco),
        ),
        const SizedBox(height: 24),
        Text('Tempo', style: TextStyle(fontSize: 12, color: fraco)),
        const SizedBox(height: 6),
        Card(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                const Text('Intervalo de envio'),
                const Spacer(),
                Text(
                    '${(c.pubMs / 1000).toStringAsFixed(c.pubMs % 1000 == 0 ? 0 : 1)} s',
                    style: const TextStyle(
                        fontFamily: 'monospace', fontWeight: FontWeight.w600)),
              ]),
              Slider(
                value: c.pubMs.clamp(200, 10000).toDouble(),
                min: 200,
                max: 10000,
                divisions: 49,
                label: '${c.pubMs} ms',
                // Mexe a tela ao arrastar (feedback imediato)...
                onChanged: (v) => setState(
                    () => _local = c.copiaCom(pubMs: v.round())),
                // ...mas so ENVIA ao soltar: cada arrasto dispara dezenas de
                // valores, e gravar todos entupiria o BLE e desgastaria a flash.
                onChangeEnd: (v) => _mudar(c.copiaCom(pubMs: v.round())),
              ),
              Text('entre 0,2 e 10 s',
                  style: TextStyle(fontSize: 11, color: fraco)),
              const SizedBox(height: 10),
              Row(children: [
                const Text('Amostras de IMU por envio'),
                const Spacer(),
                Text('${c.lote}',
                    style: const TextStyle(
                        fontFamily: 'monospace', fontWeight: FontWeight.w600)),
              ]),
              Slider(
                value: c.lote.clamp(1, c.loteMax).toDouble(),
                min: 1,
                max: c.loteMax.toDouble(),
                divisions: c.loteMax > 1 ? c.loteMax - 1 : 1,
                label: '${c.lote}',
                onChanged: (v) =>
                    setState(() => _local = c.copiaCom(lote: v.round())),
                onChangeEnd: (v) => _mudar(c.copiaCom(lote: v.round())),
              ),
              Text('maximo ${c.loteMax}, definido no firmware',
                  style: TextStyle(fontSize: 11, color: fraco)),
              const SizedBox(height: 6),
            ]),
          ),
        ),
        const SizedBox(height: 24),
        Text('Base', style: TextStyle(fontSize: 12, color: fraco)),
        const SizedBox(height: 6),
        Card(
          child: SwitchListTile(
            title: const Text('Enviar para a base por Wi-Fi'),
            subtitle: const Text('publicacao MQTT no Raspberry Pi'),
            value: c.mqtt,
            onChanged: (v) => _mudar(c.copiaCom(mqtt: v)),
          ),
        ),
        const SizedBox(height: 8),
        Text(
          'Desligar economiza bateria em campo, onde nao ha rede. O BLE continua '
          'funcionando e o celular segue gravando.',
          style: TextStyle(fontSize: 12, color: fraco),
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
                  'Os ajustes ficam gravados na estacao e sobrevivem a queda de '
                  'energia.',
                  style: TextStyle(fontSize: 12, color: fraco),
                ),
              ),
            ]),
          ),
        ),
      ]),
      // Faixa fina de "enviando" no topo, para o toque nunca parecer ignorado.
      if (_enviando)
        const Positioned(
            top: 0, left: 0, right: 0, child: LinearProgressIndicator(minHeight: 2)),
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

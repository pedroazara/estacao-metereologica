import 'package:flutter/material.dart';

import '../ble/estacao_ble.dart';
import '../modelo/leituras.dart';

/// Ajustes da estacao — grava na caracteristica `cfg` do ESP32.
///
/// A tela NUNCA mostra o que voce pediu: mostra o que a estacao respondeu.
/// O firmware aplica limites proprios (pubms fica entre 200 e 60000, lote nao
/// passa do tamanho do array) e devolve o estado real por notificacao. Exibir o
/// pedido enquanto a estacao fez outra coisa e a forma mais facil de o
/// pesquisador achar que configurou algo que nao esta valendo.
class TelaConfig extends StatelessWidget {
  final EstacaoBle ble;
  const TelaConfig({super.key, required this.ble});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: ble,
      builder: (context, _) {
        if (!ble.conectado) {
          return _Aviso(
              icone: Icons.link_off,
              texto: 'Conecte na estacao para ver e mudar os ajustes.');
        }
        final c = ble.config;
        if (c == null) {
          return const _Aviso(
              icone: Icons.hourglass_empty, texto: 'lendo a configuracao...');
        }
        return _Formulario(ble: ble, cfg: c);
      },
    );
  }
}

class _Formulario extends StatelessWidget {
  final EstacaoBle ble;
  final Configuracao cfg;
  const _Formulario({required this.ble, required this.cfg});

  Future<void> _enviar(BuildContext context, Configuracao nova) async {
    final ok = await ble.enviarConfig(nova);
    if (!context.mounted) return;
    if (!ok) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('nao consegui escrever na estacao')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;

    return ListView(padding: const EdgeInsets.all(16), children: [
      Text('Sensores', style: TextStyle(fontSize: 12, color: fraco)),
      const SizedBox(height: 6),
      Card(
        child: Column(children: [
          SwitchListTile(
            title: const Text('GPS'),
            subtitle: const Text('posicao, velocidade, satelites'),
            value: cfg.gps,
            onChanged: (v) => _enviar(context, cfg.copiaCom(gps: v)),
          ),
          SwitchListTile(
            title: const Text('IMU'),
            subtitle: const Text('aceleracao, giro e atitude'),
            value: cfg.imu,
            onChanged: (v) => _enviar(context, cfg.copiaCom(imu: v)),
          ),
          SwitchListTile(
            title: const Text('Ambiental'),
            subtitle: const Text('temperatura e umidade'),
            value: cfg.amb,
            onChanged: (v) => _enviar(context, cfg.copiaCom(amb: v)),
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
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              const Text('Intervalo de envio'),
              const Spacer(),
              Text('${(cfg.pubMs / 1000).toStringAsFixed(cfg.pubMs % 1000 == 0 ? 0 : 1)} s',
                  style: const TextStyle(
                      fontFamily: 'monospace', fontWeight: FontWeight.w600)),
            ]),
            Slider(
              value: cfg.pubMs.clamp(200, 10000).toDouble(),
              min: 200,
              max: 10000,
              divisions: 49,
              label: '${cfg.pubMs} ms',
              onChanged: (_) {},
              // So envia ao SOLTAR: cada arrasto do dedo dispara dezenas de
              // valores, e escrever todos entupiria a fila do BLE e faria a
              // estacao gravar em Preferences dezenas de vezes por gesto —
              // desgaste de flash por causa de um slider.
              onChangeEnd: (v) =>
                  _enviar(context, cfg.copiaCom(pubMs: v.round())),
            ),
            Text('entre 0,2 e 10 s', style: TextStyle(fontSize: 11, color: fraco)),
            const SizedBox(height: 10),
            Row(children: [
              const Text('Amostras de IMU por envio'),
              const Spacer(),
              Text('${cfg.lote}',
                  style: const TextStyle(
                      fontFamily: 'monospace', fontWeight: FontWeight.w600)),
            ]),
            Slider(
              value: cfg.lote.clamp(1, cfg.loteMax).toDouble(),
              min: 1,
              max: cfg.loteMax.toDouble(),
              divisions: cfg.loteMax > 1 ? cfg.loteMax - 1 : 1,
              label: '${cfg.lote}',
              onChanged: (_) {},
              onChangeEnd: (v) =>
                  _enviar(context, cfg.copiaCom(lote: v.round())),
            ),
            Text('maximo ${cfg.loteMax}, definido no firmware',
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
          value: cfg.mqtt,
          onChanged: (v) => _enviar(context, cfg.copiaCom(mqtt: v)),
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
    ]);
  }
}

class _Aviso extends StatelessWidget {
  final IconData icone;
  final String texto;
  const _Aviso({required this.icone, required this.texto});

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;
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

import 'package:flutter/material.dart';

import '../ble/estacao_ble.dart';
import '../rede/base_status.dart';

/// Painel de campo, simples. Responde: estou gravando? o GPS pegou posicao? a
/// estacao esta conectada (para o ambiente)? Analise fina e da base.
class TelaPainel extends StatelessWidget {
  final EstacaoBle ble;
  final BaseStatus base;
  const TelaPainel({super.key, required this.ble, required this.base});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([ble, base]),
      builder: (context, _) {
        final fraco = Theme.of(context).colorScheme.outline;
        final a = ble.ambiente;
        final g = ble.gravador;
        final temGps = ble.telLat != null;

        return ListView(padding: const EdgeInsets.all(16), children: [
          Row(children: [
            Expanded(
              child: Text('Estacao Movel',
                  style: Theme.of(context).textTheme.titleMedium),
            ),
            _ChipBase(base: base),
          ]),
          const SizedBox(height: 4),
          Text(
            ble.conectado
                ? 'estacao conectada (${ble.status?.dev ?? "esp32"})'
                : 'estacao desconectada — procurando...',
            style: TextStyle(fontSize: 12, color: fraco),
          ),
          const SizedBox(height: 14),

          // Gravar: a acao central. So o que estiver dentro da gravacao entra
          // na coleta e vira trajetoria.
          _CartaoGravacao(ble: ble),
          const SizedBox(height: 12),

          Row(children: [
            Expanded(
                child: _Numero(
                    rotulo: 'Temperatura',
                    valor: a == null ? '--' : a.temperatura.toStringAsFixed(1),
                    unidade: '°C',
                    cor: const Color(0xFFFF7A5E))),
            const SizedBox(width: 12),
            Expanded(
                child: _Numero(
                    rotulo: 'Umidade',
                    valor: a == null ? '--' : a.umidade.toStringAsFixed(1),
                    unidade: '%',
                    cor: const Color(0xFF63AEF5))),
          ]),
          if (!ble.conectado)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                'Temperatura e umidade vem da estacao por Bluetooth. '
                'Conecte para ve-las.',
                style: TextStyle(fontSize: 12, color: fraco),
              ),
            ),
          const SizedBox(height: 12),

          // GPS do telefone: a fonte de posicao.
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('POSICAO (GPS DO TELEFONE)',
                        style: TextStyle(
                            fontSize: 10, letterSpacing: 1.1, color: fraco)),
                    const SizedBox(height: 10),
                    if (!temGps)
                      Text(ble.gps.mensagem, style: TextStyle(color: fraco))
                    else
                      Row(children: [
                        Expanded(
                          child: SelectableText(
                            '${ble.telLat!.toStringAsFixed(6)}, '
                            '${ble.telLon!.toStringAsFixed(6)}',
                            style: const TextStyle(
                                fontFamily: 'monospace', fontSize: 14),
                          ),
                        ),
                        Text(
                          ble.precisaoM == null
                              ? ''
                              : '±${ble.precisaoM!.toStringAsFixed(0)} m',
                          style: TextStyle(fontSize: 12, color: fraco),
                        ),
                      ]),
                  ]),
            ),
          ),

          if (ble.conectado) ...[
            const SizedBox(height: 20),
            OutlinedButton.icon(
              onPressed: () async {
                if (g.gravando) {
                  final ok = await _confirmar(context, 'Encerrar a gravacao?',
                      'A sessao sera fechada e salva.');
                  if (!ok) return;
                }
                await ble.desconectar();
              },
              icon: const Icon(Icons.link_off),
              label: const Text('Desconectar'),
            ),
          ],
        ]);
      },
    );
  }
}

Future<bool> _confirmar(BuildContext c, String titulo, String texto) async {
  final r = await showDialog<bool>(
    context: c,
    builder: (_) => AlertDialog(
      title: Text(titulo),
      content: Text(texto),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancelar')),
        FilledButton(
            onPressed: () => Navigator.pop(c, true), child: const Text('Sim')),
      ],
    ),
  );
  return r ?? false;
}

class _CartaoGravacao extends StatelessWidget {
  final EstacaoBle ble;
  const _CartaoGravacao({required this.ble});

  @override
  Widget build(BuildContext context) {
    final g = ble.gravador;
    final semGps = ble.telLat == null;
    final cor = g.gravando ? const Color(0xFFE04B4B) : null;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(children: [
          Row(children: [
            Icon(g.gravando ? Icons.fiber_manual_record : Icons.play_circle_outline,
                color: cor, size: 28),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(g.gravando ? 'Gravando medicao' : 'Pronto para medir',
                        style: TextStyle(
                            fontWeight: FontWeight.w600, color: cor)),
                    Text(
                      g.gravando
                          ? '${ble.trilha.length} pontos de trajetoria'
                          : 'toque para comecar a registrar a trajetoria',
                      style: TextStyle(
                          fontSize: 12,
                          color: Theme.of(context).colorScheme.outline),
                    ),
                  ]),
            ),
            FilledButton(
              onPressed: () async {
                if (g.gravando) {
                  await ble.pararGravacao();
                } else {
                  await ble.iniciarGravacao();
                }
              },
              child: Text(g.gravando ? 'Parar' : 'Gravar'),
            ),
          ]),
          // Aviso honesto: gravar sem GPS registra so o ambiente, sem trajeto.
          if (!g.gravando && semGps)
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Row(children: [
                const Icon(Icons.warning_amber, size: 16, color: Color(0xFFD29922)),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'Sem posicao do GPS ainda — a trajetoria so comeca quando o '
                    'telefone pegar sinal.',
                    style: TextStyle(
                        fontSize: 11,
                        color: Theme.of(context).colorScheme.outline),
                  ),
                ),
              ]),
            ),
        ]),
      ),
    );
  }
}

class _Numero extends StatelessWidget {
  final String rotulo, valor, unidade;
  final Color cor;
  const _Numero(
      {required this.rotulo,
      required this.valor,
      required this.unidade,
      required this.cor});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(rotulo,
              style: TextStyle(
                  fontSize: 11, color: Theme.of(context).colorScheme.outline)),
          const SizedBox(height: 6),
          Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Text(valor,
                    style: TextStyle(
                        fontSize: 30, fontWeight: FontWeight.w700, color: cor)),
                const SizedBox(width: 4),
                Text(unidade,
                    style: TextStyle(
                        fontSize: 13,
                        color: Theme.of(context).colorScheme.outline)),
              ]),
        ]),
      ),
    );
  }
}

/// Chip de alcance da base: em campo (gravando) fica pausado; parado, mostra se
/// da para sincronizar.
class _ChipBase extends StatelessWidget {
  final BaseStatus base;
  const _ChipBase({required this.base});

  @override
  Widget build(BuildContext context) {
    final IconData icone;
    final String texto;
    final Color cor;
    if (base.pausado) {
      icone = Icons.hiking;
      texto = 'em campo';
      cor = const Color(0xFFD29922);
    } else if (base.alcancavel) {
      icone = Icons.cloud_done;
      texto = 'base ao alcance';
      cor = const Color(0xFF3FB950);
    } else {
      icone = Icons.cloud_off;
      texto = 'base fora de alcance';
      cor = const Color(0xFF7D8794);
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: const Color(0xFF0A0E13),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: const Color(0xFF232D38)),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icone, size: 14, color: cor),
        const SizedBox(width: 6),
        Text(texto, style: TextStyle(fontSize: 11, color: cor)),
      ]),
    );
  }
}

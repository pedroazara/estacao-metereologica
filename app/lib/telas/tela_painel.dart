import 'package:flutter/material.dart';

import '../ble/estacao_ble.dart';
import '../modelo/leituras.dart';

/// A tela de campo. Responde, em ordem de importancia: a estacao esta viva? o
/// GPS pegou fix? estou gravando? Analise e da base, nao daqui.
class TelaPainel extends StatelessWidget {
  final EstacaoBle ble;
  const TelaPainel({super.key, required this.ble});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: ble,
      builder: (context, _) {
        if (!ble.conectado) return _Desconectado(ble: ble);
        return _Conectado(ble: ble);
      },
    );
  }
}

class _Desconectado extends StatelessWidget {
  final EstacaoBle ble;
  const _Desconectado({required this.ble});

  @override
  Widget build(BuildContext context) {
    final procurando = ble.estado == EstadoLigacao.procurando ||
        ble.estado == EstadoLigacao.conectando;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.sensors_off,
                size: 64, color: Theme.of(context).colorScheme.outline),
            const SizedBox(height: 20),
            Text('Estacao Movel',
                style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 8),
            Text(ble.mensagem,
                textAlign: TextAlign.center,
                style: TextStyle(color: Theme.of(context).colorScheme.outline)),
            const SizedBox(height: 28),
            FilledButton.icon(
              onPressed: procurando ? null : ble.procurarEConectar,
              icon: procurando
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.bluetooth_searching),
              label: Text(procurando ? 'procurando...' : 'Conectar'),
            ),
          ],
        ),
      ),
    );
  }
}

class _Conectado extends StatelessWidget {
  final EstacaoBle ble;
  const _Conectado({required this.ble});

  @override
  Widget build(BuildContext context) {
    final s = ble.status;
    final p = ble.posicao;
    final a = ble.ambiente;
    final g = ble.gravador;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _Cabecalho(ble: ble),
        const SizedBox(height: 12),

        // Gravacao vem no topo: e a unica acao irreversivel desta tela. Se o
        // pesquisador esquecer de ligar, a coleta se perde — e enquanto nao ha
        // cartao SD no ESP32, se perde para sempre.
        _CartaoGravacao(ble: ble),
        const SizedBox(height: 12),

        Row(children: [
          Expanded(
              child: _Numero(
                  rotulo: 'Temperatura',
                  valor: a == null ? '--' : a.temperatura.toStringAsFixed(1),
                  unidade: 'C',
                  cor: const Color(0xFFFF7A5E))),
          const SizedBox(width: 12),
          Expanded(
              child: _Numero(
                  rotulo: 'Umidade',
                  valor: a == null ? '--' : a.umidade.toStringAsFixed(1),
                  unidade: '%',
                  cor: const Color(0xFF63AEF5))),
        ]),
        const SizedBox(height: 12),

        _CartaoGps(p: p, s: s),
        const SizedBox(height: 12),

        _CartaoImu(lote: ble.ultimoLote),
        const SizedBox(height: 12),

        _CartaoDiagnostico(ble: ble),
        const SizedBox(height: 20),

        OutlinedButton.icon(
          onPressed: () async {
            if (g.gravando) {
              final ok = await _confirmar(context,
                  'Encerrar a gravacao?', 'A sessao sera fechada e salva.');
              if (!ok) return;
            }
            await ble.desconectar();
          },
          icon: const Icon(Icons.link_off),
          label: const Text('Desconectar'),
        ),
      ],
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

class _Cabecalho extends StatelessWidget {
  final EstacaoBle ble;
  const _Cabecalho({required this.ble});

  @override
  Widget build(BuildContext context) {
    final s = ble.status;
    return Row(children: [
      const Icon(Icons.circle, size: 10, color: Color(0xFF4FD07A)),
      const SizedBox(width: 8),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(s?.dev ?? 'estacao',
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
          Text(
            s == null
                ? 'aguardando status'
                : 'no ar ha ${_duracao(s.uptimeS)}'
                    '${s.wifi ? "  ·  wifi" : ""}'
                    '${s.mqtt ? "  ·  base" : ""}',
            style: TextStyle(
                fontSize: 12, color: Theme.of(context).colorScheme.outline),
          ),
        ]),
      ),
    ]);
  }
}

class _CartaoGravacao extends StatelessWidget {
  final EstacaoBle ble;
  const _CartaoGravacao({required this.ble});

  @override
  Widget build(BuildContext context) {
    final g = ble.gravador;
    final cor = g.gravando ? const Color(0xFFE04B4B) : null;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(children: [
          Icon(g.gravando ? Icons.fiber_manual_record : Icons.save_outlined,
              color: cor),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(g.gravando ? 'Gravando' : 'Nao esta gravando',
                      style: TextStyle(
                          fontWeight: FontWeight.w600, color: cor)),
                  Text(
                    g.gravando
                        ? '${g.linhas} registros'
                        : 'o dado passa e nao fica guardado',
                    style: TextStyle(
                        fontSize: 12,
                        color: Theme.of(context).colorScheme.outline),
                  ),
                ]),
          ),
          FilledButton.tonal(
            onPressed: () async {
              if (g.gravando) {
                final ok = await _confirmar(context, 'Encerrar a gravacao?',
                    'A sessao sera fechada e salva em Sessoes.');
                if (!ok) return;
                await ble.pararGravacao();
              } else {
                await ble.iniciarGravacao();
              }
            },
            child: Text(g.gravando ? 'Parar' : 'Gravar'),
          ),
        ]),
      ),
    );
  }
}

class _CartaoGps extends StatelessWidget {
  final Posicao? p;
  final Status? s;
  const _CartaoGps({required this.p, required this.s});

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;
    final temFix = p?.fix ?? false;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text('GPS', style: TextStyle(fontSize: 12, color: fraco)),
            const Spacer(),
            Chip(
              visualDensity: VisualDensity.compact,
              label: Text(temFix ? 'com fix' : 'sem fix',
                  style: const TextStyle(fontSize: 11)),
              backgroundColor: temFix
                  ? const Color(0xFF1E4620)
                  : const Color(0xFF4A3B12),
            ),
          ]),
          const SizedBox(height: 10),
          if (p == null)
            Text('aguardando posicao...', style: TextStyle(color: fraco))
          else ...[
            SelectableText(
              '${p!.lat.toStringAsFixed(6)}, ${p!.lon.toStringAsFixed(6)}',
              style: const TextStyle(
                  fontFamily: 'monospace', fontSize: 15, height: 1.4),
            ),
            const SizedBox(height: 10),
            Wrap(spacing: 18, runSpacing: 6, children: [
              _Par('satelites', '${p!.satelites}'),
              _Par('hdop', p!.hdop.toStringAsFixed(2)),
              _Par('altitude', '${p!.alt.toStringAsFixed(0)} m'),
              _Par('velocidade', '${p!.vel.toStringAsFixed(1)} km/h'),
              _Par('rumo', p!.rumo.toStringAsFixed(0)),
            ]),
          ],
        ]),
      ),
    );
  }
}

class _CartaoImu extends StatelessWidget {
  final LoteImu? lote;
  const _CartaoImu({required this.lote});

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;
    final a = (lote?.amostras.isNotEmpty ?? false) ? lote!.amostras.last : null;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('IMU', style: TextStyle(fontSize: 12, color: fraco)),
          const SizedBox(height: 10),
          if (a == null)
            Text('aguardando amostras...', style: TextStyle(color: fraco))
          else
            Wrap(spacing: 18, runSpacing: 6, children: [
              _Par('|a|', '${a.moduloG.toStringAsFixed(3)} g'),
              _Par('roll', a.roll.toStringAsFixed(1)),
              _Par('pitch', a.pitch.toStringAsFixed(1)),
              _Par('yaw', a.yaw.toStringAsFixed(1)),
              _Par('dt', '${(lote!.dt * 1000).toStringAsFixed(0)} ms'),
              _Par('pedaco', '${lote!.pedaco + 1}/${lote!.totalPedacos}'),
            ]),
        ]),
      ),
    );
  }
}

class _CartaoDiagnostico extends StatelessWidget {
  final EstacaoBle ble;
  const _CartaoDiagnostico({required this.ble});

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;
    final falhas = ble.falhasDeParse;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Ligacao', style: TextStyle(fontSize: 12, color: fraco)),
          const SizedBox(height: 10),
          Wrap(spacing: 18, runSpacing: 6, children: [
            _Par('notificacoes', '${ble.notificacoesRecebidas}'),
            _Par('falhas de leitura', '$falhas'),
          ]),
          // Falha de parse quase sempre e MTU: a notificacao chegou cortada e o
          // JSON nao fecha. Dizer isso aqui poupa uma investigacao inteira.
          if (falhas > 0) ...[
            const SizedBox(height: 10),
            Text(
              'Notificacoes chegando truncadas. Costuma ser o MTU: '
              'desconecte e conecte de novo.',
              style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.error),
            ),
          ],
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
          Row(crossAxisAlignment: CrossAxisAlignment.baseline,
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

class _Par extends StatelessWidget {
  final String rotulo, valor;
  const _Par(this.rotulo, this.valor);

  @override
  Widget build(BuildContext context) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(rotulo,
          style: TextStyle(
              fontSize: 10, color: Theme.of(context).colorScheme.outline)),
      Text(valor,
          style: const TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w600,
              fontFamily: 'monospace')),
    ]);
  }
}

String _duracao(int s) {
  final h = s ~/ 3600, m = (s % 3600) ~/ 60, x = s % 60;
  if (h > 0) return '${h}h${m.toString().padLeft(2, '0')}';
  if (m > 0) return '${m}m${x.toString().padLeft(2, '0')}s';
  return '${x}s';
}

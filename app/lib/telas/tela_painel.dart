import 'package:flutter/material.dart';

import '../coleta/central_sensores.dart';
import '../modelo/sensores.dart';

/// O início organiza a coleta; a conferência dos valores fica em Sensores.
class TelaPainel extends StatelessWidget {
  final CentralSensores central;
  final VoidCallback abrirSensores;
  final VoidCallback abrirSessoes;
  final VoidCallback abrirMapa;

  const TelaPainel({
    super.key,
    required this.central,
    required this.abrirSensores,
    required this.abrirSessoes,
    required this.abrirMapa,
  });

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: central,
    builder: (context, _) {
      final tema = Theme.of(context);
      final cores = tema.colorScheme;
      final escolhidos = SensorId.values
          .where((id) => central.conferir(id).selecionado)
          .toList();
      return ListView(
        padding: const EdgeInsets.fromLTRB(20, 24, 20, 32),
        children: [
          Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 780),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: cores.primaryContainer,
                          borderRadius: BorderRadius.circular(18),
                        ),
                        child: Icon(
                          Icons.landscape_outlined,
                          color: cores.onPrimaryContainer,
                          size: 30,
                        ),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              'Skylog',
                              style: tema.textTheme.headlineMedium,
                            ),
                            Text(
                              'Suas medições em campo',
                              style: tema.textTheme.bodyMedium?.copyWith(
                                color: cores.onSurfaceVariant,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 28),
                  Card.filled(
                    margin: EdgeInsets.zero,
                    color: central.gravando
                        ? cores.primaryContainer
                        : cores.surfaceContainerHigh,
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Row(
                            children: [
                              Icon(
                                central.gravando
                                    ? Icons.radio_button_checked
                                    : Icons.explore_outlined,
                                color: central.gravando
                                    ? cores.onPrimaryContainer
                                    : cores.primary,
                              ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: Text(
                                  central.gravando
                                      ? 'Coleta em andamento'
                                      : 'Prepare sua coleta',
                                  style: tema.textTheme.titleLarge,
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 12),
                          Text(
                            central.gravando
                                ? 'Acompanhe os sensores durante a coleta. '
                                      'Ao encerrar, os registros ficam salvos no celular.'
                                : 'Escolha o que deseja medir e confira os sensores '
                                      'antes de começar a registrar.',
                            style: tema.textTheme.bodyLarge,
                          ),
                          const SizedBox(height: 24),
                          Wrap(
                            spacing: 32,
                            runSpacing: 16,
                            children: central.gravando
                                ? [
                                    _Indicador(
                                      valor: _duracao(central.duracao),
                                      rotulo: 'Tempo de coleta',
                                    ),
                                    _Indicador(
                                      valor: '${central.registros}',
                                      rotulo: 'Registros salvos',
                                    ),
                                  ]
                                : [
                                    _Indicador(
                                      valor: '${central.selecionados}',
                                      rotulo: central.selecionados == 1
                                          ? 'Sensor selecionado'
                                          : 'Sensores selecionados',
                                    ),
                                    _Indicador(
                                      valor:
                                          '${central.prontos}/${central.selecionados}',
                                      rotulo: 'Prontos para medir',
                                    ),
                                  ],
                          ),
                          if (escolhidos.isNotEmpty) ...[
                            const SizedBox(height: 18),
                            Wrap(
                              spacing: 8,
                              runSpacing: 6,
                              children: [
                                for (final id in escolhidos)
                                  Chip(
                                    avatar: Icon(
                                      central.conferir(id).pronto
                                          ? Icons.check_circle_outline
                                          : Icons.schedule,
                                      size: 18,
                                    ),
                                    label: Text(id.titulo),
                                  ),
                              ],
                            ),
                          ],
                          const SizedBox(height: 20),
                          if (central.gravando) ...[
                            FilledButton.icon(
                              onPressed: central.ocupado
                                  ? null
                                  : () => _executar(
                                      context,
                                      central.encerrarColeta,
                                    ),
                              icon: const Icon(Icons.stop_rounded),
                              label: Text(
                                central.ocupado
                                    ? 'Salvando…'
                                    : 'Encerrar coleta',
                              ),
                            ),
                            const SizedBox(height: 8),
                            OutlinedButton.icon(
                              onPressed: abrirSensores,
                              icon: const Icon(Icons.sensors),
                              label: const Text('Abrir sensores'),
                            ),
                          ] else ...[
                            FilledButton.icon(
                              onPressed: abrirSensores,
                              icon: const Icon(Icons.tune_rounded),
                              label: const Text('Selecionar sensores'),
                            ),
                            const SizedBox(height: 8),
                            OutlinedButton.icon(
                              onPressed: central.podeIniciar
                                  ? () => _executar(
                                      context,
                                      central.iniciarColeta,
                                    )
                                  : null,
                              icon: const Icon(Icons.play_arrow_rounded),
                              label: Text(
                                central.ocupado
                                    ? 'Preparando…'
                                    : 'Iniciar coleta',
                              ),
                            ),
                            const SizedBox(height: 10),
                            Text(
                              central.selecao.carregando
                                  ? 'Carregando sua seleção de sensores…'
                                  : central.selecionados == 0
                                  ? 'Selecione pelo menos um sensor para começar.'
                                  : central.prontos < central.selecionados
                                  ? 'Confira os sensores selecionados: ainda há '
                                        'leituras indisponíveis.'
                                  : 'Os sensores selecionados estão prontos para a coleta.',
                              style: tema.textTheme.bodyMedium,
                            ),
                          ],
                        ],
                      ),
                    ),
                  ),
                  if (central.mensagem != null) ...[
                    const SizedBox(height: 16),
                    Semantics(
                      liveRegion: true,
                      child: Card.filled(
                        margin: EdgeInsets.zero,
                        child: Padding(
                          padding: const EdgeInsets.all(16),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Icon(Icons.info_outline, size: 22),
                              const SizedBox(width: 12),
                              Expanded(child: Text(central.mensagem!)),
                            ],
                          ),
                        ),
                      ),
                    ),
                  ],
                  const SizedBox(height: 28),
                  Text('Seu trabalho', style: tema.textTheme.titleMedium),
                  const SizedBox(height: 12),
                  Card.outlined(
                    margin: EdgeInsets.zero,
                    child: Column(
                      children: [
                        ListTile(
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 20,
                            vertical: 6,
                          ),
                          leading: const Icon(Icons.folder_outlined),
                          title: const Text('Minhas coletas'),
                          subtitle: const Text(
                            'Consulte os registros salvos no celular',
                          ),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: abrirSessoes,
                        ),
                        const Divider(height: 1, indent: 20, endIndent: 20),
                        ListTile(
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 20,
                            vertical: 6,
                          ),
                          leading: const Icon(Icons.map_outlined),
                          title: const Text('Mapa'),
                          subtitle: const Text(
                            'Veja sua posição e o percurso da coleta',
                          ),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: abrirMapa,
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      );
    },
  );
}

class _Indicador extends StatelessWidget {
  final String valor;
  final String rotulo;
  const _Indicador({required this.valor, required this.rotulo});

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        valor,
        style: Theme.of(context).textTheme.headlineMedium
            ?.copyWith(fontWeight: FontWeight.w600),
      ),
      const SizedBox(height: 4),
      Text(rotulo, style: Theme.of(context).textTheme.bodyMedium),
    ],
  );
}

String _duracao(Duration tempo) {
  final horas = tempo.inHours.toString().padLeft(2, '0');
  final minutos = tempo.inMinutes.remainder(60).toString().padLeft(2, '0');
  final segundos = tempo.inSeconds.remainder(60).toString().padLeft(2, '0');
  return '$horas:$minutos:$segundos';
}

Future<void> _executar(
  BuildContext context,
  Future<void> Function() acao,
) async {
  try {
    await acao();
  } catch (_) {
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text(
          'Não foi possível concluir a ação. Confira os sensores e tente novamente.',
        ),
      ),
    );
  }
}

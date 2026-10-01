import 'package:flutter/material.dart';

import '../coleta/central_sensores.dart';
import '../modelo/sensores.dart';

/// Seleção e conferência ao vivo, antes e durante a coleta.
class TelaSensores extends StatelessWidget {
  final CentralSensores central;
  final VoidCallback abrirDecibelimetro;
  final VoidCallback abrirAjustes;

  const TelaSensores({
    super.key,
    required this.central,
    required this.abrirDecibelimetro,
    required this.abrirAjustes,
  });

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: central,
    builder: (context, _) {
      final tema = Theme.of(context);
      final bloqueada =
          central.gravando ||
          central.ocupado ||
          central.selecao.bloqueada ||
          central.selecao.carregando ||
          central.selecao.salvando;
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
                      Expanded(
                        child: Text(
                          'Sensores',
                          style: tema.textTheme.headlineMedium,
                        ),
                      ),
                      IconButton(
                        tooltip: 'Ajustes da estação',
                        onPressed: abrirAjustes,
                        icon: const Icon(Icons.settings_outlined),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    central.gravando
                        ? 'Acompanhe os valores e a disponibilidade dos sensores '
                              'da coleta em andamento.'
                        : 'Escolha os sensores da próxima coleta e confira se '
                              'as leituras estão chegando.',
                    style: tema.textTheme.bodyLarge,
                  ),
                  const SizedBox(height: 16),
                  Wrap(
                    spacing: 10,
                    runSpacing: 8,
                    children: [
                      Chip(
                        avatar: const Icon(Icons.checklist_rounded, size: 18),
                        label: Text(
                          central.selecao.carregando
                              ? 'Carregando seleção…'
                              : '${central.selecionados} selecionados',
                        ),
                      ),
                      Chip(
                        avatar: Icon(
                          central.selecionados > 0 &&
                                  central.prontos == central.selecionados
                              ? Icons.check_circle_outline
                              : Icons.schedule,
                          size: 18,
                        ),
                        label: Text(
                          '${central.prontos}/${central.selecionados} prontos',
                        ),
                      ),
                      if (central.selecao.salvando)
                        const Chip(label: Text('Salvando seleção…')),
                    ],
                  ),
                  if (central.gravando) ...[
                    const SizedBox(height: 12),
                    const _Aviso(
                      texto:
                          'A seleção fica bloqueada durante a coleta. '
                          'Encerre a coleta para trocar os sensores.',
                      icone: Icons.lock_outline,
                    ),
                  ],
                  if (central.selecao.erro != null) ...[
                    const SizedBox(height: 12),
                    _Aviso(texto: central.selecao.erro!),
                  ],
                  if (central.mensagem != null) ...[
                    const SizedBox(height: 12),
                    _Aviso(texto: central.mensagem!),
                  ],
                  const SizedBox(height: 28),
                  const _Grupo(
                    titulo: 'No celular',
                    detalhe: 'Funcionam sem conexão com a estação.',
                    icone: Icons.smartphone_outlined,
                  ),
                  for (final id in SensorId.values.where((id) => !id.daEstacao))
                    Padding(
                      padding: const EdgeInsets.only(top: 12),
                      child: _CartaoSensor(
                        id: id,
                        central: central,
                        bloqueada: bloqueada,
                        abrirDecibelimetro: abrirDecibelimetro,
                      ),
                    ),
                  const SizedBox(height: 28),
                  _Grupo(
                    titulo: 'Na estação',
                    detalhe: central.ble.conectado
                        ? 'Estação conectada por Bluetooth. Confira cada leitura.'
                        : 'Conecte a estação por Bluetooth para receber as leituras.',
                    icone: Icons.sensors,
                  ),
                  for (final id in SensorId.values.where((id) => id.daEstacao))
                    Padding(
                      padding: const EdgeInsets.only(top: 12),
                      child: _CartaoSensor(
                        id: id,
                        central: central,
                        bloqueada: bloqueada,
                        abrirDecibelimetro: abrirDecibelimetro,
                      ),
                    ),
                  const SizedBox(height: 24),
                  Card.filled(
                    margin: EdgeInsets.zero,
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                            central.gravando
                                ? 'Coleta em andamento'
                                : 'Tudo pronto para começar?',
                            style: tema.textTheme.titleMedium,
                          ),
                          const SizedBox(height: 8),
                          Text(
                            central.gravando
                                ? '${central.registros} registros salvos no celular.'
                                : central.selecionados == 0
                                ? 'Selecione pelo menos um sensor.'
                                : central.prontos < central.selecionados
                                ? 'Aguarde as leituras e confira os sensores '
                                      'selecionados antes de iniciar.'
                                : 'Todos os sensores selecionados estão prontos.',
                          ),
                          const SizedBox(height: 16),
                          FilledButton.icon(
                            onPressed: central.gravando
                                ? central.ocupado
                                      ? null
                                      : () => _executar(
                                          context,
                                          central.encerrarColeta,
                                        )
                                : central.podeIniciar
                                ? () =>
                                      _executar(context, central.iniciarColeta)
                                : null,
                            icon: Icon(
                              central.gravando
                                  ? Icons.stop_rounded
                                  : Icons.play_arrow_rounded,
                            ),
                            label: Text(
                              central.ocupado
                                  ? 'Aguarde…'
                                  : central.gravando
                                  ? 'Encerrar coleta'
                                  : 'Iniciar coleta',
                            ),
                          ),
                        ],
                      ),
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

class _Grupo extends StatelessWidget {
  final String titulo;
  final String detalhe;
  final IconData icone;
  const _Grupo({
    required this.titulo,
    required this.detalhe,
    required this.icone,
  });

  @override
  Widget build(BuildContext context) => Row(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Icon(icone, color: Theme.of(context).colorScheme.primary),
      const SizedBox(width: 12),
      Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(titulo, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 4),
            Text(
              detalhe,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
    ],
  );
}

class _CartaoSensor extends StatelessWidget {
  final SensorId id;
  final CentralSensores central;
  final bool bloqueada;
  final VoidCallback abrirDecibelimetro;

  const _CartaoSensor({
    required this.id,
    required this.central,
    required this.bloqueada,
    required this.abrirDecibelimetro,
  });

  @override
  Widget build(BuildContext context) {
    final c = central.conferir(id);
    final tema = Theme.of(context);
    final cores = tema.colorScheme;
    final corEstado = c.pronto
        ? cores.primary
        : c.selecionado
        ? cores.tertiary
        : cores.onSurfaceVariant;
    return Card.outlined(
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(20),
        side: BorderSide(
          color: c.selecionado ? cores.primary : cores.outlineVariant,
          width: c.selecionado ? 1.5 : 1,
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Icon(_icone(id), color: cores.primary),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(id.titulo, style: tema.textTheme.titleMedium),
                      const SizedBox(height: 4),
                      Text(
                        id.origem,
                        style: tema.textTheme.bodySmall?.copyWith(
                          color: cores.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Semantics(
                  label: 'Incluir ${id.titulo} na coleta',
                  child: Switch(
                    value: c.selecionado,
                    onChanged: bloqueada
                        ? null
                        : (valor) => _executar(
                            context,
                            () => central.alterar(id, valor),
                          ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text(id.descricao, style: tema.textTheme.bodyMedium),
            const SizedBox(height: 16),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  c.pronto ? Icons.check_circle_outline : Icons.info_outline,
                  color: corEstado,
                  size: 20,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    c.estado,
                    style: tema.textTheme.labelLarge?.copyWith(
                      color: corEstado,
                    ),
                  ),
                ),
              ],
            ),
            if (c.valor != null) ...[
              const SizedBox(height: 10),
              Text(
                c.valor!,
                style: tema.textTheme.titleLarge?.copyWith(
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
            if (c.detalhe.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                c.detalhe,
                style: tema.textTheme.bodyMedium?.copyWith(
                  color: cores.onSurfaceVariant,
                ),
              ),
            ],
            if (c.atualizadoEm != null) ...[
              const SizedBox(height: 6),
              Text(
                _atualizacao(c.atualizadoEm!),
                style: tema.textTheme.bodySmall?.copyWith(
                  color: cores.onSurfaceVariant,
                ),
              ),
            ],
            if (id == SensorId.gpsCelular && c.selecionado && !c.pronto) ...[
              const SizedBox(height: 16),
              Align(
                alignment: Alignment.centerLeft,
                child: OutlinedButton.icon(
                  onPressed: central.ocupado
                      ? null
                      : () => _executar(context, central.testarGps),
                  icon: const Icon(Icons.my_location),
                  label: const Text('Tentar localização'),
                ),
              ),
            ],
            if (id == SensorId.somCelular && c.selecionado) ...[
              const SizedBox(height: 16),
              Wrap(
                spacing: 10,
                runSpacing: 8,
                children: [
                  OutlinedButton.icon(
                    onPressed:
                        central.gravando ||
                            central.ocupado ||
                            central.som.iniciando
                        ? null
                        : () => _executar(
                            context,
                            central.som.medindo
                                ? central.pararTesteMicrofone
                                : central.testarMicrofone,
                          ),
                    icon: Icon(
                      central.som.medindo ? Icons.stop_rounded : Icons.mic_none,
                    ),
                    label: Text(
                      central.som.iniciando
                          ? 'Iniciando microfone…'
                          : central.som.medindo
                          ? 'Parar teste'
                          : 'Testar microfone',
                    ),
                  ),
                  TextButton.icon(
                    onPressed: abrirDecibelimetro,
                    icon: const Icon(Icons.graphic_eq),
                    label: const Text('Abrir decibelímetro'),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _Aviso extends StatelessWidget {
  final String texto;
  final IconData icone;
  const _Aviso({required this.texto, this.icone = Icons.info_outline});

  @override
  Widget build(BuildContext context) => Semantics(
    liveRegion: true,
    child: Card.filled(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icone, size: 22),
            const SizedBox(width: 12),
            Expanded(child: Text(texto)),
          ],
        ),
      ),
    ),
  );
}

IconData _icone(SensorId id) => switch (id) {
  SensorId.gpsCelular => Icons.my_location,
  SensorId.somCelular => Icons.mic_none,
  SensorId.ambienteRs485 => Icons.device_thermostat,
  SensorId.luminosidadeGy302 => Icons.light_mode_outlined,
  SensorId.temperaturaKy028 => Icons.thermostat_outlined,
  SensorId.temperaturaKy013 => Icons.thermostat_outlined,
  SensorId.ruidoKy038 => Icons.graphic_eq,
};

String _atualizacao(DateTime instante) {
  final segundos = DateTime.now().difference(instante).inSeconds;
  if (segundos <= 1) return 'Última leitura agora';
  if (segundos < 60) return 'Última leitura há $segundos s';
  final minutos = segundos ~/ 60;
  if (minutos < 60) return 'Última leitura há $minutos min';
  final hora = instante.hour.toString().padLeft(2, '0');
  final minuto = instante.minute.toString().padLeft(2, '0');
  return 'Última leitura às $hora:$minuto';
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
          'Não foi possível concluir a ação. Confira o estado dos sensores e tente novamente.',
        ),
      ),
    );
  }
}

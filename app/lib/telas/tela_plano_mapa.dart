import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../modelo/plano.dart';
import '../rede/cliente_planos.dart';
import '../rede/plano_atual.dart';
import 'widgets/area_plano.dart';

/// Tela cheia de UM plano - area pintada solida no mapa, igual a visao de
/// plano do painel da base (aba "Planejar area"), com o botao de escolher
/// esse plano embaixo.
class TelaPlanoMapa extends StatefulWidget {
  final int planoId;
  final String nomeInicial;
  final PlanoAtual planos;
  const TelaPlanoMapa({
    super.key,
    required this.planoId,
    required this.nomeInicial,
    required this.planos,
  });

  @override
  State<TelaPlanoMapa> createState() => _TelaPlanoMapaState();
}

class _TelaPlanoMapaState extends State<TelaPlanoMapa> {
  PlanoDetalhe? _detalhe;
  String? _erro;
  bool _escolhendo = false;

  @override
  void initState() {
    super.initState();
    _carregar();
  }

  Future<void> _carregar() async {
    setState(() => _erro = null);
    try {
      final d = await ClientePlanos(base: widget.planos.base.host)
          .obter(widget.planoId);
      if (!mounted) return;
      setState(() => _detalhe = d);
    } catch (e) {
      if (!mounted) return;
      setState(() => _erro = 'nao consegui carregar esta area ($e)');
    }
  }

  Future<void> _escolher() async {
    setState(() => _escolhendo = true);
    final ok = await widget.planos.escolher(widget.planoId);
    if (!mounted) return;
    setState(() {
      _escolhendo = false;
      if (ok) _detalhe = PlanoDetalhe(
          id: _detalhe!.id,
          nome: _detalhe!.nome,
          tracos: _detalhe!.tracos,
          criadoEm: _detalhe!.criadoEm,
          ativo: true);
    });
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(ok
          ? '"${widget.nomeInicial}" e o plano ativo agora'
          : 'nao consegui falar com a base'),
      backgroundColor: ok ? null : Theme.of(context).colorScheme.error,
    ));
  }

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;
    return Scaffold(
      appBar: AppBar(title: Text(widget.nomeInicial)),
      body: _erro != null
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Icon(Icons.cloud_off, size: 40, color: fraco),
                  const SizedBox(height: 12),
                  Text(_erro!, textAlign: TextAlign.center),
                  const SizedBox(height: 12),
                  OutlinedButton(onPressed: _carregar, child: const Text('Tentar de novo')),
                ]),
              ),
            )
          : _detalhe == null
              ? const Center(child: CircularProgressIndicator())
              : _MapaDoPlano(detalhe: _detalhe!),
      bottomNavigationBar: _detalhe == null
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: _detalhe!.ativo
                    ? const Row(children: [
                        Icon(Icons.check_circle, color: Color(0xFF3FB950)),
                        SizedBox(width: 8),
                        Text('Este e o plano ativo',
                            style: TextStyle(fontWeight: FontWeight.w600)),
                      ])
                    : FilledButton.icon(
                        onPressed: _escolhendo ? null : _escolher,
                        icon: _escolhendo
                            ? const SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.check),
                        label: const Text('Fazer este plano'),
                      ),
              ),
            ),
    );
  }
}

class _MapaDoPlano extends StatelessWidget {
  final PlanoDetalhe detalhe;
  const _MapaDoPlano({required this.detalhe});

  @override
  Widget build(BuildContext context) {
    final todos = [
      for (final t in detalhe.tracos) for (final p in t.pontos) LatLng(p.$1, p.$2)
    ];
    if (todos.isEmpty) {
      return const Center(child: Text('Esta area nao tem nada pintado.'));
    }
    final centro = todos[todos.length ~/ 2];
    return FlutterMap(
      options: MapOptions(
        initialCenter: centro,
        initialZoom: 17,
        initialCameraFit: CameraFit.coordinates(
          coordinates: todos,
          padding: const EdgeInsets.all(40),
        ),
      ),
      children: [
        TileLayer(
          urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
          userAgentPackageName: 'br.estacao.estacao_movel',
          maxNativeZoom: 19,
        ),
        // 0.6, nao 1: o painel da base consegue ficar 100% solido e ainda
        // mostrar rua/predio por baixo porque usa mix-blend-mode:multiply no
        // CSS - o Flutter nao tem um jeito simples equivalente para uma
        // camada de mapa (exigiria capturar os tiles num buffer a parte).
        // 0.6 e o meio-termo: area bem visivel, mapa ainda legivel embaixo.
        AreaPlanoOverlay(tracos: detalhe.tracos, opacidade: 0.6),
      ],
    );
  }
}

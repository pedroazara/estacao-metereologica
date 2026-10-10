import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../ble/estacao_ble.dart';
import '../rede/plano_atual.dart';
import 'widgets/area_plano.dart';

/// Mapa de verdade (OpenStreetMap) com a sua posicao e a trajetoria da medicao.
///
/// A trajetoria so aparece quando voce esta GRAVANDO - e o caminho da medida,
/// nao do trajeto ate comecar. A posicao vem do GPS do proprio telefone.
///
/// Quando ha um plano ATIVO (aba Planos), a area pintada dele aparece por
/// baixo do trajeto - guia visual de "e aqui que preciso andar", mesmo dado
/// que o painel da base mostra, so que no proprio mapa de quem esta em campo.
class TelaMapa extends StatefulWidget {
  final EstacaoBle ble;
  final PlanoAtual? planos;
  const TelaMapa({super.key, required this.ble, this.planos});

  @override
  State<TelaMapa> createState() => _TelaMapaState();
}

class _TelaMapaState extends State<TelaMapa> {
  final _mapa = MapController();
  bool _seguir = true;

  @override
  void initState() {
    super.initState();
    widget.ble.addListener(_aoAtualizar);
    widget.planos?.addListener(_aoAtualizar);
  }

  @override
  void dispose() {
    widget.ble.removeListener(_aoAtualizar);
    widget.planos?.removeListener(_aoAtualizar);
    super.dispose();
  }

  void _aoAtualizar() {
    if (!mounted) return;
    setState(() {});
    if (_seguir) {
      final la = widget.ble.telLat, lo = widget.ble.telLon;
      if (la != null && lo != null) {
        _mapa.move(LatLng(la, lo), _mapa.camera.zoom);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final ble = widget.ble;
    final la = ble.telLat, lo = ble.telLon;
    final fraco = Theme.of(context).colorScheme.outline;

    if (la == null || lo == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(Icons.my_location, size: 48, color: fraco),
            const SizedBox(height: 14),
            Text(ble.gps.mensagem,
                textAlign: TextAlign.center, style: TextStyle(color: fraco)),
            const SizedBox(height: 8),
            Text(
              'Aguardando o GPS do telefone.\n'
              'Confira se a localizacao esta ligada e a permissao concedida.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 12, color: fraco),
            ),
          ]),
        ),
      );
    }

    final aqui = LatLng(la, lo);
    final trilha =
        ble.trilha.map((p) => LatLng(p[0], p[1])).toList(growable: false);

    return Stack(children: [
      FlutterMap(
        mapController: _mapa,
        options: MapOptions(
          initialCenter: aqui,
          initialZoom: 17,
          onPositionChanged: (pos, gesto) {
            if (gesto && _seguir) setState(() => _seguir = false);
          },
        ),
        children: [
          TileLayer(
            urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
            userAgentPackageName: 'br.estacao.estacao_movel',
            maxNativeZoom: 19,
          ),
          // Area do plano ativo, se tiver - por baixo da trilha e da posicao,
          // so um guia de fundo ("e por aqui").
          if (widget.planos?.selecionado != null)
            AreaPlanoOverlay(tracos: widget.planos!.selecionado!.tracos),
          if (trilha.length > 1)
            PolylineLayer(polylines: [
              Polyline(points: trilha, strokeWidth: 4, color: const Color(0xFF3FB950)),
            ]),
          MarkerLayer(markers: [
            Marker(
              point: aqui,
              width: 26,
              height: 26,
              child: Container(
                decoration: BoxDecoration(
                  color: const Color(0xFF3FB950),
                  shape: BoxShape.circle,
                  border: Border.all(color: Colors.white, width: 3),
                  boxShadow: const [
                    BoxShadow(color: Color(0x883FB950), blurRadius: 8, spreadRadius: 2)
                  ],
                ),
              ),
            ),
          ]),
        ],
      ),
      Positioned(
        bottom: 2,
        right: 4,
        child: Container(
          color: const Color(0x99000000),
          padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
          child: const Text('© OpenStreetMap',
              style: TextStyle(fontSize: 9, color: Colors.white70)),
        ),
      ),
      Positioned(top: 12, left: 12, child: _Resumo(ble: ble)),
      if (!_seguir)
        Positioned(
          bottom: 16,
          right: 12,
          child: FloatingActionButton.small(
            onPressed: () {
              setState(() => _seguir = true);
              _mapa.move(aqui, _mapa.camera.zoom);
            },
            child: const Icon(Icons.my_location),
          ),
        ),
    ]);
  }
}

class _Resumo extends StatelessWidget {
  final EstacaoBle ble;
  const _Resumo({required this.ble});

  @override
  Widget build(BuildContext context) {
    final prec = ble.precisaoM;
    final a = ble.ambiente;
    final gravando = ble.gravador.gravando;
    Widget linha(String r, String v, [Color? cor]) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 2),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Text('$r ',
                style: const TextStyle(fontSize: 10, color: Color(0xFF8B949E))),
            Text(v,
                style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    fontFamily: 'monospace',
                    color: cor ?? Colors.white)),
          ]),
        );
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xE6141B22),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFF232D38)),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(gravando ? Icons.fiber_manual_record : Icons.circle_outlined,
              size: 10,
              color: gravando ? const Color(0xFFE04B4B) : const Color(0xFF7D8794)),
          const SizedBox(width: 6),
          Text(gravando ? 'gravando trajetoria' : 'nao gravando',
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
        ]),
        const SizedBox(height: 6),
        linha('precisao GPS', prec == null ? '--' : '±${prec.toStringAsFixed(0)} m'),
        linha('temperatura',
            a == null ? '--' : '${a.temperatura.toStringAsFixed(1)} °C',
            const Color(0xFFFF7A5E)),
        linha('umidade', a == null ? '--' : '${a.umidade.toStringAsFixed(0)} %'),
        linha('pontos', '${ble.trilha.length}'),
      ]),
    );
  }
}

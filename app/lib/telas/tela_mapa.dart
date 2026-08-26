import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../ble/estacao_ble.dart';

/// Mapa de verdade (OpenStreetMap) com a sua localizacao e a trajetoria.
///
/// A posicao vem do GPS do TELEFONE (decisao de 25/08/2026). Desenha duas
/// linhas: o GPS cru do telefone e a PREVIA do Kalman por cima. Os tiles vem da
/// internet quando ha rede; offline, o mapa usa o que ja estiver em cache e as
/// linhas continuam aparecendo sobre o fundo.
class TelaMapa extends StatefulWidget {
  final EstacaoBle ble;
  const TelaMapa({super.key, required this.ble});

  @override
  State<TelaMapa> createState() => _TelaMapaState();
}

class _TelaMapaState extends State<TelaMapa> {
  final _mapa = MapController();
  bool _seguir = true; // a camera acompanha a posicao ate o usuario arrastar

  @override
  void initState() {
    super.initState();
    widget.ble.addListener(_aoAtualizar);
  }

  @override
  void dispose() {
    widget.ble.removeListener(_aoAtualizar);
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
    final cru = ble.fusao.trilhaCru.map((p) => LatLng(p[0], p[1])).toList();
    final kf = ble.fusao.trilhaFiltrada.map((p) => LatLng(p[0], p[1])).toList();

    return Stack(children: [
      FlutterMap(
        mapController: _mapa,
        options: MapOptions(
          initialCenter: aqui,
          initialZoom: 17,
          onPositionChanged: (pos, gesto) {
            // Arrastar o mapa desliga o seguimento; senao a proxima posicao
            // puxaria a vista de volta e nao daria para olhar em volta.
            if (gesto && _seguir) setState(() => _seguir = false);
          },
        ),
        children: [
          TileLayer(
            urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
            userAgentPackageName: 'br.estacao.estacao_movel',
            maxNativeZoom: 19,
          ),
          if (cru.length > 1)
            PolylineLayer(polylines: [
              Polyline(points: cru, strokeWidth: 3, color: const Color(0x8858A6FF)),
            ]),
          if (kf.length > 1)
            PolylineLayer(polylines: [
              Polyline(points: kf, strokeWidth: 4, color: const Color(0xFF3FB950)),
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
      // Atribuicao do OSM (exigida pela licenca dos tiles).
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
      // Painel de resumo flutuante.
      Positioned(
        top: 12,
        left: 12,
        child: _Resumo(ble: ble),
      ),
      // Botao para religar o seguimento depois de arrastar.
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
    final f = ble.fusao;
    final prec = ble.gps.precisaoM;
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
        Row(children: [
          const Icon(Icons.circle, size: 8, color: Color(0xFF3FB950)),
          const SizedBox(width: 6),
          Text(f.parado ? 'parado' : 'em movimento',
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
        ]),
        const SizedBox(height: 6),
        linha('velocidade', '${f.velocidadeKmh.toStringAsFixed(1)} km/h'),
        linha('incerteza Kalman', '±${f.sigmaPos.toStringAsFixed(1)} m',
            const Color(0xFF3FB950)),
        linha('precisao GPS', prec == null ? '--' : '±${prec.toStringAsFixed(0)} m'),
        linha('pontos', '${f.trilhaCru.length}'),
      ]),
    );
  }
}

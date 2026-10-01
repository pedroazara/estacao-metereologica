import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../../modelo/plano.dart';

/// Desenha a area pintada de um plano como uma mancha SOLIDA, do mesmo jeito
/// que o painel da base faz (ver planoPane em servidor_telemetria.py):
/// cada circulo OPACO, e a translucidez fica so na CAMADA INTEIRA (aqui, via
/// [Opacity] em volta de tudo), nao em cada circulo.
///
/// Por que isso importa: com opacidade por circulo, pinceladas que se
/// cruzam ficam mais escuras onde se sobrepoem - uma mancha esverdeada
/// desigual, "tudo sobreposto". Com opacidade so na camada, nao importa
/// quantos circulos se sobreponham por baixo: o resultado visual e sempre
/// a MESMA cor solida.
class AreaPlanoOverlay extends StatelessWidget {
  final List<Traco> tracos;
  final double opacidade;
  const AreaPlanoOverlay(
      {super.key, required this.tracos, this.opacidade = 0.45});

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: opacidade,
      child: CircleLayer(
        circles: [
          for (final t in tracos)
            for (final (lat, lon) in t.pontos)
              CircleMarker(
                point: LatLng(lat, lon),
                radius: t.raioM,
                useRadiusInMeter: true,
                color: const Color(0xFF2F9E57), // opaco - a mistura fica na Opacity
              ),
        ],
      ),
    );
  }
}

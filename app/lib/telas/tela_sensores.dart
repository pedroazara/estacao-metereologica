import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../ble/estacao_ble.dart';
import '../modelo/leituras.dart';

/// Acelerometro, giroscopio e atitude — o mesmo conjunto do painel.py, em
/// canvas. Le a ultima amostra do ultimo lote de IMU que chegou por BLE.
class TelaSensores extends StatelessWidget {
  final EstacaoBle ble;
  const TelaSensores({super.key, required this.ble});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: ble,
      builder: (context, _) {
        if (!ble.conectado) {
          return _centro(context, 'Conecte na estacao para ver os sensores.');
        }
        final lote = ble.ultimoLote;
        final a = (lote?.amostras.isNotEmpty ?? false) ? lote!.amostras.last : null;
        if (a == null) {
          return _centro(context, 'Aguardando amostras de IMU.');
        }
        return ListView(padding: const EdgeInsets.all(16), children: [
          _Cartao(
            titulo: 'Circulo de forca G',
            filho: AspectRatio(
              aspectRatio: 1,
              child: CustomPaint(painter: _GmetroPainter(a)),
            ),
            rodape: Row(mainAxisAlignment: MainAxisAlignment.spaceAround, children: [
              _kv('|a|', '${a.moduloG.toStringAsFixed(3)} g'),
              _kv('ax', a.ax.toStringAsFixed(3)),
              _kv('ay', a.ay.toStringAsFixed(3)),
              _kv('az', a.az.toStringAsFixed(3)),
            ]),
          ),
          const SizedBox(height: 12),
          _Cartao(
            titulo: 'Atitude',
            filho: SizedBox(
              height: 170,
              child: CustomPaint(painter: _HorizontePainter(a.roll, a.pitch)),
            ),
            rodape: Row(mainAxisAlignment: MainAxisAlignment.spaceAround, children: [
              _kv('roll', '${a.roll.toStringAsFixed(1)}°'),
              _kv('pitch', '${a.pitch.toStringAsFixed(1)}°'),
              _kv('yaw', '${a.yaw.toStringAsFixed(1)}°'),
            ]),
          ),
          const SizedBox(height: 12),
          _Cartao(
            titulo: 'Giroscopio (graus/s)',
            filho: Column(children: [
              _BarraGiro(rotulo: 'X', valor: a.gx, cor: const Color(0xFFF85149)),
              _BarraGiro(rotulo: 'Y', valor: a.gy, cor: const Color(0xFF3FB950)),
              _BarraGiro(rotulo: 'Z', valor: a.gz, cor: const Color(0xFF58A6FF)),
            ]),
            rodape: Text(
              ble.fusao.giroRmsDps > 0
                  ? 'RMS ${ble.fusao.giroRmsDps.toStringAsFixed(1)} °/s   ·   '
                      'ruido |a| ${ble.fusao.desvioAccG.toStringAsFixed(3)} g'
                  : '',
              style: TextStyle(
                  fontSize: 11, color: Theme.of(context).colorScheme.outline),
            ),
          ),
        ]);
      },
    );
  }

  static Widget _kv(String r, String v) => Column(children: [
        Text(v,
            style: const TextStyle(
                fontFamily: 'monospace',
                fontSize: 14,
                fontWeight: FontWeight.w600)),
        Text(r, style: const TextStyle(fontSize: 10, color: Color(0xFF7D8794))),
      ]);

  static Widget _centro(BuildContext c, String t) {
    final fraco = Theme.of(c).colorScheme.outline;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          Icon(Icons.speed, size: 48, color: fraco),
          const SizedBox(height: 14),
          Text(t, textAlign: TextAlign.center, style: TextStyle(color: fraco)),
        ]),
      ),
    );
  }
}

class _Cartao extends StatelessWidget {
  final String titulo;
  final Widget filho, rodape;
  const _Cartao(
      {required this.titulo, required this.filho, required this.rodape});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(titulo.toUpperCase(),
              style: TextStyle(
                  fontSize: 10,
                  letterSpacing: 1.2,
                  color: Theme.of(context).colorScheme.outline)),
          const SizedBox(height: 12),
          filho,
          const SizedBox(height: 12),
          rodape,
        ]),
      ),
    );
  }
}

/// Ponto de forca G no plano frente/lado, com aneis de escala. Escala fixa 2 g.
class _GmetroPainter extends CustomPainter {
  final AmostraImu a;
  static const double escala = 2.0;
  _GmetroPainter(this.a);

  @override
  void paint(Canvas canvas, Size size) {
    final cx = size.width / 2, cy = size.height / 2;
    final raio = math.min(cx, cy) - 20;
    final grade = Paint()
      ..color = const Color(0xFF232D38)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    for (var i = 1; i <= 4; i++) {
      canvas.drawCircle(Offset(cx, cy), raio * i / 4, grade);
    }
    canvas.drawLine(Offset(cx - raio, cy), Offset(cx + raio, cy), grade);
    canvas.drawLine(Offset(cx, cy - raio), Offset(cx, cy + raio), grade);

    _txt(canvas, 'FRENTE', Offset(cx - 22, cy - raio - 16));
    _txt(canvas, 'TRAS', Offset(cx - 14, cy + raio + 4));
    _txt(canvas, 'DIR', Offset(cx + raio + 4, cy - 6));
    _txt(canvas, 'ESQ', Offset(cx - raio - 30, cy - 6));

    // ay = lateral (x da tela), ax = longitudinal (y da tela, frente para cima)
    final px = cx + (a.ay / escala).clamp(-1.0, 1.0) * raio;
    final py = cy - (a.ax / escala).clamp(-1.0, 1.0) * raio;
    canvas.drawLine(
        Offset(cx, cy),
        Offset(px, py),
        Paint()
          ..color = const Color(0x5958A6FF)
          ..strokeWidth = 1);
    canvas.drawCircle(Offset(px, py), 7, Paint()..color = const Color(0xFF58A6FF));
    canvas.drawCircle(
        Offset(px, py),
        7,
        Paint()
          ..color = Colors.white
          ..strokeWidth = 2
          ..style = PaintingStyle.stroke);
  }

  void _txt(Canvas c, String s, Offset o) {
    final tp = TextPainter(
      text: TextSpan(
          text: s,
          style: const TextStyle(color: Color(0xFF7D8794), fontSize: 9)),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(c, o);
  }

  @override
  bool shouldRepaint(covariant _GmetroPainter old) =>
      old.a.ax != a.ax || old.a.ay != a.ay;
}

/// Horizonte artificial: ceu/solo girando por roll, deslocando por pitch.
class _HorizontePainter extends CustomPainter {
  final double roll, pitch;
  _HorizontePainter(this.roll, this.pitch);

  @override
  void paint(Canvas canvas, Size size) {
    final cx = size.width / 2, cy = size.height / 2;
    final raio = math.min(cx, cy) - 6;
    canvas.save();
    canvas.clipPath(Path()..addOval(Rect.fromCircle(center: Offset(cx, cy), radius: raio)));
    canvas.translate(cx, cy);
    canvas.rotate(-roll * math.pi / 180.0);
    final desl = pitch * 2.2;

    canvas.drawRect(Rect.fromLTRB(-300, -300 + desl, 300, desl),
        Paint()..color = const Color(0xFF2B6CA3)); // ceu
    canvas.drawRect(Rect.fromLTRB(-300, desl, 300, 300 + desl),
        Paint()..color = const Color(0xFF6B4A2F)); // solo
    canvas.drawLine(Offset(-300, desl), Offset(300, desl),
        Paint()..color = Colors.white..strokeWidth = 2);
    canvas.restore();

    canvas.drawCircle(
        Offset(cx, cy),
        raio,
        Paint()
          ..color = const Color(0xFF232D38)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2);
    // Marca fixa do "aviao" no centro.
    final amarelo = Paint()
      ..color = const Color(0xFFD29922)
      ..strokeWidth = 3;
    canvas.drawLine(Offset(cx - 20, cy), Offset(cx - 6, cy), amarelo);
    canvas.drawLine(Offset(cx + 6, cy), Offset(cx + 20, cy), amarelo);
    canvas.drawCircle(Offset(cx, cy), 2, amarelo);
  }

  @override
  bool shouldRepaint(covariant _HorizontePainter old) =>
      old.roll != roll || old.pitch != pitch;
}

class _BarraGiro extends StatelessWidget {
  final String rotulo;
  final double valor;
  final Color cor;
  const _BarraGiro({required this.rotulo, required this.valor, required this.cor});

  static const double escala = 250.0; // °/s de fundo de escala

  @override
  Widget build(BuildContext context) {
    final f = (valor / escala).clamp(-1.0, 1.0);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(children: [
        SizedBox(width: 18, child: Text(rotulo, style: TextStyle(color: cor))),
        Expanded(
          child: LayoutBuilder(builder: (context, c) {
            final meio = c.maxWidth / 2;
            return Container(
              height: 20,
              decoration: BoxDecoration(
                color: const Color(0xFF0A0E13),
                borderRadius: BorderRadius.circular(5),
                border: Border.all(color: const Color(0xFF232D38)),
              ),
              child: Stack(children: [
                Positioned(
                  left: f >= 0 ? meio : meio + f * meio,
                  width: (f.abs() * meio).clamp(1.0, meio),
                  top: 2,
                  bottom: 2,
                  child: Container(
                    decoration: BoxDecoration(
                        color: cor, borderRadius: BorderRadius.circular(3)),
                  ),
                ),
                Positioned(
                    left: meio, top: 0, bottom: 0, child: Container(width: 1, color: const Color(0xFF39424E))),
              ]),
            );
          }),
        ),
        const SizedBox(width: 10),
        SizedBox(
          width: 56,
          child: Text(valor.toStringAsFixed(1),
              textAlign: TextAlign.right,
              style: const TextStyle(
                  fontFamily: 'monospace', fontWeight: FontWeight.w600)),
        ),
      ]),
    );
  }
}

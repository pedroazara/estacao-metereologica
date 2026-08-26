// Verificacao de fidelidade do porte: a MESMA sequencia deterministica roda no
// filtro Python (src/filtro_kalman.py) e aqui, e os resultados tem de bater.
//
// Os valores esperados foram gerados pelo Python de referencia — se este teste
// falhar depois de mexer no filtro Dart, foi o porte que divergiu da fonte, nao
// o contrario. Regerar o padrao-ouro: ver o comentario no fim do arquivo.

import 'package:estacao_movel/kalman/filtro_cv.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('FiltroCV reproduz o filtro Python amostra a amostra', () {
    final kf = FiltroCV();
    const lat0 = -21.2257, lon0 = -44.9774;
    kf.iniciar(lat0, lon0);
    var t = 0.0;
    const dt = 0.1;
    var lat = lat0;

    for (var i = 0; i < 200; i++) {
      t += dt;
      final andando = i >= 100;
      final az = 1.0 + (andando && i.isOdd ? 0.05 : 0.0);
      kf.prever(0.0, 0.0, az, 0.0, 0.0, dt);
      if (i % 10 == 0) {
        double vel = 0.0;
        if (andando) {
          lat += 1.4 * 10 * dt / 111320.0;
          vel = 5.04;
        }
        kf.atualizarMovimentoGps(t, lat, lon0, vel);
        kf.corrigirGps(lat, lon0, hdop: 1.0);
        kf.corrigirVelocidade(vel, 0.0);
      }
    }

    // Padrao-ouro do Python. Tolerancia apertada: e a mesma matematica escalar,
    // entao so a aritmetica de ponto flutuante separa os dois.
    final pos = kf.posicao;
    expect(pos[0], closeTo(-21.225578923, 1e-7));
    expect(pos[1], closeTo(-44.977400000, 1e-7));
    expect(kf.velocidadeKmh, closeTo(5.556017, 1e-4));
    expect(kf.sigmaPos, closeTo(2.178937, 1e-4));
    expect(kf.parado, isFalse);
    expect(kf.x[0], closeTo(13.528829, 1e-4));
    expect(kf.x[2], closeTo(1.543338, 1e-4));
  });

  test('parado zera a velocidade (ZUPT)', () {
    final kf = FiltroCV();
    kf.iniciar(-21.2257, -44.9774);
    var t = 0.0;
    // 40 s totalmente quieto: o detector precisa entrar em repouso e o ZUPT
    // precisa zerar qualquer velocidade residual.
    for (var i = 0; i < 400; i++) {
      t += 0.1;
      kf.prever(0.0, 0.0, 1.0, 0.0, 0.0, 0.1);
      if (i % 10 == 0) {
        kf.atualizarMovimentoGps(t, -21.2257, -44.9774, 0.0);
        kf.corrigirGps(-21.2257, -44.9774, hdop: 1.0);
        kf.corrigirVelocidade(0.0, 0.0);
      }
    }
    expect(kf.parado, isTrue);
    expect(kf.velocidadeKmh, closeTo(0.0, 0.01));
  });
}

// Regerar o padrao-ouro (rodar da raiz do repositorio):
//
//   ./.venv/bin/python3 -c "import sys; sys.path.insert(0,'src'); \
//     from filtro_kalman import FiltroCV; kf=FiltroCV(); \
//     ... (mesma sequencia acima) ...; print(kf.posicao, kf.velocidade_kmh)"

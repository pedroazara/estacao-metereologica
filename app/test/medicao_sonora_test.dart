import 'dart:math' as math;

import 'package:estacao_movel/audio/medicao_sonora.dart';
import 'package:flutter_test/flutter_test.dart';

AmostraSom _amostra(
  double dbfs, {
  int count = 4800,
  int sampleRate = 48000,
  double? peak,
  String deviceKey = 'microfone-interno',
}) {
  final potencia = math.pow(10, dbfs / 10).toDouble();
  return AmostraSom(
    meanSquare: potencia,
    peak: peak ?? math.sqrt(potencia),
    count: count,
    sampleRate: sampleRate,
    deviceKey: deviceKey,
  );
}

Map<String, dynamic> _evento() => {
  'meanSquare': 0.01,
  'peak': 0.2,
  'count': 4800,
  'sampleRate': 48000,
  'deviceKey': 'microfone-interno',
};

void main() {
  group('AmostraSom', () {
    test('converte evento válido e calcula a duração real do bloco', () {
      final amostra = AmostraSom.deEvento(_evento());
      expect(amostra.meanSquare, 0.01);
      expect(amostra.peak, 0.2);
      expect(amostra.count, 4800);
      expect(amostra.sampleRate, 48000);
      expect(amostra.deviceKey, 'microfone-interno');
      expect(amostra.duracao, 0.1);
    });

    test('rejeita valores ausentes, não finitos e fora do contrato', () {
      final invalidos = <String, List<dynamic>>{
        'meanSquare': [null, '0.1', -0.1, 1.01, double.nan, double.infinity],
        'peak': [null, -0.1, 1.01, double.nan, double.negativeInfinity],
        'count': [null, 0, -1, 1.5, double.nan, double.infinity],
        'sampleRate': [null, 0, -1, 44100.5, double.infinity],
        'deviceKey': [null, 123, '', '   '],
      };
      for (final campo in invalidos.entries) {
        for (final invalido in campo.value) {
          final evento = _evento()..[campo.key] = invalido;
          expect(
            () => AmostraSom.deEvento(evento),
            throwsFormatException,
            reason: '${campo.key}=$invalido',
          );
        }
      }
    });
  });

  group('MedicaoSonora', () {
    test('combina -60 e -40 dBFS pela energia, não pela média dos dB', () {
      final medicao = MedicaoSonora()
        ..adicionar(_amostra(-60))
        ..adicionar(_amostra(-40));

      expect(medicao.mediaDbfs, closeTo(-42.9670862188, 1e-9));
      expect(medicao.atualDbfs, closeTo(-40, 1e-9));
      expect(medicao.minimoDbfs, closeTo(-60, 1e-9));
      expect(medicao.maximoDbfs, closeTo(-40, 1e-9));
      expect(medicao.segundos, closeTo(0.2, 1e-9));
      expect(medicao.blocos, 2);
    });

    test(
      'pondera potência por duração mesmo com taxas de captura diferentes',
      () {
        final medicao = MedicaoSonora()
          ..adicionar(_amostra(-60, count: 44100, sampleRate: 44100))
          ..adicionar(_amostra(-40, count: 144000));
        final esperado = 10 * math.log((1e-6 + 3 * 1e-4) / 4) / math.ln10;

        expect(medicao.mediaDbfs, closeTo(esperado, 1e-9));
        expect(medicao.segundos, 4);
      },
    );

    test('silêncio usa piso numérico sem adicionar energia artificial', () {
      final medicao = MedicaoSonora()
        ..adicionar(
          const AmostraSom(
            meanSquare: 0,
            peak: 0,
            count: 4800,
            sampleRate: 48000,
            deviceKey: 'microfone-interno',
          ),
        );
      expect(medicao.semSinal, isTrue);
      expect(medicao.atualDbfs, closeTo(-120, 1e-9));
      expect(medicao.mediaDbfs, closeTo(-120, 1e-9));
      expect(medicao.referenciaDbfs, isNull);

      medicao.adicionar(_amostra(-100));
      expect(medicao.semSinal, isFalse);
      expect(medicao.mediaDbfs, closeTo(-103.0102999566, 1e-9));

      medicao.adicionar(_amostra(-150));
      expect(medicao.atualDbfs, closeTo(-120, 1e-9));
      expect(medicao.semSinal, isFalse);
    });

    test('saturação usa o pico do último bloco e máximo usa seu RMS', () {
      final medicao = MedicaoSonora()..adicionar(_amostra(-30, peak: 0.999));
      expect(medicao.saturado, isTrue);
      expect(medicao.maximoDbfs, closeTo(-30, 1e-9));

      medicao.adicionar(_amostra(-40, peak: 0.998));
      expect(medicao.saturado, isFalse);
      expect(medicao.maximoDbfs, closeTo(-30, 1e-9));
    });

    test(
      'referência exige dois segundos recentes e integra só os últimos três',
      () {
        final medicao = MedicaoSonora();
        for (var i = 0; i < 19; i++) {
          medicao.adicionar(_amostra(-60));
        }
        expect(medicao.referenciaDbfs, isNull);
        medicao.adicionar(_amostra(-60));
        expect(medicao.referenciaDbfs, closeTo(-60, 1e-9));

        for (var i = 0; i < 30; i++) {
          medicao.adicionar(_amostra(-40));
        }
        expect(medicao.referenciaDbfs, closeTo(-40, 1e-9));
        expect(medicao.mediaDbfs, lessThan(-40.1));
      },
    );

    test('referência recorta blocos na fronteira e pondera pela duração', () {
      final medicao = MedicaoSonora()
        ..adicionar(_amostra(-60, count: 120000))
        ..adicionar(_amostra(-40, count: 48000));
      final esperado = 10 * math.log((2e-6 + 1e-4) / 3) / math.ln10;
      expect(medicao.referenciaDbfs, closeTo(esperado, 1e-9));
    });

    test('referência fica indisponível enquanto houver saturação recente', () {
      final medicao = MedicaoSonora()..adicionar(_amostra(-30, peak: 1));
      for (var i = 0; i < 29; i++) {
        medicao.adicionar(_amostra(-40));
      }
      expect(medicao.saturado, isFalse);
      expect(medicao.referenciaDbfs, isNull);
      medicao.adicionar(_amostra(-40));
      expect(medicao.referenciaDbfs, closeTo(-40, 1e-9));
    });

    test('referência rejeita silêncio e potência insuficiente na janela', () {
      final medicao = MedicaoSonora()..adicionar(_amostra(-40, count: 144000));
      expect(medicao.referenciaDbfs, isNotNull);

      medicao.adicionar(_amostra(-100));
      expect(medicao.referenciaDbfs, isNull);
      medicao.adicionar(_amostra(-40, count: 144000));
      expect(medicao.referenciaDbfs, isNotNull);
    });

    test(
      'não mistura rotas nem altera estatísticas ao rejeitar uma amostra',
      () {
        final medicao = MedicaoSonora()..adicionar(_amostra(-40));
        expect(
          () => medicao.adicionar(_amostra(-20, deviceKey: 'headset')),
          throwsStateError,
        );
        expect(medicao.blocos, 1);
        expect(medicao.atualDbfs, closeTo(-40, 1e-9));
        expect(medicao.segundos, 0.1);
      },
    );

    test('também valida amostras criadas diretamente', () {
      final medicao = MedicaoSonora();
      expect(
        () => medicao.adicionar(
          const AmostraSom(
            meanSquare: double.nan,
            peak: 0,
            count: 1,
            sampleRate: 48000,
            deviceKey: 'microfone-interno',
          ),
        ),
        throwsFormatException,
      );
      expect(medicao.blocos, 0);
    });

    test('limpar remove estatísticas, referência e vínculo com a rota', () {
      final medicao = MedicaoSonora()
        ..adicionar(_amostra(-40, count: 144000))
        ..limpar();
      expect(medicao.segundos, 0);
      expect(medicao.blocos, 0);
      expect(medicao.atualDbfs, isNull);
      expect(medicao.mediaDbfs, isNull);
      expect(medicao.minimoDbfs, isNull);
      expect(medicao.maximoDbfs, isNull);
      expect(medicao.referenciaDbfs, isNull);
      expect(medicao.historico, isEmpty);
      expect(medicao.semSinal, isFalse);
      expect(medicao.saturado, isFalse);

      medicao.adicionar(_amostra(-20, deviceKey: 'headset'));
      expect(medicao.atualDbfs, closeTo(-20, 1e-9));
    });

    test(
      'histórico fica restrito a trinta segundos sem truncar a média geral',
      () {
        final medicao = MedicaoSonora()..adicionar(_amostra(-20, count: 48000));
        for (var i = 0; i < 400; i++) {
          medicao.adicionar(_amostra(-40));
        }
        final historico = medicao.historico;
        expect(historico.length, lessThanOrEqualTo(301));
        expect(historico.first.segundos, greaterThan(medicao.segundos - 30));
        expect(historico.last.segundos, medicao.segundos);
        expect(medicao.blocos, 401);
        expect(medicao.mediaDbfs, greaterThan(-39));
        expect(() => historico.clear(), throwsUnsupportedError);
      },
    );

    test('limita o número de pontos mesmo recebendo blocos muito curtos', () {
      final medicao = MedicaoSonora();
      for (var i = 0; i < 1000; i++) {
        medicao.adicionar(_amostra(-40, count: 48));
      }
      expect(medicao.historico.length, 400);
      expect(medicao.blocos, 1000);
      expect(medicao.segundos, closeTo(1, 1e-9));
    });
  });
}

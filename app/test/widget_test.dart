// Teste de fumaca: garante que o app sobe e monta a navegacao.
//
// Nao testa BLE/GPS (exigem hardware). Verifica so que a arvore de widgets
// monta com as abas certas. Nao checa o botao "Conectar" porque o app agora
// AUTO-CONECTA na partida - o botao ja aparece como "procurando...".

import 'package:flutter_test/flutter_test.dart';

import 'package:estacao_movel/main.dart';

void main() {
  testWidgets('sobe e mostra a navegacao', (tester) async {
    await tester.pumpWidget(const AppEstacao());

    // As cinco abas da navegacao inferior - estaveis, sem depender de conexao.
    expect(find.text('Painel'), findsOneWidget);
    expect(find.text('Mapa'), findsOneWidget);
    expect(find.text('Sensores'), findsOneWidget);
    expect(find.text('Estacao'), findsOneWidget);
    expect(find.text('Sessoes'), findsOneWidget);
  });
}

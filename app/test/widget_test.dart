// Teste de fumaca: garante que o app sobe e mostra a tela de conexao.
//
// Nao ha teste de BLE aqui de proposito - isso exige radio e uma estacao viva,
// entao seria teste de bancada, nao unitario. O que da para verificar sem
// hardware e que a arvore de widgets monta e que o estado inicial e o certo.

import 'package:flutter_test/flutter_test.dart';

import 'package:estacao_movel/main.dart';

void main() {
  testWidgets('abre desconectado, oferecendo conectar', (tester) async {
    await tester.pumpWidget(const AppEstacao());

    expect(find.text('Estacao Movel'), findsOneWidget);
    expect(find.text('Conectar'), findsOneWidget);

    // As tres abas da navegacao.
    expect(find.text('Painel'), findsOneWidget);
    expect(find.text('Estacao'), findsOneWidget);
    expect(find.text('Sessoes'), findsOneWidget);
  });
}

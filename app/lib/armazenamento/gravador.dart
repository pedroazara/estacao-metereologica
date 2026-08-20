import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// Grava a coleta em disco, no celular.
///
/// POR QUE ISTO EXISTE. Enquanto o ESP32 nao tem cartao SD, o celular e a
/// CUSTODIA do dado — a unica copia entre o campo e a base. Ver
/// docs/04-telemetria.md: o cru tem que sobreviver, porque e ele que permite
/// reprocessar a mesma coleta com outra sintonia do filtro depois.
///
/// FORMATO: JSONL (um JSON por linha). Escolhido por tres razoes praticas:
///
///   1. Anexavel. Nao ha que reescrever nem fechar um array — o processo pode
///      morrer a qualquer momento e o arquivo continua valido ate a ultima
///      linha inteira. Um JSON array exigiria fechar o `]`, e sessao
///      interrompida viraria arquivo corrompido.
///   2. Cada linha ja e o envelope que a base entende, com o `src` dizendo de
///      qual caracteristica veio.
///   3. Da para inspecionar com `head`, `grep` e `wc -l` sem escrever codigo.
class Gravador {
  IOSink? _saida;
  File? _arquivo;
  int _linhas = 0;
  DateTime? _inicio;

  bool get gravando => _saida != null;
  int get linhas => _linhas;
  String? get caminho => _arquivo?.path;
  DateTime? get inicio => _inicio;

  static Future<Directory> _pasta() async {
    final base = await getApplicationDocumentsDirectory();
    final p = Directory('${base.path}/sessoes');
    if (!await p.exists()) await p.create(recursive: true);
    return p;
  }

  /// Abre uma sessao nova. O nome carrega a data e o dispositivo, para o
  /// arquivo ser identificavel sem abrir — mesmo criterio do CSV da base.
  Future<void> iniciar(String dev) async {
    if (_saida != null) return;
    final agora = DateTime.now();
    final carimbo = agora
        .toIso8601String()
        .substring(0, 19)
        .replaceAll(RegExp(r'[:\-]'), '')
        .replaceFirst('T', '-');
    final pasta = await _pasta();
    _arquivo = File('${pasta.path}/$dev-$carimbo.jsonl');
    // Modo append: se o arquivo ja existir por algum acaso, nunca sobrescreve.
    _saida = _arquivo!.openWrite(mode: FileMode.writeOnlyAppend);
    _linhas = 0;
    _inicio = agora;
  }

  /// Anexa um envelope.
  ///
  /// O carimbo de tempo e do CELULAR, na chegada — o ESP32 sem GPS com fix nao
  /// tem hora absoluta, e sem cartao SD nao ha `ms` de boot para reancorar
  /// depois. Fica registrado como `ts_app` justamente para nao se passar pelo
  /// `ts` carimbado na origem, que e o que docs/04-telemetria.md especifica.
  void anexar(String origem, Map<String, dynamic> corpo) {
    final s = _saida;
    if (s == null) return;
    s.writeln(jsonEncode({
      'src': origem,
      'ts_app': DateTime.now().toIso8601String(),
      'm': corpo,
    }));
    _linhas++;
  }

  Future<void> encerrar() async {
    final s = _saida;
    _saida = null;
    if (s == null) return;
    await s.flush();
    await s.close();
  }

  static Future<List<File>> sessoes() async {
    final pasta = await _pasta();
    final itens = await pasta
        .list()
        .where((e) => e is File && e.path.endsWith('.jsonl'))
        .cast<File>()
        .toList();
    itens.sort((a, b) => b.path.compareTo(a.path));  // mais recente primeiro
    return itens;
  }
}

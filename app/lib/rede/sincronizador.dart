import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// Envia as sessoes gravadas no celular para a base, por HTTP POST.
///
/// POR QUE HTTP E NAO MQTT. A sincronizacao e "despejar o arquivo quando chegar
/// em casa", nao streaming. Um POST e mais simples, retomavel e dispensa broker
/// — ver docs/04-telemetria.md. A base atende em POST /sync e usa o nome do
/// arquivo como manifesto: reenviar a mesma sessao e um no-op, entao uma
/// transferencia que cai na metade pode ser repetida inteira sem duplicar.
class Sincronizador {
  /// Host da base. Nome mDNS por padrao; o usuario pode trocar por IP se o
  /// mDNS nao resolver no celular dele (acontece).
  String base;
  final int porta;

  Sincronizador({this.base = 'raspberrypi.local', this.porta = 8771});

  static Future<File> _arquivoConfig() async {
    final dir = await getApplicationDocumentsDirectory();
    return File('${dir.path}/base.txt');
  }

  static Future<String> carregarBase() async {
    try {
      final f = await _arquivoConfig();
      if (await f.exists()) {
        final s = (await f.readAsString()).trim();
        if (s.isNotEmpty) return s;
      }
    } catch (_) {}
    return 'raspberrypi.local';
  }

  Future<void> salvarBase(String host) async {
    base = host.trim();
    try {
      await (await _arquivoConfig()).writeAsString(base);
    } catch (_) {}
  }

  /// Envia um arquivo. Retorna um resultado legivel para a UI.
  Future<ResultadoSync> enviar(File arquivo) async {
    final nome = arquivo.path.split('/').last;
    final dev = nome.split('-').first; // o gravador nomeia dev-<carimbo>.jsonl
    final corpo = await arquivo.readAsBytes();

    final cliente = HttpClient()
      ..connectionTimeout = const Duration(seconds: 8);
    try {
      final req = await cliente
          .postUrl(Uri.parse('http://$base:$porta/sync'))
          .timeout(const Duration(seconds: 8));
      req.headers.set('X-Dev', dev);
      req.headers.set('X-Sessao', nome);
      req.headers.contentType = ContentType('application', 'x-ndjson');
      req.add(corpo);
      final resp = await req.close().timeout(const Duration(seconds: 30));
      final texto = await utf8.decodeStream(resp);
      if (resp.statusCode != 200) {
        return ResultadoSync(false, 'base respondeu ${resp.statusCode}');
      }
      final j = jsonDecode(texto) as Map<String, dynamic>;
      if (j['ok'] != true) {
        return ResultadoSync(false, (j['erro'] ?? 'falha').toString());
      }
      await _marcarEnviado(arquivo);
      return ResultadoSync(true,
          j['ja_tinha'] == true ? 'a base ja tinha' : 'enviada',
          jaTinha: j['ja_tinha'] == true);
    } on SocketException {
      return ResultadoSync(false, 'base inacessivel — ela esta ligada?');
    } catch (e) {
      return ResultadoSync(false, e.toString());
    } finally {
      cliente.close(force: true);
    }
  }

  /// Marca por arquivo-irmao, nunca apagando o original: enquanto o ESP32 nao
  /// tem cartao, este arquivo pode ser a unica copia do cru. A base guarda a
  /// dela; o celular guarda a sua. Ver docs/04-telemetria.md ("nao apague").
  static Future<void> _marcarEnviado(File arquivo) async {
    try {
      await File('${arquivo.path}.enviado').writeAsString(
          DateTime.now().toIso8601String());
    } catch (_) {}
  }

  static Future<bool> jaEnviado(File arquivo) =>
      File('${arquivo.path}.enviado').exists();
}

class ResultadoSync {
  final bool ok;
  final String mensagem;
  final bool jaTinha;
  ResultadoSync(this.ok, this.mensagem, {this.jaTinha = false});
}

import 'dart:convert';
import 'dart:io';

import '../modelo/plano.dart';

/// Fala com os endpoints de planejamento da base (POST/GET /plano*, ver
/// src/servidor_telemetria.py). Mesmo host/porta que o Sincronizador usa -
/// e a MESMA base, so outra parte da API dela.
///
/// PULL, nao push: a base nunca manda nada sozinha para o telefone. O app
/// pergunta quando quer saber (abrir a aba Planos, puxar para atualizar) -
/// mesmo padrao ja usado pelo BaseStatus e pelo Sincronizador.
class ClientePlanos {
  final String base;
  final int porta;
  const ClientePlanos({this.base = 'raspberrypi.local', this.porta = 8771});

  Uri _url(String caminho) => Uri.parse('http://$base:$porta$caminho');

  /// So devolve `null` quando a base RESPONDEU e disse "nao tem nada" (200
  /// com corpo vazio) - isso e um resultado valido (ex.: medidor sem plano
  /// ativo). Falha de rede (base fora do ar, host errado, timeout) e outra
  /// coisa: propaga a excecao, para quem chama (PlanoAtual.carregar) poder
  /// diferenciar "sem plano" de "nao consegui nem perguntar" e avisar o
  /// usuario direito, em vez de mostrar uma lista vazia enganosa.
  Future<T?> _getJson<T>(
      String caminho, T Function(dynamic) converter) async {
    final cliente = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    try {
      final req = await cliente.getUrl(_url(caminho)).timeout(const Duration(seconds: 5));
      final resp = await req.close().timeout(const Duration(seconds: 5));
      if (resp.statusCode != 200) {
        await resp.drain<void>();
        throw HttpException('base respondeu ${resp.statusCode}');
      }
      final texto = await utf8.decodeStream(resp);
      if (texto.trim().isEmpty) return null;
      return converter(jsonDecode(texto));
    } finally {
      cliente.close(force: true);
    }
  }

  /// Planos do medidor conectado - so os dele, nao os de outras estacoes.
  Future<List<PlanoResumo>> listar(String dev) async {
    final r = await _getJson('/planos?dev=${Uri.encodeQueryComponent(dev)}',
        (j) => (j as List).map((e) => PlanoResumo.fromJson(e)).toList());
    return r ?? [];
  }

  Future<PlanoDetalhe?> obter(int id) async {
    final r = await _getJson('/plano?id=$id', (j) => j as Map<String, dynamic>);
    if (r == null || r.isEmpty) return null;
    return PlanoDetalhe.fromJson(r);
  }

  /// O plano que a BASE tem marcado como ativo para esse medidor agora -
  /// pode ter mudado desde a ultima vez que o app perguntou (o operador da
  /// base pode trocar a qualquer momento).
  Future<PlanoDetalhe?> ativoPara(String dev) async {
    final r = await _getJson('/plano/ativo?dev=${Uri.encodeQueryComponent(dev)}',
        (j) => j as Map<String, dynamic>);
    if (r == null || r.isEmpty) return null;
    return PlanoDetalhe.fromJson(r);
  }

  /// Marca `id` como o plano ativo do medidor dele - e assim que o usuario
  /// "escolhe qual vai fazer": a escolha fica gravada na base (mesmo lugar
  /// que o botao "Ativar para o medidor" do painel mexe), entao os dois
  /// lados sempre concordam sobre qual e o plano do momento.
  Future<bool> ativar(int id) async {
    final cliente = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    try {
      final req = await cliente
          .postUrl(_url('/plano/ativar'))
          .timeout(const Duration(seconds: 5));
      final corpo = utf8.encode(jsonEncode({'id': id}));
      req.contentLength = corpo.length;
      req.add(corpo);
      final resp = await req.close().timeout(const Duration(seconds: 5));
      await resp.drain<void>();
      return resp.statusCode == 200;
    } catch (_) {
      return false;
    } finally {
      cliente.close(force: true);
    }
  }
}

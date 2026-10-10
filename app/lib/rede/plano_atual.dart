import 'package:flutter/foundation.dart';

import '../ble/estacao_ble.dart';
import '../modelo/plano.dart';
import 'base_status.dart';
import 'cliente_planos.dart';

/// Estado dos planos do medidor conectado - uma instancia so para o app
/// inteiro (como o BaseStatus), para a aba Planos e a aba Mapa (que desenha
/// a area do plano escolhido por cima do trajeto) verem sempre a mesma
/// coisa.
///
/// So funciona com a estacao CONECTADA: o `dev` vem do BLE (EstacaoBle.status),
/// nao existe conceito de "dev" solto no app - ver [[app-mobile-em-andamento]].
class PlanoAtual extends ChangeNotifier {
  final EstacaoBle ble;
  final BaseStatus base;
  PlanoAtual({required this.ble, required this.base}) {
    ble.addListener(_aoMudarBle);
  }

  List<PlanoResumo> lista = [];
  PlanoDetalhe? selecionado;
  bool carregando = false;
  String? erro;
  String? _ultimoDev;

  String? get dev => ble.status?.dev;

  void _aoMudarBle() {
    final d = dev;
    if (d != null && d != _ultimoDev) {
      _ultimoDev = d;
      carregar();
    }
  }

  Future<void> carregar() async {
    final d = dev;
    if (d == null) return;
    carregando = true;
    erro = null;
    notifyListeners();
    final cliente = ClientePlanos(base: base.host);
    try {
      lista = await cliente.listar(d);
      selecionado = await cliente.ativoPara(d);
    } catch (e) {
      // Mensagem com o host tentado: o erro mais comum e mDNS
      // (raspberrypi.local) nao resolver no celular - o usuario troca por
      // IP no campo "Endereco da base" da aba Sessoes (mesmo campo usado
      // para sincronizar, o host e compartilhado).
      erro = 'nao consegui falar com a base em "${base.host}" ($e)';
    } finally {
      carregando = false;
      notifyListeners();
    }
  }

  /// Escolhe qual plano o usuario vai fazer agora - grava na base (fica
  /// valendo para os dois lados, painel e app) e atualiza o mapa local.
  Future<bool> escolher(int id) async {
    final cliente = ClientePlanos(base: base.host);
    final ok = await cliente.ativar(id);
    if (ok) await carregar();
    return ok;
  }

  bool _vivo = true;

  @override
  void notifyListeners() {
    if (_vivo) super.notifyListeners();
  }

  @override
  void dispose() {
    _vivo = false;
    ble.removeListener(_aoMudarBle);
    super.dispose();
  }
}

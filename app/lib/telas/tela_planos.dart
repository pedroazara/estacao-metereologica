import 'package:flutter/material.dart';

import '../rede/plano_atual.dart';
import 'tela_plano_mapa.dart';

/// Os planos (areas pintadas no painel da base) do medidor conectado, e a
/// escolha de qual o usuario vai fazer agora.
///
/// So le e escolhe - pintar/editar a area continua sendo so no painel da
/// base (aba "Planejar area"). Escolher aqui grava na base como "ativo"
/// (mesmo campo que o botao do painel mexe), entao os dois lados sempre
/// concordam sobre qual e o plano do momento.
class TelaPlanos extends StatefulWidget {
  final PlanoAtual planos;
  const TelaPlanos({super.key, required this.planos});

  @override
  State<TelaPlanos> createState() => _TelaPlanosState();
}

class _TelaPlanosState extends State<TelaPlanos> {
  @override
  void initState() {
    super.initState();
    widget.planos.addListener(_aoAtualizar);
    if (widget.planos.dev != null && widget.planos.lista.isEmpty) {
      widget.planos.carregar();
    }
  }

  @override
  void dispose() {
    widget.planos.removeListener(_aoAtualizar);
    super.dispose();
  }

  void _aoAtualizar() {
    if (mounted) setState(() {});
  }

  Future<void> _escolher(int id, String nome) async {
    final ok = await widget.planos.escolher(id);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(ok
          ? '"$nome" e o plano ativo agora'
          : 'nao consegui falar com a base'),
      backgroundColor:
          ok ? null : Theme.of(context).colorScheme.error,
    ));
  }

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;
    final p = widget.planos;
    final dev = p.dev;

    if (dev == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(Icons.pin_drop_outlined, size: 48, color: fraco),
            const SizedBox(height: 14),
            Text('Conecte a estacao para ver os planos dela.',
                textAlign: TextAlign.center, style: TextStyle(color: fraco)),
          ]),
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: p.carregar,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
        children: [
          Text('planos de $dev',
              style: TextStyle(fontSize: 11, color: fraco, letterSpacing: 1)),
          const SizedBox(height: 8),
          if (p.carregando) const LinearProgressIndicator(minHeight: 2),
          if (p.erro != null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Row(children: [
                Icon(Icons.cloud_off, size: 16, color: fraco),
                const SizedBox(width: 8),
                Expanded(
                    child: Text(p.erro!,
                        style: TextStyle(fontSize: 12, color: fraco))),
              ]),
            ),
          if (!p.carregando && p.erro == null && p.lista.isEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 60),
              child: Column(children: [
                Icon(Icons.pin_drop_outlined, size: 48, color: fraco),
                const SizedBox(height: 14),
                Text('Nenhuma area planejada para esta estacao ainda.',
                    textAlign: TextAlign.center, style: TextStyle(color: fraco)),
                const SizedBox(height: 6),
                Text(
                  'Pinte uma no painel da base, aba "Planejar area".',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 12, color: fraco),
                ),
              ]),
            ),
          for (final plano in p.lista)
            Card(
              child: ListTile(
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => TelaPlanoMapa(
                    planoId: plano.id,
                    nomeInicial: plano.nome,
                    planos: p,
                  ),
                )),
                leading: Icon(
                  plano.ativo ? Icons.check_circle : Icons.circle_outlined,
                  color: plano.ativo ? const Color(0xFF3FB950) : fraco,
                ),
                title: Text(plano.nome),
                subtitle: Text(_fmtData(plano.criadoEm),
                    style: TextStyle(fontSize: 12, color: fraco)),
                trailing: plano.ativo
                    ? const Text('ativo',
                        style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                            color: Color(0xFF3FB950)))
                    : OutlinedButton(
                        onPressed: () => _escolher(plano.id, plano.nome),
                        child: const Text('Fazer este'),
                      ),
              ),
            ),
        ],
      ),
    );
  }
}

String _fmtData(String iso) => iso.replaceFirst('T', ' ').split('.').first;

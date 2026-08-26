import 'dart:io';

import 'package:flutter/material.dart';

import '../armazenamento/gravador.dart';
import '../rede/sincronizador.dart';

/// As coletas guardadas no celular, e o envio delas para a base.
///
/// Enquanto o ESP32 nao tem cartao SD, estes arquivos sao a unica copia do
/// dado cru. Nada aqui apaga nada: sincronizar marca, nao remove.
class TelaSessoes extends StatefulWidget {
  const TelaSessoes({super.key});

  @override
  State<TelaSessoes> createState() => _TelaSessoesState();
}

class _TelaSessoesState extends State<TelaSessoes> {
  final _sinc = Sincronizador();
  final _hostCtrl = TextEditingController();
  List<File> _arquivos = [];
  final Set<String> _enviados = {};
  bool _carregando = true;
  String? _ocupado; // caminho do arquivo em envio

  @override
  void initState() {
    super.initState();
    _iniciar();
  }

  @override
  void dispose() {
    _hostCtrl.dispose();
    super.dispose();
  }

  Future<void> _iniciar() async {
    _sinc.base = await Sincronizador.carregarBase();
    _hostCtrl.text = _sinc.base;
    await _carregar();
  }

  Future<void> _carregar() async {
    final a = await Gravador.sessoes();
    final enviados = <String>{};
    for (final f in a) {
      if (await Sincronizador.jaEnviado(f)) enviados.add(f.path);
    }
    if (!mounted) return;
    setState(() {
      _arquivos = a;
      _enviados
        ..clear()
        ..addAll(enviados);
      _carregando = false;
    });
  }

  Future<void> _enviar(File f) async {
    setState(() => _ocupado = f.path);
    await _sinc.salvarBase(_hostCtrl.text);
    final r = await _sinc.enviar(f);
    if (!mounted) return;
    setState(() => _ocupado = null);
    if (r.ok) _enviados.add(f.path);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text('${f.path.split('/').last}: ${r.mensagem}'),
      backgroundColor: r.ok ? null : Theme.of(context).colorScheme.error,
    ));
  }

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;
    if (_carregando) return const Center(child: CircularProgressIndicator());

    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
        child: TextField(
          controller: _hostCtrl,
          decoration: InputDecoration(
            labelText: 'Endereco da base',
            helperText: 'nome (raspberrypi.local) ou IP, porta 8771',
            isDense: true,
            border: const OutlineInputBorder(),
            prefixIcon: const Icon(Icons.dns_outlined),
          ),
          onSubmitted: (v) => _sinc.salvarBase(v),
        ),
      ),
      Expanded(
        child: RefreshIndicator(
          onRefresh: _carregar,
          child: _arquivos.isEmpty
              ? ListView(children: [
                  const SizedBox(height: 100),
                  Icon(Icons.folder_open, size: 48, color: fraco),
                  const SizedBox(height: 14),
                  Center(
                      child: Text('nenhuma coleta gravada ainda',
                          style: TextStyle(color: fraco))),
                ])
              : ListView.builder(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                  itemCount: _arquivos.length + 1,
                  itemBuilder: (context, i) {
                    if (i == _arquivos.length) return _Rodape(fraco: fraco);
                    final f = _arquivos[i];
                    return _Item(
                      arquivo: f,
                      enviado: _enviados.contains(f.path),
                      ocupado: _ocupado == f.path,
                      onEnviar: _ocupado == null ? () => _enviar(f) : null,
                    );
                  },
                ),
        ),
      ),
    ]);
  }
}

class _Item extends StatelessWidget {
  final File arquivo;
  final bool enviado, ocupado;
  final VoidCallback? onEnviar;
  const _Item(
      {required this.arquivo,
      required this.enviado,
      required this.ocupado,
      required this.onEnviar});

  Future<void> _verConteudo(BuildContext context) async {
    int linhas = 0, gps = 0, amb = 0;
    String trecho = '';
    try {
      final texto = await arquivo.readAsString();
      final ls = texto.split('\n').where((l) => l.trim().isNotEmpty).toList();
      linhas = ls.length;
      gps = ls.where((l) => l.contains('"gps"')).length;
      amb = ls.where((l) => l.contains('"amb"')).length;
      trecho = ls.take(6).join('\n');
    } catch (_) {}
    if (!context.mounted) return;
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        title: Text(arquivo.path.split('/').last,
            style: const TextStyle(fontSize: 13)),
        content: SingleChildScrollView(
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(linhas == 0
                    ? 'ARQUIVO VAZIO — nada foi gravado nesta sessao.'
                    : '$linhas linhas  ·  $gps pontos de GPS  ·  $amb leituras de ambiente'),
                if (linhas == 0)
                  const Padding(
                    padding: EdgeInsets.only(top: 8),
                    child: Text(
                      'A gravacao comecou mas nenhum dado chegou. Verifique se '
                      'o GPS pegou posicao (ponto verde no Mapa) e se a estacao '
                      'estava conectada, e grave de novo.',
                      style: TextStyle(fontSize: 12),
                    ),
                  ),
                if (trecho.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: const Color(0xFF0A0E13),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: SelectableText(trecho,
                        style: const TextStyle(
                            fontFamily: 'monospace', fontSize: 10)),
                  ),
                ],
              ]),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Fechar')),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;
    final nome = arquivo.path.split('/').last;
    final tam = arquivo.existsSync() ? arquivo.lengthSync() : 0;

    return Card(
      child: ListTile(
        onTap: () => _verConteudo(context),
        leading: Icon(
            enviado ? Icons.cloud_done : Icons.description_outlined,
            color: enviado ? const Color(0xFF3FB950) : null),
        title: Text(nome, style: const TextStyle(fontSize: 13)),
        subtitle: Text(
            '${_tamanho(tam)}${enviado ? "  ·  na base" : ""}  ·  toque para ver',
            style: TextStyle(fontSize: 12, color: fraco)),
        trailing: ocupado
            ? const SizedBox(
                width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
            : IconButton(
                icon: Icon(enviado ? Icons.refresh : Icons.cloud_upload_outlined),
                tooltip: enviado ? 'enviar de novo' : 'enviar para a base',
                onPressed: onEnviar,
              ),
      ),
    );
  }
}

class _Rodape extends StatelessWidget {
  final Color fraco;
  const _Rodape({required this.fraco});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 24),
      child: Column(children: [
        Icon(Icons.shield_outlined, size: 20, color: fraco),
        const SizedBox(height: 8),
        Text(
          'Enviar nao apaga nada: o celular guarda a copia dele,\n'
          'a base guarda a dela. Reenviar a mesma sessao e seguro.',
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 12, color: fraco),
        ),
      ]),
    );
  }
}

String _tamanho(int b) {
  if (b < 1024) return '$b B';
  if (b < 1024 * 1024) return '${(b / 1024).toStringAsFixed(1)} KB';
  return '${(b / 1024 / 1024).toStringAsFixed(1)} MB';
}

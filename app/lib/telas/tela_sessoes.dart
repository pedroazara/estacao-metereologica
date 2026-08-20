import 'dart:io';

import 'package:flutter/material.dart';

import '../armazenamento/gravador.dart';

/// As coletas guardadas no celular, esperando ir para a base.
///
/// Enquanto o ESP32 nao tem cartao SD, estes arquivos sao a unica copia do
/// dado cru. A tela evita de proposito qualquer botao de apagar em massa: o
/// custo de um toque errado aqui e uma coleta de campo inteira.
class TelaSessoes extends StatefulWidget {
  const TelaSessoes({super.key});

  @override
  State<TelaSessoes> createState() => _TelaSessoesState();
}

class _TelaSessoesState extends State<TelaSessoes> {
  List<File> _arquivos = [];
  bool _carregando = true;

  @override
  void initState() {
    super.initState();
    _carregar();
  }

  Future<void> _carregar() async {
    final a = await Gravador.sessoes();
    if (!mounted) return;
    setState(() {
      _arquivos = a;
      _carregando = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;

    if (_carregando) {
      return const Center(child: CircularProgressIndicator());
    }

    return RefreshIndicator(
      onRefresh: _carregar,
      child: _arquivos.isEmpty
          ? ListView(children: [
              const SizedBox(height: 120),
              Icon(Icons.folder_open, size: 48, color: fraco),
              const SizedBox(height: 14),
              Center(
                child: Text('nenhuma coleta gravada ainda',
                    style: TextStyle(color: fraco)),
              ),
            ])
          : ListView.builder(
              padding: const EdgeInsets.all(16),
              itemCount: _arquivos.length + 1,
              itemBuilder: (context, i) {
                if (i == _arquivos.length) return _Rodape(fraco: fraco);
                final f = _arquivos[i];
                return _Item(arquivo: f);
              },
            ),
    );
  }
}

class _Item extends StatelessWidget {
  final File arquivo;
  const _Item({required this.arquivo});

  @override
  Widget build(BuildContext context) {
    final fraco = Theme.of(context).colorScheme.outline;
    final nome = arquivo.path.split('/').last;
    final tam = arquivo.existsSync() ? arquivo.lengthSync() : 0;

    return Card(
      child: ListTile(
        leading: const Icon(Icons.description_outlined),
        title: Text(nome, style: const TextStyle(fontSize: 13)),
        subtitle: Text(_tamanho(tam), style: TextStyle(fontSize: 12, color: fraco)),
        onTap: () => showDialog(
          context: context,
          builder: (_) => AlertDialog(
            title: const Text('Sessao'),
            content: Column(mainAxisSize: MainAxisSize.min, children: [
              SelectableText(arquivo.path,
                  style: const TextStyle(fontSize: 11, fontFamily: 'monospace')),
              const SizedBox(height: 12),
              Text(_tamanho(tam)),
            ]),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('Fechar')),
            ],
          ),
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
        Icon(Icons.cloud_upload_outlined, size: 20, color: fraco),
        const SizedBox(height: 8),
        Text(
          'O envio automatico para a base ainda nao existe.\n'
          'Por enquanto os arquivos ficam aqui, em seguranca.',
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

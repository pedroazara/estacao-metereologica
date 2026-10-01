import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../audio/decibelimetro.dart';
import '../audio/medicao_sonora.dart';

class TelaDecibelimetro extends StatefulWidget {
  final Decibelimetro? controlador;
  final bool gerenciarControlador;
  final bool Function()? coletaEmAndamento;
  const TelaDecibelimetro({
    super.key,
    this.controlador,
    this.gerenciarControlador = true,
    this.coletaEmAndamento,
  });

  @override
  State<TelaDecibelimetro> createState() => _TelaDecibelimetroState();
}

class _TelaDecibelimetroState extends State<TelaDecibelimetro>
    with WidgetsBindingObserver {
  late final Decibelimetro _som;

  @override
  void initState() {
    super.initState();
    _som = widget.controlador ?? Decibelimetro();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (!widget.gerenciarControlador) return;
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden ||
        state == AppLifecycleState.detached) {
      unawaited(_som.parar(segundoPlano: true));
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    if (widget.gerenciarControlador) _som.dispose();
    super.dispose();
  }

  Future<void> _calibrar() async {
    final campo = TextEditingController();
    String? mensagem;
    bool salvando = false;
    await showDialog<void>(
      context: context,
      builder: (contexto) => StatefulBuilder(
        builder: (contexto, atualizar) => AlertDialog(
          title: const Text('Calibrar o microfone'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text(
                  'Compare com um medidor de referência próximo ao telefone, '
                  'sob um som estável. Informe o nível em dB SPL sem ponderação A/C. '
                  'O ajuste usa os últimos 3 segundos de leitura.',
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: campo,
                  autofocus: true,
                  enabled: !salvando,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: InputDecoration(
                    labelText: 'Leitura de referência',
                    suffixText: 'dB SPL',
                    hintText: 'Ex.: 70,0',
                    errorText: mensagem,
                    border: const OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                const Text(
                  'A calibração por um ponto é uma aproximação. '
                  'A resposta do microfone varia com a frequência e a intensidade.',
                  style: TextStyle(fontSize: 12),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: salvando ? null : () => Navigator.pop(contexto),
              child: const Text('Cancelar'),
            ),
            FilledButton(
              onPressed: salvando
                  ? null
                  : () async {
                      final valor = double.tryParse(
                        campo.text.trim().replaceAll(',', '.'),
                      );
                      if (valor == null ||
                          !valor.isFinite ||
                          valor < 0 ||
                          valor > 160) {
                        atualizar(
                          () => mensagem = 'Informe um valor entre 0 e 160.',
                        );
                        return;
                      }
                      atualizar(() {
                        salvando = true;
                        mensagem = null;
                      });
                      try {
                        await _som.calibrar(valor);
                        if (contexto.mounted) Navigator.pop(contexto);
                      } catch (_) {
                        if (contexto.mounted) {
                          atualizar(() {
                            salvando = false;
                            mensagem = 'Não foi possível calibrar. Confira a medição e tente novamente.';
                          });
                        }
                      }
                    },
              child: Text(salvando ? 'Salvando…' : 'Salvar ajuste'),
            ),
          ],
        ),
      ),
    );
    // O campo permanece na árvore durante a animação de saída do diálogo.
    await Future<void>.delayed(const Duration(milliseconds: 250));
    campo.dispose();
  }

  Future<void> _removerCalibracao() async {
    try {
      await _som.removerCalibracao();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Não foi possível remover o ajuste salvo.'),
          ),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    const acento = Color(0xFF76E0C2);
    return Scaffold(
      appBar: AppBar(title: const Text('Decibelímetro')),
      bottomNavigationBar: SafeArea(
        minimum: const EdgeInsets.fromLTRB(20, 8, 20, 12),
        child: ListenableBuilder(
          listenable: _som,
          builder: (context, _) => FilledButton.icon(
            onPressed:
                _som.carregando ||
                    _som.iniciando ||
                    (widget.coletaEmAndamento?.call() ?? false)
                ? null
                : _som.medindo
                ? _som.parar
                : _som.iniciar,
            style: FilledButton.styleFrom(
              minimumSize: const Size.fromHeight(54),
            ),
            icon: Icon(_som.medindo ? Icons.stop_rounded : Icons.mic_rounded),
            label: Text(
              (widget.coletaEmAndamento?.call() ?? false)
                  ? 'Som incluído na coleta'
                  : _som.iniciando
                  ? 'Aguardando microfone…'
                  : _som.medindo
                  ? 'Encerrar medição'
                  : _som.medicao.blocos > 0
                  ? 'Nova medição'
                  : 'Iniciar medição',
            ),
          ),
        ),
      ),
      body: ListenableBuilder(
        listenable: _som,
        builder: (context, _) {
          final medicao = _som.medicao;
          final atual = _som.corrigir(medicao.atualDbfs);
          final esquema = Theme.of(context).colorScheme;
          return ListView(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
            children: [
              Row(
                children: [
                  Icon(
                    Icons.mic_none_rounded,
                    size: 18,
                    color: esquema.onSurfaceVariant,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'MICROFONE DO CELULAR',
                      style: TextStyle(
                        fontSize: 12,
                        letterSpacing: 1,
                        color: esquema.onSurfaceVariant,
                      ),
                    ),
                  ),
                  Text(
                    _duracao(medicao.segundos),
                    style: const TextStyle(
                      fontFeatures: [FontFeature.tabularFigures()],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 18),
              Container(
                padding: const EdgeInsets.all(24),
                decoration: BoxDecoration(
                  color: const Color(0xFF102722),
                  borderRadius: BorderRadius.circular(24),
                  border: Border.all(color: acento.withValues(alpha: .25)),
                ),
                child: Column(
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(
                          _som.medindo
                              ? Icons.circle
                              : Icons.pause_circle_outline,
                          size: 12,
                          color: _som.medindo ? acento : Colors.white60,
                        ),
                        const SizedBox(width: 8),
                        Flexible(
                          child: Text(
                            _som.iniciando
                                ? 'ABRINDO MICROFONE'
                                : _som.medindo
                                ? 'MEDINDO AGORA'
                                : 'MEDIÇÃO PARADA',
                            style: const TextStyle(
                              fontSize: 12,
                              letterSpacing: 1,
                              color: Colors.white70,
                            ),
                            textAlign: TextAlign.center,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 14),
                    Semantics(
                      label: 'Nível atual',
                      child: FittedBox(
                        fit: BoxFit.scaleDown,
                        child: Text(
                          atual == null ? '—' : _numero(atual),
                          style: const TextStyle(
                            fontSize: 76,
                            height: 1.1,
                            fontWeight: FontWeight.w300,
                            color: acento,
                            fontFeatures: [FontFeature.tabularFigures()],
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      _som.unidade,
                      style: const TextStyle(fontSize: 18, color: Colors.white),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      _som.calibrado
                          ? 'Ajuste por referência • sem ponderação A/C'
                          : 'Nível digital • ainda sem calibração',
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        fontSize: 12,
                        color: Colors.white70,
                      ),
                    ),
                    const SizedBox(height: 22),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(4),
                      child: LinearProgressIndicator(
                        value: medicao.atualDbfs == null
                            ? 0
                            : ((medicao.atualDbfs! + 100) / 100).clamp(
                                0.0,
                                1.0,
                              ),
                        minHeight: 6,
                        color: acento,
                        backgroundColor: Colors.white12,
                        semanticsLabel:
                            'Nível relativo à capacidade do microfone',
                      ),
                    ),
                    const SizedBox(height: 10),
                    const Text(
                      'Leitura RMS em blocos de 100 ms',
                      style: TextStyle(fontSize: 12, color: Colors.white60),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),
              LayoutBuilder(
                builder: (context, limites) {
                  final itens = [
                    _Estatistica(
                      titulo: 'Mínimo',
                      valor: _som.corrigir(medicao.minimoDbfs),
                    ),
                    _Estatistica(
                      titulo: 'Média energética',
                      valor: _som.corrigir(medicao.mediaDbfs),
                    ),
                    _Estatistica(
                      titulo: 'Máximo',
                      valor: _som.corrigir(medicao.maximoDbfs),
                    ),
                  ];
                  if (limites.maxWidth < 330 ||
                      MediaQuery.textScalerOf(context).scale(14) > 20) {
                    return Column(
                      children: itens
                          .map(
                            (e) => Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: e,
                            ),
                          )
                          .toList(),
                    );
                  }
                  return Row(
                    children: [
                      for (var i = 0; i < itens.length; i++) ...[
                        if (i > 0) const SizedBox(width: 8),
                        Expanded(child: itens[i]),
                      ],
                    ],
                  );
                },
              ),
              const SizedBox(height: 8),
              Text(
                'Estatísticas da medição inteira. Máximo e mínimo dos blocos RMS.',
                style: TextStyle(fontSize: 12, color: esquema.onSurfaceVariant),
              ),
              const SizedBox(height: 20),
              Row(
                children: [
                  const Expanded(
                    child: Text(
                      'Últimos 30 segundos',
                      style: TextStyle(fontWeight: FontWeight.w600),
                    ),
                  ),
                  Text(
                    _som.calibrado ? 'dB SPL est.' : 'dBFS',
                    style: TextStyle(
                      fontSize: 12,
                      color: esquema.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Container(
                height: 170,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: esquema.surfaceContainerLow,
                  borderRadius: BorderRadius.circular(16),
                ),
                child: medicao.historico.isEmpty
                    ? Center(
                        child: Text(
                          'Inicie para acompanhar o nível sonoro.',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: esquema.onSurfaceVariant),
                        ),
                      )
                    : Semantics(
                        label:
                            'Gráfico do nível sonoro nos últimos 30 segundos',
                        child: CustomPaint(
                          painter: _GraficoSom(
                            pontos: medicao.historico,
                            omitirPiso: _som.calibrado,
                            ajuste: _som.ajuste,
                            cor: acento,
                            texto: esquema.onSurfaceVariant,
                          ),
                        ),
                      ),
              ),
              if (_som.erro != null) _Mensagem(texto: _som.erro!, erro: true),
              if (_som.aviso != null) _Mensagem(texto: _som.aviso!),
              if (_som.medindo && medicao.saturado)
                const _Mensagem(
                  texto: 'Microfone saturado: o som ultrapassou a capacidade de captura. A leitura está limitada.',
                  erro: true,
                ),
              if (_som.medindo && medicao.semSinal && medicao.blocos > 0)
                const _Mensagem(
                  texto: 'O microfone está retornando silêncio digital. Confira se outro app está usando o áudio. −120 dBFS é o piso numérico, não uma medição do ambiente.',
                ),
              if (_som.permissaoBloqueada)
                TextButton.icon(
                  onPressed: _som.abrirPermissoes,
                  icon: const Icon(Icons.settings_outlined),
                  label: const Text('Permitir microfone nas configurações'),
                ),
              const SizedBox(height: 20),
              OutlinedButton.icon(
                onPressed:
                    _som.podeCalibrar &&
                        !(widget.coletaEmAndamento?.call() ?? false)
                    ? _calibrar
                    : null,
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size.fromHeight(48),
                ),
                icon: const Icon(Icons.tune_rounded),
                label: const Text('Calibrar com referência'),
              ),
              if (!_som.podeCalibrar && !_som.calibrado)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    'Para calibrar, inicie e aguarde pelo menos 2 segundos de sinal sem saturação.',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontSize: 12,
                      color: esquema.onSurfaceVariant,
                    ),
                  ),
                ),
              if (_som.calibracao != null)
                TextButton(
                  onPressed: (widget.coletaEmAndamento?.call() ?? false)
                      ? null
                      : _removerCalibracao,
                  child: const Text('Remover calibração salva'),
                ),
              const SizedBox(height: 18),
              Text(
                widget.gerenciarControlador
                    ? 'O áudio é processado no telefone e não é gravado nem enviado. '
                          'A medição termina ao sair desta tela ou apagar a tela. '
                          'O ajuste não transforma o celular em um instrumento certificado.'
                    : 'Na coleta, são salvos somente os níveis sonoros, nunca o áudio. '
                          'Você pode voltar à tela Sensores para conferir os outros sensores. '
                          'Ao sair do aplicativo, a captura do microfone é encerrada.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 12, color: esquema.onSurfaceVariant),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _Estatistica extends StatelessWidget {
  final String titulo;
  final double? valor;
  const _Estatistica({required this.titulo, required this.valor});

  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 14),
    decoration: BoxDecoration(
      color: Theme.of(context).colorScheme.surfaceContainerLow,
      borderRadius: BorderRadius.circular(14),
    ),
    child: Column(
      children: [
        Text(
          titulo,
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 12),
        ),
        const SizedBox(height: 8),
        Text(
          valor == null ? '—' : _numero(valor!),
          style: const TextStyle(
            fontSize: 23,
            fontWeight: FontWeight.w600,
            fontFeatures: [FontFeature.tabularFigures()],
          ),
        ),
      ],
    ),
  );
}

class _Mensagem extends StatelessWidget {
  final String texto;
  final bool erro;
  const _Mensagem({required this.texto, this.erro = false});
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 12),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(
          erro ? Icons.warning_amber_rounded : Icons.info_outline,
          size: 18,
          color: erro ? Theme.of(context).colorScheme.error : null,
        ),
        const SizedBox(width: 8),
        Expanded(child: Text(texto, style: const TextStyle(fontSize: 13))),
      ],
    ),
  );
}

String _numero(double v) => v.toStringAsFixed(1).replaceAll('.', ',');
String _duracao(double segundos) {
  final s = segundos.floor();
  return '${(s ~/ 60).toString().padLeft(2, '0')}:${(s % 60).toString().padLeft(2, '0')}';
}

class _GraficoSom extends CustomPainter {
  final List<PontoSom> pontos;
  final bool omitirPiso;
  final double ajuste;
  final Color cor, texto;
  _GraficoSom({
    required this.pontos,
    required this.omitirPiso,
    required this.ajuste,
    required this.cor,
    required this.texto,
  });

  @override
  void paint(Canvas canvas, Size size) {
    if (pontos.isEmpty) return;
    final valores = pontos
        .where((p) => !omitirPiso || p.dbfs > -119.999999)
        .map((p) => p.dbfs + ajuste);
    if (valores.isEmpty) return;
    final minimo = ((valores.reduce(math.min) - 5) / 10).floor() * 10.0;
    final maximo = math.max(
      minimo + 20,
      ((valores.reduce(math.max) + 5) / 10).ceil() * 10.0,
    );
    final fim = math.max(30.0, pontos.last.segundos);
    final inicio = fim - 30;
    const margem = 42.0;
    final largura = size.width - margem;
    final altura = size.height - 20;
    void legenda(String txt, double x, double y) {
      final p = TextPainter(
        text: TextSpan(
          text: txt,
          style: TextStyle(color: texto, fontSize: 10),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      p.paint(canvas, Offset(x, y));
    }

    for (var i = 0; i <= 4; i++) {
      final y = altura * i / 4;
      canvas.drawLine(
        Offset(margem, y),
        Offset(size.width, y),
        Paint()
          ..color = texto.withValues(alpha: .15)
          ..strokeWidth = 1,
      );
      legenda(
        (maximo - (maximo - minimo) * i / 4).toStringAsFixed(0),
        0,
        math.max(0, y - 6),
      );
    }
    legenda('${inicio.toInt()} s', margem, altura + 6);
    legenda('${fim.toInt()} s', math.max(margem, size.width - 34), altura + 6);
    final caminho = Path();
    var novoTrecho = true;
    for (var i = 0; i < pontos.length; i++) {
      final p = pontos[i];
      if (omitirPiso && p.dbfs <= -119.999999) {
        novoTrecho = true;
        continue;
      }
      final x = margem + ((p.segundos - inicio) / 30).clamp(0.0, 1.0) * largura;
      final y = altura * (1 - (p.dbfs + ajuste - minimo) / (maximo - minimo));
      if (novoTrecho) {
        caminho.moveTo(x, y);
        novoTrecho = false;
      } else {
        caminho.lineTo(x, y);
      }
    }
    canvas.drawPath(
      caminho,
      Paint()
        ..color = cor
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2,
    );
  }

  @override
  bool shouldRepaint(covariant _GraficoSom oldDelegate) => true;
}

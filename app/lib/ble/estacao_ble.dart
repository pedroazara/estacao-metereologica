import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';

import '../armazenamento/gravador.dart';
import '../gps/gps_telefone.dart';
import '../kalman/fusao.dart';
import '../modelo/leituras.dart';
import 'contrato.dart';

enum EstadoLigacao { desligado, procurando, conectando, ligado, erro }

/// Toda a conversa com a estacao. A interface so observa isto.
class EstacaoBle extends ChangeNotifier {
  EstadoLigacao estado = EstadoLigacao.desligado;
  String mensagem = 'desconectado';

  Status? status;
  Posicao? posicao;
  Ambiente? ambiente;
  LoteImu? ultimoLote;
  Configuracao? config;

  int notificacoesRecebidas = 0;
  int falhasDeParse = 0;
  DateTime? ultimaNotificacao;

  final gravador = Gravador();

  /// Previa de Kalman ao vivo. Alimentada pelas notificacoes; a base continua
  /// dona do resultado oficial. Ver kalman/filtro_cv.dart.
  final fusao = FusaoAoVivo();

  /// GPS do telefone: a fonte de posicao atual (o do ESP fica dormente).
  final gps = GpsTelefone();

  /// Ultima posicao do telefone, para o mapa marcar onde voce esta.
  double? get telLat => gps.lat;
  double? get telLon => gps.lon;

  BluetoothDevice? _dispositivo;
  BluetoothCharacteristic? _carCfg;
  BluetoothCharacteristic? _carLoc;
  final _assinaturas = <StreamSubscription<dynamic>>[];
  StreamSubscription<dynamic>? _assEstado;
  Timer? _autoTimer;

  bool get conectado => estado == EstadoLigacao.ligado;

  /// Auto-conexao: enquanto ligado, o app tenta achar e conectar na estacao
  /// sozinho, e reconecta se ela cair. Assim, chegar perto do ESP ja conecta -
  /// sem apertar botao. O botao "Conectar" continua valendo como forcar agora.
  void iniciarAutoConexao() {
    _autoTimer?.cancel();
    _tentarSeSolto();
    _autoTimer = Timer.periodic(
        const Duration(seconds: 5), (_) => _tentarSeSolto());
  }

  void _tentarSeSolto() {
    if (estado == EstadoLigacao.desligado || estado == EstadoLigacao.erro) {
      procurarEConectar();
    }
  }

  /// Liga o GPS do telefone e encaminha cada posicao para o Kalman e a gravacao.
  /// Chamado uma vez, na partida do app.
  Future<void> iniciarGpsTelefone() async {
    gps.aoReceber = _gpsDoTelefone;
    gps.addListener(notifyListeners); // o mapa observa a posicao do telefone
    await gps.iniciar();
  }

  void _gpsDoTelefone(
      double lat, double lon, double precisaoM, double velKmh, double rumo) {
    // ESCREVE a posicao no ESP, que a re-emite como `pos`. Nao alimenta a fusao
    // aqui: o dado entra pelo mesmo caminho que o resto (a notificacao `pos`),
    // com a linha de tempo do ESP. Uma fonte so. Se o ESP nao estiver conectado,
    // simplesmente nao ha para onde mandar - o mapa ainda mostra a posicao crua
    // do telefone (telLat/telLon) para o usuario nao ficar sem referencia.
    final c = _carLoc;
    if (c == null) return;
    final corpo = jsonEncode({
      'lat': lat, 'lon': lon, 'vel': velKmh,
      'rumo': rumo.isNaN ? 0.0 : rumo,
    });
    // WRITE_NR (sem resposta): posicao e continua, perder uma nao importa, e
    // esperar confirmacao a cada fix so encheria a fila do BLE.
    c.write(utf8.encode(corpo), withoutResponse: true).catchError((_) {});
  }

  /// Procura a estacao e conecta na primeira que anunciar o nosso servico.
  ///
  /// O filtro e por UUID DE SERVICO, nao por nome: nome de dispositivo BLE nao
  /// e unico nem confiavel, e um vizinho com um ESP32 qualquer chamado
  /// "esp32-01" apareceria na lista. O UUID e nosso.
  Future<void> procurarEConectar() async {
    if (estado == EstadoLigacao.procurando ||
        estado == EstadoLigacao.conectando) {
      return;
    }
    _mudar(EstadoLigacao.procurando, 'procurando a estacao...');

    try {
      if (await FlutterBluePlus.isSupported == false) {
        _mudar(EstadoLigacao.erro, 'este celular nao tem Bluetooth LE');
        return;
      }
      // Em muitos Android o radio esta desligado; pedir para ligar evita um
      // "nao encontrei nada" que na verdade era o Bluetooth off.
      if (FlutterBluePlus.adapterStateNow != BluetoothAdapterState.on) {
        try {
          await FlutterBluePlus.turnOn();
        } catch (_) {
          _mudar(EstadoLigacao.erro, 'ligue o Bluetooth e tente de novo');
          return;
        }
      }

      await FlutterBluePlus.startScan(
        withServices: [Guid(Contrato.servico)],
        timeout: const Duration(seconds: 15),
      );

      BluetoothDevice? achado;
      await for (final resultados in FlutterBluePlus.scanResults) {
        if (resultados.isNotEmpty) {
          achado = resultados.first.device;
          break;
        }
      }
      await FlutterBluePlus.stopScan();

      if (achado == null) {
        // desligado (nao "erro"): a auto-conexao vai tentar de novo, e ficar
        // fora de alcance e normal. So o botao manual mostra isso como aviso.
        _mudar(EstadoLigacao.desligado,
            'estacao nao encontrada - ela esta ligada e por perto?');
        return;
      }
      await _conectar(achado);
    } catch (e) {
      _mudar(EstadoLigacao.erro, 'falha ao procurar: $e');
    }
  }

  Future<void> _conectar(BluetoothDevice d) async {
    _mudar(EstadoLigacao.conectando, 'conectando...');
    _dispositivo = d;

    await _assEstado?.cancel();
    _assEstado = d.connectionState.listen((s) {
      if (s == BluetoothConnectionState.disconnected &&
          estado == EstadoLigacao.ligado) {
        _mudar(EstadoLigacao.desligado, 'a estacao desconectou');
        _limparAssinaturas();
      }
    });

    // ATENCAO AO `license`. O flutter_blue_plus tem licenca dupla desde a 2.x:
    // `nonprofit` cobre uso pessoal, pesquisa e instituicao de ensino, que e o
    // caso deste projeto; uso com fins lucrativos exige licenca COMERCIAL paga.
    // Se a estacao virar produto vendido, isto precisa ser revisto - ver o
    // LICENSE do pacote. Nao e detalhe de compilacao, e condicao de uso.
    //
    // O `mtu` aqui ja faz a negociacao na conexao, antes de qualquer
    // notificacao chegar. Fazer depois seria tarde: as primeiras notificacoes
    // viriam truncadas no MTU padrao de 23 bytes.
    await d.connect(
      license: License.nonprofit,
      mtu: Contrato.mtu,
      autoConnect: false,
      timeout: const Duration(seconds: 20),
    );

    final servicos = await d.discoverServices();
    final servico = servicos.firstWhere(
      (s) => s.uuid == Guid(Contrato.servico),
      orElse: () => throw Exception('servico da estacao nao encontrado'),
    );

    for (final c in servico.characteristics) {
      final id = c.uuid.toString().toLowerCase();
      if (id == Contrato.cfg.toLowerCase()) _carCfg = c;
      if (id == Contrato.loc.toLowerCase()) _carLoc = c;
      await _assinar(c, id);
    }

    // Le a configuracao atual de cara: a tela de ajustes precisa mostrar o que
    // a estacao ESTA fazendo, nao valores padrao inventados pelo app.
    try {
      final bruto = await _carCfg?.read();
      if (bruto != null && bruto.isNotEmpty) _processar(Contrato.cfg, bruto);
    } catch (_) {}

    _mudar(EstadoLigacao.ligado, 'conectado');
  }

  Future<void> _assinar(BluetoothCharacteristic c, String id) async {
    if (!c.properties.notify) return;
    await c.setNotifyValue(true);
    _assinaturas.add(c.onValueReceived.listen((dados) => _processar(id, dados)));
  }

  void _processar(String id, List<int> bytes) {
    if (bytes.isEmpty) return;
    notificacoesRecebidas++;
    ultimaNotificacao = DateTime.now();

    Map<String, dynamic> j;
    try {
      j = jsonDecode(utf8.decode(bytes)) as Map<String, dynamic>;
    } catch (_) {
      // Quase sempre significa notificacao truncada, ou seja: o MTU nao subiu.
      falhasDeParse++;
      notifyListeners();
      return;
    }

    switch (id) {
      case _ when id == Contrato.status.toLowerCase():
        status = Status.doJson(j);
        _gravar('status', j);
      case _ when id == Contrato.pos.toLowerCase():
        // `pos` agora carrega a posicao do TELEFONE, re-emitida pelo ESP com a
        // linha de tempo dele (o campo interno "src" diz tel/esp). Uma fonte so:
        // alimenta o Kalman e vai para a gravacao pelo mesmo caminho.
        posicao = Posicao.doJson(j);
        fusao.gps(posicao!);
        _gravar('pos', j);
      case _ when id == Contrato.amb.toLowerCase():
        ambiente = Ambiente.doJson(j);
        _gravar('amb', j);
      case _ when id == Contrato.imu.toLowerCase():
        ultimoLote = LoteImu.doJson(j);
        // Cada amostra do pedaco adianta a predicao, com o dt do proprio lote.
        for (final a in ultimoLote!.amostras) {
          fusao.imu(a, ultimoLote!.dt);
        }
        _gravar('imu', j);
      case _ when id == Contrato.cfg.toLowerCase():
        config = Configuracao.doJson(j);
      // A configuracao NAO vai para o arquivo da coleta: ela e ajuste do
      // instrumento, nao medida. Misturar as duas coisas sujaria a serie.
    }
    notifyListeners();
  }

  void _gravar(String origem, Map<String, dynamic> j) {
    if (gravador.gravando) gravador.anexar(origem, j);
  }

  Future<void> iniciarGravacao() async {
    await gravador.iniciar(status?.dev ?? 'estacao');
    notifyListeners();
  }

  Future<void> pararGravacao() async {
    await gravador.encerrar();
    notifyListeners();
  }

  /// Envia a configuracao. O firmware responde por notify com o estado REAL
  /// depois de aplicar os limites dele, entao nao atualizamos `config` aqui —
  /// mostrar o pedido como se fosse o resultado seria mentir para o usuario.
  Future<bool> enviarConfig(Configuracao nova) async {
    final c = _carCfg;
    if (c == null) return false;
    try {
      await c.write(utf8.encode(jsonEncode(nova.paraEscrita())),
          withoutResponse: false);
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> desconectar() async {
    await gravador.encerrar();
    _limparAssinaturas();
    await _assEstado?.cancel();
    _assEstado = null;
    try {
      await _dispositivo?.disconnect();
    } catch (_) {}
    _dispositivo = null;
    _carCfg = null;
    _carLoc = null;
    _mudar(EstadoLigacao.desligado, 'desconectado');
  }

  void _limparAssinaturas() {
    for (final a in _assinaturas) {
      a.cancel();
    }
    _assinaturas.clear();
  }

  void _mudar(EstadoLigacao e, String m) {
    estado = e;
    mensagem = m;
    notifyListeners();
  }

  // Operacoes assincronas (GPS, BLE, HTTP) podem completar depois do dispose e
  // chamar notifyListeners num objeto morto - assercao. O guarda torna isso um
  // no-op, seguro tanto em teste quanto ao fechar o app.
  bool _vivo = true;

  @override
  void notifyListeners() {
    if (_vivo) super.notifyListeners();
  }

  @override
  void dispose() {
    _vivo = false;
    _autoTimer?.cancel();
    _limparAssinaturas();
    _assEstado?.cancel();
    gps.removeListener(notifyListeners);
    gps.parar();
    gravador.encerrar();
    super.dispose();
  }
}

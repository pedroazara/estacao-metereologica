import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';

import '../armazenamento/gravador.dart';
import '../gps/gps_telefone.dart';
import '../modelo/leituras.dart';
import 'contrato.dart';

enum EstadoLigacao { desligado, procurando, conectando, ligado, erro }

/// Toda a conversa com a estacao. A interface so observa isto.
///
/// SIMPLIFICADO em 26/08/2026: sem IMU, sem Kalman. A POSICAO vem do GPS do
/// proprio telefone (gravada direto, independente do ESP); o AMBIENTE
/// (temperatura/umidade) vem do ESP por BLE. A trajetoria comeca quando o
/// usuario aperta Gravar. Analise fina fica na base.
class EstacaoBle extends ChangeNotifier {
  EstadoLigacao estado = EstadoLigacao.desligado;
  String mensagem = 'desconectado';

  Status? status;
  Ambiente? ambiente;
  Configuracao? config;

  final gravador = Gravador();
  final gps = GpsTelefone();

  /// Trajetoria da MEDICAO: pontos [lat,lon] acumulados enquanto grava. Vazia
  /// fora da gravacao - a trajetoria e da medida, nao do passeio ate comecar.
  final List<List<double>> trilha = [];

  /// Posicao atual do telefone, para o mapa marcar onde voce esta agora.
  double? get telLat => gps.lat;
  double? get telLon => gps.lon;
  double? get precisaoM => gps.precisaoM;

  BluetoothDevice? _dispositivo;
  BluetoothCharacteristic? _carCfg;
  final _assinaturas = <StreamSubscription<dynamic>>[];
  StreamSubscription<dynamic>? _assEstado;
  Timer? _autoTimer;

  bool get conectado => estado == EstadoLigacao.ligado;

  // ------------------------------------------------------------------ GPS
  /// Liga o GPS do telefone. Chamado uma vez, na partida do app.
  Future<void> iniciarGpsTelefone() async {
    gps.aoReceber = _gpsDoTelefone;
    gps.addListener(notifyListeners); // o mapa observa a posicao do telefone
    await gps.iniciar();
  }

  void _gpsDoTelefone(
      double lat, double lon, double precisaoM, double velKmh, double rumo) {
    // So registra e desenha durante a gravacao: a trajetoria e da medida.
    if (gravador.gravando) {
      gravador.anexar('gps', {
        'lat': lat, 'lon': lon, 'precisao': precisaoM,
        'vel': velKmh, 'rumo': rumo.isNaN ? 0.0 : rumo,
      });
      if (trilha.isEmpty ||
          trilha.last[0] != lat || trilha.last[1] != lon) {
        trilha.add([lat, lon]);
        if (trilha.length > 5000) trilha.removeAt(0);
      }
    }
    notifyListeners();
  }

  // -------------------------------------------------------------- Gravacao
  Future<void> iniciarGravacao() async {
    trilha.clear();
    // Comeca a trajetoria na posicao atual, se ja houver fix.
    final la = gps.lat, lo = gps.lon;
    if (la != null && lo != null) trilha.add([la, lo]);
    await gravador.iniciar(status?.dev ?? 'estacao');
    notifyListeners();
  }

  Future<void> pararGravacao() async {
    await gravador.encerrar();
    notifyListeners();
  }

  // ------------------------------------------------------------ Auto-conexao
  void iniciarAutoConexao() {
    _autoTimer?.cancel();
    _tentarSeSolto();
    _autoTimer =
        Timer.periodic(const Duration(seconds: 5), (_) => _tentarSeSolto());
  }

  void _tentarSeSolto() {
    if (estado == EstadoLigacao.desligado || estado == EstadoLigacao.erro) {
      procurarEConectar();
    }
  }

  /// Procura a estacao (por UUID de servico, nao por nome) e conecta. So para
  /// receber os dados ambientais - a posicao nao depende disto.
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
    // license.nonprofit: uso pessoal/pesquisa/ensino. Uso comercial exige
    // licenca paga do flutter_blue_plus - ver o LICENSE do pacote.
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
      // Assina o que este app usa: ambiente, status e a CONFIG. A cfg precisa
      // ser assinada para o app receber a confirmacao do que a estacao aplicou
      // - sem isso, mudar um ajuste so aparecia depois de reiniciar o app. IMU
      // e posicao do ESP sao ignorados (a posicao vem do GPS do telefone).
      if (id == Contrato.amb.toLowerCase() ||
          id == Contrato.status.toLowerCase() ||
          id == Contrato.cfg.toLowerCase()) {
        await _assinar(c, id);
      }
    }
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
    Map<String, dynamic> j;
    try {
      j = jsonDecode(utf8.decode(bytes)) as Map<String, dynamic>;
    } catch (_) {
      return;
    }
    if (id == Contrato.amb.toLowerCase()) {
      ambiente = Ambiente.doJson(j);
      if (gravador.gravando) gravador.anexar('amb', j);
    } else if (id == Contrato.status.toLowerCase()) {
      status = Status.doJson(j);
    } else if (id == Contrato.cfg.toLowerCase()) {
      config = Configuracao.doJson(j);
    }
    notifyListeners();
  }

  /// Envia a configuracao. A estacao responde por notify com o estado real
  /// (apos os limites dela), entao nao atualizamos `config` aqui.
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

  // Guarda contra notify apos dispose (async que completa depois de fechar).
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

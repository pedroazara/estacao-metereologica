import 'filtro_cv.dart';
import '../modelo/leituras.dart';

/// A previa de Kalman ao vivo no telefone. Consome as leituras que chegam por
/// BLE, roda o FiltroCV e guarda as duas trilhas — GPS cru e filtrada — para o
/// painel desenhar uma sobre a outra.
///
/// E PREVIA, sempre. O cru continua sendo gravado pelo Gravador, e a base
/// reprocessa em lote com o filtro Python completo; ESTA saida nunca e a fonte
/// de verdade. Ver o cabecalho de filtro_cv.dart.
class FusaoAoVivo {
  final _kf = FiltroCV();
  double _t = 0.0;

  // Trilhas em [lat, lon]. O cap protege a memoria e o desenho numa sessao
  // longa; a base guarda a serie inteira, aqui basta o traçado recente.
  static const int _maxPontos = 2000;
  final List<List<double>> trilhaCru = [];
  final List<List<double>> trilhaFiltrada = [];

  bool get pronto => _kf.pronto;
  bool get parado => _kf.parado;
  double get velocidadeKmh => _kf.velocidadeKmh;
  double get sigmaPos => _kf.sigmaPos;
  int get rejeitados => _kf.rejeitados;
  double get desvioAccG => _kf.detector.desvioAccG;
  double get giroRmsDps => _kf.detector.giroRmsDps;

  List<double>? get posicaoFiltrada => _kf.pronto ? _kf.posicao : null;

  /// Uma amostra de IMU adianta a predicao. O dt vem do lote (medido no ESP32
  /// pelo esp_timer), nunca do relogio do telefone — latencia de BLE
  /// contaminaria a predicao exatamente como contaminaria a do MQTT.
  void imu(AmostraImu a, double dt) {
    if (!_kf.pronto || dt <= 0) return;
    _t += dt;
    _kf.prever(a.ax, a.ay, a.az, a.roll, a.pitch, dt,
        gx: a.gx, gy: a.gy, gz: a.gz);
  }

  /// Um fix de GPS inicia o filtro (no primeiro) ou o corrige.
  void gps(Posicao p) {
    if (!p.fix) return;
    _t += 1.0; // base de tempo grosseira para o detector; o dt real vem do IMU
    if (!_kf.pronto) {
      _kf.iniciar(p.lat, p.lon);
      trilhaCru.add([p.lat, p.lon]);
      return;
    }
    _kf.atualizarMovimentoGps(_t, p.lat, p.lon, p.vel);
    _kf.corrigirGps(p.lat, p.lon, hdop: p.hdop);
    _kf.corrigirVelocidade(p.vel, p.rumo);

    _acrescentar(trilhaCru, [p.lat, p.lon]);
    final f = _kf.posicao;
    _acrescentar(trilhaFiltrada, f);
  }

  void _acrescentar(List<List<double>> t, List<double> ponto) {
    // Ignora repeticoes exatas e mantem o cap por remocao do inicio.
    if (t.isNotEmpty &&
        t.last[0] == ponto[0] &&
        t.last[1] == ponto[1]) {
      return;
    }
    t.add(ponto);
    if (t.length > _maxPontos) t.removeAt(0);
  }

  void reiniciar() {
    trilhaCru.clear();
    trilhaFiltrada.clear();
    _t = 0.0;
    // Um FiltroCV novo seria o ideal, mas ele nao tem reset; recriar exigiria
    // torna-lo nao-final. Como iniciar() ja zera todo o estado, o proximo fix
    // reinicia limpo. Aqui so limpamos o que a UI mostra.
  }
}

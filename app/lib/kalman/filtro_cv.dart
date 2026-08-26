/// Porte Dart do nucleo de src/filtro_kalman.py — FiltroCV + DetectorParado.
///
/// POR QUE ISTO EXISTE, E ATE ONDE VAI. Roda no telefone como PREVIA ao vivo,
/// nada mais. O dado cru continua sendo gravado no JSONL (custodia intacta) e a
/// BASE continua sendo a autoridade: ela reprocessa o mesmo cru em lote, com o
/// filtro Python completo, e e esse resultado que vale para pesquisa. Ver
/// docs/04-telemetria.md. Aqui e so o que o painel de campo mostra enquanto o
/// pesquisador anda — util para saber que o filtro esta convergindo, nunca a
/// fonte de verdade.
///
/// FIDELIDADE. Traducao 1:1 das classes FiltroCV e DetectorParado do Python,
/// que ja usam matematica escalar (sem numpy) — por isso o porte e mecanico e
/// nao uma reimplementacao. NAO inclui passos (PDR), rumo nem calibracao
/// inicial: esses ~700 linhas ficam na base, e o rumo sem magnetometro e o
/// pedaco que menos vale ao vivo.
library;

import 'dart:math' as math;

const double grausParaM = 111320.0;
const double g = 9.80665;
const int nx = 4; // estado: n, e, vn, ve

// Convencao de sinais deste exemplar (ver filtro_kalman.py). O roll sai
// invertido em relacao ao nivelamento assumido.
const double sinalRoll = -1.0;

/// Compensacao de inclinacao: leva o vetor medido (g) ao plano horizontal e
/// remove a gravidade. Retorna [aFrente, aDireita] em g. Invariante ao rumo.
List<double> nivelar(
    double ax, double ay, double az, double rollGraus, double pitchGraus) {
  final r = _rad(sinalRoll * rollGraus);
  final p = _rad(pitchGraus);
  final cr = math.cos(r), sr = math.sin(r);
  final cp = math.cos(p), sp = math.sin(p);
  // R = Rx(roll) . Ry(pitch), mesma expansao do Python.
  // linha 0 (X nivelado, "direita"): (cp, 0, sp)
  // linha 1 (Y nivelado, "frente"):  (sr*sp, cr, -sr*cp)
  final aDireita = cp * ax + 0.0 * ay + sp * az;
  final aFrente = (sr * sp) * ax + cr * ay + (-sr * cp) * az;
  return [aFrente, aDireita];
}

double _rad(double graus) => graus * math.pi / 180.0;

/// Detecta repouso pela vibracao do IMU, com o GPS como evidencia
/// independente e histerese. Traducao fiel de DetectorParado.
class DetectorParado {
  final int janela;
  final double limiar, limiarSaida, limiarGiro, limiarSaidaGiro;
  final double confirmarParadoS, confirmarMovimentoS;

  final _buf = <double>[];
  final _bufGiro = <double>[];
  double _quietoS = 0.0, _movimentoS = 0.0;
  final _gps = <List<double>>[]; // [t, n, e, vel_kmh]
  bool _gpsMovendo = false, _gpsParado = false;
  bool parado = false;
  double desvioAccG = 0.0, giroRmsDps = 0.0;

  DetectorParado({
    this.janela = 12,
    this.limiar = 0.025,
    this.limiarSaida = 0.045,
    this.limiarGiro = 2.5,
    this.limiarSaidaGiro = 5.0,
    this.confirmarParadoS = 1.5,
    this.confirmarMovimentoS = 0.3,
  });

  bool get gpsMovendo => _gpsMovendo;
  bool get gpsParado => _gpsParado;

  void reiniciarGps() {
    _gps.clear();
    _gpsMovendo = false;
    _gpsParado = false;
  }

  void atualizarGps(double t, double n, double e, double velKmh) {
    _gps.add([t, n, e, math.max(0.0, velKmh)]);
    while (_gps.length > 2 && t - _gps.first[0] > 6.0) {
      _gps.removeAt(0);
    }
    final ult = _gps.length > 5 ? _gps.sublist(_gps.length - 5) : _gps;
    final velocidades = ult.map((p) => p[3]).toList()..sort();
    final mediana = velocidades[velocidades.length ~/ 2];
    final baseOk = _gps.length >= 2 && t - _gps.first[0] >= 3.0;
    final desloc = baseOk
        ? _hypot(n - _gps.first[1], e - _gps.first[2])
        : 0.0;

    if (mediana >= 3.0 || (mediana >= 1.8 && desloc >= 2.0)) {
      _gpsMovendo = true;
      _gpsParado = false;
    } else if (mediana <= 0.7 ||
        (mediana <= 1.2 && (!baseOk || desloc <= 3.5))) {
      _gpsMovendo = false;
      _gpsParado = _gps.length >= 3;
    }
  }

  bool amostra(double ax, double ay, double az,
      {double gx = 0.0,
      double gy = 0.0,
      double gz = 0.0,
      double dt = 0.1,
      bool passoDetectado = false,
      bool passosAtivos = false}) {
    final m = math.sqrt(ax * ax + ay * ay + az * az);
    _buf.add(m);
    _bufGiro.add(math.sqrt(gx * gx + gy * gy + gz * gz));
    if (_buf.length > janela) {
      _buf.removeAt(0);
      _bufGiro.removeAt(0);
    }
    if (_buf.length < janela) return parado;

    final media = _buf.reduce((a, b) => a + b) / _buf.length;
    final varr =
        _buf.map((v) => (v - media) * (v - media)).reduce((a, b) => a + b) /
            _buf.length;
    desvioAccG = math.sqrt(varr);
    giroRmsDps = math.sqrt(
        _bufGiro.map((v) => v * v).reduce((a, b) => a + b) / _bufGiro.length);

    final giroQuieto = giroRmsDps < limiarGiro;
    final imuQuieta = desvioAccG < limiar && giroQuieto;
    final quieto = (imuQuieta || (_gpsParado && giroQuieto)) &&
        !passosAtivos &&
        !_gpsMovendo;
    final passoForte = passosAtivos || (passoDetectado && !_gpsParado);
    final movimentoForte = passoForte ||
        _gpsMovendo ||
        giroRmsDps > limiarSaidaGiro ||
        (desvioAccG > limiarSaida && !_gpsParado);

    final d = math.max(0.0, math.min(dt, 1.0));
    if (parado) {
      if (movimentoForte) {
        parado = false;
        _movimentoS = _quietoS = 0.0;
      } else if (!quieto) {
        _movimentoS += d;
        if (_movimentoS >= confirmarMovimentoS) {
          parado = false;
          _movimentoS = _quietoS = 0.0;
        }
      } else {
        _movimentoS = 0.0;
      }
    } else {
      if (quieto) {
        _quietoS += d;
        if (_quietoS >= confirmarParadoS) {
          parado = true;
          _quietoS = _movimentoS = 0.0;
        }
      } else {
        _quietoS = 0.0;
      }
    }
    return parado;
  }
}

/// Kalman de 4 estados (n, e, vn, ve), modelo de velocidade constante. Nao usa
/// rumo em nenhuma etapa; o acelerometro entra so pelo modulo horizontal.
/// Traducao fiel de FiltroCV.
class FiltroCV {
  // parametros (defaults identicos ao Python)
  final double qBase, ganhoAcc, rPos, rVel, rZupt;
  final double velParado, velRumoOk, fcAcc, qParado;
  final bool adaptarPorHdop;

  static const double velMax = 55.0;
  static const double portaoSigma = 4.0;
  static const double portaoMin = 25.0;
  static const double tauSuavizacao = 0.45;
  static const double velRevela = 2.5;
  static const double accMaxQ = 3.0;

  double? lat0, lon0;
  double cosLat0 = 1.0;
  List<double> x = List.filled(nx, 0.0);
  List<List<double>> p = _ident();
  bool pronto = false;

  final detector = DetectorParado();
  bool parado = true;
  int rejeitados = 0;
  int _seguidos = 0;
  List<double> _resto = [0.0, 0.0];
  double _lpAcc = 0.0;
  double ultimaAcc = 0.0;
  bool entrouParado = false;

  FiltroCV({
    this.qBase = 0.25,
    this.ganhoAcc = 3.0,
    this.rPos = 9.0,
    this.rVel = 0.5,
    this.rZupt = 0.01,
    this.velParado = 1.0,
    this.velRumoOk = 3.0,
    this.fcAcc = 0.7,
    this.adaptarPorHdop = true,
    this.qParado = 0.005,
  });

  static List<List<double>> _ident() =>
      List.generate(nx, (i) => List.generate(nx, (j) => i == j ? 1.0 : 0.0));

  List<double> grausParaMetros(double lat, double lon) => [
        (lat - lat0!) * grausParaM,
        (lon - lon0!) * grausParaM * cosLat0,
      ];

  List<double> metrosParaGraus(double n, double e) => [
        lat0! + n / grausParaM,
        lon0! + e / (grausParaM * cosLat0),
      ];

  void iniciar(double lat, double lon,
      {double sigmaPos = 5.0, double sigmaVel = 2.0}) {
    lat0 = lat;
    lon0 = lon;
    cosLat0 = math.cos(_rad(lat));
    x = List.filled(nx, 0.0);
    p = _ident();
    final s = [sigmaPos, sigmaPos, sigmaVel, sigmaVel];
    for (var i = 0; i < nx; i++) {
      p[i][i] = s[i] * s[i];
    }
    _resto = [0.0, 0.0];
    _lpAcc = 0.0;
    detector.reiniciarGps();
    pronto = true;
  }

  void atualizarMovimentoGps(double t, double lat, double lon, double velKmh) {
    if (!pronto) return;
    final ne = grausParaMetros(lat, lon);
    detector.atualizarGps(t, ne[0], ne[1], velKmh);
  }

  void prever(double ax, double ay, double az, double roll, double pitch,
      double dt,
      {double gx = 0.0,
      double gy = 0.0,
      double gz = 0.0,
      bool passoDetectado = false,
      bool passosAtivos = false}) {
    if (!pronto || dt <= 0) return;

    final estavaParado = parado;
    parado = detector.amostra(ax, ay, az,
        gx: gx,
        gy: gy,
        gz: gz,
        dt: dt,
        passoDetectado: passoDetectado,
        passosAtivos: passosAtivos);
    entrouParado = parado && !estavaParado;

    final af = nivelar(ax, ay, az, roll, pitch);
    final modulo = _hypot(af[0], af[1]) * g;

    final alfa =
        fcAcc > 0 ? dt / (dt + 1.0 / (2 * math.pi * fcAcc)) : 1.0;
    _lpAcc += alfa * (modulo - _lpAcc);
    final acc = parado ? 0.0 : math.min(_lpAcc, accMaxQ);
    ultimaAcc = acc;

    if (parado) {
      _corrigir(2, 0.0, rZupt);
      _corrigir(3, 0.0, rZupt);
      x[2] = x[3] = 0.0;
    }

    x[0] += x[2] * dt;
    x[1] += x[3] * dt;
    x[2] = math.max(-velMax, math.min(velMax, x[2]));
    x[3] = math.max(-velMax, math.min(velMax, x[3]));

    // P = F P F' + Q
    final f = _ident();
    f[0][2] = f[1][3] = dt;
    final fp = List.generate(
        nx,
        (i) => List.generate(
            nx, (j) => _sum(nx, (k) => f[i][k] * p[k][j])));
    p = List.generate(
        nx,
        (i) => List.generate(
            nx, (j) => _sum(nx, (k) => fp[i][k] * f[j][k])));

    final q = parado ? qParado : qBase * (1.0 + ganhoAcc * acc);
    final dt3 = dt * dt * dt, dt2 = dt * dt;
    p[0][0] += dt3 / 3 * q;
    p[1][1] += dt3 / 3 * q;
    p[0][2] += dt2 / 2 * q;
    p[2][0] += dt2 / 2 * q;
    p[1][3] += dt2 / 2 * q;
    p[3][1] += dt2 / 2 * q;
    p[2][2] += dt * q;
    p[3][3] += dt * q;

    final resto = _hypot(_resto[0], _resto[1]);
    if (resto > 1e-6) {
      final alvo = math.max(resto * math.exp(-dt / tauSuavizacao),
          resto - velRevela * dt);
      final fator = math.max(0.0, alvo) / resto;
      _resto[0] *= fator;
      _resto[1] *= fator;
    }
  }

  void _corrigir(int i, double z, double r) {
    final y = z - x[i];
    final s = p[i][i] + r;
    if (s <= 0) return;
    final antes = [x[0], x[1]];
    final k = List.generate(nx, (j) => p[j][i] / s);
    for (var j = 0; j < nx; j++) {
      x[j] += k[j] * y;
    }
    final linha = List<double>.from(p[i]);
    p = List.generate(
        nx, (r2) => List.generate(nx, (c) => p[r2][c] - k[r2] * linha[c]));
    _resto[0] += x[0] - antes[0];
    _resto[1] += x[1] - antes[1];
  }

  void corrigirGps(double lat, double lon, {double hdop = 1.0}) {
    if (!pronto) {
      iniciar(lat, lon);
      return;
    }
    final ne = grausParaMetros(lat, lon);
    final n = ne[0], e = ne[1];
    final r =
        rPos * (adaptarPorHdop ? math.pow(math.max(hdop, 0.5), 2).toDouble() : 1.0);

    final dist = _hypot(n - x[0], e - x[1]);
    final limite = math.max(
        portaoMin,
        portaoSigma *
            math.sqrt(math.max(p[0][0], 0) + math.max(p[1][1], 0) + r));
    if (dist > limite) {
      rejeitados++;
      _seguidos++;
      if (_seguidos >= 3) {
        final ondeEstava = posicao;
        iniciar(lat, lon);
        _resto = [
          (lat - ondeEstava[0]) * grausParaM,
          (lon - ondeEstava[1]) * grausParaM * cosLat0,
        ];
        _seguidos = 0;
      }
      return;
    }
    _seguidos = 0;
    _corrigir(0, n, r);
    _corrigir(1, e, r);
  }

  void corrigirVelocidade(double velKmh, double rumoGraus) {
    if (!pronto) return;
    if (velKmh < velParado && (parado || !detector.gpsMovendo)) {
      _corrigir(2, 0.0, rZupt);
      _corrigir(3, 0.0, rZupt);
      return;
    }
    if (velKmh < velRumoOk) return;

    final v = velKmh / 3.6;
    final escala =
        math.max(1.0, math.pow(velRumoOk / 3.6 / math.max(v, 0.1), 2).toDouble());
    final r = _rad(rumoGraus);
    _corrigir(2, v * math.cos(r), rVel * escala);
    _corrigir(3, v * math.sin(r), rVel * escala);
  }

  List<double> get posicao =>
      metrosParaGraus(x[0] - _resto[0], x[1] - _resto[1]);
  List<double> get posicaoBruta => metrosParaGraus(x[0], x[1]);
  double get velocidadeKmh => _hypot(x[2], x[3]) * 3.6;
  double get sigmaPos => math.sqrt(math.max(p[0][0], 0) + math.max(p[1][1], 0));
}

double _hypot(double a, double b) => math.sqrt(a * a + b * b);
double _sum(int n, double Function(int) f) {
  var s = 0.0;
  for (var k = 0; k < n; k++) {
    s += f(k);
  }
  return s;
}

/// Modelos das leituras que chegam por BLE.
///
/// Cada classe corresponde a uma caracteristica do servico. Os campos seguem
/// exatamente as chaves que o firmware emite (ver publicarPosicao,
/// publicarAmbiente e publicarImu em painel_wifi.ino).
library;

import 'dart:math' as math;

class Status {
  final String dev;
  final int uptimeS;
  final bool wifi, mqtt, ambViva, gpsVivo, fix;
  final int satelites;

  const Status({
    required this.dev,
    required this.uptimeS,
    required this.wifi,
    required this.mqtt,
    required this.ambViva,
    required this.gpsVivo,
    required this.fix,
    required this.satelites,
  });

  factory Status.doJson(Map<String, dynamic> j) => Status(
        dev: (j['dev'] ?? '?') as String,
        uptimeS: (j['up'] ?? 0) as int,
        wifi: j['wifi'] == true,
        mqtt: j['mqtt'] == true,
        ambViva: j['amb'] == true,
        gpsVivo: j['gps'] == true,
        fix: j['fix'] == true,
        satelites: (j['sat'] ?? 0) as int,
      );
}

class Posicao {
  final int seq;
  final bool fix;
  final double lat, lon, alt, vel, hdop, rumo;
  final int satelites;

  const Posicao({
    required this.seq,
    required this.fix,
    required this.lat,
    required this.lon,
    required this.alt,
    required this.vel,
    required this.satelites,
    required this.hdop,
    required this.rumo,
  });

  factory Posicao.doJson(Map<String, dynamic> j) => Posicao(
        seq: (j['seq'] ?? 0) as int,
        fix: j['fix'] == true,
        lat: _d(j['lat']),
        lon: _d(j['lon']),
        alt: _d(j['alt']),
        vel: _d(j['vel']),
        satelites: (j['sat'] ?? 0) as int,
        hdop: _d(j['hdop']),
        rumo: _d(j['rumo']),
      );
}

class Ambiente {
  final int seq;
  final double temperatura, umidade;

  const Ambiente({
    required this.seq,
    required this.temperatura,
    required this.umidade,
  });

  factory Ambiente.doJson(Map<String, dynamic> j) => Ambiente(
        seq: (j['seq'] ?? 0) as int,
        temperatura: _d(j['temp']),
        umidade: _d(j['umid']),
      );
}

/// Um pedaco do lote de IMU.
///
/// O firmware fatia o lote porque ele nao cabe numa notificacao BLE. Cada
/// pedaco e um envelope completo — `p` de `np` — entao o app nao precisa
/// remontar nada para gravar: escreve o pedaco como veio. A remontagem, se um
/// dia interessar, e trabalho da base, que tem CPU e memoria de sobra.
class LoteImu {
  final int seq, pedaco, totalPedacos;
  final double dt;
  final List<AmostraImu> amostras;

  const LoteImu({
    required this.seq,
    required this.pedaco,
    required this.totalPedacos,
    required this.dt,
    required this.amostras,
  });

  factory LoteImu.doJson(Map<String, dynamic> j) => LoteImu(
        seq: (j['seq'] ?? 0) as int,
        pedaco: (j['p'] ?? 0) as int,
        totalPedacos: (j['np'] ?? 1) as int,
        dt: _d(j['dt']),
        amostras: ((j['m'] ?? []) as List)
            .map((a) => AmostraImu.doJson(a as Map<String, dynamic>))
            .toList(),
      );
}

class AmostraImu {
  final double ax, ay, az, gx, gy, gz, roll, pitch, yaw;

  const AmostraImu({
    required this.ax,
    required this.ay,
    required this.az,
    required this.gx,
    required this.gy,
    required this.gz,
    required this.roll,
    required this.pitch,
    required this.yaw,
  });

  factory AmostraImu.doJson(Map<String, dynamic> j) => AmostraImu(
        ax: _d(j['ax']),
        ay: _d(j['ay']),
        az: _d(j['az']),
        gx: _d(j['gx']),
        gy: _d(j['gy']),
        gz: _d(j['gz']),
        roll: _d(j['roll']),
        pitch: _d(j['pitch']),
        yaw: _d(j['yaw']),
      );

  double get moduloG => math.sqrt(ax * ax + ay * ay + az * az);
}

/// Configuracao da estacao — o que a caracteristica `cfg` le e escreve.
class Configuracao {
  final String dev;
  final bool gps, imu, amb, mqtt;
  final int pubMs, lote, loteMax;

  const Configuracao({
    required this.dev,
    required this.gps,
    required this.imu,
    required this.amb,
    required this.mqtt,
    required this.pubMs,
    required this.lote,
    required this.loteMax,
  });

  factory Configuracao.doJson(Map<String, dynamic> j) => Configuracao(
        dev: (j['dev'] ?? '?') as String,
        gps: j['gps'] == true,
        imu: j['imu'] == true,
        amb: j['amb'] == true,
        mqtt: j['mqtt'] == true,
        pubMs: (j['pubms'] ?? 1000) as int,
        lote: (j['lote'] ?? 10) as int,
        loteMax: (j['lotemax'] ?? 10) as int,
      );

  Configuracao copiaCom({
    bool? gps,
    bool? imu,
    bool? amb,
    bool? mqtt,
    int? pubMs,
    int? lote,
  }) =>
      Configuracao(
        dev: dev,
        gps: gps ?? this.gps,
        imu: imu ?? this.imu,
        amb: amb ?? this.amb,
        mqtt: mqtt ?? this.mqtt,
        pubMs: pubMs ?? this.pubMs,
        lote: lote ?? this.lote,
        loteMax: loteMax,
      );

  /// So os campos que o firmware aceita escrever. `dev` e `lotemax` sao
  /// informativos e mandar de volta so daria trabalho ao parser do ESP32.
  Map<String, dynamic> paraEscrita() => {
        'gps': gps,
        'imu': imu,
        'amb': amb,
        'mqtt': mqtt,
        'pubms': pubMs,
        'lote': lote,
      };
}

double _d(dynamic v) => v is num ? v.toDouble() : 0.0;

/// Modelos das leituras que chegam por BLE.
///
/// Cada classe corresponde a uma caracteristica do servico. Os campos seguem
/// exatamente as chaves que o firmware emite (ver publicarPosicao,
/// publicarAmbiente e publicarImu em painel_wifi.ino).
library;

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


class Ambiente {
  final int seq;
  final double temperatura, umidade;

  /// Ruido (dB, Leq do ciclo - KY-038), temperaturas auxiliares pro Kalman
  /// (KY-028/KY-013, ainda sem fusao no app) e luminosidade (lux - GY-302).
  /// -1 em `luminosidade` significa sensor ausente ou sem leitura valida
  /// naquele ciclo (ver publicarAmbiente() em painel_wifi.ino).
  final double ruidoDb, temperaturaAux28, temperaturaAux13, luminosidade;

  const Ambiente({
    required this.seq,
    required this.temperatura,
    required this.umidade,
    required this.ruidoDb,
    required this.temperaturaAux28,
    required this.temperaturaAux13,
    required this.luminosidade,
  });

  factory Ambiente.doJson(Map<String, dynamic> j) => Ambiente(
        seq: (j['seq'] ?? 0) as int,
        temperatura: _d(j['temp']),
        umidade: _d(j['umid']),
        ruidoDb: _d(j['db']),
        temperaturaAux28: _d(j['t28']),
        temperaturaAux13: _d(j['t13']),
        luminosidade: j.containsKey('lux') ? _d(j['lux']) : -1.0,
      );
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

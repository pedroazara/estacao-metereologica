#!/usr/bin/env python3
"""
Simula uma estacao publicando no broker MQTT, sem hardware nenhum.

    python3 simular_estacao.py

Serve para testar o servidor de telemetria, o mapa e o filtro de Kalman sem
depender de fix de GPS - que nao existe dentro de casa. Publica exatamente os
mesmos topicos e o mesmo formato que o firmware do ESP32:

    estacao/<dev>/pos      1 Hz
    estacao/<dev>/imu      1 Hz, lote de 10 amostras
    estacao/<dev>/amb      0.5 Hz
    estacao/<dev>/status   retained

O trajeto e um quarteirao retangular percorrido a pe (~1,4 m/s), com ruido de
GPS proporcional ao HDOP. E o cenario que mais separa o rastro cru do filtrado:
parado o ruido aparece como borrao, andando aparece como serrilhado.

    python3 simular_estacao.py --broker 192.168.0.111 --parado
"""

import argparse
import json
import math
import random
import time

import paho.mqtt.client as mqtt

# Ponto de partida: Lavras/MG, a mesma regiao dos dados reais em data/.
LAT0, LON0 = -21.2255, -44.9001
METROS_POR_GRAU_LAT = 111320.0

# Retangulo de 80 x 50 m, percorrido no sentido horario.
LADOS = [(0.0, 80.0), (90.0, 50.0), (180.0, 80.0), (270.0, 50.0)]
VELOCIDADE_MS = 1.4       # caminhada
RUIDO_GPS_M = 2.5         # desvio tipico de um receptor civil com HDOP ~1


def metros_para_graus(norte_m, leste_m):
    lat = LAT0 + norte_m / METROS_POR_GRAU_LAT
    lon = LON0 + leste_m / (METROS_POR_GRAU_LAT * math.cos(math.radians(LAT0)))
    return lat, lon


class Trajeto:
    """Caminha pelo retangulo, um passo de dt por vez."""

    def __init__(self, parado=False):
        self.parado = parado
        self.norte = 0.0
        self.leste = 0.0
        self.lado = 0
        self.andado = 0.0

    def passo(self, dt):
        if self.parado:
            return 0.0
        rumo, comprimento = LADOS[self.lado]
        d = VELOCIDADE_MS * dt
        self.norte += d * math.cos(math.radians(rumo))
        self.leste += d * math.sin(math.radians(rumo))
        self.andado += d
        if self.andado >= comprimento:
            self.andado = 0.0
            self.lado = (self.lado + 1) % len(LADOS)
        return rumo


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--broker", default="localhost")
    p.add_argument("--porta", type=int, default=1883)
    p.add_argument("--dev", default="esp32-sim",
                   help="id da estacao (use outro que nao o ESP32 real)")
    p.add_argument("--parado", action="store_true",
                   help="fica no mesmo lugar - mostra o ruido puro do GPS")
    p.add_argument("--hz-imu", type=float, default=10.0)
    args = p.parse_args()

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                          client_id=f"simulador-{args.dev}")
    topico_status = f"estacao/{args.dev}/status"
    cliente.will_set(topico_status, '{"online":false}', qos=1, retain=True)

    try:
        cliente.connect(args.broker, args.porta, keepalive=30)
    except OSError as erro:
        print(f"nao consegui falar com o broker: {erro}")
        print("O Mosquitto esta rodando?   systemctl status mosquitto")
        return
    cliente.loop_start()
    cliente.publish(topico_status, '{"online":true}', qos=1, retain=True)

    print(f"publicando como '{args.dev}' em {args.broker}:{args.porta}")
    print("Ctrl+C para parar.\n")

    trajeto = Trajeto(parado=args.parado)
    dt_imu = 1.0 / args.hz_imu
    n_lote = int(args.hz_imu)
    seq_pos = seq_imu = seq_amb = 0
    temp, umid = 23.0, 55.0

    try:
        while True:
            inicio = time.monotonic()

            # --- IMU: um lote por segundo -------------------------------
            lote = []
            for k in range(n_lote):
                rumo = trajeto.passo(dt_imu)
                if args.parado:
                    ax = ay = 0.0
                    az = 1.0 + random.gauss(0, 0.004)
                else:
                    # Oscilacao da passada: e o que o DetectorPassos procura.
                    fase = 2 * math.pi * 1.8 * (seq_imu * 1.0 + k * dt_imu)
                    ax = 0.09 * math.sin(fase) + random.gauss(0, 0.01)
                    ay = 0.05 * math.cos(fase) + random.gauss(0, 0.01)
                    az = 1.0 + 0.14 * math.sin(2 * fase) + random.gauss(0, 0.01)
                lote.append({
                    "ax": round(ax, 3), "ay": round(ay, 3), "az": round(az, 3),
                    "gx": round(random.gauss(0, 0.4), 2),
                    "gy": round(random.gauss(0, 0.4), 2),
                    "gz": round(random.gauss(0, 0.6), 2),
                    "roll": round(random.gauss(0, 0.8), 2),
                    "pitch": round(random.gauss(0, 0.8), 2),
                    "yaw": round(rumo, 2),
                })
            cliente.publish(f"estacao/{args.dev}/imu",
                            json.dumps({"seq": seq_imu, "dt": round(dt_imu, 4),
                                        "m": lote}))
            seq_imu += 1

            # --- GPS: 1 Hz, com ruido -----------------------------------
            hdop = round(random.uniform(0.8, 1.6), 2)
            ruido = RUIDO_GPS_M * hdop
            lat, lon = metros_para_graus(
                trajeto.norte + random.gauss(0, ruido),
                trajeto.leste + random.gauss(0, ruido))
            cliente.publish(f"estacao/{args.dev}/pos", json.dumps({
                "seq": seq_pos, "fix": True,
                "lat": round(lat, 7), "lon": round(lon, 7),
                "alt": round(918.0 + random.gauss(0, 3), 1),
                "vel": 0.0 if args.parado else round(VELOCIDADE_MS * 3.6, 2),
                "sat": random.randint(7, 11), "hdop": hdop,
                "rumo": round(LADOS[trajeto.lado][0], 1),
            }), qos=1)
            seq_pos += 1

            # --- ambiente: a cada 2 s -----------------------------------
            if seq_pos % 2 == 0:
                temp = max(15.0, min(35.0, temp + random.gauss(0, 0.05)))
                umid = max(20.0, min(95.0, umid + random.gauss(0, 0.15)))
                cliente.publish(f"estacao/{args.dev}/amb", json.dumps({
                    "seq": seq_amb, "temp": round(temp, 1),
                    "umid": round(umid, 1)}), qos=1)
                seq_amb += 1

            print(f"\rseq {seq_pos}   norte {trajeto.norte:7.1f} m   "
                  f"leste {trajeto.leste:7.1f} m   T {temp:.1f} C   "
                  f"hdop {hdop:.2f}   ", end="", flush=True)

            time.sleep(max(0.0, 1.0 - (time.monotonic() - inicio)))
    except KeyboardInterrupt:
        print("\nencerrando.")
    finally:
        cliente.publish(topico_status, '{"online":false}', qos=1, retain=True)
        time.sleep(0.2)
        cliente.loop_stop()
        cliente.disconnect()


if __name__ == "__main__":
    main()

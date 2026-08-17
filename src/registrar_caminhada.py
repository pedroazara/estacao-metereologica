#!/usr/bin/env python3
"""
Registra uma caminhada crua, para analise offline.

    python3 registrar_caminhada.py                    # grava ate Ctrl+C
    python3 registrar_caminhada.py --minutos 10       # para sozinho
    python3 registrar_caminhada.py --saida ../data/volta1.csv

POR QUE GRAVAR CRU

Nada de filtro aqui. O arquivo guarda exatamente o que o sensor disse, para
que qualquer filtro possa ser testado depois sobre a MESMA caminhada - sem
precisar sair andando de novo a cada ajuste de parametro. Comparar variantes
so faz sentido com a entrada identica.

A COLUNA gps_novo E ESSENCIAL

O GPS fala a ~1 Hz, o IMU a ~18 Hz. Lendo os registradores de posicao a cada
amostra, o mesmo fix aparece repetido varias vezes. Alimentar um Kalman com o
mesmo fix como se fossem medidas independentes o deixa artificialmente
confiante - o erro cai por multiplicacao de informacao que nao existe.

Por isso cada linha marca gps_novo = 1 apenas quando lat/lon realmente
mudaram desde a leitura anterior. Na analise, use so essas.

RESSALVA MEDIDA: este firmware parece atualizar a coordenada continuamente, e
nao em degraus limpos de epoca - parado na bancada, lat/lon mudavam na 7a casa
decimal a CADA leitura. Entao gps_novo separa leituras repetidas, mas nao
garante uma epoca de GNSS por linha. Para a analise isso significa que os
fixes sao correlacionados entre si; tratar cada linha como medida
independente ainda superestima a informacao disponivel.

MARCAR EVENTOS

Aperte ENTER durante a gravacao para marcar o instante (a coluna marca recebe
um numero incremental). Serve para anotar "aqui virei a esquina", "aqui
parei", e depois conferir se o filtro pegou.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import select
import sys
import time

from wtgahrs3 import (ENDERECO_PADRAO, ESCALA_ACC_G, ESCALA_ANGULO,
                      ESCALA_GYRO_DPS, REG_AX, REG_LONL, REG_SVNUM, WTGAHRS3,
                      ddmm_para_graus, listar_portas, procurar_sensor, s16, s32)

COLUNAS = [
    "t",            # segundos desde o inicio (monotonico)
    "relogio",      # hora local, para casar com anotacoes suas
    # --- IMU, toda amostra ---
    "ax", "ay", "az",           # g
    "gx", "gy", "gz",           # graus/s
    "roll", "pitch", "yaw",     # graus
    # --- GPS, repetido ate mudar ---
    "gps_novo",                 # 1 = fix inedito nesta linha
    "lat", "lon", "alt_m", "vel_kmh", "cog", "sat", "hdop", "pdop", "vdop",
    "lat_bruto", "lon_bruto",   # int32 crus, para depurar a conversao
    "marca",                    # incrementa quando voce aperta ENTER
]


def ler_imu(sensor) -> dict:
    v = [s16(x) for x in sensor._ler(REG_AX, 12)]
    return {
        "ax": v[0] / 32768 * ESCALA_ACC_G,
        "ay": v[1] / 32768 * ESCALA_ACC_G,
        "az": v[2] / 32768 * ESCALA_ACC_G,
        "gx": v[3] / 32768 * ESCALA_GYRO_DPS,
        "gy": v[4] / 32768 * ESCALA_GYRO_DPS,
        "gz": v[5] / 32768 * ESCALA_GYRO_DPS,
        "roll": v[9] / 32768 * ESCALA_ANGULO,
        "pitch": v[10] / 32768 * ESCALA_ANGULO,
        "yaw": v[11] / 32768 * ESCALA_ANGULO,
    }


def ler_gps(sensor) -> dict:
    g = sensor._ler(REG_LONL, 8)      # LonL,LonH,LatL,LatH,Height,Yaw,VL,VH
    d = sensor._ler(REG_SVNUM, 4)     # SVNUM, PDOP, HDOP, VDOP
    lon_bruto = s32(g[0], g[1])
    lat_bruto = s32(g[2], g[3])
    return {
        "lat": ddmm_para_graus(lat_bruto),
        "lon": ddmm_para_graus(lon_bruto),
        "alt_m": s16(g[4]) / 10.0,
        "cog": s16(g[5]) / 100.0,
        "vel_kmh": s32(g[6], g[7]) / 1000.0,
        "sat": d[0],
        "pdop": d[1] / 100.0,
        "hdop": d[2] / 100.0,
        "vdop": d[3] / 100.0,
        "lat_bruto": lat_bruto,
        "lon_bruto": lon_bruto,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Grava uma caminhada crua para analise")
    # "auto" porque o adaptador troca de numero (ttyUSB0 -> ttyUSB1) sempre que
    # e replugado - e isso acontece justamente ao montar o equipamento para sair.
    p.add_argument("--porta", default="auto")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--endereco", type=lambda x: int(x, 0), default=ENDERECO_PADRAO)
    p.add_argument("--saida", default=None, help="CSV de saida")
    p.add_argument("--minutos", type=float, default=0.0,
                   help="0 = grava ate Ctrl+C")
    # 2 e nao 4: o GNSS deste modulo atualiza a 5 Hz, e com ciclos=4 a leitura
    # caia para 3,8 Hz - abaixo da taxa da fonte, perdendo epocas por aliasing.
    # Com 2, o GPS e lido a ~7 Hz, acima dos 5 Hz de origem.
    p.add_argument("--ciclos-gps", type=int, default=2, dest="ciclos_gps",
                   help="ler o GPS a cada N amostras de IMU")
    args = p.parse_args()

    saida = args.saida or os.path.join(
        os.path.dirname(__file__), "..", "data",
        time.strftime("caminhada-%Y%m%d-%H%M%S.csv"))
    os.makedirs(os.path.dirname(os.path.abspath(saida)), exist_ok=True)

    if args.porta == "auto":
        print("procurando o sensor...")
        sensor = procurar_sensor(bauds=[args.baud], enderecos=(args.endereco,),
                                 verbose=False)
        if sensor is None:
            sensor = procurar_sensor(verbose=False)   # tenta outros bauds
        if sensor is None:
            print("nenhum sensor respondeu. Portas vistas:", listar_portas() or "nenhuma")
            return 1
        print(f"sensor em {sensor.porta} @ {sensor.baudrate} baud")
    else:
        try:
            sensor = WTGAHRS3(args.porta, endereco=args.endereco, baudrate=args.baud)
        except Exception as e:
            print(f"nao consegui abrir {args.porta}: {e}")
            return 1
        if not sensor.esta_respondendo():
            print(f"sensor nao responde em {args.porta} @ {args.baud}")
            return 1

    print(f"gravando em {os.path.abspath(saida)}")
    print("ENTER marca um evento | Ctrl+C encerra\n")

    f = open(saida, "w", newline="")
    w = csv.DictWriter(f, fieldnames=COLUNAS)
    w.writeheader()

    gps = {k: "" for k in ("lat", "lon", "alt_m", "vel_kmh", "cog", "sat",
                           "hdop", "pdop", "vdop", "lat_bruto", "lon_bruto")}
    ultimo_fix = None
    marca = 0
    marcar_ok = sys.stdin.isatty()
    n = n_gps = 0
    t0 = time.monotonic()
    fim = t0 + args.minutos * 60 if args.minutos > 0 else float("inf")

    try:
        while time.monotonic() < fim:
            # ENTER sem bloquear: marca o instante atual. Em EOF (entrada
            # redirecionada de /dev/null, por exemplo) o select acusa pronto
            # para sempre - por isso a marcacao se desliga em vez de disparar
            # a cada volta do laco.
            if marcar_ok and select.select([sys.stdin], [], [], 0)[0]:
                if sys.stdin.readline() == "":
                    marcar_ok = False
                else:
                    marca += 1
                    print(f"\n  >> marca {marca} em t={time.monotonic()-t0:.1f}s")

            try:
                linha = ler_imu(sensor)
            except Exception:
                continue

            novo = 0
            if n % args.ciclos_gps == 0:
                try:
                    g = ler_gps(sensor)
                    # so conta como fix inedito se a coordenada mudou
                    chave = (g["lat_bruto"], g["lon_bruto"])
                    if chave != ultimo_fix:
                        ultimo_fix = chave
                        novo = 1
                        n_gps += 1
                    gps = g
                except Exception:
                    pass

            t = time.monotonic() - t0
            linha.update(gps)
            linha["t"] = f"{t:.4f}"
            linha["relogio"] = time.strftime("%H:%M:%S")
            linha["gps_novo"] = novo
            linha["marca"] = marca
            w.writerow(linha)
            n += 1

            if n % 20 == 0:
                f.flush()   # nao perder a caminhada se algo travar
                v = gps.get("vel_kmh") or 0.0
                sat = gps.get("sat") or 0
                print(f"\r  {t:6.1f}s | {n:6d} amostras | {n_gps:4d} fixes"
                      f" | {v:5.1f} km/h | {sat:2} sat | marcas {marca}   ",
                      end="", flush=True)
    except KeyboardInterrupt:
        print("\n\nencerrado por Ctrl+C")
    finally:
        f.flush()
        f.close()
        sensor.fechar()

    dur = time.monotonic() - t0
    print(f"\n{n} amostras de IMU ({n/max(dur,1e-9):.1f} Hz)"
          f" e {n_gps} fixes de GPS em {dur:.0f}s")
    print(f"salvo em {os.path.abspath(saida)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

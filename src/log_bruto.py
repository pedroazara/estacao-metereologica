#!/usr/bin/env python3
"""
Registra os valores CRUS de posicao e identifica qual codificacao o firmware
usou em cada amostra.

O WTGAHRS3 tem um bug de sinal: normalmente codifica

    B (bug)     valor = -graus*1e7 + minutos*1e5

mas foi observada ao menos uma amostra com a codificacao correta

    A (correta) valor = -(graus*1e7 + minutos*1e5)

Este script conta quantas amostras usam cada uma. Minutos >= 60 sao impossiveis
e denunciam qual codificacao NAO foi usada. Quando as duas dao minutos validos,
a amostra e marcada AMBIGUA.

    python3 log_bruto.py --porta /dev/ttyUSB0 --minutos 5
"""

import argparse
import csv
import math
import os
import signal
import sys
import time
from collections import Counter
from datetime import datetime

from wtgahrs3 import ENDERECO_PADRAO, REG_LONL, REG_SVNUM, WTGAHRS3, s32

_parar = False


def _handler(signum, frame):
    global _parar
    _parar = True


def decodificar_A(v: int) -> tuple:
    """NMEA correta: |v| = graus*1e7 + minutos*1e5."""
    s = -1 if v < 0 else 1
    a = abs(v)
    dd = a // 10_000_000
    mm = (a % 10_000_000) / 100_000.0
    return s * (dd + mm / 60.0), mm


def decodificar_B(v: int) -> tuple:
    """Quirk do firmware: sinal so nos graus."""
    dd = math.floor(v / 10_000_000)
    mm = (v - dd * 10_000_000) / 100_000.0
    s = -1 if dd < 0 else 1
    return s * (abs(dd) + mm / 60.0), mm


def classificar(v: int) -> str:
    _, mmA = decodificar_A(v)
    _, mmB = decodificar_B(v)
    okA, okB = mmA < 60, mmB < 60
    if okA and okB:
        return "AMBIGUO"
    if okB:
        return "B"
    if okA:
        return "A"
    return "INVALIDO"


def main() -> int:
    p = argparse.ArgumentParser(description="Log de valores crus de posicao")
    p.add_argument("--porta", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--endereco", default=hex(ENDERECO_PADRAO))
    p.add_argument("--minutos", type=float, default=5.0, help="duracao em minutos")
    p.add_argument("--csv", default="../data/bruto.csv")
    args = p.parse_args()

    signal.signal(signal.SIGINT, _handler)

    sensor = WTGAHRS3(args.porta, endereco=int(args.endereco, 0), baudrate=args.baud)
    if not sensor.esta_respondendo():
        print("Sensor nao responde.")
        return 1

    os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
    arq = open(args.csv, "w", newline="", encoding="utf-8")
    w = csv.writer(arq)
    w.writerow(["timestamp", "lat_bruto", "lon_bruto", "cod_lat", "cod_lon",
                "latA", "lonA", "latB", "lonB", "sat", "hdop"])

    print(f"Registrando por {args.minutos} min. Ctrl+C para parar antes.\n")
    print("  #   lat_bruto     lon_bruto     cod_lat   cod_lon   sat")
    print("  " + "-" * 62)

    fim = time.monotonic() + args.minutos * 60
    contagem = Counter()
    n = 0
    anomalias = []

    while not _parar and time.monotonic() < fim:
        try:
            g = sensor._ler(REG_LONL, 8)
            d = sensor._ler(REG_SVNUM, 4)
        except Exception as e:
            print(f"  ! {e}")
            time.sleep(0.5)
            continue

        lon_b = s32(g[0], g[1])
        lat_b = s32(g[2], g[3])
        cl, co = classificar(lat_b), classificar(lon_b)
        latA, _ = decodificar_A(lat_b)
        lonA, _ = decodificar_A(lon_b)
        latB, _ = decodificar_B(lat_b)
        lonB, _ = decodificar_B(lon_b)

        n += 1
        contagem[cl] += 1
        w.writerow([datetime.now().isoformat(timespec="milliseconds"),
                    lat_b, lon_b, cl, co,
                    round(latA, 7), round(lonA, 7), round(latB, 7), round(lonB, 7),
                    d[0], d[2]])
        arq.flush()

        marca = ""
        if cl == "A":
            marca = "   <<< CODIFICACAO CORRETA (anomalia)"
            anomalias.append((n, lat_b, lon_b))
        print(f"  {n:3d}  {lat_b:>12d}  {lon_b:>12d}   {cl:8s} {co:8s}  {d[0]:2d}{marca}")

        time.sleep(1)

    sensor.fechar()
    arq.close()

    print()
    print("=" * 66)
    print("RESUMO")
    print("=" * 66)
    print(f"  amostras: {n}")
    for k, v in contagem.most_common():
        print(f"    latitude codificada como {k:9s}: {v:4d}  ({100*v/max(n,1):.1f}%)")
    print()
    if contagem["A"] == 0:
        print("  O firmware usou SEMPRE a codificacao B (bug).")
        print("  O driver atual esta correto. Nenhuma mudanca necessaria.")
    elif contagem["B"] == 0:
        print("  O firmware usou SEMPRE a codificacao A (correta).")
        print("  O driver precisa VOLTAR para a formula original.")
    else:
        print(f"  O firmware ALTERNA entre as duas codificacoes.")
        print(f"  {contagem['A']} amostra(s) em A, {contagem['B']} em B.")
        print("  O driver precisa detectar a codificacao por amostra.")
        print("\n  Amostras anomalas:")
        for i, la, lo in anomalias[:20]:
            print(f"    #{i}  lat={la}  lon={lo}")
    print(f"\n  CSV: {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

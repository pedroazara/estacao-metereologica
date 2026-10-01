#!/usr/bin/env python3
"""
Media de ruido (Leq) a partir do dB instantaneo que o ESP32 manda pelo
serial - uma leitura por linha (dB = a*log10(amplitude) + b, calculado no
firmware/teste_ky038 com os coeficientes da calibracao).

    python3 src/media_ruido_ky038.py --porta /dev/ttyUSB0 --janela 60

POR QUE NAO E SO A MEDIA ARITMETICA DOS dB
--------------------------------------------
dB e escala logaritmica de energia sonora. A media que importa fisicamente
- chamada Leq, nivel equivalente continuo - e a media da POTENCIA, nao do
numero em dB direto:

    Leq = 10 * log10( media(10^(dB_i/10)) )

Uma media aritmetica simples dos dB subestima picos (um grito curto some na
conta); a Leq pondera a energia de verdade, e e assim que medidores de
ruido ambiental calculam o nivel medio de um periodo.

Este script so integra o Leq localmente e imprime - ainda nao manda nada
pra base da estacao. Isso e um passo separado (ver ble_estacao.h e
docs/04-telemetria.md: o cru precisa sobreviver, entao integrar isso na
telemetria de verdade exige decidir onde o dB instantaneo fica guardado,
nao so a media).
"""

from __future__ import annotations

import argparse
import math
import sys
import time

import serial


def main() -> int:
    p = argparse.ArgumentParser(description="Calcula o Leq (nivel medio de ruido) a partir do dB instantaneo do serial")
    p.add_argument("--porta", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--janela", type=float, default=60.0, help="segundos de integracao por media (Leq)")
    args = p.parse_args()

    try:
        porta = serial.Serial(args.porta, args.baud, timeout=1)
    except serial.SerialException as e:
        print(f"Nao consegui abrir {args.porta}: {e}")
        return 1

    potencias = []
    inicio_janela = time.monotonic()

    print(f"Integrando Leq a cada {args.janela:.0f}s. Ctrl+C para parar.\n")
    try:
        while True:
            linha = porta.readline().decode("utf-8", errors="ignore").strip()
            if linha:
                try:
                    db = float(linha)
                    potencias.append(10 ** (db / 10))
                except ValueError:
                    pass

            agora = time.monotonic()
            if agora - inicio_janela >= args.janela:
                if potencias:
                    leq = 10 * math.log10(sum(potencias) / len(potencias))
                    hora = time.strftime("%H:%M:%S")
                    print(f"{hora}  Leq({args.janela:.0f}s) = {leq:.1f} dB   ({len(potencias)} amostras)")
                potencias = []
                inicio_janela = agora
    except KeyboardInterrupt:
        print("\nInterrompido.")
    finally:
        porta.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

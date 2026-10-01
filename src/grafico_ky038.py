#!/usr/bin/env python3
"""
Grafico ao vivo de uma leitura numerica vinda do serial do ESP32 (ex: a
amplitude do KY-038), com eixo Y FIXO - ao contrario do Serial Plotter do
Arduino IDE, que reajusta a escala sozinho e atrapalha enxergar o pico
quando o sinal volta pra valores baixos.

    python3 src/grafico_ky038.py --porta /dev/ttyUSB0 --ymax 500

O firmware do lado do ESP32 so precisa mandar um numero por linha (e o que
o sketch de teste do KY-038 ja faz, imprimindo a amplitude por janela).
"""

from __future__ import annotations

import argparse
import sys
from collections import deque

import matplotlib.pyplot as plt
import serial
from matplotlib.animation import FuncAnimation


def main() -> int:
    p = argparse.ArgumentParser(description="Plota ao vivo uma leitura numerica do serial, com eixo Y fixo")
    p.add_argument("--porta", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--ymin", type=float, default=0)
    p.add_argument("--ymax", type=float, default=500, help="ajusta conforme o range que voce vir no seu sensor")
    p.add_argument("--janela", type=int, default=200, help="quantas amostras ficam visiveis na tela")
    args = p.parse_args()

    try:
        porta = serial.Serial(args.porta, args.baud, timeout=1)
    except serial.SerialException as e:
        print(f"Nao consegui abrir {args.porta}: {e}")
        return 1

    valores = deque([0.0] * args.janela, maxlen=args.janela)

    fig, ax = plt.subplots()
    (linha,) = ax.plot(valores)
    ax.set_ylim(args.ymin, args.ymax)
    ax.set_xlim(0, args.janela)
    ax.set_xlabel("amostra")
    ax.set_ylabel("leitura")
    ax.set_title(f"{args.porta} @ {args.baud} baud  (eixo Y fixo: {args.ymin}-{args.ymax})")

    def atualizar(_frame):
        while porta.in_waiting:
            bruto = porta.readline().decode("utf-8", errors="ignore").strip()
            if not bruto:
                continue
            try:
                valores.append(float(bruto))
            except ValueError:
                continue
        linha.set_ydata(valores)
        return (linha,)

    anim = FuncAnimation(fig, atualizar, interval=30, cache_frame_data=False)
    try:
        plt.show()
    finally:
        porta.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

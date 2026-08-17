#!/usr/bin/env python3
"""
Confere as convencoes de sinal deste exemplar do WTGAHRS3.

    python3 verificar_eixos.py

Os sinais vivem em filtro_kalman.py (SINAL_ROLL, SINAL_GZ). Este script diz se
os valores la configurados batem com o hardware que voce tem em maos - a
documentacao da WitMotion nao fecha com o que o modulo entrega, e o sinal varia
entre modelos.

Um sinal trocado nao e um detalhe: no nivelamento, inclinar o sensor passa a
injetar gravidade nos eixos horizontais para o lado ERRADO, o que da o DOBRO
do erro de simplesmente ignorar a inclinacao. No rumo, o mapa gira ao contrario.
"""

from __future__ import annotations

import sys
import time

from filtro_kalman import SINAL_GZ, SINAL_ROLL
from wtgahrs3 import (ENDERECO_PADRAO, ESCALA_ANGULO, ESCALA_GYRO_DPS, REG_AX,
                      WTGAHRS3, listar_portas, procurar_sensor, s16)


def ler(sensor):
    v = [s16(x) for x in sensor._ler(REG_AX, 12)]
    return {
        "gz": v[5] / 32768 * ESCALA_GYRO_DPS,
        "roll": v[9] / 32768 * ESCALA_ANGULO,
        "pitch": v[10] / 32768 * ESCALA_ANGULO,
    }


def media(sensor, segundos=1.5):
    acum = {"gz": 0.0, "roll": 0.0, "pitch": 0.0}
    n = 0
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        try:
            d = ler(sensor)
        except Exception:
            continue
        for k in acum:
            acum[k] += d[k]
        n += 1
        time.sleep(0.05)
    return {k: v / max(n, 1) for k, v in acum.items()}


def esperar(msg):
    print(msg)
    try:
        input("  ENTER quando estiver na posicao... ")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)


def main() -> int:
    sensor = procurar_sensor(verbose=False)
    if sensor is None:
        print("sensor nao encontrado. Portas:", listar_portas() or "nenhuma")
        return 1
    print(f"sensor em {sensor.porta} @ {sensor.baudrate} baud\n")
    print("Deixe o sensor numa superficie plana, com a face de cima para cima,")
    print("e escolha uma borda para chamar de FRENTE. Mantenha essa referencia.\n")

    # -- ROLL ---------------------------------------------------------------
    print("=" * 68)
    print("  ROLL  (inclinacao lateral)")
    print("=" * 68)
    esperar("\n  1) Deixe o sensor NIVELADO, parado.\n")
    base = media(sensor)
    print(f"     roll nivelado: {base['roll']:+.2f} graus")

    esperar("\n  2) Incline o sensor para a DIREITA (borda direita para baixo),\n"
            "     uns 30 graus, e segure.\n")
    dir_ = media(sensor)
    d_roll = dir_["roll"] - base["roll"]
    print(f"     roll inclinado: {dir_['roll']:+.2f}  (variacao {d_roll:+.2f})")

    if abs(d_roll) < 8:
        print("\n  >> Variacao pequena demais. Incline mais e repita.")
        roll_ok = None
    else:
        # Convencao assumida em _R_nivel: roll positivo = borda DIREITA para
        # baixo. Se o sensor reporta negativo nessa situacao, esta invertido.
        precisa_inverter = d_roll < 0
        esperado = SINAL_ROLL < 0
        roll_ok = (precisa_inverter == esperado)
        print(f"\n     inclinando para a direita, o roll foi "
              f"{'NEGATIVO' if d_roll < 0 else 'POSITIVO'}")
        print(f"     -> este exemplar {'PRECISA' if precisa_inverter else 'NAO precisa'}"
              f" de inversao")
        print(f"     -> filtro_kalman.SINAL_ROLL = {SINAL_ROLL:+.0f}"
              f"  ({'CONFERE' if roll_ok else 'ESTA ERRADO'})")
        if not roll_ok:
            print(f"     -> corrija para SINAL_ROLL = {-SINAL_ROLL:+.0f}")

    # -- GZ / rumo ----------------------------------------------------------
    print("\n" + "=" * 68)
    print("  GZ  (giro em torno do eixo vertical)")
    print("=" * 68)
    esperar("\n  Nivele o sensor de novo. Ao apertar ENTER, gire-o no sentido\n"
            "  HORARIO (visto de cima) uns 90 graus, em ~2 s, e pare.\n")
    print("  girando... ", end="", flush=True)
    integral = 0.0
    t0 = time.monotonic()
    ant = t0
    while time.monotonic() - t0 < 3.0:
        try:
            d = ler(sensor)
        except Exception:
            continue
        agora = time.monotonic()
        integral += d["gz"] * (agora - ant)
        ant = agora
        time.sleep(0.03)
    print("ok")
    print(f"     gz integrado no giro horario: {integral:+.1f} graus")

    if abs(integral) < 20:
        print("\n  >> Rotacao pequena demais ou nao detectada. Repita girando mais rapido.")
        gz_ok = None
    else:
        # Azimute cresce no sentido horario. Se gz saiu NEGATIVO num giro
        # horario, ele precisa ser negado para virar taxa de azimute.
        precisa_inverter = integral < 0
        esperado = SINAL_GZ < 0
        gz_ok = (precisa_inverter == esperado)
        print(f"     -> este exemplar {'PRECISA' if precisa_inverter else 'NAO precisa'}"
              f" de inversao")
        print(f"     -> filtro_kalman.SINAL_GZ = {SINAL_GZ:+.0f}"
              f"  ({'CONFERE' if gz_ok else 'ESTA ERRADO'})")
        if not gz_ok:
            print(f"     -> corrija para SINAL_GZ = {-SINAL_GZ:+.0f}")

    sensor.fechar()
    print()
    if roll_ok and gz_ok:
        print("Tudo confere com o que esta em filtro_kalman.py.")
    elif roll_ok is None or gz_ok is None:
        print("Algum teste ficou inconclusivo - repita com movimentos maiores.")
    else:
        print("Ajuste as constantes indicadas em src/filtro_kalman.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

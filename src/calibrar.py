#!/usr/bin/env python3
"""
Calibracao do acelerometro do WTGAHRS3 (corrige roll e pitch).

    python3 calibrar.py --porta /dev/ttyUSB0

NAO existe calibracao de bussola neste modelo. O WTGAHRS3 usa o chip JY61P,
que e de 6 eixos - acelerometro e giroscopio, sem magnetometro. O yaw vem da
integracao do giroscopio: parte de zero onde o sensor ligou e acumula deriva.
O proprio datasheet registra isso na tabela de precisao de rumo:
"Heading accuracy: 6-axis algorithm, static 0.5 (Dynamic integral cumulative
error exists)". Para rumo geografico, use o rumo do GPS (course over ground).

O giroscopio se autocalibra e nao tem comando de calibracao.

Protocolo (WitMotion):
  - desbloqueio: registrador KEY   (0x69) <- 0xB588
  - modo:        registrador CALSW (0x01) <- 0x01 calibracao do acelerometro
                                             0x00 operacao normal
  - gravar:      registrador SAVE  (0x00) <- 0x0000

O sensor volta a travar ~10 s apos o ultimo comando, entao cada escrita e
precedida de um novo desbloqueio.
"""

import argparse
import sys
import time

from wtgahrs3 import (ENDERECO_PADRAO, ESCALA_ACC_G, ESCALA_ANGULO, REG_AX,
                      REG_CALSW, WTGAHRS3, s16)

CAL_NORMAL = 0x00
CAL_ACELEROMETRO = 0x01


def ler_bloco(sensor) -> dict:
    """Le aceleracao e angulos numa unica transacao Modbus."""
    v = [s16(x) for x in sensor._ler(REG_AX, 12)]
    return {
        "ax": v[0] / 32768 * ESCALA_ACC_G,
        "ay": v[1] / 32768 * ESCALA_ACC_G,
        "az": v[2] / 32768 * ESCALA_ACC_G,
        "roll": v[9] / 32768 * ESCALA_ANGULO,
        "pitch": v[10] / 32768 * ESCALA_ANGULO,
    }


def barra(frac: float, largura: int = 24) -> str:
    n = int(max(0.0, min(1.0, frac)) * largura)
    return "[" + "#" * n + "." * (largura - n) + "]"


def media_estavel(sensor, segundos: float = 2.0) -> dict:
    """Media de varias leituras, para o valor nao depender de um instante."""
    acum, n = {"ax": 0.0, "ay": 0.0, "az": 0.0, "roll": 0.0, "pitch": 0.0}, 0
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        try:
            d = ler_bloco(sensor)
        except Exception:
            continue
        for k in acum:
            acum[k] += d[k]
        n += 1
        time.sleep(0.05)
    return {k: v / max(n, 1) for k, v in acum.items()}


def calibrar_acelerometro(sensor, segundos: float = 5.0) -> bool:
    print()
    print("=" * 66)
    print("  CALIBRACAO DO ACELEROMETRO  (corrige roll e pitch)")
    print("=" * 66)
    print("""
  Coloque o sensor numa superficie PLANA e NIVELADA, na mesma posicao em
  que ele vai ficar montado. Ele precisa estar completamente PARADO -
  nao encoste nem no cabo.

  O que acontece: parado e nivelado, a unica aceleracao existente e a
  gravidade, e ela aponta so no eixo Z. Tudo que sobrar em X e Y e viés
  do sensor, e vira offset a subtrair. E por isso que a superficie
  precisa estar mesmo nivelada - se estiver torta, o firmware vai gravar
  a inclinacao da mesa como se fosse erro do sensor.
""")
    if input("  Pronto? Enter para comecar, ou 'n' para cancelar: ").strip().lower() == "n":
        return False

    print("\n  medindo o estado atual...")
    antes = media_estavel(sensor, 2.0)
    print(f"  antes:  X={antes['ax']:+.4f}  Y={antes['ay']:+.4f}  Z={antes['az']:+.4f} g"
          f"   roll={antes['roll']:+.2f}  pitch={antes['pitch']:+.2f}")

    sensor.desbloquear()
    sensor._escrever(REG_CALSW, CAL_ACELEROMETRO)
    time.sleep(0.1)

    inicio = time.monotonic()
    while (t := time.monotonic() - inicio) < segundos:
        print(f"\r  calibrando {barra(t/segundos)} {segundos-t:4.1f}s ", end="", flush=True)
        time.sleep(0.1)
    print(f"\r  calibrando {barra(1.0)}  ok    ")

    sensor.desbloquear()
    sensor._escrever(REG_CALSW, CAL_NORMAL)
    time.sleep(0.1)
    sensor.salvar()
    time.sleep(0.5)

    print("  verificando...")
    d = media_estavel(sensor, 2.0)
    print(f"  depois: X={d['ax']:+.4f}  Y={d['ay']:+.4f}  Z={d['az']:+.4f} g"
          f"   roll={d['roll']:+.2f}  pitch={d['pitch']:+.2f}")

    ok_xy = abs(d["ax"]) < 0.05 and abs(d["ay"]) < 0.05
    ok_z = 0.90 < d["az"] < 1.10
    ok_ang = abs(d["roll"]) < 1.5 and abs(d["pitch"]) < 1.5

    print()
    print(f"  X e Y proximos de 0 (< 0,05 g)   {'OK' if ok_xy else 'FALHOU'}")
    print(f"  Z proximo de 1 g                 {'OK' if ok_z else 'FALHOU'}")
    print(f"  roll e pitch proximos de 0       {'OK' if ok_ang else 'FALHOU'}")

    if ok_xy and ok_z and ok_ang:
        print("\n  Calibracao gravada na memoria do sensor - persiste ao desligar.")
        return True

    print("\n  Se falhou, verifique:")
    print("    - a superficie estava mesmo nivelada? (use um nivel, ou o app do celular)")
    print("    - o sensor mexeu durante os 5 segundos?")
    print("    - o cabo estava fazendo forca sobre ele?")
    print("  Corrija e repita - cada calibracao sobrescreve a anterior.")
    return False


def main() -> int:
    p = argparse.ArgumentParser(
        description="Calibracao do acelerometro do WTGAHRS3",
        epilog="Este modelo nao tem magnetometro, entao nao ha calibracao de bussola.")
    p.add_argument("--porta", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--endereco", default=hex(ENDERECO_PADRAO))
    p.add_argument("--tempo", type=float, default=5.0, help="duracao da calibracao, em segundos")
    args = p.parse_args()

    try:
        sensor = WTGAHRS3(args.porta, endereco=int(args.endereco, 0), baudrate=args.baud)
        if not sensor.esta_respondendo():
            raise RuntimeError("sem resposta")
    except Exception as e:
        print(f"ERRO ao conectar: {e}")
        print("Rode `python3 scan_barramento.py` para achar os parametros certos.")
        return 1

    print(f"Sensor OK em {args.porta} @ {args.baud}, endereco {args.endereco}")

    try:
        calibrar_acelerometro(sensor, args.tempo)
    except KeyboardInterrupt:
        print("\n\nInterrompido. Voltando o sensor ao modo normal...")
        try:
            sensor.desbloquear()
            sensor._escrever(REG_CALSW, CAL_NORMAL)
            sensor.salvar()
            print("Modo normal restaurado.")
        except Exception as e:
            print(f"Nao consegui restaurar: {e}. Desligue e religue o sensor.")
    finally:
        sensor.fechar()
    return 0


if __name__ == "__main__":
    sys.exit(main())

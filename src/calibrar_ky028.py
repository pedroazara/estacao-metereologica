#!/usr/bin/env python3
"""
Calibra o KY-028 direto dos dados, sem confiar nos valores fisicos assumidos
(resistor fixo, R0 e Beta do NTC) - eles variam demais entre clones e, se
errados, dao temperaturas bem longe da realidade (foi o que aconteceu aqui:
nenhuma das duas hipoteses batia com o RS485).

    python3 src/calibrar_ky028.py --ponto 502 23.0 --ponto 1800 34.5

Cada ponto e (leitura ADC do KY-028, temperatura real no mesmo instante -
useo RS485 como referencia).

A MATEMATICA
------------
A equacao de Beta do termistor e:

    1/T = 1/T0 + (1/Beta) * ln(R/R0)

Sem confiar no resistor fixo nem no R0 do NTC, escrevemos R como proporcional
a razao x=(adc/4095): ratio = (1-x)/x. Isso absorve as constantes fisicas
desconhecidas dentro de uma unica reta:

    1/T = a * ln(ratio) + b

Ajustando "a" e "b" por minimos quadrados com pontos reais (adc, temperatura),
a calibracao fica certa pro seu modulo especifico - e nem precisa saber a
orientacao do divisor de antemao: se a montagem for a invertida, "a" sai
negativo sozinho e a formula continua funcionando do mesmo jeito.

QUANTOS PONTOS E QUE TEMPERATURAS
-----------------------------------
Precisa de pelo menos 2 temperaturas BEM diferentes (nao so leituras
repetidas da mesma temperatura ambiente) para o ajuste fazer sentido. Deixe
o KY-028 encostado no sensor RS485 (ou bem proximo) e mude a temperatura dos
dois juntos - segurando na mao, com um secador de cabelo a distancia segura,
etc. - anotando (adc, temperatura do RS485) em cada condicao estavel.
"""

from __future__ import annotations

import argparse
import math
import sys


def ler_pontos_arquivo(caminho: str) -> list[tuple[float, float]]:
    pontos = []
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            adc_str, temp_str = linha.split(",")
            pontos.append((float(adc_str), float(temp_str)))
    return pontos


def ler_pontos_interativo() -> list[tuple[float, float]]:
    print("Digite os pares 'adc temperatura_c' (um por linha). Linha vazia termina.\n")
    pontos = []
    while True:
        try:
            entrada = input(f"  ponto {len(pontos) + 1}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not entrada:
            break
        adc_str, temp_str = entrada.split()
        pontos.append((float(adc_str), float(temp_str)))
    return pontos


def ajustar(pontos: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Minimos quadrados de 1/T = a*ln((1-x)/x) + b. Retorna (a, b, r2)."""
    xs = []
    ys = []
    for adc, temp_c in pontos:
        x = adc / 4095.0
        x = min(max(x, 0.0001), 0.9999)
        ratio = (1.0 - x) / x
        xs.append(math.log(ratio))
        ys.append(1.0 / (temp_c + 273.15))

    n = len(pontos)
    media_x = sum(xs) / n
    media_y = sum(ys) / n
    cov_xy = sum((x - media_x) * (y - media_y) for x, y in zip(xs, ys))
    var_x = sum((x - media_x) ** 2 for x in xs)

    if var_x == 0:
        raise ValueError("todos os pontos tem o mesmo adc - nao da pra ajustar uma reta")

    a = cov_xy / var_x
    b = media_y - a * media_x

    previstos = [a * x + b for x in xs]
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, previstos))
    ss_tot = sum((y - media_y) ** 2 for y in ys)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0

    return a, b, r2


def main() -> int:
    p = argparse.ArgumentParser(description="Calibra o KY-028 (1/T = a*ln((1-x)/x) + b, x=adc/4095)")
    p.add_argument(
        "--ponto",
        nargs=2,
        type=float,
        action="append",
        metavar=("ADC", "TEMP_C"),
        help="um par adc/temperatura real medido; pode repetir --ponto varias vezes",
    )
    p.add_argument("--arquivo", help="arquivo com linhas 'adc,temperatura_c'")
    args = p.parse_args()

    if args.arquivo:
        pontos = ler_pontos_arquivo(args.arquivo)
    elif args.ponto:
        pontos = [(adc, t) for adc, t in args.ponto]
    else:
        pontos = ler_pontos_interativo()

    if len(pontos) < 2:
        print("Preciso de pelo menos 2 pontos (em temperaturas diferentes) pra ajustar a curva.")
        return 1

    temps = [t for _, t in pontos]
    if max(temps) - min(temps) < 3:
        print("Aviso: os pontos cobrem uma faixa pequena de temperatura "
              f"({min(temps):.1f}C a {max(temps):.1f}C) - o ajuste tende a ficar ruim ou instavel.")
        print("Tenta incluir pontos mais espalhados (ex: sensor mais quente e mais frio).\n")

    a, b, r2 = ajustar(pontos)

    print(f"{len(pontos)} pontos usados no ajuste.")
    print(f"\n  1/T = {a:.6f} * ln((1-x)/x) + {b:.6f}   (x = adc/4095, T em kelvin)")
    print(f"\n  R^2 = {r2:.3f}  ", end="")
    if r2 < 0.9:
        print("(ajuste fraco - vale coletar mais pontos, mais espalhados em temperatura)")
    else:
        print("(ajuste razoavel)")

    print("\nNo firmware, troca a formula de temperatura por:")
    print("  float x = adc / 4095.0f;")
    print("  x = constrain(x, 0.0001f, 0.9999f);")
    print("  float ratio = (1.0f - x) / x;")
    print(f"  float tempK = 1.0f / ({a:.6f}f * logf(ratio) + {b:.6f}f);")
    print("  float tempC = tempK - 273.15f;")
    return 0


if __name__ == "__main__":
    sys.exit(main())

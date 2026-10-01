#!/usr/bin/env python3
"""
Calibra o KY-038: ajusta dB = a*log10(tensao) + b a partir de pares
(tensao medida no sensor, dB de referencia) coletados manualmente.

    python3 calibrar_ky038.py --ponto 1.2 40 --ponto 2.1 60 --ponto 2.8 75

Ou acumule os pontos num arquivo texto (uma linha "tensao,db" por medida) e
rode:

    python3 calibrar_ky038.py --arquivo pontos_ky038.csv

COMO COLETAR OS PONTOS
-----------------------
Com o sensor ligado, abra o monitor serial do ESP32 (mostrando a "tensao"
que o firmware de teste imprime) e um app medidor de dB no celular, lado a
lado. Para cada nivel de som (silencio, fala normal, grito, palma...), anote
os dois valores no mesmo instante. Quanto mais pontos espalhados na faixa
que interessa, melhor o ajuste - uns 8 a 10 ja da uma curva razoavel.

POR QUE LOGARITMO E NAO RETA
------------------------------
dB e uma escala logaritmica da amplitude do sinal, entao o modelo certo e
dB = a*log10(tensao) + b, nao uma reta em tensao. E esse "a" e "b" que este
script encontra, por minimos quadrados sobre log10(tensao) x dB medido.
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
            tensao_str, db_str = linha.split(",")
            pontos.append((float(tensao_str), float(db_str)))
    return pontos


def ler_pontos_interativo() -> list[tuple[float, float]]:
    print("Digite os pares 'tensao db' (um por linha). Linha vazia termina.\n")
    pontos = []
    while True:
        try:
            entrada = input(f"  ponto {len(pontos) + 1}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not entrada:
            break
        tensao_str, db_str = entrada.split()
        pontos.append((float(tensao_str), float(db_str)))
    return pontos


def ajustar(pontos: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Minimos quadrados de dB = a*log10(tensao) + b. Retorna (a, b, r2)."""
    xs = [math.log10(t) for t, _ in pontos]
    ys = [db for _, db in pontos]
    n = len(pontos)

    media_x = sum(xs) / n
    media_y = sum(ys) / n
    cov_xy = sum((x - media_x) * (y - media_y) for x, y in zip(xs, ys))
    var_x = sum((x - media_x) ** 2 for x in xs)

    if var_x == 0:
        raise ValueError("todos os pontos tem a mesma tensao - nao da pra ajustar uma reta")

    a = cov_xy / var_x
    b = media_y - a * media_x

    previstos = [a * x + b for x in xs]
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, previstos))
    ss_tot = sum((y - media_y) ** 2 for y in ys)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0

    return a, b, r2


def main() -> int:
    p = argparse.ArgumentParser(description="Calibra o KY-038 (dB = a*log10(tensao) + b)")
    p.add_argument(
        "--ponto",
        nargs=2,
        type=float,
        action="append",
        metavar=("TENSAO", "DB"),
        help="um par tensao/db medido; pode repetir --ponto varias vezes",
    )
    p.add_argument("--arquivo", help="arquivo com linhas 'tensao,db'")
    args = p.parse_args()

    if args.arquivo:
        pontos = ler_pontos_arquivo(args.arquivo)
    elif args.ponto:
        pontos = [(t, db) for t, db in args.ponto]
    else:
        pontos = ler_pontos_interativo()

    pontos = [(t, db) for t, db in pontos if t > 0]
    if len(pontos) < 2:
        print("Preciso de pelo menos 2 pontos com tensao > 0 pra ajustar a curva.")
        return 1

    a, b, r2 = ajustar(pontos)

    print(f"\n{len(pontos)} pontos usados no ajuste.")
    print(f"\n  dB = {a:.3f} * log10(tensao) + {b:.3f}")
    print(f"\n  R^2 = {r2:.3f}  ", end="")
    if r2 < 0.8:
        print("(ajuste fraco - vale coletar mais pontos ou revisar as medidas)")
    else:
        print("(ajuste razoavel)")

    print("\nNo firmware/codigo, troca a formula de dB por:")
    print(f"  dB = {a:.3f} * log10(tensao) + {b:.3f};")
    return 0


if __name__ == "__main__":
    sys.exit(main())

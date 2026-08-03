#!/usr/bin/env python3
"""
Varredura de forca bruta do mapa de registradores.

Le TODOS os registradores do sensor e procura, em cada par adjacente, alguma
combinacao (ordem de palavras x codificacao) que decodifique para uma posicao
proxima da referencia informada.

Use quando a posicao lida nao bate e voce ja descartou fix ruim.

    python3 varredura_registradores.py --porta /dev/ttyUSB0 --lat -21.245 --lon -45.000
"""

import argparse
import math
import sys
import time

from wtgahrs3 import ENDERECO_PADRAO, WTGAHRS3, s16, s32


def dist_km(a, b) -> float:
    dlat = (a[0] - b[0]) * 111.32
    dlon = (a[1] - b[1]) * 111.32 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlon)


def ddmm(v: int) -> float:
    """ddmm.mmmmm -> graus decimais. Retorna NaN se os minutos forem invalidos."""
    s = -1 if v < 0 else 1
    a = abs(v)
    g, m = a // 10_000_000, (a % 10_000_000) / 100_000.0
    if m >= 60.0:
        return float("nan")      # minutos >= 60 nao existem: codificacao errada
    return s * (g + m / 60.0)


CODIFICACOES = {
    "ddmm.mmmmm": ddmm,
    "graus x1e7": lambda v: v / 1e7,
    "graus x1e6": lambda v: v / 1e6,
    "graus x1e5": lambda v: v / 1e5,
}


def ler_tudo(sensor, inicio=0x00, fim=0x60, bloco=8) -> dict:
    """Le todos os registradores da faixa, tolerando falhas em blocos isolados."""
    regs = {}
    addr = inicio
    while addr <= fim:
        n = min(bloco, fim - addr + 1)
        try:
            vals = sensor._ler(addr, n)
            for i, v in enumerate(vals):
                regs[addr + i] = v
        except Exception:
            for i in range(n):
                regs.setdefault(addr + i, None)
        addr += n
        time.sleep(0.05)
    return regs


def main() -> int:
    p = argparse.ArgumentParser(description="Varredura do mapa de registradores")
    p.add_argument("--porta", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--endereco", default=hex(ENDERECO_PADRAO))
    p.add_argument("--lat", type=float, required=True, help="latitude real, ex: -21.245")
    p.add_argument("--lon", type=float, required=True, help="longitude real, ex: -45.000")
    p.add_argument("--tolerancia", type=float, default=25.0, help="erro maximo aceito, km")
    args = p.parse_args()

    ref = (args.lat, args.lon)

    sensor = WTGAHRS3(args.porta, endereco=int(args.endereco, 0), baudrate=args.baud)
    if not sensor.esta_respondendo():
        print("Sensor nao responde.")
        return 1

    print("Lendo registradores 0x00..0x60 ...")
    regs = ler_tudo(sensor)
    sensor.fechar()

    print()
    print("=" * 78)
    print("DESPEJO COMPLETO")
    print("=" * 78)
    for base in range(0x00, 0x61, 8):
        linha = f"  0x{base:02X}: "
        for i in range(8):
            v = regs.get(base + i)
            linha += "  ----" if v is None else f"  {v:04X}"
        print(linha)

    print()
    print("=" * 78)
    print(f"BUSCA POR PARES QUE DECODIFIQUEM PERTO DE {ref[0]:.5f}, {ref[1]:.5f}")
    print("=" * 78)

    # candidatos de latitude e de longitude, separadamente
    achados_lat, achados_lon = [], []

    for a in range(0x00, 0x60):
        r1, r2 = regs.get(a), regs.get(a + 1)
        if r1 is None or r2 is None:
            continue
        variantes = {
            "(L,H)": s32(r1, r2),
            "(H,L)": s32(r2, r1),
        }
        for ordem, bruto in variantes.items():
            if bruto == 0:
                continue
            for nome_cod, fn in CODIFICACOES.items():
                try:
                    val = fn(bruto)
                except Exception:
                    continue
                if val != val:      # NaN
                    continue
                if abs(val - ref[0]) * 111.32 < args.tolerancia:
                    achados_lat.append((a, ordem, nome_cod, bruto, val))
                if abs(val - ref[1]) * 111.32 * math.cos(math.radians(ref[0])) < args.tolerancia:
                    achados_lon.append((a, ordem, nome_cod, bruto, val))

    def mostrar(titulo, achados, alvo):
        print(f"\n  {titulo}  (alvo {alvo:.5f})")
        if not achados:
            print("    nenhum candidato dentro da tolerancia")
            return
        for a, ordem, cod, bruto, val in achados:
            erro = abs(val - alvo) * 111.32
            print(f"    reg 0x{a:02X}/0x{a+1:02X} {ordem} {cod:12s} bruto={bruto:>12d} "
                  f"-> {val:11.6f}  erro {erro:6.2f} km")

    mostrar("LATITUDE", achados_lat, ref[0])
    mostrar("LONGITUDE", achados_lon, ref[1])

    print()
    print("=" * 78)
    if achados_lat and achados_lon:
        print("Achou candidatos. Se lat e lon aparecem em registradores adjacentes")
        print("com a MESMA codificacao, esse e o mapa correto deste modelo.")
    else:
        print("Nenhum registrador do sensor codifica a sua posicao de referencia.")
        print("Isso significa que o receptor realmente acredita estar em outro lugar,")
        print("ou que a referencia informada nao corresponde a posicao da antena.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

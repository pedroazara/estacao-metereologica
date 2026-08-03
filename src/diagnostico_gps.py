#!/usr/bin/env python3
"""
Diagnostico de posicao: despeja os registradores CRUS do GNSS e testa todas as
decodificacoes plausiveis contra uma coordenada de referencia conhecida.

Use quando a posicao lida nao bater com onde voce realmente esta.

    python3 diagnostico_gps.py --porta /dev/ttyUSB0 --referencia -21.2453,-44.9997

A referencia pode sair do Google Maps: clique no ponto onde voce esta,
copie o par de numeros.
"""

import argparse
import math
import sys
import time

from wtgahrs3 import ENDERECO_PADRAO, WTGAHRS3, REG_LONL, REG_SVNUM, s32, ddmm_para_graus


def distancia_km(a, b) -> float:
    dlat = (a[0] - b[0]) * 111.32
    dlon = (a[1] - b[1]) * 111.32 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlon)


def decodificacoes(lon_l, lon_h, lat_l, lat_h) -> dict:
    """Todas as interpretacoes plausiveis dos 4 registradores."""
    lon_n = s32(lon_l, lon_h)   # ordem documentada: baixo, alto
    lat_n = s32(lat_l, lat_h)
    lon_s = s32(lon_h, lon_l)   # palavras trocadas
    lat_s = s32(lat_h, lat_l)

    return {
        "ddmm.mmmmm (documentado)": (ddmm_para_graus(lat_n), ddmm_para_graus(lon_n)),
        "graus decimais x1e7": (lat_n / 1e7, lon_n / 1e7),
        "palavras trocadas + ddmm": (ddmm_para_graus(lat_s), ddmm_para_graus(lon_s)),
        "palavras trocadas x1e7": (lat_s / 1e7, lon_s / 1e7),
        "lat<->lon invertidos": (ddmm_para_graus(lon_n), ddmm_para_graus(lat_n)),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Diagnostico de decodificacao de posicao")
    p.add_argument("--porta", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--endereco", default=hex(ENDERECO_PADRAO))
    p.add_argument(
        "--referencia",
        help="sua posicao real. Coordenadas negativas comecam com '-', entao use "
             "o sinal de igual: --referencia=-21.245,-45.000",
    )
    p.add_argument("--lat", type=float, help="latitude de referencia (alternativa a --referencia)")
    p.add_argument("--lon", type=float, help="longitude de referencia")
    p.add_argument("--amostras", type=int, default=10, help="quantas leituras coletar")
    args = p.parse_args()

    ref = None
    if args.lat is not None and args.lon is not None:
        ref = (args.lat, args.lon)
    elif args.referencia:
        try:
            la, lo = args.referencia.replace(" ", "").replace(";", ",").split(",")
            ref = (float(la), float(lo))
        except Exception:
            print("ERRO: --referencia deve ser 'lat,lon'. Exemplos que funcionam:")
            print("  --referencia=-21.245,-45.000")
            print("  --lat -21.245 --lon -45.000")
            return 1

    sensor = WTGAHRS3(args.porta, endereco=int(args.endereco, 0), baudrate=args.baud)
    if not sensor.esta_respondendo():
        print("Sensor nao responde. Rode scan_barramento.py.")
        return 1

    print("=" * 74)
    print("REGISTRADORES CRUS  (0x49..0x4C = Lon/Lat, 0x55..0x58 = qualidade)")
    print("=" * 74)

    ultimas = None
    for i in range(1, args.amostras + 1):
        try:
            g = sensor._ler(REG_LONL, 8)
            d = sensor._ler(REG_SVNUM, 4)
        except Exception as e:
            print(f"  leitura {i}: falhou ({e})")
            time.sleep(1)
            continue

        lon_l, lon_h, lat_l, lat_h = g[0], g[1], g[2], g[3]
        sat, pdop, hdop, vdop = d[0], d[1], d[2], d[3]

        print(
            f"[{i:2d}] LonL=0x{lon_l:04X} LonH=0x{lon_h:04X} "
            f"LatL=0x{lat_l:04X} LatH=0x{lat_h:04X} | "
            f"sat={sat:2d} pdop={pdop:5d} hdop={hdop:5d} vdop={vdop:5d}"
        )
        print(
            f"     int32:  lon={s32(lon_l, lon_h):>12d}  lat={s32(lat_l, lat_h):>12d}"
        )

        if sat > 0:
            ultimas = (lon_l, lon_h, lat_l, lat_h, sat, pdop, hdop, vdop)
        time.sleep(1)

    sensor.fechar()

    if ultimas is None:
        print("\nNenhuma amostra com satelite. Sem fix, nao da para diagnosticar.")
        return 1

    lon_l, lon_h, lat_l, lat_h, sat, pdop, hdop, vdop = ultimas

    print()
    print("=" * 74)
    print("QUALIDADE DO FIX")
    print("=" * 74)
    print(f"  satelites: {sat}")
    print(f"  HDOP cru = {hdop}  ->  /100 = {hdop/100:.2f}   /1000 = {hdop/1000:.2f}")
    if sat >= 8 and hdop / 100 > 5:
        print("  ATENCAO: muitos satelites com HDOP alto e contraditorio.")
        print("  Ou o fix ainda esta assentando, ou a escala do DOP nao e /100.")
    if hdop / 100 > 5:
        print("  HDOP > 5: posicao nao confiavel. Espere assentar antes de concluir.")

    print()
    print("=" * 74)
    print("DECODIFICACOES POSSIVEIS")
    print("=" * 74)

    cands = decodificacoes(lon_l, lon_h, lat_l, lat_h)
    if ref:
        print(f"  referencia informada: {ref[0]:.6f}, {ref[1]:.6f}\n")
        ranking = sorted(
            ((nome, v, distancia_km(v, ref)) for nome, v in cands.items()),
            key=lambda x: x[2],
        )
        for nome, (la, lo), dist in ranking:
            marca = "   <<< BATE" if dist < 5 else ("   (perto)" if dist < 50 else "")
            print(f"  {nome:28s} {la:11.6f} {lo:11.6f}   erro {dist:9.1f} km{marca}")

        melhor = ranking[0]
        print()
        if melhor[2] < 5:
            print(f"  CONCLUSAO: a decodificacao correta e '{melhor[0]}'.")
            if melhor[0] != "ddmm.mmmmm (documentado)":
                print("  O driver precisa ser ajustado - me mostre esta saida.")
            else:
                print("  O driver ja esta correto.")
        else:
            print("  CONCLUSAO: nenhuma decodificacao bate com a referencia.")
            print("  Isso aponta para fix ruim (nao para bug de formula).")
            print("  Espere o HDOP cair abaixo de 2 e rode de novo.")
    else:
        for nome, (la, lo) in cands.items():
            print(f"  {nome:28s} {la:11.6f} {lo:11.6f}")
        print("\n  Rode com --referencia lat,lon para ranquear automaticamente.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

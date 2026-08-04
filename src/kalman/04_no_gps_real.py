#!/usr/bin/env python3
"""
KALMAN - LICAO 4: aplicando no GPS de verdade

    python3 04_no_gps_real.py                    # simulacao controlada
    python3 04_no_gps_real.py --csv ../../data/trajeto.csv   # seus dados

Duas coisas que a simulacao escondia e aparecem no hardware real:

1. GRAUS NAO SAO METROS. O filtro precisa trabalhar em unidades lineares,
   entao convertemos lat/lon para um plano tangente local em metros, com
   origem no primeiro fix:

       norte = (lat - lat0) * 111320
       leste = (lon - lon0) * 111320 * cos(lat0)

   O cos(lat0) e essencial: um grau de longitude vale 111 km no equador mas
   so 103 km em Lavras, e zero nos polos. Sem ele, todo deslocamento
   leste-oeste sai 7% errado por aqui.

2. NAO DA PARA USAR O ACELEROMETRO NA PREDICAO. Ele mede no referencial do
   proprio sensor, e converter para Norte/Leste exige saber para onde o
   sensor aponta. Esse hardware nao tem magnetometro, entao essa informacao
   nao existe.

   A saida e o modelo de velocidade constante, com o acelerometro entrando
   por outra porta: modulando o Q. Quando ele detecta movimento, o filtro
   afrouxa; parado, aperta.
"""

import argparse
import csv
import math
import random
import sys
from datetime import datetime

sys.path.insert(0, "..")
from filtro_kalman import FiltroGPS


def dist_m(a, b):
    dn = (a[0] - b[0]) * 111320
    de = (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0]))
    return math.hypot(dn, de)


# ---------------------------------------------------------------------------
def simulacao(duracao=180, hz=5.0, sigma_gps=3.0, semente=3):
    """Trajeto sintetico em Lavras: reta, curva, parada, reta."""
    random.seed(semente)
    lat0, lon0 = -21.2257, -44.9774
    dt = 1.0 / hz

    kf = FiltroGPS(q=0.6, r_pos=sigma_gps**2, ganho_acc=3.0)
    verdade, bruto, filtrado = [], [], []

    n = e = 0.0
    for i in range(int(duracao * hz)):
        t = i * dt
        if t < 50:                      # reta para o norte
            vn, ve = 8.0, 0.0
        elif t < 80:                    # curva para leste
            ang = math.radians((t - 50) * 3)
            vn, ve = 8 * math.cos(ang), 8 * math.sin(ang)
        elif t < 100:                   # parado
            vn = ve = 0.0
        else:                           # reta para leste
            vn, ve = 0.0, 9.0

        n += vn * dt
        e += ve * dt
        lat = lat0 + n / 111320
        lon = lon0 + e / (111320 * math.cos(math.radians(lat0)))
        verdade.append((lat, lon))

        vel_kmh = math.hypot(vn, ve) * 3.6
        acc = 1.0 + random.gauss(0, 0.02) + (0.05 if 50 < t < 80 else 0)
        kf.informar_aceleracao(random.gauss(0, .05), random.gauss(0, .05), acc)
        kf.prever(dt)

        # GPS a 1 Hz, com ruido
        if i % int(hz) == 0:
            glat = lat + random.gauss(0, sigma_gps) / 111320
            glon = lon + random.gauss(0, sigma_gps) / (111320 * math.cos(math.radians(lat0)))
            if not kf.pronto:
                kf.iniciar(glat, glon)
            else:
                kf.corrigir_gps(glat, glon, hdop=1.0)
                if vel_kmh > 1.5:
                    rumo = (math.degrees(math.atan2(ve, vn)) + 360) % 360
                    kf.corrigir_velocidade(vel_kmh + random.gauss(0, .3), rumo)
            bruto.append((glat, glon))
        filtrado.append(kf.posicao)

    return verdade, bruto, filtrado, kf


def rodar_simulacao():
    verdade, bruto, filtrado, kf = simulacao()

    # erro do GPS bruto so pode ser medido nos instantes em que ele existe
    passo = len(verdade) // len(bruto)
    e_bruto = [dist_m(bruto[i], verdade[i * passo]) for i in range(len(bruto))]
    e_filt = [dist_m(filtrado[i * passo], verdade[i * passo]) for i in range(len(bruto))]

    def rms(v):
        return math.sqrt(sum(x * x for x in v) / len(v))

    print("=" * 70)
    print("  SIMULACAO CONTROLADA  (180 s, ruido de GPS de 3 m)")
    print("=" * 70)
    print(f"\n  Erro RMS contra a trajetoria verdadeira:")
    print(f"    GPS bruto       {rms(e_bruto):7.2f} m")
    print(f"    KALMAN          {rms(e_filt):7.2f} m")
    print(f"    melhoria        {(1 - rms(e_filt)/rms(e_bruto))*100:6.1f} %")
    print(f"\n    pior caso GPS   {max(e_bruto):7.2f} m")
    print(f"    pior caso KF    {max(e_filt):7.2f} m")
    print(f"\n  Incerteza final estimada pelo filtro: {kf.sigma_pos:.2f} m")


# ---------------------------------------------------------------------------
def rodar_csv(caminho, corte_sessao=120.0):
    """
    Reproduz um trajeto gravado.

    O CSV acumula gravacoes de varias execucoes, e um intervalo grande entre
    pontos significa que o programa foi fechado e reaberto - nao que voce
    ficou parado. Tratar isso como trajeto continuo produz um salto falso e
    contamina qualquer metrica. Entao separamos por sessao.
    """
    pontos = []
    with open(caminho, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                pontos.append((datetime.fromisoformat(r["timestamp"]),
                               float(r["latitude"]), float(r["longitude"])))
            except (KeyError, ValueError):
                continue
    if len(pontos) < 5:
        print(f"Poucos pontos em {caminho}.")
        return

    sessoes, atual = [], [pontos[0]]
    for i in range(1, len(pontos)):
        if (pontos[i][0] - pontos[i-1][0]).total_seconds() > corte_sessao:
            sessoes.append(atual)
            atual = []
        atual.append(pontos[i])
    sessoes.append(atual)

    print("=" * 70)
    print(f"  SEUS DADOS: {caminho}")
    print("=" * 70)
    print(f"\n  {len(pontos)} pontos, separados em {len(sessoes)} sessao(oes)")
    print(f"  (corte a cada intervalo maior que {corte_sessao:.0f} s)\n")

    for k, s in enumerate(sessoes, 1):
        dur = (s[-1][0] - s[0][0]).total_seconds()
        if len(s) < 5:
            print(f"  sessao {k}: {len(s):3d} pontos, {dur:6.0f} s  -> curta demais, ignorada")
            continue

        kf = FiltroGPS(q=0.6, r_pos=9.0, ganho_acc=0.0)   # o CSV nao tem IMU
        kf.iniciar(s[0][1], s[0][2])
        saida, anterior = [], s[0][0]
        for ts, lat, lon in s:
            dt = (ts - anterior).total_seconds()
            anterior = ts
            if dt > 0:
                kf.prever(min(dt, 10.0))
            kf.corrigir_gps(lat, lon, hdop=1.0)
            saida.append(kf.posicao)

        db = sum(dist_m((s[i][1], s[i][2]), (s[i-1][1], s[i-1][2]))
                 for i in range(1, len(s)))
        df = sum(dist_m(saida[i], saida[i-1]) for i in range(1, len(saida)))
        vmed = db / dur * 3.6 if dur > 0 else 0
        print(f"  sessao {k}: {len(s):3d} pontos, {dur:6.0f} s, "
              f"{vmed:5.1f} km/h medios")
        print(f"     bruto {db:8.1f} m   filtrado {df:8.1f} m   "
              f"reducao {(1-df/db)*100:6.1f} %")

    print(f"""
  COMO LER ISSO

  A reducao e o efeito esperado do filtro: somando distancias entre leituras
  cruas, cada oscilacao do GPS vira "movimento" e infla o total. O filtro
  remove esse deslocamento falso. Nao e que ele encurte o trajeto - e que o
  valor bruto estava inflado.

  Mas atencao ao avaliar com este arquivo: os pontos gravados pelo painel ja
  passaram pelo filtro de deslocamento minimo de 3 m, entao o jitter que o
  Kalman removeria ja tinha sido descartado antes de chegar ao CSV. Para
  medir o ganho de verdade, grave com --min-dist 0.
""")


def main():
    p = argparse.ArgumentParser(description="Kalman no GPS real")
    p.add_argument("--csv", help="arquivo de trajeto gravado")
    args = p.parse_args()
    if args.csv:
        rodar_csv(args.csv)
    else:
        rodar_simulacao()


if __name__ == "__main__":
    main()

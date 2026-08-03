#!/usr/bin/env python3
"""
Calibracao do WTGAHRS3: acelerometro (roll/pitch) e magnetometro (bussola).

    python3 calibrar.py --porta /dev/ttyUSB0              # sequencia guiada
    python3 calibrar.py --porta /dev/ttyUSB0 --acelerometro
    python3 calibrar.py --porta /dev/ttyUSB0 --magnetometro
    python3 calibrar.py --porta /dev/ttyUSB0 --verificar   # so testa, nao altera

Baseado no protocolo oficial da WitMotion:
  - desbloqueio: registrador KEY (0x69) <- 0xB588
  - modo:        registrador CALSW (0x01)
                   0x00 operacao normal
                   0x01 calibracao automatica do acelerometro
                   0x07 calibracao magnetica (ajuste esferico)
                   0x09 calibracao magnetica (bi-planar)
  - gravar:      registrador SAVE (0x00) <- 0x0000

O sensor volta a travar sozinho ~10 s apos o ultimo comando, entao cada escrita
e precedida de um novo desbloqueio.
"""

import argparse
import math
import statistics
import sys
import time

from wtgahrs3 import (ENDERECO_PADRAO, ESCALA_ACC_G, ESCALA_ANGULO, REG_AX,
                      REG_CALSW, WTGAHRS3, s16)

CAL_NORMAL = 0x00
CAL_ACELEROMETRO = 0x01
CAL_MAGNETICA_ESFERICA = 0x07
CAL_MAGNETICA_BIPLANAR = 0x09


def ler_bloco(sensor) -> dict:
    """Le acelerometro, magnetometro e angulos numa unica transacao."""
    v = [s16(x) for x in sensor._ler(REG_AX, 12)]
    return {
        "ax": v[0] / 32768 * ESCALA_ACC_G,
        "ay": v[1] / 32768 * ESCALA_ACC_G,
        "az": v[2] / 32768 * ESCALA_ACC_G,
        "hx": v[6], "hy": v[7], "hz": v[8],
        "roll": v[9] / 32768 * ESCALA_ANGULO,
        "pitch": v[10] / 32768 * ESCALA_ANGULO,
        "yaw": v[11] / 32768 * ESCALA_ANGULO,
    }


def entrar_modo(sensor, modo: int) -> None:
    sensor.desbloquear()
    sensor._escrever(REG_CALSW, modo)
    time.sleep(0.1)


def sair_e_salvar(sensor) -> None:
    sensor.desbloquear()
    sensor._escrever(REG_CALSW, CAL_NORMAL)
    time.sleep(0.1)
    sensor.salvar()


def barra(frac: float, largura: int = 24) -> str:
    n = int(max(0.0, min(1.0, frac)) * largura)
    return "[" + "#" * n + "." * (largura - n) + "]"


# ---------------------------------------------------------------------------
def calibrar_acelerometro(sensor, segundos: float = 5.0) -> bool:
    print()
    print("=" * 66)
    print("  CALIBRACAO DO ACELEROMETRO  (corrige roll e pitch)")
    print("=" * 66)
    print("""
  Coloque o sensor numa superficie PLANA e NIVELADA, na mesma posicao
  em que ele vai ficar montado. Ele precisa estar completamente PARADO
  durante todo o processo - nao encoste nem no cabo.

  O que acontece: o firmware assume que, parado e nivelado, a unica
  aceleracao existente e a gravidade, apontando so no eixo Z. Qualquer
  coisa que sobre nos eixos X e Y e viés, e vira offset a subtrair.
""")
    if input("  Pronto? Enter para comecar, ou 'n' para pular: ").strip().lower() == "n":
        return False

    antes = ler_bloco(sensor)
    print(f"\n  antes:  X={antes['ax']:+.4f}  Y={antes['ay']:+.4f}  Z={antes['az']:+.4f} g"
          f"   roll={antes['roll']:+.2f}  pitch={antes['pitch']:+.2f}")

    entrar_modo(sensor, CAL_ACELEROMETRO)
    inicio = time.monotonic()
    while (t := time.monotonic() - inicio) < segundos:
        print(f"\r  calibrando {barra(t/segundos)} {segundos-t:4.1f}s ", end="", flush=True)
        time.sleep(0.1)
    print(f"\r  calibrando {barra(1.0)}  ok    ")

    sair_e_salvar(sensor)
    time.sleep(0.5)

    d = ler_bloco(sensor)
    print(f"  depois: X={d['ax']:+.4f}  Y={d['ay']:+.4f}  Z={d['az']:+.4f} g"
          f"   roll={d['roll']:+.2f}  pitch={d['pitch']:+.2f}")

    ok_xy = abs(d["ax"]) < 0.05 and abs(d["ay"]) < 0.05
    ok_z = 0.90 < d["az"] < 1.10
    ok_ang = abs(d["roll"]) < 1.5 and abs(d["pitch"]) < 1.5

    print()
    print(f"  X e Y proximos de 0    {'OK' if ok_xy else 'FALHOU'}")
    print(f"  Z proximo de 1 g       {'OK' if ok_z else 'FALHOU'}")
    print(f"  roll e pitch ~ 0       {'OK' if ok_ang else 'FALHOU'}")
    if not (ok_xy and ok_z and ok_ang):
        print("\n  Se falhou: a superficie estava mesmo nivelada? O sensor mexeu?")
        print("  Repita com ele bem apoiado e imovel.")
    return ok_xy and ok_z and ok_ang


# ---------------------------------------------------------------------------
def calibrar_magnetometro(sensor, segundos: float = 35.0, biplanar: bool = False) -> bool:
    modo = CAL_MAGNETICA_BIPLANAR if biplanar else CAL_MAGNETICA_ESFERICA
    print()
    print("=" * 66)
    print("  CALIBRACAO DO MAGNETOMETRO  (corrige a bussola / yaw)")
    print("=" * 66)
    print(f"""
  ANTES DE COMECAR, afaste o sensor de:
    - notebook, celular, monitor, fonte chaveada
    - mesa ou suporte com estrutura metalica
    - a antena GPS, se ela tiver base magnetica (aquele ima e forte
      o suficiente para saturar o magnetometro sozinho)

  Durante os {segundos:.0f} segundos, gire o sensor DEVAGAR cobrindo todas as
  orientacoes: rotacione em torno do eixo X, depois do Y, depois do Z.
  Imagine que voce esta pintando a superficie de uma esfera.

  Por que girar em tudo: parado, o campo da Terra tem intensidade
  constante. Girando o sensor, as leituras (X,Y,Z) deveriam desenhar uma
  esfera centrada na origem. O firmware ajusta uma esfera aos pontos
  coletados; o quanto ela esta deslocada e o viés a corrigir. Cobrindo
  so um plano voce da um circulo, e circulo nao determina esfera.
""")
    if input("  Pronto? Enter para comecar, ou 'n' para pular: ").strip().lower() == "n":
        return False

    entrar_modo(sensor, modo)
    lim = {"hx": [1e9, -1e9], "hy": [1e9, -1e9], "hz": [1e9, -1e9]}
    inicio = time.monotonic()

    while (t := time.monotonic() - inicio) < segundos:
        try:
            d = ler_bloco(sensor)
            for eixo in ("hx", "hy", "hz"):
                lim[eixo][0] = min(lim[eixo][0], d[eixo])
                lim[eixo][1] = max(lim[eixo][1], d[eixo])
        except Exception:
            time.sleep(0.1)
            continue

        # a amplitude de cada eixo indica se aquela direcao ja foi coberta
        amp = {e: lim[e][1] - lim[e][0] for e in ("hx", "hy", "hz")}
        maior = max(amp.values()) or 1
        cob = "  ".join(f"{e[1].upper()}{barra(amp[e]/maior, 10)}" for e in amp)
        print(f"\r  {segundos-t:4.1f}s  gire devagar   {cob} ", end="", flush=True)
        time.sleep(0.08)

    print("\r" + " " * 78)
    sair_e_salvar(sensor)
    time.sleep(0.5)

    amp = {e: lim[e][1] - lim[e][0] for e in ("hx", "hy", "hz")}
    print("  amplitude coberta por eixo:")
    for e in ("hx", "hy", "hz"):
        print(f"    {e[1].upper()}: {amp[e]:6.0f}   (min {lim[e][0]:+6.0f}  max {lim[e][1]:+6.0f})")

    menor, maior = min(amp.values()), max(amp.values())
    razao = menor / maior if maior else 0
    print()
    if razao < 0.4:
        print(f"  ATENCAO: cobertura desigual (o eixo mais fraco teve so {razao*100:.0f}%")
        print("  da amplitude do mais forte). Provavelmente voce girou pouco num")
        print("  dos eixos. Vale repetir cobrindo melhor todas as direcoes.")
        return False
    print(f"  Cobertura equilibrada entre os eixos ({razao*100:.0f}%).")
    return True


# ---------------------------------------------------------------------------
def verificar_magnetometro(sensor, segundos: float = 20.0) -> bool:
    """
    Teste objetivo da calibracao magnetica.

    O campo da Terra tem intensidade constante num dado lugar. Entao, se o
    magnetometro estiver bem calibrado, o MODULO do vetor (X,Y,Z) deve
    permanecer praticamente o mesmo enquanto voce gira o sensor - so a
    direcao muda. Se o modulo varia muito, sobra viés de ferro duro/doce.
    """
    print()
    print("=" * 66)
    print("  VERIFICACAO DA BUSSOLA")
    print("=" * 66)
    print(f"""
  Gire o sensor devagar em todas as direcoes por {segundos:.0f} segundos.

  O teste: a intensidade do campo magnetico da Terra e constante aqui.
  Bem calibrado, o modulo do vetor magnetico quase nao muda enquanto voce
  gira - so a direcao. Se o modulo oscila muito, ainda ha viés.
""")
    input("  Enter para comecar: ")

    modulos, rumos = [], []
    inicio = time.monotonic()
    while (t := time.monotonic() - inicio) < segundos:
        try:
            d = ler_bloco(sensor)
        except Exception:
            time.sleep(0.1)
            continue
        m = math.sqrt(d["hx"] ** 2 + d["hy"] ** 2 + d["hz"] ** 2)
        modulos.append(m)
        rumos.append(d["yaw"])
        print(f"\r  {segundos-t:4.1f}s  |H|={m:7.0f}  yaw={d['yaw']:+7.1f}  "
              f"amostras={len(modulos)} ", end="", flush=True)
        time.sleep(0.08)
    print("\r" + " " * 78)

    if len(modulos) < 20:
        print("  Amostras insuficientes.")
        return False

    media = statistics.mean(modulos)
    cv = statistics.pstdev(modulos) / media * 100 if media else 999
    cobertura = max(rumos) - min(rumos)

    # Estimativa do viés residual, que e o numero que realmente importa.
    # Girando o sensor, o modulo medido varia entre |campo - vies| e
    # |campo + vies|. Entao:
    #     campo ~ (max + min) / 2      vies ~ (max - min) / 2
    # e o pior erro de rumo que esse vies provoca e asin(vies / campo).
    alto, baixo = max(modulos), min(modulos)
    campo = (alto + baixo) / 2
    vies = (alto - baixo) / 2
    razao = vies / campo if campo else 9
    erro_rumo = math.degrees(math.asin(min(1.0, razao)))

    print(f"  |H| entre {baixo:.0f} e {alto:.0f}   (media {media:.0f})")
    print(f"  campo estimado          : {campo:8.0f}")
    print(f"  vies residual estimado  : {vies:8.0f}   ({razao*100:.0f}% do campo)")
    print(f"  variacao do modulo (CV) : {cv:8.1f} %")
    print(f"  faixa de yaw coberta    : {cobertura:8.0f} graus")
    print()

    if cobertura < 120:
        print("  Voce girou pouco - o teste precisa de bastante variacao de rumo.")
        print("  Repita girando mais.")
        return False

    if razao >= 1.0:
        print("  MUITO RUIM: o vies e maior que o proprio campo da Terra.")
        print("  Ha algo magnetico junto do sensor. Procure e afaste antes de")
        print("  tentar calibrar de novo.")
        return False

    print(f"  ERRO DE RUMO ESTIMADO: ate {erro_rumo:.1f} graus")
    print()
    if erro_rumo < 3:
        print("  EXCELENTE. A bussola esta bem calibrada.")
        return True
    if erro_rumo < 8:
        print("  ACEITAVEL. Serve para orientacao geral, mas nao para medida fina.")
        return True
    print("  RUIM. Esse erro e grande demais para a bussola ser util.")
    print("  Possiveis causas:")
    print("    - algo metalico ou magnetico perto do sensor")
    print("    - a calibracao nao cobriu todas as orientacoes")
    print("    - a antena GPS de base magnetica esta proxima")
    return False


# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description="Calibracao do WTGAHRS3")
    p.add_argument("--porta", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--endereco", default=hex(ENDERECO_PADRAO))
    p.add_argument("--acelerometro", action="store_true", help="so o acelerometro")
    p.add_argument("--magnetometro", action="store_true", help="so o magnetometro")
    p.add_argument("--verificar", action="store_true", help="so verifica, nao altera nada")
    p.add_argument("--biplanar", action="store_true",
                   help="usa o modo magnetico bi-planar (0x09) em vez do esferico")
    p.add_argument("--tempo-mag", type=float, default=35.0, dest="tempo_mag")
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
        if args.verificar:
            verificar_magnetometro(sensor)
        elif args.acelerometro:
            calibrar_acelerometro(sensor)
        elif args.magnetometro:
            calibrar_magnetometro(sensor, args.tempo_mag, args.biplanar)
            verificar_magnetometro(sensor)
        else:
            print("\nSequencia completa: acelerometro, depois magnetometro,")
            print("depois verificacao. A configuracao e gravada no sensor.")
            calibrar_acelerometro(sensor)
            calibrar_magnetometro(sensor, args.tempo_mag, args.biplanar)
            verificar_magnetometro(sensor)
            print()
            print("=" * 66)
            print("  Calibracao gravada na memoria do sensor - persiste ao desligar.")
            print("  Se voce remontar o sensor em outro suporte, calibre de novo:")
            print("  o metal da nova montagem muda o viés magnetico.")
            print("=" * 66)
    except KeyboardInterrupt:
        print("\n\nInterrompido. Voltando o sensor ao modo normal...")
        try:
            sair_e_salvar(sensor)
            print("Modo normal restaurado.")
        except Exception as e:
            print(f"Nao consegui restaurar: {e}")
            print("Desligue e religue o sensor.")
    finally:
        sensor.fechar()

    return 0


if __name__ == "__main__":
    sys.exit(main())

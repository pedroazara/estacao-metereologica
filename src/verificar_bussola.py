#!/usr/bin/env python3
"""
Verifica empiricamente se o WTGAHRS3 tem magnetometro (bussola) de verdade.

    python3 verificar_bussola.py --porta /dev/ttyUSB0

POR QUE ESTE TESTE EXISTE
-------------------------
O documento "High-precision sensor Modbus protocol.pdf" lista os registradores
HX/HY/HZ (0x3A-0x3C), os offsets de campo magnetico (0x0B-0x0D) e os modos de
calibracao magnetica do CALSW (0x07 esferico, 0x09 bi-planar). Isso NAO prova
nada sobre este sensor: aquele PDF e o mapa de registradores generico da
WitMotion, compartilhado por toda a familia de produtos. O proprio arquivo
admite isso na nota de rodape - "The callout register is only applicable to
HWTX073". Um endereco existir na tabela nao significa que o silicio por tras
dele exista na placa.

Pior: aquele PDF nem descreve este modelo. Nele os angulos ocupam 32 bits
(LRoll/HRoll em 0x3D-0x3E) e a temperatura fica em 0x43. No WTGAHRS3 a
temperatura esta em 0x40 e os angulos sao de 16 bits - que e como o driver le,
e funciona. Ou seja, e o protocolo de outra familia.

A unica prova possivel e fisica: aplicar um estimulo que SO um magnetometro
sente e ver se os registradores respondem.

OS TRES TESTES
--------------
1. ESTATICO   - sensor parado. Mede o ruido de fundo de cada eixo. Um
                magnetometro real sempre tem algum ruido; registrador morto
                fica congelado num valor exato.

2. ROTACAO    - girar 360 graus na horizontal. O campo magnetico da Terra e
                fixo no mundo, entao no referencial do sensor ele GIRA: HX e HY
                devem descrever um circulo, e o modulo horizontal
                sqrt(HX^2+HY^2) deve ficar aproximadamente constante. Esse
                circulo e a assinatura que nenhum outro subsistema imita.

3. IMA        - aproximar um ima (ou alto-falante, ou fone de ouvido) do
                sensor. E o teste mais decisivo dos tres, porque nada mais no
                modulo - acelerometro, giroscopio, GNSS, barometro - reage a um
                campo magnetico. Se HX/HY/HZ nao se mexem com um ima encostado,
                nao ha magnetometro.

O veredito exige o teste 3. Os testes 1 e 2 servem para caracterizar e para
descartar falso negativo (por exemplo, ima fraco demais).
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time

from wtgahrs3 import ENDERECO_PADRAO, ESCALA_ANGULO, REG_AX, WTGAHRS3, s16

# Limiares do veredito. Sao folgados de proposito: a diferenca entre um
# magnetometro real e um registrador morto e de ordens de grandeza, nao de
# alguns por cento.
#
# O criterio principal e a ATIVIDADE - a fracao de leituras consecutivas em que
# o valor mudou - e nao a amplitude. A razao e um modo de falha real observado
# nesta bancada: o registrador 0x3C fica congelado por dezenas de amostras e
# entao salta 24000 LSB de uma vez, sozinho, sem estimulo nenhum. Um criterio
# baseado em amplitude aceita esse salto como "resposta ao ima" e da falso
# positivo. Atividade nao: um magnetometro real, com um ima passando por perto,
# muda em praticamente TODA leitura (atividade ~ 1,0); o salto espurio de um
# registrador morto rende atividade ~ 0,03.
ATIVIDADE_MINIMA = 0.50       # fracao de leituras que mudam sob estimulo
AMPLITUDE_MINIMA = 500        # LSB - o campo da Terra le na casa dos milhares
CIRCULARIDADE_MAXIMA = 0.35   # desvio relativo de |H| horizontal na rotacao


def ler_amostra(sensor: WTGAHRS3) -> tuple[int, int, int, float]:
    """Le HX, HY, HZ e yaw numa unica transacao Modbus (0x34..0x3F)."""
    v = [s16(x) for x in sensor._ler(REG_AX, 12)]
    return v[6], v[7], v[8], v[11] / 32768.0 * ESCALA_ANGULO


def amostrar(sensor: WTGAHRS3, segundos: float) -> list[tuple[float, int, int, int, float]]:
    """Coleta amostras por N segundos, mostrando o progresso ao vivo."""
    amostras = []
    inicio = time.monotonic()
    fim = inicio + segundos
    while time.monotonic() < fim:
        try:
            hx, hy, hz, yaw = ler_amostra(sensor)
        except Exception:
            continue
        t = time.monotonic() - inicio
        amostras.append((t, hx, hy, hz, yaw))
        restante = segundos - t
        print(
            f"\r  {restante:4.1f}s  HX={hx:7d}  HY={hy:7d}  HZ={hz:7d}  yaw={yaw:7.2f}   ",
            end="",
            flush=True,
        )
        time.sleep(0.05)
    print("\r" + " " * 70 + "\r", end="")
    return amostras


def coluna(amostras: list, i: int) -> list[float]:
    return [a[i] for a in amostras]


def amplitude(valores: list[float]) -> float:
    return max(valores) - min(valores) if valores else 0.0


def atividade(valores: list[float]) -> float:
    """
    Fracao de leituras consecutivas em que o valor mudou.

    E a metrica que separa sensor de registrador morto. Um magnetometro real
    sob estimulo muda quase sempre (~1,0). Um registrador congelado que da um
    salto isolado fica perto de 0, por maior que seja o salto.
    """
    if len(valores) < 2:
        return 0.0
    mudancas = sum(1 for a, b in zip(valores, valores[1:]) if a != b)
    return mudancas / (len(valores) - 1)


def resumo(valores: list[float]) -> dict:
    return {
        "media": statistics.fmean(valores) if valores else 0.0,
        "desvio": statistics.pstdev(valores) if len(valores) > 1 else 0.0,
        "amplitude": amplitude(valores),
        "distintos": len(set(valores)),
        "atividade": atividade(valores),
    }


def esperar(mensagem: str) -> None:
    print(mensagem)
    try:
        input("  ENTER para comecar... ")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------
def teste_estatico(sensor: WTGAHRS3, segundos: float) -> dict:
    print("=" * 70)
    print("  TESTE 1 - ESTATICO  (ruido de fundo)")
    print("=" * 70)
    esperar("\n  Deixe o sensor PARADO na mesa. Nao encoste nem no cabo.\n")

    a = amostrar(sensor, segundos)
    if not a:
        print("  Nenhuma leitura. Verifique a conexao.")
        sys.exit(1)

    eixos = {n: resumo(coluna(a, i)) for i, n in enumerate(("HX", "HY", "HZ"), start=1)}
    print(f"  {len(a)} amostras em {segundos:.0f}s\n")
    print(f"  {'eixo':6} {'media':>10} {'desvio':>9} {'amplitude':>10} {'distintos':>10} {'atividade':>10}")
    for nome, r in eixos.items():
        print(
            f"  {nome:6} {r['media']:10.1f} {r['desvio']:9.2f} {r['amplitude']:10.0f} "
            f"{r['distintos']:10d} {r['atividade']:9.0%}"
        )

    congelados = [n for n, r in eixos.items() if r["distintos"] == 1]
    if congelados:
        print(f"\n  >> {', '.join(congelados)} congelado(s) num valor exato ao longo de todas")
        print("     as amostras. Um sensor analogico real nunca faz isso.")
    print()
    return {"eixos": eixos, "atividade_max": max(r["atividade"] for r in eixos.values())}


def teste_rotacao(sensor: WTGAHRS3, segundos: float, base: dict) -> dict:
    print("=" * 70)
    print("  TESTE 2 - ROTACAO 360 GRAUS  (procura o circulo do campo terrestre)")
    print("=" * 70)
    esperar(
        f"\n  Com o sensor deitado na mesa, gire-o LENTAMENTE uma volta completa\n"
        f"  em torno do eixo vertical durante os proximos {segundos:.0f} segundos.\n"
        f"  Longe de metal, do notebook e de fontes de alimentacao.\n"
    )

    a = amostrar(sensor, segundos)
    eixos = {n: resumo(coluna(a, i)) for i, n in enumerate(("HX", "HY", "HZ"), start=1)}
    yaw = coluna(a, 4)

    print(f"  {len(a)} amostras, yaw percorreu {amplitude(yaw):.0f} graus\n")
    print(f"  {'eixo':6} {'amplitude':>10} {'atividade':>11} {'atividade parado':>18}")
    for nome, r in eixos.items():
        print(
            f"  {nome:6} {r['amplitude']:10.0f} {r['atividade']:10.0%} "
            f"{base['eixos'][nome]['atividade']:17.0%}"
        )

    # Circularidade: num magnetometro real, |H| horizontal e invariante a rotacao.
    modulos = [math.hypot(hx, hy) for _, hx, hy, _, _ in a]
    media_mod = statistics.fmean(modulos) if modulos else 0.0
    desvio_mod = statistics.pstdev(modulos) if len(modulos) > 1 else 0.0
    circ = (desvio_mod / media_mod) if media_mod > 1 else float("inf")
    print(f"\n  |H| horizontal: media {media_mod:.0f}, desvio relativo {circ:.1%}")
    print(f"  (um magnetometro real fica abaixo de {CIRCULARIDADE_MAXIMA:.0%} - o modulo")
    print("   do campo da Terra nao muda quando voce gira o sensor)")

    girou = amplitude(yaw) > 90
    if not girou:
        print("\n  >> O yaw quase nao mudou. O sensor foi mesmo girado? Repita o teste.")
    print()
    return {"eixos": eixos, "circularidade": circ, "girou": girou}


def teste_ima(sensor: WTGAHRS3, segundos: float, base: dict) -> dict:
    print("=" * 70)
    print("  TESTE 3 - IMA  (decisivo)")
    print("=" * 70)
    esperar(
        f"\n  Pegue um ima. Serve o de geladeira, mas melhor ainda um ima de fone\n"
        f"  de ouvido, alto-falante ou HD velho. Durante os proximos {segundos:.0f} segundos,\n"
        f"  passe o ima encostando na carcaca do sensor, por todos os lados.\n\n"
        f"  Nada mais no modulo reage a um ima. Se HX/HY/HZ nao se mexerem aqui,\n"
        f"  nao existe magnetometro.\n"
    )

    a = amostrar(sensor, segundos)
    colunas = {n: coluna(a, i) for i, n in enumerate(("HX", "HY", "HZ"), start=1)}
    eixos = {n: resumo(v) for n, v in colunas.items()}

    print(f"  {len(a)} amostras\n")
    print(f"  {'eixo':6} {'min':>9} {'max':>9} {'amplitude':>10} {'atividade':>11} {'parado':>9}")
    for nome, r in eixos.items():
        v = colunas[nome]
        print(
            f"  {nome:6} {min(v):9.0f} {max(v):9.0f} {r['amplitude']:10.0f} "
            f"{r['atividade']:10.0%} {base['eixos'][nome]['atividade']:8.0%}"
        )
    print()
    return {"eixos": eixos}


# ---------------------------------------------------------------------------
def veredito(base: dict, rot: dict, ima: dict) -> None:
    print("=" * 70)
    print("  VEREDITO")
    print("=" * 70)

    # Amplitude sozinha nao vale - um salto espurio isolado tambem produz
    # amplitude grande. Um eixo so conta como "respondeu" se, SOB O ESTIMULO,
    # mudar de forma continuada, com amplitude compativel com campo magnetico, e
    # mais ativo do que ele proprio ficou parado. A comparacao e eixo a eixo: um
    # HX barulhento nao pode servir de alibi para um HZ morto.
    def respondeu(teste: dict) -> tuple[bool, str]:
        for nome, r in teste["eixos"].items():
            if (
                r["atividade"] >= ATIVIDADE_MINIMA
                and r["amplitude"] >= AMPLITUDE_MINIMA
                and r["atividade"] > base["eixos"][nome]["atividade"]
            ):
                return True, f"{nome}: atividade {r['atividade']:.0%}, amplitude {r['amplitude']:.0f} LSB"
        pior = max(teste["eixos"].items(), key=lambda kv: kv[1]["amplitude"])
        return False, (
            f"melhor eixo {pior[0]}: atividade {pior[1]['atividade']:.0%}, "
            f"amplitude {pior[1]['amplitude']:.0f} LSB"
        )

    reagiu_ima, detalhe_ima = respondeu(ima)
    reagiu_giro, detalhe_giro = respondeu(rot)

    # A circularidade so significa alguma coisa se HX e HY tiverem se mexido.
    # Com um eixo congelado, |H| = sqrt(HX^2+HY^2) fica constante por construcao
    # e o teste passa trivialmente - um registrador morto "acerta" o circulo.
    horizontais_vivos = all(
        rot["eixos"][n]["amplitude"] >= AMPLITUDE_MINIMA for n in ("HX", "HY")
    )
    circular = horizontais_vivos and rot["circularidade"] <= CIRCULARIDADE_MAXIMA

    fundo = ", ".join(f"{n} {r['atividade']:.0%}" for n, r in base["eixos"].items())
    print(f"\n  atividade parado      : {fundo}   (controle, sem estimulo)")
    print(f"  respondeu ao ima      : {'SIM' if reagiu_ima else 'NAO'}  ({detalhe_ima})")
    print(f"  respondeu a rotacao   : {'SIM' if reagiu_giro else 'NAO'}  ({detalhe_giro})")
    if horizontais_vivos:
        print(f"  |H| constante no giro : {'SIM' if circular else 'NAO'}  "
              f"(desvio relativo {rot['circularidade']:.1%})")
    else:
        print("  |H| constante no giro : INCONCLUSIVO  (HX ou HY nao variou - sem")
        print("                          movimento nos dois eixos o circulo nao existe)")

    print()
    if reagiu_ima and reagiu_giro and circular:
        print("  >> TEM MAGNETOMETRO, e ele esta funcional e coerente.")
        print("     Vale rodar a calibracao magnetica (CALSW = 0x07, ajuste esferico)")
        print("     e passar a usar o yaw como rumo absoluto.")
    elif reagiu_ima and reagiu_giro:
        print("  >> TEM MAGNETOMETRO, mas o campo medido nao fecha um circulo limpo.")
        print("     Provavel distorcao hard/soft iron, ou o giro nao foi horizontal.")
        print("     Calibre (CALSW = 0x07) e repita.")
    elif reagiu_ima:
        print("  >> TEM MAGNETOMETRO. Reagiu ao ima mas nao a rotacao - suspeita de")
        print("     eixo saturado ou giro insuficiente. Repita o teste 2.")
    else:
        print("  >> NAO TEM MAGNETOMETRO.")
        print()
        print("     Os registradores 0x3A-0x3C existem no mapa Modbus mas nao estao")
        print("     ligados a nenhum sensor: nao reagem nem a um ima encostado.")
        if max(r["amplitude"] for r in ima["eixos"].values()) >= AMPLITUDE_MINIMA:
            print()
            print("     Houve salto grande de valor durante o teste do ima, mas isolado -")
            print("     o registrador ficou congelado antes e depois. Nao e leitura de")
            print("     campo, e lixo: o mesmo salto aparece com o sensor parado, sem ima.")
        print("     Bate com o chip declarado no datasheet (JY61P, 6 eixos) e com a")
        print("     nota de precisao de rumo do manual: 'Dynamic integral cumulative")
        print("     error exists' - assinatura de yaw por integracao de giroscopio.")
        print()
        print("     Para rumo geografico use o course over ground do GNSS (0x4E),")
        print("     valido apenas em movimento.")
    print()


def main() -> int:
    p = argparse.ArgumentParser(description="Confere se o WTGAHRS3 tem bussola de verdade")
    p.add_argument("--porta", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--endereco", type=lambda x: int(x, 0), default=ENDERECO_PADRAO)
    # O teste parado e o controle: precisa ser longo o bastante para capturar os
    # saltos espurios raros, senao a atividade de fundo sai subestimada.
    p.add_argument("--estatico", type=float, default=25.0, help="segundos do teste parado")
    p.add_argument("--rotacao", type=float, default=25.0, help="segundos do teste de rotacao")
    p.add_argument("--ima", type=float, default=15.0, help="segundos do teste do ima")
    args = p.parse_args()

    try:
        sensor = WTGAHRS3(args.porta, endereco=args.endereco, baudrate=args.baud)
    except Exception as e:
        print(f"Nao consegui abrir {args.porta}: {e}")
        return 1

    if not sensor.esta_respondendo():
        print(f"Sensor nao responde em {args.porta} @ {args.baud} baud, endereco 0x{args.endereco:02X}")
        sensor.fechar()
        return 1

    print(f"\nConectado em {args.porta} @ {args.baud} baud, endereco 0x{args.endereco:02X}\n")
    try:
        base = teste_estatico(sensor, args.estatico)
        rot = teste_rotacao(sensor, args.rotacao, base)
        ima = teste_ima(sensor, args.ima, base)
        veredito(base, rot, ima)
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 1
    finally:
        sensor.fechar()
    return 0


if __name__ == "__main__":
    sys.exit(main())

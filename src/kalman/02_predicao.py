#!/usr/bin/env python3
"""
KALMAN - LICAO 2: o passo de predicao, e o filtro 1D completo

    python3 02_predicao.py

Na licao 1 voce fundiu duas medidas do MESMO instante. Um filtro precisa de
mais uma coisa: saber o que acontece ENTRE uma medida e a seguinte.

O ciclo completo tem dois passos que se alternam:

  PREVER   (a cada amostra do acelerometro, ~10 Hz)
      x <- x + a*dt          onde a velocidade vai parar, integrando a aceleracao
      P <- P + Q*dt          e a incerteza CRESCE, porque a integracao deriva

  CORRIGIR (a cada leitura do GPS, ~1 Hz)
      K <- P / (P + R)       o ganho da licao 1
      x <- x + K*(z - x)     puxa a estimativa na direcao da medida
      P <- (1 - K)*P         e a incerteza DIMINUI, porque chegou informacao

A assimetria e o ponto principal da licao: prever aumenta a incerteza,
corrigir diminui. Entre duas leituras de GPS o filtro vai ficando cada vez
menos confiante, e quando o GPS chega ele ja esta "com fome" de informacao -
K sobe sozinho e ele aceita bem a medida. Se o GPS sumir por muito tempo, P
cresce sem parar e o filtro sabe que nao sabe.

Os dois parametros que voce ajusta:

  Q  quanto o acelerometro deriva por segundo, em variancia. Grande = confia
     pouco na propria previsao, segue mais o GPS. Pequeno = confia na
     previsao, filtra mais o ruido do GPS mas demora a corrigir erro.
  R  variancia do ruido do GPS. Da para MEDIR: deixe o sensor parado,
     colete velocidade por uns minutos e calcule a variancia.

Este arquivo simula um trajeto com aceleracao, cruzeiro e frenagem, e compara
tres estimativas contra a verdade: so GPS, so integracao do acelerometro, e
o filtro de Kalman.
"""

import json
import math
import random


class KalmanVelocidade:
    """
    Filtro de Kalman escalar para velocidade, em km/h.

    Estado:  x = velocidade estimada (km/h)
             P = variancia da estimativa ((km/h)^2)
    """

    def __init__(self, x0: float = 0.0, P0: float = 100.0,
                 Q: float = 0.5, R: float = 0.25):
        self.x = x0
        self.P = P0
        self.Q = Q      # densidade de ruido do processo, (km/h)^2 por segundo
        self.R = R      # variancia da medida de GPS, (km/h)^2

    def prever(self, aceleracao_ms2: float, dt: float) -> None:
        """
        Avanca o estado usando o acelerometro.

        A aceleracao vem em m/s^2 e a velocidade esta em km/h, entao o fator
        3,6 faz a conversao: 1 m/s = 3,6 km/h.

        P cresce proporcionalmente ao tempo decorrido. Isso modela o fato de
        que a integracao acumula erro: quanto mais tempo sem correcao, menos
        confiavel a previsao.
        """
        self.x += aceleracao_ms2 * 3.6 * dt
        self.P += self.Q * dt

    def corrigir(self, medida_kmh: float) -> float:
        """
        Incorpora uma leitura de GPS. Retorna o ganho K usado.

        Identico a licao 1: K = P/(P+R), so que agora P vem da predicao.
        """
        K = self.P / (self.P + self.R)
        self.x += K * (medida_kmh - self.x)
        self.P = (1 - K) * self.P
        return K


# ---------------------------------------------------------------------------
# Simulacao
# ---------------------------------------------------------------------------
def perfil_verdadeiro(t: float) -> tuple:
    """Velocidade (km/h) e aceleracao (m/s^2) reais no instante t."""
    if t < 12:            # acelerando
        a = 1.0
    elif t < 40:          # cruzeiro
        a = 0.0
    elif t < 52:          # freando
        a = -1.0
    elif t < 75:          # cruzeiro mais lento
        a = 0.0
    else:                 # acelerando de novo
        a = 0.6
    # integra o perfil para achar a velocidade verdadeira
    v, tt, passo = 0.0, 0.0, 0.01
    while tt < t:
        aa = perfil_aceleracao(tt)
        v += aa * 3.6 * passo
        tt += passo
    return max(0.0, v), a


def perfil_aceleracao(t: float) -> float:
    if t < 12:
        return 1.0
    if t < 40:
        return 0.0
    if t < 52:
        return -1.0
    if t < 75:
        return 0.0
    return 0.6


def simular(duracao=100.0, hz_imu=10.0, hz_gps=1.0,
            sigma_gps=0.5, sigma_acc=0.05, vies_acc=0.03, semente=42):
    """
    Gera dados sinteticos e roda as tres estimativas.

    sigma_gps  ruido da velocidade do GPS, km/h
    sigma_acc  ruido do acelerometro, m/s^2
    vies_acc   VIES do acelerometro, m/s^2 - o vilao da integracao pura.
               0,03 m/s^2 parece nada, mas integrado por 100 s vira
               0,03 * 3,6 * 100 = 10,8 km/h de erro acumulado.
    """
    random.seed(semente)
    dt = 1.0 / hz_imu
    passos_gps = int(hz_imu / hz_gps)

    kf = KalmanVelocidade(x0=0.0, P0=100.0, Q=0.5, R=sigma_gps ** 2)

    v_verdade = 0.0
    v_integrada = 0.0          # so acelerometro, sem correcao
    ultimo_gps = 0.0
    serie = []

    n = int(duracao * hz_imu)
    for i in range(n):
        t = i * dt
        a_real = perfil_aceleracao(t)
        v_verdade = max(0.0, v_verdade + a_real * 3.6 * dt)

        # acelerometro medido: verdade + vies + ruido
        a_medida = a_real + vies_acc + random.gauss(0, sigma_acc)

        v_integrada += a_medida * 3.6 * dt
        kf.prever(a_medida, dt)

        houve_gps = (i % passos_gps == 0)
        if houve_gps:
            ultimo_gps = max(0.0, v_verdade + random.gauss(0, sigma_gps))
            kf.corrigir(ultimo_gps)

        serie.append({
            "t": round(t, 2),
            "verdade": round(v_verdade, 4),
            "gps": round(ultimo_gps, 4) if houve_gps else None,
            "integrado": round(v_integrada, 4),
            "kalman": round(kf.x, 4),
            "sigma": round(math.sqrt(kf.P), 4),
        })

    return serie


def rms(serie, chave) -> float:
    vals = [(p[chave] - p["verdade"]) ** 2 for p in serie if p[chave] is not None]
    return math.sqrt(sum(vals) / len(vals))


def main():
    serie = simular()

    print("=" * 70)
    print("  FILTRO DE KALMAN 1D - velocidade")
    print("=" * 70)
    print("""
  Simulacao: 100 s de trajeto com aceleracao, cruzeiro, frenagem.
    GPS          1 Hz, ruido de 0,5 km/h
    Acelerometro 10 Hz, ruido de 0,05 m/s2 e VIES de 0,03 m/s2
""")

    e_gps = rms(serie, "gps")
    e_int = rms(serie, "integrado")
    e_kal = rms(serie, "kalman")

    print("  Erro RMS contra a velocidade verdadeira:")
    print(f"    so GPS                  {e_gps:7.3f} km/h")
    print(f"    so acelerometro         {e_int:7.3f} km/h")
    print(f"    KALMAN                  {e_kal:7.3f} km/h")
    print()
    print(f"    Kalman contra GPS:      {(1 - e_kal/e_gps)*100:5.1f} % melhor")
    print(f"    Kalman contra integrar: {(1 - e_kal/e_int)*100:5.1f} % melhor")

    print("\n  Evolucao do erro da integracao pura (o vies acumulando):")
    for t in (10, 25, 50, 75, 99):
        p = serie[int(t * 10)]
        print(f"    t={t:3d}s   verdade {p['verdade']:6.2f}   "
              f"integrado {p['integrado']:6.2f}  (erro {p['integrado']-p['verdade']:+6.2f})   "
              f"kalman {p['kalman']:6.2f}  (erro {p['kalman']-p['verdade']:+6.2f})")

    print("\n  Incerteza do filtro entre duas leituras de GPS:")
    print("    (P cresce na predicao, despenca quando o GPS chega)")
    for i in range(50, 61):
        p = serie[i]
        marca = "  <- GPS" if p["gps"] is not None else ""
        print(f"    t={p['t']:5.1f}s   sigma = {p['sigma']:.4f} km/h{marca}")

    with open("/tmp/kalman_serie.json", "w") as f:
        json.dump(serie, f)

    print("\n" + "=" * 70)
    print("  O que observar")
    print("=" * 70)
    print("""
  1. A integracao pura comeca boa e vai piorando. O vies de 0,03 m/s2 e
     minusculo, mas integrado nunca para de crescer. Sozinho, um
     acelerometro nao serve para navegar.

  2. O GPS nao acumula erro - o erro dele fica sempre na mesma faixa -
     mas e ruidoso e chega so 1 vez por segundo.

  3. O Kalman fica melhor que os dois. Ele usa o acelerometro para
     preencher os buracos entre leituras de GPS, e usa o GPS para impedir
     que o vies do acelerometro se acumule.

  4. Repare no sigma: sobe durante a predicao, cai quando o GPS chega.
     Esse numero e a auto-avaliacao do filtro, e sai de graca.
""")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
KALMAN - LICAO 3: estado com duas variaveis, e o filtro vira matricial

    python3 03_matricial.py

Ate agora o estado era um numero so (velocidade). Agora sao dois:

    x = [ p ]   posicao (m)
        [ v ]   velocidade (m/s)

E a incerteza deixa de ser um numero e vira uma MATRIZ 2x2:

    P = [ Ppp  Ppv ]
        [ Pvp  Pvv ]

    Ppp  o quanto duvido da posicao
    Pvv  o quanto duvido da velocidade
    Ppv  a CORRELACAO entre os dois erros   <- a novidade da licao

Essa correlacao e o motivo de valer a pena juntar as duas variaveis num
filtro so, em vez de rodar dois filtros 1D separados. Se o filtro descobre
que a velocidade estimada estava alta demais, ele sabe que a posicao
provavelmente tambem esta adiantada - porque a posicao foi construida
integrando aquela velocidade. Os erros nao sao independentes.

Consequencia pratica, e o experimento deste arquivo: o GPS aqui mede APENAS
POSICAO. A velocidade nunca e medida. Mesmo assim o filtro a estima bem,
porque a correlacao Ppv permite corrigir a velocidade a partir de um erro
observado na posicao.

--------------------------------------------------------------------------
As equacoes, agora em forma matricial:

  PREVER
      x = F x + B a
      P = F P F' + Q

      F = [ 1  dt ]   como o estado evolui sozinho:
          [ 0   1 ]   p_novo = p + v*dt      v_novo = v

      B = [ dt^2/2 ]  como a aceleracao entra
          [   dt   ]

  CORRIGIR
      y = z - H x            inovacao: o quanto a medida surpreendeu
      S = H P H' + R         incerteza da inovacao
      K = P H' S^-1          ganho de Kalman (agora um vetor 2x1)
      x = x + K y
      P = (I - K H) P

      H = [ 1  0 ]   a medida ve so a posicao

Detalhe que simplifica muito: como H tem uma linha so, S e um ESCALAR.
Nao ha inversao de matriz nenhuma neste filtro - so uma divisao.
--------------------------------------------------------------------------
"""

import json
import math
import random


# --- algebra de matrizes 2x2, escrita a mao para nao esconder nada ---------
def mat_mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(len(B))) for j in range(len(B[0]))]
            for i in range(len(A))]


def mat_transpor(A):
    return [[A[i][j] for i in range(len(A))] for j in range(len(A[0]))]


def mat_somar(A, B):
    return [[A[i][j] + B[i][j] for j in range(len(A[0]))] for i in range(len(A))]


class KalmanPosVel:
    """
    Filtro de Kalman 2D: estado [posicao, velocidade].

    q  densidade de ruido da aceleracao (m/s^2)^2 - o quanto o acelerometro
       mente. Alimenta a matriz Q.
    R  variancia da medida de posicao do GPS (m^2).
    """

    def __init__(self, p0=0.0, v0=0.0, sigma_p0=100.0, sigma_v0=10.0,
                 q=0.05, R=9.0):
        self.x = [[p0], [v0]]
        self.P = [[sigma_p0 ** 2, 0.0],
                  [0.0, sigma_v0 ** 2]]
        self.q = q
        self.R = R

    def prever(self, aceleracao, dt):
        F = [[1.0, dt],
             [0.0, 1.0]]
        B = [[dt * dt / 2.0],
             [dt]]

        # x = F x + B a
        Fx = mat_mul(F, self.x)
        self.x = [[Fx[0][0] + B[0][0] * aceleracao],
                  [Fx[1][0] + B[1][0] * aceleracao]]

        # Q do modelo de "aceleracao ruidosa": o mesmo ruido de aceleracao
        # afeta posicao e velocidade, e por isso Q tem termos fora da
        # diagonal - ja nasce correlacionado.
        Q = [[dt**4 / 4 * self.q, dt**3 / 2 * self.q],
             [dt**3 / 2 * self.q, dt**2 * self.q]]

        # P = F P F' + Q
        self.P = mat_somar(mat_mul(mat_mul(F, self.P), mat_transpor(F)), Q)

    def corrigir_posicao(self, z):
        """Incorpora uma medida de POSICAO. Retorna (K_posicao, K_velocidade)."""
        # H = [1 0], entao H x = p  e  H P H' = P[0][0]
        y = z - self.x[0][0]           # inovacao
        S = self.P[0][0] + self.R      # escalar, sem inversao de matriz

        K = [self.P[0][0] / S,         # quanto corrigir a posicao
             self.P[1][0] / S]         # quanto corrigir a VELOCIDADE
        #    ^^^^^^^^^^^^ vem da correlacao posicao-velocidade.
        #    Se Ppv fosse zero, uma medida de posicao nunca mexeria na
        #    velocidade, e o filtro nunca aprenderia a velocidade.

        self.x[0][0] += K[0] * y
        self.x[1][0] += K[1] * y

        # P = (I - K H) P,  com K H = [[K0, 0], [K1, 0]]
        P = self.P
        self.P = [[(1 - K[0]) * P[0][0], (1 - K[0]) * P[0][1]],
                  [P[1][0] - K[1] * P[0][0], P[1][1] - K[1] * P[0][1]]]
        return K

    @property
    def posicao(self):
        return self.x[0][0]

    @property
    def velocidade(self):
        return self.x[1][0]

    @property
    def sigma_p(self):
        return math.sqrt(max(self.P[0][0], 0))

    @property
    def sigma_v(self):
        return math.sqrt(max(self.P[1][1], 0))

    @property
    def correlacao(self):
        d = self.sigma_p * self.sigma_v
        return self.P[0][1] / d if d > 0 else 0.0


# ---------------------------------------------------------------------------
def aceleracao_real(t):
    if t < 15:
        return 1.2
    if t < 45:
        return 0.0
    if t < 60:
        return -1.2
    if t < 70:
        return 0.0
    return 0.8


def simular(duracao=100.0, hz_imu=10.0, hz_gps=1.0,
            sigma_gps_pos=3.0, sigma_acc=0.08, vies_acc=0.04, semente=7):
    random.seed(semente)
    dt = 1.0 / hz_imu
    passos_gps = int(hz_imu / hz_gps)

    kf = KalmanPosVel(p0=0.0, v0=0.0, q=0.05, R=sigma_gps_pos ** 2)

    p_real = v_real = 0.0
    p_int = v_int = 0.0          # integracao pura do acelerometro
    serie = []

    for i in range(int(duracao * hz_imu)):
        t = i * dt
        a = aceleracao_real(t)

        # verdade
        p_real += v_real * dt + 0.5 * a * dt * dt
        v_real = max(0.0, v_real + a * dt)

        # acelerometro medido
        a_med = a + vies_acc + random.gauss(0, sigma_acc)
        p_int += v_int * dt + 0.5 * a_med * dt * dt
        v_int += a_med * dt

        kf.prever(a_med, dt)

        z = None
        if i % passos_gps == 0:
            z = p_real + random.gauss(0, sigma_gps_pos)
            kf.corrigir_posicao(z)

        serie.append({
            "t": round(t, 2),
            "p_real": round(p_real, 3), "v_real": round(v_real, 3),
            "p_gps": round(z, 3) if z is not None else None,
            "p_int": round(p_int, 3), "v_int": round(v_int, 3),
            "p_kf": round(kf.posicao, 3), "v_kf": round(kf.velocidade, 3),
            "sig_p": round(kf.sigma_p, 3), "sig_v": round(kf.sigma_v, 3),
            "corr": round(kf.correlacao, 3),
        })
    return serie


def rms(serie, a, b):
    vals = [(p[a] - p[b]) ** 2 for p in serie if p[a] is not None]
    return math.sqrt(sum(vals) / len(vals))


def main():
    serie = simular()

    print("=" * 72)
    print("  KALMAN 2D - estado [posicao, velocidade]")
    print("=" * 72)
    print("""
  Simulacao: 100 s em linha reta, com aceleracao, cruzeiro e frenagem.
    GPS           1 Hz, mede SO POSICAO, ruido de 3 m
    Acelerometro 10 Hz, ruido 0,08 m/s2, vies 0,04 m/s2
""")

    print("  POSICAO - erro RMS (m):")
    print(f"    so GPS                  {rms(serie,'p_gps','p_real'):8.3f}")
    print(f"    so acelerometro         {rms(serie,'p_int','p_real'):8.3f}")
    print(f"    KALMAN                  {rms(serie,'p_kf','p_real'):8.3f}")

    print("\n  VELOCIDADE - erro RMS (m/s):")
    print(f"    so acelerometro         {rms(serie,'v_int','v_real'):8.3f}")
    print(f"    KALMAN                  {rms(serie,'v_kf','v_real'):8.3f}")
    print("    (o GPS nao aparece aqui: ele nunca mediu velocidade)")

    print("\n" + "=" * 72)
    print("  A CORRELACAO SE CONSTRUINDO")
    print("=" * 72)
    print("""
  No inicio o filtro nao sabe nada e Ppv = 0. Conforme ele integra a
  aceleracao, os erros de posicao e velocidade vao ficando ligados, e a
  correlacao sobe. E ela que permite corrigir a velocidade usando uma
  medida que so ve posicao.
""")
    print("     t      sigma_p    sigma_v    correlacao    K_velocidade")
    print("   " + "-" * 60)
    kf = KalmanPosVel(q=0.05, R=9.0)
    dt = 0.1
    for i in range(101):
        t = i * dt
        kf.prever(1.2, dt)
        if i % 10 == 0:
            K = kf.corrigir_posicao(0.6 * t * t)
            if i in (0, 10, 20, 40, 70, 100):
                print(f"   {t:5.1f}s   {kf.sigma_p:8.3f}   {kf.sigma_v:8.3f}   "
                      f"{kf.correlacao:9.3f}     {K[1]:8.4f}")

    print("""
  Repare no K_velocidade: ele comeca alto, porque o filtro nao faz ideia da
  velocidade e qualquer informacao ajuda. Conforme a estimativa melhora, ele
  cai - o filtro passa a confiar mais no que ja sabe.

  E repare na correlacao: fica alta e positiva. Traduzindo, o filtro
  aprendeu que "se eu errei a posicao para frente, provavelmente errei a
  velocidade para cima tambem". Dois filtros 1D separados nunca descobririam
  isso, e a velocidade ficaria a deriva.
""")

    with open("/tmp/kalman2d.json", "w") as f:
        json.dump(serie, f)

    print("=" * 72)
    print("  Por que nao ha inversao de matriz aqui")
    print("=" * 72)
    print("""
  A literatura escreve K = P H' (H P H' + R)^-1, e a inversao assusta. Mas
  quando voce mede UMA grandeza por vez, H tem uma linha so e o termo
  (H P H' + R) e um numero. Vira divisao.

  Se voce medir posicao e velocidade juntas no mesmo passo, ai S vira 2x2 e
  precisa inverter. O truque padrao e nem fazer isso: aplicar as duas
  medidas em sequencia, uma correcao escalar depois da outra. Da o mesmo
  resultado e evita a inversao inteira.
""")


if __name__ == "__main__":
    main()

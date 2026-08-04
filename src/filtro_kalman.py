"""
Fusao GPS + acelerometro para o WTGAHRS3 - versao 2.

  FiltroINS           Kalman de 6 estados movido pelo acelerometro,
                      corrigido pelo GPS. Estima o proprio vies.
  NavegacaoInercial   acelerometro puro, sem correcao. So comparacao.
  DetectorParado      detecta repouso pela vibracao do IMU.

--------------------------------------------------------------------------
COMO O SINAL DO ACELEROMETRO E INTERPRETADO (a parte que mudou)

O acelerometro mede, no referencial do sensor, a soma de tres coisas:

    medido = movimento real + gravidade projetada + vies do sensor

Separar as tres e todo o problema. A versao 2 trata cada uma explicitamente:

1. GRAVIDADE, por compensacao de inclinacao. O sensor mede roll e pitch com
   0,2 grau de precisao (referenciados pela propria gravidade - e o ponto
   forte dele). Com esses angulos, rotacionamos o vetor medido para o plano
   horizontal e subtraimos 1 g do eixo vertical. Sobram as componentes
   horizontais do movimento real + vies.

   Por que isso importa: 1 grau de inclinacao vaza 0,017 g = 0,17 m/s2 de
   gravidade para os eixos horizontais. Sem compensar, inclinar o sensor
   durante o percurso e lido como aceleracao e o filtro dispara. A versao
   anterior usava um vetor de referencia fixo, que so valia para a
   inclinacao em que foi medido.

2. VIES, estimado PELO PROPRIO FILTRO. O estado ganha duas variaveis:

       x = [ n, e, vn, ve, bn, be ]

   bn, be = vies de aceleracao no referencial de navegacao (m/s2).

   O vies e observavel atraves do GPS: se a velocidade estimada sobe
   sistematicamente entre fixes e o GPS sempre corrige para baixo, a
   correlacao construida pela matriz P atribui a diferenca ao vies - e ele
   passa a ser subtraido da predicao. O filtro se calibra sozinho, tambem
   absorvendo erros de convencao de eixos e residuos da compensacao de
   inclinacao. Modelado como random walk lento (q_bias).

3. ROTACAO PARA O MUNDO, pelo yaw ancorado no rumo do GPS. Continua sendo a
   parte fraca (sem magnetometro o yaw deriva), mas o erro que ela introduz
   e coberto por q_acc e corrigido pelo GPS a cada fix.

ZUPT: parado (detectado pela vibracao, nao pelo GPS), velocidade = 0 vira
medida a cada amostra. Alem de segurar a posicao, e quando o vies converge
mais rapido - com velocidade cravada em zero, qualquer aceleracao residual
so pode ser vies.
--------------------------------------------------------------------------
"""

from __future__ import annotations

import math

GRAUS_PARA_M = 111320.0
G = 9.80665
NX = 6      # tamanho do estado


# ---------------------------------------------------------------------------
def identidade(n):
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _R_nivel(roll_graus, pitch_graus):
    """
    Matriz corpo->nivelado: R = Rx(roll) . Ry(pitch), convencao WitMotion
    (roll gira em torno de X, pitch em torno de Y, regra da mao direita).
    """
    r = math.radians(roll_graus)
    p = math.radians(pitch_graus)
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    # Rx(r) @ Ry(p), expandida:
    return ((cp,       0.0,   sp),
            (sr * sp,  cr,   -sr * cp),
            (-cr * sp, sr,    cr * cp))


def nivelar(ax, ay, az, roll_graus, pitch_graus) -> tuple:
    """
    Compensacao de inclinacao: leva o vetor medido (em g) para o plano
    horizontal e remove a gravidade. Retorna (a_frente, a_direita) em g.

    Se a convencao de sinais do modulo real diferir da assumida, o residuo
    para uma inclinacao CONSTANTE e constante - e o estado de vies do
    filtro o absorve. O que esta funcao garante e que INCLINAR o sensor
    nao injete gravidade nova nos eixos horizontais.
    """
    R = _R_nivel(roll_graus, pitch_graus)
    a_direita = R[0][0]*ax + R[0][1]*ay + R[0][2]*az   # X nivelado
    a_frente  = R[1][0]*ax + R[1][1]*ay + R[1][2]*az   # Y nivelado
    # vertical: R[2].a, que deve dar ~1 g e e descartado
    return a_frente, a_direita


def gravidade_no_corpo(roll_graus, pitch_graus) -> tuple:
    """
    Vetor gravidade visto pelo sensor inclinado (g) - inversa exata de
    nivelar(). Usado em testes e simulacao.
    """
    R = _R_nivel(roll_graus, pitch_graus)   # ortogonal: inversa = transposta
    return (R[2][0], R[2][1], R[2][2])


def para_mundo(a_frente, a_direita, rumo_graus) -> tuple:
    """Componentes niveladas -> Norte/Leste, dado o rumo (horario a partir do N)."""
    r = math.radians(rumo_graus)
    c, s = math.cos(r), math.sin(r)
    return (a_frente * c - a_direita * s,     # Norte
            a_frente * s + a_direita * c)     # Leste


# ---------------------------------------------------------------------------
class DetectorParado:
    """
    Detecta repouso pela vibracao: parado, o desvio padrao de |a| numa
    janela de ~1 s fica em ~0,003 g; em movimento passa de 0,03 g. O GPS
    nao serve para isso - parado ele reporta ate ~1 km/h de velocidade
    fantasma, e so fala 1 vez por segundo.
    """

    def __init__(self, janela: int = 12, limiar_g: float = 0.012,
                 confirmacoes: int = 2):
        self.janela = janela
        self.limiar = limiar_g
        self.confirmacoes = confirmacoes
        self._buf = []
        self._seguidas = 0
        self.parado = False

    def amostra(self, ax, ay, az) -> bool:
        m = math.sqrt(ax * ax + ay * ay + az * az)
        self._buf.append(m)
        if len(self._buf) > self.janela:
            self._buf.pop(0)
        if len(self._buf) < self.janela:
            return self.parado
        media = sum(self._buf) / len(self._buf)
        var = sum((v - media) ** 2 for v in self._buf) / len(self._buf)
        quieto = math.sqrt(var) < self.limiar
        self._seguidas = self._seguidas + 1 if quieto else 0
        if self._seguidas >= self.confirmacoes:
            self.parado = True
        elif not quieto:
            self.parado = False
        return self.parado


# ---------------------------------------------------------------------------
class FiltroINS:
    """
    Kalman de 6 estados: posicao, velocidade e vies de aceleracao (N/E).

      q_acc   ruido de aceleracao nao modelado, (m/s2)^2/s. Cobre erro de
              rumo, vibracao e o que a compensacao de inclinacao nao pegou.
      q_bias  passeio do vies, (m/s2)^2/s. Pequeno: vies muda devagar.
      r_pos   variancia da posicao do GPS (m^2); escalada pelo HDOP.
      r_vel   variancia da velocidade do GPS (m/s)^2.
      r_zupt  variancia da medida de parada.
    """

    # Aceleracao maxima aceita. Um pedestre acelera ~1 m/s2, um carro urbano
    # ~2,5 m/s2. Valores acima disso, numa estacao movel, sao sacudida ou
    # impacto - nao deslocamento.
    ACC_MAX = 2.0          # m/s2 aceitos apos remover gravidade e vies
    VEL_MAX = 55.0         # m/s
    BIAS_MAX = 1.5         # m/s2: o vies estimado nao pode explodir
    PORTAO_SIGMA = 4.0
    PORTAO_MIN = 25.0
    TAU_SUAVIZACAO = 0.45  # s: dissolucao das correcoes na exibicao
    VEL_REVELA = 2.5       # m/s: teto da taxa de revelacao

    def __init__(self, q_acc: float = 1.5, q_bias: float = 0.005,
                 r_pos: float = 9.0, r_vel: float = 0.4, r_zupt: float = 0.04,
                 vel_parado: float = 1.5, vel_rumo_ok: float = 4.0,
                 fc_acc: float = 0.7, peso_acc: float = 1.0,
                 adaptar_por_hdop: bool = True):
        self.q_acc = q_acc
        self.q_bias = q_bias
        self.r_pos = r_pos
        self.r_vel = r_vel
        self.r_zupt = r_zupt
        self.vel_parado = vel_parado
        self.vel_rumo_ok = vel_rumo_ok
        self.fc_acc = fc_acc          # corte do passa-baixa, Hz
        self.peso_acc = peso_acc      # 0 = ignora o acelerometro, 1 = usa todo
        self.adaptar_por_hdop = adaptar_por_hdop
        self._lp = [0.0, 0.0]         # estado do passa-baixa (aN, aE)
        self._vel_gps = 0.0           # ultima velocidade reportada pelo GPS

        self.lat0 = self.lon0 = None
        self.cos_lat0 = 1.0
        self.x = [0.0] * NX
        self.P = identidade(NX)
        self.pronto = False

        self.detector = DetectorParado()
        self.parado = True
        self.yaw_ancora: float | None = None
        self.rejeitados = 0
        self._seguidos = 0
        self._resto = [0.0, 0.0]          # suavizacao de exibicao
        self.ultima_acc = (0.0, 0.0)

    # -- conversoes ---------------------------------------------------------
    def graus_para_metros(self, lat, lon):
        return ((lat - self.lat0) * GRAUS_PARA_M,
                (lon - self.lon0) * GRAUS_PARA_M * self.cos_lat0)

    def metros_para_graus(self, n, e):
        return (self.lat0 + n / GRAUS_PARA_M,
                self.lon0 + e / (GRAUS_PARA_M * self.cos_lat0))

    def iniciar(self, lat, lon, sigma_pos=5.0, sigma_vel=2.0, sigma_bias=0.3):
        self.lat0, self.lon0 = lat, lon
        self.cos_lat0 = math.cos(math.radians(lat))
        self.x = [0.0] * NX
        self.P = identidade(NX)
        for i, s in enumerate((sigma_pos, sigma_pos, sigma_vel, sigma_vel,
                               sigma_bias, sigma_bias)):
            self.P[i][i] = s * s
        self._resto = [0.0, 0.0]
        self._lp = [0.0, 0.0]
        self.pronto = True

    def ancorar_rumo(self, yaw, rumo_gps):
        if self.yaw_ancora is None:
            self.yaw_ancora = rumo_gps - yaw

    def rumo(self, yaw) -> float:
        return (yaw + (self.yaw_ancora or 0.0)) % 360

    # -- predicao -----------------------------------------------------------
    def prever(self, ax, ay, az, roll, pitch, yaw, dt):
        """ax..az em g; roll, pitch, yaw em graus; dt em segundos."""
        if not self.pronto or dt <= 0:
            return

        # O detector de vibracao sozinho nao basta: um veiculo em velocidade
        # constante numa estrada boa vibra pouco, e seria declarado parado -
        # o ZUPT entao zeraria uma velocidade que existe de verdade. O GPS tem
        # poder de veto: se ele diz que ha movimento, nao ha repouso.
        vibra_quieto = self.detector.amostra(ax, ay, az)
        imu_parado = vibra_quieto and self._vel_gps < self.vel_parado
        self.parado = imu_parado

        # 1. gravidade fora, pelo roll/pitch
        a_f, a_d = nivelar(ax, ay, az, roll, pitch)
        # 2. para o mundo, pelo rumo
        aN, aE = para_mundo(a_f * G, a_d * G, self.rumo(yaw))
        # 3. vies estimado fora
        aN -= self.x[4]
        aE -= self.x[5]

        # 4. PASSA-BAIXA. Esta e a diferenca entre um filtro utilizavel e um
        #    que sai correndo a cada esbarrao. Sacudir o sensor na mao produz
        #    aceleracao de 2 a 10 Hz; deslocamento real de pedestre ou veiculo
        #    acontece abaixo de 1 Hz. Um passa-baixa de polo unico em fc_acc
        #    separa os dois: a sacudida e atenuada e, sendo oscilatoria, o que
        #    passa ainda se cancela na integracao. A aceleracao real, sustentada,
        #    atravessa quase intacta.
        alfa = dt / (dt + 1.0 / (2 * math.pi * self.fc_acc)) if self.fc_acc > 0 else 1.0
        self._lp[0] += alfa * (aN - self._lp[0])
        self._lp[1] += alfa * (aE - self._lp[1])
        aN, aE = self._lp[0] * self.peso_acc, self._lp[1] * self.peso_acc

        if imu_parado:
            aN = aE = 0.0            # em repouso, o resto e ruido por definicao
            self._lp = [0.0, 0.0]
        else:
            lim = self.ACC_MAX
            aN = max(-lim, min(lim, aN))
            aE = max(-lim, min(lim, aE))
        self.ultima_acc = (aN, aE)

        # x = F x + B a      (F inclui o acoplamento do vies na velocidade)
        x = self.x
        meio = dt * dt / 2
        x[0] += x[2] * dt + aN * meio
        x[1] += x[3] * dt + aE * meio
        x[2] += aN * dt
        x[3] += aE * dt
        # vies: random walk, nao muda na predicao

        v = self.VEL_MAX
        x[2] = max(-v, min(v, x[2]))
        x[3] = max(-v, min(v, x[3]))
        b = self.BIAS_MAX
        x[4] = max(-b, min(b, x[4]))
        x[5] = max(-b, min(b, x[5]))

        # P = F P F' + Q, com F esparso feito a mao:
        #   F = I + dt*(pos<-vel) - dt*(vel<-vies) - meio*(pos<-vies)
        F = identidade(NX)
        F[0][2] = F[1][3] = dt
        F[0][4] = F[1][5] = -meio
        F[2][4] = F[3][5] = -dt

        P = self.P
        FP = [[sum(F[i][k] * P[k][j] for k in range(NX)) for j in range(NX)]
              for i in range(NX)]
        self.P = [[sum(FP[i][k] * F[j][k] for k in range(NX)) for j in range(NX)]
                  for i in range(NX)]

        qa, qb = self.q_acc, self.q_bias
        self.P[0][0] += dt ** 3 / 3 * qa
        self.P[1][1] += dt ** 3 / 3 * qa
        self.P[0][2] += dt ** 2 / 2 * qa
        self.P[2][0] += dt ** 2 / 2 * qa
        self.P[1][3] += dt ** 2 / 2 * qa
        self.P[3][1] += dt ** 2 / 2 * qa
        self.P[2][2] += dt * qa
        self.P[3][3] += dt * qa
        self.P[4][4] += dt * qb
        self.P[5][5] += dt * qb

        # suavizacao de exibicao: dissolve a correcao pendente com teto de taxa
        resto = math.hypot(self._resto[0], self._resto[1])
        if resto > 1e-6:
            alvo = max(resto * math.exp(-dt / self.TAU_SUAVIZACAO),
                       resto - self.VEL_REVELA * dt)
            f = max(0.0, alvo) / resto
            self._resto[0] *= f
            self._resto[1] *= f

        # ZUPT continuo em repouso: velocidade e zero a cada amostra.
        # E tambem o momento em que o vies converge mais rapido.
        if imu_parado:
            self._corrigir(2, 0.0, self.r_zupt)
            self._corrigir(3, 0.0, self.r_zupt)

    # -- correcoes ----------------------------------------------------------
    def _corrigir(self, i, z, R):
        """
        Correcao escalar. Toda correcao move a posicao (via correlacao),
        entao o salto de exibicao e capturado aqui, para qualquer medida.
        """
        y = z - self.x[i]
        S = self.P[i][i] + R
        if S <= 0:
            return
        antes = (self.x[0], self.x[1])
        K = [self.P[j][i] / S for j in range(NX)]
        for j in range(NX):
            self.x[j] += K[j] * y
        linha = list(self.P[i])
        self.P = [[self.P[r][c] - K[r] * linha[c] for c in range(NX)]
                  for r in range(NX)]
        self._resto[0] += self.x[0] - antes[0]
        self._resto[1] += self.x[1] - antes[1]

    def corrigir_gps(self, lat, lon, hdop=1.0):
        if not self.pronto:
            self.iniciar(lat, lon)
            return
        n, e = self.graus_para_metros(lat, lon)
        R = self.r_pos * (max(hdop, 0.5) ** 2 if self.adaptar_por_hdop else 1.0)

        # portao de inovacao: fix absurdamente longe da previsao e rejeitado
        dist = math.hypot(n - self.x[0], e - self.x[1])
        limite = max(self.PORTAO_MIN,
                     self.PORTAO_SIGMA * math.sqrt(max(self.P[0][0], 0)
                                                   + max(self.P[1][1], 0) + R))
        if dist > limite:
            self.rejeitados += 1
            self._seguidos += 1
            if self._seguidos >= 3:
                # varios fixes concordam entre si: o errado e o filtro
                onde_estava = self.posicao
                vies = (self.x[4], self.x[5])       # preserva o aprendido
                self.iniciar(lat, lon)
                self.x[4], self.x[5] = vies
                self._resto = [(lat - onde_estava[0]) * GRAUS_PARA_M,
                               (lon - onde_estava[1]) * GRAUS_PARA_M * self.cos_lat0]
                self._seguidos = 0
            return
        self._seguidos = 0

        self._corrigir(0, n, R)
        self._corrigir(1, e, R)

    def corrigir_velocidade(self, velocidade_kmh, rumo_graus):
        if not self.pronto:
            return
        self._vel_gps = velocidade_kmh
        self.parado = velocidade_kmh < self.vel_parado and self.detector.parado
        if velocidade_kmh < self.vel_parado:
            self._corrigir(2, 0.0, self.r_zupt)
            self._corrigir(3, 0.0, self.r_zupt)
            return
        if velocidade_kmh < self.vel_rumo_ok:
            return                     # rumo do GPS ainda e ruido
        v = velocidade_kmh / 3.6
        r = math.radians(rumo_graus)
        self._corrigir(2, v * math.cos(r), self.r_vel)
        self._corrigir(3, v * math.sin(r), self.r_vel)

    # -- saidas -------------------------------------------------------------
    @property
    def posicao(self):
        """Suavizada para exibicao; posicao_bruta da o estado exato."""
        return self.metros_para_graus(self.x[0] - self._resto[0],
                                      self.x[1] - self._resto[1])

    @property
    def posicao_bruta(self):
        return self.metros_para_graus(self.x[0], self.x[1])

    @property
    def velocidade_kmh(self):
        return math.hypot(self.x[2], self.x[3]) * 3.6

    @property
    def sigma_pos(self):
        return math.sqrt(max(self.P[0][0], 0) + max(self.P[1][1], 0))

    @property
    def vies(self):
        """Vies estimado (m/s2), para diagnostico."""
        return (self.x[4], self.x[5])


# ---------------------------------------------------------------------------
class NavegacaoInercial:
    """
    Acelerometro puro para comparacao: mesma interpretacao do sinal
    (nivelamento por roll/pitch, rotacao por yaw, vies inicial medido
    parado, ZUPT), mas SEM correcao de GPS. A deriva restante e a que o
    Kalman elimina.
    """

    def __init__(self, amostras_ref: int = 100):
        self.lat0 = self.lon0 = None
        self.cos_lat0 = 1.0
        self.n = self.e = self.vn = self.ve = 0.0
        self.pronto = False
        self.amostras_ref = amostras_ref
        self._acum = [0.0, 0.0]
        self._n = 0
        self.vies = [0.0, 0.0]          # em g, no plano nivelado
        self.calibrando = True
        self.yaw_ancora = None
        self.detector = DetectorParado()
        self._lp = [0.0, 0.0]

    def iniciar(self, lat, lon):
        self.lat0, self.lon0 = lat, lon
        self.cos_lat0 = math.cos(math.radians(lat))
        self.n = self.e = self.vn = self.ve = 0.0
        self._acum = [0.0, 0.0]
        self._n = 0
        self.calibrando = True
        self.yaw_ancora = None
        self._lp = [0.0, 0.0]
        self.pronto = True

    def ancorar_rumo(self, yaw, rumo_gps):
        if self.yaw_ancora is None:
            self.yaw_ancora = rumo_gps - yaw

    def passo(self, ax, ay, az, roll, pitch, yaw, dt):
        if not self.pronto or dt <= 0:
            return

        a_f, a_d = nivelar(ax, ay, az, roll, pitch)

        if self.calibrando:
            self._acum[0] += a_f
            self._acum[1] += a_d
            self._n += 1
            if self._n >= self.amostras_ref:
                self.vies = [self._acum[0] / self._n, self._acum[1] / self._n]
                self.calibrando = False
            return

        if self.detector.amostra(ax, ay, az):
            self.vn = self.ve = 0.0
            self._lp = [0.0, 0.0]
            return

        aN, aE = para_mundo((a_f - self.vies[0]) * G,
                            (a_d - self.vies[1]) * G,
                            (yaw + (self.yaw_ancora or 0.0)) % 360)
        # mesmo passa-baixa do Kalman, para a comparacao ser justa
        alfa = dt / (dt + 1.0 / (2 * math.pi * 0.7))
        self._lp[0] += alfa * (aN - self._lp[0])
        self._lp[1] += alfa * (aE - self._lp[1])
        aN, aE = self._lp[0], self._lp[1]
        self.vn += aN * dt
        self.ve += aE * dt
        self.n += self.vn * dt
        self.e += self.ve * dt

    @property
    def posicao(self):
        if not self.pronto:
            return (None, None)
        return (self.lat0 + self.n / GRAUS_PARA_M,
                self.lon0 + self.e / (GRAUS_PARA_M * self.cos_lat0))

    @property
    def velocidade_kmh(self):
        return math.hypot(self.vn, self.ve) * 3.6

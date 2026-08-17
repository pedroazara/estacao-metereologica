"""
Fusao GPS + IMU para o WTGAHRS3 - versao 3.

  FiltroCV            Kalman de 4 estados, modelo de velocidade constante.
                      NAO usa rumo. E o filtro de posicao.
  EstimadorRumo       rumo geografico por filtro complementar (gz cru negado
                      + direcao do deslocamento do GPS), reancorado sempre.
  MediaParado         media de fixes em repouso - o ganho real de precisao.
  NavegacaoInercial   acelerometro puro, sem correcao. So comparacao.
  DetectorParado      detecta repouso pela vibracao do IMU.

--------------------------------------------------------------------------
POR QUE A VERSAO 2 (FiltroINS) FOI REMOVIDA

A v2 girava a aceleracao medida para Norte/Leste usando o yaw, e integrava
isso na predicao. Duas medidas nesta bancada condenam a ideia:

  - o yaw do firmware andou 32 graus com o sensor PARADO na mesa, em 2 min;
  - a ancora do rumo (yaw_ancora) era calculada uma unica vez e nunca mais
    corrigida, entao o erro so crescia.

O resultado e o filtro "andando sozinho": aceleracao empurrada numa direcao
progressivamente errada, com confianca alta. Sem magnetometro (confirmado
com ima - ver src/verificar_bussola.py) nao existe rumo absoluto em repouso,
entao esse caminho nao tem conserto.

O proprio docs/03-filtro-kalman.md ja dizia isso: "Sem rotacao correta,
injetar a aceleracao na predicao PIORA o resultado".

--------------------------------------------------------------------------
A SAIDA: O MODELO DE VELOCIDADE CONSTANTE NAO PRECISA DE RUMO

Entre dois fixes de GPS, presume-se velocidade constante; toda aceleracao
real e tratada como ruido de processo. O estado e so

    x = [ n, e, vn, ve ]

e nenhuma etapa exige saber para onde o sensor aponta. O problema do yaw
simplesmente deixa de existir.

O acelerometro nao fica de fora - ele entra pelo MODULO da aceleracao
horizontal, que e invariante a rotacao no plano. Nao importa se o sensor
esta virado para o Norte ou para o Sul: |a| horizontal e o mesmo. Esse
modulo modula o Q:

    q_efetivo = q_base * (1 + ganho_acc * |a_horizontal|)

Parado ou em velocidade constante o filtro aperta e suaviza forte; em
manobra ele afrouxa e passa a seguir o GPS. E o uso honesto do IMU com a
informacao que este hardware realmente tem.

MEDIDO NESTA BANCADA (120 s parado, 18,2 Hz):
  - deriva do dead reckoning no trecho imovel: 0,18 m em 10 s, 1,26 m em 30 s
  - com os esbarroes normais de bancada: 6,8 m em 10 s, 113,8 m em 60 s
  - 92% das amostras de gz sao exatamente zero: o firmware crava o giro em
    zero em repouso, e descarta rotacoes lentas
--------------------------------------------------------------------------
"""

from __future__ import annotations

import math
from collections import deque

GRAUS_PARA_M = 111320.0
G = 9.80665
NX = 4      # estado do FiltroCV: n, e, vn, ve


def dif_angular(a, b) -> float:
    """Diferenca a - b levada para (-180, +180]. Angulos em graus."""
    return (a - b + 180.0) % 360.0 - 180.0


# ---------------------------------------------------------------------------
# Convencao de sinais deste exemplar
# ---------------------------------------------------------------------------
# O firmware da WitMotion nao segue a mesma regra de mao em todos os eixos, e a
# documentacao nao fecha com o que o hardware entrega. Em vez de espalhar
# sinais trocados pelo codigo, cada um vive aqui, num lugar so.
#
#   SINAL_ROLL  o roll sai invertido em relacao a convencao usada pelo
#               nivelamento (X para a direita, Y para a frente, Z para cima).
#               Rode src/verificar_eixos.py para confirmar no seu exemplar.
#   SINAL_GZ    o giro e anti-horario positivo e o azimute cresce no horario.
#               Medido em 604 viradas de uma caminhada real: 81% delas com gz
#               de sinal oposto a mudanca de rumo.
SINAL_ROLL = -1.0
SINAL_GZ = -1.0


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

    O roll entra multiplicado por SINAL_ROLL: este exemplar reporta o roll com
    o sinal trocado em relacao a convencao assumida aqui. Sem a correcao,
    inclinar o sensor para um lado injeta gravidade nos eixos horizontais para
    o lado ERRADO - o dobro do erro que simplesmente ignorar a inclinacao.
    """
    R = _R_nivel(SINAL_ROLL * roll_graus, pitch_graus)
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
    Detecta repouso pela vibracao. Medido nesta bancada, com o sensor imovel
    e janela de 12 amostras (~0,7 s a 18 Hz): desvio de |a| com mediana
    0,0005 g e p95 de 0,024 g. A decisao combina essa variacao com giro,
    passos e uma janela do GPS; nenhum pico isolado muda o estado.

    O GPS sozinho tambem nao basta: parado ele reporta velocidade fantasma e
    so fala algumas vezes por segundo. Aqui ele entra por mediana e histerese.
    Quando confirma velocidade zero, tem poder para vetar vibracao espuria do
    acelerometro - importante em montagem sujeita a motores ou vento.
    """

    def __init__(self, janela: int = 12, limiar_g: float = 0.025,
                 limiar_saida_g: float = 0.045,
                 limiar_giro_dps: float = 2.5,
                 limiar_saida_giro_dps: float = 5.0,
                 confirmar_parado_s: float = 1.5,
                 confirmar_movimento_s: float = 0.3):
        self.janela = janela
        self.limiar = limiar_g
        self.limiar_saida = limiar_saida_g
        self.limiar_giro = limiar_giro_dps
        self.limiar_saida_giro = limiar_saida_giro_dps
        self.confirmar_parado_s = confirmar_parado_s
        self.confirmar_movimento_s = confirmar_movimento_s
        self._buf = []
        self._buf_giro = []
        self._quieto_s = 0.0
        self._movimento_s = 0.0
        self._gps: list[tuple[float, float, float, float]] = []
        self._gps_movendo = False
        self._gps_parado = False
        self.parado = False
        self.desvio_acc_g = 0.0
        self.giro_rms_dps = 0.0

    def atualizar_gps(self, t, n, e, velocidade_kmh):
        """
        Atualiza a evidencia independente de movimento do GNSS.

        A mediana da velocidade evita que uma unica leitura fantasma tire o
        sistema do repouso. O deslocamento numa base de alguns segundos evita
        confiar apenas no campo de velocidade do receptor.
        """
        self._gps.append((t, n, e, max(0.0, velocidade_kmh)))
        while len(self._gps) > 2 and t - self._gps[0][0] > 6.0:
            self._gps.pop(0)

        velocidades = sorted(p[3] for p in self._gps[-5:])
        mediana = velocidades[len(velocidades) // 2]
        base_ok = len(self._gps) >= 2 and t - self._gps[0][0] >= 3.0
        desloc = (math.hypot(n - self._gps[0][1], e - self._gps[0][2])
                   if base_ok else 0.0)

        # Histerese: e facil entrar em "movendo" quando as duas evidencias
        # concordam, e preciso elas baixarem de verdade para voltar.
        if mediana >= 3.0 or (mediana >= 1.8 and desloc >= 2.0):
            self._gps_movendo = True
            self._gps_parado = False
        elif mediana <= 0.7 or (mediana <= 1.2
                                and (not base_ok or desloc <= 3.5)):
            self._gps_movendo = False
            self._gps_parado = len(self._gps) >= 3

    def amostra(self, ax, ay, az, gx=0.0, gy=0.0, gz=0.0, dt=0.1,
                passo_detectado=False, passos_ativos=False) -> bool:
        m = math.sqrt(ax * ax + ay * ay + az * az)
        self._buf.append(m)
        self._buf_giro.append(math.sqrt(gx * gx + gy * gy + gz * gz))
        if len(self._buf) > self.janela:
            self._buf.pop(0)
            self._buf_giro.pop(0)
        if len(self._buf) < self.janela:
            return self.parado
        media = sum(self._buf) / len(self._buf)
        var = sum((v - media) ** 2 for v in self._buf) / len(self._buf)
        self.desvio_acc_g = math.sqrt(var)
        self.giro_rms_dps = math.sqrt(
            sum(v * v for v in self._buf_giro) / len(self._buf_giro))

        giro_quieto = self.giro_rms_dps < self.limiar_giro
        imu_quieta = self.desvio_acc_g < self.limiar and giro_quieto
        # Se o GPS confirma repouso e nao ha giro nem passos coerentes, ruido
        # translacional do acelerometro nao pode manter o sistema "andando".
        quieto = ((imu_quieta or (self._gps_parado and giro_quieto))
                  and not passos_ativos and not self._gps_movendo)
        passo_forte = passos_ativos or (passo_detectado and not self._gps_parado)
        movimento_forte = (passo_forte or self._gps_movendo
                            or self.giro_rms_dps > self.limiar_saida_giro
                            or (self.desvio_acc_g > self.limiar_saida
                                and not self._gps_parado))

        dt = max(0.0, min(dt, 1.0))
        if self.parado:
            if movimento_forte:
                self.parado = False
                self._movimento_s = self._quieto_s = 0.0
            elif not quieto:
                self._movimento_s += dt
                if self._movimento_s >= self.confirmar_movimento_s:
                    self.parado = False
                    self._movimento_s = self._quieto_s = 0.0
            else:
                self._movimento_s = 0.0
        else:
            if quieto:
                self._quieto_s += dt
                if self._quieto_s >= self.confirmar_parado_s:
                    self.parado = True
                    self._quieto_s = self._movimento_s = 0.0
            else:
                self._quieto_s = 0.0
        return self.parado

    @property
    def gps_movendo(self) -> bool:
        return self._gps_movendo

    @property
    def gps_parado(self) -> bool:
        return self._gps_parado

    def reiniciar_gps(self):
        self._gps.clear()
        self._gps_movendo = False
        self._gps_parado = False


# ---------------------------------------------------------------------------
class DetectorPassos:
    """
    Conta passos pela oscilacao vertical do corpo, e nao integrando aceleracao.

    POR QUE ISTO, E NAO INTEGRAR

    Integrar a aceleracao duas vezes faz o erro crescer com t^2 - foi o que
    destruiu o FiltroINS (113,8 m de deslocamento fantasma em 120 s parado).
    Contar passos faz o erro crescer com a DISTANCIA percorrida, e a uma taxa
    de poucos por cento, porque cada passo e um evento discreto: erra-se o
    comprimento, nunca a existencia.

    MEDIDO NA CAMINHADA REAL (data/caminhada-20260806-174706.csv, 12 Hz):
      - amplitude da oscilacao de |a|: 0,0003 g parado, 0,0656 g andando.
        Tres ordens de grandeza de separacao - o limiar e folgado.
      - cadencia 1,50 passos/s, intervalo mediano 0,668 s com desvio 0,106 s.
        Caminhada e um sinal notavelmente regular.
      - 170 passos para 90,8 m de GPS: 0,534 m por passo.

    COMO FUNCIONA

    A gravidade e removida por uma media movel de ~0,8 s (o passa-alta), nao
    pelo roll/pitch: o que interessa aqui e so a oscilacao, e a media movel
    dispensa qualquer convencao de eixos. Depois, deteccao de pico com limiar e
    tempo refratario - o refratario e o que impede que o repique de um mesmo
    passo seja contado duas vezes.
    """

    def __init__(self, taxa_hz: float = 12.0, limiar_g: float = 0.035,
                 refratario_s: float = 0.28, cadencia_max_s: float = 2.0):
        self.limiar = limiar_g
        self.refratario = refratario_s
        self.cadencia_max_s = cadencia_max_s
        self.janela = max(3, int(taxa_hz * 0.8))
        self._buf: list[float] = []
        self._ant = 0.0
        self._ant2 = 0.0
        self._t = 0.0
        self._t_ultimo = -9.0
        self.passos = 0
        self.ultimo_intervalo = 0.0
        self._intervalos: deque[float] = deque(maxlen=4)

    def amostra(self, ax, ay, az, dt) -> bool:
        """Retorna True exatamente nas amostras em que um passo foi detectado."""
        if dt <= 0:
            return False
        self._t += dt
        m = math.sqrt(ax*ax + ay*ay + az*az)
        self._buf.append(m)
        if len(self._buf) > self.janela:
            self._buf.pop(0)
        if len(self._buf) < self.janela:
            return False

        osc = m - sum(self._buf) / len(self._buf)

        # pico local: o valor anterior e maior que os dois vizinhos.
        # Testamos no anterior porque so agora conhecemos o sucessor.
        pico = (self._ant > self.limiar
                and self._ant >= self._ant2
                and self._ant > osc)
        self._ant2, self._ant = self._ant, osc

        if pico and self._t - self._t_ultimo >= self.refratario:
            intervalo = self._t - self._t_ultimo
            self._t_ultimo = self._t
            self.passos += 1
            if intervalo < self.cadencia_max_s:
                self._intervalos.append(intervalo)
                ordenados = sorted(self._intervalos)
                self.ultimo_intervalo = ordenados[len(ordenados) // 2]
            else:
                # Primeiro pico depois de uma pausa inicia uma nova sequencia;
                # nunca pode ressuscitar a cadencia da caminhada anterior.
                self._intervalos.clear()
                self.ultimo_intervalo = 0.0
            return True
        return False

    @property
    def cadencia_hz(self) -> float:
        """Passos por segundo, 0 se a ultima cadencia nao for de caminhada."""
        if (self.ultimo_intervalo <= 0
                or self._t - self._t_ultimo >= self.cadencia_max_s):
            return 0.0
        return 1.0 / self.ultimo_intervalo

    @property
    def caminhada_confirmada(self) -> bool:
        """Exige tres passos e intervalos coerentes antes de liberar o PDR."""
        if len(self._intervalos) < 2 or self.cadencia_hz <= 0:
            return False
        return max(self._intervalos) / max(min(self._intervalos), 1e-6) <= 1.8

    def andando(self) -> bool:
        """True somente para uma sequencia recente e coerente de passos."""
        return self.caminhada_confirmada

    def encerrar_movimento(self):
        """Expira a cadencia sem apagar a contagem total de passos."""
        self._intervalos.clear()
        self.ultimo_intervalo = 0.0
        self._t_ultimo = self._t - self.cadencia_max_s


# ---------------------------------------------------------------------------
class FiltroCV:
    """
    Kalman de 4 estados - posicao e velocidade Norte/Leste - com modelo de
    velocidade constante. Nao usa rumo em nenhuma etapa.

      q_base     ruido de processo de base, (m/s2)^2/s.
      ganho_acc  quanto o modulo da aceleracao horizontal medida infla o Q.
      r_pos      variancia da posicao do GPS (m^2); escalada pelo HDOP.
      r_vel      variancia da velocidade do GPS (m/s)^2.
      r_zupt     variancia da medida de parada.

    O acelerometro entra so pelo modulo horizontal, que independe do rumo.
    """

    VEL_MAX = 55.0         # m/s
    PORTAO_SIGMA = 4.0
    PORTAO_MIN = 25.0
    TAU_SUAVIZACAO = 0.45  # s: dissolucao das correcoes na exibicao
    VEL_REVELA = 2.5       # m/s: teto da taxa de revelacao
    ACC_MAX_Q = 3.0        # m/s2: teto do modulo que modula o Q

    def __init__(self, q_base: float = 0.25, ganho_acc: float = 3.0,
                 r_pos: float = 9.0, r_vel: float = 0.5, r_zupt: float = 0.01,
                 vel_parado: float = 1.0, vel_rumo_ok: float = 3.0,
                 fc_acc: float = 0.7, adaptar_por_hdop: bool = True,
                 q_parado: float = 0.005):
        self.q_base = q_base
        self.ganho_acc = ganho_acc
        self.r_pos = r_pos
        self.r_vel = r_vel
        self.r_zupt = r_zupt
        self.vel_parado = vel_parado          # km/h
        self.vel_rumo_ok = vel_rumo_ok        # km/h
        self.fc_acc = fc_acc
        self.adaptar_por_hdop = adaptar_por_hdop
        self.q_parado = q_parado

        self.lat0 = self.lon0 = None
        self.cos_lat0 = 1.0
        self.x = [0.0] * NX
        self.P = identidade(NX)
        self.pronto = False

        self.detector = DetectorParado()
        self.parado = True
        self.rejeitados = 0
        self._seguidos = 0
        self._resto = [0.0, 0.0]
        self._lp_acc = 0.0                    # modulo de |a| horizontal, suavizado
        self._vel_gps = 0.0
        self.ultima_acc = 0.0
        self.entrou_parado = False

    # -- conversoes ---------------------------------------------------------
    def graus_para_metros(self, lat, lon):
        return ((lat - self.lat0) * GRAUS_PARA_M,
                (lon - self.lon0) * GRAUS_PARA_M * self.cos_lat0)

    def metros_para_graus(self, n, e):
        return (self.lat0 + n / GRAUS_PARA_M,
                self.lon0 + e / (GRAUS_PARA_M * self.cos_lat0))

    def iniciar(self, lat, lon, sigma_pos=5.0, sigma_vel=2.0):
        self.lat0, self.lon0 = lat, lon
        self.cos_lat0 = math.cos(math.radians(lat))
        self.x = [0.0] * NX
        self.P = identidade(NX)
        for i, s in enumerate((sigma_pos, sigma_pos, sigma_vel, sigma_vel)):
            self.P[i][i] = s * s
        self._resto = [0.0, 0.0]
        self._lp_acc = 0.0
        self.detector.reiniciar_gps()
        self.pronto = True

    # -- predicao -----------------------------------------------------------
    def atualizar_movimento_gps(self, t, lat, lon, velocidade_kmh):
        if not self.pronto:
            return
        n, e = self.graus_para_metros(lat, lon)
        self.detector.atualizar_gps(t, n, e, velocidade_kmh)

    def prever(self, ax, ay, az, roll, pitch, dt, gx=0.0, gy=0.0, gz=0.0,
               passo_detectado=False, passos_ativos=False):
        """
        ax..az em g; roll e pitch em graus; dt em segundos.

        Nao recebe yaw: o modelo CV nao precisa de rumo. A aceleracao so serve
        para dizer ao filtro o QUANTO desconfiar da premissa de velocidade
        constante - e para isso basta o modulo.
        """
        if not self.pronto or dt <= 0:
            return

        # O detector de vibracao sozinho nao basta: um veiculo em velocidade
        # constante numa estrada boa vibra pouco e seria declarado parado - o
        # ZUPT entao zeraria uma velocidade que existe. O GPS tem poder de veto.
        estava_parado = self.parado
        parado = self.detector.amostra(
            ax, ay, az, gx, gy, gz, dt,
            passo_detectado=passo_detectado, passos_ativos=passos_ativos)
        self.parado = parado
        self.entrou_parado = parado and not estava_parado

        # Modulo da aceleracao horizontal, apos tirar a gravidade pelo
        # roll/pitch. E invariante a rotacao no plano - por isso dispensa yaw.
        a_f, a_d = nivelar(ax, ay, az, roll, pitch)
        modulo = math.hypot(a_f, a_d) * G

        alfa = dt / (dt + 1.0 / (2 * math.pi * self.fc_acc)) if self.fc_acc > 0 else 1.0
        self._lp_acc += alfa * (modulo - self._lp_acc)
        acc = 0.0 if parado else min(self._lp_acc, self.ACC_MAX_Q)
        self.ultima_acc = acc

        # ZUPT vem ANTES da propagacao. Assim a velocidade antiga nao desloca
        # nem mais um passo temporal depois que o repouso foi confirmado.
        if parado:
            self._corrigir(2, 0.0, self.r_zupt)
            self._corrigir(3, 0.0, self.r_zupt)
            self.x[2] = self.x[3] = 0.0

        # x = F x   (velocidade constante: a posicao anda, a velocidade fica)
        x = self.x
        x[0] += x[2] * dt
        x[1] += x[3] * dt
        v = self.VEL_MAX
        x[2] = max(-v, min(v, x[2]))
        x[3] = max(-v, min(v, x[3]))

        # P = F P F' + Q
        F = identidade(NX)
        F[0][2] = F[1][3] = dt
        P = self.P
        FP = [[sum(F[i][k] * P[k][j] for k in range(NX)) for j in range(NX)]
              for i in range(NX)]
        self.P = [[sum(FP[i][k] * F[j][k] for k in range(NX)) for j in range(NX)]
                  for i in range(NX)]

        # Q do modelo de ruido branco em aceleracao, com q modulado pelo IMU
        q = self.q_parado if parado else self.q_base * (1.0 + self.ganho_acc * acc)
        self.P[0][0] += dt ** 3 / 3 * q
        self.P[1][1] += dt ** 3 / 3 * q
        self.P[0][2] += dt ** 2 / 2 * q
        self.P[2][0] += dt ** 2 / 2 * q
        self.P[1][3] += dt ** 2 / 2 * q
        self.P[3][1] += dt ** 2 / 2 * q
        self.P[2][2] += dt * q
        self.P[3][3] += dt * q

        # suavizacao de exibicao: dissolve a correcao pendente com teto de taxa
        resto = math.hypot(self._resto[0], self._resto[1])
        if resto > 1e-6:
            alvo = max(resto * math.exp(-dt / self.TAU_SUAVIZACAO),
                       resto - self.VEL_REVELA * dt)
            f = max(0.0, alvo) / resto
            self._resto[0] *= f
            self._resto[1] *= f

    # -- correcoes ----------------------------------------------------------
    def _corrigir(self, i, z, R):
        """
        Correcao escalar. Toda correcao move a posicao (via correlacao), entao
        o salto de exibicao e capturado aqui, para qualquer medida.
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
                self.iniciar(lat, lon)
                self._resto = [(lat - onde_estava[0]) * GRAUS_PARA_M,
                               (lon - onde_estava[1]) * GRAUS_PARA_M * self.cos_lat0]
                self._seguidos = 0
            return
        self._seguidos = 0

        self._corrigir(0, n, R)
        self._corrigir(1, e, R)

    def corrigir_velocidade(self, velocidade_kmh, rumo_graus):
        """
        A velocidade do GPS entra decomposta em Norte/Leste. Isso usa o rumo,
        mas o rumo DO GPS - medido, absoluto, sem deriva - e nao o yaw do IMU.
        """
        if not self.pronto:
            return
        self._vel_gps = velocidade_kmh
        # A leitura baixa so vira ZUPT se a janela GNSS tambem nao indicar
        # movimento. Assim um unico zero espurio durante a caminhada nao freia
        # o filtro, mas uma parada confirmada pelo GPS zera a velocidade antes
        # mesmo de terminar a histerese mais conservadora da IMU.
        if (velocidade_kmh < self.vel_parado
                and (self.parado or not self.detector.gps_movendo)):
            self._corrigir(2, 0.0, self.r_zupt)
            self._corrigir(3, 0.0, self.r_zupt)
            return
        if velocidade_kmh < self.vel_rumo_ok:
            return                     # rumo do GPS ainda e ruido

        # A incerteza angular do course over ground cresce quando a velocidade
        # cai: com ruido de posicao sigma e fixes de 1 Hz, o erro de rumo e da
        # ordem de atan(sigma / v). A pe (~1,4 m/s) isso passa de 30 graus,
        # entao R cresce na mesma proporcao em vez de confiar igual.
        v = velocidade_kmh / 3.6
        escala = max(1.0, (self.vel_rumo_ok / 3.6 / max(v, 0.1)) ** 2)
        r = math.radians(rumo_graus)
        self._corrigir(2, v * math.cos(r), self.r_vel * escala)
        self._corrigir(3, v * math.sin(r), self.r_vel * escala)

    def corrigir_pdr(self, cadencia_hz, comprimento_passo, rumo_graus,
                     sigma_rumo_graus, sigma_passo=0.12):
        """
        Entrada do acelerometro na posicao, por contagem de passos.

        Esta e a resposta a "dar sensibilidade ao acelerometro". Ele NAO volta
        a ser integrado - o que entra e uma MEDIDA DE VELOCIDADE construida de
        duas grandezas que o hardware mede bem:

            velocidade = cadencia (Hz) x comprimento do passo (m)

        A cadencia sai da contagem de passos, que e um evento discreto e
        robusto (desvio de 0,106 s no intervalo, medido). O comprimento vem da
        calibracao contra o GPS no trecho reto inicial.

        O R e anisotropico na pratica: o erro AO LONGO do rumo vem do
        comprimento do passo (poucos por cento), enquanto o erro
        PERPENDICULAR vem do erro de rumo e vale v*sen(sigma_rumo). Com
        sigma_rumo de 21 graus a 1 m/s, isso da 0,36 m/s de incerteza lateral -
        muito maior que a longitudinal. Como as correcoes aqui sao escalares em
        vn e ve, usamos o pior caso dos dois para nao mentir confianca.
        """
        if not self.pronto or self.parado or cadencia_hz <= 0:
            return False
        v = cadencia_hz * comprimento_passo
        if v <= 0 or v > 8.0:          # 8 m/s ja e corrida, nao caminhada
            return False

        sr = math.radians(max(sigma_rumo_graus, 1.0))
        sigma_lat = v * math.sin(sr)
        sigma_lon = cadencia_hz * sigma_passo
        R = max(sigma_lat, sigma_lon) ** 2

        r = math.radians(rumo_graus)
        self._corrigir(2, v * math.cos(r), R)
        self._corrigir(3, v * math.sin(r), R)
        return True

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


# ---------------------------------------------------------------------------
class EstimadorRumo:
    """
    Rumo geografico sem magnetometro, versao 2 - reescrita a partir de uma
    caminhada real de 179 s (data/caminhada-20260806-174706.csv).

    A versao 1 usava o yaw do firmware ancorado no COG instantaneo do GPS.
    Os dados mostraram que as DUAS pontas estavam erradas:

    1. O YAW DO FIRMWARE NAO SERVE. Contra uma referencia de rumo decente, o
       erro do yaw tem p90 de 174 graus - ou seja, em 10% do tempo ele aponta
       para o lado oposto. O gz cru, integrado, tem p90 de 34 graus.

    2. O COG INSTANTANEO NAO SERVE DE ANCORA A PE. A mediana da variacao do
       COG entre atualizacoes e boa (0,3 graus), mas o p90 a 3-4 km/h e de
       16 graus por 0,17 s - uma cauda enorme. Medido: em 2 s de caminhada
       quase reta, o COG chegou a acusar 149 graus de curva enquanto o giro
       media 2,9 graus. A 1 m/s o deslocamento por epoca (0,2 m) e uma fracao
       do ruido de posicao (~2,5 m), entao o COG e quase so ruido.

    O QUE SUBSTITUIU

    Fonte de curto prazo: o gz CRU, integrado - e NEGADO. O giro e positivo no
    sentido anti-horario (mao direita em torno do eixo vertical) e o azimute
    cresce no sentido horario. Medido em 604 viradas reais: 81% delas com gz
    de sinal oposto a mudanca de rumo. Sem o sinal trocado o rumo gira para o
    lado errado.

    Ancora de longo prazo: a DIRECAO DO DESLOCAMENTO sobre uma base de varios
    segundos, e nao o COG instantaneo. Andando a 1 m/s, 8 s valem ~8 m, bem
    acima do ruido do GPS - a direcao entre os extremos passa a significar
    alguma coisa. E a mesma informacao do COG, integrada por tempo suficiente
    para o ruido sair.

    LIMITE QUE CONTINUA VALENDO: parado, e sem nunca ter andado, nao existe
    rumo absoluto neste hardware. `confiavel` diz quando a saida presta.
    """

    # Base de deslocamento para a ancora. Abaixo de ~6 s a pe o ruido do GPS
    # ainda domina; acima de ~12 s a estimativa fica velha demais numa curva.
    BASE_S = 8.0
    DESLOC_MIN = 4.0          # m: sem isso o angulo do deslocamento e ruido
    PASSO_MAX = 0.30          # fracao da discrepancia absorvida por ancoragem
    DERIVA_GZ = 0.05          # graus/s de incerteza acumulada com o giro livre
    GANHO_GZ = 1.0            # escala do giro; medido entre 0,76 e 0,93
    TAU_RESIDUO = 15          # ancoragens na media movel do residuo
    INCERTEZA_MIN = 8.0       # graus: nao prometer melhor do que isso a pe
    ROTACAO_MAX_BASE = 25.0   # so ancora em trecho aproximadamente reto
    TAU_BIAS_S = 20.0         # adaptacao lenta do zero do giro em repouso

    def __init__(self):
        self.offset: float | None = None
        self.incerteza = 180.0
        self._rel = 0.0           # rumo relativo, integrado do giro
        self._hist: list[tuple[float, float, float, float]] = []  # (t, n, e, rel)
        self._ancoras = 0
        self.vies_gz = 0.0
        # A incerteza NAO decai por formula. A v1 fazia isso e mentia feio:
        # relatava 3,5 graus enquanto o erro real na caminhada era de 21. Aqui
        # ela e a media movel do |residuo| observado nas ancoragens - ou seja,
        # o quanto o giro e o GPS de fato discordam. Numero medido, nao
        # prometido.
        self._residuo = None

    def definir_vies_gz(self, vies_gz):
        """Aplica o zero medido durante a fase parada da calibracao."""
        self.vies_gz = vies_gz

    def fixar_rumo(self, rumo_graus, incerteza=15.0):
        """
        Crava o rumo absoluto AGORA, descartando o offset acumulado.

        Usado uma vez, no fim da calibracao inicial: o trecho reto da uma
        direcao de deslocamento que E a direcao do corpo, o que nao vale em
        curva. Depois disso o offset volta a ser so corrigido gradualmente por
        `ancorar_por_deslocamento` - cravar de novo, no meio da operacao, jogaria
        fora o que o giro sabe sobre a rotacao recente.
        """
        self.offset = (rumo_graus - self._rel) % 360.0
        self.incerteza = incerteza
        self._residuo = incerteza

    # -- curto prazo --------------------------------------------------------
    def passo(self, gz, dt, parado=False):
        """
        Integra o giro. gz em graus/s (eixo Z do sensor), dt em segundos.

        O sinal negativo e a correcao medida: gz e anti-horario positivo,
        azimute e horario positivo.
        """
        if dt <= 0:
            return
        if parado:
            # Em repouso nao existe informacao nova de rumo. Congelamos a
            # integral e usamos o periodo para acompanhar lentamente o bias.
            alfa = dt / (self.TAU_BIAS_S + dt)
            self.vies_gz += alfa * (gz - self.vies_gz)
            return
        self._rel += SINAL_GZ * (gz - self.vies_gz) * self.GANHO_GZ * dt
        if self.offset is not None:
            self.incerteza = min(180.0, self.incerteza + self.DERIVA_GZ * dt)

    # -- longo prazo --------------------------------------------------------
    def ancorar_por_deslocamento(self, t, n, e, movendo=True):
        """
        Recebe a posicao ja em metros (Norte, Leste) - de preferencia a
        filtrada, nao o fix cru. Quando houver base temporal e deslocamento
        suficientes, corrige o offset. Retorna True se ancorou.
        """
        if not movendo:
            # Nao deixe jitter de uma parada virar uma base de rumo quando a
            # pessoa comecar a andar novamente.
            self._hist.clear()
            return False

        self._hist.append((t, n, e, self._rel))
        # guarda o suficiente para cobrir a base, com folga
        while len(self._hist) > 2 and t - self._hist[0][0] > self.BASE_S * 2.5:
            self._hist.pop(0)

        # acha o ponto mais antigo que ainda cobre a base
        antigo = None
        for reg in self._hist:
            if t - reg[0] >= self.BASE_S:
                antigo = reg
            else:
                break
        if antigo is None:
            return False

        dn, de = n - antigo[1], e - antigo[2]
        if math.hypot(dn, de) < self.DESLOC_MIN:
            return False        # parado ou andando de lado: sem direcao util
        if abs(dif_angular(self._rel, antigo[3])) > self.ROTACAO_MAX_BASE:
            return False        # curva: deslocamento nao representa o corpo

        rumo_desloc = math.degrees(math.atan2(de, dn)) % 360.0
        # o rumo do deslocamento vale para o MEIO da base, e o giro ja andou
        # desde entao; por isso comparamos com o rumo relativo daquele instante
        t_meio = (t + antigo[0]) / 2.0
        alvo = rumo_desloc - self._rel_em(t_meio)

        if self.offset is None:
            self.offset = alvo % 360.0
            self.incerteza = 45.0
        else:
            residuo = abs(dif_angular(alvo, self.offset))
            self.offset = (self.offset
                           + self.PASSO_MAX * dif_angular(alvo, self.offset)) % 360.0
            # media movel do desacordo observado entre giro e GPS
            if self._residuo is None:
                self._residuo = residuo
            else:
                k = 1.0 / self.TAU_RESIDUO
                self._residuo += k * (residuo - self._residuo)
            self.incerteza = max(self.INCERTEZA_MIN, self._residuo)
        self._ancoras += 1
        return True

    def _rel_em(self, t_alvo):
        """
        Rumo relativo no instante t_alvo, interpolado do historico.

        Isso importa: o rumo do deslocamento descreve o MEIO da base, nao o
        agora. Comparar com o rumo relativo atual embutiria toda a curva feita
        durante a base como se fosse erro de offset - e o estimador passaria a
        perseguir a propria defasagem em vez de corrigir a deriva.
        """
        h = self._hist
        if not h:
            return self._rel
        if t_alvo <= h[0][0]:
            return h[0][3]
        for a, b in zip(h, h[1:]):
            if a[0] <= t_alvo <= b[0]:
                span = b[0] - a[0]
                if span <= 0:
                    return a[3]
                f = (t_alvo - a[0]) / span
                return a[3] + f * (b[3] - a[3])
        return h[-1][3]

    # -- saidas -------------------------------------------------------------
    @property
    def rumo(self) -> float | None:
        if self.offset is None:
            return None
        return (self._rel + self.offset) % 360.0

    @property
    def confiavel(self) -> bool:
        return self.offset is not None and self.incerteza <= 25.0

    @property
    def ancoragens(self) -> int:
        return self._ancoras


# ---------------------------------------------------------------------------
class MediaParado:
    """
    Media de fixes de GPS enquanto o sensor esta parado.

    E o unico ganho de precisao real que este hardware oferece. O erro do GPS
    tem uma parte aleatoria que cai com sqrt(N) ao promediar - 30 fixes
    reduzem essa parcela em ~5,5x. (A parte sistematica, de ionosfera e
    multicaminho, nao cai; por isso o ganho satura, e a saida nao fica melhor
    que ~1 m sem correcao diferencial.)

    O acelerometro nao ajuda aqui: parado, ele so acrescenta a deriva medida
    de 1,26 m em 30 s. Ficar parado e promediar e estritamente melhor.
    """

    def __init__(self, minimo: int = 5, maximo: int = 600):
        self.minimo = minimo
        self.maximo = maximo
        self._lat: list[float] = []
        self._lon: list[float] = []

    def reiniciar(self):
        self._lat.clear()
        self._lon.clear()

    def amostra(self, lat, lon, parado: bool):
        """Acumula quando parado; descarta tudo assim que se move."""
        if not parado:
            if self._lat:
                self.reiniciar()
            return
        self._lat.append(lat)
        self._lon.append(lon)
        if len(self._lat) > self.maximo:
            self._lat.pop(0)
            self._lon.pop(0)

    @property
    def n(self) -> int:
        return len(self._lat)

    @property
    def posicao(self):
        """Media dos fixes acumulados, ou (None, None) se ainda for cedo."""
        if self.n < self.minimo:
            return (None, None)
        return (sum(self._lat) / self.n, sum(self._lon) / self.n)

    @property
    def sigma_m(self) -> float:
        """
        Erro padrao da media, em metros. Mede so a dispersao dos fixes - o
        vies comum a todos eles nao aparece aqui e nao some com N.
        """
        if self.n < 2:
            return float("inf")
        lat_m = sum(self._lat) / self.n
        lon_m = sum(self._lon) / self.n
        cos_lat = math.cos(math.radians(lat_m))
        d = [((la - lat_m) * GRAUS_PARA_M) ** 2
             + ((lo - lon_m) * GRAUS_PARA_M * cos_lat) ** 2
             for la, lo in zip(self._lat, self._lon)]
        return math.sqrt(sum(d) / (self.n - 1) / self.n)


# ---------------------------------------------------------------------------
class CalibracaoInicial:
    """
    Procedimento guiado de partida. Maquina de estados de quatro fases:

      1. SATELITES  espera o GNSS travar (satelites e HDOP sustentados).
      2. PARADO     fique imovel: mede o vies do giro e a posicao media.
      3. RETO       ande alguns metros em linha reta: e daqui que saem AS DUAS
                    coisas que nao existem sem calibracao -
                       - o zero do rumo (o giro so mede variacao)
                       - o comprimento do seu passo (para o PDR)
      4. PRONTO     opera.

    POR QUE A FASE 3 E OBRIGATORIA

    Sem magnetometro, o giroscopio mede variacao de angulo, nunca o angulo
    absoluto. Alguem precisa dizer uma vez "voce esta apontado para ca", e a
    unica fonte disponivel e o deslocamento medido pelo GPS. Andar reto e o
    jeito de tornar esse deslocamento inequivoco: em linha reta o rumo do
    deslocamento E o rumo do corpo, o que nao vale numa curva.

    O mesmo trecho da o comprimento do passo de graca - distancia do GPS
    dividida por passos contados - e ele varia bastante entre pessoas (0,4 a
    0,8 m), entao chutar um valor custa erro proporcional a distancia.
    """

    SATELITES = "satelites"
    PARADO = "parado"
    RETO = "reto"
    PRONTO = "pronto"

    # Fase 1
    SAT_MIN = 6
    HDOP_MAX = 2.5
    SEG_FIX_ESTAVEL = 3.0

    # Fase 2
    SEG_PARADO = 8.0

    # Fase 3
    DIST_MIN_RETO = 15.0      # m de deslocamento em linha reta
    PASSOS_MIN = 12
    RETIDAO_MIN = 0.90        # deslocamento / caminho percorrido

    def __init__(self):
        self.fase = self.SATELITES
        self.desde = 0.0
        self._fix_ok_desde = None
        self._pts: list[tuple[float, float, float, int]] = []  # (t, n, e, passos)
        self._passos_ini = 0
        self._gz = []
        # resultados
        self.vies_gz = 0.0
        self.comprimento_passo = 0.65     # m, chute ate medir
        self.rumo_inicial = None
        self.retidao = 0.0
        self.dist_reto = 0.0
        self.passos_reto = 0

    # -- texto para a interface --------------------------------------------
    def instrucao(self) -> str:
        return {
            self.SATELITES: "Aguarde o GPS travar nos satelites. Fique a ceu aberto.",
            self.PARADO: f"Fique PARADO por {self.SEG_PARADO:.0f} s. Nao mexa no sensor.",
            self.RETO: f"Ande {self.DIST_MIN_RETO:.0f} m em LINHA RETA, "
                       f"em ritmo normal, sem curvas.",
            self.PRONTO: "Calibrado. Pode andar normalmente.",
        }[self.fase]

    def progresso(self, t) -> float:
        """0 a 1 dentro da fase atual, para a barra da interface."""
        if self.fase == self.SATELITES:
            if self._fix_ok_desde is None:
                return 0.0
            return min(1.0, (t - self._fix_ok_desde) / self.SEG_FIX_ESTAVEL)
        if self.fase == self.PARADO:
            return min(1.0, (t - self.desde) / self.SEG_PARADO)
        if self.fase == self.RETO:
            return min(1.0, self.dist_reto / self.DIST_MIN_RETO)
        return 1.0

    @property
    def pronto(self) -> bool:
        return self.fase == self.PRONTO

    # -- avanco -------------------------------------------------------------
    def passo_imu(self, gz, parado, t):
        """Chamado a cada amostra do IMU."""
        if self.fase == self.PARADO and parado:
            self._gz.append(gz)

    def passo_gps(self, t, n, e, sat, hdop, passos_agora, parado):
        """
        Chamado a cada fix util, com a posicao ja em metros. Retorna True
        quando a fase muda - util para a interface avisar.
        """
        if self.fase == self.SATELITES:
            bom = sat >= self.SAT_MIN and 0 < hdop <= self.HDOP_MAX
            if not bom:
                self._fix_ok_desde = None
                return False
            if self._fix_ok_desde is None:
                self._fix_ok_desde = t
            elif t - self._fix_ok_desde >= self.SEG_FIX_ESTAVEL:
                self.fase = self.PARADO
                self.desde = t
                self._gz.clear()
                return True
            return False

        if self.fase == self.PARADO:
            if not parado:
                self.desde = t          # mexeu: reinicia a contagem
                self._gz.clear()
                return False
            if t - self.desde >= self.SEG_PARADO:
                if self._gz:
                    self.vies_gz = sum(self._gz) / len(self._gz)
                self.fase = self.RETO
                self.desde = t
                self._pts = [(t, n, e, passos_agora)]
                self._passos_ini = passos_agora
                return True
            return False

        if self.fase == self.RETO:
            self._pts.append((t, n, e, passos_agora))
            if len(self._pts) < 3:
                return False

            # JANELA DESLIZANTE, e nao "desde o inicio da fase". Medir a
            # retidao desde o comeco condena o trecho inteiro assim que a
            # pessoa faz uma curva - foi o que aconteceu na primeira caminhada
            # gravada, que fechou um circuito e nunca calibrou (retidao 0,35).
            # Aqui procuramos o trecho reto mais LONGO que termina agora: basta
            # que os ultimos metros tenham sido retos, nao a caminhada toda.
            melhor = None
            acum = 0.0
            for k in range(len(self._pts)-2, -1, -1):
                a, b = self._pts[k], self._pts[k+1]
                acum += math.hypot(b[1]-a[1], b[2]-a[2])
                if acum <= 0:
                    continue
                reta = math.hypot(n - a[1], e - a[2])
                ret = reta / acum
                if reta >= self.DIST_MIN_RETO and ret >= self.RETIDAO_MIN:
                    melhor = (a, acum, reta, ret)
                elif acum > self.DIST_MIN_RETO * 2.5:
                    break        # ja olhamos longe o bastante para tras

            if melhor is None:
                # so para a interface mostrar algo enquanto nao fecha
                a = self._pts[0]
                cam = sum(math.hypot(q[1]-p[1], q[2]-p[2])
                          for p, q in zip(self._pts, self._pts[1:]))
                self.dist_reto = math.hypot(n-a[1], e-a[2])
                self.retidao = self.dist_reto/cam if cam > 0 else 0.0
                self.passos_reto = passos_agora - self._passos_ini
                # nao deixa a lista crescer sem limite
                while len(self._pts) > 2 and t - self._pts[0][0] > 300:
                    self._pts.pop(0)
                return False

            a, caminho, reta, ret = melhor
            self.dist_reto, self.retidao = reta, ret
            self.passos_reto = passos_agora - a[3]
            if self.passos_reto < self.PASSOS_MIN:
                return False

            # rumo do trecho: a direcao do deslocamento entre os extremos
            self.rumo_inicial = math.degrees(math.atan2(e-a[2], n-a[1])) % 360.0
            # comprimento do passo: caminho REAL dividido por passos. Usamos o
            # caminho, nao a reta: mesmo andando "reto" o caminho e um pouco
            # maior, e e ele que os passos percorreram.
            self.comprimento_passo = max(
                0.3, min(1.1, caminho / max(self.passos_reto, 1)))
            self.fase = self.PRONTO
            self.desde = t
            return True

        return False

    def reiniciar(self):
        self.__init__()


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

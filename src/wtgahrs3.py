"""
Driver Modbus RTU para o sensor WitMotion WTGAHRS3-485.

Baseado no "WIT Standard Modbus Protocol" oficial:
https://wit-motion.gitbook.io/witmotion-sdk/wit-standard-modbus-protocol/modbus-communication-protocol

Padroes de fabrica:
    - Endereco Modbus (slave id): 0x50 (80 decimal)
    - Baud rate: 9600, 8 bits de dados, sem paridade, 1 stop bit (8N1)
    - Funcao de leitura: 0x03 (read holding registers)
    - Funcao de escrita: 0x06 (write single register)

Uso rapido:
    from wtgahrs3 import WTGAHRS3
    s = WTGAHRS3("/dev/ttyUSB0")
    print(s.ler_posicao())
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, asdict, field
from typing import Iterable, Optional

import minimalmodbus
import serial
import serial.tools.list_ports


# ---------------------------------------------------------------------------
# Mapa de registradores (endereco hexadecimal)
# ---------------------------------------------------------------------------
REG_SAVE = 0x00       # salvar / reiniciar / restaurar padrao de fabrica
REG_CALSW = 0x01      # modo de calibracao
REG_BAUD = 0x04       # baud rate da porta serial
REG_IICADDR = 0x1A    # endereco Modbus do dispositivo
REG_GPSBAUD = 0x26    # baud rate do modulo GPS interno
REG_YYMM = 0x30       # data/hora (4 registradores: 0x30..0x33)
REG_AX = 0x34         # aceleracao X (bloco IMU: 0x34..0x40, 13 registradores)
REG_TEMP = 0x40       # temperatura do chip
REG_PRESSUREL = 0x45  # pressao + altitude barometrica (0x45..0x48)
REG_LONL = 0x49       # longitude/latitude (0x49..0x4C)
REG_GPSHEIGHT = 0x4D  # altitude GPS, rumo, velocidade (0x4D..0x50)
REG_Q0 = 0x51         # quaternions (0x51..0x54)
REG_SVNUM = 0x55      # satelites + PDOP/HDOP/VDOP (0x55..0x58)
REG_TIMEZONE = 0x6B   # fuso horario do GPS
REG_KEY = 0x69        # registrador de desbloqueio

CHAVE_DESBLOQUEIO = 0xB588
CMD_SALVAR = 0x0000

ENDERECO_PADRAO = 0x50
BAUDS_COMUNS = [9600, 115200, 19200, 38400, 57600, 4800, 230400]

# Fundo de escala padrao de fabrica
ESCALA_ACC_G = 16.0        # +/- 16 g
ESCALA_GYRO_DPS = 2000.0   # +/- 2000 graus/s
ESCALA_ANGULO = 180.0      # +/- 180 graus


# ---------------------------------------------------------------------------
# Conversoes
# ---------------------------------------------------------------------------
def s16(v: int) -> int:
    """Interpreta um registrador de 16 bits como inteiro com sinal."""
    return v - 0x10000 if v >= 0x8000 else v


def s32(baixo: int, alto: int) -> int:
    """Junta dois registradores (baixo, alto) num inteiro de 32 bits com sinal."""
    v = ((alto & 0xFFFF) << 16) | (baixo & 0xFFFF)
    return v - 0x100000000 if v >= 0x80000000 else v


def ddmm_para_graus(bruto: int) -> float:
    """
    Converte o int32 de coordenada da WitMotion para graus decimais.

    ATENCAO - a formula do manual oficial esta INCOMPLETA para o hemisferio
    sul/oeste. O manual diz:

        dd       = Lon[31:0] / 10000000
        mm.mmmmm = (Lon[31:0] % 10000000) / 100000

    Isso so funciona com coordenadas positivas. O firmware monta o inteiro como

        valor = graus_COM_SINAL * 1e7  +  minutos_SEM_SINAL * 1e5

    ou seja, aplica o sinal do hemisferio apenas na parte dos graus. No Brasil
    (lat e lon negativas) os minutos entram SOMADOS em vez de subtraidos, e a
    formula do manual devolve "minutos" acima de 60 - valor impossivel.

    Exemplo real medido em Lavras/MG (03/08/2026):
        posicao real     : -21.225671  (21 graus 13.54026 minutos SUL)
        firmware codifica: -21*1e7 + 1354026 = -208645974
        formula do manual: -20 graus 86.47 minutos -> -21.4412  (136 km errado)
        formula correta  : -21.2255                             (36 m do GPS do celular)

    A correcao e usar floor() em vez de truncar o modulo: para valores
    negativos, floor(-20.86) = -21, que e a parte de graus verdadeira.

    LIMITACAO: coordenadas com |graus| < 1 (proximo ao equador ou ao meridiano
    de Greenwich) sao ambiguas, porque o sinal se perde quando a parte dos
    graus e zero. Nao afeta o Brasil.
    """
    dd = math.floor(bruto / 10_000_000)              # parte de graus, com sinal
    mm = (bruto - dd * 10_000_000) / 100_000.0       # minutos, sempre positivos
    sinal = -1 if dd < 0 else 1
    return sinal * (abs(dd) + mm / 60.0)


# ---------------------------------------------------------------------------
# Estruturas de dados
# ---------------------------------------------------------------------------
@dataclass
class Posicao:
    """Bloco de posicao lido dos registradores 0x49..0x58."""
    latitude: float           # graus decimais (negativo = sul)
    longitude: float          # graus decimais (negativo = oeste)
    altitude_gps_m: float     # metros, do receptor GNSS
    rumo_gps_deg: float       # graus, direcao de deslocamento
    velocidade_kmh: float     # km/h sobre o solo
    satelites: int            # numero de satelites em uso
    pdop: float               # diluicao de precisao (posicao)
    hdop: float               # diluicao de precisao (horizontal)
    vdop: float               # diluicao de precisao (vertical)
    lat_bruto: int = 0        # valor int32 cru, util para depuracao
    lon_bruto: int = 0

    @property
    def tem_fix(self) -> bool:
        """True quando o GPS ja travou em satelites suficientes."""
        return self.satelites >= 4 and self.hdop > 0

    def como_dict(self) -> dict:
        return asdict(self)


@dataclass
class Ambiente:
    """Pressao e altitude barometrica (registradores 0x45..0x48) + temperatura."""
    pressao_pa: int
    altitude_baro_m: float
    temperatura_c: float


@dataclass
class Imu:
    """Bloco inercial (registradores 0x34..0x3F)."""
    acc_x: float
    acc_y: float
    acc_z: float
    gyro_x: float
    gyro_y: float
    gyro_z: float
    mag_x: int
    mag_y: int
    mag_z: int
    roll: float
    pitch: float
    yaw: float


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
class WTGAHRS3:
    """Interface Modbus RTU para o WTGAHRS3-485."""

    def __init__(
        self,
        porta: str,
        endereco: int = ENDERECO_PADRAO,
        baudrate: int = 9600,
        timeout: float = 0.5,
        debug: bool = False,
    ):
        self.porta = porta
        self.endereco = endereco
        self.baudrate = baudrate

        self.inst = minimalmodbus.Instrument(porta, endereco, mode=minimalmodbus.MODE_RTU)
        self.inst.serial.baudrate = baudrate
        self.inst.serial.bytesize = 8
        self.inst.serial.parity = serial.PARITY_NONE
        self.inst.serial.stopbits = 1
        self.inst.serial.timeout = timeout
        self.inst.clear_buffers_before_each_transaction = True
        self.inst.close_port_after_each_call = False
        self.inst.debug = debug

    # -- primitivas ---------------------------------------------------------
    def _ler(self, endereco_registrador: int, quantidade: int) -> list[int]:
        """Le N registradores consecutivos com funcao 0x03."""
        return self.inst.read_registers(endereco_registrador, quantidade, functioncode=3)

    def _escrever(self, endereco_registrador: int, valor: int) -> None:
        """Escreve um registrador com funcao 0x06."""
        self.inst.write_register(endereco_registrador, valor, functioncode=6)

    def esta_respondendo(self) -> bool:
        """Faz uma leitura curta so para confirmar que o sensor responde."""
        try:
            self._ler(REG_TEMP, 1)
            return True
        except Exception:
            return False

    # -- leituras -----------------------------------------------------------
    def ler_posicao(self) -> Posicao:
        """Le latitude, longitude, altitude, velocidade e qualidade do fix."""
        # 0x49..0x50 -> LonL, LonH, LatL, LatH, GPSHeight, GPSYaw, GPSVL, GPSVH
        g = self._ler(REG_LONL, 8)
        lon_bruto = s32(g[0], g[1])
        lat_bruto = s32(g[2], g[3])

        # 0x55..0x58 -> SVNUM, PDOP, HDOP, VDOP
        d = self._ler(REG_SVNUM, 4)

        return Posicao(
            latitude=ddmm_para_graus(lat_bruto),
            longitude=ddmm_para_graus(lon_bruto),
            altitude_gps_m=s16(g[4]) / 10.0,
            rumo_gps_deg=s16(g[5]) / 100.0,
            velocidade_kmh=s32(g[6], g[7]) / 1000.0,
            satelites=d[0],
            pdop=d[1] / 100.0,
            hdop=d[2] / 100.0,
            vdop=d[3] / 100.0,
            lat_bruto=lat_bruto,
            lon_bruto=lon_bruto,
        )

    def ler_ambiente(self) -> Ambiente:
        """Le pressao barometrica, altitude barometrica e temperatura do chip."""
        p = self._ler(REG_PRESSUREL, 4)   # PressureL, PressureH, HeightL, HeightH
        t = self._ler(REG_TEMP, 1)
        return Ambiente(
            pressao_pa=s32(p[0], p[1]),
            altitude_baro_m=s32(p[2], p[3]) / 100.0,   # registrador vem em cm
            temperatura_c=s16(t[0]) / 100.0,
        )

    def ler_imu(self) -> Imu:
        """Le aceleracao, velocidade angular, campo magnetico e angulos."""
        r = self._ler(REG_AX, 12)  # 0x34..0x3F
        v = [s16(x) for x in r]
        return Imu(
            acc_x=v[0] / 32768.0 * ESCALA_ACC_G,
            acc_y=v[1] / 32768.0 * ESCALA_ACC_G,
            acc_z=v[2] / 32768.0 * ESCALA_ACC_G,
            gyro_x=v[3] / 32768.0 * ESCALA_GYRO_DPS,
            gyro_y=v[4] / 32768.0 * ESCALA_GYRO_DPS,
            gyro_z=v[5] / 32768.0 * ESCALA_GYRO_DPS,
            mag_x=v[6],
            mag_y=v[7],
            mag_z=v[8],
            roll=v[9] / 32768.0 * ESCALA_ANGULO,
            pitch=v[10] / 32768.0 * ESCALA_ANGULO,
            yaw=v[11] / 32768.0 * ESCALA_ANGULO,
        )

    def ler_data_hora(self) -> dict:
        """Le a data/hora do GPS (registradores 0x30..0x33)."""
        r = self._ler(REG_YYMM, 4)
        return {
            "ano": 2000 + (r[0] & 0xFF),
            "mes": (r[0] >> 8) & 0xFF,
            "dia": r[1] & 0xFF,
            "hora": (r[1] >> 8) & 0xFF,
            "minuto": r[2] & 0xFF,
            "segundo": (r[2] >> 8) & 0xFF,
            "milissegundo": r[3],
        }

    # -- configuracao -------------------------------------------------------
    def desbloquear(self) -> None:
        """
        Destrava o sensor para aceitar escritas de configuracao.
        O sensor volta a travar sozinho depois de ~10 s sem comandos.
        """
        self._escrever(REG_KEY, CHAVE_DESBLOQUEIO)
        time.sleep(0.1)

    def salvar(self) -> None:
        """Grava a configuracao atual na memoria nao volatil."""
        self._escrever(REG_SAVE, CMD_SALVAR)
        time.sleep(0.2)

    def definir_fuso_horario(self, utc_offset: int) -> None:
        """
        Ajusta o fuso do GPS. Brasilia = UTC-3 -> utc_offset = -3.
        O registrador usa 0 = UTC-12, entao o valor gravado e (offset + 12).
        """
        if not -12 <= utc_offset <= 12:
            raise ValueError("utc_offset precisa estar entre -12 e +12")
        self.desbloquear()
        self._escrever(REG_TIMEZONE, utc_offset + 12)
        self.salvar()

    def fechar(self) -> None:
        if self.inst.serial and self.inst.serial.is_open:
            self.inst.serial.close()

    def __enter__(self) -> "WTGAHRS3":
        return self

    def __exit__(self, *exc) -> None:
        self.fechar()


# ---------------------------------------------------------------------------
# Descoberta automatica
# ---------------------------------------------------------------------------
def listar_portas() -> list[str]:
    """Lista as portas seriais USB visiveis no sistema."""
    return [p.device for p in serial.tools.list_ports.comports()]


def procurar_sensor(
    portas: Optional[Iterable[str]] = None,
    bauds: Optional[Iterable[int]] = None,
    enderecos: Iterable[int] = (0x50,),
    verbose: bool = True,
) -> Optional[WTGAHRS3]:
    """
    Varre portas x baud rates x enderecos Modbus ate achar um sensor que responda.
    Retorna uma instancia conectada, ou None.
    """
    portas = list(portas) if portas else listar_portas()
    bauds = list(bauds) if bauds else BAUDS_COMUNS

    for porta in portas:
        for baud in bauds:
            for end in enderecos:
                if verbose:
                    print(f"  testando {porta} @ {baud} baud, endereco 0x{end:02X} ...", end=" ")
                try:
                    s = WTGAHRS3(porta, endereco=end, baudrate=baud, timeout=0.3)
                    if s.esta_respondendo():
                        if verbose:
                            print("OK")
                        return s
                    s.fechar()
                except Exception:
                    pass
                if verbose:
                    print("nada")
    return None

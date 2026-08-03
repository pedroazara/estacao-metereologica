#!/usr/bin/env python3
"""
Le posicao (e opcionalmente IMU e ambiente) do WTGAHRS3-485 em loop,
mostra no terminal e grava em CSV.

    python3 src/ler_posicao.py --porta /dev/ttyUSB0
    python3 src/ler_posicao.py --porta /dev/ttyUSB0 --hz 5 --csv data/trajeto.csv
    python3 src/ler_posicao.py --porta /dev/ttyUSB0 --completo
"""

import argparse
import csv
import os
import signal
import sys
import time
from datetime import datetime

from wtgahrs3 import ENDERECO_PADRAO, WTGAHRS3

_parar = False


def _handler(signum, frame):
    global _parar
    _parar = True


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Leitor de posicao do WTGAHRS3-485")
    p.add_argument("--porta", default="/dev/ttyUSB0", help="porta serial (padrao: /dev/ttyUSB0)")
    p.add_argument("--baud", type=int, default=9600, help="baud rate (padrao: 9600)")
    p.add_argument(
        "--endereco",
        default=hex(ENDERECO_PADRAO),
        help="endereco Modbus, aceita 0x50 ou 80 (padrao: 0x50)",
    )
    p.add_argument("--hz", type=float, default=1.0, help="leituras por segundo (padrao: 1)")
    p.add_argument("--csv", default="data/posicao.csv", help="arquivo CSV de saida")
    p.add_argument("--completo", action="store_true", help="tambem grava IMU e pressao/temperatura")
    p.add_argument("--sem-csv", action="store_true", help="so mostra na tela, nao grava arquivo")
    return p.parse_args()


def reconectar(sensor, porta: str, endereco: int, baud: int, tentativas: int = 30):
    """
    Tenta reabrir a porta serial depois de uma queda.

    Numa estacao movel a porta USB cai com frequencia (vibracao, cabo,
    autosuspend). Quando o kernel reenumera o adaptador, ele pode voltar com
    outro nome (ttyUSB0 -> ttyUSB1), entao tambem procuramos portas vizinhas.
    """
    import glob

    try:
        sensor.fechar()
    except Exception:
        pass

    for i in range(1, tentativas + 1):
        if _parar:
            return None

        # a porta original primeiro, depois qualquer outra ttyUSB* que exista
        candidatas = [porta] + sorted(
            p for p in glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
            if p != porta
        )

        for cand in candidatas:
            try:
                novo = WTGAHRS3(cand, endereco=endereco, baudrate=baud, timeout=0.3)
                if novo.esta_respondendo():
                    print(f"  > reconectado em {cand}", flush=True)
                    return novo
                novo.fechar()
            except Exception:
                pass

        print(f"  . tentando reconectar ({i}/{tentativas}) ...", flush=True)
        time.sleep(2)

    return None


def main() -> int:
    args = parse_args()
    signal.signal(signal.SIGINT, _handler)

    endereco = int(args.endereco, 0)
    intervalo = 1.0 / args.hz if args.hz > 0 else 1.0

    print(f"Conectando em {args.porta} @ {args.baud} baud, endereco 0x{endereco:02X} ...")
    try:
        sensor = WTGAHRS3(args.porta, endereco=endereco, baudrate=args.baud)
    except Exception as e:
        print(f"ERRO ao abrir a porta: {e}")
        print("Dica: rode `python3 src/scan_barramento.py` para descobrir os parametros certos.")
        return 1

    if not sensor.esta_respondendo():
        print("Porta abriu, mas o sensor nao respondeu.")
        print("Rode `python3 src/scan_barramento.py` para varrer portas/baud/endereco.")
        sensor.fechar()
        return 1

    print("Sensor OK. Ctrl+C para parar.\n")

    colunas = [
        "timestamp_pc", "latitude", "longitude", "altitude_gps_m",
        "velocidade_kmh", "rumo_gps_deg", "satelites", "pdop", "hdop", "vdop",
    ]
    if args.completo:
        colunas += [
            "pressao_pa", "altitude_baro_m", "temperatura_c",
            "roll", "pitch", "yaw",
            "acc_x", "acc_y", "acc_z",
            "gyro_x", "gyro_y", "gyro_z",
        ]

    escritor = None
    arquivo = None
    if not args.sem_csv:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        novo = not os.path.exists(args.csv) or os.path.getsize(args.csv) == 0
        arquivo = open(args.csv, "a", newline="", encoding="utf-8")
        escritor = csv.DictWriter(arquivo, fieldnames=colunas)
        if novo:
            escritor.writeheader()
        print(f"Gravando em {args.csv}\n")

    n = 0
    erros = 0
    try:
        while not _parar:
            inicio = time.monotonic()
            try:
                pos = sensor.ler_posicao()
                linha = {
                    "timestamp_pc": datetime.now().isoformat(timespec="milliseconds"),
                    "latitude": round(pos.latitude, 7),
                    "longitude": round(pos.longitude, 7),
                    "altitude_gps_m": pos.altitude_gps_m,
                    "velocidade_kmh": pos.velocidade_kmh,
                    "rumo_gps_deg": pos.rumo_gps_deg,
                    "satelites": pos.satelites,
                    "pdop": pos.pdop,
                    "hdop": pos.hdop,
                    "vdop": pos.vdop,
                }

                if args.completo:
                    amb = sensor.ler_ambiente()
                    imu = sensor.ler_imu()
                    linha.update({
                        "pressao_pa": amb.pressao_pa,
                        "altitude_baro_m": round(amb.altitude_baro_m, 2),
                        "temperatura_c": round(amb.temperatura_c, 2),
                        "roll": round(imu.roll, 3),
                        "pitch": round(imu.pitch, 3),
                        "yaw": round(imu.yaw, 3),
                        "acc_x": round(imu.acc_x, 4),
                        "acc_y": round(imu.acc_y, 4),
                        "acc_z": round(imu.acc_z, 4),
                        "gyro_x": round(imu.gyro_x, 3),
                        "gyro_y": round(imu.gyro_y, 3),
                        "gyro_z": round(imu.gyro_z, 3),
                    })

                if escritor:
                    escritor.writerow(linha)
                    arquivo.flush()

                n += 1
                if pos.tem_fix:
                    status = (
                        f"lat {pos.latitude:11.6f}  lon {pos.longitude:11.6f}  "
                        f"alt {pos.altitude_gps_m:7.1f} m  "
                        f"vel {pos.velocidade_kmh:6.2f} km/h  "
                        f"sat {pos.satelites:2d}  hdop {pos.hdop:.2f}"
                    )
                else:
                    status = (
                        f"SEM FIX - sat {pos.satelites:2d}  "
                        f"(bruto lat={pos.lat_bruto} lon={pos.lon_bruto})"
                    )
                print(f"[{n:6d}] {status}", flush=True)
                erros = 0

            except Exception as e:
                erros += 1
                print(f"  ! falha na leitura ({erros}): {e}", flush=True)

                # Erros de I/O (errno 5) quase sempre significam que a porta
                # USB sumiu - cabo chacoalhou, autosuspend, reenumeracao.
                # Numa estacao movel isso e rotina, entao reconectamos em vez
                # de desistir.
                if erros >= 3:
                    sensor = reconectar(sensor, args.porta, endereco, args.baud)
                    if sensor is None:
                        print("  ! nao foi possivel reconectar. Encerrando.")
                        break
                    erros = 0

            resto = intervalo - (time.monotonic() - inicio)
            if resto > 0:
                time.sleep(resto)

    finally:
        sensor.fechar()
        if arquivo:
            arquivo.close()
        print(f"\nEncerrado. {n} leituras gravadas.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

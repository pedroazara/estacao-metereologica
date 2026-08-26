#!/usr/bin/env python3
"""
Processa uma sessao sincronizada do celular: roda o filtro de Kalman em LOTE
sobre o JSONL cru e grava o derivado num banco SQLite.

    python3 processar_sessao.py data/sincronizadas/esp32-01/esp32-01-....jsonl

E o passo que faltava para a base ser um banco de dados, e nao um deposito de
arquivos. As duas camadas do docs/04-telemetria.md:

  - CRU, imutavel: o proprio arquivo .jsonl, guardado pelo /sync. Nunca muda.
  - DERIVADO, recomputavel: as linhas no SQLite. Reprocessar uma sessao APAGA
    o derivado antigo dela e recalcula do zero — com outro Q/R, se quiser —
    sem tocar no cru.

POR QUE EM LOTE, E NAO AO VIVO. O caminho ao vivo (MQTT) e sensivel a jitter de
rede e amostra fora de ordem. Aqui tudo chega junto e em ordem, entao o filtro
roda uma vez, limpo. E o mesmo FiltroCV ja validado — nenhuma logica nova de
filtro mora aqui.
"""

import json
import os
import sqlite3
from datetime import datetime


def _epoch(iso):
    """ts_app do celular -> segundos. E o carimbo de chegada no telefone: o
    ESP32 sem fix de GPS nao tem hora absoluta, entao esta e a melhor ancora de
    tempo disponivel. Nao se passa pelo `ts` de origem que o doc especifica."""
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return None


ESQUEMA = """
CREATE TABLE IF NOT EXISTS sessoes (
    sessao       TEXT PRIMARY KEY,
    dev          TEXT,
    processada_em TEXT,
    n_derivado   INTEGER
);
CREATE TABLE IF NOT EXISTS derivado (
    dev       TEXT,
    sessao    TEXT,
    ts        REAL,
    lat       REAL, lon       REAL,
    kf_lat    REAL, kf_lon    REAL,
    kf_vel    REAL, kf_sigma  REAL, kf_parado INTEGER,
    sat       INTEGER, hdop   REAL,
    temp      REAL, umid      REAL
);
CREATE INDEX IF NOT EXISTS ix_derivado_dev_ts ON derivado(dev, ts);
CREATE INDEX IF NOT EXISTS ix_derivado_sessao ON derivado(sessao);
"""


def abrir_banco(caminho):
    con = sqlite3.connect(caminho)
    con.executescript(ESQUEMA)
    return con


def processar_sessao(caminho_jsonl, caminho_db):
    """Le o JSONL, roda o Kalman e grava o derivado. Idempotente: reprocessar
    a mesma sessao substitui o derivado dela. Retorna o numero de linhas
    derivadas gravadas."""
    sessao = os.path.basename(caminho_jsonl)
    dev = sessao.split("-")[0]

    temp = umid = None
    derivadas = []

    # SIMPLIFICADO em 26/08/2026: sem Kalman. A posicao e o GPS do telefone
    # (`gps`), gravado direto. Cada ponto leva a ultima temperatura/umidade
    # conhecida - e isso que o painel colore no mapa. As colunas kf_* repetem a
    # posicao crua (nao ha filtro), para o painel nao precisar mudar.
    with open(caminho_jsonl, "r", encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha:
                continue
            try:
                env = json.loads(linha)
            except json.JSONDecodeError:
                continue
            src = env.get("src")
            m = env.get("m") or {}
            t = _epoch(env.get("ts_app"))
            if t is None:
                continue

            if src == "amb":
                temp = m.get("temp", temp)
                umid = m.get("umid", umid)

            elif src == "gps":
                lat, lon = m.get("lat"), m.get("lon")
                if lat is None or lon is None:
                    continue
                vel = m.get("vel", 0.0)
                derivadas.append((
                    dev, sessao, t, lat, lon, lat, lon,
                    vel, m.get("precisao", 0.0), 0,
                    0, 0.0, temp, umid,
                ))

    con = abrir_banco(caminho_db)
    try:
        # Recomputavel: fora o cru, tudo que pertence a esta sessao e apagado
        # antes de reinserir. Reprocessar nunca duplica.
        con.execute("DELETE FROM derivado WHERE sessao = ?", (sessao,))
        con.executemany(
            "INSERT INTO derivado VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", derivadas)
        con.execute(
            "INSERT OR REPLACE INTO sessoes VALUES (?,?,?,?)",
            (sessao, dev, datetime.now().isoformat(timespec="seconds"),
             len(derivadas)))
        con.commit()
    finally:
        con.close()
    return len(derivadas)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db = os.path.join(raiz, "data", "estacao.db")
    n = processar_sessao(sys.argv[1], db)
    print(f"{os.path.basename(sys.argv[1])}: {n} pontos derivados -> {db}")


if __name__ == "__main__":
    main()

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


# Fonte unica da verdade das colunas de `derivado`. Precisa ficar em lista (e
# nao so no CREATE TABLE) porque bancos antigos - como o da base ja implantada
# - podem ter sido criados antes de uma coluna existir, e ALTER TABLE ADD
# COLUMN sempre acrescenta no FIM da tabela, nunca na posicao "certa" do
# schema. Por isso o INSERT abaixo referencia as colunas pelo nome, nao por
# posicao: a ordem fisica de colunas antigas x novas pode divergir da lista
# aqui, e so o nome continua confiavel.
COLUNAS_DERIVADO = [
    ("dev", "TEXT"), ("sessao", "TEXT"), ("ts", "REAL"),
    ("lat", "REAL"), ("lon", "REAL"),
    ("kf_lat", "REAL"), ("kf_lon", "REAL"),
    ("kf_vel", "REAL"), ("kf_sigma", "REAL"), ("kf_parado", "INTEGER"),
    ("sat", "INTEGER"), ("hdop", "REAL"),
    ("temp", "REAL"), ("umid", "REAL"),
    ("ruido_db", "REAL"), ("temp_aux28", "REAL"), ("temp_aux13", "REAL"),
    ("luminosidade", "REAL"),
]

ESQUEMA_SESSOES = """
CREATE TABLE IF NOT EXISTS sessoes (
    sessao       TEXT PRIMARY KEY,
    dev          TEXT,
    processada_em TEXT,
    n_derivado   INTEGER
);
"""


def abrir_banco(caminho):
    con = sqlite3.connect(caminho)
    con.executescript(ESQUEMA_SESSOES)
    con.execute(
        "CREATE TABLE IF NOT EXISTS derivado (%s)"
        % ", ".join(f"{nome} {tipo}" for nome, tipo in COLUNAS_DERIVADO))
    # Migracao: acrescenta colunas que a versao instalada do banco nao tinha
    # ainda (ex.: os sensores baratos, adicionados depois do primeiro deploy
    # na base). Tabela ja no formato atual -> nenhuma delas entra aqui.
    existentes = {l[1] for l in con.execute("PRAGMA table_info(derivado)")}
    for nome, tipo in COLUNAS_DERIVADO:
        if nome not in existentes:
            con.execute(f"ALTER TABLE derivado ADD COLUMN {nome} {tipo}")
    con.executescript(
        "CREATE INDEX IF NOT EXISTS ix_derivado_dev_ts ON derivado(dev, ts);"
        "CREATE INDEX IF NOT EXISTS ix_derivado_sessao ON derivado(sessao);")
    con.commit()
    return con


def processar_sessao(caminho_jsonl, caminho_db):
    """Le o JSONL, roda o Kalman e grava o derivado. Idempotente: reprocessar
    a mesma sessao substitui o derivado dela. Retorna o numero de linhas
    derivadas gravadas."""
    sessao = os.path.basename(caminho_jsonl)
    dev = sessao.split("-")[0]

    temp = umid = None
    ruido_db = temp_aux28 = temp_aux13 = luminosidade = None
    derivadas = []

    # SIMPLIFICADO em 26/08/2026: sem Kalman. A posicao e o GPS do telefone
    # (`gps`), gravado direto. Cada ponto leva a ultima temperatura/umidade
    # conhecida - e isso que o painel colore no mapa. As colunas kf_* repetem a
    # posicao crua (nao ha filtro), para o painel nao precisar mudar.
    #
    # Os sensores baratos (KY-038/028/013, GY-302) seguem o mesmo padrao de
    # "carrega o ultimo valor conhecido" do temp/umid. luminosidade herda o -1
    # do firmware quando o GY-302 nao respondeu naquele ciclo (ver
    # publicarAmbiente() em painel_wifi.ino) - nao e distinguido aqui de "sem
    # amostra ainda", os dois casos ficam None ate a primeira leitura chegar.
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
                ruido_db = m.get("db", ruido_db)
                temp_aux28 = m.get("t28", temp_aux28)
                temp_aux13 = m.get("t13", temp_aux13)
                luminosidade = m.get("lux", luminosidade)

            elif src == "gps":
                lat, lon = m.get("lat"), m.get("lon")
                if lat is None or lon is None:
                    continue
                vel = m.get("vel", 0.0)
                derivadas.append((
                    dev, sessao, t, lat, lon, lat, lon,
                    vel, m.get("precisao", 0.0), 0,
                    0, 0.0, temp, umid,
                    ruido_db, temp_aux28, temp_aux13, luminosidade,
                ))

    con = abrir_banco(caminho_db)
    try:
        # Recomputavel: fora o cru, tudo que pertence a esta sessao e apagado
        # antes de reinserir. Reprocessar nunca duplica.
        con.execute("DELETE FROM derivado WHERE sessao = ?", (sessao,))
        colunas = ", ".join(nome for nome, _ in COLUNAS_DERIVADO)
        marcadores = ", ".join("?" * len(COLUNAS_DERIVADO))
        con.executemany(
            f"INSERT INTO derivado ({colunas}) VALUES ({marcadores})",
            derivadas)
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

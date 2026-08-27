#!/usr/bin/env python3
"""
Planejador de area: o usuario PINTA, no painel do Pi, a area que quer que
seja medida - arrasta o mouse como um pincel, com um tamanho de pincel
escolhido por ele. So isso e o PRE-processamento: fica gravada a area (os
tracos da pintura), nada mais.

O usuario sai a campo e mede do jeito que sempre mediu (sessoes sincronizadas
normalmente, sem nenhum casamento especial no momento do /sync).

O POS-processamento acontece quando alguem ABRE o plano de novo, ao vivo, sem
nada pre-calculado: `pontos_do_plano()` cruza a area pintada contra as
amostras (`derivado`) que aquele `dev` ja tem no banco e devolve as que
caem dentro de alguma pincelada - essas viram os pontos do Voronoi. Como e
tudo calculado na hora, nao existe "nuvem de pontos-alvo" fixa pra decorar
nem pontos "travados" num raio errado - o usuario escolhe o raio de
abrangencia (o quanto cada amostra "puxa" a celula do Voronoi) na hora de
olhar, nao na hora de planejar.

Versao anterior gerava uma grade dos dos pontos-alvo (hexagonal) igual pra
todo mundo andar em cima - foi abandonada por dar trabalho demais ao usuario
(saber que tem que visitar 174 pontos exatos antes de sair de casa) sem
ganho real, ja que o Voronoi no fim das contas particiona o espaco inteiro
de qualquer jeito.
"""

import json
import math
import sqlite3
from datetime import datetime

GRAUS_PARA_M = 111320.0


def _para_metros(lat, lon, lat0, lon0):
    cos_lat = math.cos(math.radians(lat0))
    return ((lat - lat0) * GRAUS_PARA_M, (lon - lon0) * GRAUS_PARA_M * cos_lat)


def _dist_ponto_segmento(px, py, ax, ay, bx, by):
    """Distancia (em metros, ja que px..by estao em metros locais) de um
    ponto ate o SEGMENTO a-b, nao a reta infinita - projeta e limita t em
    [0,1]."""
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


ESQUEMA = """
CREATE TABLE IF NOT EXISTS planos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT,
    dev TEXT,
    rota TEXT,
    criado_em TEXT,
    ativo INTEGER DEFAULT 0
);
"""


def _abrir(caminho_db):
    con = sqlite3.connect(caminho_db)
    con.executescript(ESQUEMA)
    return con


def criar_plano(caminho_db, nome, dev, tracos):
    """Grava a area pintada. `tracos`: lista de pinceladas {"raio_m": float,
    "pontos": [[lat,lon], ...]}, cada uma com o proprio raio (largura do
    pincel naquele momento - mudar o slider depois nao redesenha o que ja
    foi pintado). Retorna o id do plano. Nao gera nem grava nenhum ponto -
    isso e trabalho do pos-processamento, ver pontos_do_plano()."""
    con = _abrir(caminho_db)
    try:
        cur = con.execute(
            "INSERT INTO planos (nome, dev, rota, criado_em) VALUES (?,?,?,?)",
            (nome, dev, json.dumps(tracos),
             datetime.now().isoformat(timespec="seconds")))
        plano_id = cur.lastrowid
        con.commit()
    finally:
        con.close()
    return plano_id


def atualizar_plano(caminho_db, plano_id, tracos):
    """Substitui a area pintada de um plano ja existente - edicao, nao cria
    outro plano. Nome, dev, ativo e criado_em ficam como estavam."""
    con = _abrir(caminho_db)
    try:
        cur = con.execute(
            "UPDATE planos SET rota = ? WHERE id = ?",
            (json.dumps(tracos), plano_id))
        ok = cur.rowcount > 0
        con.commit()
    finally:
        con.close()
    return ok


def listar_planos(caminho_db):
    con = _abrir(caminho_db)
    try:
        linhas = con.execute(
            "SELECT id, nome, dev, criado_em, ativo FROM planos "
            "ORDER BY criado_em DESC").fetchall()
    finally:
        con.close()
    return [{"id": i, "nome": n, "dev": d, "criado_em": c, "ativo": bool(a)}
            for (i, n, d, c, a) in linhas]


def obter_plano(caminho_db, plano_id):
    con = _abrir(caminho_db)
    try:
        p = con.execute(
            "SELECT nome, dev, rota, criado_em, ativo FROM planos "
            "WHERE id = ?", (plano_id,)).fetchone()
    finally:
        con.close()
    if p is None:
        return None
    nome, dev, rota, criado_em, ativo = p
    return {"nome": nome, "dev": dev, "tracos": json.loads(rota),
            "criado_em": criado_em, "ativo": bool(ativo)}


def apagar_plano(caminho_db, plano_id):
    con = _abrir(caminho_db)
    try:
        con.execute("DELETE FROM planos WHERE id = ?", (plano_id,))
        con.commit()
    finally:
        con.close()


def ativar_plano(caminho_db, plano_id):
    """Marca `plano_id` como o plano ATIVO do seu `dev` - so um ativo por
    medidor de cada vez, por isso desativa qualquer outro plano do mesmo dev
    antes. E o plano que `plano_ativo()` devolve para o app buscar (fase
    seguinte, fora desta rodada - aqui so deixamos o dado pronto)."""
    con = _abrir(caminho_db)
    try:
        dev = con.execute(
            "SELECT dev FROM planos WHERE id = ?", (plano_id,)).fetchone()
        if dev is None:
            return False
        con.execute("UPDATE planos SET ativo = 0 WHERE dev = ?", dev)
        con.execute("UPDATE planos SET ativo = 1 WHERE id = ?", (plano_id,))
        con.commit()
    finally:
        con.close()
    return True


def desativar_plano(caminho_db, plano_id):
    con = _abrir(caminho_db)
    try:
        con.execute("UPDATE planos SET ativo = 0 WHERE id = ?", (plano_id,))
        con.commit()
    finally:
        con.close()


def plano_ativo(caminho_db, dev):
    """O plano ativo de um medidor, no formato que o APP vai consumir quando
    chegar a vez dele (fase seguinte): a area (tracos) para guiar o usuario
    ate la, nao pontos exatos - o app nao precisa saber do pos-processamento,
    so de onde e a area. `None` se esse dev nao tem plano ativo."""
    con = _abrir(caminho_db)
    try:
        p = con.execute(
            "SELECT id, nome, rota, criado_em FROM planos "
            "WHERE dev = ? AND ativo = 1", (dev,)).fetchone()
    finally:
        con.close()
    if p is None:
        return None
    plano_id, nome, rota, criado_em = p
    return {"id": plano_id, "nome": nome, "tracos": json.loads(rota),
            "criado_em": criado_em}


def pontos_do_plano(caminho_db, plano_id):
    """POS-processamento: cruza a area pintada do plano contra as amostras
    (`derivado`) que o `dev` dele ja tem no banco, na hora, sem nada
    pre-calculado. Devolve as amostras que caem dentro de alguma pincelada -
    e o que a tela usa para colorir por temperatura e montar o Voronoi.
    Roda de novo toda vez que o plano e aberto, entao reflete sync novos
    automaticamente, sem precisar de nenhum passo especial no /sync."""
    con = _abrir(caminho_db)
    try:
        p = con.execute(
            "SELECT dev, rota FROM planos WHERE id = ?", (plano_id,)).fetchone()
        if p is None:
            return []
        dev, rota = p
        tracos = [t for t in json.loads(rota)
                  if t.get("pontos") and t.get("raio_m", 0) > 0]
        if not tracos:
            return []
        try:
            amostras = con.execute(
                "SELECT lat, lon, temp, umid, sessao FROM derivado "
                "WHERE dev = ?", (dev,)).fetchall()
        except sqlite3.Error:
            return []
    finally:
        con.close()
    if not amostras:
        return []

    todos_pontos = [p for t in tracos for p in t["pontos"]]
    lat0 = sum(p[0] for p in todos_pontos) / len(todos_pontos)
    lon0 = sum(p[1] for p in todos_pontos) / len(todos_pontos)

    segmentos = []  # (ax, ay, bx, by, raio_m)
    for t in tracos:
        raio = t["raio_m"]
        m = [_para_metros(p[0], p[1], lat0, lon0) for p in t["pontos"]]
        if len(m) == 1:
            segmentos.append((m[0][0], m[0][1], m[0][0], m[0][1], raio))
        else:
            for i in range(len(m) - 1):
                segmentos.append(
                    (m[i][0], m[i][1], m[i + 1][0], m[i + 1][1], raio))

    resultado = []
    for (alat, alon, temp, umid, sessao) in amostras:
        ax, ay = _para_metros(alat, alon, lat0, lon0)
        dentro = any(
            _dist_ponto_segmento(ax, ay, sx1, sy1, sx2, sy2) <= raio
            for (sx1, sy1, sx2, sy2, raio) in segmentos)
        if dentro:
            resultado.append({"lat": alat, "lon": alon, "temp": temp,
                               "umid": umid, "sessao": sessao})
    return resultado

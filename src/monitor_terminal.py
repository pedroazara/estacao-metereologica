#!/usr/bin/env python3
"""
Monitor de terminal da base: mostra o dado chegando, ao vivo, por SSH.

    python3 monitor_terminal.py                      # broker e servidor locais
    python3 monitor_terminal.py --broker 192.168.0.100

Feito para rodar dentro de um SSH no Raspberry Pi, ao lado do
servidor_telemetria.py que ja esta no ar como servico. Nao substitui o painel
web - responde outra pergunta, que e "esta chegando dado, e o servidor esta
processando?".

POR QUE DUAS FONTES. Este monitor assina o broker MQTT por conta propria E
consulta o /dados do servidor. Parece redundante e nao e: sao os dois lados da
regra de diagnostico do docs/04-telemetria.md. O painel BARRAMENTO mostra o que
o broker esta entregando; os painels GPS/KALMAN mostram o que o servidor fez com
isso. Se o barramento anda e o resto congela, o problema e o servidor Python; se
o barramento nao anda, o problema e o ESP32 ou a rede. Com uma fonte so, essa
distincao exigiria abrir um segundo terminal com mosquitto_sub.

Depende so de paho-mqtt (ja no requirements.txt) e da stdlib - curses e json
vem com o Python. Nada de framework de TUI: a base e um Pi 3B com 1 GB.

    q  sair          z  zerar contadores e barramento
"""

import argparse
import curses
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from collections import deque

import paho.mqtt.client as mqtt

TOPICO_RAIZ = "estacao"
MAX_FEED = 200          # linhas de barramento guardadas (a tela mostra menos)
MAX_HIST = 120          # amostras de temperatura/umidade para o sparkline
TIMEOUT_PACOTE = 5.0    # segundos sem publicar e a estacao e dada como ausente

BLOCOS = "▁▂▃▄▅▆▇█"
BLOCOS_ASCII = ".:-=+*#@"

_lock = threading.Lock()

# Estado alimentado pela thread do MQTT. Fica separado do que vem do servidor
# de proposito: sao duas fontes independentes e misturar as duas esconderia
# justamente a divergencia que este monitor existe para mostrar.
barramento = {
    "conectado": False,
    "erro": "",
    "msgs": 0,
    "bytes": 0,
    "por_topico": {},        # sufixo do topico -> contagem
    "feed": deque(maxlen=MAX_FEED),
    "ultimo": 0.0,
    "marcas": deque(maxlen=64),   # instantes das ultimas mensagens, para a taxa
    "devs": set(),
    "status_lwt": {},        # dev -> ultimo payload de status (retained/LWT)
}

servidor = {"ok": False, "erro": "", "dados": {}, "quando": 0.0}

_hist_temp = deque(maxlen=MAX_HIST)
_hist_umid = deque(maxlen=MAX_HIST)
_parar = threading.Event()
_eventos = queue.Queue()


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------
def ao_conectar(cliente, dados, flags, rc, propriedades=None):
    with _lock:
        barramento["conectado"] = (rc == 0)
        barramento["erro"] = "" if rc == 0 else f"recusado (rc={rc})"
    if rc == 0:
        cliente.subscribe(f"{TOPICO_RAIZ}/+/+", qos=0)


def ao_desconectar(cliente, dados, flags, rc, propriedades=None):
    with _lock:
        barramento["conectado"] = False
        barramento["erro"] = "conexao caiu"


def ao_receber(cliente, dados, msg):
    agora = time.time()
    partes = msg.topic.split("/")
    dev = partes[1] if len(partes) > 2 else "?"
    tipo = partes[-1]

    # O seq e util demais no feed para nao extrair: e com ele que se ve perda
    # de amostra sem comparar relogio. Payload que nao for JSON nao derruba o
    # monitor - o firmware pode publicar status como texto puro.
    seq = None
    try:
        corpo = json.loads(msg.payload.decode("utf-8", "replace"))
        if isinstance(corpo, dict):
            seq = corpo.get("seq")
    except (ValueError, UnicodeDecodeError):
        corpo = None

    with _lock:
        barramento["msgs"] += 1
        barramento["bytes"] += len(msg.payload)
        barramento["por_topico"][tipo] = barramento["por_topico"].get(tipo, 0) + 1
        barramento["ultimo"] = agora
        barramento["marcas"].append(agora)
        barramento["devs"].add(dev)
        if tipo == "status" and isinstance(corpo, dict):
            barramento["status_lwt"][dev] = corpo
        barramento["feed"].appendleft(
            (agora, dev, tipo, seq, len(msg.payload)))


def thread_mqtt(args):
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                          client_id=f"monitor-terminal-{int(time.time())}")
    cliente.on_connect = ao_conectar
    cliente.on_disconnect = ao_desconectar
    cliente.on_message = ao_receber
    # reconnect_delay_set deixa o proprio paho insistir: sem isso, um broker que
    # sobe depois do monitor nunca seria alcancado.
    cliente.reconnect_delay_set(min_delay=1, max_delay=15)
    while not _parar.is_set():
        try:
            cliente.connect(args.broker, args.porta_broker, keepalive=30)
            cliente.loop_forever(retry_first_connection=True)
        except Exception as erro:
            with _lock:
                barramento["conectado"] = False
                barramento["erro"] = str(erro)[:48]
            _parar.wait(3.0)


def thread_servidor(args):
    url = f"http://{args.servidor}:{args.porta_painel}/dados"
    while not _parar.is_set():
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resposta:
                d = json.loads(resposta.read().decode("utf-8"))
            with _lock:
                servidor.update({"ok": True, "erro": "", "dados": d,
                                 "quando": time.time()})
                if d.get("temp") is not None:
                    _hist_temp.append(d["temp"])
                if d.get("umid") is not None:
                    _hist_umid.append(d["umid"])
        except (urllib.error.URLError, OSError, ValueError) as erro:
            with _lock:
                servidor["ok"] = False
                servidor["erro"] = str(getattr(erro, "reason", erro))[:40]
        _parar.wait(1.0)


# ---------------------------------------------------------------------------
# Desenho
# ---------------------------------------------------------------------------
class Tela:
    """Escrita defensiva no curses.

    addstr numa posicao que nao existe levanta curses.error e mata a thread
    principal. Num terminal que o usuario redimensiona no meio da sessao isso
    e garantido, entao todo desenho passa por aqui.
    """

    def __init__(self, janela, ascii_puro):
        self.j = janela
        self.alt, self.larg = janela.getmaxyx()
        self.ascii = ascii_puro

    def txt(self, y, x, texto, atributo=0):
        if y < 0 or y >= self.alt or x < 0 or x >= self.larg:
            return
        espaco = self.larg - x
        if espaco <= 0:
            return
        try:
            self.j.addnstr(y, x, texto, espaco, atributo)
        except curses.error:
            pass

    def titulo(self, y, rotulo, nota=""):
        """Cabecalho de secao: rotulo, regua ate a margem e uma nota opcional."""
        if y >= self.alt:
            return
        self.txt(y, 1, rotulo, curses.color_pair(6) | curses.A_BOLD)
        inicio = 2 + len(rotulo)
        traco = "-" if self.ascii else "─"
        if inicio < self.larg - 1:
            self.txt(y, inicio, traco * (self.larg - inicio - 1),
                     curses.color_pair(7))
        if nota:
            self.txt(y, max(inicio, self.larg - len(nota) - 2), nota,
                     curses.color_pair(3) | curses.A_BOLD)


def sparkline(valores, largura, ascii_puro):
    """Historico recente em uma linha. Escala pelo proprio intervalo.

    Sem escala fixa de proposito: o interesse aqui e a FORMA da variacao
    (subindo, caindo, estavel), nao o valor absoluto - esse ja esta impresso
    do lado. Com escala fixa, a variacao de 0,3 C de uma sala fechada viraria
    uma linha reta e nao diria nada.
    """
    if not valores:
        return ""
    amostras = list(valores)[-largura:]
    menor, maior = min(amostras), max(amostras)
    faixa = maior - menor
    rampa = BLOCOS_ASCII if ascii_puro else BLOCOS
    if faixa < 1e-9:
        return rampa[0] * len(amostras)
    return "".join(
        rampa[min(len(rampa) - 1, int((v - menor) / faixa * (len(rampa) - 1)))]
        for v in amostras)


def num(valor, casas=1, sufixo=""):
    if valor is None:
        return "--"
    try:
        return f"{valor:.{casas}f}{sufixo}"
    except (TypeError, ValueError):
        return str(valor)


def taxa_por_segundo():
    """Mensagens por segundo, medida na janela das ultimas marcas."""
    marcas = list(barramento["marcas"])
    if len(marcas) < 2:
        return 0.0
    intervalo = marcas[-1] - marcas[0]
    return (len(marcas) - 1) / intervalo if intervalo > 0.05 else 0.0


def desenhar(tela, args, inicio):
    j = tela.j
    j.erase()
    agora = time.time()

    with _lock:
        bar_ok = barramento["conectado"]
        bar_erro = barramento["erro"]
        msgs = barramento["msgs"]
        kbytes = barramento["bytes"] / 1024.0
        por_topico = dict(barramento["por_topico"])
        feed = list(barramento["feed"])
        ultimo_bar = barramento["ultimo"]
        devs = sorted(barramento["devs"])
        status_lwt = dict(barramento["status_lwt"])
        srv_ok = servidor["ok"]
        srv_erro = servidor["erro"]
        d = dict(servidor["dados"])
        temp_hist = list(_hist_temp)
        umid_hist = list(_hist_umid)

    taxa = taxa_por_segundo()
    idade = agora - ultimo_bar if ultimo_bar else None
    fluindo = idade is not None and idade < TIMEOUT_PACOTE

    BOM = curses.color_pair(1) | curses.A_BOLD
    RUIM = curses.color_pair(2) | curses.A_BOLD
    ALERTA = curses.color_pair(3) | curses.A_BOLD
    ROTULO = curses.color_pair(4)
    VALOR = curses.color_pair(5) | curses.A_BOLD
    FRACO = curses.color_pair(7)

    ponto = "*" if tela.ascii else "●"

    # 80x24 e o tamanho padrao de um terminal, e e nele que este monitor mais
    # vai ser aberto. Com o espacamento folgado, o BARRAMENTO - que e a razao de
    # existir da tela - ficava com UMA linha. Entao o layout aperta: some com as
    # linhas em branco entre secoes e junta o IMU em duas linhas, devolvendo
    # ~5 linhas de feed. Acima de 30 linhas, respira.
    compacto = tela.alt < 30
    pular = 1 if compacto else 2

    # O servidor guarda o ultimo estado na memoria e continua servindo ele
    # depois que a estacao para de publicar. Sem marcar isso, "passos 524" fica
    # na tela com cara de leitura viva - e um monitor cuja unica funcao e
    # responder "esta chegando dado?" nao pode mentir justamente nisso. Entao
    # AMBIENTE/GPS/IMU esmaecem e ganham a idade no cabecalho quando congelam.
    # A idade vem do servidor, nao do barramento deste monitor: e o servidor
    # quem produziu esses numeros.
    ultimo_srv = d.get("ultimo_pacote") or 0
    idade_srv = (agora - ultimo_srv) if ultimo_srv else None
    congelado = idade_srv is None or idade_srv > TIMEOUT_PACOTE
    nota_gelo = ""
    if congelado and idade_srv is not None:
        nota_gelo = f" congelado ha {idade_srv:.0f}s "
    elif congelado:
        nota_gelo = " sem dado ainda "
    # VAL vale para o que o servidor produziu; os contadores do proprio monitor
    # (msgs, taxa, feed) continuam com VALOR - eles estao vivos de qualquer jeito.
    VAL = (curses.color_pair(7) | curses.A_DIM) if congelado else VALOR

    # ---- cabecalho ----------------------------------------------------
    cab = " ESTACAO METEOROLOGICA - monitor da base "
    tela.txt(0, 0, cab.ljust(tela.larg), curses.color_pair(8) | curses.A_BOLD)
    relogio = time.strftime("%H:%M:%S")
    tela.txt(0, max(0, tela.larg - len(relogio) - 1), relogio,
             curses.color_pair(8) | curses.A_BOLD)

    y = 2
    tela.txt(y, 1, ponto, BOM if bar_ok else RUIM)
    tela.txt(y, 3, "broker", ROTULO)
    tela.txt(y, 11, f"{args.broker}:{args.porta_broker}" if bar_ok
             else (bar_erro or "sem conexao"), VALOR if bar_ok else RUIM)
    coluna = max(42, tela.larg // 2)
    tela.txt(y, coluna, ponto, BOM if srv_ok else RUIM)
    tela.txt(y, coluna + 2, "servidor", ROTULO)
    tela.txt(y, coluna + 12,
             "processando" if srv_ok else (srv_erro or "fora do ar"),
             VALOR if srv_ok else RUIM)

    y += 1
    tela.txt(y, 3, "estacao", ROTULO)
    nome = d.get("dev") or (devs[0] if devs else None)
    if nome:
        # O LWT retido e a unica forma de saber que a estacao caiu sem inventar
        # heuristica de timeout - o broker anuncia sozinho.
        caiu = status_lwt.get(nome, {}).get("online") is False
        # Truncar aqui e obrigatorio: a coluna da direita comeca em `coluna` e o
        # addnstr so corta na margem da TELA, nao na do vizinho - sem isto o
        # aviso de offline atropela o contador de mensagens.
        cabe = max(8, coluna - 12)
        if caiu and not fluindo:
            aviso = f"{nome}  (offline - LWT)"
            tela.txt(y, 11, aviso[:cabe], ALERTA)
        else:
            tela.txt(y, 11, nome[:cabe], VALOR)
    else:
        tela.txt(y, 11, "nenhuma publicou ainda", FRACO)

    tela.txt(y, coluna + 2, "msgs", ROTULO)
    tela.txt(y, coluna + 12,
             f"{msgs}   {taxa:.1f}/s   {kbytes:.0f} KB", VALOR)

    y += 1
    tela.txt(y, 3, "fluxo", ROTULO)
    if idade is None:
        tela.txt(y, 11, "aguardando a primeira mensagem", FRACO)
    elif fluindo:
        detalhe = "  ".join(f"{t} {n}" for t, n in sorted(por_topico.items()))
        tela.txt(y, 11, detalhe or "ativo", VALOR)
    else:
        tela.txt(y, 11, f"parado ha {idade:.0f}s", ALERTA)

    perdidos = d.get("perdidos")
    if perdidos:
        tela.txt(y, coluna + 2, "perdidas", ROTULO)
        tela.txt(y, coluna + 12, str(perdidos), ALERTA)

    # ---- ambiente -----------------------------------------------------
    y += pular
    tela.titulo(y, "AMBIENTE", nota_gelo)
    y += 1
    larg_spark = max(10, min(48, tela.larg - 34))
    cor_temp = (curses.color_pair(7) | curses.A_DIM) if congelado \
        else curses.color_pair(9)
    cor_umid = (curses.color_pair(7) | curses.A_DIM) if congelado \
        else curses.color_pair(10)
    tela.txt(y, 2, "temperatura", ROTULO)
    tela.txt(y, 16, num(d.get("temp"), 1, " C").rjust(8), VAL)
    tela.txt(y, 26, sparkline(temp_hist, larg_spark, tela.ascii), cor_temp)
    y += 1
    tela.txt(y, 2, "umidade", ROTULO)
    tela.txt(y, 16, num(d.get("umid"), 1, " %").rjust(8), VAL)
    tela.txt(y, 26, sparkline(umid_hist, larg_spark, tela.ascii), cor_umid)

    # ---- GPS ----------------------------------------------------------
    y += pular
    tela.titulo(y, "GPS", nota_gelo)
    y += 1
    tem_fix = bool(d.get("fix"))
    tela.txt(y, 2, "fix", ROTULO)
    if congelado:
        # "fix sim" em verde numa estacao que parou de publicar e a pior mentira
        # possivel desta tela: sugere GPS travado agora.
        tela.txt(y, 16, "sim" if tem_fix else "nao", VAL)
    else:
        tela.txt(y, 16, "sim" if tem_fix else "nao", BOM if tem_fix else ALERTA)
    tela.txt(y, 24, "sat", ROTULO)
    tela.txt(y, 28, str(d.get("sat", "--")), VAL)
    tela.txt(y, 34, "hdop", ROTULO)
    tela.txt(y, 39, num(d.get("hdop"), 2), VAL)
    tela.txt(y, 46, "alt", ROTULO)
    tela.txt(y, 50, num(d.get("alt"), 1, " m"), VAL)

    y += 1
    lat, lon = d.get("lat"), d.get("lon")
    tela.txt(y, 2, "cru", ROTULO)
    tela.txt(y, 16, "--" if lat is None else f"{lat:.6f}, {lon:.6f}", VAL)

    y += 1
    klat, klon = d.get("kf_lat"), d.get("kf_lon")
    tela.txt(y, 2, "kalman", ROTULO)
    if klat is None:
        tela.txt(y, 16, "sem solucao ainda", FRACO)
    else:
        mais = "+/-" if tela.ascii else "±"
        parado = d.get("kf_parado")
        tela.txt(y, 16, f"{klat:.6f}, {klon:.6f}", VAL)
        tela.txt(y, 42, f"{mais}{num(d.get('kf_sigma'), 1)} m",
                 VAL if congelado else curses.color_pair(9))
        tela.txt(y, 53, num(d.get("kf_vel"), 1, " km/h"), VAL)
        if congelado:
            tela.txt(y, 66, "parado" if parado else "movendo", VAL)
        else:
            tela.txt(y, 66, "parado" if parado else "movendo",
                     FRACO if parado else BOM)

    y += 1
    tela.txt(y, 2, "passos", ROTULO)
    tela.txt(y, 16, str(d.get("passos", "--")), VAL)
    tela.txt(y, 24, "pontos", ROTULO)
    tela.txt(y, 32, f"{len(d.get('pontos') or [])} cru / "
                    f"{len(d.get('kf_pontos') or [])} kf", VAL)

    # ---- IMU ----------------------------------------------------------
    y += pular
    tela.titulo(y, "IMU", nota_gelo)
    y += 1
    grau = " deg" if tela.ascii else "°"
    tela.txt(y, 2, "acel g", ROTULO)
    tela.txt(y, 16, f"x {num(d.get('ax'), 2)}   y {num(d.get('ay'), 2)}   "
                    f"z {num(d.get('az'), 2)}", VAL)
    y += 1
    tela.txt(y, 2, "giro d/s", ROTULO)
    tela.txt(y, 16, f"x {num(d.get('gx'), 1)}   y {num(d.get('gy'), 1)}   "
                    f"z {num(d.get('gz'), 1)}", VAL)

    if compacto:
        # Atitude condensada numa coluna a direita das duas linhas acima, em vez
        # de gastar uma terceira linha so para ela.
        tela.txt(y - 1, 48, f"r {num(d.get('roll'), 1)}  "
                            f"p {num(d.get('pitch'), 1)}{grau}", VAL)
        tela.txt(y, 48, f"yaw {num(d.get('yaw'), 1)}{grau}   "
                        f"n {d.get('amostras_imu', '--')}", VAL)
    else:
        tela.txt(y - 1, 48, f"roll {num(d.get('roll'), 1)}{grau}", VAL)
        tela.txt(y, 48, f"pitch {num(d.get('pitch'), 1)}{grau}", VAL)
        y += 1
        tela.txt(y, 2, "amostras", ROTULO)
        tela.txt(y, 16, str(d.get("amostras_imu", "--")), VAL)
        tela.txt(y, 48, f"yaw {num(d.get('yaw'), 1)}{grau}", VAL)

    # ---- barramento ---------------------------------------------------
    y += pular
    tela.titulo(y, "BARRAMENTO (MQTT cru)")
    y += 1
    rodape = 1
    espaco = tela.alt - y - rodape
    if espaco > 0:
        if not feed:
            tela.txt(y, 2, "nada publicado ainda - o ESP32 esta ligado e na "
                           "mesma rede?", FRACO)
        for i, (quando, dev, tipo, seq, tamanho) in enumerate(feed[:espaco]):
            linha = y + i
            # O mais recente em destaque; o resto esmaece para o olho achar
            # o topo da lista sem procurar.
            atr = VALOR if i == 0 else FRACO
            hora = time.strftime("%H:%M:%S", time.localtime(quando))
            tela.txt(linha, 2, f"{hora}.{int(quando * 10) % 10}", FRACO)
            tela.txt(linha, 13, f"{dev}/{tipo}".ljust(24), atr)
            tela.txt(linha, 38,
                     f"seq {seq}".ljust(12) if seq is not None else "".ljust(12),
                     atr)
            tela.txt(linha, 51, f"{tamanho} B", atr)

    # ---- rodape -------------------------------------------------------
    ajuda = " q sair    z zerar contadores "
    decorrido = int(agora - inicio)
    direita = f" no ar ha {decorrido // 60}m{decorrido % 60:02d}s "
    tela.txt(tela.alt - 1, 0, ajuda.ljust(tela.larg),
             curses.color_pair(8))
    tela.txt(tela.alt - 1, max(0, tela.larg - len(direita)), direita,
             curses.color_pair(8))
    j.noutrefresh()
    curses.doupdate()


def zerar():
    with _lock:
        barramento["msgs"] = 0
        barramento["bytes"] = 0
        barramento["por_topico"].clear()
        barramento["feed"].clear()
        barramento["marcas"].clear()
        _hist_temp.clear()
        _hist_umid.clear()


def laco(janela, args):
    curses.curs_set(0)
    janela.nodelay(True)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)     # bom
        curses.init_pair(2, curses.COLOR_RED, -1)       # ruim
        curses.init_pair(3, curses.COLOR_YELLOW, -1)    # alerta
        curses.init_pair(4, curses.COLOR_CYAN, -1)      # rotulo
        curses.init_pair(5, -1, -1)                     # valor
        curses.init_pair(6, curses.COLOR_WHITE, -1)     # titulo de secao
        curses.init_pair(7, curses.COLOR_BLUE, -1)      # fraco / regua
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(9, curses.COLOR_MAGENTA, -1)   # temperatura
        curses.init_pair(10, curses.COLOR_BLUE, -1)     # umidade

    inicio = time.time()
    while not _parar.is_set():
        tecla = janela.getch()
        if tecla in (ord("q"), ord("Q")):
            break
        if tecla in (ord("z"), ord("Z")):
            zerar()
        if tecla == curses.KEY_RESIZE:
            janela.clear()

        tela = Tela(janela, args.ascii)
        if tela.alt < 14 or tela.larg < 60:
            janela.erase()
            try:
                janela.addnstr(0, 0, "terminal pequeno demais "
                                     "(minimo 60x14)", tela.larg)
            except curses.error:
                pass
            janela.refresh()
        else:
            desenhar(tela, args, inicio)
        time.sleep(0.2)


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--broker", default="localhost",
                   help="endereco do broker MQTT")
    p.add_argument("--porta-broker", type=int, default=1883)
    p.add_argument("--servidor", default="localhost",
                   help="onde roda o servidor_telemetria.py")
    p.add_argument("--porta-painel", type=int, default=8771)
    p.add_argument("--ascii", action="store_true",
                   help="so ASCII, para terminal sem UTF-8")
    args = p.parse_args()

    threading.Thread(target=thread_mqtt, args=(args,), daemon=True).start()
    threading.Thread(target=thread_servidor, args=(args,), daemon=True).start()
    try:
        curses.wrapper(laco, args)
    except KeyboardInterrupt:
        pass
    finally:
        _parar.set()


if __name__ == "__main__":
    main()

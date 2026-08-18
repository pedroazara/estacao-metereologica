# Base de processamento — Raspberry Pi

Documento de referência da **base**: o Raspberry Pi que recebe o dado coletado
em campo, roda o filtro de Kalman, guarda o histórico e mostra tudo para o
pesquisador. Decidido em 17/08/2026.

Ver [04-telemetria.md](04-telemetria.md) para a arquitetura completa e o
contrato de dados. Este documento cobre só a máquina da base: para que ela
serve, como preparar e o que ela não aguenta.

---

## Por que um Pi e não o notebook

Tecnicamente o notebook é superior ao Pi 3B em tudo — CPU, RAM, disco, tela.
Se o critério fosse só "rodar o Kalman", o Pi não acrescentaria nada.

O critério é outro: **transformar o projeto em instrumento.** Com o notebook, o
pesquisador precisa ter Python instalado, criar venv, descobrir que a porta é
`/dev/ttyUSB0` e lembrar de rodar o script — e se fechar a tampa para ir a uma
reunião, a coleta morre. Com a base, ele liga na tomada e funciona.

Consequências que só a base entrega:

- **Sempre ligada.** O `status` do MQTT é `retained` + LWT: o broker anuncia
  sozinho quando o ESP32 cai. Esse aviso não vale nada se ninguém estiver
  escutando — com notebook, "o ESP32 caiu às 3h" simplesmente não é registrado.
- **Endereço fixo.** O ESP32 precisa de um destino que não mude quando alguém
  abre ou fecha um notebook.
- **Histórico que acumula.** SQLite crescendo por meses, não CSV solto na pasta
  de quem por acaso estava com a máquina.

**Isto não é uma escolha de arquitetura, é de implantação.** O
`src/servidor_telemetria.py` roda igual nos dois. Desenvolva no PC (iteração
rápida), implante no Pi (o produto). Zero reescrita.

---

## O hardware e os limites dele

Modelo em uso: **Raspberry Pi 3 Model B V1.2** — quad-core Cortex-A53 1,2 GHz,
1 GB de RAM, Wi-Fi 2,4 GHz.

CPU e RAM **não** são gargalo para Mosquitto + SQLite + Kalman + servidor web.
Sobra folga. Os limites reais são outros três:

| Limite | Consequência | O que fazer |
|---|---|---|
| **~2,5–3,5 W e entrada microUSB** | *undervoltage* fácil (o raio na tela): travamento intermitente e SD corrompido | fonte de **2,5 A** de verdade, cabo grosso e curto. **A base fica na tomada, nunca vai a campo.** |
| **1 GB de RAM** | Chromium num painel ao vivo usa swap **no cartão SD** — lento e desgastando | painel leve (ver abaixo); nada de desktop completo |
| **Cartão SD** | corrompe com desligamento abrupto e desgasta com escrita contínua | banco em pendrive/SSD USB; rootfs read-only quando virar produto |

> **O 3B é protótipo de gabinete, não a versão final.** Ele é ótimo como
> servidor e marginal como quiosque com touchscreen. Quando a tela entrar, um
> Pi 4 ou 5 resolve o gargalo **sem trocar uma linha de código** — a decisão de
> hardware ficou isolada de propósito.

### Regra do painel: leve por projeto

O painel que vai rodar na tela da base tem que nascer leve, não ser otimizado
depois:

- HTML + `<canvas>` puro. Sem framework.
- **Sem tiles online** (Leaflet/OSM). Além do peso, em campo não há internet —
  plote a trajetória em coordenadas locais, como o `src/mapa_ao_vivo.py` já faz.
- Sem animação contínua; redesenhe só quando chega dado.

O peso vem do framework, não do Pi. Painel leve roda liso até no 3B.

---

## Preparação — passo a passo

### 1. Gravar o cartão

Use o **Raspberry Pi Imager** e abra a engrenagem de configuração **antes** de
gravar: dá para definir hostname, habilitar **SSH**, criar o usuário e informar
a rede Wi-Fi. Isso elimina a novela de monitor + teclado no primeiro boot.

Sistema: **Raspberry Pi OS Lite (64-bit)** — sem desktop. Com 1 GB de RAM não
se gasta memória com interface gráfica; a tela touch vem depois, e vai ser um
quiosque mínimo, não um ambiente completo.

### 2. Entrar por SSH

```bash
ssh pedro@raspberrypi.local
```

Se o `.local` não resolver, pegue o IP no roteador. Vale se acostumar a nunca
plugar monitor — é assim que a base vai ser operada.

### 3. Dependências

```bash
sudo apt update && sudo apt install -y mosquitto mosquitto-clients git python3-venv
```

Clone o repositório, crie o venv e instale o `requirements.txt` normalmente —
as dependências (`pyserial`, `minimalmodbus`, `paho-mqtt`) são Python puro e
não têm problema em ARM.

### 4. Broker

```bash
sudo cp servidor/mosquitto-estacao.conf /etc/mosquitto/conf.d/estacao.conf && sudo systemctl restart mosquitto
```

O arquivo de config **não é opcional** — o Mosquitto 2.0 sem configuração só
escuta em `localhost` e recusa cliente anônimo. As armadilhas dessa instalação
(chave duplicada, `exit-code 3` sem causa visível) estão documentadas na seção
*Broker MQTT* de [04-telemetria.md](04-telemetria.md).

### 5. Teste de ponta a ponta, sem hardware nenhum

Esta é a tarefa que vale mais — ela exercita **exatamente** o caminho que o
ESP32 vai usar, usando o simulador que já existe.

No Pi, suba o servidor:

```bash
python3 src/servidor_telemetria.py
```

E do **PC**, aponte o simulador para o Pi:

```bash
python3 src/simular_estacao.py --broker raspberrypi.local
```

O truque é rodar o simulador de outra máquina: assim você testa dispositivo
externo → broker no Pi → filtro → painel, que é a topologia real. Abra
`http://raspberrypi.local:8771` do PC e veja o rastro cru e o filtrado.

Num segundo SSH, espie o barramento cru — é o melhor jeito de entender o que
está trafegando:

```bash
mosquitto_sub -h localhost -t 'estacao/#' -v
```

Regra de diagnóstico: se aparece aqui, o problema está no servidor Python; se
não aparece, está no ESP32 ou na rede.

### 5.1 Monitor de terminal

`src/monitor_terminal.py` é a tela de operação da base por SSH — a resposta
rápida para *"está chegando dado, e o servidor está processando?"*. Na base ele
está instalado como um comando só:

```bash
estacao
```

O atalho é `bin/estacao`, ligado em `/usr/local/bin/estacao` por symlink — o
script resolve o próprio caminho e acha a raiz do projeto, então funciona de
qualquer diretório e continua válido se o repositório for movido. Não confundir
com `bin/estacao-metereologica`, que abre o painel web do sensor ligado por USB;
este aqui não fala com sensor nenhum, só olha o que chega pela rede. Instalar
noutra máquina:

```bash
sudo ln -sf "$PWD/bin/estacao" /usr/local/bin/estacao
```

Sem o atalho, o caminho longo é:

```bash
~/estacao/.venv/bin/python3 ~/estacao/src/monitor_terminal.py
```

Ele aplica a regra de diagnóstico acima **na mesma tela**, e é por isso que lê
de duas fontes de propósito: assina o broker por conta própria (painel
`BARRAMENTO`, o que o MQTT está entregando) e consulta o `/dados` do servidor
(painéis `GPS`/`IMU`, o que o Kalman fez com aquilo). Barramento andando com o
resto congelado acusa o servidor Python; barramento parado acusa o ESP32 ou a
rede. Com uma fonte só isso exigiria um segundo terminal.

Depende apenas de `paho-mqtt` e da stdlib (`curses`) — nada de framework de TUI,
pela mesma regra de painel leve. O layout se adapta: abaixo de 30 linhas ele
comprime as seções para não sacrificar o feed do barramento, que é o ponto da
tela. Use `--ascii` em terminal sem UTF-8, e `--broker`/`--servidor` para
monitorar a base a partir do notebook.

> **Confira o fuso antes de confiar no carimbo.** A imagem do Raspberry Pi OS
> pode nascer em `Europe/London`; como o `servidor_telemetria.py` grava com
> `datetime.now()` (hora local, sem offset), o CSV inteiro sai com hora errada e
> nada no arquivo denuncia isso. Corrija com
> `sudo timedatectl set-timezone America/Sao_Paulo` e **reinicie o serviço**.

### 6. Adaptador RS485-USB no Pi

A base normalmente não lê sensor — quem lê é o ESP32. Mas vale validar que o
`src/` roda igual em ARM, e ter o caminho direto disponível para bancada:

```bash
sudo usermod -aG dialout $USER && ls -l /dev/ttyUSB*
```

Depois de relogar, rode o `src/scan_barramento.py`. Ver
[01-guia-conexao-linux.md](01-guia-conexao-linux.md) para permissões e driver.

### 7. Systemd — o que transforma script em instrumento

Sem isto, a base é um computador com um script. Com isto, é um aparelho.
Crie `/etc/systemd/system/telemetria.service`:

```ini
[Unit]
Description=Servidor de telemetria da estacao
After=network.target mosquitto.service

[Service]
ExecStart=/home/pedro/estacao/.venv/bin/python3 /home/pedro/estacao/src/servidor_telemetria.py
WorkingDirectory=/home/pedro/estacao/src
Restart=always
User=pedro

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now telemetria && systemctl status telemetria
```

`Restart=always` é o detalhe que importa: se o processo morrer, volta sozinho.
Instrumento não pede para ser reiniciado.

### 8. Dados fora do cartão SD

Banco crescendo 24/7 em cartão SD é desgaste garantido. Monte um pendrive ou
SSD USB e aponte o banco para lá — o SD fica só com o sistema. Use `UUID=` no
`/etc/fstab` (o `/dev/sda1` muda de nome conforme a ordem de conexão) e a opção
`nofail`, para o Pi não travar o boot se o pendrive não estiver plugado.

---

## Divisão de responsabilidades

| | ESP32 (campo) | Raspberry Pi (base) |
|---|---|---|
| lê sensores RS485 | ✅ | ❌ |
| carimba `seq`, `ts`, `ms` | ✅ | ❌ |
| grava tudo no cartão SD | ✅ | ❌ |
| painel de conferência (dado cru) | ✅ | ❌ |
| filtro de Kalman | ❌ | ✅ |
| banco histórico | ❌ | ✅ |
| painel de análise / tela touch | ❌ | ✅ |
| reprocessamento com outra sintonia | ❌ | ✅ |
| envio para API externa | ❌ | ✅ |

O ESP32 é um **sensor honesto**: lê, carimba, guarda, publica, não perde dado.
Toda interpretação é da base.

---

## Pendente

- [ ] Tela touch. Preferir a **DSI oficial de 7"** a HDMI + USB: não ocupa porta
      USB, não precisa de conversor, driver de toque já vem no sistema. Somar o
      consumo dela ao dimensionar a fonte.
- [ ] Quiosque no boot (navegador leve em tela cheia via systemd), depois que o
      painel leve existir.
- [ ] **Rootfs read-only (overlayfs).** Vira obrigatório quando for produto: um
      instrumento é desligado no botão da régua, sem shutdown gracioso.
- [ ] Como o ESP32 acha a base: reserva de DHCP no roteador ou mDNS
      (`ESPmDNS`). Decidir antes da primeira sincronização de verdade.
- [ ] Acesso remoto (Tailscale ou equivalente), se for preciso abrir o painel
      fora da rede local.
- [ ] Banco em duas camadas (cru imutável + derivado recomputável) — ver
      [04-telemetria.md](04-telemetria.md).

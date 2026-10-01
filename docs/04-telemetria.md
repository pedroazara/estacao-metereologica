# Telemetria — arquitetura do sistema

## Arquitetura aprovada em 01/10/2026

O ESP32 fica responsável por **três sensores externos**: luminosidade GY-302,
temperatura auxiliar KY-028 e temperatura/umidade do sensor ambiental RS485.
O **celular fornece GPS, movimento e som**. WTGAHRS3 (GPS + IMU), KY-038 e
KY-013 saem do conjunto previsto para a estação. Esta decisão não acrescenta
outros sensores à placa.

| Origem | Medida | Interface prevista |
|---|---|---|
| ESP32 · GY-302/BH1750 | Luminosidade | I²C: SDA GPIO21, SCL GPIO22; endereço `0x23` |
| ESP32 · KY-028 | Temperatura auxiliar | AO no GPIO35; DO não utilizado pelo firmware principal |
| ESP32 · ambiental RS485 | Temperatura e umidade | Conversor RS485 ↔ UART2: RX GPIO16, TX GPIO17; Modbus `0x01`, 9600 8N1 |
| Celular · GPS | Localização e percurso | Serviço de localização do aplicativo |
| Celular · sensores de movimento | Movimento | Coleta ainda pendente de implementação e validação |
| Celular · microfone | Níveis sonoros | Captura e processamento no aplicativo, sem guardar áudio |

O GY-302 e o KY-028 usam 3,3 V na configuração documentada do firmware.
A tensão de alimentação do sensor ambiental depende do módulo utilizado;
RS485 define a comunicação, não essa tensão. As conexões da placa devem ser
conferidas com [a documentação do firmware](../firmware/README.md) e os
arquivos de hardware do projeto.

```text
GY-302 ── I²C ──────────┐
KY-028 ── analógico ────┼── ESP32 ── BLE ──┐
ambiental ── RS485 ─────┘                 │
                                         ▼
                            aplicativo no celular
                          + GPS e níveis sonoros
                          + movimento (pendente)
                                         │
                              sessão local no celular
                                         │
                           envio HTTP para a base
                                         ▼
                         Raspberry Pi: guarda e análise
```

A base continua responsável por receber sessões, manter o histórico e apoiar
as análises. O filtro descrito em [03-filtro-kalman.md](03-filtro-kalman.md)
e as ferramentas WTG permanecem como referência histórica. A integração com
movimento medido pelo telefone ainda precisa ser definida e validada.

## Estado implementado e pendências

A decisão de hardware acima é o destino da migração; **o código ainda contém
partes da arquitetura anterior**.

- O aplicativo já obtém o GPS do telefone e grava registros `gps`. A recepção
  por BLE usada pelo app inclui os dados ambientais da estação; posição e IMU
  enviados pelo ESP32 são ignorados nesse fluxo.
- A coleta já pode gravar níveis sonoros como `som_celular`. O áudio é
  processado no telefone, sem ser salvo ou enviado. A captura atual ocorre
  com o app em primeiro plano; ao ser interrompida, a coleta que a utiliza é
  encerrada e salva. dB SPL estimado depende de calibração; dBFS é nível
  relativo à captura.
- Não há coleta implementada dos sensores de movimento do telefone nos
  arquivos inspecionados do app. GPS com velocidade e rumo não equivale a
  uma série de acelerômetro/giroscópio. Ainda faltam definição de medidas,
  unidades, frequência, carimbo e contrato, além da implementação.
- O firmware principal ainda possui aquisição e configuração do WTGAHRS3,
  KY-038 e KY-013. O app também mantém opções e campos desses módulos. Sua
  remoção ou isolamento como legado exige uma alteração de código posterior;
  esta revisão de documentação não a executa.
- O cartão SD no ESP32 continua sendo um plano antigo, não a custódia atual
  da coleta. Hoje as sessões são gravadas no telefone e podem ser enviadas à
  base por HTTP. O envio marca a sessão como enviada e preserva o arquivo
  local.

## Registros e transporte existentes

A implementação em [gravador.dart](../app/lib/armazenamento/gravador.dart)
grava JSONL com `src`, `ts_app` e `m`. `ts_app` é o horário registrado pelo
telefone ao anexar o dado; para os dados recebidos da estação, não representa
um carimbo de aquisição gerado no ESP32.

Os registros de GPS já incluem latitude, longitude, precisão, velocidade e
rumo em `m`. Os de som já incluem nível em dBFS, nível em dB SPL estimado
quando calibrado, estado de calibração, janela e indicadores de saturação ou
ausência de sinal. As fontes são, respectivamente,
[estacao_ble.dart](../app/lib/ble/estacao_ble.dart) e
[central_sensores.dart](../app/lib/coleta/central_sensores.dart).
Nenhum novo formato de movimento é definido por este documento.

O [sincronizador](../app/lib/rede/sincronizador.dart) envia o arquivo para
`POST /sync` na base, como `application/x-ndjson`, e preserva a cópia local.
BLE recebe as leituras da estação durante a coleta; HTTP envia a sessão
salva para a base. O MQTT e os contratos de posição/IMU do ESP32 ainda
presentes no repositório pertencem ao caminho anterior e precisam ser
considerados ao migrar firmware, testes e base.

## Próximas etapas da migração

1. Alinhar firmware, opções do aplicativo e documentação de montagem aos três
   sensores externos aprovados, mantendo a leitura ambiental independente do
   WTGAHRS3.
2. Implementar e validar a coleta de movimento no telefone, incluindo os
   critérios de disponibilidade dos sensores e continuidade da coleta.
3. Ajustar ingestão, análise e testes ao contrato efetivamente adotado para o
   telefone. Preservar a leitura das sessões históricas sem atribuir seus
   dados WTG à nova origem.
4. Validar uma sessão completa: três sensores da estação, GPS e som do
   telefone, gravação local e envio para a base. Movimento só entra nessa
   validação depois de implementado.

## Referência da arquitetura anterior — 17/08/2026

**As seções abaixo preservam o projeto anterior para consulta.** Descrevem
WTGAHRS3 no ESP32, proposta de cartão SD, envelopes e cálculos de volume de
GPS/IMU que não definem a montagem atual. Algumas partes foram implementadas;
outras ficaram como plano. Em especial, as afirmações de que o app nativo
está adiado, o celular é uma fonte secundária e o ESP32 grava sempre em SD
não descrevem o estado atual. A arquitetura e as pendências vigentes estão
nas seções acima.

Documento de referência de como a estação vira um sistema de telemetria.
Decidido em 17/08/2026 (revisado no mesmo dia, ver
[Histórico da decisão](#histórico-da-decisão)): o sistema tem **dois lados com
papéis distintos**, e a divisão não é "quem implementa melhor", é o que cada um
faz bem.

- **ESP32 (`firmware/`) — campo.** Lê os sensores, carimba, grava tudo em
  cartão SD, mostra o cru num painel de conferência. É um *sensor honesto*: não
  interpreta nada.
- **Raspberry Pi (`src/`) — base.** Recebe, roda o Kalman em lote, persiste,
  analisa e prepara o envio. Toda interpretação numérica mora aqui.

O objetivo declarado não é só processar dado: é um **produto** — uma base com
tela touch para o pesquisador analisar a coleta. Ver
[05-base-raspberry-pi.md](05-base-raspberry-pi.md) para a máquina da base,
`firmware/README.md` para o estado do hardware e
[03-filtro-kalman.md](03-filtro-kalman.md) para a teoria do filtro.

Este documento é a base; sensores novos entram depois sem mudar a arquitetura
— ver a seção [Adicionar um sensor novo](#adicionar-um-sensor-novo).

---

### Visão geral

```
CAMPO
─────
WTGAHRS3 (GPS+IMU) ─┐
sensor ambiental    ├── RS485 (Modbus) ──┐
BME280 (pressão)    ── I2C ──────────────┤
                                          ▼
                                       ESP32
                              (aquisição → carimbo → custódia)
                                          │
                        ┌─────────────────┴─────────────────┐
                        ▼                                   ▼
                 cartão SD (cru)              Wi-Fi → celular do pesquisador
              custódia, sempre grava            painel de conferência (cru)

        ····················· volta para a base ·····················

BASE
────
                                       ESP32
                                          │
                            sincronização em lote (Wi-Fi)
                                          ▼
                                  Raspberry Pi
                                          │
                              Kalman (lote, reprocessável)
                                          ▼
                              SQLite (cru + derivado)
                                          │
                          ┌───────────────┴───────────────┐
                          ▼                               ▼
                  tela touch (análise)              API externa
```

O ESP32 lê, carimba e **guarda**. A base interpreta, persiste e mostra.
Nenhuma lógica de negócio mora no navegador.

**Não há Kalman ao vivo.** Todo dado chega em lote e é processado como lote —
ver [Por que o Kalman fica na base](#por-que-o-kalman-fica-na-base).

---

### Firmware: aquisição, carimbo e custódia

Modbus é uma transação por vez — os dois sensores RS485 (WTGAHRS3 em `0x50`,
ambiental em `0x01`) dividem o mesmo par A/B e a mesma UART2, então a leitura
precisa intercalar: IMU rápido (~10 Hz), GPS a 1 Hz, ambiente a cada alguns
segundos. É a mesma intercalação que `src/painel.py` já faz do lado Python.

A prioridade do firmware, nesta ordem: **não perder amostra**, **carimbar
certo**, **gravar no SD**. Publicar na rede vem depois de tudo isso — se a rede
falhar, o dado já está salvo.

Ponto de atenção real: **rede bloqueia**. Uma reconexão de Wi-Fi leva segundos,
não milissegundos. Se a leitura dos sensores estiver no mesmo fluxo que a
rede, uma reconexão trava a aquisição junto — e o `dt` da amostra fica errado
bem na hora que a rede pisca, que é justamente o cenário de campo. A gravação
no SD tem que estar do lado da aquisição, nunca do lado da rede.

**Começar simples:** máquina de estados por `millis()`, sem bloquear em nada,
no estilo que `firmware/painel_wifi/painel_wifi.ino` já usa (leitura a cada
2 s, servidor sempre serve o último valor em cache). Só migrar para tasks
FreeRTOS explícitas (`xTaskCreate`, uma fila entre aquisição e rede) se a
reconexão de rede começar a comer amostras de verdade — o ESP32 já roda
FreeRTOS por baixo mesmo em Arduino puro (`loop()` é uma task no core 1,
Wi-Fi já roda no core 0), então subir para tasks explícitas depois não exige
biblioteca nova, só reorganizar o código existente.

---

### Contrato de dados

Um envelope único para toda fonte. O ESP32 emite **só medida crua** — o campo
`kf` é preenchido pela base, nunca pelo firmware:

```json
{
  "dev": "esp32-01",
  "seq": 14832,
  "ts": 1786982400.250,
  "ms": 3812445,
  "src": "gps",
  "buf": false,
  "m":  { "lat": -21.2255, "lon": -44.9001, "alt": 918.3, "vel": 1.4, "sat": 9, "hdop": 1.1 }
}
```

| Campo | Para quê |
|---|---|
| `dev` | identifica a estação (relevante já quando houver mais de uma) |
| `seq` | contador monotônico — detecta amostra perdida sem comparar tempo |
| `ts` | timestamp UTC absoluto, **carimbado no ESP32**, nunca no servidor |
| `ms` | `millis()` desde o boot — âncora de tempo, **sempre presente** |
| `src` | qual sensor/bloco gerou a amostra (`gps`, `imu`, `amb`, ...) |
| `buf` | `true` quando a amostra veio do cartão, chegou atrasada |
| `m` | medida crua, unidade fixa por chave, sem heterogeneidade de escala |
| `kf` | saída do Kalman — **adicionado pela base ao persistir**, nunca trafega vindo do ESP32 |

**Por que carimbar no ESP32:** ele não tem RTC — liga achando que é 1970. O
GPS resolve isso: no primeiro fix, sincroniza o relógio interno pelo tempo do
GPS. Se a base carimbasse na chegada, uma sessão inteira que passou horas no
cartão entraria no banco com a hora errada.

**Por que `ms` existe — e por que ele não é redundante.** Enquanto havia
publicação ao vivo, a hora de chegada no servidor era uma rede de segurança
feia mas real. Com sincronização só na base, ela sumiu: se o GPS nunca pegar
fix (partida em ambiente fechado, mata fechada, prédio), o arquivo inteiro
nasce em 1970 e **não há nada** para corrigir depois.

O `ms` resolve de graça. Gravando `millis()` em toda amostra, basta **um único
fix** em qualquer ponto da sessão para a base reancorar o arquivo inteiro por
regressão linear entre `ms` e `ts`. Sem nenhum fix, a sessão ainda tem ordem e
intervalos corretos — só falta o deslocamento absoluto, que pode ser recuperado
depois pela trilha GPX do celular (ver
[Segunda fonte de posição](#segunda-fonte-de-posição-o-celular)).

Um DS3231 (~R$ 10) é o cinto-e-suspensório, se um dia isso incomodar.

### Tópicos MQTT

> **Decisão em aberto: o MQTT pode não ser necessário.** Ele foi desenhado para
> streaming ao vivo de mensagens pequenas. Com sincronização em lote na base
> ("despejar 30 MB quando chegar em casa"), um `POST` HTTP de arquivo é mais
> simples, retomável e dispensa broker. O painel do celular também não precisa
> dele — o `firmware/painel_wifi/` já serve HTTP direto do ESP32.
>
> O que mantém o MQTT vivo por enquanto: ele **já está implementado dos dois
> lados** (`src/servidor_telemetria.py` e o firmware), e continua sendo o
> caminho certo caso um dia se queira ver dado ao vivo na base com o campo
> coberto por rede. Decidir antes de investir mais nessa camada. O `FiltroCV`,
> o painel e o banco não dependem dela.

Separados por **taxa**, não por sensor — isso é o que evita que um sensor
lento (ambiente) e um rápido (IMU) briguem no mesmo tópico:

```
estacao/esp32-01/pos      1 Hz
estacao/esp32-01/imu      lote de N amostras por mensagem, 1x/s
estacao/esp32-01/amb      a cada alguns segundos
estacao/esp32-01/status   retained + LWT (Last Will)
```

IMU vai em lote — um array de 10 amostras numa mensagem custa muito menos que
10 mensagens MQTT separadas. O **LWT** deixa o broker anunciar sozinho quando
o ESP32 cai, sem precisar de heartbeat manual.

QoS 1 para posição/ambiente (não pode faltar), QoS 0 para IMU (perder uma
amostra de IMU não muda o filtro).

---

### Cartão SD: custódia, não buffer

O ESP32 grava **sempre** no cartão, não só quando a rede cai. Essa é a mudança
de conceito: o cartão não é buffer de contingência, é a **custódia do dado
cru**. A publicação em rede é uma cópia, e cópia pode falhar.

O argumento mais forte não é nem a autonomia: se o Wi-Fi falhar, se o broker
cair, se o cartão da base queimar, se a sintonia do Kalman estiver errada — o
dado cru continua no cartão, intacto. Para um instrumento de pesquisa isso é a
diferença entre "perdi a coleta" e "perdi a visualização da coleta".

#### Quanto cabe

O CSV de telemetria já gravado em `data/` mede **~195 bytes por amostra** a
1 Hz. Em envelope JSON, com o IMU a 10 Hz dominando:

| fonte | taxa | volume |
|---|---|---|
| `pos` (GPS) | 1 Hz | ~200 B/s |
| `imu` (lote) | 10 Hz | ~800 B/s |
| `amb` | 0,1 Hz | ~15 B/s |
| **total** | | **~1 KB/s ≈ 3,6 MB/h** |

Uma jornada de 8 h ≈ **29 MB**. Contra isso:

| meio | capacidade | aguenta |
|---|---|---|
| RAM (ring buffer) | ~50–100 KB | **~1 min** |
| LittleFS (placa de 4 MB) | ~1–1,5 MB | **~25 min** |
| LittleFS (placa de 16 MB) | ~6–10 MB | ~2–3 h |
| **cartão SD de 8 GB** | 8 GB | **~9 meses** |

**A flash interna não serve.** 25 minutos é buffer de soluço de Wi-Fi, não de
jornada de campo. Com cartão SD (módulo SPI, ~R$ 10–15, pinos separados da
UART2 do RS485 — sem conflito), a pergunta "quanto tempo aguenta" deixa de
existir.

#### Três cuidados

1. **Grave compacto, não JSON.** No cartão, CSV ou struct binário — o JSON só
   existe para trafegar. Cai para ~1/5 do tamanho e a serialização fica mais
   barata na CPU.
2. **`flush()` a cada poucos segundos.** Perder energia com dado só no buffer
   da lib SD custa o rabo do arquivo.
3. **Nome do arquivo pelo tempo do GPS**, não por contador — nasce
   identificável e casa com o `ts` do envelope.

#### Sincronização com a base

Regras que evitam o problema clássico:

- **Manifesto.** A base guarda quais arquivos (ou faixas de `seq`) já ingeriu, e
  o ESP32 pergunta antes de mandar. Sem isso, uma transferência interrompida
  pela metade vira sessão duplicada no banco — e duplicata em série temporal é
  veneno.
- **Não apague o cartão depois de sincronizar.** A tentação é liberar espaço;
  8 GB dão meses. Marque como sincronizado e rotacione só quando encher de
  verdade. O cartão é a cópia independente.
- **`buf: true`** marca o que veio do cartão. Com o Kalman em lote, isso deixou
  de exigir tratamento especial — ver a seção seguinte.

---

### Por que o Kalman fica na base

O porte de `src/filtro_kalman.py` para C++ foi **cancelado** em 17/08/2026.

**O motivo não é CPU.** Matrizes 4×4 a 10 Hz é bem menos de 1% do ESP32 — o
chip rodaria isso sem suar. Quem justificar essa decisão por desempenho vai
tomar a decisão errada na próxima encruzilhada.

Os motivos reais são três:

1. **Caminho único.** O desenho anterior tinha dois: o ao vivo (sequencial,
   sensível a `dt`, quebra com amostra fora de ordem) e o de lote, com a regra
   de que `buf: true` dispara reprocessamento. Com tudo chegando em lote, o
   caminho ao vivo simplesmente não existe. Isso elimina uma categoria inteira
   de bug — jitter de rede contaminando o `dt`, amostra fora de ordem, estado
   do filtro corrompido por reconexão.
2. **Reversibilidade.** Filtro que roda ao vivo e descarta o cru é
   irreversível. Como o cru fica no cartão e no banco, a mesma coleta pode ser
   reprocessada com `Q`/`R` diferentes quantas vezes for preciso. É isso que
   faz um instrumento de pesquisa ser sério.
3. **Custo de porte.** ~1200 linhas validadas e testadas em Python, contra
   reescrever e reverificar em C++ sem ganho funcional.

**O custo aceito:** não há posição filtrada em campo. O painel do celular
mostra GPS cru, temperatura, umidade e aceleração bruta — sem `kf`, sem
contagem de passos, sem sigma. É escolha consciente: a função do painel de
campo é responder *"o GPS pegou fix? o sensor está vivo? estou gravando?"*, não
analisar.

Se um dia a operação exigir posição filtrada em campo sem base, o porte volta à
pauta — e aí valem os cuidados originais: `float` dentro do filtro (a FPU do
ESP32 é de precisão simples, `double` é emulado e caro), `dt` medido com
`esp_timer_get_time()`, e verificação passando os CSVs de `data/` pelos dois
filtros com comparação amostra a amostra. `tests/test_filtro_kalman.py` serve
de especificação.

---

### Segunda fonte de posição: o celular

O celular do pesquisador tem GPS próprio e vale como **verificação cruzada** e
**âncora de tempo** — não como fonte de verdade. O WTGAHRS3 com antena bem
posicionada tende a ser melhor que um celular no bolso; em compensação o
celular usa A-GPS assistido pela rede, pega fix muito mais rápido e tem hora
absoluta confiável.

**Não dá para pegar essa posição pelo painel do ESP32.** `navigator.geolocation`
exige contexto seguro (HTTPS) em Chrome, Firefox e Safari desde 2016, e o
`painel_wifi` serve `http://192.168.4.1`. A chamada não retorna. Contornos
descartados: certificado auto-assinado no ESP32 (aviso vermelho no navegador,
~40 KB de RAM por conexão de TLS, Safari/iOS ainda mais restritivo) e PWA (que
também exige HTTPS).

**Solução adotada:** o celular grava a própria trilha com qualquer app de GPS
logger que exporte **GPX**, e a base funde as duas séries por timestamp na
interpretação. Não exige nada do ESP32, nada de HTTPS, nada de app novo — e a
base já é a camada de fusão.

Ganho colateral: se a trilha do celular cobre a sessão, ela reancora no tempo
absoluto um arquivo do ESP32 que tenha nascido sem nenhum fix de GPS.

#### App nativo — adiado, com motivo

Um app nativo é melhor como estado final: além do GPS, ele registra em segundo
plano (uma página web é suspensa quando a tela bloqueia, então só funciona com
alguém olhando) e abre a porta para BLE.

Foi **adiado explicitamente** — é uma terceira base de código num projeto que
já tem duas, e entra numa fila onde o SD, a ingestão na base e a tela ainda não
existem. O motivo decisivo é outro: hoje não se sabe se o GPS do celular
concorda com o WTGAHRS3, se o alcance BLE serve, nem o que o pesquisador quer
ver em campo. Duas ou três saídas com um GPX logger genérico respondem as três
coisas de graça, e aí o app nasce sabendo em vez de adivinhando.

Quando for a hora, três decisões já tomadas: **Flutter** (uma base para Android
e iOS, curva suave vindo de Python), **Android primeiro** (iOS custa US$ 99/ano
e distribuição por TestFlight; APK resolve para instrumento de laboratório) e
**BLE, não Wi-Fi** — o app só precisa de telemetria ao vivo, que é pouquíssimo
dado, e BLE evita a pior dor de cabeça do AP sem internet, que é o Android e o
iOS tentando voltar para os dados móveis e roteando o tráfego para o lugar
errado. O despejo dos 30 MB vai para a base, não para o celular.

Isso realimenta o firmware: se o app entrar, o ESP32 ganha um serviço BLE e o
AP Wi-Fi passa a existir só para a sincronização na base.

---

### Broker MQTT (Mosquitto)

O broker é o servidor que fica no meio: recebe as publicações do ESP32 e
entrega para quem assinou. Não interpreta o conteúdo — só roteia.

#### Instalação

```bash
sudo apt install mosquitto mosquitto-clients
sudo cp servidor/mosquitto-estacao.conf /etc/mosquitto/conf.d/estacao.conf
sudo systemctl restart mosquitto
```

**O arquivo de config não é opcional.** O Mosquitto 2.0 mudou os padrões de
fábrica: sem configuração ele escuta apenas em `localhost` e recusa cliente
anônimo. Como o ESP32 vem de outra máquina da rede, sem isso ele nunca
conecta — e a falha aparece só como "Connection refused" no monitor serial,
sem nada útil no log. O `servidor/mosquitto-estacao.conf` resolve os dois
pontos (`listener 1883 0.0.0.0` e `allow_anonymous true`).

`allow_anonymous true` vale para rede local doméstica. Antes de expor o
broker num VPS ou abrir porta no roteador, trocar por autenticação com senha
e TLS na 8883.

> **Não repita chave que o config principal já define.** O
> `/etc/mosquitto/mosquitto.conf` do Ubuntu já traz `persistence`,
> `persistence_location` e `log_dest`. Redefinir qualquer uma delas no arquivo
> de `conf.d/` é **erro fatal** — o broker recusa subir com `Duplicate
> <chave> value in configuration`, e o `systemctl status` mostra só
> `exit-code 3`, sem a causa. A mensagem real aparece rodando o broker à mão:
>
> ```bash
> /usr/sbin/mosquitto -c /etc/mosquitto/mosquitto.conf
> ```
>
> Depois de algumas falhas seguidas o systemd também bloqueia o restart
> (`Start request repeated too quickly`); aí é `sudo systemctl reset-failed
> mosquitto` antes de tentar de novo.

#### Verificar que está no ar

```bash
mosquitto_sub -h localhost -t 'estacao/#' -v
```

Fica escutando tudo que qualquer estação publicar. É a primeira coisa a rodar
quando o dado não chega: se aparece aqui, o problema está no servidor Python;
se não aparece, está no ESP32 ou na rede.

---

### Base (servidor)

A máquina é um **Raspberry Pi 3B** — preparação, limites de hardware e
`systemd` em [05-base-raspberry-pi.md](05-base-raspberry-pi.md).

- **Ingestor** em Python (`src/servidor_telemetria.py`, com `paho-mqtt`),
  assina `estacao/+/+`, valida o envelope, roda o Kalman e grava.
- **Kalman aqui, não no ESP32** — motivos em
  [Por que o Kalman fica na base](#por-que-o-kalman-fica-na-base).
- **`dt` vem do ESP32, não do relógio da base.** O lote de IMU carrega o campo
  `dt` medido com `esp_timer`. A latência de rede varia de dezenas a centenas
  de ms e contaminaria a predição do filtro se a base medisse o intervalo na
  chegada. Vale igual para lote: o `dt` é uma propriedade da aquisição, não da
  entrega.
- **Reancoragem de tempo.** Antes de ingerir uma sessão, regredir `ms` contra
  os `ts` que tiverem fix válido e reescrever o `ts` das amostras sem fix. Ver
  [Contrato de dados](#contrato-de-dados).
- **Saída atual:** CSV (`data/telemetria-<data>.csv`) + painel web em
  `http://localhost:8771`, mostrando GPS cru e trajeto filtrado sobrepostos.
- **Ainda não feito:** banco em duas camadas (cru imutável + derivado
  recomputável) e o reprocessamento que ele habilita. SQLite com índice em
  `(dev, ts)` aguenta a taxa desse projeto; não trocar por Timescale/Influx
  antes de doer. O banco vai em pendrive/SSD USB, não no cartão SD do Pi.
- **Painel leve por projeto.** HTML + `<canvas>` puro, sem framework, sem tiles
  online — restrição do Pi 3B com 1 GB de RAM, detalhada no doc 05. Isso é
  requisito de projeto, não otimização posterior.

---

### Adicionar um sensor novo

O contrato foi desenhado para isso. Um sensor novo:

1. Ganha um `src` novo (ex.: `"src": "bme280"`) e entra no `m` com as chaves
   dele — não exige mudar o schema dos outros.
2. Ganha um tópico MQTT na taxa que fizer sentido (`estacao/esp32-01/press`,
   por exemplo) — não compartilha tópico com um sensor de taxa diferente.
3. Ganha uma leitura na task/estado de aquisição correspondente ao barramento
   dele (RS485 intercalado se for Modbus, direto se for I2C/SPI — não disputa
   UART com os sensores RS485 existentes).
4. Só entra no Kalman se for uma fonte de posição ou movimento; sensores
   puramente ambientais (pressão, TVOC) só passam por `m`, sem `kf`.

Nenhuma dessas mudanças toca no ingestor, no banco ou no painel — eles já
lidam com `src` e `m` genéricos.

---

### Roadmap

| Fase | Entrega | Pronto quando |
|---|---|---|
| 1 | Dois sensores Modbus no mesmo RS485 | leitura estável dos dois por 1 h seguida |
| 2 | Aquisição por máquina de estados (`millis()`), sem bloquear em rede | IMU a 10 Hz sem perder amostra mesmo com Wi-Fi instável |
| ~~3~~ | ~~Kalman em C++~~ — **cancelado**, o filtro roda na base | — |
| ~~4~~ | ~~Kalman no ESP32~~ — **cancelado** junto com a fase 3 | — |
| 5 | Base no Raspberry Pi: broker + ingestor + painel, com `systemd` | pilha sobe sozinha no boot e o simulador (`src/simular_estacao.py`) roda de outra máquina ponta a ponta |
| 6 | Cartão SD no ESP32 gravando **sempre**, com `ms` no envelope | jornada de campo inteira gravada, e sessão sem nenhum fix de GPS ainda reancorável |
| 7 | Sincronização em lote ESP32 → base, com manifesto | transferência interrompida na metade retomada sem duplicar amostra |
| 8 | Banco em duas camadas (cru + derivado) e reprocessamento | mesma coleta reprocessada com outro `Q`/`R` sem reingerir |
| 9 | Tela touch e quiosque no boot | base liga na tomada e mostra a última sessão sem ninguém digitar comando |
| 10 | BME280 (pressão) como primeiro sensor extra | segue o fluxo de [Adicionar um sensor novo](#adicionar-um-sensor-novo) sem tocar a base |

A fase 5 vem antes da 6 de propósito: com o simulador funcionando contra a
base, o ESP32 entra num alvo já validado, e qualquer falha depois disso é do
firmware — não da rede, nem do broker, nem do filtro.

Fora do roadmap, adiado com motivo registrado: **app nativo no celular** (ver
[App nativo](#app-nativo--adiado-com-motivo)) e **porte do Kalman para C++**.

---

### Riscos conhecidos

- **RS485 compartilhado** entre WTGAHRS3 e sensor ambiental ainda precisa
  estar validado em bancada antes da fase 1 — dois endereços Modbus, mesmo
  par A/B, sem colisão de timing.
- **Banda em campo:** com o schema acima (JSON + overhead de MQTT/TLS), `pos`
  a 1 Hz sai em ~22 MB/dia e `amb` a cada 10s é desprezível (~1,5 MB/dia). O
  IMU bruto em lote é o pesado: ~95 MB/dia sozinho, ~3,6 GB/mês no total — já
  aperta um plano de hotspot de celular. Mandando o IMU **agregado**
  (média/desvio/pico por eixo a cada segundo, em vez das 10 amostras cruas) o
  total cai para ~48 MB/dia, **~1,4 GB/mês**. Decisão de config no que vai no
  campo `m` do tópico `imu`, não de arquitetura — o cru continua existindo
  localmente, só não é tudo publicado.
- **Credenciais fora do Git** — Wi-Fi e usuário/senha do broker num
  `secrets.h` no `.gitignore`, nunca hardcoded no `.ino` versionado.
- **Pressão continua descoberta** até o BME280 entrar — nem o WTGAHRS3 nem o
  sensor ambiental medem (varredura documentada em `firmware/README.md`).
- **Sessão sem hora absoluta.** Sem RTC e sem publicação ao vivo, uma sessão em
  que o GPS nunca pegou fix nasce em 1970. Mitigado pelo campo `ms` e pela
  trilha GPX do celular; só vira problema se as duas coisas faltarem juntas.
- **Duplicata na ingestão.** Sincronização em lote sem manifesto é a forma mais
  fácil de sujar o banco. Tratar como requisito da fase 7, não como detalhe.
- **Pi 3B como quiosque.** Chromium com 1 GB de RAM num painel ao vivo é lento
  e usa swap no cartão SD. Risco de produto, não de dado — mitigado pelo painel
  leve, resolvido de vez por um Pi 4/5 quando for a hora. Ver
  [05-base-raspberry-pi.md](05-base-raspberry-pi.md).

---

### Histórico da decisão

Este documento foi escrito e revisado no mesmo dia, **17/08/2026**, e a versão
final contradiz a inicial em pontos importantes. Registro para que a
contradição não seja redescoberta como se fosse dúvida nova:

| Antes | Depois | Por quê |
|---|---|---|
| ESP32 lê, **funde** e publica | ESP32 lê, carimba e **guarda**; a base funde | caminho único de lote, reversibilidade do cru, custo do porte |
| Kalman portado para C++ (fases 3 e 4) | porte cancelado | idem — **não foi por CPU**, que é folgada |
| Servidor no notebook | base no Raspberry Pi 3B | o objetivo é um produto, não um script: sempre ligada, endereço fixo, histórico que acumula |
| SD como *buffer* de contingência | SD como **custódia**, grava sempre | cópia independente do cru vale mais que o buffer em si |
| Stream ao vivo + regra especial para `buf: true` | tudo em lote, sem caminho ao vivo | elimina jitter de rede no `dt` e amostra fora de ordem |
| MQTT como espinha dorsal | MQTT opcional, decisão em aberto | sincronização em lote pede HTTP, não broker |

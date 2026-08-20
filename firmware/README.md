# Firmware ESP32 — Estação Meteorológica Móvel

O **lado de campo** do projeto: o ESP32 lê o **WitMotion WTGAHRS3-485** e o
sensor ambiental direto pelo barramento RS485.

As duas áreas não são implementações rivais — cada uma faz uma parte (decidido
em 17/08/2026, ver [../docs/04-telemetria.md](../docs/04-telemetria.md)):

> O ESP32 é um **sensor honesto**: lê, carimba (`seq`, `ts`, `ms`), grava tudo
> no cartão SD e serve um painel de conferência com o dado **cru**. Ele não
> interpreta nada. Toda interpretação numérica — filtro de Kalman, banco
> histórico, análise — roda na **base**, um Raspberry Pi ligado na tomada
> (ver [../docs/05-base-raspberry-pi.md](../docs/05-base-raspberry-pi.md)).

Consequência prática para quem mexe aqui: **o porte do filtro de Kalman para
C++ foi cancelado.** Não por falta de CPU — matrizes 4×4 a 10 Hz é bem menos de
1% do chip —, mas porque processar em lote na base mantém a coleta
reprocessável e elimina o `dt` contaminado por rede. A prioridade do firmware,
nesta ordem: **não perder amostra**, **carimbar certo**, **gravar no SD**. Rede
vem depois.

O lado Python continua servindo de bancada de estudo e validação numérica no
PC, além de ser o código que roda na base.

---

## Hardware

| Item | Observação |
|---|---|
| ESP32 DevKit (WROOM-32) | qualquer variante com UART2 livre |
| WTGAHRS3-485 | GPS/BeiDou + IMU 6 eixos, Modbus RTU 9600 8N1, endereço 0x50 |
| Transceptor RS485 | **preferir 3,3 V** (MAX3485, SP3485, MAX13487) |

> **Atenção ao nível lógico.** Os módulos MAX485 vendidos em kit são de 5 V: o
> pino RO devolve 5 V e os GPIO do ESP32 são 3,3 V *não tolerantes*. Ou usa um
> transceptor 3,3 V, ou coloca um divisor resistivo / conversor de nível na
> linha RO → RX.

> **Alimentação do sensor.** O WTGAHRS3-485 não é alimentado pelo 3V3 do ESP32.
> Confirmar a faixa de tensão e o consumo no datasheet em
> [../docs/datasheets/](../docs/datasheets/) antes de ligar.

### Ligação

```
WTGAHRS3        Transceptor RS485        ESP32
   A  ─────────── A
   B  ─────────── B
                  RO ──────────────────── GPIO16 (RX2)
                  DI ──────────────────── GPIO17 (TX2)
                  DE ┐
                  RE ┴─────────────────── GPIO4   (-1 se o módulo for automático)
                  VCC/GND ─────────────── 3V3 / GND
   VCC/GND ─── fonte externa (ver datasheet) — GND comum com o ESP32
```

Os pinos são constantes no topo de
[`estacao_esp32/estacao_esp32.ino`](estacao_esp32/estacao_esp32.ino).

## Sketches

| Sketch | Para quê |
|---|---|
| [`teste_sensor_ambiental/`](teste_sensor_ambiental/) | **diagnóstico** do sensor ambiental RS485: varre baud e endereço, despeja registradores e mostra todas as escalas possíveis |
| [`varredura_registradores/`](varredura_registradores/) | varredura larga (`0x0000`–`0x01FF`, funções `0x03` e `0x04`) para caçar grandezas escondidas |
| [`painel_wifi/`](painel_wifi/) | **ESP32 vira ponto de acesso** e serve uma página com temperatura e umidade ao vivo |
| [`estacao_esp32/`](estacao_esp32/) | leitura do WTGAHRS3-485 (GPS + IMU) e do sensor ambiental (T/UR), no mesmo barramento RS485 |

### Painel Wi-Fi

O ESP32 conecta na sua rede (roteador ou hotspot do celular) e serve o painel
nela. Na primeira vez — ou se a rede salva não for encontrada — ele sobe um
ponto de acesso temporário só para configuração:

| | |
|---|---|
| rede | `EstacaoMeteorologica-Config` |
| senha | `estacao2026` |
| página | `http://192.168.4.1` |

Conecte nessa rede, escolha o Wi-Fi de verdade numa lista (varredura ao vivo)
ou digite o nome manualmente, informe a senha e salve. O ESP32 grava a
credencial em memória não volátil (`Preferences`, sobrevive a queda de
energia) e reinicia direto na rede escolhida. Se a conexão falhar — rede fora
do ar, senha errada —, ele volta sozinho para o modo de configuração, então
não fica inacessível.

Depois de configurado, o painel fica em `http://estacao.local` (mDNS) ou no IP
que aparece no monitor serial. Um botão "trocar rede Wi-Fi" no rodapé da
própria página apaga a credencial salva e reinicia no modo de configuração,
sem precisar regravar o firmware.

Um servidor DNS responde tudo para o próprio ESP32 **só durante a
configuração**, então na maioria dos celulares a página do portal abre sozinha
ao conectar (o aviso de "faça login na rede"). Até 8 aparelhos ao mesmo tempo
no portal.

A página do painel busca `/dados` (JSON) a cada 2 s e mostra temperatura,
umidade (mínimo/máximo da sessão, gráfico dos últimos 6 minutos) e posição GPS
(fix, lat/lon, altitude, velocidade, satélites, HDOP) com um rastro relativo
em metros desde o primeiro fix — não é um mapa de ruas, só um canvas com o
trajeto. Um mapa de verdade fica viável agora que o ESP32 tem internet via
roteador (o navegador busca os tiles direto, sem o ESP32 mediar), mas ainda
não está implementado — ver `docs/04-telemetria.md`.

O `loop()` nunca bloqueia: a leitura Modbus roda em intervalos fixos (ambiente
a cada 2 s, GPS a cada 1 s) e o servidor sempre entrega o último valor em
cache.

Ajuste rede e senha no topo de
[`painel_wifi.ino`](painel_wifi/painel_wifi.ino) — a senha precisa ter no mínimo
8 caracteres.

O sketch de diagnóstico compila tanto em **ESP32** (usa `Serial2`, pinos 16/17)
quanto em **Arduino Uno/Nano** (usa `SoftwareSerial` nos pinos 10/11) — ele
detecta a placa sozinho pelo `#if defined(ESP32)`.

### Dois sensores no mesmo barramento: o que quebra e por quê

Ao pôr o WTGAHRS3 (`0x50`) e o sensor ambiental (`0x01`) no mesmo par A/B, o
GPS continuou perfeito e **o ambiental parou de responder quase sempre** —
chegava uma leitura de vez em quando. Duas causas somadas, as duas no driver
`ambiente_rs485.cpp`:

**1. Silêncio entre quadros (t3.5).** O Modbus RTU delimita um quadro pela
pausa de 3,5 caracteres antes dele: a 9600 8N1 são 3,65 ms. Enquanto o
ambiental era o único no barramento e lido a cada 2 s, essa pausa acontecia
sozinha. Com o WTGAHRS3 sendo lido a 10 Hz, o pedido ao ambiental passou a
sair na cola de uma resposta do GPS — e sem a pausa ele descarta o quadro
calado. O WTGAHRS3 tolera a falta dela, este sensor não. Corrigido com
`delay(SILENCIO_QUADRO_MS)` antes de cada transação.

**2. Recepção sem sincronismo.** O `receberQuadro()` aceitava como primeiro
byte o que estivesse na linha. Uma resposta do WTGAHRS3 que chegasse atrasada
— depois do timeout da própria leitura dele — entrava como byte inicial da
resposta do ambiental, deslocava o quadro inteiro e derrubava o CRC. Corrigido
sincronizando no endereço: byte que não for `0x01` seguido da função `0x03` é
lixo de outra transação e é descartado.

> **Sintoma característico:** o `dt` do lote de IMU alternava entre 0,0985 s
> (lote de 10 amostras) e 0,1229 s (lote de 8), num ciclo de exatamente 2 s —
> o `INTERVALO_AMBIENTE_MS`. Cada tentativa falha de ler o termômetro gastava
> os 200 ms inteiros de timeout e engolia 2 amostras do IMU. Foi esse padrão
> que apontou o culpado.

O mesmo par de problemas existe em tese no driver do WTGAHRS3, que só não
sofre porque o ambiental é lido 20× menos. Se leituras do GPS começarem a
falhar depois de acelerar o ambiental, é aqui que se olha.

**3. Orçamento do barramento.** Resolvido o protocolo, sobra a disputa por
tempo. A 9600 baud cada transação custa aproximadamente:

| Leitura | Bytes | Tempo |
|---|---|---|
| IMU (12 registradores) | 37 | ~38 ms |
| GPS (2 transações) | 50 | ~52 ms |
| ambiente (+ silêncio t3.5) | 17 | ~23 ms |

O IMU a 10 Hz sozinho consome 380 ms de cada segundo — 85% do barramento. Com
um temporizador independente por sensor, eles competem, e o mais raro é sempre
o prejudicado.

`painel_wifi.ino` fatia o tempo em **slots de 100 ms, ciclo de 20 slots (2 s)**,
cada slot com dono definido: slot 4 é do ambiente, slots 9 e 19 são do GPS, os
17 restantes são do IMU. Nunca há duas transações Modbus disputando a UART.

Taxas resultantes: **IMU 8,5 Hz, GPS 1 Hz, ambiente 0,5 Hz**, com o lote de IMU
alternando entre 8 e 9 amostras por segundo.

> A queda de 10 Hz para 8,5 Hz no IMU é o preço da vaga garantida dos outros
> dois. Ainda dá ~4,7 amostras por passada (cadência típica de 1,8 Hz), que é o
> mínimo confortável para o `DetectorPassos`. Se um dia o IMU precisar de mais
> taxa, o caminho não é apertar o escalonador e sim subir o baud do barramento —
> o WTGAHRS3 aceita até 230400 no registrador `0x04`.

### Sensor ambiental RS485 (T / UR / pressão / TVOC / eCO2)

O anúncio não publica o mapa de registradores e o produto vem em 4 variantes
("Type 1–4") com conjuntos de sensores diferentes, então o sketch **descobre**
em vez de supor. Ele imprime cada registrador como bruto, ÷10, ÷100 e na
codificação de negativo por offset 10000, para você identificar visualmente qual
coluna é temperatura.

### Mapa confirmado na bancada (14/08/2026, unidade "Type 2")

Endereço Modbus **1**, **9600 8N1**, função **`0x03`** (holding registers):

| Registrador | Grandeza | Conversão |
|---|---|---|
| `0x0000` | temperatura | ÷10 → °C (int16 com sinal) |
| `0x0001` | umidade | ÷10 → %RH |
| `0x0002`–`0x000F` | existem, leem sempre 0 | — |
| `0x0010` em diante | não respondem | — |

A função `0x04` (input registers) não devolve nada neste sensor.

Primeira leitura real: **23,0 °C** e **55,7 %RH**, estáveis por ~70 s com deriva
fisicamente coerente (23,2 → 22,9 °C enquanto a umidade caía de 58,6 % para
56,0 %).

> A hipótese inicial vinha do protocolo da família "N em 1"
> ([modbus.cn](https://www.modbus.cn/25909.html)), que põe eCO2 em `0x0000`,
> TVOC em `0x0001` e temperatura ÷100 em `0x0005`. **O despejo bruto derrubou
> ela**: os dois únicos registradores com dado são temperatura e umidade, em
> décimos. Foi exatamente para isso que o despejo existia.

### Varredura larga: não há pressão neste sensor

O sketch [`varredura_registradores/`](varredura_registradores/) percorreu
`0x0000`–`0x01FF` registrador a registrador, nas funções `0x03` e `0x04`
(14/08/2026):

| Faixa | Resultado |
|---|---|
| `0x0000`–`0x000F` | existem; só `0x0000` (temperatura) e `0x0001` (umidade) têm dado |
| `0x0060`–`0x006F`, `0x00C0`–`0x00DF`, `0x0100`–`0x010F` | existem, com valores não-zero em alguns |
| todo o resto até `0x01FF` | mudo |
| função `0x04`, faixa inteira | mudo nos 512 endereços |

Os valores fora de `0x0000`/`0x0001` **não são medições**: os blocos `0x0160` e
`0x01C0` espelham `0x0060` e `0x00C0`, e nas duas leituras — separadas por ~28 s
dentro da mesma varredura — os sete valores vieram **byte a byte idênticos**
(`31, 17952, 1539, 19957, 20224, 50, 19789`). Grandeza física medida flutua;
configuração não. `0x0064 = 1` e `0x0065 = 3` casam com endereço Modbus 1 e o
índice de 9600 baud, o que reforça a leitura de que é o bloco de configuração.

> **Conclusão: esta unidade mede apenas temperatura e umidade.** Não há pressão,
> TVOC nem eCO2 em lugar nenhum do espaço de endereços. O anúncio lista as cinco
> grandezas para a família; o "Type 2" é a variante de dois sensores.
>
> **Consequência para o projeto:** a pressão atmosférica continua descoberta —
> o WTGAHRS3 também não mede (`0x45` lê 0 Pa). Segue valendo o **BME280** do
> TODO do README principal, que resolve pressão e ainda dá um segundo par
> temperatura/umidade para conferência cruzada.

## Como abrir

Arduino IDE → *Arquivo ▸ Abrir* → o `.ino` dentro da pasta do sketch desejado.
Cada pasta tem o mesmo nome do seu sketch, como a IDE exige; arquivos `.h`/`.cpp`
ao lado são compilados junto automaticamente.

- Placa: **ESP32 Dev Module** (pacote *esp32* da Espressif, em *Gerenciador de Placas*)
- Monitor serial: **115200 baud**
- Para o `painel_wifi`: **Partition Scheme → "Huge APP (3MB No OTA/1MB SPIFFS)"**

O Modbus RTU (função 0x03, CRC16) está implementado no próprio driver. O
`painel_wifi` precisa de uma biblioteca externa: **PubSubClient**, pelo
Gerenciador de Bibliotecas. O BLE já vem no core da Espressif.

### O esquema de partição não é opcional no `painel_wifi`

Medido com `arduino-cli` no core esp32 3.3.11:

| | flash | do limite padrão |
|---|---|---|
| sem BLE | 999.808 B | 76% |
| **com BLE** | **1.751.199 B** | **134% — não cabe** |

A pilha BLE custa ~730 KB. Com a partição padrão (1.310.720 B para a aplicação)
o upload **falha**; com `huge_app` (3.145.728 B) o sketch fica em 55%. A RAM não
é problema: 20%, com 261 KB livres, mesmo com Wi-Fi e BLE no ar juntos.

O custo de `huge_app` é ficar sem partição de OTA — que este projeto não usa.

Pela linha de comando:

```bash
arduino-cli compile --fqbn esp32:esp32:esp32:PartitionScheme=huge_app firmware/painel_wifi
```

## Estado atual

- [x] Driver Modbus RTU do WTGAHRS3 (posição, IMU, ambiente)
- [x] Sketch de leitura contínua imprimindo no monitor serial
- [x] Sketch de diagnóstico do sensor ambiental RS485
- [x] Mapa de registradores do sensor ambiental confirmado na bancada
- [x] Pressão atmosférica descartada nesse sensor (varredura `0x0000`–`0x01FF`)
- [x] Driver definitivo do sensor ambiental (`ambiente_rs485.h/.cpp`), separado
      do sketch de diagnóstico
- [x] WTGAHRS3 e sensor ambiental no mesmo barramento RS485 (endereços Modbus
      `0x50` e `0x01`, mesma UART e mesmo pino DE) — implementado em
      `estacao_esp32.ino` e `painel_wifi.ino`
- [x] Painel Wi-Fi: temperatura, umidade e posição GPS (com rastro relativo)
      ao vivo, **não validado em hardware ainda**
- [x] Portal de configuração Wi-Fi: ESP32 conecta na rede do usuário
      (roteador/hotspot) em vez de ficar preso como AP isolado; credencial
      salva em `Preferences`, com fallback automático para o portal se a
      conexão falhar — **não validado em hardware ainda**
- [ ] **BME280** para cobrir a pressão que falta
- [ ] Gravar histórico (cartão SD ou SPIFFS) — hoje o gráfico vive só no
      navegador e some ao recarregar a página
- [ ] Definir o que o ESP32 faz com os dados: cartão SD? Wi-Fi/MQTT? display?
- [ ] Portar o filtro de Kalman de [`../src/filtro_kalman.py`](../src/filtro_kalman.py)

Ambos os sketches foram verificados com `arduino-cli compile` para
`esp32:esp32:esp32` e, no caso do diagnóstico, também para `arduino:avr:uno`.
Nenhum foi testado contra o hardware ainda.

## Conhecimento herdado do lado Python

O mapa de registradores e as fórmulas de conversão vieram de
[`../docs/02-protocolo-modbus-wtgahrs3.md`](../docs/02-protocolo-modbus-wtgahrs3.md)
e de [`../src/wtgahrs3.py`](../src/wtgahrs3.py). O ponto mais importante: a
**fórmula de latitude/longitude do manual oficial está errada no hemisfério
sul/oeste**. A correção (usar `floor` na parte de graus) está replicada em
`ddmmParaGraus()` — não "simplificar" aquilo achando que é bug.

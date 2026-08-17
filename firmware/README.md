# Firmware ESP32 — Estação Meteorológica Móvel

Segunda área do projeto. Enquanto a raiz do repositório (`src/`, `docs/`,
`tests/`) contém a implementação em **Python rodando no PC** — sensor ligado por
adaptador RS485-USB, painel web, filtro de Kalman —, esta pasta contém a
implementação **embarcada no ESP32**, que lê o mesmo sensor
**WitMotion WTGAHRS3-485** direto pelo barramento RS485.

As duas áreas são independentes: o Python continua sendo a bancada de estudo e
validação, o ESP32 é a versão de campo.

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
| [`estacao_esp32/`](estacao_esp32/) | leitura do WTGAHRS3-485 (GPS + IMU) |

### Painel Wi-Fi

O ESP32 cria a própria rede — não precisa de roteador nem internet. Qualquer
celular que conectar e abrir o navegador vê os valores ao vivo.

| | |
|---|---|
| rede | `EstacaoMeteorologica` |
| senha | `estacao2026` |
| página | `http://192.168.4.1` |

Um servidor DNS responde tudo para o próprio ESP32, então na maioria dos
celulares a página abre sozinha ao conectar (o aviso de "faça login na rede").
Até 8 aparelhos ao mesmo tempo.

A página busca `/dados` (JSON) a cada 2 s e mostra os dois valores em destaque,
mínimo e máximo da sessão, um gráfico dos últimos 6 minutos e o estado da
conexão. O `loop()` nunca bloqueia no serial: a leitura Modbus roda a cada 2 s e
o servidor entrega sempre o último valor em cache.

Ajuste rede e senha no topo de
[`painel_wifi.ino`](painel_wifi/painel_wifi.ino) — a senha precisa ter no mínimo
8 caracteres.

O sketch de diagnóstico compila tanto em **ESP32** (usa `Serial2`, pinos 16/17)
quanto em **Arduino Uno/Nano** (usa `SoftwareSerial` nos pinos 10/11) — ele
detecta a placa sozinho pelo `#if defined(ESP32)`.

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

Nenhuma biblioteca externa é necessária — o Modbus RTU (função 0x03, CRC16) está
implementado no próprio driver.

## Estado atual

- [x] Driver Modbus RTU do WTGAHRS3 (posição, IMU, ambiente)
- [x] Sketch de leitura contínua imprimindo no monitor serial
- [x] Sketch de diagnóstico do sensor ambiental RS485
- [x] Mapa de registradores do sensor ambiental confirmado na bancada
- [x] Pressão atmosférica descartada nesse sensor (varredura `0x0000`–`0x01FF`)
- [x] Painel Wi-Fi: ESP32 como ponto de acesso servindo temperatura e umidade
- [ ] **BME280** para cobrir a pressão que falta
- [ ] Gravar histórico (cartão SD ou SPIFFS) — hoje o gráfico vive só no
      navegador e some ao recarregar a página
- [ ] Driver definitivo do sensor ambiental, separado do sketch de diagnóstico
- [ ] Compartilhar o barramento RS485 entre o WTGAHRS3 e o sensor ambiental
      (endereços Modbus diferentes na mesma linha A/B) ou usar UARTs separadas
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

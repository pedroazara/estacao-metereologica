# Firmware ESP32 — Estação Meteorológica Móvel

## Configuração vigente (01/10/2026)

O ESP32 fica responsável por **três sensores externos**: **GY-302/BH1750**
(luminosidade), **KY-028** (temperatura auxiliar) e **sensor ambiental RS485**
(temperatura e umidade). **GPS, movimento/IMU e som serão obtidos pelo telefone
da pessoa que realizar a medição.**

O **WTGAHRS3-485**, o **KY-038** e o **KY-013** saíram da montagem prevista.
Os testes, drivers e estudos desses módulos permanecem como histórico.

**Estado da implementação:** esta decisão atualiza a especificação. O
[`painel_wifi.ino`](painel_wifi/painel_wifi.ino) ainda contém a implementação
anterior, com os seis módulos, consultas ao WTGAHRS3 e leituras do KY-038 e do
KY-013. A retirada dessas rotinas, dos campos de telemetria e das telas
correspondentes ainda precisa ser feita. A coleta de movimento pelo telefone
é parte da arquitetura prevista e ainda precisa ser implementada no app;
ver o [estado do aplicativo](../app/README.md).

### Hardware e conexões previstos

| Item | Função / ligação ao ESP32 |
|---|---|
| ESP32 DevKit (WROOM-32) | controlador dos três sensores externos |
| GY-302 / BH1750 | 3V3 e GND; SDA → GPIO21; SCL → GPIO22; ADDR → GND (I²C `0x23`) |
| KY-028 | 3V3 e GND; AO → GPIO35; DO sem ligação |
| Sensor ambiental RS485 | A/B → conversor RS485/TTL; endereço Modbus `0x01`, 9600 baud, 8N1 |
| Conversor RS485 / TTL V2 automático | entrada TXD ← GPIO17; saída RXD → GPIO16; alimentação de 5 V e GND comum, conforme o [módulo documentado](../hardware/skylog/conversor-rs485.md) |

```text
GY-302         ESP32                 KY-028
 VCC ───────── 3V3 ────────────────── VCC
 GND ───────── GND ────────────────── GND
 SDA ───────── GPIO21         GPIO35 ─ AO
 SCL ───────── GPIO22                  DO (sem ligação)
 ADDR ──────── GND

Sensor ambiental       Conversor RS485/TTL automático       ESP32
 A ─────────────────── A
 B ─────────────────── B
                       RXD (saída) ──────────────────────── GPIO16 (RX2)
                       TXD (entrada) ────────────────────── GPIO17 (TX2)
                       +5V / GND ────────────────────────── 5 V / GND
 VCC ───────────────── fonte do sensor, conforme sua especificação
 GND ───────────────── terra comum
```

O conversor automático documentado não expõe DE/RE: a configuração esperada
é **`PINO_DE = -1`**. O firmware ainda traz **`PINO_DE = 4`**, próprio da
configuração anterior com controle manual, e precisa ser alinhado ao módulo
usado. A compatibilidade elétrica do exemplar físico do conversor continua
sujeita às verificações registradas na sua documentação.

Os sensores ficam fora da placa. Seus footprints na PCB representam **somente
os conectores**: GY-302 com 5 pinos, KY-028 com 4 pinos e sensor ambiental
com 4 pinos, com passo de 2,54 mm. O conector do KY-028 preserva DO mesmo
sem uso no firmware.

O divisor externo de **10 kΩ / 10 kΩ** pertencia à entrada do KY-038 e **não é
necessário na nova montagem**, pois esse módulo foi retirado. A configuração
vigente não especifica divisor externo para GY-302 ou KY-028, alimentados em
3,3 V. Isso não remove os resistores presentes nos próprios módulos: a
terminação e a polarização do conversor RS485 devem ser consideradas conforme
a [documentação do conversor](../hardware/skylog/conversor-rs485.md).

### Coleta e processamento

Permanece a separação entre coleta e processamento: o ESP32 fornece os dados
ambientais e o telefone fornece posição, movimento e som; a análise pode
rodar na base. A gravação no cartão SD do ESP32 pertence ao planejamento
histórico e não foi implementada; não é um requisito da coleta vigente,
cuja gravação fica a cargo do telefone.

O porte do filtro de Kalman para C++ foi cancelado na arquitetura anterior
([telemetria](../docs/04-telemetria.md)). A mudança da origem dos dados de
movimento para o telefone ainda exige adaptar e validar o fluxo de coleta e
processamento. O lado Python permanece como bancada de estudo e validação.

---

## Sketches

| Sketch | Para quê |
|---|---|
| [`teste_sensor_ambiental/`](teste_sensor_ambiental/) | **diagnóstico** do sensor ambiental RS485: varre baud e endereço, despeja registradores e mostra todas as escalas possíveis |
| [`varredura_registradores/`](varredura_registradores/) | varredura larga (`0x0000`–`0x01FF`, funções `0x03` e `0x04`) para caçar grandezas escondidas |
| [`painel_wifi/`](painel_wifi/) | firmware principal com portal Wi-Fi, painel e telemetria; ainda contém os seis módulos da configuração anterior, pendente de adaptação |
| [`estacao_esp32/`](estacao_esp32/) | **legado**: leitura do WTGAHRS3-485 (GPS + IMU) e do sensor ambiental (T/UR), no mesmo barramento RS485 |
| [`teste_sensores_baratos/`](teste_sensores_baratos/) | bancada histórica com KY-013, KY-028, KY-038 e GY-302; inclui módulos retirados da montagem vigente |
| [`teste_gy302/`](teste_gy302/) | nome de pasta preservado, mas o sketch atual controla motor com AccelStepper; o teste do sensor precisa ser restaurado. A aquisição BH1750 está em [`painel_wifi.ino`](painel_wifi/painel_wifi.ino) |
| [`teste_ky028/`](teste_ky028/) | teste individual e calibração da temperatura auxiliar mantida no projeto |
| [`teste_ky013/`](teste_ky013/), [`teste_ky038/`](teste_ky038/) | **legado**: testes individuais dos módulos retirados |

### Painel Wi-Fi — implementação existente

As funções abaixo descrevem o código atual, ainda anterior à retirada dos
módulos. O GPS mostrado pelo painel e as rotinas de GPS/IMU continuam ligados
ao WTGAHRS3; isso ainda não representa a coleta pelo telefone.

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

### Histórico: dois sensores no mesmo barramento

Esta seção registra a bancada e o escalonador da configuração com WTGAHRS3 e
sensor ambiental. Na montagem vigente, **somente o sensor ambiental permanece
no RS485**; o escalonador e as consultas de GPS/IMU abaixo ainda existem no
código e deverão ser removidos ou adaptados.

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

### Sensor ambiental RS485: temperatura e umidade confirmadas

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
> **Consequência para o projeto:** a pressão atmosférica continua descoberta.
> O **BME280** foi uma possibilidade de expansão estudada; não integra a
> configuração vigente de três sensores. O WTGAHRS3 retirado também não
> fornecia essa medição (`0x45` lia 0 Pa).

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

## Estado atual e adaptação pendente

- [x] Configuração definida: GY-302, KY-028 e ambiental RS485 no ESP32;
      GPS, movimento/IMU e som ficam a cargo do telefone.
- [x] Sketch de diagnóstico e mapa do sensor ambiental confirmados na bancada.
- [x] Driver do sensor ambiental (`ambiente_rs485.h/.cpp`), separado do
      diagnóstico; esta unidade fornece somente temperatura e umidade.
- [x] Aquisição de GY-302 e KY-028 em `painel_wifi`; teste individual do
      KY-028 disponível. A pasta `teste_gy302` contém atualmente código de motor.
- [x] Portal de configuração Wi-Fi com credenciais em `Preferences` e
      retorno ao portal quando a conexão falha.
- [ ] Adaptar `painel_wifi` para os três sensores mantidos: retirar consultas ao
      WTGAHRS3 e leituras de KY-038/KY-013, revisar escalonador, painel e
      telemetria, e validar o funcionamento sem os módulos retirados.
- [ ] Alinhar `PINO_DE` ao conversor automático (`-1`) e validar a ligação real.
- [ ] Implementar a coleta de movimento pelo telefone e integrar/validar os
      dados do telefone com as leituras ambientais do ESP32.
- [ ] Validar a nova montagem e o firmware adaptado em bancada.

A integração anterior com WTGAHRS3 e ambiental no mesmo barramento, o painel
com GPS e os testes dos módulos retirados permanecem como histórico. As
verificações anteriores de compilação com `arduino-cli` não validam a nova
configuração. Esta atualização é apenas documental e não altera os sketches.

## Histórico: conhecimento herdado do lado Python

As notas abaixo se aplicam ao driver legado do WTGAHRS3, preservado para
consulta; não definem o formato das posições fornecidas pelo telefone.

O mapa de registradores e as fórmulas de conversão vieram de
[`../docs/02-protocolo-modbus-wtgahrs3.md`](../docs/02-protocolo-modbus-wtgahrs3.md)
e de [`../src/wtgahrs3.py`](../src/wtgahrs3.py). O ponto mais importante: a
**fórmula de latitude/longitude do manual oficial está errada no hemisfério
sul/oeste**. A correção (usar `floor` na parte de graus) está replicada em
`ddmmParaGraus()` — não "simplificar" aquilo achando que é bug.

# Protocolo Modbus do WTGAHRS3-485 — referência offline (legado)

> **Referência histórica — arquitetura revisada em 01/10/2026.** O WTGAHRS3
> foi retirado do conjunto de sensores previsto para a estação. Seu protocolo
> permanece documentado para interpretar dados e manter ferramentas antigas;
> não é o contrato dos sensores do celular nem do sensor ambiental RS485.
> O ESP32 passa a atender apenas GY-302, KY-028 e o ambiental RS485. GPS,
> movimento e som ficam no celular; a captura de movimento ainda precisa ser
> implementada. Ver [arquitetura atual](04-telemetria.md).

Transcrito da documentação oficial da WitMotion
([WIT Standard Modbus Protocol](https://wit-motion.gitbook.io/witmotion-sdk/wit-standard-modbus-protocol/modbus-communication-protocol)).
Serve como referência de trabalho enquanto os PDFs do Drive não estão à mão.

## Parâmetros de comunicação

| Item | Valor de fábrica |
|---|---|
| Protocolo | Modbus RTU |
| Endereço do dispositivo | `0x50` (80 decimal), alterável no registrador `0x1A` |
| Baud rate | **9600** (registrador `0x04`, valor padrão `0x0002`) |
| Formato | 8 bits de dados, sem paridade, 1 stop bit (8N1) |
| Função de leitura | `0x03` — read holding registers |
| Função de escrita | `0x06` — write single register |

### Formato do quadro

Leitura:

```
Envia:  ID  0x03  ADDR_H  ADDR_L  LEN_H  LEN_L  CRC_H  CRC_L
Recebe: ID  0x03  LEN     D1_H    D1_L   ...    CRC_H  CRC_L
```

Escrita:

```
Envia:  ID  0x06  ADDR_H  ADDR_L  DATA_H  DATA_L  CRC_H  CRC_L
Recebe: (eco do mesmo quadro)
```

Tudo em hexadecimal binário, **não** ASCII. CRC é o CRC-16 Modbus padrão.

> **Travamento automático:** o sensor volta a travar sozinho ~10 s depois do
> último comando. Para escrever configuração: desbloquear (`KEY` ← `0xB588`) →
> escrever → salvar (`SAVE` ← `0x0000`).

---

## Registradores de dados (somente leitura)

### Data e hora — `0x30`–`0x33`

| Addr | Nome | Conteúdo |
|---|---|---|
| `0x30` | YYMM | byte baixo = ano (+2000), byte alto = mês |
| `0x31` | DDHH | byte baixo = dia, byte alto = hora |
| `0x32` | MMSS | byte baixo = minuto, byte alto = segundo |
| `0x33` | MS | milissegundos (16 bits) |

### Bloco inercial — `0x34`–`0x40`

| Addr | Nome | Conversão |
|---|---|---|
| `0x34`–`0x36` | AX, AY, AZ | `valor / 32768 × 16` → g |
| `0x37`–`0x39` | GX, GY, GZ | `valor / 32768 × 2000` → °/s |
| `0x3A`–`0x3C` | HX, HY, HZ | campo magnético — ⚠️ **não implementado neste modelo**, ver abaixo |
| `0x3D` | Roll | `valor / 32768 × 180` → ° |
| `0x3E` | Pitch | `valor / 32768 × 180` → ° |
| `0x3F` | Yaw | `valor / 32768 × 180` → ° |
| `0x40` | TEMP | `valor / 100` → °C (temperatura **do chip**) |

Todos são int16 com sinal. Os fundos de escala 16 g / 2000 °/s são os padrões
de fábrica; se você mudar `ACCRANGE`/`GYRORANGE`, ajuste o divisor.

#### Os registradores de campo magnético não têm sensor atrás

`0x3A`–`0x3C` respondem à leitura, mas o que devolvem não tem cara de medida
física. Com o sensor **parado na mesa, sem estímulo nenhum**:

- `HY` fica **congelado** num valor exato por centenas de amostras seguidas;
- `HZ` fica congelado e então **salta a escala inteira** (de `252` a `31960`,
  ou de `+32731` a `−32705`) de uma leitura para a outra, sozinho;
- `HX` oscila numa faixa de ~100 LSB, ordens de grandeza abaixo do que o campo
  terrestre produziria.

Registrador que fica congelado e dá saltos de escala cheia sem estímulo não é
saída de sensor. Para fechar a questão de forma direta, rode
`src/verificar_bussola.py` e passe um ímã pela carcaça no teste 3: só um
magnetômetro reage a campo magnético — acelerômetro, giroscópio e GNSS não.

O que confunde: o `High-precision sensor Modbus protocol.pdf` lista `HX/HY/HZ`,
os offsets `0x0B`–`0x0D` e os modos de calibração magnética do `CALSW`. Mas
aquele PDF é o **mapa genérico da família WitMotion**, não deste produto — ele
mesmo traz a ressalva *"The callout register is only applicable to HWTX073"*, e
nem sequer descreve este modelo (nele os ângulos são de 32 bits em `0x3D`–`0x42`
e a temperatura fica em `0x43`; aqui os ângulos são de 16 bits e a temperatura
está em `0x40`). Endereço existir na tabela não significa silício existir na
placa.

### Pressão e altitude barométrica — `0x45`–`0x48`

| Addr | Nome | Conversão |
|---|---|---|
| `0x45` / `0x46` | PressureL / PressureH | `(H << 16) \| L` → **Pa** |
| `0x47` / `0x48` | HeightL / HeightH | `(H << 16) \| L` → **cm** (divida por 100 para metros) |

### Posição GNSS — `0x49`–`0x50`

| Addr | Nome | Conversão |
|---|---|---|
| `0x49` / `0x4A` | LonL / LonH | int32 = `(LonH << 16) \| LonL` |
| `0x4B` / `0x4C` | LatL / LatH | int32 = `(LatH << 16) \| LatL` |
| `0x4D` | GPSHeight | `valor / 10` → m (altitude GNSS) |
| `0x4E` | GPSYAW | `valor / 100` → ° (rumo sobre o solo) |
| `0x4F` / `0x50` | GPSVL / GPSVH | int32 `/ 1000` → km/h |

#### Conversão de latitude/longitude — o manual está errado para o Brasil

O sensor **não** entrega graus decimais. Entrega o formato NMEA `ddmm.mmmmm`
empacotado num int32. O manual oficial manda decodificar assim:

```
dd       = Lon[31:0] / 10000000
mm.mmmmm = (Lon[31:0] % 10000000) / 100000
```

**Essa fórmula só funciona no hemisfério norte/leste.** No Brasil ela erra a
posição em mais de 100 km.

##### O bug

O firmware monta o inteiro aplicando o sinal do hemisfério **apenas na parte
dos graus**:

```
valor = graus_COM_SINAL × 10⁷  +  minutos_SEM_SINAL × 10⁵
```

Com latitude e longitude negativas, os minutos entram **somados** em vez de
subtraídos. A fórmula do manual então extrai um campo de "minutos" acima de 60,
que é geometricamente impossível — minuto vai de 0 a 59,999 por definição.
Esse valor impossível é a assinatura do problema.

##### Medição real (Lavras/MG, 03/08/2026)

| | Latitude | Longitude |
|---|---|---|
| Posição real (GPS do celular) | -21,225671 | -44,977374 |
| Registrador cru lido | `-208647109` | `-434134045` |
| Fórmula do manual | -21,4412 | -43,6890 |
| **Erro da fórmula do manual** | | **136 km** |
| Fórmula corrigida | -21,2255 | -44,9777 |
| **Erro da fórmula corrigida** | | **36 m** |

Os 36 m residuais são a diferença esperada entre o receptor do celular e a
antena do sensor — ou seja, dentro do ruído.

##### Fórmula correta

Use `floor()` em vez de truncar o módulo. Para valores negativos,
`floor(-20,86) = -21`, que é a parte de graus verdadeira:

```python
import math

def ddmm_para_graus(bruto: int) -> float:
    dd = math.floor(bruto / 10_000_000)          # graus, com sinal
    mm = (bruto - dd * 10_000_000) / 100_000.0   # minutos, sempre positivos
    sinal = -1 if dd < 0 else 1
    return sinal * (abs(dd) + mm / 60.0)
```

Validado nos quatro quadrantes: Brasil (S/O), Shenzhen (N/L), Nova York (N/O)
e Sydney (S/L).

**Limitação:** coordenadas com `|graus| < 1` (perto do equador ou do meridiano
de Greenwich) são ambíguas, porque o sinal se perde quando a parte inteira dos
graus é zero. Não afeta o Brasil.

> Por que a WitMotion nunca notou: Shenzhen fica a 22°N, 114°L. Os dois sinais
> são positivos e a fórmula documentada funciona perfeitamente lá.

### Quaternions — `0x51`–`0x54`

`q0`–`q3`, cada um `valor / 32768`.

### Qualidade do fix — `0x55`–`0x58`

| Addr | Nome | Conversão |
|---|---|---|
| `0x55` | SVNUM | número de satélites em uso (valor direto) |
| `0x56` | PDOP | `valor / 100` — diluição de precisão (posição) |
| `0x57` | HDOP | `valor / 100` — horizontal |
| `0x58` | VDOP | `valor / 100` — vertical |

Regra prática: **HDOP < 2** e **≥ 4 satélites** significa fix confiável.
HDOP > 5 indica posição ruim; ignore ou marque a amostra.

---

## Registradores de configuração (leitura/escrita)

| Addr | Nome | Função |
|---|---|---|
| `0x00` | SAVE | `0x0000` salva; `0x0001` restaura padrão de fábrica |
| `0x01` | CALSW | modo de calibração (ver tabela abaixo) |
| `0x04` | BAUD | baud rate da serial |
| `0x05`–`0x0D` | offsets | zero bias de acelerômetro, giroscópio e magnetômetro |
| `0x1A` | IICADDR | endereço Modbus do dispositivo |
| `0x1B` | LEDOFF | desliga o LED |
| `0x1F` | BANDWIDTH | largura de banda do filtro |
| `0x20` | GYRORANGE | fundo de escala do giroscópio |
| `0x21` | ACCRANGE | fundo de escala do acelerômetro |
| `0x23` | ORIENT | direção de instalação (horizontal/vertical) |
| `0x24` | AXIS6 | algoritmo 6 eixos vs 9 eixos |
| `0x26` | GPSBAUD | baud rate do módulo GNSS interno |
| `0x69` | KEY | desbloqueio — escreva `0xB588` |
| `0x6B` | TIMEZONE | fuso do GPS: `0x0000` = UTC−12, então grave `offset + 12` |
| `0x74` | MODDELAY | atraso de resposta do 485 |

### Valores de BAUD (`0x04`)

| Valor | Baud |
|---|---|
| `0x01` | 4800 |
| **`0x02`** | **9600 (padrão)** |
| `0x03` | 19200 |
| `0x04` | 38400 |
| `0x05` | 57600 |
| `0x06` | 115200 |
| `0x07` | 230400 |

> Depois de mudar o baud, o sensor **para de responder no baud antigo**
> imediatamente. Se errar, use `scan_barramento.py` para reencontrá-lo.

### Valores de CALSW (`0x01`)

| Valor | Modo |
|---|---|
| `0x00` | operação normal |
| `0x01` | calibração automática do acelerômetro |
| `0x03` | zerar altitude barométrica |
| `0x04` | zerar ângulo de rumo (yaw) |
| `0x07` | calibração magnética — ajuste esférico |
| `0x08` | definir referência angular |
| `0x09` | calibração magnética — modo bi-planar |

---

## Exemplos de quadro (do manual oficial)

```
Ler temperatura (0x40, 1 registrador):
  Envia:  50 03 00 40 00 01 88 5F

Ler bloco GPS (0x4D, 4 registradores):
  Envia:  50 03 00 4D 00 04 D9 9F

Ler qualidade do fix (0x55, 4 registradores):
  Envia:  50 03 00 55 00 04 59 98

Zerar o ângulo de rumo:
  Envia:  50 06 00 01 00 04 D4 48
  Recebe: 50 06 00 01 00 04 D4 48

Mudar baud para 115200:
  Envia:  50 06 00 04 00 06 ...
  Recebe: 50 06 00 04 00 06 45 88
```

Útil para testar com `cutecom`, `minicom` ou `python -m serial.tools.miniterm`
sem depender do driver.

---

## Fontes

- [WIT Standard Modbus Protocol — WitMotion SDK](https://wit-motion.gitbook.io/witmotion-sdk/wit-standard-modbus-protocol/modbus-communication-protocol)
- [WT901C RS485 User Manual (mesma família, v23-0213)](https://cdn.robotshop.com/rbm/f83835f4-5e29-4ee0-9cc2-e49300031503/5/53734f08-7d97-497f-87d3-5f2e8c31c91c/a0fe5b4c_wt901c-rs485-manual.pdf)
- [Página oficial do WTGAHRS3 com links dos PDFs](https://witmotion-sensor.com/products/industry-grade-beidou-wtgahrs3-6-axis-gps-imu-accelerometer)

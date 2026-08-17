# Guia de conexão — WTGAHRS3-485 no Linux

Do zero até ler latitude/longitude no terminal. Siga na ordem; cada passo tem
uma verificação antes de avançar.

---

## Passo 0 — Baixe a documentação oficial

Os PDFs ficam no Google Drive da WitMotion e exigem download manual pelo navegador.
Baixe estes dois e salve em `docs/datasheets/`:

| Documento | Link |
|---|---|
| **WTGAHRS3-485 Manual** | https://drive.google.com/file/d/1kt713OMcwgPzatyHhcEy9E0DeirDhO76/view |
| **WTGAHRS3-485 Datasheet** | https://drive.google.com/file/d/1pYLruCa41Ho_GSfHDJJuFSlDhxFmUONi/view |
| WTGAHRS3 — antena GNSS | https://drive.google.com/file/d/1sUN8csbQkVX8HsKZwE982D_KgYZqFkjU/view |
| GPS Series Quick Guide | https://drive.google.com/file/d/1s8CCTepwqA-kE_PMbG_l0R5LVH4NGCuS/view |
| Vídeo demo do WTGAHRS3 | https://drive.google.com/file/d/13BDvnm1_wMUnkxbK-h-eIvWDKyQb0pG3/view |

O protocolo Modbus completo (o que realmente importa para o código) já está
transcrito em [`02-protocolo-modbus-wtgahrs3.md`](02-protocolo-modbus-wtgahrs3.md),
então você não fica bloqueado esperando o download.

> **Confira no datasheet antes de ligar a energia:** a tensão de alimentação.
> O WTGAHRS3 é especificado para **5 V** (~40 mA típico). A família RS485 da
> WitMotion às vezes aceita 5–36 V, mas isso varia por modelo — **não chute**.
> Alimentar acima do especificado queima a placa de forma permanente.

---

## Passo 1 — Identifique os 4 fios do sensor

O WTGAHRS3-485 tem quatro condutores:

| Sinal | Função | Vai para |
|---|---|---|
| **VCC** | alimentação | +5 V do adaptador (ou fonte externa) |
| **GND** | terra | GND do adaptador — **obrigatório**, mesmo em RS485 |
| **A** (D+ / 485-A) | par diferencial + | terminal **A** do adaptador |
| **B** (D− / 485-B) | par diferencial − | terminal **B** do adaptador |

**Não confie na cor do fio.** As cores mudam entre lotes e revendedores. Use a
etiqueta impressa no cabo ou a tabela de pinagem do datasheet que você baixou no
Passo 0.

Dois avisos que valem a placa:

- **Nunca** ligue VCC direto no GND.
- Se A e B estiverem trocados, **não queima nada** — o sensor simplesmente não
  responde. Trocar os dois fios é o primeiro teste quando nada funciona.

---

## Passo 2 — Plugue o adaptador e confirme que o Linux o enxergou

Com o adaptador RS485-USB **desconectado**, abra um terminal e rode:

```bash
dmesg -w
```

Agora plugue o adaptador. Devem aparecer linhas como:

```
usb 1-2: new full-speed USB device number 5 using xhci_hcd
usb 1-2: ch341-uart converter now attached to ttyUSB0
```

Saia com `Ctrl+C`. O nome no fim (`ttyUSB0`) é a sua porta: `/dev/ttyUSB0`.

Confirme:

```bash
ls -l /dev/ttyUSB*
```

Saída esperada:

```
crw-rw---- 1 root dialout 188, 0 ago  3 10:12 /dev/ttyUSB0
```

### Se nada aparecer

Descubra o chip do adaptador:

```bash
lsusb
```

- `QinHeng Electronics CH340` → driver `ch341` (no kernel desde sempre)
- `Silicon Labs CP210x` → driver `cp210x` (idem)
- `FTDI FT232`/`FT485` → driver `ftdi_sio` (idem)

**Em Linux você não precisa instalar driver.** Todos os três já vêm no kernel.
Se o dispositivo aparece no `lsusb` mas não vira `/dev/ttyUSB*`, o culpado
costuma ser o `brltty` (serviço de braille) que sequestra chips CH340:

```bash
sudo systemctl stop brltty
sudo systemctl disable brltty
# desplugue e replugue o adaptador
```

---

## Passo 3 — Dê permissão de acesso à porta serial

Por padrão sua conta não pode abrir `/dev/ttyUSB0`. Adicione-se ao grupo `dialout`:

```bash
sudo usermod -aG dialout $USER
```

**Faça logout e login de novo** (ou reinicie) — grupo novo só vale em sessão nova.

Verifique:

```bash
groups | grep dialout
```

> Em Arch/Manjaro o grupo pode se chamar `uucp` em vez de `dialout`. Cheque a
> saída do `ls -l /dev/ttyUSB0` do Passo 2 para ver qual grupo é o dono.
>
> Evite `sudo chmod 666 /dev/ttyUSB0` — funciona, mas volta ao normal a cada
> replug. O grupo é permanente.

---

## Passo 4 — Faça a fiação

Adaptador desligado do USB. Ligue:

```
Sensor            Adaptador RS485-USB
------            -------------------
VCC   ---------→  5V   (ou fonte externa de 5 V)
GND   ---------→  GND
A     ---------→  A  (D+, às vezes marcado "485+")
B     ---------→  B  (D−, às vezes marcado "485−")
```

Notas práticas:

- Se o adaptador não fornece 5 V, use fonte externa — mas **una os GNDs**.
- Se o sensor não responder no Passo 6, **troque A com B** antes de qualquer
  outra coisa. É o erro mais comum e é inofensivo.
- Resistor de terminação de 120 Ω só importa em cabo longo (>10 m) ou vários
  dispositivos no barramento. Na bancada, ignore.
- A **antena GNSS** precisa estar conectada e com vista para o céu. Sem antena
  você lê o IMU normalmente, mas latitude/longitude ficam zeradas para sempre.

Agora plugue o USB.

---

## Passo 5 — Prepare o ambiente Python

```bash
cd ~/Documentos/projetos/estacao-metereologica
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Isso instala `pyserial` e `minimalmodbus`.

### Atalho: o comando `estacao-metereologica`

Para não precisar lembrar de caminho, venv nem porta, instale o lançador uma
única vez:

```bash
ln -sf ~/Documentos/projetos/estacao-metereologica/bin/estacao-metereologica ~/.local/bin/
```

A partir daí, de qualquer diretório:

```bash
estacao-metereologica
```

Ele descobre sozinho em qual porta e em qual baud rate o sensor está, abre o
painel no navegador e grava o trajeto em `data/trajeto.csv`. Sem hardware por
perto, use `estacao-metereologica --simular`. Qualquer opção do `painel.py`
(`--porta`, `--hz`, `--csv`, …) é repassada adiante.

Se nada responder, ele diz o motivo provável — sem porta USB, fora do grupo
`dialout`, ou porta presente mas muda (aí é fiação: A/B trocados é o erro mais
comum).

---

## Passo 6 — Encontre o sensor

Este script varre todas as portas × todos os baud rates × endereços Modbus
`0x50`–`0x5F` até algo responder:

```bash
cd src
python3 scan_barramento.py
```

Saída esperada:

```
SENSOR ENCONTRADO
  Porta:    /dev/ttyUSB0
  Baudrate: 9600
  Endereco: 0x50
```

Padrões de fábrica: **9600 8N1, endereço Modbus 0x50**.

### Se não encontrar

Em ordem de probabilidade:

1. **A e B trocados** → inverta os dois fios.
2. **GND não ligado** → RS485 é diferencial, mas ainda precisa de referência comum.
3. **Sem alimentação** → o LED do sensor está aceso?
4. **Permissão** → você fez logout/login depois do `usermod`? (Passo 3)
5. **Porta ocupada** → `sudo lsof /dev/ttyUSB0` mostra quem está segurando.
6. **Baud alterado de fábrica** → o script já testa todos os baud rates comuns.

---

## Passo 7 — Leia posição

```bash
python3 ler_posicao.py --porta /dev/ttyUSB0
```

Saída:

```
[     1] SEM FIX - sat  0  (bruto lat=0 lon=0)
[     2] SEM FIX - sat  3  (bruto lat=0 lon=0)
...
[    47] lat  -23.550520  lon  -46.633308  alt   760.3 m  vel   0.00 km/h  sat 11  hdop 0.92
```

**Sobre o primeiro fix:** partindo do zero (cold start), o receptor precisa
baixar o almanaque dos satélites. Isso leva de **1 a 15 minutos** e exige céu
aberto — dentro de casa raramente funciona. Deixe a antena na janela ou saia
com o notebook. Depois do primeiro fix, os seguintes levam segundos.

Os dados vão para `data/posicao.csv`.

### Opções

```bash
# 5 leituras por segundo
python3 ler_posicao.py --porta /dev/ttyUSB0 --hz 5

# grava também IMU, pressão e temperatura (útil para a estação completa)
python3 ler_posicao.py --porta /dev/ttyUSB0 --completo --csv ../data/trajeto01.csv

# só mostra na tela, sem gravar
python3 ler_posicao.py --porta /dev/ttyUSB0 --sem-csv
```

---

## Passo 8 (opcional) — Ajuste o fuso horário

O sensor guarda a hora do GPS. Para Brasília (UTC−3):

```python
from wtgahrs3 import WTGAHRS3
s = WTGAHRS3("/dev/ttyUSB0")
s.definir_fuso_horario(-3)   # já faz desbloqueio + save
print(s.ler_data_hora())
```

---

## Passo 9 (opcional) — Software oficial da WitMotion

O PC software da WitMotion (`Minimu.exe`) **só roda em Windows**. Ele é útil para
calibração do acelerômetro/magnetômetro com feedback visual. Se quiser usar:

- Download: https://witmotion-sensor.com/pages/software-downloads-and-general-information
- No Linux, roda razoavelmente bem no Wine, mas você precisa mapear a porta:
  `ln -s /dev/ttyUSB0 ~/.wine/dosdevices/com1`
- Alternativa: máquina virtual com passthrough USB.

Para o projeto da estação você não depende dele — a calibração também pode ser
feita por Modbus escrevendo no registrador `CALSW` (0x01), documentado em
[`02-protocolo-modbus-wtgahrs3.md`](02-protocolo-modbus-wtgahrs3.md).

---

## Diagnóstico rápido

| Sintoma | Causa provável |
|---|---|
| `/dev/ttyUSB*` não existe | driver não carregou, ou `brltty` sequestrou o CH340 |
| `Permission denied` | falta grupo `dialout`, ou não fez logout/login |
| `No communication with the instrument` | A/B trocados, GND solto, baud errado ou endereço errado |
| CRC error intermitente | cabo longo sem terminação 120 Ω, ou baud alto demais |
| Responde, mas lat/lon = 0 | sem fix GPS — antena desconectada ou sem céu aberto |
| `Device or resource busy` | outro processo com a porta aberta (`sudo lsof /dev/ttyUSB0`) |
| Valores absurdos de ângulo | falta calibrar acelerômetro/magnetômetro |

---

## Próximos passos para a estação meteorológica

O WTGAHRS3 cobre **posição e movimento** muito bem. Para as grandezas
meteorológicas, o resultado dos testes de bancada foi:

- **Pressão: não disponível.** O registrador `0x45` existe no protocolo Modbus
  (que é compartilhado por toda a linha WitMotion), mas neste modelo retorna
  **0 Pa** — o WTGAHRS3 não tem barômetro, apesar do que dizem alguns anúncios.
  O manual oficial lista como módulos com barômetro apenas WT901B, HWT901B,
  WTGAHRS1 e WTGAHRS2.
- **Temperatura: não confiável para o ar.** O `0x40` é a temperatura interna
  do chip e lê alto por autoaquecimento.
- **Umidade: não medida.**

Ou seja, as três grandezas do projeto precisam de um sensor dedicado. O
**BME280** (I²C) entrega temperatura, umidade e pressão num módulo só — é o
caminho mais curto para fechar o escopo. Alternativas: SHT31 (mais preciso em
umidade) + BMP280 separados.

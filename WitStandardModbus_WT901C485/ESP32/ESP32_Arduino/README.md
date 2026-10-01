# WT901C485 com ESP32 na Arduino IDE

Esta pasta contem uma versao autocontida do exemplo Modbus para a Arduino IDE.
O projeto ESP-IDF original permanece inalterado em `../ESP32_sdk`.

## Arquivos necessarios

Os quatro arquivos abaixo precisam permanecer juntos na pasta `ESP32_Arduino`:

- `ESP32_Arduino.ino`
- `wit_c_sdk.c`
- `wit_c_sdk.h`
- `REG.h`

## Preparacao da Arduino IDE

1. Instale a Arduino IDE 2.x.
2. No Gerenciador de Placas, instale **esp32 by Espressif Systems**.
3. Abra `ESP32_Arduino.ino`.
4. Em **Ferramentas > Placa**, escolha a sua placa. Para um ESP32 comum, use
   **ESP32 Dev Module**.
5. Selecione a porta serial do ESP32.

O sketch usa o Monitor Serial em **115200 baud**.

## Ligacoes

Para um conversor RS485 com controle automatico de direcao:

| ESP32 | Conversor RS485 |
|---|---|
| GPIO 4 (TX1) | DI |
| GPIO 5 (RX1) | RO |
| GND | GND |

Ligue tambem `A` do conversor em `A` do WT901C485, `B` em `B`, e mantenha o
GND da fonte do sensor em comum com o GND do ESP32/conversor.

O ESP32 nao tolera 5 V nos GPIOs. Use um transceptor RS485 de 3,3 V, como o
MAX3485, ou um modulo que faca a adequacao de nivel logico.

Se o seu modulo possuir `DE` e `/RE`, una os dois, conecte-os a um GPIO e
altere `RS485_DIRECTION_PIN` no sketch de `-1` para esse numero de GPIO.

## Uso

Depois do upload, abra o Monitor Serial. O programa procura automaticamente o
sensor nos baud rates suportados e passa a mostrar aceleracao, giroscopio,
angulos, campo magnetico e temperatura a cada 500 ms.

Digite `h` no Monitor Serial para ver os comandos de calibracao e configuracao.

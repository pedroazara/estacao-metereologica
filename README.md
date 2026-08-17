# Estação Meteorológica Móvel

Estação portátil para coleta de temperatura, umidade, pressão e posição.

**Fase atual:** aquisição de posição via sensor WitMotion **WTGAHRS3-485**
(GPS/BeiDou + IMU de 6 eixos), lido por Modbus RTU sobre um adaptador
RS485-USB.

## Duas áreas, papéis distintos

O projeto tem duas áreas. Elas **não** são implementações rivais do mesmo
sistema — cada uma faz uma parte (decidido em 17/08/2026, ver
[docs/04](docs/04-telemetria.md)):

| Área | Onde | Papel |
|---|---|---|
| **ESP32 — campo** | [`firmware/`](firmware/) | lê os sensores no RS485, carimba, grava tudo no cartão SD, serve um painel de conferência com o dado **cru**. Não interpreta nada. |
| **Python — base** | raiz (`src/`, `docs/`, `tests/`) | recebe, roda o filtro de Kalman em lote, persiste, analisa. Roda num **Raspberry Pi** ligado na tomada; o PC continua servindo de bancada. |

O objetivo final é um **produto**: uma base com tela touch para o pesquisador
analisar a coleta.

Este README cobre a área Python. A do ESP32 tem o seu próprio:
[firmware/README.md](firmware/README.md).

---

## Comece por aqui

1. **[docs/01-guia-conexao-linux.md](docs/01-guia-conexao-linux.md)** — do zero
   até ler coordenadas no terminal. Fiação, driver, permissões, primeiro fix.
2. **[docs/02-protocolo-modbus-wtgahrs3.md](docs/02-protocolo-modbus-wtgahrs3.md)** —
   mapa completo de registradores e fórmulas de conversão.
3. **[docs/03-filtro-kalman.md](docs/03-filtro-kalman.md)** — teoria, derivação
   e as decisões de projeto do filtro de fusão GPS + acelerômetro.
4. **[docs/04-telemetria.md](docs/04-telemetria.md)** — **a arquitetura do
   sistema**: divisão de papéis, contrato de dados, cartão SD, roadmap e o
   histórico das decisões. Comece por aqui se a dúvida for "por que assim?".
5. **[docs/05-base-raspberry-pi.md](docs/05-base-raspberry-pi.md)** — preparar o
   Raspberry Pi da base do zero: sistema, broker, `systemd`, e o teste ponta a
   ponta sem hardware nenhum.

## Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo usermod -aG dialout $USER   # depois faça logout/login
```

## Uso

```bash
cd src

# 1. descobrir porta, baud e endereço Modbus do sensor
python3 scan_barramento.py

# 2. ler posição em loop e gravar CSV
python3 ler_posicao.py --porta /dev/ttyUSB0

# 3. CALIBRAÇÃO do acelerômetro (uma vez, antes de confiar em roll/pitch)
python3 calibrar.py --porta /dev/ttyUSB0

# 4. PAINEL COMPLETO: mapa GPS + acelerômetro, em abas
python3 painel.py --porta /dev/ttyUSB0

# 5. gravar tudo (posição + IMU + temperatura)
python3 ler_posicao.py --porta /dev/ttyUSB0 --completo --hz 5
```

### Painel

Abre sozinho em `http://localhost:8770`, com duas abas:

**Mapa GPS** — posição sobre OpenStreetMap ou imagem de satélite, rastro do
trajeto, distância percorrida, velocidade e exportação GPX.

**Acelerômetro** — círculo de força G com rastro, barras por eixo com retenção
de pico, horizonte artificial e giroscópio.

O botão **Kalman** alterna entre o rastro filtrado (verde) e o bruto (azul).
Com o filtro ligado o painel mostra também a distância filtrada e a incerteza
de posição estimada. Ver [docs/03](docs/03-filtro-kalman.md).

O IMU é lido a 10 Hz e o GPS a 1 Hz, intercalados na mesma porta serial.

Para testar sem o sensor conectado:

```bash
python3 painel.py --simular
```

A versão só do mapa continua disponível em `mapa_ao_vivo.py` (porta 8765).

## Estrutura

```
estacao-metereologica/
├── docs/
│   ├── 01-guia-conexao-linux.md      guia passo a passo
│   ├── 02-protocolo-modbus-wtgahrs3.md  registradores e conversões
│   ├── 03-filtro-kalman.md           teoria e aplicação do filtro
│   ├── 04-telemetria.md              ← arquitetura do sistema e decisões
│   ├── 05-base-raspberry-pi.md       preparar a base do zero
│   └── datasheets/                   PDFs oficiais da WitMotion
├── src/
│   ├── wtgahrs3.py                   driver Modbus do sensor
│   ├── filtro_kalman.py              fusão GPS + acelerômetro
│   ├── scan_barramento.py            descoberta automática de porta/baud/endereço
│   ├── calibrar.py                   calibração do acelerômetro
│   ├── painel.py                     painel web: mapa GPS + acelerômetro
│   ├── mapa_ao_vivo.py               só o mapa (versão separada)
│   ├── ler_posicao.py                aquisição contínua → CSV
│   ├── servidor_telemetria.py        ← base: recebe, filtra, grava e serve
│   ├── simular_estacao.py            estação falsa, para testar a base sem hardware
│   ├── diagnostico_gps.py            (investigação) decodificação de coordenadas
│   ├── varredura_registradores.py    (investigação) busca no mapa de registradores
│   ├── log_bruto.py                  (investigação) valores crus e codificação
│   └── kalman/                       lições numeradas, do zero ao filtro final
├── servidor/
│   └── mosquitto-estacao.conf        config do broker (não é opcional — ver doc 04)
├── firmware/                         ← área do ESP32 (Arduino IDE)
│   ├── README.md                     fiação, níveis lógicos e roteiro
│   ├── teste_sensor_ambiental/       diagnóstico do sensor RS485 de T/UR/pressão
│   ├── painel_wifi/                  painel de conferência de campo
│   └── estacao_esp32/
│       ├── estacao_esp32.ino         sketch: leitura contínua no monitor serial
│       ├── wtgahrs3.h                driver Modbus RTU portado do Python
│       ├── wtgahrs3.cpp
│       ├── ambiente_rs485.h          driver do sensor ambiental (T/UR)
│       └── ambiente_rs485.cpp
├── data/                             CSVs coletados
└── requirements.txt
```

## O que o WTGAHRS3 cobre — e o que falta

| Grandeza | WTGAHRS3 | Observação |
|---|---|---|
| Posição (lat/lon/alt) | ✅ | GPS + BeiDou, registradores `0x49`–`0x50` |
| Velocidade e rumo sobre o solo | ✅ | `0x4E`–`0x50`, só válido em movimento |
| Aceleração e velocidade angular | ✅ | IMU de 6 eixos |
| Roll e pitch | ✅ | precisos, referenciados pela gravidade |
| **Yaw / bússola** | ⚠️ | integração do giroscópio, com zero arbitrário e deriva acumulada — **não é rumo geográfico** |
| **Pressão atmosférica** | ❌ | não medida (registrador `0x45` existe no protocolo genérico, mas lê 0 Pa) |
| **Temperatura do ar** | ⚠️ | `0x40` é a temperatura **do chip**, não do ar — lê alto por autoaquecimento |
| **Umidade relativa** | ❌ | não medida |

> **Não há magnetômetro nem barômetro.** O documento de especificação oficial
> (`docs/datasheets/WTGAHRS3.pdf`) diz: *"The built-in chips of the product are
> **JY61P** and GPS module"* — o JY61P é um chip de **6 eixos**, acelerômetro e
> giroscópio apenas. A tabela de precisão de rumo do datasheet do 485 confirma:
> *"Heading accuracy: 6-axis algorithm, static 0.5° (**Dynamic integral
> cumulative error exists**)"* — erro integral cumulativo é assinatura de rumo
> por giroscópio, sem referência absoluta. E a tabela de conteúdo de saída, nas
> três variantes, lista apenas *"Time, acceleration, angular velocity, angle,
> latitude and longitude, ground speed"*: sem campo magnético, sem pressão.
>
> A menção a "Barometer", "Magnetometer" e "Built-in WT901B" na capa dos
> datasheets é template copiado do WT901B — o mesmo vício que aparece nos
> anúncios de marketplace. Confirmado na bancada: `0x45` retorna 0 Pa.
>
> **Consequência prática:** para rumo geográfico, use o *course over ground* do
> GPS (registrador `0x4E`), que só é válido em movimento. Não existe calibração
> de bússola neste modelo.

Ou seja: **as três grandezas meteorológicas do projeto (temperatura, umidade,
pressão) precisam de um sensor dedicado.** O WTGAHRS3 entra como módulo de
posição e movimento. Um **BME280** (I²C, ~R$ 25) resolve as três de uma vez.

## Ficha rápida do sensor

| Item | Valor |
|---|---|
| Modelo | WitMotion WTGAHRS3-485 |
| Chip interno | JY61P (IMU 6 eixos) + módulo GNSS |
| Interface | RS485, Modbus RTU |
| Endereço padrão | `0x50` |
| Baud padrão | 9600, 8N1 (suporta 4800 a 230400) |
| Alimentação | 5–36 V, **padrão 12 V**; 21 mA em 12 V na versão 485 |
| Precisão de inclinação | 0,2° (roll e pitch) |
| Precisão de rumo | 0,5° estático, com deriva dinâmica acumulada |
| GNSS | GPS + BeiDou + GLONASS + QZSS, precisão <2,5 m (CEP50) |
| Taxa de atualização GNSS | 5 Hz |
| TTFF | ≤32 s partida a frio, ≤1 s a quente |
| Tamanho / proteção | 40 × 32 × 16 mm, invólucro robusto |
| Temperatura de operação | −40 °C a +85 °C |

> A tabela de parâmetros elétricos do datasheet especifica 5 V mínimo, 12 V
> padrão, 36 V máximo. O aviso de "mais de 5 V danifica" que aparece no manual
> é boilerplate copiado e contradiz a tabela do próprio documento. 5 V funciona
> normalmente; a faixa maior é útil se você for alimentar por bateria de 12 V.

## TODO

### Pendências abertas

- [ ] **Confirmar se o firmware alterna a codificação de coordenadas.** Numa
  sessão de ~240 amostras, uma saiu com a codificação NMEA correta em vez da
  bugada (ver `docs/02-`). Se isso se repetir, o driver precisa detectar a
  codificação por amostra em vez de assumir uma. Rodar
  `log_bruto.py --minutos 5` e ver o veredito.
  *O filtro de salto do painel já protege na prática, mas a causa segue aberta.*
- [x] ~~Baixar os PDFs oficiais e confirmar ausência de barômetro e tensão de
  alimentação.~~ Feito — estão em `docs/datasheets/`. Resultado: sem barômetro,
  **sem magnetômetro** (chip JY61P, 6 eixos), e alimentação de 5 a 36 V.
- [ ] Reportar o bug de sinal ao suporte da WitMotion (`liubin@wit-motion.com`)
  com o caso reproduzível.

### Hardware

- [ ] **Bancada motorizada para caracterizar o IMU.** Suporte com motor de
  passo girando ângulos controlados e repetíveis. Sem magnetômetro no sensor,
  o uso deixa de ser calibração magnética e passa a ser **caracterização**:
  medir a deriva do yaw ao longo do tempo, verificar a linearidade de roll e
  pitch contra ângulos conhecidos, e quantificar o erro do giroscópio. O ganho
  é a **repetibilidade** — comparar medições entre si só faz sentido se a
  entrada for a mesma.
- [ ] **Sensor ambiental BME280** (I²C): temperatura, umidade e pressão. É o
  que fecha o escopo meteorológico do projeto — o WTGAHRS3 não mede nenhuma
  das três de forma útil.
- [ ] Definir montagem final: fixação do sensor, posição da antena GNSS com
  vista para o céu, alimentação por bateria, proteção contra chuva.
- [x] ~~Avaliar operação autônoma (Raspberry Pi) para não depender do notebook
  em campo.~~ Decidido em 17/08/2026 — mas ao contrário do que a linha sugeria:
  quem fica autônomo **em campo** é o ESP32; o **Pi 3B fica na base**, ligado na
  tomada. Levar o Pi a campo esbarra em consumo (~2,5–3,5 W) e na entrada
  microUSB, que dá *undervoltage* e corrompe o cartão. Ver
  [docs/05](docs/05-base-raspberry-pi.md).
- [ ] **Módulo de cartão SD no ESP32** (SPI, ~R$ 10–15). É o que fecha a
  custódia do dado cru em campo — a flash interna só aguenta ~25 min.
- [ ] Tela touch da base — preferir a DSI oficial de 7" a HDMI + USB.

### Software

- [ ] **Gerenciamento de trajetórias.** Salvar sessões nomeadas, listar as
  anteriores, recarregar no mapa, comparar duas. Hoje tudo vai para um CSV
  único que só cresce.
- [ ] **Mapa offline.** Cache dos tiles para uso em campo sem internet. Hoje o
  mapa depende de conexão — que é justamente o que falta onde a estação vai
  ser usada. Virou **requisito**, não melhoria: o painel da base roda num Pi 3B
  com 1 GB de RAM, onde tiles online são peso que não cabe. Ver
  [docs/05](docs/05-base-raspberry-pi.md).
- [x] ~~**Filtro de Kalman** fundindo GPS e acelerômetro.~~ Feito — estado 4D
  (posição e velocidade Norte/Leste), modelo de velocidade constante com Q
  modulado pelo acelerômetro e R adaptado pelo HDOP. Integrado ao painel,
  documentado em `docs/03-filtro-kalman.md`. Melhoria medida de 52,8% no erro
  RMS em simulação controlada.
- [ ] Validar o filtro em trajeto real gravado com `--min-dist 0`, numa sessão
  única — os dados atuais não servem para isso (ver `docs/03`).
- [ ] Ajustar `q` e `r_pos` com dados reais: medir a variância do GPS parado
  para obter `R`, e calibrar `q` observando o rastro em movimento.
- [ ] Gráficos históricos: temperatura × tempo, altitude × distância
  percorrida, e as variáveis meteorológicas sobre o trajeto.
- [ ] Exportação em KML, para abrir no Google Earth.
- [ ] Detecção automática de paradas, para segmentar o trajeto em trechos.
- [ ] Sincronizar o relógio dos registros pelo tempo do GPS, em vez do relógio
  do PC — o GPS é uma referência de tempo muito mais precisa e já está ali.
  Especificado nos campos `ts` e `ms` do contrato de dados, incluindo o caso
  em que o GPS **nunca** pega fix. Ver
  [docs/04](docs/04-telemetria.md#contrato-de-dados).

### Validação

- [ ] Comparar as medidas meteorológicas contra uma estação fixa de referência
  (INMET tem estação automática em Lavras) para estimar erro.
- [ ] Testar o comportamento em movimento real de veículo: vibração,
  perda de sinal em túnel e sob vegetação, taxa de amostragem adequada.

## Fontes

- [Protocolo Modbus oficial da WitMotion](https://wit-motion.gitbook.io/witmotion-sdk/wit-standard-modbus-protocol/modbus-communication-protocol)
- [Página do produto WTGAHRS3 (links dos PDFs)](https://witmotion-sensor.com/products/industry-grade-beidou-wtgahrs3-6-axis-gps-imu-accelerometer)
- [Software e drivers WitMotion](https://witmotion-sensor.com/pages/software-downloads-and-general-information)

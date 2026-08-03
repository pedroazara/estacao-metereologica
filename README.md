# Estação Meteorológica Móvel

Estação portátil para coleta de temperatura, umidade, pressão e posição.

**Fase atual:** aquisição de posição via sensor WitMotion **WTGAHRS3-485**
(GPS/BeiDou + IMU 6 eixos + barômetro), lido por Modbus RTU sobre um adaptador
RS485-USB.

---

## Comece por aqui

1. **[docs/01-guia-conexao-linux.md](docs/01-guia-conexao-linux.md)** — do zero
   até ler coordenadas no terminal. Fiação, driver, permissões, primeiro fix.
2. **[docs/02-protocolo-modbus-wtgahrs3.md](docs/02-protocolo-modbus-wtgahrs3.md)** —
   mapa completo de registradores e fórmulas de conversão.

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

# 3. PAINEL COMPLETO: mapa GPS + acelerômetro, em abas
python3 painel.py --porta /dev/ttyUSB0

# 4. gravar tudo (posição + IMU + temperatura)
python3 ler_posicao.py --porta /dev/ttyUSB0 --completo --hz 5
```

### Painel

Abre sozinho em `http://localhost:8770`, com duas abas:

**Mapa GPS** — posição sobre OpenStreetMap ou imagem de satélite, rastro do
trajeto, distância percorrida, velocidade e exportação GPX.

**Acelerômetro** — círculo de força G com rastro, barras por eixo com retenção
de pico, horizonte artificial, bússola e giroscópio.

O IMU é lido a 10 Hz e o GPS a 1 Hz, intercalados na mesma porta serial.

Para testar sem o sensor conectado:

```bash
python3 painel.py --simular
```

As versões separadas continuam disponíveis: `mapa_ao_vivo.py` (só GPS,
porta 8765) e `monitor_imu.py` (só IMU, porta 8766).

## Estrutura

```
estacao-metereologica/
├── docs/
│   ├── 01-guia-conexao-linux.md      guia passo a passo
│   ├── 02-protocolo-modbus-wtgahrs3.md  registradores e conversões
│   └── datasheets/                   PDFs oficiais (baixe do Drive)
├── src/
│   ├── wtgahrs3.py                   driver Modbus do sensor
│   ├── scan_barramento.py            descoberta automática
│   └── ler_posicao.py                aquisição + CSV
├── data/                             CSVs coletados
└── requirements.txt
```

## O que o WTGAHRS3 cobre — e o que falta

| Grandeza | WTGAHRS3 | Observação |
|---|---|---|
| Posição (lat/lon/alt) | ✅ | GPS + BeiDou, registradores `0x49`–`0x50` |
| Velocidade e rumo | ✅ | `0x4E`–`0x50` |
| Aceleração / giro / ângulos | ✅ | IMU 6 eixos + magnetômetro |
| **Pressão atmosférica** | ❌ | registrador `0x45` existe no protocolo mas **lê 0 Pa** — este modelo não tem barômetro (confirmado na bancada em 03/08/2026) |
| **Temperatura do ar** | ⚠️ | `0x40` é a temperatura **do chip**, não do ar — lê alto por autoaquecimento |
| **Umidade relativa** | ❌ | não medida |

> **Nota sobre o barômetro:** vários anúncios de marketplace listam "barometer"
> para o WTGAHRS3, mas é texto copiado de outros modelos da linha. O manual
> oficial da WitMotion cita como módulos com barômetro apenas WT901B, HWT901B,
> WTGAHRS1 e WTGAHRS2. O nome oficial deste é "**6-axis** GPS-IMU", contra
> "**10-axis** ... +Air Pressure" do WTGAHRS1. Na prática, `0x45` retorna 0.

Ou seja: **as três grandezas meteorológicas do projeto (temperatura, umidade,
pressão) precisam de um sensor dedicado.** O WTGAHRS3 entra como módulo de
posição e movimento. Um **BME280** (I²C, ~R$ 25) resolve as três de uma vez.

## Ficha rápida do sensor

| Item | Valor |
|---|---|
| Modelo | WitMotion WTGAHRS3-485 |
| Interface | RS485, Modbus RTU |
| Endereço padrão | `0x50` |
| Baud padrão | 9600, 8N1 |
| Alimentação | 5 V (~40 mA) — **confirme no datasheet antes de ligar** |
| Precisão angular | 0,05° estático / 0,1° dinâmico |
| Taxa de saída | ajustável, 0,2–200 Hz |

## Fontes

- [Protocolo Modbus oficial da WitMotion](https://wit-motion.gitbook.io/witmotion-sdk/wit-standard-modbus-protocol/modbus-communication-protocol)
- [Página do produto WTGAHRS3 (links dos PDFs)](https://witmotion-sensor.com/products/industry-grade-beidou-wtgahrs3-6-axis-gps-imu-accelerometer)
- [Software e drivers WitMotion](https://witmotion-sensor.com/pages/software-downloads-and-general-information)

# Estação Meteorológica Móvel — Skylog

Estação portátil para medir temperatura, umidade e luminosidade, associadas
à localização, ao movimento e ao som coletados pelo telefone da pessoa que
realiza a medição.

## Configuração vigente — 01/10/2026

| Origem | Sensor ou recurso | Responsabilidade |
|---|---|---|
| ESP32 | Sensor ambiental RS485 | Temperatura e umidade |
| ESP32 | KY-028 | Temperatura auxiliar |
| ESP32 | GY-302 / BH1750 | Luminosidade |
| Telefone | GPS / localização | Posição e percurso da coleta |
| Telefone | Sensores de movimento | Movimento / dados inerciais |
| Telefone | Microfone | Níveis sonoros, sem gravação de áudio |

**WTGAHRS3-485, KY-038 e KY-013 deixam de fazer parte da montagem.** GPS e
movimento deixam de depender do módulo WitMotion; o som passa a vir do
microfone do telefone. O KY-013 não será substituído por outro termistor:
a temperatura auxiliar fica somente com o KY-028.

Os três sensores ligados ao ESP32 ficam **externos à placa principal**.
No KiCad, seus footprints representam apenas os conectores: GY-302 com
cinco contatos, KY-028 com quatro e sensor ambiental RS485 com quatro,
todos com passo de 2,54 mm. O divisor de dois resistores de 10 kΩ previsto
para o antigo KY-038 deixa de ser necessário nessa montagem.
A pressão atmosférica não está confirmada no conjunto atual de sensores.

## Responsabilidades no sistema

| Parte | Onde | Papel |
|---|---|---|
| **ESP32** | [firmware/](firmware/) | Ler os três sensores externos e enviar suas leituras ao aplicativo. |
| **Telefone** | [app/](app/) | Fornecer localização, movimento e som; reunir as leituras ambientais recebidas por Bluetooth e gravar a coleta localmente. |
| **Base Python / Raspberry Pi** | [src/](src/) e [docs/05](docs/05-base-raspberry-pi.md) | Receber as coletas sincronizadas, armazenar, processar e apresentar os resultados. |
| **Placa de interface** | [hardware/skylog/](hardware/skylog/) | Reunir ESP32, conversor RS485 e conexões dos sensores externos. |

Essa é a configuração aprovada; a migração do código ainda precisa ser
concluída. O aplicativo já usa GPS e microfone do telefone e grava dados
ambientais via Bluetooth. A captura de movimento pelo telefone ainda não
foi implementada. O firmware ainda contém rotinas de WTGAHRS3, KY-038 e
KY-013, e o aplicativo ainda oferece KY-013/KY-038 na seleção de sensores.
Essas referências descrevem a implementação anterior, não a lista de
componentes da montagem vigente.

## Próximas adequações

- [ ] Ajustar o firmware para ler somente o sensor ambiental RS485, o KY-028
  e o GY-302, retirando a aquisição de GPS/IMU, KY-038 e KY-013 do ESP32.
- [ ] Retirar KY-013/KY-038 das opções de novas coletas no aplicativo,
  preservando a leitura de sessões antigas.
- [ ] Implementar a coleta de movimento pelos sensores do telefone e
  definir seu formato de registro, unidades e horários de amostragem.
- [ ] Alinhar o processamento na base às origens de GPS, movimento e som do
  telefone; manter compatibilidade com os arquivos já coletados.
- [ ] Revisar alimentação, terra comum e UART do conversor no esquema KiCad
  e sincronizar a PCB com o esquema antes de rotear.
- [ ] Concentrar os três conectores externos em uma borda e verificar a
  viabilidade de rotear em uma única camada de cobre. Retomar no Windows.
- [ ] Validar uma coleta completa com os sensores ambientais e o telefone.

## Comece por aqui

1. [Aplicativo](app/README.md): preparar o telefone, selecionar sensores,
   gravar e sincronizar a coleta.
2. [Firmware](firmware/README.md): pinagem dos três sensores mantidos e
   diferenças entre a montagem vigente e os sketches antigos.
3. [Arquitetura e telemetria](docs/04-telemetria.md): divisão de
   responsabilidades, dados atuais e contratos históricos.
4. [Base Raspberry Pi](docs/05-base-raspberry-pi.md): preparação e operação
   da base; exemplos de GPS/IMU externo são históricos.
5. [Filtro de Kalman](docs/03-filtro-kalman.md): implementação existente;
   a entrada de movimento do telefone ainda precisa de integração.
6. [Projeto KiCad no Windows ou Linux](hardware/skylog/README.md): bibliotecas
   incluídas, arquivo para abrir e pendências de conexão e roteamento.

Os guias de [conexão Linux](docs/01-guia-conexao-linux.md) e
[protocolo WTGAHRS3](docs/02-protocolo-modbus-wtgahrs3.md) continuam
preservados como referência da bancada anterior.

## Referência histórica: bancada com WTGAHRS3

As instruções, características do sensor e registros de desenvolvimento
abaixo pertencem à montagem anterior. Não são requisitos de compra,
montagem ou coleta da configuração vigente. Os drivers e dados antigos
continuam no repositório para consulta e reprodução de experimentos.

### Instalação das ferramentas históricas

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo usermod -aG dialout $USER   # depois faça logout/login
```

### Uso histórico com WTGAHRS3

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

### Painel histórico de bancada

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

## Estrutura e ferramentas preservadas

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
│   ├── wtgahrs3.py                   driver Modbus histórico do WTGAHRS3
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
├── app/                              aplicativo de coleta no telefone
├── hardware/skylog/                  projeto KiCad e conectores externos
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

## Referência histórica: medições do WTGAHRS3

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

Na etapa anterior, essas limitações motivaram a avaliação de um BME280.
Essa proposta não integra a montagem vigente: temperatura e umidade vêm
do sensor ambiental RS485, temperatura auxiliar do KY-028 e luminosidade
do GY-302; posição e movimento passam a ser responsabilidade do telefone.

## Ficha histórica do WTGAHRS3

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

## Registro histórico de pendências

Esta lista registra o planejamento anterior à decisão de 01/10/2026.
Itens como bancada WitMotion, antena GNSS, BME280 e cartão SD no ESP32
não são requisitos da montagem vigente. As adequações atuais estão em
[Próximas adequações](#próximas-adequações).

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

## Fontes da referência histórica

- [Protocolo Modbus oficial da WitMotion](https://wit-motion.gitbook.io/witmotion-sdk/wit-standard-modbus-protocol/modbus-communication-protocol)
- [Página do produto WTGAHRS3 (links dos PDFs)](https://witmotion-sensor.com/products/industry-grade-beidou-wtgahrs3-6-axis-gps-imu-accelerometer)
- [Software e drivers WitMotion](https://witmotion-sensor.com/pages/software-downloads-and-general-information)

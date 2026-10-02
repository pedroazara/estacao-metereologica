# Skylog — proposta única de PCB

Abra somente [skylog.kicad_pro](skylog.kicad_pro) no KiCad 10. O projeto reúne
[um esquema](skylog.kicad_sch) e [uma PCB](skylog.kicad_pcb). Esta é a proposta
bicamada consolidada em 02/10/2026, com bibliotecas locais incluídas.
Mantenha a pasta `hardware/skylog` completa ao transportar o projeto.

## Placa proposta

- Duas faces de cobre, F.Cu e B.Cu, sem jumpers de fio.
- Planos de GND preenchidos nas duas faces, com alívios térmicos. Os 11 pontos
  de terra estão conectados pelo plano do verso; **não há trilhas GND**.
- Folga dos planos de 0,25 mm, alívios de 0,30 mm e remoção de ilhas isoladas.
- Dimensões da PCB: **86 × 111 mm**, com recorte superior de **20 × 2 mm**.
- ESP32 junto à borda, com a região desenhada da antena fora do material da
  placa-base. Reservar espaço para a parte do módulo que ultrapassa a borda.
- Trilha de entrada de alimentação com 0,80 mm; sinais com 0,20 mm.
- Serigrafia dos contatos do ESP32 com altura mínima de 0,80 mm.

A posição da antena segue a orientação de colocá-la além da placa-base nas
[recomendações da Espressif](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32/pcb-layout-design.html#general-principles-of-pcb-layout-for-modules-positioning-a-module-on-a-base-board).
O gabinete e o alcance do rádio ainda exigem avaliação na montagem real.
O footprint `skylog:ESP32-DevKitC_BoardEdge` adapta a serigrafia à borda,
preservando a posição relativa, o formato e a furação dos contatos.

Veja [as duas faces da PCB](documentacao/proposta-final/pcb.png) e
[o esquema](documentacao/proposta-final/esquema.png).

## Conexões externas

| Ponto | Ligação |
|---|---|
| J1.1 | 3V3 para o módulo ENS160+AHT21 |
| J1.2 | GND do ENS160+AHT21 |
| J1.3 | SDA, GPIO21 do ESP32 |
| J1.4 | SCL, GPIO22 do ESP32 |
| J2 — WEMOS_5V | Entrada da saída regulada de 5 V do Wemos Battery Shield V3 |
| J3 — WEMOS_GND | GND do Wemos Battery Shield V3 |

J1 é uma interface por cabo de passo 2,54 mm; sua numeração pertence à PCB,
e não presume a ordem física dos contatos do módulo. Monte o cabo seguindo
os rótulos do ENS160+AHT21. A alimentação escolhida é 3,3 V. Se expostos no
módulo, CS deve ficar em 3,3 V e ADD em GND; INT permanece sem uso. O I²C é
compartilhado com o BH1750.

J2 e J3 são dois pontos de solda independentes, com pads de 3 mm e furos de
1,2 mm. O shield e a bateria ficam externos. J2 alimenta a rede VIN_SENSOR,
o contato 5V do ESP32 e o conversor RS485. Usar somente **5 V regulados**;
não conectar diretamente a célula da bateria, 3V3, 12 V ou 24 V nessa entrada.

Desconecte a alimentação do shield antes de alimentar o ESP32 por USB.
O [guia da DevKitC](https://documentation.espressif.com/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html#power-supply-options)
define USB, 5V/GND e 3V3/GND como opções mutuamente exclusivas.

As demais ligações são RS485 em GPIO17/TX e GPIO16/RX, BH1750 com ADDR em GND,
e KY-028 com AO em GPIO35 e DO sem uso. Os sensores ficam externos por cabos;
ESP32 e conversor RS485 ocupam a placa de interface.

## Verificação

A proposta consolidada passou no KiCad 10.0.5 com:

- **ERC: zero erros e zero avisos.**
- **DRC: zero erros, zero avisos e zero conexões pendentes.**
- **Zero diferenças entre esquema e PCB.**
- **Zero trilhas GND**, com todos os 11 contatos de terra ligados ao plano.

Os 29 contatos externos intencionalmente livres têm marcadores NC. Três
PWR_FLAG documentam 5V externo, retorno GND e 3V3 do regulador integrado à
DevKitC. Nenhuma verificação foi desativada para obter esses resultados.

Relatórios atuais: [ERC](documentacao/proposta-final/erc.json),
[DRC](documentacao/proposta-final/drc.json) e
[conectividade/geometria](documentacao/proposta-final/validacao-pcb.json).

## Antes de encomendar

Conferir a pinagem, as dimensões e o encaixe dos módulos físicos. Em especial,
o footprint do conversor RS485 reproduz as ilhas da placa de referência;
a montagem direta e os conectores JST do exemplar real precisam ser validados.
Há também uma divergência de identificação do transceptor no projeto original,
descrita em [conversor-rs485.md](conversor-rs485.md). A checagem do KiCad não
resolve essa diferença no hardware físico. Confirmar capacidade da fonte,
regras da fábrica e montagem antes de liberar a fabricação.

## Histórico

As versões alternativas, os testes e os esquemas auxiliares foram retirados
da área de trabalho. O histórico completo anterior à consolidação está em
[um backup ZIP](backups/historico-antes-proposta-unica-2026-10-02.zip), com
manifesto SHA-256 e integridade verificada. Não é necessário abrir esse
histórico para usar o projeto atual.

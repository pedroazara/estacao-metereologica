# Conversor RS485 / TTL V2 — módulo pronto

## Uso na configuração vigente — 01/10/2026

O conversor permanece necessário para o **sensor ambiental RS485 de
temperatura e umidade**. O WTGAHRS3-485 foi retirado da montagem;
GPS, movimento e som passam a ser responsabilidade do telefone.
O ESP32 mantém também KY-028 (analógico) e GY-302 (I²C), que não usam
este conversor. KY-013 e KY-038 não integram mais a montagem.

Os sensores externos usam somente footprints de conectores na placa
principal. O footprint deste conversor continua representando o módulo
conforme a documentação abaixo; ele é uma interface, não um sensor.
A decisão de escopo foi documentada, mas o firmware ainda precisa ser
migrado. Para o controle automático do módulo descrito aqui, a configuração
prevista continua sendo `PINO_DE = -1`.

Símbolo e footprint para KiCad, preparados a partir do [projeto de Virendra K no OSHWLab](https://oshwlab.com/virendra/rs485-to-ttl-converter-module_v2). Os arquivos EasyEDA do esquemático e da PCB foram baixados em 29/09/2026 e preservados em `originais/conversor-rs485/`. O link fornece um projeto completo da placa do conversor; o símbolo externo e o footprint de módulo em KiCad foram criados a partir dele.

## Onde abrir

Todos os arquivos de trabalho estão nesta pasta **Skylog**:

- [Projeto principal da estação](skylog.kicad_pro).
- [Esquemático do módulo RS485/TTL](modulo-rs485-ttl-v2.kicad_sch).
- [Cópia da estação com o conversor ligado](skylog-com-conversor-rs485.kicad_pro).
- [Footprint do conversor](skylog.pretty/RS485_TTL_V2_OSHWLab_PadGeometry.kicad_mod).
- [Prévia do esquema com o conversor](documentacao/conversor-rs485/skylog-com-conversor-rs485.png).

Na organização de 30/09/2026, o conteúdo do esquema principal foi preservado. A biblioteca local foi reunida em `skylog.kicad_sym`, com o sensor existente e o conversor. As imagens e os relatórios ficam em `documentacao/conversor-rs485/`; os arquivos EasyEDA em `originais/conversor-rs485/`; as cópias antigas em `backups/`.

O ZIP da entrega anterior foi guardado em `backups/conversor-rs485-entrega-2026-09-29.zip` como histórico. Para trabalhar, use os arquivos desta pasta, que já estão organizados e compartilham as bibliotecas locais.

## Arquivos e uso

- **Símbolo instalado no projeto:** `skylog:RS485_TTL_V2_OSHWLab`, dentro de `skylog.kicad_sym`. No editor de esquemas do projeto Skylog, adicione um símbolo e pesquise `RS485_TTL_V2`.
- **Footprint associado:** `skylog:RS485_TTL_V2_OSHWLab_PadGeometry`, em `skylog.pretty/`.
- **Esquema do módulo:** `modulo-rs485-ttl-v2.kicad_sch`, com os sete contatos externos identificados.
- **Cópia de integração com a estação:** `skylog-com-conversor-rs485.kicad_pro` e `.kicad_sch`. Inclui U3, UART do ESP32, A/B do sensor, terra comum e rede de 5 V do conversor. A alimentação VIN_SENSOR continua separada.
- **Pré-visualizações e conferência:** pasta `documentacao/conversor-rs485/`.
- **Geometria detalhada, origem e hash:** `documentacao/conversor-rs485/geometria-footprint.json`.

A cópia de integração usa o salvamento automático mais recente disponível às 17:32 de 29/09/2026. O arquivo principal `skylog.kicad_sch`, sua PCB e o firmware não foram alterados. Havia um editor aberto com alterações não salvas. Um backup dos arquivos existentes está em `backups/conversor-rs485-antes-inclusao-2026-09-29.zip`.

## Pinagem preservada

Os números incluem o conector original: `CN1.1` significa pino 1 do conector CN1. Cada número do símbolo corresponde ao mesmo número no footprint.

| Número | Sinal do módulo | Ligação na estação |
|---|---|---|
| CN1.1 | TXD — entrada do conversor | ESP32 GPIO17, TX da UART2 |
| CN1.2 | RXD — saída do conversor | ESP32 GPIO16, RX da UART2 |
| CN1.3 | GND | Terra comum |
| CN1.4 | +5V | Alimentação de 5 V do conversor |
| CN2.1 | B | RS485_B do sensor |
| CN2.2 | A | RS485_A do sensor |
| CN2.3 | GND | Terra comum |

TXD/RXD são os nomes do projeto original. A direção foi conferida seguindo as conexões internas do 74HC04: TXD entra no pino 1; RXD sai do pino 4. O módulo tem controle automático de direção e não expõe DE/RE. Ao usar este módulo com o firmware da estação, selecionar `PINO_DE = -1`; os sketches existentes continuam com sua configuração anterior.

## Montagem e dimensões

**O footprint reproduz as ilhas de solda, contorno e furos da PCB do conversor. Não representa um encaixe validado para os conectores JST já montados.**

Para usar o módulo pronto com seus JST, a ligação é por cabos com conectores compatíveis; os conectores que vão na placa da estação devem corresponder ao cabo escolhido. Para apoiar o módulo sobre a placa-base e usar este footprint como conexão elétrica direta, será necessário preparar terminais adequados voltados para a placa-base e verificar espaçamento vertical, encaixe, comprimento e acesso aos contatos. Não assumir que os JST montados encaixam em barras fêmeas de 2,54 mm.

- Contorno da placa: **53,721 × 19,177 mm**.
- CN1: 4 contatos; CN2: 3 contatos; passo nominal **2,50 mm**.
- Diâmetro dos furos dos contatos: **1,100023 mm**, conforme arquivo-fonte.
- Quatro furos mecânicos não metalizados: **2,032 mm**.
- Centros dos furos: X = **1,651 / 52,070 mm**; Y = **1,524 / 17,526 mm**.
- Origem no canto superior esquerdo, vista por cima, mesma orientação da PCB do autor.
- F.Fab, F.SilkS e F.CrtYd incluídos. Courtyard com margem de 0,5 mm, sujeito ao espaço real necessário para cabos, terminais e fixadores.
- Sem modelo 3D: a altura e a montagem física não foram medidas.

A conversão usa 0,254 mm por unidade EasyEDA. O campo HOLE do arquivo foi conferido no desenho renderizado: círculo com raio de 4 unidades, portanto diâmetro de 2,032 mm. A descrição desse campo na documentação EasyEDA é ambígua; foi usada a geometria efetivamente exibida pelo editor.

## Divergência elétrica no original

O autor mostra **SN75176BDR** no valor de U2, mas o campo de fabricante informa **SN75HVD08D**. O pino VCC de U2 está ligado a **3,3 V**, gerados pelo AMS1117 a partir da entrada de 5 V. Isso exige conferir o chip instalado: o [SN75176B da TI](https://www.ti.com/lit/ds/symlink/sn75176b.pdf) exige alimentação de 4,75–5,25 V; o [SN75HVD08 da TI](https://www.ti.com/lit/gpn/sn75hvd08) admite 3,0–5,5 V.

O 74HC04 que recebe e fornece os sinais UART também está desenhado na rede de 3,3 V. A cópia de integração representa esse circuito do link; a compatibilidade elétrica do exemplar físico não foi testada. A alimentação externa de 5 V não comprova tolerância de TXD a lógica de 5 V. A terminação de 120 ohms e a polarização já aparecem no circuito original e devem ser consideradas na montagem do barramento.

## Verificação elétrica e geométrica da entrega de 29/09/2026

- KiCad 10.0.6 abriu e exportou ambos os esquemas; previews inspecionados visualmente.
- KiCad `pcbnew` 9.0.9 carregou o footprint e confirmou sete pads elétricos e quatro furos mecânicos.
- Números de pinos do símbolo e pads do footprint conferidos um a um.
- Netlist da integração conferida: GPIO17→TXD, RXD→GPIO16, A→A, B→B, os dois GND no terra comum e CN1.4 na rede de 5 V.
- ERC da cópia: **41 erros e 2 avisos** relacionados à estação ainda incompleta: pinos do ESP32 abertos, fontes de alimentação não definidas, VIN_SENSOR isolado e caminho ausente da biblioteca Espressif existente. Nenhuma ocorrência de ERC identifica U3. Esses resultados não equivalem à liberação da estação para fabricação; o relatório está em `documentacao/conversor-rs485/erc-integracao.rpt`.

## Procedência

Projeto: Virendra K / Sharvi Technology, esquema revisão 1.1 (02/11/2022); página do projeto consultada em 29/09/2026. A página exibe o campo de licença “Public Domain” e também um aviso genérico da plataforma que restringe uso comercial. Ambos os sinais foram preservados aqui sem resolver a contradição por conta própria.

- [Esquemático EasyEDA original](https://easyeda.com/editor#id=2b967d8ad281424394704b3d4fad3b83)
- [PCB EasyEDA original](https://easyeda.com/editor#id=306f8a3a026b492e8094eff702293304)
- [Formato de arquivos EasyEDA](https://docs.easyeda.com/en/DocumentFormat/3-EasyEDA-PCB-File-Format/index.html)

SHA-256 dos originais:

```text
c0408d09d232f7d3c4ccc6a74569735938c77df62e6a59ce9317f42f8abbe5c1  esquematico-easyeda.json
9c9249ee74d1c4a58dfd6d5f31e10e83a872a459262cbfbcae359f15e1746d02  pcb-easyeda.json
```

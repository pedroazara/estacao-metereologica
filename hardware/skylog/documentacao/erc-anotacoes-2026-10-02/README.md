# Revisão das anotações de ERC — 02/10/2026

Os três esquemas — principal, monocamada e bicamada — passaram a apresentar
**zero erros e zero avisos de ERC**, sem regras desativadas nem exclusões novas.
Esta revisão identifica condições reais do projeto para o verificador; não
acrescenta componentes físicos, resistores de pull-up ou circuitos de alimentação.

## Contatos externos sem uso

Foram acrescentados 29 marcadores de não conexão: U1 pinos
`2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 20, 21, 22, 23, 24, 25, 26, 29, 30, 31, 34, 35, 37`
e U3 pino `4` (DO do KY-028). São os contatos sem utilização na placa de
interface. Quatro deles, U1.2, U1.3, U1.4 e U1.5, também geravam a mensagem
de entrada não acionada; os marcadores NC resolvem essa duplicidade.

U1 é uma **DevKitC completa**, que já inclui regulador de 3,3 V, circuito de
reset/EN e interface USB. A ausência de ligação externa em U1.2 não significa
que EN do chip ESP32 esteja flutuando. O circuito incorporado consta do
[esquema oficial da DevKitC V4](https://dl.espressif.com/dl/schematics/esp32_devkitc_v4-sch-20180607a.pdf).
Essa interpretação deve ser conferida com a placa física e não se aplica a
um módulo ESP32 nu no lugar da DevKitC.

## Origem da alimentação

| Anotação | Rede preservada | Fonte física representada |
|---|---|---|
| #FLG01 | `/VIN_SENSOR` | Saída regulada de 5V do Wemos Battery Shield V3 externo, entrando em J2 |
| #FLG02 | `/GND_SENSOR` | Retorno da alimentação externa em J3 |
| #FLG03 | `Net-(U1-3V3)` | Regulador integrado da DevKitC alimentada por 5V |

Os PWR_FLAG têm `on_board=false`, sem footprint, sem inclusão na lista de
componentes físicos e sem alteração dos tipos elétricos dos módulos. A
biblioteca padrão `power` do KiCad 10 foi registrada para manter esses
símbolos verificáveis. O uso de PWR_FLAG para fontes que chegam por conectores
é documentado no [manual do KiCad 10](https://docs.kicad.org/10.0/en/eeschema/eeschema.html).

O shield deve fornecer 5V regulados; sua bateria e sua saída de 3V3 não se
conectam a J2. Desconecte a alimentação externa antes de alimentar a DevKitC
por USB, conforme as opções mutuamente exclusivas do
[guia da Espressif](https://documentation.espressif.com/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html#power-supply-options).

## Bibliotecas e preservação das ligações

Foram registrados os símbolos locais `Espressif` e `Sensores_Modulos` e os
footprints locais `ESP32` e `Sensores_Modulos`, existentes em `bibliotecas/`.
Os seis avisos de biblioteca ausente desapareceram sem divergência de símbolo.

As netlists exportadas antes/depois das anotações mantiveram **exatamente os
mesmos nomes de redes, membros de cada rede e UUIDs dos componentes**:
41 redes no principal e na monocamada, 39 na bicamada. Não houve alteração de
geometria de footprint nesta revisão de ERC. A variante de serigrafia de U1
na borda da bicamada é uma alteração separada e deve permanecer documentada
com o footprint específico dessa versão.

- `validacao-esquemas.json`: resultado da comparação dos três esquemas.
- `*-netlist-antes.xml` e `*-netlist-depois.xml`: evidência das ligações.
- `*-erc-final.json`: resultados de ERC.
- `anotacoes-manifest.json`: identificação dos NC e das fontes representadas.
- `*-antes-*.kicad_sch` e cópias das tabelas: arquivos anteriores às anotações.

ERC limpo não comprova a montagem física, a capacidade de corrente da fonte
ou a compatibilidade do exemplar do conversor RS485. Essas verificações dos
módulos reais continuam necessárias antes da fabricação e da energização.

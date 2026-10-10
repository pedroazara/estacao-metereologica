# Skylog — placa de circuito impresso da V1

O projeto de referência é [skylog.kicad_pro](projetos/referencia-bicamada/skylog.kicad_pro), com
[esquema](projetos/referencia-bicamada/skylog.kicad_sch) e [PCB](projetos/referencia-bicamada/skylog.kicad_pcb), no formato KiCad 10.
Há também a variante local [skylog.kicad-pedro_sch.kicad_pro](projetos/desenvolvimento/skylog.kicad-pedro_sch.kicad_pro),
com esquema e PCB próprios. A PCB dessa variante já tinha alterações locais em
10/10/2026; seus Gerbers e arquivos de furação não estão no commit-base.
Não misture as imagens/relatórios da proposta principal com essa variante.
As duas estão identificadas na [documentação técnica](../../docs/versoes/2026-10-10-2cd5977-local/documentacao-tecnica.md).
Mantenha a pasta `hardware/skylog` completa ao transportar o projeto.

O aplicativo e a base implementam coleta e sincronização, com testes locais.
Além da placa e da caixa, faltam integrar ENS160+AHT21 e alinhar o firmware à
montagem prevista. Após concluir a variante escolhida, a placa será usinada em
CNC e montada. O conjunto completo deve ser validado antes de definir as
dimensões finais e imprimir a caixa.

## Organização técnica

| Pasta | Uso |
|---|---|
| `projetos/desenvolvimento/` | Variante `skylog.kicad-pedro_sch`, com as alterações locais de trabalho |
| `projetos/referencia-bicamada/` | Referência `skylog`, independente do desenvolvimento |
| `bibliotecas/` | Símbolos, footprints e modelos associados compartilhados |
| `modelos/` | Arquivos CAD originais de referência dos módulos |
| `originais/` | Fontes EasyEDA e procedência do conversor |
| `documentacao/` | Notas técnicas atuais do conversor e conector RS485 |
| `fabricacao/` | Exportações separadas das fontes de engenharia |

Para continuar as edições locais, abra
[skylog.kicad-pedro_sch.kicad_pro](projetos/desenvolvimento/skylog.kicad-pedro_sch.kicad_pro).
As duas variantes compartilham bibliotecas por caminhos relativos. Esquemas,
netlists, pads e trilhas não foram redesenhados nesta organização.
Backups, caches e ensaios antigos foram removidos a pedido; a relação está no
[relatório de organização](../../docs/organizacao/2026-10-10/relatorio.md).

## Variantes encontradas em 10/10/2026

| Arquivo PCB | Geometria e roteamento lidos no arquivo | Verificação |
|---|---|---|
| `skylog.kicad_pcb` | 86 × 111 mm; 135 segmentos F.Cu e 15 B.Cu; proposta bicamada | Relatórios históricos de 02/10/2026 |
| `skylog.kicad-pedro_sch.kicad_pcb` | Contorno de 100 × 100 mm com cantos arredondados; 75 segmentos B.Cu e nenhum F.Cu; footprint ESP32 `ESP32_NodeMCU_38Pin_4Furos_AConferir`; H2–H5 | Alterada localmente; sem novo ERC/DRC nesta revisão |

A contagem de segmentos não comprova continuidade nem ausência de curtos. O
`kicad-cli` disponível é 9.0.9 e falhou ao ler o esquema de formato 10.0; não
foi possível gerar nova exportação/ERC/DRC. Os nomes `AConferir` e os arquivos
Gerber existentes não constituem liberação para fabricação.

## Estado da proposta principal bicamada

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

Veja [as duas faces da PCB de referência](../../docs/versoes/2026-10-10-2cd5977-local/figuras/pcb-principal-2026-10-02.png) e
[o esquema de referência](../../docs/versoes/2026-10-10-2cd5977-local/figuras/esquema-principal-2026-10-02.png).

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

Os relatórios salvos em 02/10/2026 para a proposta principal registram KiCad
10.0.5 com:

- **ERC: zero erros e zero avisos.**
- **DRC: zero erros, zero avisos e zero conexões pendentes.**
- **Zero diferenças entre esquema e PCB.**
- **Zero trilhas GND**, com todos os 11 contatos de terra ligados ao plano.

Os 29 contatos externos intencionalmente livres têm marcadores NC. Três
PWR_FLAG documentam 5V externo, retorno GND e 3V3 do regulador integrado à
DevKitC. Os relatórios incluem listas `ignored_checks`; zero ocorrências significa
zero nas verificações habilitadas. Esta revisão não confirma a alegação anterior
de ausência de verificações desativadas.

Relatórios históricos da proposta principal: [ERC](../../docs/versoes/2026-10-10-2cd5977-local/evidencias/erc-historico-2026-10-02.json),
[DRC](../../docs/versoes/2026-10-10-2cd5977-local/evidencias/drc-historico-2026-10-02.json). O relatório intermediário de conectividade/geometria foi removido na limpeza.

## Preparação para usinagem CNC

As orientações de duas faces abaixo se aplicam à proposta principal. A variante
local com trilhas somente em B.Cu precisa de preparação própria; não reutilize
percursos da outra placa.

Conferir a pinagem, as dimensões e o encaixe dos módulos físicos. Em especial,
o footprint do conversor RS485 reproduz as ilhas da placa de referência;
a montagem direta e os conectores JST do exemplar real precisam ser validados.
Há também uma divergência de identificação do transceptor no projeto original,
descrita em [conversor-rs485.md](documentacao/conversor-rs485.md). A checagem do KiCad não
resolve essa diferença no hardware físico. Confirmar capacidade da fonte,
diâmetro das ferramentas, largura mínima das trilhas e folgas antes de gerar o
G-code.

Na proposta principal bicamada, a preparação da usinagem deve incluir pontos
de alinhamento reproduzíveis e a orientação correta da face inferior. A CNC
não metaliza os furos: vias e contatos que dependem de ligação entre as faces
precisam de rebites próprios, fios soldados ou outra solução definida antes da
montagem.

Gerar percursos separados para:

- isolamento das trilhas da face superior;
- isolamento das trilhas da face inferior;
- furação dos componentes, vias e pontos de alinhamento;
- recorte externo e recortes internos da placa.

Antes de montar os componentes, inspecionar visualmente a usinagem e medir
continuidade e possíveis curtos entre trilhas e planos de cobre.

Também é necessário conferir o espaço dos cabos e conectores, a posição das
aberturas da futura caixa, a ventilação dos sensores ambientais e a distância
da antena do ESP32 em relação à placa e ao gabinete. Os relatórios ERC/DRC
validam o projeto elétrico e geométrico, mas não substituem o teste da placa
fabricada com os módulos reais.

## Etapas para concluir a V1

- [ ] Encerrar a revisão do esquema e do leiaute da PCB.
- [ ] Conferir todos os módulos, footprints, conectores e tensões na bancada.
- [ ] Definir ferramentas, profundidades e origem; na bicamada, alinhar as duas faces.
- [ ] Gerar e revisar os percursos e o G-code de isolamento, furação e recorte.
- [ ] Usinar a placa na CNC e conferir continuidade, isolamento e dimensões.
- [ ] Montar a placa e validar alimentação, RS485, I²C, entrada analógica,
  Bluetooth e Wi-Fi com o conjunto completo de sensores.
- [ ] Medir a montagem final, projetar a caixa e imprimir o gabinete.

## Histórico documental

As imagens e evidências selecionadas da proposta bicamada permanecem na
[documentação versionada](../../docs/versoes/2026-10-10-2cd5977-local/documentacao-tecnica.md).
Os backups e estados de recuperação locais foram removidos na limpeza solicitada.
O histórico rastreado continua no Git; arquivos locais ignorados removidos não
são recuperáveis por Git. As duas variantes úteis de engenharia foram mantidas.

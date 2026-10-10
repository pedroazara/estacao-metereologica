# Skylog — Estação Meteorológica Móvel

O Skylog é uma estação portátil para coletar dados ambientais durante
percursos. O sistema combina sensores conectados a um ESP32 com os recursos do
telefone, registra cada coleta localmente e envia os resultados para uma base
com site de visualização e análise.

## Versão documentada

Estado local analisado em **10/10/2026**, baseado no commit
`2cd59777b90f2dd33dc27cc1e1f5b2505c5105bb`, com alterações locais anteriores.
A V1 é uma meta de montagem; o aplicativo declara `1.0.0+1`.
Consulte a [documentação técnica consolidada](docs/versoes/2026-10-10-2cd5977-local/documentacao-tecnica.md) e o
[inventário de READMEs](docs/versoes/2026-10-10-2cd5977-local/cobertura-readmes.md).

## Estado da versão 1

O **aplicativo**, o **site** e o **sistema de coleta e sincronização** têm
implementação e testes locais. A V1 ainda precisa de integração de software e
validação física, além de finalizar o hardware:

- integrar ENS160+AHT21 no firmware principal, contrato BLE, aplicativo e base;
- alinhar o firmware ao conversor automático e à retirada dos sensores antigos;
- concluir o projeto da placa de circuito impresso;
- usinar a placa na CNC e montar os componentes;
- validar o conjunto completo de sensores;
- projetar e imprimir a caixa.

O firmware atual ainda consulta WTGAHRS3, KY-013 e KY-038, e o aplicativo
oferece KY-013/KY-038 na seleção. O ENS160+AHT21 existe apenas em sketch de
teste; ainda não é transmitido pelo firmware principal nem selecionável no app.
O som do telefone fica no JSONL, mas ainda não alimenta a medida de ruído no mapa.

## Sensores definidos para a montagem V1

| Origem | Sensor ou recurso | Medições |
|---|---|---|
| Estação | Sensor ambiental RS485 | Temperatura e umidade |
| Estação | KY-028 | Temperatura auxiliar |
| Estação | ENS160 + AHT21 | eCO₂ estimado, TVOC, índice de qualidade do ar, temperatura e umidade de compensação |
| Estação | GY-302 / BH1750 | Luminosidade em lux |
| Telefone | GPS | Posição, percurso, velocidade e rumo |
| Telefone | Microfone | Nível sonoro, sem armazenar o áudio |

O ENS160 é um sensor de qualidade do ar. O valor de eCO₂ é uma estimativa
calculada a partir dos gases detectados, não uma medição direta da concentração
de dióxido de carbono. O AHT21 fornece temperatura e umidade para compensar as
leituras do ENS160.

## Funcionamento

```text
Sensor RS485 ────────────┐
KY-028 ──────────────────┤
ENS160 + AHT21 (previsto)┼── ESP32 ── Bluetooth ── aplicativo
GY-302 / BH1750 ─────────┘                           + GPS do telefone
                                                     + nível sonoro
                                                            │
                                                    coleta em JSONL
                                                            │
                                                sincronização pela rede
                                                            │
                                                            ▼
                                               base de processamento
                                                            │
                                                            ▼
                                                  site de visualização
```

O ESP32 lê RS485, KY-028 e BH1750 e transmite os dados ao aplicativo por
Bluetooth Low Energy. O aplicativo acrescenta a localização e o nível sonoro
obtidos pelo telefone, registra a coleta e mantém o arquivo salvo localmente.
Quando a base está acessível, a sessão pode ser sincronizada pela rede.

A base recebe os arquivos, preserva os dados coletados, processa as medições e
disponibiliza os resultados no site. A indisponibilidade da base durante o
trabalho de campo não interrompe a coleta no telefone. Entretanto, no firmware
atual o modo de configuração Wi-Fi anuncia BLE, mas retorna antes da aquisição
e publicação dos sensores. O funcionamento completo sem rede Wi-Fi no arranque
continua pendente.

## Divisão de responsabilidades

| Componente | Responsabilidade |
|---|---|
| ESP32 | Ler os sensores externos e transmitir as medições por Bluetooth |
| Aplicativo | Receber as leituras, coletar GPS e nível sonoro, gravar e sincronizar as sessões |
| Base | Armazenar e processar as coletas recebidas |
| Site | Exibir sessões, trajetos e medições |
| Placa de interface | Reunir ESP32, alimentação, conversor RS485 e conexões dos sensores |

## Hardware da estação

Os sensores ficam externos à placa de interface e são ligados por cabos:

- o sensor ambiental utiliza RS485 por meio de um conversor conectado à UART
  do ESP32;
- o KY-028 utiliza uma entrada analógica;
- o ENS160+AHT21 e o GY-302/BH1750 compartilham o barramento I²C;
- o ESP32 concentra as leituras e a comunicação Bluetooth com o telefone.

A placa está sendo projetada no KiCad e será fabricada por **usinagem CNC**.
Antes da usinagem serão conferidas a pinagem, as tensões, os conectores, as
dimensões dos módulos, a posição da antena do ESP32 e a compatibilidade das
trilhas e folgas com as ferramentas disponíveis. A caixa será projetada depois
da montagem definitiva, para que as aberturas, fixações e ventilação acompanhem
as dimensões reais.

## Formato e fluxo da coleta

As sessões são gravadas no telefone em JSONL, com um registro por linha. Esse
formato permite acrescentar medições continuamente e preserva as linhas já
gravadas mesmo se a coleta for interrompida.

O fluxo principal é:

1. o aplicativo conecta à estação por Bluetooth;
2. a pessoa seleciona os sensores e confere as leituras;
3. o aplicativo grava os dados ambientais, a posição e o nível sonoro;
4. a sessão permanece armazenada no telefone;
5. o arquivo é enviado para a base quando ela estiver disponível;
6. os dados processados podem ser consultados no site.

## Estrutura do projeto

| Pasta | Conteúdo |
|---|---|
| [`app/`](app/) | Aplicativo Flutter para Android |
| [`firmware/`](firmware/) | Firmware e testes dos sensores conectados ao ESP32 |
| [`hardware/skylog/`](hardware/skylog/) | Esquema, PCB e documentação da placa de interface |
| [`src/`](src/) | Servidor, processamento e site da base |
| [`docs/`](docs/) | Documentação técnica da arquitetura e da implantação |
| [`servidor/`](servidor/) | Arquivos de configuração da base |
| [`docs/organizacao/`](docs/organizacao/2026-10-10/relatorio.md) | Organização técnica, limpeza e rastreabilidade |
| [`ferramentas/cnc-jog/`](ferramentas/cnc-jog/LEIA-ME.txt) | Ferramenta local auxiliar da oficina, ainda não versionada no commit-base |

## Organização técnica

O [índice do hardware](hardware/README.md) aponta para a variante em desenvolvimento,
a referência bicamada, as bibliotecas e os arquivos de fabricação. Backups,
recuperações e testes obsoletos foram removidos conforme o
[relatório de limpeza](docs/organizacao/2026-10-10/relatorio.md).
O firmware, o app, a base e os dados mantêm suas pastas próprias.

## Documentação

- [Aplicativo Android](app/README.md)
- [Firmware e ligações dos sensores](firmware/README.md)
- [Arquitetura e telemetria](docs/04-telemetria.md)
- [Base de processamento](docs/05-base-raspberry-pi.md)
- [Placa de circuito impresso](hardware/skylog/README.md)

## Executar a base e conferir o software

Na raiz do repositório, `python3 src/servidor_telemetria.py --porta 8771` inicia
a base. Abra `http://localhost:8771` no navegador. Esse servidor usa a biblioteca
padrão do Python e SQLite; não precisa de Mosquitto. O mapa carrega Leaflet e
mapas online. Preparação do Android: [app/README.md](app/README.md).

Verificações locais: na pasta `app`, com dependências já disponíveis,
`flutter test --no-pub` e
`flutter analyze --no-pub`. Em 10/10/2026 passaram 7 testes Python e 66 Flutter;
a análise apontou uma ocorrência informativa de estilo preexistente. Os 7 testes
Python eram do filtro legado e foram removidos na limpeza posterior; os testes
do aplicativo permanecem. O painel CNC possui testes próprios em `ferramentas/`.

## Etapas para concluir a V1

- [ ] Integrar ENS160+AHT21 de ponta a ponta e retirar opções antigas da coleta V1.
- [ ] Resolver aquisição no modo de configuração Wi-Fi e alinhar `PINO_DE`.
- [ ] Integrar som do telefone ao processamento e à visualização da base.

- [ ] Finalizar o esquema e o leiaute da PCB.
- [ ] Conferir os módulos, footprints, conectores e tensões com os componentes
  reais.
- [ ] Definir ferramentas, profundidades e origem da variante escolhida; na
  bicamada, definir alinhamento entre faces.
- [ ] Gerar os percursos e o G-code de isolamento, furação e recorte.
- [ ] Usinar a placa na CNC e inspecionar continuidade, curtos e alinhamento.
- [ ] Montar e testar alimentação, RS485, I²C, entrada analógica, Bluetooth e
  Wi-Fi.
- [ ] Executar uma coleta completa com o hardware definitivo.
- [ ] Projetar e imprimir a caixa.

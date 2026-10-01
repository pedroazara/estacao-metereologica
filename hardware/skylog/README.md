# Skylog — projeto KiCad

## Abrir no Windows ou Linux

Use **KiCad 10.0.6 ou uma versão posterior compatível**. O projeto foi salvo
no formato do KiCad 10; o KiCad 9 não deve ser usado para editar esses arquivos.

1. Clone ou atualize o repositório e selecione a branch
   `wip/checkpoint-app-base`, que contém este estado do projeto.
2. Abra [skylog.kicad_pro](skylog.kicad_pro) pelo gerenciador de projetos do KiCad.
3. Abra o esquema `skylog.kicad_sch` e a placa `skylog.kicad_pcb` a partir dele.

As tabelas [sym-lib-table](sym-lib-table) e [fp-lib-table](fp-lib-table)
registram as bibliotecas dentro desta pasta, usando `${KIPRJMOD}`.
Não é necessário recriar os registros globais deste computador Linux.
O símbolo e o footprint do ESP32, os conectores dos sensores e seus modelos
3D ativos estão incluídos. Mantenha a pasta `hardware/skylog` completa.

`skylog-com-conversor-rs485.kicad_sch` e `modulo-rs485-ttl-v2.kicad_sch`
são referências da integração anterior; o esquema principal é `skylog.kicad_sch`.
Os arquivos em `originais/`, `documentacao/` e `modelos/` preservam fontes e
referências. Previews e relatórios antigos não certificam o estado atual da PCB.

## Montagem aprovada

Os sensores ficam externos à placa, representados apenas por conectores de
passo 2,54 mm. ESP32 e conversor RS485 ficam na placa de interface.

| Sensor externo | Contatos do conector | Ligação prevista no ESP32 |
|---|---|---|
| GY-302 / BH1750 | 1 ADDR, 2 SDA, 3 SCL, 4 GND, 5 VCC | ADDR em GND; SDA GPIO21; SCL GPIO22; VCC 3,3 V |
| KY-028 | 1 AO, 2 GND, 3 VCC, 4 DO | AO GPIO35; VCC 3,3 V; DO sem uso |
| Ambiental RS485 | 1 VIN, 2 GND, 3 A, 4 B | A/B pelo conversor; alimentação e terra comuns conforme revisão elétrica |

O conversor de referência recebe 5 V em CN1.4; CN1.3 é GND. A UART prevista
usa GPIO17 (TX do ESP32 → TXD/CN1.1 do conversor) e GPIO16 (RX do ESP32 ←
RXD/CN1.2 do conversor). Veja as limitações e a origem do módulo em
[conversor-rs485.md](conversor-rs485.md).

GPS, movimento e som serão fornecidos pelo telefone. WTGAHRS3-485, KY-038 e
KY-013 não integram a nova montagem. A migração completa do firmware e do
aplicativo continua pendente, conforme o [README principal](../../README.md).

## Retomar o roteamento

O roteamento foi pausado a pedido do usuário em 01/10/2026 para continuar no
Windows. A preparação para transferência alterou apenas dependências e
caminhos de bibliotecas/modelos; não reorganizou componentes nem trilhas.

- Concentrar os três conectores dos sensores em uma única borda.
- Tentar rotear em uma camada de cobre; a viabilidade ainda precisa ser validada.
- Definir o contorno e as dimensões da placa; não há medida de caixa confirmada.
- Revisar as conexões antes de rotear. Na última verificação, o esquema já
  ligava AO do KY-028 somente ao GPIO35, mas a PCB ainda continha a rede antiga
  unindo GPIO35, GPIO36 e GPIO39. Sincronizar a PCB com o esquema.
- Ajustar a UART: o esquema salvo ainda usava GPIO1/3, enquanto a ligação
  prevista no firmware/documentação usa GPIO17/16.
- Ligar ADDR do GY-302 a GND para fixar o endereço previsto 0x23.
- Conferir a pinagem real dos módulos e executar ERC/DRC após as correções.

A placa está em desenvolvimento e ainda não está liberada para fabricação.
Arquivos de trava, preferências locais, caches, históricos e backups não são
versionados; permanecem no computador de origem.

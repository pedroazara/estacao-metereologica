# Cobertura da revisão de READMEs — 10/10/2026

> Inventário da análise inicial, anterior à limpeza. Caminhos removidos e a
> cobertura atual constam do [relatório de organização](../../organizacao/2026-10-10/relatorio.md).

Inventário recursivo, incluindo arquivos ocultos e ignorados, nomes sem
distinção de maiúsculas/minúsculas e variantes de extensão. Também foram
incluídos `antes-README.md`, nomes com README no meio e os avisos `LEIA-ME`/`LEIA_`.
Metadados, dependências e snapshots foram inventariados e classificados abaixo,
sem serem tratados como documentação autoral ativa. Arquivos dentro de ZIPs
não foram extraídos nem sincronizados; os backups permanecem históricos.
Não foi encontrado arquivo AGENTS.md nos caminhos consultados. Não foi encontrado
submódulo Git registrado; os repositórios em `.history` são históricos do KiCad.
`cnc-jog/` é um componente auxiliar local não rastreado no commit-base, incluído
na revisão de seu guia, sem operar a CNC.

**Total: 31 arquivos — 5 atualizados, 2 revisados sem alteração e 24 excluídos.**

| Caminho a partir da raiz | Situação | Motivo/resultado |
|---|---|---|
| `.venv/lib/python3.13/site-packages/fontTools/ttLib/tables/table_API_readme.txt` | Excluído | Dependência/ambiente virtual; não é documentação autoral. |
| `.venv/lib/python3.13/site-packages/matplotlib/mpl-data/fonts/pdfcorefonts/readme.txt` | Excluído | Dependência/ambiente virtual; não é documentação autoral. |
| `.venv/lib/python3.13/site-packages/matplotlib/mpl-data/sample_data/README.txt` | Excluído | Dependência/ambiente virtual; não é documentação autoral. |
| `.venv/lib/python3.13/site-packages/numpy/_core/tests/data/umath-validation-set-README.txt` | Excluído | Dependência/ambiente virtual; não é documentação autoral. |
| `.venv/lib/python3.13/site-packages/numpy/ma/README.rst` | Excluído | Dependência/ambiente virtual; não é documentação autoral. |
| `README.md` | Atualizado | Conteúdo conferido e alinhado ao estado local; informações históricas preservadas. |
| `WitStandardModbus_WT901C485/Arduino/README.md` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `WitStandardModbus_WT901C485/C++/README.md` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `WitStandardModbus_WT901C485/ESP32/ESP32_Arduino/README.md` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `WitStandardModbus_WT901C485/ESP32/README.md` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `WitStandardModbus_WT901C485/Linux_C/README.txt` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `WitStandardModbus_WT901C485/Matlab/README.md` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `WitStandardModbus_WT901C485/Python/Python-SDK-WT901C485/chs/README.txt` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `WitStandardModbus_WT901C485/Python/README.md` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `WitStandardModbus_WT901C485/README.md` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `WitStandardModbus_WT901C485/ROS/README.md` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `WitStandardModbus_WT901C485/STM32F1/README.md` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `WitStandardModbus_WT901C485/Windows_C#/README.md` | Excluído | SDK externo WitMotion preservado; exemplos de fabricante, fora da revisão autoral. |
| `app/README.md` | Atualizado | Conteúdo conferido e alinhado ao estado local; informações históricas preservadas. |
| `cnc-jog/LEIA-ME.txt` | Revisado sem alteração | Guia da ferramenta auxiliar de oficina, conferido com servidor e simulador; não faz parte da coleta. Comissionamento físico continua pendente. |
| `cnc-jog/exemplos/LEIA_ANTES_DO_RASCUNHO.txt` | Revisado sem alteração | Aviso específico de rascunho lido e preservado; não foi tratado como instrução de fabricação da PCB. |
| `firmware/README.md` | Atualizado | Conteúdo conferido e alinhado ao estado local; informações históricas preservadas. |
| `hardware/recuperacao/antes-reparo-simbolo-20260925-145119/.history/.git/hooks/README.sample` | Excluído | Metadados internos de controle de versão. |
| `hardware/recuperacao/antes-reparo-simbolo-20260925-145119/.history/README.txt` | Excluído | Snapshot automático KiCad; histórico de recuperação, não documentação ativa. |
| `hardware/recuperacao/antes-reparo-simbolo-20260925-145119/LEIA-ME.txt` | Excluído | Cópia histórica anterior a alterações; preservada como evidência, não sincronizada ao presente. |
| `hardware/skylog/.history/.git/hooks/README.sample` | Excluído | Metadados internos de controle de versão. |
| `hardware/skylog/.history/README.txt` | Excluído | Snapshot automático KiCad; histórico de recuperação, não documentação ativa. |
| `hardware/skylog/README.md` | Atualizado | Conteúdo conferido e alinhado ao estado local; informações históricas preservadas. |
| `hardware/skylog/bibliotecas/Sensores_Modulos/originais/gy302/readme.md` | Excluído | README original de biblioteca de terceiros; autoria/licença preservadas. |
| `hardware/skylog/documentacao/ens160-aht21-2026-10-02/antes-README.md` | Excluído | Cópia histórica anterior a alterações; preservada como evidência, não sincronizada ao presente. |
| `hardware/skylog/documentacao/erc-anotacoes-2026-10-02/README.md` | Atualizado | Conteúdo conferido e alinhado ao estado local; informações históricas preservadas. |

A revisão sem alteração indica leitura/conferência, não execução dos procedimentos.
Os READMEs externos excluídos não foram auditados quanto à correção técnica.
As cinco alterações documentais desta revisão se somam às edições locais já
existentes; nenhum texto anterior foi descartado por redefinição da árvore Git.

Correções relacionadas fora do inventário: `docs/04-telemetria.md`,
`docs/05-base-raspberry-pi.md` e `hardware/skylog/conversor-rs485.md`, para evitar
instruções contraditórias e links a arquivos ausentes. Fontes, testes e limites
estão no [documento consolidado](documentacao-tecnica.md).

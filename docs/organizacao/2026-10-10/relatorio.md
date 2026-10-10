# Organização técnica e limpeza — 10/10/2026

O projeto foi organizado após a documentação do estado local baseado no commit
`2cd59777b90f2dd33dc27cc1e1f5b2505c5105bb`. As alterações locais anteriores
foram incorporadas aos movimentos, sem restaurar versões antigas do Git.
A pedido do usuário, históricos, recuperações e ensaios obsoletos foram removidos.

## Estrutura de trabalho

```text
app/                              Aplicativo e testes atuais
firmware/                         Firmware principal e diagnósticos dos sensores V1
src/                              Base HTTP/SQLite e utilitários preservados
servidor/                         Configurações de implantação
bin/                              Atalhos existentes
data/                             Medições e banco existentes, preservados
docs/                             Documentação e registros da organização
hardware/skylog/
  projetos/desenvolvimento/       Variante local skylog.kicad-pedro_sch
  projetos/referencia-bicamada/   Proposta skylog de referência
  bibliotecas/                   Símbolos, footprints e modelos associados
  modelos/                       Fontes CAD dos módulos
  originais/                     Fontes EasyEDA do conversor
  documentacao/                  Notas técnicas atuais
  fabricacao/                    Exportações não validadas e arte de serigrafia
ferramentas/cnc-jog/              Painel da oficina e testes atuais
```

Para trabalhar na placa local, abrir
[skylog.kicad-pedro_sch.kicad_pro](../../../hardware/skylog/projetos/desenvolvimento/skylog.kicad-pedro_sch.kicad_pro).
Para consultar a outra proposta, abrir
[skylog.kicad_pro](../../../hardware/skylog/projetos/referencia-bicamada/skylog.kicad_pro).
Os nomes de arquivos foram preservados; a separação das pastas identifica suas
funções. “Desenvolvimento” não significa aprovação elétrica ou para fabricação.

## Movimentos e preservação

Foram planejados/executados 42 movimentos, com 1.046 arquivos inventariados
antes da limpeza posterior. [movimentos.json](movimentos.json) registra os
caminhos anteriores, destinos e hashes SHA-256. Nem todos os destinos permanecem:
o SDK e o histórico foram removidos em seguida, conforme a nova instrução.

Os esquemas e arquivos `.kicad_pro` foram movidos sem alterar seu conteúdo.
Nas PCBs e em quatro footprints locais, a única edição foi trocar o prefixo dos
caminhos de modelos 3D para a nova posição relativa. Não houve alteração de
trilhas, pads, pinos, redes, componentes ou geometria. Cada projeto recebeu
suas tabelas de bibliotecas, mantendo os nomes `skylog`, `Espressif`, `ESP32`,
`Sensores_Modulos` e a referência padrão `power`.

Gerbers, furações, CSV e STEP foram apenas movidos. O requisito de matplotlib foi retirado após a remoção dos gráficos/exercícios
que o usavam; o comentário de paho-mqtt foi alinhado ao monitor legado. Não
foram instalados ou desinstalados pacotes do ambiente.

As entradas do Gerber Job
continuam apontando aos mesmos arquivos. Nada foi regenerado ou aprovado para
fabricação. Bibliotecas originais/licenças, arquivos CAD de referência e dados
de medição foram mantidos; não foram tratados como ensaios descartáveis.

## Limpeza solicitada

Foram removidos **16 conjuntos, totalizando 1.038 arquivos e 78.408.444 bytes
(aproximadamente 75 MiB)**. A relação com justificativas e hashes está em
[remocoes.json](remocoes.json).

- SDK de exemplos WitMotion antigo.
- Backups, recuperações, histórico automático e estados/caches do KiCad.
- Relatórios e prévias intermediárias de revisões/importação de bibliotecas.
- Firmware antigo WTGAHRS3, bancada conjunta, testes KY-013/KY-038 e pasta
  `teste_gy302` que continha teste de motor.
- Exercícios `src/kalman`, testes do filtro legado e seis scripts antigos de
  simulação, calibração/ensaio de módulos retirados.

Foram mantidos os testes do app e do painel CNC e os diagnósticos de ENS160+AHT21,
KY-028, sensor ambiental e varredura de registradores. Os drivers WTG ainda usados
pelo firmware principal permanecem até a alteração funcional desse firmware.
Os utilitários restantes em `src` têm dependências entre si e atalhos em `bin`;
esta limpeza não mudou seu funcionamento. Cópias documentais selecionadas de
esquemas, imagens e evidências permanecem na versão técnica, não como projetos
ativos concorrentes.

Arquivos rastreados removidos continuam recuperáveis pelo histórico Git.
Backups e estados locais ignorados removidos não têm recuperação garantida.
Não foi criada outra pasta escondida contendo o material descartado.

## Verificações

- Comparação de hashes e conteúdo normalizado dos arquivos de engenharia, com
  diferenças restritas aos caminhos dos modelos 3D.
- Verificação das tabelas de bibliotecas, footprints usados, modelos locais,
  referências do Gerber Job e links Markdown: executar da raiz
  `python3 ferramentas/verificar_estrutura.py`.
- Painel CNC após mudança: **59 testes Python e 8 testes de interface passaram**,
  com simulação/portas temporárias, sem acesso à CNC física.
- Aplicativo: **66 testes passaram** durante a etapa documental; seu código não
  foi modificado pela organização. A análise do app apontou uma ocorrência
  informativa preexistente de estilo.
- Base: envio HTTP sintético conferido na etapa documental; seus três módulos
  ativos permaneceram idênticos. Nenhum dado real foi modificado.
- KiCad: validação estrutural dos caminhos, sem novo ERC/DRC. O CLI disponível
  é 9.0.9 e não abriu o esquema de formato 10.0. Abrir/revalidar em KiCad 10
  continua necessário para a checagem elétrica completa.

Resultados locais: [estrutura](verificacao-estrutura.txt),
[integridade](verificacao-integridade.txt),
[testes CNC Python](testes-cnc-python.txt) e
[testes CNC interface](testes-cnc-interface.txt).

## Cobertura documental após a limpeza

O inventário inicial de 31 arquivos permanece como registro da etapa anterior.
A relação atual cobre 13 arquivos: 10 criados/atualizados, 2 revisados sem
alteração e 1 original de terceiro excluído, conforme
[inventario-readmes.md](inventario-readmes.md).
Os READMEs do app/firmware foram preservados e sincronizados; o índice de hardware
agora prioriza os arquivos de engenharia corretos. Novos índices existem somente
para áreas com propósito distinto: hardware, bibliotecas, fabricação, ferramentas,
documentação e base Python. As notas técnicas antigas mantidas estão identificadas
como históricas, inclusive quando mencionam procedimentos já removidos.

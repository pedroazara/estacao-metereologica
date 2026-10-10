# Arquivos de fabricação

| Pasta | Conteúdo | Situação |
|---|---|---|
| `exportacoes-nao-validadas/pedro/` | Gerbers B.Cu, Edge.Cuts e F.Silkscreen; furação PTH/NPTH; Gerber Job, CSV e STEP | Exportações locais recebidas; correspondência com a PCB atual não revalidada |
| `serigrafia/` | Arte SkyLog em SVG editável e PNG | Recurso gráfico; não substitui a camada de fabricação da PCB |

Fonte de desenvolvimento:
[skylog.kicad-pedro_sch.kicad_pro](../projetos/desenvolvimento/skylog.kicad-pedro_sch.kicad_pro).
Os nomes dos arquivos foram preservados para manter as referências do Gerber Job.
Não houve regeneração nem alteração de Gerber, furação, CSV ou STEP.

Antes de fabricar, conferir a variante, os componentes reais, ERC/DRC,
correspondência esquema/PCB e exportações geradas da mesma revisão. Registrar
commit/estado local, versão do KiCad e parâmetros da exportação. Arquivos aprovados
podem formar uma entrega identificada por revisão; não há entrega aprovada nesta
pasta. Os exemplos do painel CNC são exercícios de oficina, não percursos da PCB.

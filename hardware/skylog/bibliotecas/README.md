# Bibliotecas locais do hardware

| Diretório | Conteúdo | Nome no KiCad |
|---|---|---|
| `skylog/` | Símbolos e footprints específicos do projeto, conversor e conectores | `skylog` |
| `ESP32/` | Símbolo DevKitC e footprints das variantes de ESP32 | `Espressif`, `ESP32` |
| `Sensores_Modulos/` | Interfaces KY-028/BH1750, modelos 3D e fontes de origem | `Sensores_Modulos` |

Cada projeto em `../projetos/` contém suas tabelas `sym-lib-table` e
`fp-lib-table`. Os caminhos usam `${KIPRJMOD}/../../bibliotecas/`, preservando
os nomes lógicos já usados nos esquemas e PCBs. Os modelos 3D dos conectores
seguem a mesma convenção; não dependem do caminho pessoal deste computador.

`power` depende da biblioteca padrão do KiCad 10 (`KICAD10_SYMBOL_DIR`).
Os arquivos em `originais/` dentro da biblioteca de sensores são fontes de
procedência e licença, não bibliotecas ativas nem modelos prontos para inserir.
Seus caminhos históricos não definem a configuração dos projetos atuais.

O footprint `ESP32_NodeMCU_38Pin_4Furos_AConferir` continua exigindo conferência
física. A reorganização alterou apenas caminhos dos modelos, sem mudar pads,
pinos, dimensões, posições ou geometria.

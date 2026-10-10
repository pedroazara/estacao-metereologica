# Ferramentas auxiliares

[cnc-jog/LEIA-ME.txt](cnc-jog/LEIA-ME.txt) descreve o painel local da oficina.
Para iniciar, execute `./ferramentas/cnc-jog/iniciar.sh` a partir da raiz do
repositório; o script resolve sua própria pasta. O painel atende em
`http://127.0.0.1:8765` e não inicia movimentos automaticamente.

Os testes usam simulação, sem CNC física. Na pasta `cnc-jog`:

```bash
python3 -m unittest -v
node --test test_controls.cjs
```

O guia local registra os limites operacionais e o comissionamento pendente.
Esta ferramenta não gera os percursos de fabricação da PCB.

Verificação da organização, sem hardware: na raiz,
`python3 ferramentas/verificar_estrutura.py`. Confere links locais, bibliotecas,
footprints, modelos 3D e referências do Gerber Job; não substitui ERC/DRC.

#!/usr/bin/env python3
"""Confere caminhos locais, bibliotecas e modelos; não executa ERC/DRC."""
from pathlib import Path
import re
import sys
from urllib.parse import unquote

RAIZ = Path(__file__).resolve().parents[1]
ERROS = []
contagens = {'links': 0, 'bibliotecas': 0, 'modelos': 0, 'footprints': 0}


def conferir(caminho, contexto):
    if not caminho.exists():
        ERROS.append(f'{contexto}: {caminho.relative_to(RAIZ) if caminho.is_relative_to(RAIZ) else caminho}')


for projeto in (RAIZ / 'hardware/skylog/projetos').iterdir():
    if not projeto.is_dir():
        continue
    libs = {}
    for nome in ('sym-lib-table', 'fp-lib-table'):
        tabela = projeto / nome
        conferir(tabela, 'Tabela ausente')
        if not tabela.exists():
            continue
        for lib, uri in re.findall(r'\(lib\s+\(name "([^"]+)"\).*?\(uri "([^"]+)"\)', tabela.read_text(), re.S):
            if uri.startswith('${KIPRJMOD}'):
                destino = Path(uri.replace('${KIPRJMOD}', str(projeto))).resolve()
                conferir(destino, f'Biblioteca {lib}')
                contagens['bibliotecas'] += 1
                if nome == 'fp-lib-table':
                    libs[lib] = destino
    for pcb in projeto.glob('*.kicad_pcb'):
        for footprint in re.findall(r'\(footprint "([^"]+)"', pcb.read_text()):
            lib, _, nome = footprint.partition(':')
            if lib in libs:
                conferir(libs[lib] / (nome + '.kicad_mod'), f'Footprint de {pcb.name}')
                contagens['footprints'] += 1
        for modelo in re.findall(r'\(model "([^"]+)"', pcb.read_text()):
            if modelo.startswith('${KIPRJMOD}'):
                conferir(Path(modelo.replace('${KIPRJMOD}', str(projeto))).resolve(), f'Modelo de {pcb.name}')
                contagens['modelos'] += 1
    for lib in libs.values():
        for fp in lib.glob('*.kicad_mod'):
            for modelo in re.findall(r'\(model "([^"]+)"', fp.read_text()):
                if modelo.startswith('${KIPRJMOD}'):
                    conferir(Path(modelo.replace('${KIPRJMOD}', str(projeto))).resolve(), f'Modelo de {fp.name}')
                    contagens['modelos'] += 1

# Bibliotecas padrão do KiCad dependem da instalação KiCad 10 e não são locais.
for job in (RAIZ / 'hardware/skylog/fabricacao').rglob('*.gbrjob'):
    import json
    for arquivo in json.loads(job.read_text()).get('FilesAttributes', []):
        conferir(job.parent / arquivo['Path'], 'Gerber referenciado pelo Job')

for doc in RAIZ.rglob('*.md'):
    if any(x in doc.parts for x in ('.git', '.venv', '.dart_tool', 'build', '.gradle', 'originais')):
        continue
    texto = re.sub(r'```.*?```', '', doc.read_text(), flags=re.S)
    for url in re.findall(r'\]\(([^\s)]+)\)', texto):
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', url) or url.startswith('#'):
            continue
        caminho = unquote(url.split('#')[0])
        conferir((doc.parent / caminho).resolve(), f'Link em {doc.relative_to(RAIZ)}')
        contagens['links'] += 1

for erro in ERROS:
    print('ERRO:', erro)
print('Contagens:', contagens)
print('Resultado:', 'FALHOU' if ERROS else 'OK')
print('Limites: não valida eletricamente a placa, bibliotecas padrão ou URLs externas.')
sys.exit(bool(ERROS))

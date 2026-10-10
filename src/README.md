# Base e utilitários Python

O caminho atual da coleta utiliza três módulos:

| Módulo | Responsabilidade |
|---|---|
| `servidor_telemetria.py` | HTTP, recepção de sessões e site |
| `processar_sessao.py` | JSONL para SQLite, GPS bruto associado ao último ambiente |
| `planejamento.py` | Planos e cruzamento espacial com medições |

Na raiz do projeto: `python3 src/servidor_telemetria.py --porta 8771`.
A base atual não depende de MQTT nem executa Kalman; detalhes e limitações estão
no [documento técnico](../docs/versoes/2026-10-10-2cd5977-local/documentacao-tecnica.md).
`calibrar_ky028.py` atende a temperatura auxiliar da montagem prevista.

Os demais utilitários WTG/painéis antigos e `filtro_kalman.py` permanecem por
suas dependências entre módulos e atalhos em `bin/`; não são o servidor atual.
A limpeza removeu exercícios e testes obsoletos, sem desmontar esses utilitários
ou alterar o comportamento do servidor. Dados de medição foram preservados.

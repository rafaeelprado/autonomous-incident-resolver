# AIR — Autonomous Incident Resolver

Sistema multi-agente (Python + CrewAI + Claude) que recebe um log de erro de produção, identifica a causa raiz, audita o código afetado e gera patch + relatório de incidente em Markdown.

```
logs/error.log ──► LogAnalyst ──► CodeAuditor ──► PatchEngineer ──► incidente_resolvido.md
                   (LogAnalysis)   (CodeAudit)     (Markdown)         runs/<timestamp>.json
```

## Destaques de engenharia

- **Structured outputs (Pydantic)** entre agentes: contratos tipados em vez de texto livre.
- **Tool com sandbox**: leitura de arquivos restrita à raiz do projeto, com linhas numeradas para citações precisas.
- **Testes como oráculo**: `tests/test_app.py` reproduz o incidente (red) e define quando um patch é válido (green).
- **Rastreabilidade**: cada execução salva análise estruturada e consumo de tokens em `runs/`.

## Como rodar

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # preencha ANTHROPIC_API_KEY

pytest -q          # 3 testes falham — o bug está reproduzido
python main.py     # gera incidente_resolvido.md e runs/<timestamp>.json
```

## Resultado de uma execução real

Incidente: `POST /orders` retornando HTTP 500 (`TypeError` em `apply_coupon`), alerta de SLO com 5xx em 18,4% (limite 1%).

| Etapa | Resultado |
|---|---|
| LogAnalyst | Localizou `src/app.py:42`, função `apply_coupon`, 2 requisições afetadas, severidade SEV2 |
| CodeAuditor | Confirmou a hipótese e achou um **defeito que não estava no log**: cupom desativado (30% off) seria aceito |
| PatchEngineer | Patch com guardas para cupom ausente, inexistente e inativo (HTTP 422 em vez de 500) + postmortem com 5 porquês |
| Testes | Antes: **3 falham / 1 passa**. Depois do patch: **4/4 passam** |
| Custo | `claude-sonnet-5`, ~135 mil tokens, 24 chamadas à API |

Artefatos em [`examples/`](examples/): [relatório de incidente](examples/incidente_resolvido.md), [JSON da execução](examples/run_20260917-100508.json) e [código corrigido](examples/app_corrigido.py).

## Estrutura

```
├── logs/error.log     # stack trace realista (FastAPI/uvicorn)
├── src/app.py         # Order Service com o bug
├── tests/test_app.py  # testes de contrato/regressão
├── main.py            # orquestração CrewAI
├── examples/          # saída real de uma execução (relatório, JSON, patch)
└── requirements.txt
```

## Roadmap

- [ ] Agente **Verifier**: aplica o patch em sandbox e roda `pytest` (loop de autocorreção)
- [ ] Múltiplos cenários de incidente + avaliação (taxa de patches que passam nos testes, custo por incidente)
- [ ] Observabilidade de agentes (tracing) e GitHub Action que abre PR com o patch

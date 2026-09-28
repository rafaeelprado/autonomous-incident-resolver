# 🤖 AIR — Autonomous Incident Resolver

🇺🇸 [English](README.md) · 🇧🇷 **Português**

**Sistema multi-agente que lê um log de erro de produção, encontra a causa raiz, audita o código-fonte e entrega um patch testado, sem triagem humana.**

Construído com **CrewAI** + **Claude (Anthropic)**, usando saídas estruturadas (Pydantic) como contratos rígidos entre os agentes, uma ferramenta de leitura de arquivos com sandbox e uma suíte pytest que funciona como a *verdade de referência* do que significa "corrigido".

```
logs/error.log ──▶ LogAnalyst ──▶ CodeAuditor ──▶ PatchEngineer ──▶ incidente_resolvido.md
                   (causa raiz)   (confirma e      (patch + testes +    runs/<ts>.json
                                   acha defeitos    postmortem)         (rastro estruturado)
                                   latentes)
```

---

## Por que isso existe

A maioria dos projetos de portfólio com agentes de IA é um chatbot em volta de um prompt. **O AIR é um pipeline que faz trabalho real de SRE**: recebe um log bruto de produção, reconstrói a falha, lê o arquivo-fonte de verdade e produz um patch que um humano pode revisar e aprovar, junto com os testes de regressão que provam que ele funciona.

Este repositório é exatamente o incidente que ele resolveu, mantido como demo viva:

- Um serviço de checkout em FastAPI (`src/app.py`) com um **bug de produção realista**: um crash `TypeError` quando o código do cupom está ausente ou é inválido.
- Um **log com stack trace de múltiplos frames** (`logs/error.log`), exatamente como apareceria no `uvicorn`/`FastAPI` em produção, incluindo um alerta de violação de SLO.
- O **relatório de incidente completo gerado pela IA** ([`examples/incidente_resolvido.md`](examples/incidente_resolvido.md)), com análise de causa raiz (5 porquês), patch em diff unificado e suíte pytest, gerado de ponta a ponta pelos três agentes abaixo, sem edição manual.

---

## Os agentes

| Agente | Papel | Saída |
|---|---|---|
| **LogAnalyst** | Especialista em triagem SRE. Analisa o log, descarta os frames de bibliotecas, isola o frame mais profundo da aplicação, correlaciona `request_id`s e payloads para achar o gatilho e classifica a severidade usando os alertas de SLO. | `LogAnalysis` (Pydantic) |
| **CodeAuditor** | Revisor sênior de Python/FastAPI. Lê o arquivo apontado, confirma ou refuta a hipótese a partir de primeiros princípios e procura **defeitos latentes** no mesmo caminho de código, não só o crash reportado. | `CodeAudit` (Pydantic) |
| **PatchEngineer** | Responsável pela correção. Transforma a auditoria num patch mínimo e seguro: erros de regra de negócio viram `HTTPException` (4xx), nunca 500; o contrato de sucesso fica intacto; os testes de regressão vão junto com o patch. | Relatório de incidente em Markdown |

Toda passagem entre agentes é um objeto Pydantic tipado, não texto livre. Assim, uma leitura errada do LogAnalyst não corrompe silenciosamente o raciocínio do CodeAuditor.

---

## O que ele realmente encontrou

Rodando a crew sobre `logs/error.log`, o AIR:

1. Isolou o crash em `src/app.py:42`: `coupon["percent"]` aplicado a um cupom `None`.
2. Correlacionou dois `request_id`s diferentes a dois gatilhos diferentes: um cupom ausente (`coupon_code=None`) e um inexistente (`"BEMVINDO5"`), ambos batendo na mesma linha sem proteção.
3. **Encontrou um bug que nunca apareceu nos logs**: `BLACKFRIDAY` é um cupom real marcado como `active: False`, mas o código nunca checava essa flag. Ele aplicaria 30% de desconto silenciosamente se fosse usado.
4. Entregou um patch que trata "sem cupom" como caminho de sucesso válido e cupons "inexistente" e "inativo" como `422`, com uma suíte pytest provando os quatro cenários (antes: 3 falham / 1 passa → depois: 4/4 passam).

Raciocínio completo, causa raiz com 5 porquês, diff e testes: [`examples/incidente_resolvido.md`](examples/incidente_resolvido.md).

Artefatos da execução em [`examples/`](examples/): [relatório de incidente](examples/incidente_resolvido.md) · [JSON estruturado da execução](examples/run_20260917-100508.json) · [código corrigido](examples/app_corrigido.py)

**Custo medido dessa execução:** `claude-sonnet-5`, ~135 mil tokens, 24 chamadas à API.

---

## Destaques de engenharia

- **Saídas estruturadas como contratos**: `LogAnalysis` e `CodeAudit` são modelos Pydantic, não prosa. Os agentes seguintes recebem dados tipados e validados em vez de interpretar texto livre.
- **Acesso a ferramentas com sandbox**: a `SandboxedFileReadTool` resolve caminhos a partir da raiz do projeto e recusa qualquer coisa fora dela (nada de `../../etc/passwd`). Ela devolve o conteúdo com linhas numeradas, para que os agentes citem linhas exatas em vez de chutar.
- **Testes como oráculo**: `tests/test_app.py` define o que é "correto" *antes* de a correção existir. Um patch só é válido quando a suíte fica verde, a mesma ideia de teste de contrato usada para barrar o CI em resposta a incidentes reais.
- **Rastreabilidade completa**: cada execução grava um artefato JSON estruturado (`runs/<timestamp>.json`) com a análise tipada e o consumo de tokens. As execuções ficam auditáveis e fáceis de avaliar depois (taxa de sucesso, custo por incidente etc.).

---

## Rode você mesmo

```bash
git clone https://github.com/rafaeelprado/autonomous-incident-resolver.git
cd autonomous-incident-resolver

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env    # adicione sua ANTHROPIC_API_KEY

pytest -q               # 3 testes falham: o incidente está reproduzido
python main.py          # roda a crew de 3 agentes e gera incidente_resolvido.md
```

O modelo é definido por `AIR_MODEL` no `.env` (padrão `anthropic/claude-sonnet-5`). Um modelo menor, como o Haiku, reduz o custo; os resultados com ele ainda não foram medidos.

---

## Stack

`CrewAI` · `Claude (Anthropic API)` · `Pydantic v2` · `FastAPI` · `pytest` · `python-dotenv`

## Roadmap

- [ ] **Agente Verifier**: aplica o próprio patch num sandbox e roda o pytest de novo, num loop de autocorreção
- [ ] Benchmark com múltiplos incidentes (taxa de patches aprovados vs. custo por incidente)
- [ ] GitHub Action que abre um PR com o patch gerado automaticamente

---

*Criado por [Rafael Prado](https://github.com/rafaeelprado) como parte de um portfólio para vagas de AI Engineering, com foco em orquestração multi-agente, raciocínio estruturado e código em que um engenheiro possa confiar de verdade.*

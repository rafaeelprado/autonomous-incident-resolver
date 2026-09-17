"""
AIR — Autonomous Incident Resolver
Pipeline multi-agente (CrewAI) que transforma um log de erro em diagnóstico + patch.

Fluxo sequencial:
    LogAnalyst  ->  CodeAuditor  ->  PatchEngineer  ->  incidente_resolvido.md

Uso:
    python main.py
    python main.py --log logs/error.log --source src/app.py --output incidente_resolvido.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal, Type

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tools import BaseTool
from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
class ReadFileInput(BaseModel):
    path: str = Field(description="Caminho relativo à raiz do projeto, ex: 'src/app.py'")


class SandboxedFileReadTool(BaseTool):
    """Lê arquivos SOMENTE dentro do projeto e devolve o conteúdo numerado.

    - Sandbox: bloqueia path traversal (../../etc/passwd) — agentes nunca devem
      ter acesso irrestrito ao filesystem.
    - Numeração de linhas: permite ao LLM citar linhas exatas em vez de "chutar".
    """

    name: str = "read_project_file"
    description: str = (
        "Lê um arquivo de texto do projeto e retorna o conteúdo com números de linha. "
        "Use caminhos relativos à raiz do projeto (ex: 'logs/error.log', 'src/app.py')."
    )
    args_schema: Type[BaseModel] = ReadFileInput
    max_chars: int = 60_000

    def _run(self, path: str) -> str:
        target = (PROJECT_ROOT / path).resolve()
        if not target.is_relative_to(PROJECT_ROOT):
            return f"ERRO: acesso negado fora do projeto: {path}"
        if not target.is_file():
            return f"ERRO: arquivo não encontrado: {path}"
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        numbered = "\n".join(f"{i:>4} | {line}" for i, line in enumerate(lines, start=1))
        return numbered[: self.max_chars]


# --------------------------------------------------------------------------- #
# Contratos de saída (structured outputs)
# --------------------------------------------------------------------------- #
class LogAnalysis(BaseModel):
    exception_type: str = Field(description="Ex: TypeError, KeyError")
    exception_message: str
    file_path: str = Field(description="Arquivo da aplicação (não de libs) onde o erro ocorreu")
    line_number: int
    function_name: str
    failing_expression: str = Field(description="Trecho exato de código que lançou a exceção")
    call_chain: list[str] = Field(description="Frames da aplicação, do endpoint até a falha")
    trigger_conditions: list[str] = Field(description="Payloads/condições que dispararam o erro (com request_ids)")
    occurrences: int
    severity: Literal["SEV1", "SEV2", "SEV3", "SEV4"]
    impact_summary: str
    root_cause_hypothesis: str


class CodeAudit(BaseModel):
    confirmed_root_cause: str
    faulty_code_snippet: str
    why_it_failed: str = Field(description="Explicação de primeiros princípios da falha")
    hypothesis_confirmed: bool = Field(description="O código confirma a hipótese do LogAnalyst?")
    additional_defects: list[str] = Field(description="Outros bugs/riscos latentes no mesmo fluxo")
    fix_requirements: list[str] = Field(description="Critérios que um patch correto precisa atender")


# --------------------------------------------------------------------------- #
# Crew
# --------------------------------------------------------------------------- #
def build_crew(log_path: str, source_path: str, llm: LLM) -> Crew:
    read_tool = SandboxedFileReadTool()

    log_analyst = Agent(
        role="LogAnalyst — SRE especialista em triagem de incidentes",
        goal="Extrair do log a causa raiz provável, o arquivo e a linha exatos da falha e o impacto.",
        backstory=(
            "SRE sênior com anos de plantão. Ignora ruído de frameworks (uvicorn, starlette, anyio) "
            "e foca no primeiro frame do código da aplicação. Nunca afirma algo que o log não mostra."
        ),
        tools=[read_tool],
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    code_auditor = Agent(
        role="CodeAuditor — Engenheiro de software sênior (Python/FastAPI)",
        goal="Confirmar ou refutar a hipótese do LogAnalyst lendo o código-fonte e explicar por que a lógica falhou.",
        backstory=(
            "Revisor de código rigoroso. Raciocina por primeiros princípios sobre tipos, contratos e "
            "caminhos não tratados. Procura também defeitos vizinhos que causariam o próximo incidente."
        ),
        tools=[read_tool],
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    patch_engineer = Agent(
        role="PatchEngineer — Engenheiro responsável pela correção",
        goal="Produzir um patch mínimo, seguro e testável, e um relatório técnico de incidente (postmortem).",
        backstory=(
            "Engenheiro pragmático: corrige a causa raiz, não o sintoma. Prefere mudanças pequenas, "
            "preserva o contrato da API e sempre entrega o teste que prova a correção."
        ),
        tools=[read_tool],
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    analyze_log = Task(
        description=(
            f"Leia o arquivo de log '{log_path}' com a ferramenta read_project_file.\n"
            "1. Identifique todas as exceções e agrupe as que têm a mesma assinatura.\n"
            "2. Descarte frames de bibliotecas; localize o frame mais profundo do código da aplicação.\n"
            "3. Extraia tipo, mensagem, arquivo, linha, função e a expressão que falhou.\n"
            "4. Correlacione request_ids e parâmetros (ex: coupon_code) para descobrir o gatilho.\n"
            "5. Classifique a severidade considerando alertas de SLO presentes no log."
        ),
        expected_output="Análise estruturada do incidente seguindo o schema LogAnalysis.",
        agent=log_analyst,
        output_pydantic=LogAnalysis,
    )

    audit_code = Task(
        description=(
            f"Com base na análise do log, leia '{source_path}' com read_project_file.\n"
            "1. Localize a linha apontada e confirme se o código explica a exceção.\n"
            "2. Explique, passo a passo, o fluxo de dados que leva ao valor inválido.\n"
            "3. Liste defeitos adicionais no mesmo fluxo (ex: regras de negócio ignoradas).\n"
            "4. Defina os requisitos que o patch precisa cumprir (comportamento esperado por cenário)."
        ),
        expected_output="Auditoria estruturada seguindo o schema CodeAudit.",
        agent=code_auditor,
        context=[analyze_log],
        output_pydantic=CodeAudit,
    )

    write_patch = Task(
        description=(
            f"Usando a análise do log e a auditoria, produza a correção para '{source_path}'.\n"
            "Regras:\n"
            "- Corrija a causa raiz e os defeitos adicionais relevantes, com mudança mínima.\n"
            "- Erros de input do cliente devem virar HTTP 4xx (HTTPException), nunca 500.\n"
            "- Não altere o contrato de resposta de sucesso.\n"
            "- Inclua testes pytest que falham antes e passam depois do patch.\n\n"
            "Entregue APENAS o relatório em Markdown, em português, com as seções:\n"
            "# Relatório de Incidente — <título curto>\n"
            "## 1. Resumo executivo (impacto, severidade, status)\n"
            "## 2. Linha do tempo (a partir dos timestamps do log)\n"
            "## 3. Análise de causa raiz (arquivo, linha, 5 porquês)\n"
            "## 4. Patch (bloco ```diff``` em formato unified diff + bloco ```python``` com a função corrigida)\n"
            "## 5. Testes de regressão (bloco ```python```)\n"
            "## 6. Riscos e ações preventivas (ex: validação de schema, observabilidade, lint/type-check)"
        ),
        expected_output="Relatório técnico completo em Markdown, pronto para ser revisado por um humano.",
        agent=patch_engineer,
        context=[analyze_log, audit_code],
    )

    return Crew(
        agents=[log_analyst, code_auditor, patch_engineer],
        tasks=[analyze_log, audit_code, write_patch],
        process=Process.sequential,
        verbose=True,
    )


def strip_md_fence(text: str) -> str:
    """Remove um eventual ```markdown ... ``` envolvendo o relatório inteiro."""
    t = text.strip()
    if t.startswith("```") and t.endswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    return t.strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="AIR — Autonomous Incident Resolver")
    parser.add_argument("--log", default="logs/error.log")
    parser.add_argument("--source", default="src/app.py")
    parser.add_argument("--output", default="incidente_resolvido.md")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERRO: defina ANTHROPIC_API_KEY no arquivo .env (veja .env.example).", file=sys.stderr)
        return 1

    llm = LLM(
        model=os.getenv("AIR_MODEL", "anthropic/claude-sonnet-5"),
        max_tokens=int(os.getenv("AIR_MAX_TOKENS", "8000")),
    )

    crew = build_crew(args.log, args.source, llm)
    result = crew.kickoff()

    # Relatório final (humano)
    report = strip_md_fence(result.raw)
    output_path = PROJECT_ROOT / args.output
    output_path.write_text(report, encoding="utf-8")

    # Artefato estruturado (máquina) — base para avaliação/observabilidade
    run_dir = PROJECT_ROOT / "runs"
    run_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    artifact = {
        "timestamp": stamp,
        "model": llm.model,
        "log_analysis": result.tasks_output[0].pydantic.model_dump() if result.tasks_output[0].pydantic else None,
        "code_audit": result.tasks_output[1].pydantic.model_dump() if result.tasks_output[1].pydantic else None,
        "token_usage": result.token_usage.model_dump() if result.token_usage else None,
    }
    (run_dir / f"{stamp}.json").write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n✅ Relatório salvo em: {output_path}")
    print(f"📦 Artefato estruturado: runs/{stamp}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

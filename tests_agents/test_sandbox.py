"""
Testes da SandboxedFileReadTool, a única ferramenta que os agentes do AIR têm.

Ficam em tests_agents/ (e não em tests/) porque a suíte de tests/ falha de
propósito: ela é o oráculo que reproduz o incidente. Estes aqui devem passar sempre.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import SandboxedFileReadTool  # noqa: E402

tool = SandboxedFileReadTool()


def test_bloqueia_path_traversal():
    assert tool._run("../../etc/passwd").startswith("ERRO: acesso negado")


def test_bloqueia_caminho_absoluto_fora_do_projeto():
    assert tool._run("/etc/passwd").startswith("ERRO: acesso negado")


def test_arquivo_inexistente():
    assert tool._run("src/nao_existe.py").startswith("ERRO: arquivo não encontrado")


def test_devolve_linhas_numeradas():
    primeira_linha = tool._run("src/app.py").splitlines()[0]
    assert primeira_linha.startswith("   1 | ")


def test_respeita_limite_de_caracteres():
    pequena = SandboxedFileReadTool(max_chars=50)
    assert len(pequena._run("src/app.py")) <= 50

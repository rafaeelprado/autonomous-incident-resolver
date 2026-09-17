# 🤖 AIR — Autonomous Incident Resolver

**Multi-agent system that reads a production error log, finds the root cause, audits the source code, and ships a tested patch — with zero human triage.**

Built with **CrewAI** + **Claude (Anthropic)**, using structured (Pydantic) outputs as strict contracts between agents, a sandboxed file-reading tool, and a pytest suite that acts as the *ground truth* for what "fixed" means.

```
logs/error.log ──▶ LogAnalyst ──▶ CodeAuditor ──▶ PatchEngineer ──▶ incidente_resolvido.md
                  (root cause)    (confirms +      (patch + tests +      runs/<ts>.json
                                                     finds latent     postmortem report)   (structured trace)
                                                                                        defects)
                                                                                        ```

                                                                                        ---

                                                                                        ## Why this exists

                                                                                        Most AI-agent portfolio projects are chatbots wrapped around a prompt. **AIR is a pipeline that does real SRE work**: it takes a raw production log, reconstructs the failure, reads the actual source file, and produces a patch that a human can review and merge — plus the regression tests that prove it works.

                                                                                        This repo is the exact incident it resolved, kept as a living demo:

                                                                                        - A FastAPI checkout service (`src/app.py`) with a **real, realistic production bug**: a `TypeError` crash when a coupon code is missing or invalid.
                                                                                        - A **multi-frame stack trace log** (`logs/error.log`) exactly as it would appear from `uvicorn`/`FastAPI` in production, including an SLO-breach alert.
                                                                                        - The **full AI-generated incident report** (`incidente_resolvido.md`), with root-cause analysis (5 Whys), a unified diff patch, and a pytest suite — generated end-to-end by the three agents below, no manual editing.

                                                                                        ---

                                                                                        ## The agents

                                                                                        | Agent | Role | Output |
                                                                                        |---|---|---|
                                                                                        | **LogAnalyst** | SRE triage specialist. Parses the log, discards library frames, isolates the deepest application frame, correlates `request_id`s and payloads to find the trigger, classifies severity using SLO alerts. | `LogAnalysis` (Pydantic) |
                                                                                        | **CodeAuditor** | Senior Python/FastAPI reviewer. Reads the flagged file, confirms or refutes the hypothesis from first principles, and hunts for **latent defects** in the same code path — not just the crash that got reported. | `CodeAudit` (Pydantic) |
                                                                                        | **PatchEngineer** | Fix owner. Turns the audit into a minimal, safe patch: business-rule errors become `HTTPException` (4xx), never 500; the success contract stays untouched; regression tests are shipped with the patch. | Markdown incident report |

                                                                                        Every hand-off between agents is a typed Pydantic object, not free text — so a bad LogAnalyst read can't silently corrupt the CodeAuditor's reasoning.

                                                                                        ---

                                                                                        ## What it actually found

                                                                                        Running the crew against `logs/error.log`, AIR:

                                                                                        1. Isolated the crash to `src/app.py:42` — `coupon["percent"]` on a `None` coupon.
                                                                                        2. Correlated two different `request_id`s to two different triggers: a missing coupon (`coupon_code=None`) and an unknown one (`"BEMVINDO5"`) — both hitting the same unguarded line.
                                                                                        3. **Found a bug that never appeared in the logs**: `BLACKFRIDAY` is a real coupon marked `active: False`, but the code never checked that flag — it would have silently applied a 30% discount if referenced.
                                                                                        4. Shipped a patch that treats "no coupon" as a valid success path, and both "unknown" and "inactive" coupons as a `422`, with a full pytest suite proving all four scenarios (before: 3 failing / 1 passing → after: 4/4 passing).

                                                                                        Full reasoning, 5-Whys root cause, diff, and tests: [`incidente_resolvido.md`](./incidente_resolvido.md).

                                                                                        ---

                                                                                        ## Engineering highlights

                                                                                        - **Structured outputs as contracts** — `LogAnalysis` and `CodeAudit` are Pydantic models, not prose, so downstream agents get typed, validated data instead of parsing free text.
                                                                                        - **Sandboxed tool access** — the custom `SandboxedFileReadTool` resolves paths against the project root and refuses anything outside it (no `../../etc/passwd`), and returns line-numbered content so agents cite exact lines instead of guessing.
                                                                                        - **Tests as the oracle** — `tests/test_app.py` encodes what "correct" means *before* the fix exists. A patch is only valid when the suite goes green; this is the same contract-testing idea used to gate CI in real incident response.
                                                                                        - **Full traceability** — every run writes a structured JSON artifact (`runs/<timestamp>.json`) with the typed analysis and token usage, so runs are auditable and cheap to evaluate later (success rate, cost per incident, etc.).

                                                                                        ---

                                                                                        ## Run it yourself

                                                                                        ```bash
                                                                                        git clone https://github.com/rafaeelprado/autonomous-incident-resolver.git
                                                                                        cd autonomous-incident-resolver

                                                                                        python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
                                                                                        pip install -r requirements.txt

                                                                                        cp .env.example .env    # add your ANTHROPIC_API_KEY

                                                                                        pytest -q               # 3 tests fail — the incident is reproduced
                                                                                        python main.py           # runs the 3-agent crew, generates incidente_resolvido.md
                                                                                        ```

                                                                                        Recommended model for cheap runs: `anthropic/claude-haiku-4-5` (~$0.10–0.20 per full run).

                                                                                        ---

                                                                                        ## Stack

                                                                                        `CrewAI` · `Claude (Anthropic API)` · `Pydantic v2` · `FastAPI` · `pytest` · `python-dotenv`

                                                                                        ## Roadmap

                                                                                        - [ ] **Verifier agent** — applies its own patch in a sandbox and re-runs pytest in a self-correction loop
                                                                                        - [ ] Multi-incident benchmark (patch success rate vs. cost per incident)
                                                                                        - [ ] GitHub Action that opens a PR with the generated patch automatically

                                                                                        ---

                                                                                        *Built by [Rafael Prado](https://github.com/rafaeelprado) as part of a portfolio for AI Engineering roles — focused on multi-agent orchestration, structured reasoning, and shipping code an engineer can actually trust.*
                                                                                        

# Relatório de Incidente — HTTP 500 em `POST /orders` por `TypeError` em `apply_coupon` (cupom ausente/inválido)

## 1. Resumo executivo (impacto, severidade, status)

**Severidade:** SEV2
**Status:** Causa raiz confirmada; patch proposto e testado abaixo, pronto para revisão/deploy.

**Impacto:** A rota `POST /orders` do `order-service` retornava **HTTP 500** (em vez de um erro 4xx de validação de negócio) sempre que o cliente enviava um pedido:
- sem informar `coupon_code` (`None`, valor default e legítimo do schema), ou
- com um `coupon_code` inexistente/inválido no cadastro (`COUPONS`).

Duas requisições confirmadas foram impactadas diretamente (`request_id=e93f2c7d`, `request_id=0d4a61f3`), e o incidente disparou um alerta de violação de SLO (`5xx rate 18.4%`, limite de 1%), indicando degradação ampla do serviço na janela observada, não restrita a esses dois casos. Requisições com cupom válido e ativo (`BEMVINDO10`) continuaram funcionando normalmente, confirmando o escopo do defeito.

Adicionalmente, foi identificado um **defeito latente de regra de negócio**: o campo `active` do cupom nunca era checado, o que poderia levar à aplicação indevida de desconto de um cupom desativado (`BLACKFRIDAY`, 30% off) caso ele fosse referenciado por um pedido.

## 2. Linha do tempo (a partir dos timestamps do log)

> Observação: os registros de log fornecidos não trazem timestamps explícitos no material recebido; a sequência abaixo é reconstruída pela ordem relativa dos eventos e `request_id`s citados na análise.

1. Requisições anteriores com `coupon_code=BEMVINDO10` (linhas 6 e 7 do log, `request_id=7c1e9b0a` e `request_id=b52d44e1`) — **200 OK**, desconto de 10% aplicado corretamente.
2. `request_id=e93f2c7d`, `customer_id=cus_8f3a21`, `coupon_code=None` (nenhum cupom informado) → lookup em `COUPONS.get(None)` retorna `None` → `TypeError: 'NoneType' object is not subscriptable` em `src/app.py:42` → **HTTP 500**.
3. `request_id=0d4a61f3`, `customer_id=cus_c4410e`, `coupon_code=BEMVINDO5` (código inexistente, diferente de `BEMVINDO10`) → mesmo padrão de falha → **HTTP 500**.
4. Logo após as falhas: alerta de observabilidade — `"SLO breach: order-service 5xx rate 18.4% (threshold 1%) window=5m"`, evidenciando que a taxa de erro do serviço na janela de 5 minutos excedeu em ~18x o limite aceitável.
5. Abertura de investigação: `LogAnalyst` formula hipótese de causa raiz a partir do stack trace; `Auditor` confirma a hipótese e mapeia defeitos adicionais; presente relatório consolida a correção.

## 3. Análise de causa raiz (arquivo, linha, 5 porquês)

**Arquivo:** `src/app.py`
**Função:** `apply_coupon`
**Linha do defeito:** 42 (`discount = subtotal * coupon["percent"] / Decimal("100")`), precedida pelo lookup na linha 41 (`coupon = COUPONS.get(coupon_code)`).

**Por que 1 — Por que a API retornou 500?**
Porque uma exceção `TypeError` não tratada foi propagada da função `apply_coupon` até o handler ASGI/FastAPI, que converte exceções não capturadas em erro genérico 500.

**Por que 2 — Por que ocorreu o `TypeError`?**
Porque o código tentou fazer `coupon["percent"]` quando `coupon` era `None`, e `None` não suporta o operador de subscrição (`__getitem__`).

**Por que 3 — Por que `coupon` era `None`?**
Porque `COUPONS.get(coupon_code)` retorna `None` por contrato sempre que a chave não existe no dicionário — e isso ocorre tanto quando `coupon_code` é `None` (nenhum cupom informado) quanto quando é uma string não cadastrada (ex.: `"BEMVINDO5"`).

**Por que 4 — Por que o código não tratou esse `None`?**
Porque não havia nenhuma guarda (`if coupon is None: ...`) entre o lookup (linha 41) e o uso (linha 42), apesar da própria assinatura da função (`coupon_code: Optional[str]`) e do schema `OrderPayload.coupon_code: Optional[str] = None` deixarem explícito que "nenhum cupom" é um caminho de entrada válido e esperado.

**Por que 5 — Por que esse caminho válido não foi coberto no design/implementação?**
Porque a lógica foi escrita assumindo implicitamente que todo `coupon_code` recebido seria sempre um cupom pré-validado e existente (um "caminho feliz" único), sem cobertura de testes para os cenários de cupom ausente, inexistente ou inativo, e sem uma camada de tratamento de erros de negócio (`HTTPException`) na rota `create_order` — ou seja, ausência de validação defensiva e de testes de contorno (edge cases) no momento da implementação/revisão de código.

**Causa raiz:** Falta de guarda de nulidade/validade sobre o retorno de `COUPONS.get(coupon_code)` em `apply_coupon`, combinada com a ausência de tratamento de erro de negócio (`HTTPException`) na camada da API, fazendo com que entradas legítimas do cliente (sem cupom, ou com cupom inválido/inativo) resultassem em erro de servidor (500) em vez de resposta 4xx apropriada.

## 4. Patch (diff unified + função corrigida)

```diff
--- a/src/app.py
+++ b/src/app.py
@@ -1,14 +1,15 @@
 """
 Order Service — API de checkout (FastAPI).
 
 Serviço propositalmente com um bug para o AIR (Autonomous Incident Resolver)
 diagnosticar e corrigir. NÃO corrija manualmente: este é o "paciente".
 """
 
+import logging
 from decimal import Decimal
 from typing import Optional
 
-from fastapi import FastAPI
+from fastapi import FastAPI, HTTPException
 from pydantic import BaseModel, Field
 
 app = FastAPI(title="Order Service", version="1.4.2")
 
+logger = logging.getLogger("order_service")
+
 # Simula uma tabela de cupons vinda do banco/cache
 COUPONS: dict[str, dict] = {
     "BEMVINDO10": {"percent": Decimal("10"), "active": True},
     "BLACKFRIDAY": {"percent": Decimal("30"), "active": False},
 }
@@ -37,10 +38,22 @@ def calculate_subtotal(items: list[OrderItem]) -> Decimal:
 
 
 def apply_coupon(subtotal: Decimal, coupon_code: Optional[str]) -> Decimal:
-    """Aplica o desconto do cupom ao subtotal."""
-    coupon = COUPONS.get(coupon_code)
-    discount = subtotal * coupon["percent"] / Decimal("100")
-    return subtotal - discount
+    """Aplica o desconto do cupom ao subtotal.
+
+    - Sem cupom informado (coupon_code=None): retorna o subtotal sem desconto.
+    - Cupom inexistente ou inativo: erro de negócio (HTTP 422), nunca 500.
+    - Cupom válido e ativo: aplica o percentual de desconto normalmente.
+    """
+    if coupon_code is None:
+        return subtotal
+
+    coupon = COUPONS.get(coupon_code)
+    if coupon is None or not coupon.get("active", False):
+        logger.warning("Cupom inválido ou inativo recebido: coupon_code=%s", coupon_code)
+        raise HTTPException(
+            status_code=422,
+            detail=f"Cupom inválido ou expirado: {coupon_code!r}",
+        )
+
+    discount = subtotal * coupon["percent"] / Decimal("100")
+    return subtotal - discount
```

Função corrigida completa (`src/app.py`):

```python
"""
Order Service — API de checkout (FastAPI).

Serviço propositalmente com um bug para o AIR (Autonomous Incident Resolver)
diagnosticar e corrigir. NÃO corrija manualmente: este é o "paciente".
"""

import logging
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Order Service", version="1.4.2")

logger = logging.getLogger("order_service")

# Simula uma tabela de cupons vinda do banco/cache
COUPONS: dict[str, dict] = {
    "BEMVINDO10": {"percent": Decimal("10"), "active": True},
    "BLACKFRIDAY": {"percent": Decimal("30"), "active": False},
}


class OrderItem(BaseModel):
    sku: str
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(gt=0)


class OrderPayload(BaseModel):
    customer_id: str
    items: list[OrderItem]
    coupon_code: Optional[str] = None


def calculate_subtotal(items: list[OrderItem]) -> Decimal:
    return sum((item.unit_price * item.quantity for item in items), Decimal("0"))


def apply_coupon(subtotal: Decimal, coupon_code: Optional[str]) -> Decimal:
    """Aplica o desconto do cupom ao subtotal.

    - Sem cupom informado (coupon_code=None): retorna o subtotal sem desconto.
    - Cupom inexistente ou inativo: erro de negócio (HTTP 422), nunca 500.
    - Cupom válido e ativo: aplica o percentual de desconto normalmente.
    """
    if coupon_code is None:
        return subtotal

    coupon = COUPONS.get(coupon_code)
    if coupon is None or not coupon.get("active", False):
        logger.warning("Cupom inválido ou inativo recebido: coupon_code=%s", coupon_code)
        raise HTTPException(
            status_code=422,
            detail=f"Cupom inválido ou expirado: {coupon_code!r}",
        )

    discount = subtotal * coupon["percent"] / Decimal("100")
    return subtotal - discount


@app.post("/orders")
def create_order(payload: OrderPayload) -> dict:
    subtotal = calculate_subtotal(payload.items)
    total = apply_coupon(subtotal, payload.coupon_code)
    return {
        "customer_id": payload.customer_id,
        "subtotal": str(subtotal),
        "total": str(total.quantize(Decimal("0.01"))),
        "status": "created",
    }
```

**Notas sobre o patch:**
- Contrato de resposta de sucesso **inalterado** (mesmas chaves/formatos: `customer_id`, `subtotal`, `total`, `status`).
- Cupom ausente (`None`) agora é um caminho de sucesso explícito (subtotal sem desconto), eliminando o `TypeError`.
- Cupom inexistente **e** cupom inativo (ex.: `BLACKFRIDAY`) agora são tratados de forma unificada como erro de cliente (`HTTPException(422)`), corrigindo tanto a causa raiz quanto o defeito latente de regra de negócio (desconto indevido em cupom inativo), sem exigir uma decisão de produto adicional além de "cupom inválido/expirado não aplica desconto".
- Log estruturado (`logger.warning`) adicionado no ponto de rejeição, atendendo ao requisito de observabilidade sem depender de stack trace em produção.

## 5. Testes de regressão

```python
"""
Testes de regressão para o incidente: TypeError em apply_coupon
quando coupon_code é None ou inválido/inativo.

Execução: pytest -v test_app.py
Pré-requisito: pip install pytest fastapi httpx
"""

import pytest
from decimal import Decimal
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.app import app, apply_coupon

client = TestClient(app)


def _payload(coupon_code=None):
    return {
        "customer_id": "cus_teste",
        "items": [
            {"sku": "SKU1", "quantity": 2, "unit_price": "50.00"},
        ],
        "coupon_code": coupon_code,
    }


# ---------------------------------------------------------------------------
# Testes unitários de apply_coupon
# ---------------------------------------------------------------------------

def test_apply_coupon_sem_cupom_retorna_subtotal_sem_desconto():
    """Antes do patch: TypeError. Depois do patch: retorna subtotal integral."""
    subtotal = Decimal("100.00")
    resultado = apply_coupon(subtotal, None)
    assert resultado == subtotal


def test_apply_coupon_inexistente_levanta_http_exception_422():
    """Antes do patch: TypeError (500). Depois do patch: HTTPException 422."""
    with pytest.raises(HTTPException) as exc_info:
        apply_coupon(Decimal("100.00"), "BEMVINDO5")
    assert exc_info.value.status_code == 422


def test_apply_coupon_inativo_levanta_http_exception_422():
    """Defeito adicional: cupom inativo (BLACKFRIDAY) não deve aplicar desconto
    nem estourar 500 — deve ser tratado como erro de negócio."""
    with pytest.raises(HTTPException) as exc_info:
        apply_coupon(Decimal("100.00"), "BLACKFRIDAY")
    assert exc_info.value.status_code == 422


def test_apply_coupon_valido_ativo_aplica_desconto_corretamente():
    """Não-regressão: cupom válido e ativo continua funcionando como antes."""
    subtotal = Decimal("100.00")
    resultado = apply_coupon(subtotal, "BEMVINDO10")
    assert resultado == Decimal("90.00")


# ---------------------------------------------------------------------------
# Testes de integração via API (contrato HTTP)
# ---------------------------------------------------------------------------

def test_post_orders_sem_cupom_retorna_200():
    """Reproduz request_id=e93f2c7d: coupon_code=None não deve mais causar 500."""
    response = client.post("/orders", json=_payload(coupon_code=None))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    assert body["subtotal"] == "100.00"
    assert body["total"] == "100.00"


def test_post_orders_cupom_inexistente_retorna_4xx_nao_500():
    """Reproduz request_id=0d4a61f3: coupon_code=BEMVINDO5 (inexistente)."""
    response = client.post("/orders", json=_payload(coupon_code="BEMVINDO5"))
    assert 400 <= response.status_code < 500
    assert response.status_code != 500


def test_post_orders_cupom_inativo_retorna_4xx_nao_500():
    """Cenário do defeito adicional: BLACKFRIDAY está cadastrado mas inativo."""
    response = client.post("/orders", json=_payload(coupon_code="BLACKFRIDAY"))
    assert 400 <= response.status_code < 500
    assert response.status_code != 500


def test_post_orders_cupom_valido_ativo_mantem_contrato_de_sucesso():
    """Não-regressão do fluxo feliz (equivalente às reqs 7c1e9b0a / b52d44e1)."""
    response = client.post("/orders", json=_payload(coupon_code="BEMVINDO10"))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"customer_id", "subtotal", "total", "status"}
    assert body["subtotal"] == "100.00"
    assert body["total"] == "90.00"
    assert body["status"] == "created"
```

**Comportamento esperado:**
- **Antes do patch:** `test_apply_coupon_sem_cupom_retorna_subtotal_sem_desconto`, `test_apply_coupon_inexistente_levanta_http_exception_422`, `test_apply_coupon_inativo_levanta_http_exception_422`, `test_post_orders_sem_cupom_retorna_200`, `test_post_orders_cupom_inexistente_retorna_4xx_nao_500` e `test_post_orders_cupom_inativo_retorna_4xx_nao_500` **falham** (levantam `TypeError` não tratado ou recebem `500`).
- **Depois do patch:** todos os testes **passam**, incluindo os de não-regressão do fluxo com cupom válido ativo.

## 6. Riscos e ações preventivas

**Riscos residuais:**
- O status code `422` foi escolhido por semântica ("entidade não processável" — regra de negócio violada apesar do payload ser sintaticamente válido). Se o time de produto/API preferir `400 Bad Request`, é uma troca de uma linha (`status_code=400`) sem impacto estrutural — deve ser alinhado com consumidores da API antes do deploy, pois é uma mudança de contrato de erro (ainda que dentro da faixa 4xx já exigida).
- Normalização de `coupon_code` (trim/uppercase) **não foi incluída** neste patch mínimo, pois não fazia parte da causa raiz do 500 e alteraria comportamento de matching hoje case-sensitive; recomenda-se tratar como melhoria separada (ver ações abaixo) para não misturar escopos no mesmo patch.

**Ações preventivas recomendadas:**
1. **Validação de schema/normalização:** adicionar um `field_validator` no Pydantic `OrderPayload.coupon_code` para normalizar (`strip().upper()`) antes de chegar à lógica de negócio, reduzindo falsos "cupom inválido" por diferença de caixa/espaços.
2. **Padronização de erros de negócio:** criar um handler central de exceções de domínio (ex.: `CouponError`) convertido para `HTTPException` em um único ponto, evitando que futuras funções repitam o mesmo padrão de "esquecer a guarda de nulidade".
3. **Observabilidade:** promover o `logger.warning` adicionado para incluir `request_id`/`customer_id` (via middleware de contexto) e criar uma métrica dedicada (ex.: `coupon_rejected_total{reason="not_found|inactive"}`) para diferenciar rejeições de negócio de erros reais de servidor no dashboard de SLO, evitando que rejeições 4xx voltem a ser confundidas com degradação de serviço.
4. **Lint/type-check:** habilitar `mypy`/`pyright` no pipeline de CI com modo estrito para `Optional`; o próprio type-checker teria sinalizado `coupon["percent"]` como acesso inseguro a um `Optional[dict]`, pegando esse defeito antes do deploy.
5. **Cobertura de testes obrigatória para edge cases:** exigir em code review testes explícitos para valores `None`/vazios/inexistentes em toda função que faz lookup em dicionário/cache antes do merge, especialmente em campos opcionais de payloads de API pública.
6. **Alerta de SLO como gatilho de runbook:** documentar este incidente como caso de referência no runbook de `order-service`, associando o padrão de alerta "5xx rate breach" a uma checklist rápida (ex.: "verificar se houve mudança recente em validação de campos opcionais do payload").

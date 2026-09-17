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

1. Requisições anteriores com `coupon_code=BEMVINDO10` (`request_id=7c1e9b0a` e `request_id=b52d44e1`) — **200 OK**, desconto de 10% aplicado corretamente.
2. `request_id=e93f2c7d`, `customer_id=cus_8f3a21`, `coupon_code=None` (nenhum cupom informado) → lookup em `COUPONS.get(None)` retorna `None` → `TypeError: 'NoneType' object is not subscriptable` em `src/app.py:42` → **HTTP 500**.
3. `request_id=0d4a61f3`, `customer_id=cus_c4410e`, `coupon_code=BEMVINDO5` (código inexistente, diferente de `BEMVINDO10`) → mesmo padrão de falha → **HTTP 500**.
4. Logo após as falhas: alerta de observabilidade — `"SLO breach: order-service 5xx rate 18.4% (threshold 1%) window=5m"`.
5. Abertura de investigação automatizada: `LogAnalyst` formula hipótese de causa raiz a partir do stack trace; `CodeAuditor` confirma a hipótese e mapeia defeitos adicionais; `PatchEngineer` consolida a correção.

## 3. Análise de causa raiz (arquivo, linha, 5 porquês)

**Arquivo:** `src/app.py`
**Função:** `apply_coupon`
**Linha do defeito:** 42 (`discount = subtotal * coupon["percent"] / Decimal("100")`), precedida pelo lookup na linha 41 (`coupon = COUPONS.get(coupon_code)`).

**Por que 1 — Por que a API retornou 500?**
Porque uma exceção `TypeError` não tratada foi propagada da função `apply_coupon` até o handler ASGI/FastAPI, que converte exceções não capturadas em erro genérico 500.

**Por que 2 — Por que ocorreu o `TypeError`?**
Porque o código tentou fazer `coupon["percent"]` quando `coupon` era `None`, e `None` não suporta o operador de subscrição.

**Por que 3 — Por que `coupon` era `None`?**
Porque `COUPONS.get(coupon_code)` retorna `None` por contrato sempre que a chave não existe — tanto quando `coupon_code` é `None` quanto quando é uma string não cadastrada.

**Por que 4 — Por que o código não tratou esse `None`?**
Porque não havia nenhuma guarda (`if coupon is None: ...`) entre o lookup e o uso, apesar da própria assinatura da função (`coupon_code: Optional[str]`) deixar explícito que "nenhum cupom" é um caminho de entrada válido.

**Por que 5 — Por que esse caminho válido não foi coberto no design/implementação?**
Porque a lógica foi escrita assumindo implicitamente um único "caminho feliz", sem cobertura de testes para cupom ausente, inexistente ou inativo, e sem uma camada de tratamento de erros de negócio (`HTTPException`) na rota.

**Causa raiz:** Falta de guarda de nulidade/validade sobre o retorno de `COUPONS.get(coupon_code)` em `apply_coupon`, combinada com a ausência de tratamento de erro de negócio na camada da API.

## 4. Patch

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
- Contrato de resposta de sucesso **inalterado**.
- Cupom ausente (`None`) agora é um caminho de sucesso explícito (subtotal sem desconto), eliminando o `TypeError`.
- Cupom inexistente **e** cupom inativo (ex.: `BLACKFRIDAY`) agora são tratados de forma unificada como erro de cliente (`HTTPException(422)`), corrigindo tanto a causa raiz quanto o defeito latente de regra de negócio.
- Log estruturado (`logger.warning`) adicionado no ponto de rejeição.

## 5. Testes de regressão

```python
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)

ITEMS = [{"sku": "TSHIRT-BLK-M", "quantity": 2, "unit_price": "79.90"}]


def test_order_with_valid_coupon():
    r = client.post("/orders", json={"customer_id": "c1", "items": ITEMS, "coupon_code": "BEMVINDO10"})
    assert r.status_code == 200
    assert r.json()["total"] == "143.82"


def test_order_without_coupon():
    r = client.post("/orders", json={"customer_id": "c2", "items": ITEMS})
    assert r.status_code == 200
    assert r.json()["total"] == "159.80"


def test_unknown_coupon_is_client_error():
    r = client.post("/orders", json={"customer_id": "c3", "items": ITEMS, "coupon_code": "BEMVINDO5"})
    assert 400 <= r.status_code < 500


def test_inactive_coupon_is_client_error():
    r = client.post("/orders", json={"customer_id": "c4", "items": ITEMS, "coupon_code": "BLACKFRIDAY"})
    assert 400 <= r.status_code < 500
```

**Resultado real de execução:**

```
Antes do patch:  .FFF   → 1 passed, 3 failed
Depois do patch: ....   → 4 passed, 0 failed
```

## 6. Riscos e ações preventivas

**Riscos residuais:**
- O status code `422` foi escolhido por semântica de "entidade não processável". Se o time de produto/API preferir `400 Bad Request`, é uma troca de uma linha, mas deve ser alinhado com consumidores da API antes do deploy.
- Normalização de `coupon_code` (trim/uppercase) não foi incluída neste patch mínimo — recomenda-se tratar como melhoria separada.

**Ações preventivas recomendadas:**
1. **Validação de schema/normalização:** adicionar um `field_validator` no Pydantic `OrderPayload.coupon_code` para normalizar (`strip().upper()`).
2. **Padronização de erros de negócio:** criar um handler central de exceções de domínio.
3. **Observabilidade:** incluir `request_id`/`customer_id` no log e criar uma métrica dedicada (`coupon_rejected_total{reason="not_found|inactive"}`).
4. **Lint/type-check:** habilitar `mypy`/`pyright` em modo estrito para `Optional` — o próprio type-checker teria sinalizado o acesso inseguro.
5. **Cobertura de testes obrigatória para edge cases** em code review, especialmente para campos opcionais de payloads públicos.
6. **Alerta de SLO como gatilho de runbook:** documentar este incidente como caso de referência, associando o padrão "5xx rate breach" a uma checklist de troubleshooting.

---

*Relatório gerado automaticamente pela crew AIR (LogAnalyst → CodeAuditor → PatchEngineer), com base em `logs/error.log` e `src/app.py`.*

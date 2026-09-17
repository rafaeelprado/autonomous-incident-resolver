"""
Order Service — API de checkout (FastAPI).

Serviço propositalmente com um bug para o AIR (Autonomous Incident Resolver)
diagnosticar e corrigir. NÃO corrija manualmente: este é o "paciente".
"""

from decimal import Decimal
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Order Service", version="1.4.2")

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
    """Aplica o desconto do cupom ao subtotal."""
    coupon = COUPONS.get(coupon_code)
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

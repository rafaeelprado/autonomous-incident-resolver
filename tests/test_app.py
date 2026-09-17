"""
Testes de contrato do Order Service.

Hoje 3 dos 4 testes FALHAM: os dois primeiros reproduzem o incidente de
logs/error.log e o de cupom inativo expõe um bug latente (regra ignorada). Um patch só é considerado válido
quando toda esta suíte passa.
"""

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

"""
Inventory Service – Verwaltet Lagerbestände.
Daten werden in data/inventory.json gespeichert.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
import json, os, time

DATA_FILE = os.getenv("DATA_FILE", "data/inventory.json")

# ── Datei-Persistenz ───────────────────────────────────────────────────────────
def _load() -> dict:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if not os.path.exists(DATA_FILE):
        _save({})
    with open(DATA_FILE) as f:
        return json.load(f)

def _save(data: dict):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── Seed-Bestände (dieselben IDs wie im Product Service) ──────────────────────
SEED_INVENTORY = {
    "seed-0001": 15,   # Laptop Pro 15
    "seed-0002": 30,   # Wireless Headphones
    "seed-0003": 25,   # Mechanical Keyboard
    "seed-0004": 50,   # Python Crashkurs
    "seed-0005": 20,   # Laufschuhe Air Max
    "seed-0006": 40,   # Hoodie Classic
    "seed-0007": 60,   # Trinkflasche 1L
}

@asynccontextmanager
async def lifespan(app):
    """Seed-Bestände beim Start anlegen, falls die Datenbank leer ist."""
    db = _load()
    if not db:
        for product_id, quantity in SEED_INVENTORY.items():
            db[product_id] = {"quantity": quantity, "updated_at": time.time()}
        _save(db)
    yield

app = FastAPI(title="Inventory Service", description="Lagerverwaltung", version="1.0.0", lifespan=lifespan)

# ── Pydantic-Modelle ───────────────────────────────────────────────────────────
class StockUpdate(BaseModel):
    quantity: int = Field(..., ge=0, example=50)

class StockItem(BaseModel):
    product_id: str
    quantity: int
    updated_at: float

class ReserveRequest(BaseModel):
    product_id: str = Field(..., example="abc-123")
    quantity: int = Field(..., gt=0, example=2)

# ── Endpunkte ──────────────────────────────────────────────────────────────────
@app.get("/inventory", response_model=list[StockItem], tags=["Inventory"])
async def list_inventory():
    """Kompletten Lagerbestand abrufen."""
    db = _load()
    return [{"product_id": pid, **data} for pid, data in db.items()]

@app.get("/inventory/{product_id}", response_model=StockItem, tags=["Inventory"])
async def get_stock(product_id: str):
    """Bestand für ein Produkt abrufen."""
    db = _load()
    if product_id not in db:
        raise HTTPException(404, "Produkt nicht im Lager")
    return {"product_id": product_id, **db[product_id]}

@app.put("/inventory/{product_id}", response_model=StockItem, tags=["Inventory"])
async def set_stock(product_id: str, body: StockUpdate):
    """Lagerbestand setzen (oder anlegen)."""
    db = _load()
    db[product_id] = {"quantity": body.quantity, "updated_at": time.time()}
    _save(db)
    return {"product_id": product_id, **db[product_id]}

@app.post("/inventory/reserve", tags=["Inventory"])
async def reserve_stock(body: ReserveRequest):
    """Bestand reservieren (z.B. bei Bestellung). Gibt Fehler zurück wenn nicht genug da."""
    db = _load()
    entry = db.get(body.product_id)
    if not entry or entry["quantity"] < body.quantity:
        raise HTTPException(409, f"Nicht genug Bestand für {body.product_id}")
    entry["quantity"] -= body.quantity
    entry["updated_at"] = time.time()
    db[body.product_id] = entry
    _save(db)
    return {"reserved": body.quantity, "remaining": entry["quantity"]}

@app.get("/metrics", tags=["Monitoring"])
async def metrics():
    db = _load()
    total = sum(v["quantity"] for v in db.values())
    return {"service": "inventory-service", "status": "ok",
            "total_items": len(db), "total_units": total, "uptime": int(time.time())}

@app.get("/health", tags=["Monitoring"])
async def health():
    return {"status": "ok"}

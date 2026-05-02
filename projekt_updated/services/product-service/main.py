"""
Product Service – Verwaltet den Produktkatalog.
Daten werden in data/products.json gespeichert.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
import json, os, time, uuid

DATA_FILE = os.getenv("DATA_FILE", "data/products.json")

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

# ── Seed-Daten mit festen IDs (damit Inventory-Service dieselben IDs kennt) ────
SEED_PRODUCTS = [
    {"id": "seed-0001", "name": "Laptop Pro 15",       "description": "Leistungsstarker Laptop für Studium & Arbeit", "price": 999.99,  "category": "electronics"},
    {"id": "seed-0002", "name": "Wireless Headphones", "description": "Over-Ear Kopfhörer mit Noise Cancelling",      "price":  79.99,  "category": "electronics"},
    {"id": "seed-0003", "name": "Mechanical Keyboard", "description": "Taktiles Tippgefühl mit RGB-Beleuchtung",      "price":  59.99,  "category": "electronics"},
    {"id": "seed-0004", "name": "Python Crashkurs",    "description": "Einsteigerbuch für Programmierung mit Python",  "price":  29.99,  "category": "books"},
    {"id": "seed-0005", "name": "Laufschuhe Air Max",  "description": "Leichte Laufschuhe für Alltag und Sport",      "price":  89.99,  "category": "sports"},
    {"id": "seed-0006", "name": "Hoodie Classic",      "description": "Bequemer Hoodie aus Bio-Baumwolle",             "price":  39.99,  "category": "clothing"},
    {"id": "seed-0007", "name": "Trinkflasche 1L",     "description": "Isolierte Edelstahl-Trinkflasche",              "price":  24.99,  "category": "sports"},
]

@asynccontextmanager
async def lifespan(app):
    """Seed-Daten beim Start anlegen, falls die Datenbank leer ist."""
    db = _load()
    if not db:
        for item in SEED_PRODUCTS:
            db[item["id"]] = {**item, "created_at": time.time()}
        _save(db)
    yield

app = FastAPI(title="Product Service", description="Produktverwaltung", version="1.0.0", lifespan=lifespan)

# ── Pydantic-Modelle ───────────────────────────────────────────────────────────
class ProductBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, example="Laptop Pro 15")
    description: str = Field("", example="Leistungsstarker Laptop")
    price: float = Field(..., gt=0, example=999.99)
    category: str = Field("general", example="electronics")

class ProductCreate(ProductBase):
    pass

class Product(ProductBase):
    id: str
    created_at: float

# ── Endpunkte ──────────────────────────────────────────────────────────────────
@app.get("/products", response_model=list[Product], tags=["Products"])
async def list_products():
    """Alle Produkte auflisten."""
    return list(_load().values())

@app.post("/products", response_model=Product, status_code=201, tags=["Products"])
async def create_product(body: ProductCreate):
    """Neues Produkt anlegen."""
    db = _load()
    product = Product(id=str(uuid.uuid4()), created_at=time.time(), **body.model_dump())
    db[product.id] = product.model_dump()
    _save(db)
    return product

@app.get("/products/{product_id}", response_model=Product, tags=["Products"])
async def get_product(product_id: str):
    """Ein Produkt per ID abrufen."""
    db = _load()
    if product_id not in db:
        raise HTTPException(404, "Produkt nicht gefunden")
    return db[product_id]

@app.put("/products/{product_id}", response_model=Product, tags=["Products"])
async def update_product(product_id: str, body: ProductBase):
    """Produkt aktualisieren."""
    db = _load()
    if product_id not in db:
        raise HTTPException(404, "Produkt nicht gefunden")
    updated = {**db[product_id], **body.model_dump()}
    db[product_id] = updated
    _save(db)
    return updated

@app.delete("/products/{product_id}", tags=["Products"])
async def delete_product(product_id: str):
    """Produkt löschen."""
    db = _load()
    if product_id not in db:
        raise HTTPException(404, "Produkt nicht gefunden")
    del db[product_id]
    _save(db)
    return {"message": "Produkt gelöscht"}

@app.get("/metrics", tags=["Monitoring"])
async def metrics():
    return {"service": "product-service", "status": "ok",
            "product_count": len(_load()), "uptime": int(time.time())}

@app.get("/health", tags=["Monitoring"])
async def health():
    return {"status": "ok"}

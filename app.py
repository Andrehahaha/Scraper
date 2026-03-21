from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import os
import threading
import server.database as database
import server.scraper as scraper

scheduler = BackgroundScheduler()

def job_giornaliero():
    print("⏰ [Scheduler] Avvio aggiornamento giornaliero...")
    scraper.aggiorna_tutto()
    print("✅ [Scheduler] Completato.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Primo download se DB vuoto
    if not database.ultimo_aggiornamento():
        print("📦 DB vuoto — primo download in background...")
        threading.Thread(target=scraper.aggiorna_tutto, daemon=True).start()

    # Scheduler: ogni giorno alle 03:00
    scheduler.add_job(job_giornaliero, "cron", hour=3, minute=0, id="daily")
    scheduler.start()
    print("⏰ Scheduler attivo — aggiornamento ogni giorno alle 03:00")
    yield
    scheduler.shutdown()

app = FastAPI(title="Tracker Integratori API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    job = scheduler.get_job("daily")
    prossimo = str(job.next_run_time)[:16] if job and job.next_run_time else "N/D"
    return {
        "status": "ok",
        "ultimo_aggiornamento": database.ultimo_aggiornamento(),
        "prossimo_aggiornamento": prossimo,
        "negozi": database.negozi_disponibili(),
    }


@app.get("/api/negozi")
def get_negozi():
    return {
        "negozi": list(scraper.NEGOZI.keys()),
        "colori": {n: cfg["colore"] for n, cfg in scraper.NEGOZI.items()},
    }


@app.get("/api/categorie")
def get_categorie():
    return {"categorie": scraper.CATEGORIE}


@app.get("/api/prodotti/{negozio}/{categoria}")
def get_prodotti(negozio: str, categoria: str):
    if negozio not in scraper.NEGOZI:
        raise HTTPException(404, f"Negozio '{negozio}' non trovato")
    prodotti = database.carica_categoria(negozio, categoria)
    return {
        "negozio": negozio,
        "categoria": categoria,
        "prodotti": prodotti,
        "totale": len(prodotti),
    }


@app.get("/api/offerte")
def get_offerte(
    soglia: int = Query(default=0, ge=0, le=100),
    negozio: str = Query(default=None),
):
    prodotti = database.offerte_convenienti(soglia, negozio if negozio else None)
    return {
        "soglia": soglia,
        "negozio": negozio,
        "prodotti": prodotti,
        "totale": len(prodotti),
    }


@app.get("/api/aggiornamento")
def get_aggiornamento():
    job = scheduler.get_job("daily")
    prossimo = str(job.next_run_time)[:16] if job and job.next_run_time else "N/D"
    return {
        "ultimo_aggiornamento": database.ultimo_aggiornamento(),
        "prossimo_aggiornamento": prossimo,
    }


@app.get("/api/storico/{negozio}/{nome}")
def get_storico_prezzi(
    negozio: str, nome: str,
    limite: int = Query(default=50, ge=1, le=200),
):
    storico = database.storico_prezzi_prodotto(negozio, nome, limite)
    return {"negozio": negozio, "nome": nome, "storico": storico, "totale": len(storico)}

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/api/aggiorna")
def forza_aggiornamento(secret: str = Query(...)):
    if secret != os.environ.get("ADMIN_SECRET", "cambia_questa_password"):
        raise HTTPException(403, "Secret non valido")
    threading.Thread(target=scraper.aggiorna_tutto, daemon=True).start()
    return {"status": "aggiornamento avviato"}
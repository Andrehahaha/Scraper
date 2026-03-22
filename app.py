import os
import threading
import logging
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import database
import scraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tracker")

scheduler = BackgroundScheduler(daemon=True)
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "cambia_questa_password")
UPDATE_HOUR  = int(os.environ.get("UPDATE_HOUR", "3"))


def job_giornaliero():
    log.info("⏰ Avvio aggiornamento giornaliero...")
    try:
        scraper.aggiorna_tutto()
        log.info("✅ Aggiornamento completato.")
    except Exception as e:
        log.error(f"❌ Errore aggiornamento: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        job_giornaliero, "cron",
        hour=UPDATE_HOUR, minute=0,
        id="daily", replace_existing=True,
    )
    scheduler.start()
    log.info(f"⏰ Scheduler attivo — ogni giorno alle {UPDATE_HOUR:02d}:00")


    def _controlla_aggiornamento_mancato():
        from datetime import datetime
        ult = database.ultimo_aggiornamento()
        if not ult:
            log.info("📦 DB vuoto — primo download...")
            job_giornaliero()
            return
        ora = datetime.now()
        if 3 <= ora.hour < 6:
            try:
                ultimo_dt = datetime.strptime(ult[:16], "%Y-%m-%d %H:%M")
                if ultimo_dt.date() < ora.date():
                    log.info("⏰ Aggiornamento notturno mancato — eseguo ora...")
                    job_giornaliero()
            except Exception as e:
                log.warning(f"Errore controllo data: {e}")

    threading.Thread(target=_controlla_aggiornamento_mancato, daemon=True).start()

    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Tracker Integratori API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── ENDPOINTS ────────────────────────────────────────────────

@app.get("/")
def root():
    job = scheduler.get_job("daily")
    prossimo = str(job.next_run_time)[:16] if job and job.next_run_time else "N/D"
    return {
        "status": "ok",
        "ultimo_aggiornamento": database.ultimo_aggiornamento(),
        "prossimo_aggiornamento": prossimo,
        "negozi_disponibili": database.negozi_disponibili(),
    }


# FastAPI gestisce HEAD automaticamente per i GET — basta definire GET
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/ping")
def ping():
    return {"status": "alive"}


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


# Supporta sia GET (cron-job.org semplice) che POST
@app.api_route("/api/aggiorna", methods=["GET", "POST"])
def forza_aggiornamento(secret: str = Query(...)):
    """
    Trigger aggiornamento manuale.
    Usato da cron-job.org ogni notte alle 03:00.
    Es: GET https://server.onrender.com/api/aggiorna?secret=PASSWORD
    """
    if secret != ADMIN_SECRET:
        raise HTTPException(403, "Secret non valido")
    threading.Thread(target=job_giornaliero, daemon=True).start()
    return {"status": "aggiornamento avviato"}
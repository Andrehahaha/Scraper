import os
import threading
import logging
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import database
import scraper

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _matplotlib_ok = True
except Exception:
    _matplotlib_ok = False

# notifiche opzionale — non crasha se manca
try:
    import notifiche
    _notifiche_ok = True
except ImportError:
    _notifiche_ok = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tracker")

scheduler    = BackgroundScheduler(daemon=True)
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")
if not ADMIN_SECRET:
    raise RuntimeError("ADMIN_SECRET non impostata. Imposta la variabile d'ambiente ADMIN_SECRET.")
UPDATE_HOUR  = int(os.environ.get("UPDATE_HOUR", "3"))
FLASH_SOGLIA = int(os.environ.get("FLASH_SOGLIA", "20"))
TELEGRAM_POLL_SECONDS = int(os.environ.get("TELEGRAM_POLL_SECONDS", "60"))


def _parse_prezzo_to_float(val: str):
    if not val:
        return None
    try:
        return float(str(val).replace("€", "").replace(",", ".").replace("\xa0", "").strip())
    except Exception:
        return None


def job_giornaliero():
    log.info("⏰ Avvio aggiornamento giornaliero...")
    tutte_variazioni = []
    try:
        for negozio, cfg in scraper.NEGOZI.items():
            for categoria in cfg["categorie"]:
                prodotti = scraper.scrapa_categoria(negozio, categoria)
                variazioni = database.salva_categoria(negozio, categoria, prodotti)
                if isinstance(variazioni, list):
                    tutte_variazioni.extend(variazioni)

        # Flash sale
        try:
            flash = database.flash_sale(soglia_calo=FLASH_SOGLIA, ore=25)
            if flash and _notifiche_ok:
                notifiche.controlla_flash_sale()
        except Exception as e:
            log.warning(f"flash_sale skip: {e}")
            flash = []

        # Notifiche wishlist
        _controlla_wishlist()

        tot = sum(
            len(database.carica_categoria(n, c))
            for n, cfg in scraper.NEGOZI.items()
            for c in cfg["categorie"]
        )
        if _notifiche_ok:
            try:
                notifiche.invia_report_aggiornamento(tot_prodotti=tot, flash_count=len(flash))
            except Exception as e:
                log.warning(f"notifiche report skip: {e}")

        log.info(f"✅ Aggiornamento completato — {tot} prodotti, {len(flash)} flash sale")
    except Exception as e:
        log.error(f"❌ Errore aggiornamento: {e}")


def _controlla_wishlist():
    try:
        wishlist = database.carica_wishlist()
        for p in wishlist:
            if p.get("target_raggiunto") and p.get("prezzo_target"):
                tipo = "target"
                if not database.alert_gia_inviato(p["negozio"], p["nome"], tipo, ore=23):
                    if _notifiche_ok:
                        notifiche.notifica_target_raggiunto(
                            nome=p["nome"],
                            negozio=p["negozio"],
                            prezzo_attuale=p.get("prezzo", "N/D"),
                            prezzo_target=p["prezzo_target"],
                            link=p.get("link", ""),
                        )
                    database.registra_alert(p["negozio"], p["nome"], tipo)
    except Exception as e:
        log.warning(f"_controlla_wishlist skip: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(job_giornaliero, "cron", hour=UPDATE_HOUR, minute=0,
                      id="daily", replace_existing=True)
    if _notifiche_ok:
        scheduler.add_job(
            notifiche.poll_bot_updates,
            "interval",
            seconds=max(15, TELEGRAM_POLL_SECONDS),
            id="telegram-poll",
            replace_existing=True,
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
                    log.info("⏰ Aggiornamento mancato — eseguo ora...")
                    job_giornaliero()
            except Exception as e:
                log.warning(f"Errore check data: {e}")

    threading.Thread(target=_controlla_aggiornamento_mancato, daemon=True).start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Tracker Integratori API", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# HEAD aggiunto per UptimeRobot (piano free usa HEAD)
@app.api_route("/", methods=["GET", "HEAD"])
def root():
    job = scheduler.get_job("daily")
    prossimo = str(job.next_run_time)[:16] if job and job.next_run_time else "N/D"
    return {"status": "ok", "ultimo_aggiornamento": database.ultimo_aggiornamento(),
            "prossimo_aggiornamento": prossimo, "negozi": database.negozi_disponibili()}

@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"ok": True}

@app.api_route("/ping", methods=["GET", "HEAD"])
def ping():
    return {"status": "alive"}

@app.get("/api/negozi")
def get_negozi():
    return {"negozi": list(scraper.NEGOZI.keys()),
            "colori": {n: cfg["colore"] for n, cfg in scraper.NEGOZI.items()}}

@app.get("/api/categorie")
def get_categorie():
    return {"categorie": scraper.CATEGORIE}

@app.get("/api/prodotti/{negozio}/{categoria}")
def get_prodotti(negozio: str, categoria: str):
    if negozio not in scraper.NEGOZI:
        raise HTTPException(404, f"Negozio '{negozio}' non trovato")
    prodotti = database.carica_categoria(negozio, categoria)
    return {"negozio": negozio, "categoria": categoria,
            "prodotti": prodotti, "totale": len(prodotti)}

@app.get("/api/offerte")
def get_offerte(soglia: int = Query(default=0, ge=0, le=100),
                negozio: str = Query(default=None)):
    prodotti = database.offerte_convenienti(soglia, negozio if negozio else None)
    return {"soglia": soglia, "prodotti": prodotti, "totale": len(prodotti)}

@app.get("/api/flash")
def get_flash(soglia: int = Query(default=20), ore: int = Query(default=24)):
    try:
        return {"prodotti": database.flash_sale(soglia_calo=soglia, ore=ore)}
    except Exception:
        return {"prodotti": database.offerte_convenienti(soglia)}

@app.get("/api/storico/{negozio}/{nome}")
def get_storico(negozio: str, nome: str, limite: int = Query(default=30)):
    return {"storico": database.storico_prezzi_prodotto(negozio, nome, limite)}

@app.get("/api/grafico/{negozio}/{nome}")
def get_grafico_prodotto(negozio: str, nome: str, limite: int = Query(default=30, ge=3, le=180)):
    if not _matplotlib_ok:
        raise HTTPException(503, "matplotlib non disponibile")

    storico = list(reversed(database.storico_prezzi_prodotto(negozio, nome, limite)))
    if not storico:
        raise HTTPException(404, "Storico non disponibile per questo prodotto")

    punti = []
    labels = []
    for s in storico:
        prezzo = _parse_prezzo_to_float(s.get("prezzo_corrente"))
        data = (s.get("data") or "")[:16]
        if prezzo is None:
            continue
        punti.append(prezzo)
        labels.append(data)

    if len(punti) < 2:
        raise HTTPException(404, "Dati insufficienti per generare il grafico")

    fig, ax = plt.subplots(figsize=(10, 3.6), dpi=130)
    ax.plot(range(len(punti)), punti, marker="o", linewidth=2)
    ax.fill_between(range(len(punti)), punti, [min(punti)] * len(punti), alpha=0.15)
    ax.set_title(f"Andamento prezzo - {nome}")
    ax.set_xlabel("Rilevazioni")
    ax.set_ylabel("Prezzo (€)")
    step = max(1, len(labels) // 6)
    ticks = list(range(0, len(labels), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([labels[i] for i in ticks], rotation=25, ha="right", fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()

    from io import BytesIO
    buf = BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")

@app.get("/api/aggiornamento")
def get_aggiornamento():
    job = scheduler.get_job("daily")
    prossimo = str(job.next_run_time)[:16] if job and job.next_run_time else "N/D"
    return {"ultimo_aggiornamento": database.ultimo_aggiornamento(),
            "prossimo_aggiornamento": prossimo}

@app.api_route("/api/aggiorna", methods=["GET", "POST"])
def forza_aggiornamento(secret: str = Query(...)):
    if secret != ADMIN_SECRET:
        raise HTTPException(403, "Secret non valido")
    threading.Thread(target=job_giornaliero, daemon=True).start()
    return {"status": "aggiornamento avviato"}

@app.api_route("/api/telegram/test", methods=["GET", "POST"])
def telegram_test(secret: str = Query(...), msg: str = Query(default="✅ Test Telegram dal tracker")):
    if secret != ADMIN_SECRET:
        raise HTTPException(403, "Secret non valido")
    if not _notifiche_ok:
        raise HTTPException(503, "Modulo notifiche non disponibile")
    try:
        notifiche.send_to_all(msg)
    except Exception as e:
        raise HTTPException(500, f"Errore invio Telegram: {e}")
    return {"status": "ok", "messaggio": msg}

# ── WISHLIST ─────────────────────────────────────────────────
@app.get("/api/wishlist")
def get_wishlist():
    try:
        return {"prodotti": database.carica_wishlist()}
    except Exception:
        return {"prodotti": []}

@app.post("/api/wishlist")
def add_wishlist(negozio: str, categoria: str, nome: str,
                 prezzo_target: str = Query(default=""),
                 prezzo_attuale: str = Query(default=""),
                 immagine: str = Query(default=""),
                 link: str = Query(default="")):
    try:
        database.aggiungi_wishlist(negozio, categoria, nome, prezzo_target,
                                    prezzo_attuale, immagine, link)
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"status": "aggiunto"}

@app.delete("/api/wishlist")
def del_wishlist(negozio: str, nome: str):
    try:
        database.rimuovi_wishlist(negozio, nome)
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"status": "rimosso"}
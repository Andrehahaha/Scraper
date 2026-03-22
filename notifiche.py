"""
Modulo notifiche Telegram + alert flash sale.
Configura sul server Render le variabili:
  TELEGRAM_BOT_TOKEN  = token del bot (da @BotFather)
  TELEGRAM_CHAT_ID    = il tuo chat_id (da @userinfobot)
  SOGLIA_FLASH_SALE   = calo % minimo per alert (default 20)
"""
import os
import requests
import logging
from database import get_conn

log = logging.getLogger("notifiche")

BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
SOGLIA_FLASH = int(os.environ.get("SOGLIA_FLASH_SALE", "20"))


def _send(testo: str):
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Telegram non configurato — skip notifica")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": testo, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.error(f"Telegram error: {e}")


# ── WISHLIST / ALERT PREZZI ─────────────────────────────────

def aggiungi_wishlist(negozio: str, nome: str, prezzo_target: float):
    """Salva un prodotto nella wishlist con prezzo target."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wishlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                negozio TEXT, nome TEXT, prezzo_target REAL,
                notificato INTEGER DEFAULT 0,
                aggiunto_il TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO wishlist (negozio, nome, prezzo_target, notificato)
            VALUES (?, ?, ?, 0)
        """, (negozio, nome, prezzo_target))
        conn.commit()


def rimuovi_wishlist(negozio: str, nome: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM wishlist WHERE negozio=? AND nome=?", (negozio, nome))
        conn.commit()


def carica_wishlist() -> list:
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wishlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                negozio TEXT, nome TEXT, prezzo_target REAL,
                notificato INTEGER DEFAULT 0,
                aggiunto_il TEXT DEFAULT (datetime('now'))
            )
        """)
        rows = conn.execute("""
            SELECT w.negozio, w.nome, w.prezzo_target, w.notificato,
                   p.prezzo, p.sconto, p.immagine, p.link
            FROM wishlist w
            LEFT JOIN prodotti p ON p.negozio=w.negozio AND p.nome=w.nome
        """).fetchall()
    return [
        {"negozio": r[0], "nome": r[1], "prezzo_target": r[2],
         "notificato": bool(r[3]), "prezzo": r[4] or "N/D",
         "sconto": r[5] or "", "immagine": r[6] or "", "link": r[7] or ""}
        for r in rows
    ]


def controlla_wishlist():
    """Controlla se qualche prodotto in wishlist ha raggiunto il prezzo target."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT w.id, w.negozio, w.nome, w.prezzo_target, p.prezzo
            FROM wishlist w
            JOIN prodotti p ON p.negozio=w.negozio AND p.nome=w.nome
            WHERE w.notificato=0
        """).fetchall()

    for row in rows:
        wid, negozio, nome, target, prezzo_str = row
        try:
            prezzo_val = float(
                prezzo_str.replace("€","").replace(",",".").strip()
                if prezzo_str else "9999"
            )
        except Exception:
            continue

        if prezzo_val <= target:
            _send(
                f"🎯 <b>Prezzo target raggiunto!</b>\n"
                f"🏪 {negozio}\n"
                f"📦 {nome}\n"
                f"💰 {prezzo_str} (target: {target:.2f} €)"
            )
            with get_conn() as conn:
                conn.execute("UPDATE wishlist SET notificato=1 WHERE id=?", (wid,))
                conn.commit()
            log.info(f"✅ Notifica inviata: {nome} @ {prezzo_str}")


# ── FLASH SALE ───────────────────────────────────────────────

def controlla_flash_sale():
    """
    Confronta i prezzi attuali con lo storico.
    Se un prodotto è calato di SOGLIA_FLASH% rispetto all'ultima rilevazione → notifica.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT s.negozio, s.nome, s.prezzo_vecchio, s.prezzo_corrente,
                   s.variazione, p.link
            FROM storico_prezzi s
            JOIN prodotti p ON p.negozio=s.negozio AND p.nome=s.nome
            WHERE s.variazione='diminuito'
              AND s.data = (SELECT MAX(data) FROM storico_prezzi
                            WHERE negozio=s.negozio AND nome=s.nome)
        """).fetchall()

    for negozio, nome, vecchio_str, corrente_str, _, link in rows:
        try:
            def parse(s):
                return float(s.replace("€","").replace(",",".").strip())
            vecchio  = parse(vecchio_str)
            corrente = parse(corrente_str)
            if vecchio <= 0:
                continue
            calo_pct = (vecchio - corrente) / vecchio * 100
            if calo_pct >= SOGLIA_FLASH:
                _send(
                    f"🚨 <b>Flash Sale!</b>\n"
                    f"🏪 {negozio}\n"
                    f"📦 {nome}\n"
                    f"💰 {corrente_str} (era {vecchio_str})\n"
                    f"📉 -{round(calo_pct)}% rispetto a ieri"
                    + (f"\n🔗 {link}" if link else "")
                )
                log.info(f"🚨 Flash sale: {nome} -{round(calo_pct)}%")
        except Exception:
            continue


def esegui_tutti_i_check():
    """Chiamato dal job giornaliero dopo lo scraping."""
    log.info("🔔 Controllo notifiche...")
    controlla_wishlist()
    controlla_flash_sale()

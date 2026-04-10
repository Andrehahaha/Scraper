"""Notifiche Telegram + bot commands via long polling.

Variabili ambiente:
  TELEGRAM_BOT_TOKEN   token bot (BotFather)
  TELEGRAM_CHAT_ID     chat predefinita opzionale (compatibilità)
  SOGLIA_FLASH_SALE    soglia calo % flash sale (default 20)
"""
import os
import logging
from datetime import datetime

import requests

from database import flash_sale, get_conn

log = logging.getLogger("notifiche")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SOGLIA_FLASH = int(os.environ.get("SOGLIA_FLASH_SALE", "20"))
FLASH_DIGEST_MAX_ITEMS = int(os.environ.get("TELEGRAM_FLASH_DIGEST_MAX_ITEMS", "8"))

_BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
_OFFSET = 0


def _ensure_tables():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_subscribers (
                chat_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                registered_il TEXT NOT NULL
            )
        """)
        conn.commit()


def _telegram_ok() -> bool:
    return bool(BOT_TOKEN)


def _register_chat(chat_id: str):
    _ensure_tables()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO telegram_subscribers (chat_id, enabled, registered_il)
            VALUES (?, 1, ?)
            ON CONFLICT(chat_id) DO UPDATE SET enabled=1
            """,
            (chat_id, now),
        )
        conn.commit()


def _disable_chat(chat_id: str):
    _ensure_tables()
    with get_conn() as conn:
        conn.execute("UPDATE telegram_subscribers SET enabled=0 WHERE chat_id=?", (chat_id,))
        conn.commit()


def _active_chat_ids() -> list:
    ids = []
    if CHAT_ID:
        ids.append(str(CHAT_ID))
    _ensure_tables()
    with get_conn() as conn:
        rows = conn.execute("SELECT chat_id FROM telegram_subscribers WHERE enabled=1").fetchall()
    ids.extend(str(r[0]) for r in rows)
    return list(dict.fromkeys(ids))


def _send_to_chat(chat_id: str, testo: str):
    if not _telegram_ok():
        return
    try:
        requests.post(
            f"{_BASE_URL}/sendMessage",
            json={"chat_id": chat_id, "text": testo, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.error(f"Telegram sendMessage error: {e}")


def _send(testo: str):
    send_to_all(testo)


def send_to_all(testo: str):
    if not _telegram_ok():
        return
    for chat_id in _active_chat_ids():
        _send_to_chat(chat_id, testo)


def notifica_target_raggiunto(nome: str, negozio: str, prezzo_attuale: str, prezzo_target: str, link: str = ""):
    msg = (
        "🎯 <b>Prezzo target raggiunto</b>\n"
        f"🏪 {negozio}\n"
        f"📦 {nome}\n"
        f"💰 {prezzo_attuale} (target: {prezzo_target})"
    )
    if link:
        msg += f"\n🔗 {link}"
    send_to_all(msg)


def invia_report_aggiornamento(tot_prodotti: int, flash_count: int):
    msg = (
        "✅ <b>Aggiornamento scraper completato</b>\n"
        f"📦 Prodotti disponibili: {tot_prodotti}\n"
        f"⚡ Flash sale trovati: {flash_count}"
    )
    send_to_all(msg)


def _bot_status_text() -> str:
    with get_conn() as conn:
        tot_prodotti = conn.execute("SELECT COUNT(*) FROM prodotti").fetchone()[0]
        tot_wishlist = conn.execute("SELECT COUNT(*) FROM wishlist").fetchone()[0]
        ultimo = conn.execute("SELECT MAX(aggiornato_il) FROM prodotti").fetchone()[0] or "N/D"
    return (
        "📊 <b>Stato tracker</b>\n"
        f"📦 Prodotti: {tot_prodotti}\n"
        f"💾 Wishlist: {tot_wishlist}\n"
        f"🕒 Ultimo update: {str(ultimo)[:16]}"
    )


def _bot_flash_text() -> str:
    flash = flash_sale(soglia_calo=SOGLIA_FLASH, ore=25)
    if not flash:
        return "⚡ Nessun flash sale al momento."
    top = flash[:5]
    righe = ["⚡ <b>Flash sale</b>"]
    for p in top:
        righe.append(f"• {p['nome'][:45]} — -{p['calo_percentuale']}% ({p['prezzo_corrente']})")
    return "\n".join(righe)


def _help_text() -> str:
    return (
        "🤖 <b>Comandi disponibili</b>\n"
        "/start — abilita notifiche\n"
        "/stop — disabilita notifiche\n"
        "/status — stato scraper\n"
        "/flash — top flash sale\n"
        "/help — aiuto"
    )


def poll_bot_updates():
    global _OFFSET
    if not _telegram_ok():
        return
    try:
        r = requests.get(
            f"{_BASE_URL}/getUpdates",
            params={"offset": _OFFSET + 1, "timeout": 0, "allowed_updates": ["message"]},
            timeout=12,
        )
        data = r.json()
        if not data.get("ok"):
            return
        for upd in data.get("result", []):
            _OFFSET = max(_OFFSET, int(upd.get("update_id", 0)))
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id", "")).strip()
            testo = (msg.get("text") or "").strip().lower()
            if not chat_id or not testo.startswith("/"):
                continue

            if testo.startswith("/start"):
                _register_chat(chat_id)
                _send_to_chat(chat_id, "✅ Bot attivo. Riceverai notifiche su prezzi e flash sale.")
            elif testo.startswith("/stop"):
                _disable_chat(chat_id)
                _send_to_chat(chat_id, "⏸ Notifiche disattivate. Usa /start per riattivarle.")
            elif testo.startswith("/status"):
                _register_chat(chat_id)
                _send_to_chat(chat_id, _bot_status_text())
            elif testo.startswith("/flash"):
                _register_chat(chat_id)
                _send_to_chat(chat_id, _bot_flash_text())
            elif testo.startswith("/help"):
                _register_chat(chat_id)
                _send_to_chat(chat_id, _help_text())
    except Exception as e:
        log.warning(f"poll_bot_updates skip: {e}")


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
            send_to_all(
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

    hits = []
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
                hits.append({
                    "negozio": negozio,
                    "nome": nome,
                    "corrente_str": corrente_str,
                    "vecchio_str": vecchio_str,
                    "calo": round(calo_pct),
                    "link": link,
                })
        except Exception:
            continue

    if not hits:
        return

    hits.sort(key=lambda x: x["calo"], reverse=True)
    top = hits[:max(1, FLASH_DIGEST_MAX_ITEMS)]
    righe = [f"🚨 <b>Flash Sale ({len(hits)})</b>"]
    for idx, item in enumerate(top, 1):
        righe.append(
            f"{idx}. {item['nome'][:40]} — -{item['calo']}% ({item['corrente_str']})"
        )
    extra = len(hits) - len(top)
    if extra > 0:
        righe.append(f"… e altri {extra} prodotti")

    send_to_all("\n".join(righe))
    log.info(f"🚨 Flash sale digest inviato: {len(hits)} prodotti")


def esegui_tutti_i_check():
    """Chiamato dal job giornaliero dopo lo scraping."""
    log.info("🔔 Controllo notifiche...")
    controlla_wishlist()
    controlla_flash_sale()

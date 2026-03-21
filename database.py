import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "integratori.db")


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prodotti (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                negozio TEXT NOT NULL,
                categoria TEXT NOT NULL,
                nome TEXT NOT NULL,
                prezzo TEXT NOT NULL,
                prezzo_originale TEXT,
                sconto TEXT,
                sconto_percentuale INTEGER DEFAULT 0,
                immagine TEXT,
                link TEXT,
                aggiornato_il TEXT NOT NULL
            )
        """)
        # Tabella storico per tracciare variazioni prezzo
        conn.execute("""
            CREATE TABLE IF NOT EXISTS storico_prezzi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                negozio TEXT NOT NULL,
                nome TEXT NOT NULL,
                prezzo_corrente TEXT NOT NULL,
                prezzo_originale TEXT,
                prezzo_vecchio TEXT,
                variazione TEXT,
                data TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_negozio_cat ON prodotti(negozio, categoria)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sconto ON prodotti(sconto_percentuale)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_storico_nome ON storico_prezzi(negozio, nome)")
        conn.commit()


def _estrai_percentuale(s: str) -> int:
    if not s:
        return 0
    try:
        return int("".join(c for c in s if c.isdigit()) or "0")
    except ValueError:
        return 0


def _parse_prezzo(p: str) -> float:
    """Converte stringa prezzo in float per confronti."""
    if not p:
        return None
    try:
        return float(p.replace("€", "").replace(",", ".").strip())
    except:
        return None


def salva_categoria(negozio: str, categoria: str, prodotti: list):
    ora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        # Prima di cancellare, salviamo lo storico delle variazioni
        vecchi = conn.execute(
            "SELECT nome, prezzo, prezzo_originale FROM prodotti WHERE negozio=? AND categoria=?",
            (negozio, categoria)
        ).fetchall()
        vecchi_dict = {r[0]: (r[1], r[2]) for r in vecchi}

        # Inserisce nuovo storico per prodotti con variazione prezzo
        for p in prodotti:
            nome = p.get("nome", "")
            prezzo_nuovo = p.get("prezzo", "N/D")
            prezzo_orig_nuovo = p.get("prezzo_originale", "")

            if nome in vecchi_dict:
                prezzo_vecchio, prezzo_orig_vecchio = vecchi_dict[nome]

                # Confronta prezzi numerici
                p_new = _parse_prezzo(prezzo_nuovo)
                p_old = _parse_prezzo(prezzo_vecchio)

                if p_new is not None and p_old is not None and p_new != p_old:
                    if p_new < p_old:
                        variazione = "diminuito"
                    elif p_new > p_old:
                        variazione = "aumentato"
                    else:
                        variazione = "invariato"

                    conn.execute("""
                        INSERT INTO storico_prezzi
                            (negozio, nome, prezzo_corrente, prezzo_originale, prezzo_vecchio, variazione, data)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (negozio, nome, prezzo_nuovo, prezzo_orig_nuovo, prezzo_vecchio, variazione, ora))

        # Cancella e inserisce nuovi dati
        conn.execute(
            "DELETE FROM prodotti WHERE negozio=? AND categoria=?",
            (negozio, categoria)
        )
        conn.executemany("""
            INSERT INTO prodotti
                (negozio, categoria, nome, prezzo, prezzo_originale,
                 sconto, sconto_percentuale, immagine, link, aggiornato_il)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, [
            (
                negozio, categoria,
                p.get("nome", ""), p.get("prezzo", "N/D"),
                p.get("prezzo_originale", ""), p.get("sconto", ""),
                _estrai_percentuale(p.get("sconto", "")),
                p.get("immagine", ""), p.get("link", ""), ora,
            )
            for p in prodotti
        ])
        conn.commit()


def carica_categoria(negozio: str, categoria: str) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT nome, prezzo, prezzo_originale, sconto,
                   sconto_percentuale, immagine, link, aggiornato_il
            FROM prodotti WHERE negozio=? AND categoria=?
            ORDER BY sconto_percentuale DESC, nome ASC
        """, (negozio, categoria)).fetchall()
    return [
        {"nome": r[0], "prezzo": r[1], "prezzo_originale": r[2],
         "sconto": r[3], "sconto_percentuale": r[4],
         "immagine": r[5], "link": r[6], "aggiornato_il": r[7]}
        for r in rows
    ]


def carica_tutte_categorie(negozio: str) -> dict:
    with get_conn() as conn:
        cats = conn.execute(
            "SELECT DISTINCT categoria FROM prodotti WHERE negozio=?", (negozio,)
        ).fetchall()
    return {c[0]: carica_categoria(negozio, c[0]) for c in cats}


def offerte_convenienti(soglia: int = 51, negozio: str = None) -> list:
    with get_conn() as conn:
        if negozio:
            rows = conn.execute("""
                SELECT negozio, categoria, nome, prezzo, prezzo_originale,
                       sconto, sconto_percentuale, immagine, link, aggiornato_il
                FROM prodotti
                WHERE sconto_percentuale >= ? AND negozio = ?
                ORDER BY sconto_percentuale DESC, nome ASC
            """, (soglia, negozio)).fetchall()
        else:
            rows = conn.execute("""
                SELECT negozio, categoria, nome, prezzo, prezzo_originale,
                       sconto, sconto_percentuale, immagine, link, aggiornato_il
                FROM prodotti
                WHERE sconto_percentuale >= ?
                ORDER BY sconto_percentuale DESC, nome ASC
            """, (soglia,)).fetchall()
    return [
        {"negozio": r[0], "categoria": r[1], "nome": r[2], "prezzo": r[3],
         "prezzo_originale": r[4], "sconto": r[5], "sconto_percentuale": r[6],
         "immagine": r[7], "link": r[8], "aggiornato_il": r[9]}
        for r in rows
    ]


def ultimo_aggiornamento(negozio: str = None) -> str:
    with get_conn() as conn:
        if negozio:
            row = conn.execute(
                "SELECT MAX(aggiornato_il) FROM prodotti WHERE negozio=?", (negozio,)
            ).fetchone()
        else:
            row = conn.execute("SELECT MAX(aggiornato_il) FROM prodotti").fetchone()
    return row[0] if row and row[0] else None


def negozi_nel_db() -> list:
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT negozio FROM prodotti").fetchall()
    return [r[0] for r in rows]


def storico_variazioni(negozio: str = None, limite: int = 50) -> list:
    """Restituisce le ultime variazioni di prezzo."""
    with get_conn() as conn:
        if negozio:
            rows = conn.execute("""
                SELECT negozio, nome, prezzo_corrente, prezzo_originale,
                       prezzo_vecchio, variazione, data
                FROM storico_prezzi
                WHERE negozio = ?
                ORDER BY data DESC
                LIMIT ?
            """, (negozio, limite)).fetchall()
        else:
            rows = conn.execute("""
                SELECT negozio, nome, prezzo_corrente, prezzo_originale,
                       prezzo_vecchio, variazione, data
                FROM storico_prezzi
                ORDER BY data DESC
                LIMIT ?
            """, (limite,)).fetchall()
    return [
        {"negozio": r[0], "nome": r[1], "prezzo_corrente": r[2],
         "prezzo_originale": r[3], "prezzo_vecchio": r[4],
         "variazione": r[5], "data": r[6]}
        for r in rows
    ]


def variazioni_per_marca(negozio: str, giorni: int = 7) -> list:
    """Restituisce le variazioni di prezzo per una marca negli ultimi N giorni."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT nome, prezzo_corrente, prezzo_originale,
                   prezzo_vecchio, variazione, data
            FROM storico_prezzi
            WHERE negozio = ?
            ORDER BY data DESC
        """, (negozio,)).fetchall()
    return [
        {"nome": r[0], "prezzo_corrente": r[1], "prezzo_originale": r[2],
         "prezzo_vecchio": r[3], "variazione": r[4], "data": r[5]}
        for r in rows
    ]


def storico_prezzi_prodotto(neg: str, nome: str, limite: int = 50) -> list:
    """Restituisce lo storico prezzi per un prodotto specifico."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT prezzo_corrente, prezzo_originale, prezzo_vecchio,
                   variazione, data
            FROM storico_prezzi
            WHERE negozio = ? AND nome = ?
            ORDER BY data DESC
            LIMIT ?
        """, (neg, nome, limite)).fetchall()
    return [
        {"prezzo_corrente": r[0], "prezzo_originale": r[1],
         "prezzo_vecchio": r[2], "variazione": r[3], "data": r[4]}
        for r in rows
    ]
def get_conn():
    return sqlite3.connect(DB_PATH)

def negozi_disponibili() -> list:
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT negozio FROM prodotti").fetchall()
    return [r[0] for r in rows]
init_db()

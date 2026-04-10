# Tracker Integratori - Backend API

Backend Python per monitorare prezzi di integratori su piu negozi, salvare storico in SQLite e inviare notifiche Telegram su ribassi e target wishlist.

## Elevator Pitch

Progetto personale orientato a prodotto reale: scraping periodico, normalizzazione dati, storage storico e API pronte per app mobile.

## Cosa Fa

- Esegue scraping per categorie e negozi supportati.
- Salva storico prezzi e rileva variazioni giornaliere.
- Espone API REST per catalogo, wishlist, grafici e controllo aggiornamenti.
- Invia notifiche Telegram per flash sale e prezzi target raggiunti.
- Genera grafici PNG dell'andamento prezzo per prodotto.

## Stack Tecnologico

- Python
- FastAPI
- APScheduler
- SQLite
- BeautifulSoup + requests + curl_cffi
- Telegram Bot API
- Deploy su Render

## Architettura (Sintesi)

- `scraper.py`: raccolta e parsing prodotti per negozio/categoria.
- `database.py`: persistenza SQLite, storico prezzi e query di analisi.
- `app.py`: API FastAPI, scheduler job giornaliero e endpoint amministrativi.
- `notifiche.py`: integrazione Telegram e comandi bot.

## Endpoint Principali

- `GET /api/prodotti/{negozio}/{categoria}`
- `GET /api/wishlist`
- `POST /api/wishlist`
- `DELETE /api/wishlist`
- `GET /api/grafico/{negozio}/{nome}?limite=30`
- `GET|POST /api/aggiorna?secret=...` (admin)
- `GET|POST /api/telegram/test?secret=...` (admin)

## Sicurezza e Configurazione

- `ADMIN_SECRET` obbligatoria: senza valore l'app non parte.
- `BULK_API_KEY` opzionale: abilita scraping Bulk; se assente Bulk viene saltato in modo sicuro.
- Il file SQLite `integratori.db` e runtime-generated e non deve essere versionato.

Variabili d'ambiente principali:

- `ADMIN_SECRET`
- `BULK_API_KEY`
- `UPDATE_HOUR`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_POLL_SECONDS`
- `SOGLIA_FLASH_SALE`

## Esecuzione Locale

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

## Deploy

Deploy pensato per Render tramite `render.yaml`.

## Nota CV

Questo progetto dimostra competenze in:

- backend API design
- scraping e data pipeline leggere
- schedulazione job e automazione
- integrazione servizi esterni (Telegram)
- gestione configurazioni e hardening base sicurezza

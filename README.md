# Tracker Integratori — Guida Deploy

## Struttura
```
server/   → va su Render.com (gratis)
mobile/   → diventa APK Android con "flet build apk"
```

---

## 1. Deploy Server su Render

1. Crea account su https://render.com
2. Crea un repository GitHub con la cartella `server/`
3. Su Render: New → Web Service → collega il repo
4. Render legge `render.yaml` automaticamente
5. Dopo il deploy copia l'URL (es. https://tracker-integratori.onrender.com)

> ⚠ Il piano free di Render si "addormenta" dopo 15 min di inattività.
> Per tenerlo sveglio gratis usa https://uptimerobot.com (ping ogni 5 min).

---

## 2. Configura l'app mobile

Apri `mobile/main.py` e cambia questa riga con l'URL del tuo server:
```python
API_URL = "https://tracker-integratori.onrender.com"  # <-- il tuo URL
```

---

## 3. Build APK Android

```bash
cd mobile
pip install flet
flet build apk
```

L'APK si trova in `build/apk/app-release.apk`.
Trasferiscilo sul telefono e installalo (abilita "Sorgenti sconosciute" nelle impostazioni).

> Per iOS serve un Mac con Xcode: `flet build ipa`

---

## 4. Aggiornamento forzato manuale

Il server si aggiorna automaticamente ogni giorno alle 03:00.
Per forzare un aggiornamento manuale:
```
POST https://tuo-server.onrender.com/api/aggiorna?secret=LA_TUA_PASSWORD
```
La password si trova nelle variabili d'ambiente su Render (ADMIN_SECRET).

---

## 5. Bot Telegram (notifiche + comandi)

Imposta su Render queste variabili:

- `TELEGRAM_BOT_TOKEN` = token del bot creato con @BotFather
- `TELEGRAM_CHAT_ID` = opzionale, chat fissa legacy
- `TELEGRAM_POLL_SECONDS` = intervallo polling comandi (default `60`)
- `SOGLIA_FLASH_SALE` = soglia flash in % (default `20`)

Comandi supportati nel bot:

- `/start` abilita notifiche per la chat
- `/stop` disabilita notifiche
- `/status` stato database/scraper
- `/flash` top flash sale
- `/help` aiuto

Test rapido invio messaggio Telegram:

`POST /api/telegram/test?secret=LA_TUA_PASSWORD&msg=Test%20ok`

---

## 6. Grafico prezzi reale (PNG)

Endpoint API:

`GET /api/grafico/{negozio}/{nome}?limite=30`

Esempio:

`https://tuo-server.onrender.com/api/grafico/Tsunami/ISO%20WHEY%20908g`

Restituisce un'immagine PNG con andamento storico del prezzo.

---

## Aggiungere Prozis/Bulk/MyProtein

Questi siti usano JavaScript per caricare i prodotti.
Sul server free non si può usare Selenium.
Soluzioni:
- Trovare le loro API JSON interne (come fatto con Tsunami)
- Usare un server a pagamento con Selenium

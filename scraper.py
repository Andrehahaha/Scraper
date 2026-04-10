import requests
from bs4 import BeautifulSoup
import time
import random
import uuid
import json
import re
import os
from curl_cffi import requests as curl_requests

# ============================================================
# CONFIGURAZIONE NEGOZI E SELETTORI
# ============================================================

NEGOZI = {
    "Tsunami": {
        "colore": "#E53935",
        "usa_api": False,
        "categorie": {
            "Proteine in polvere":   "https://www.tsunaminutrition.it/445-proteine-in-polvere",
            "Barrette proteiche":    "https://www.tsunaminutrition.it/23-barrette-proteiche-e-snack-proteici",
            "BCAA":                  "https://www.tsunaminutrition.it/24-aminoacidi-ramificati-bcaa",
            "Creatina":              "https://www.tsunaminutrition.it/121-creatina",
            "Pre Workout":           "https://www.tsunaminutrition.it/40-pre-workout",
        },
        "selettori": {
            "box":              "article.product-miniature",
            "nome":             "h2.product-miniature__title a",
            "prezzo":           "span.price:not(.strikethrough)",
            "prezzo_originale": "span.price.strikethrough",
            "sconto":           ".product-flags__flag--discount",
            "immagine":         "img[data-src]",
            "immagine_attr":    "data-src",
            "link":             "h2.product-miniature__title a",
        },
    },

    "Bulk": {
        "colore": "#8BC34A",
        "usa_api": True,  
        "api_key": os.environ.get("BULK_API_KEY", ""),
        "group_ids": {
            "Proteine in polvere":   "105",
            "Barrette proteiche":    "130",
            "BCAA":                  "153",
            "Creatina":              "74",
            "Pre Workout":           "164",
        },
        "categorie": {
            "Proteine in polvere":   "https://www.bulk.com/it/proteine",
            "Barrette proteiche":    "https://www.bulk.com/it/snack-proteici",
            "BCAA":                  "https://www.bulk.com/it/alimentazione-sportiva/bcaa",
            "Creatina":              "https://www.bulk.com/it/alimentazione-sportiva/creatina",
            "Pre Workout":           "https://www.bulk.com/it/alimentazione-sportiva/pre-workout",
        }
    },

    "MyProtein": {
        "colore": "#00BCD4",
        "usa_api": False,
        "categorie": {
            "Proteine in polvere":   "https://www.myprotein.it/c/nutrition/protein/",
            "Barrette proteiche":    "https://www.myprotein.it/c/nutrition/healthy-food-drinks/protein-bars/",
            "BCAA":                  "https://www.myprotein.it/c/nutrition/amino-acids/bcaa/",
            "Creatina":              "https://www.myprotein.it/c/nutrition/creatine/",
            "Pre Workout":           "https://www.myprotein.it/c/nutrition/pre-post-workout/pre-workout/",
        },
        "selettori": {
            "box":              ".product-card",
            "nome":             ".product-item-title, a[data-title]",
            "prezzo":           "[class*='price'], span[aria-label*='€']",
        },
    },

    "Prozis": {
        "colore": "#FF9800",
        "usa_api": False,
        "categorie": {
            "Proteine in polvere":   "https://www.prozis.com/it/it/nutrizione-sportiva/proteine",
            "Barrette proteiche":    "https://www.prozis.com/it/it/nutrizione-sportiva/proteine/barrette-proteiche",
            "BCAA":                  "https://www.prozis.com/it/it/nutrizione-sportiva/aumento-della-massa-muscolare/bcaa",
            "Creatina":              "https://www.prozis.com/it/it/nutrizione-sportiva/aumento-della-massa-muscolare/creatina",
            "Pre Workout":           "https://www.prozis.com/it/it/nutrizione-sportiva/pre-intra-e-post-workout/pre-workout-e-ossido-nitrico",
        },
        "selettori": {
            "box":              ".product-item",
            "nome":             "[class*='product-name']",
            "prezzo":           "[class*='price-current']",
        },
    }
}

CATEGORIE = list(next(iter(NEGOZI.values()))["categorie"].keys())

# ============================================================
# API BULK (LOGICA SCONTI INFALLIBILE)
# ============================================================
def scrapa_bulk_api(categoria: str) -> list:
    cfg = NEGOZI["Bulk"]
    group_id = cfg["group_ids"].get(categoria)
    if not group_id: return []
    if not cfg.get("api_key"):
        print("  ⚠ BULK_API_KEY non impostata: scraping Bulk disattivato")
        return []

    prodotti_tutti = []
    page = 1
    per_page = 50 # Limite anti-lag Flet
    base_url = "https://ac.cnstrc.com/browse/group_id/{group_id}"

    headers_api = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.bulk.com/",
        "Origin": "https://www.bulk.com"
    }

    finto_client_id = str(uuid.uuid4())

    while True:
        params = {
            "c": "ciojs-client-bundled-2.64.3",
            "key": cfg["api_key"], 
            "i": finto_client_id,
            "s": "1",
            "page": page,
            "num_results_per_page": per_page,
            "sort_by": "relevance",
            "sort_order": "descending",
            "_dt": int(time.time() * 1000)
        }
        
        try:
            r = requests.get(base_url.format(group_id=group_id), params=params, headers=headers_api, timeout=15)
            if r.status_code != 200: break

            data = r.json()
            results = data.get("response", {}).get("results", [])
            if not results: break

            for item in results:
                d = item.get("data", {})
                nome = d.get("product_name", "")
                if not nome: continue

                # LOGICA MIN/MAX INFALLIBILE PER I PREZZI
                # Bulk usa: regular_price = prezzo pieno, special_price = prezzo scontato
                prezzo_corrente = d.get("special_price") or d.get("price")
                prezzo_originale = d.get("regular_price") or d.get("list_price") or d.get("rrp") or d.get("original_price")

                if prezzo_corrente:
                    try:
                        prezzo_corrente = float(prezzo_corrente)
                        prezzo_str = f"{prezzo_corrente:.2f} €"

                        if prezzo_originale:
                            prezzo_originale = float(prezzo_originale)
                            prezzo_orig_str = f"{prezzo_originale:.2f} €"
                            sconto_val = round(((prezzo_originale - prezzo_corrente) / prezzo_originale) * 100)
                            sconto = f"-{sconto_val}%"
                        else:
                            prezzo_orig_str = ""
                            sconto = ""
                    except:
                        prezzo_str = f"{prezzo_corrente} €"
                        prezzo_orig_str = ""
                        sconto = ""
                else:
                    prezzo_str = "N/D"
                    prezzo_orig_str = ""
                    sconto = ""

                prodotti_tutti.append({
                    "nome": nome,
                    "prezzo": prezzo_str,
                    "prezzo_originale": prezzo_orig_str,
                    "sconto": sconto,
                    "immagine": d.get("image_url", ""),
                    "link": d.get("url", "")
                })

            total = data.get("response", {}).get("total_num_results", 0)
            if page * per_page >= total: break
            page += 1

        except Exception as e:
            print(f"  ❌ Errore API Bulk: {e}")
            break

    return prodotti_tutti

# ============================================================
# SCRAPING MYPROTEIN (STEALTH CURL_CFFI)
# ============================================================
def scrapa_myprotein(categoria: str) -> list:
    cfg = NEGOZI["MyProtein"]
    url = cfg["categorie"].get(categoria)
    if not url: return []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9",
        "Referer": "https://www.myprotein.it/",
    }

    try:
        # Falsifichiamo l'impronta di Chrome
        r = curl_requests.get(url, headers=headers, impersonate="chrome120", timeout=15)
        if r.status_code != 200:
            return []

        soup = BeautifulSoup(r.text, 'html.parser')
        prodotti = []

        # Nuovo selettore: product-card (layout aggiornato 2025)
        boxes = soup.select(".product-card")

        for box in boxes:
            # Nome: product-item-title o data-title attribute
            nome_tag = box.select_one(".product-item-title")
            nome = None
            if nome_tag:
                nome = nome_tag.get_text(strip=True)
            else:
                # Fallback: data-title attribute sul link
                link = box.select_one("a[data-title]")
                if link:
                    nome = link.get("data-title", "").strip()

            if not nome:
                continue

            # Prezzo: estrai dal testo della card (layout 2025 ha text nodes)
            testo_prezzo = box.get_text(strip=True)

            # Pattern: "discounted price 22,49 € Prima 44,99 € Risparmia 22,50 €"
            prezzo = ""
            prezzo_orig = ""

            # Cerca prezzo corrente (prima di "Prima" o primo prezzo trovato)
            match_prezzo = re.search(r'(\d{1,3}(?:,\d{2})?)\s*€', testo_prezzo)
            if match_prezzo:
                prezzo = match_prezzo.group(1).replace(',', '.') + " €"

            # Cerca prezzo originale (dopo "Prima")
            match_orig = re.search(r'Prima\s*(\d{1,3}(?:,\d{2})?)\s*€', testo_prezzo)
            if match_orig:
                prezzo_orig = match_orig.group(1).replace(',', '.') + " €"

            # Calcolo sconto
            sconto_testo = ""
            if prezzo and prezzo_orig:
                try:
                    p_val = float(re.sub(r'[^\d.]', '', prezzo.replace(',', '.')))
                    o_val = float(re.sub(r'[^\d.]', '', prezzo_orig.replace(',', '.')))
                    if o_val > p_val:
                        sconto_testo = f"-{round((o_val - p_val)/o_val * 100)}%"
                except: pass

            # Immagine: data-primary-src o img src
            img = box.select_one("img")
            immagine = ""
            if img:
                immagine = img.get("src") or img.get("data-src") or ""
            else:
                link = box.select_one("a[data-primary-src]")
                if link:
                    immagine = link.get("data-primary-src", "")

            # Link
            link = box.select_one("a[href]")
            link_url = ""
            if link:
                href = link.get("href")
                if href:
                    link_url = "https://www.myprotein.it" + href if href.startswith("/") else href

            prodotti.append({
                "nome": nome,
                "prezzo": prezzo if prezzo else "N/D",
                "prezzo_originale": prezzo_orig,
                "sconto": sconto_testo,
                "immagine": immagine,
                "link": link_url
            })

        # Limite a 50 per evitare lag Flet
        return prodotti[:50]
    except Exception as e:
        print(f"  ❌ Errore MyProtein: {e}")
        return []

# ============================================================
# SCRAPING PROZIS (CURL_CFFI STEALTH CON RETRY E FIX IMG)
# ============================================================
def scrapa_prozis(categoria: str) -> list:
    cfg = NEGOZI["Prozis"]
    url = cfg["categorie"].get(categoria)
    if not url: return []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9",
        "Referer": "https://www.prozis.com/it/it",
    }
    
    for tentativo in range(3):
        try:
            r = curl_requests.get(url, headers=headers, impersonate="chrome120", timeout=15)
            if r.status_code == 429 or r.status_code != 200:
                time.sleep(2)
                continue

            match = re.search(r"VueEs6\.render\('#catalog-desktop',\s*'ComponentLoader',\s*(\{.*?\})\s*\);", r.text, re.DOTALL)
            if not match:
                time.sleep(2)
                continue

            dati_nascosti = json.loads(match.group(1))
            prodotti = []
            risultati = dati_nascosti.get("props", {}).get("compProps", {}).get("catalogData", {}).get("wsData", {}).get("results", [])
            
            for item in risultati:
                prod = item.get("product", {})
                nome = prod.get("name")
                prezzo = prod.get("price")
                prezzo_crossed = prod.get("priceCrossed") 
                label = prod.get("label") 
                
                if nome and prezzo:
                    prezzo_pulito = prezzo.replace("€", "").strip()
                    
                    sconto_testo = ""
                    if label and "span" in label:
                        match_sconto = re.search(r'<span>(.*?)<\/span>', label)
                        if match_sconto:
                            sconto_testo = match_sconto.group(1).strip()
                            if not sconto_testo.startswith("-") and "%" in sconto_testo:
                                sconto_testo = "-" + sconto_testo

                    images = prod.get("imagesHover", []) or prod.get("images", [])
                    img_url = images[0].get("url") if images else ""
                    if img_url and img_url.startswith("//"):
                        img_url = "https:" + img_url

                    prodotti.append({
                        "nome": nome.strip(),
                        "prezzo": f"{prezzo_pulito} €",
                        "prezzo_originale": prezzo_crossed.replace("€", "").strip() + " €" if prezzo_crossed else "",
                        "sconto": sconto_testo,
                        "immagine": img_url,
                        "link": "https://www.prozis.com" + prod.get("url", "")
                    })
            
            if prodotti:
                return prodotti[:50] # Taglio a 50 per evitare lag

        except Exception as e:
            time.sleep(2)
            
    return []

# ============================================================
# SCRAPING CLASSICO CON REQUESTS (TSUNAMI)
# ============================================================
def _headers(negozio: str) -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    }

def _trova(tag, sel_str):
    for sel in [s.strip() for s in sel_str.split(",")]:
        try:
            r = tag.select_one(sel)
            if r: return r
        except: pass
    return None

def scrapa_con_requests(negozio: str, categoria: str) -> list:
    cfg = NEGOZI[negozio]
    url = cfg["categorie"].get(categoria, "")
    if not url: return []
    
    try:
        session = requests.Session()
        r = session.get(url, headers=_headers(negozio), timeout=12)
        if r.status_code != 200:
            return []
            
        soup = BeautifulSoup(r.content, "html.parser")
        boxes = soup.select(cfg["selettori"]["box"])
        
        prodotti = []
        for box in boxes:
            nome_tag = _trova(box, cfg["selettori"]["nome"])
            prezzo_tag = _trova(box, cfg["selettori"]["prezzo"])
            
            prezzo_orig_tag = _trova(box, cfg["selettori"]["prezzo_originale"])
            sconto_tag = _trova(box, cfg["selettori"]["sconto"])
            img_tag = _trova(box, cfg["selettori"]["immagine"])
            
            if nome_tag and prezzo_tag:
                prodotti.append({
                    "nome": nome_tag.get_text(strip=True),
                    "prezzo": prezzo_tag.get_text(strip=True),
                    "prezzo_originale": prezzo_orig_tag.get_text(strip=True) if prezzo_orig_tag else "",
                    "sconto": sconto_tag.get_text(strip=True) if sconto_tag else "",
                    "immagine": img_tag.get(cfg["selettori"]["immagine_attr"]) if img_tag else "",
                })
        return prodotti
    except Exception as e:
        return []

# ============================================================
# MOTORE PRINCIPALE (ROUTER)
# ============================================================
def scrapa_categoria(negozio: str, categoria: str) -> list:
    cfg = NEGOZI.get(negozio)
    if not cfg: return []
    
    if negozio == "Bulk":
        return scrapa_bulk_api(categoria)
    elif negozio == "MyProtein":
        return scrapa_myprotein(categoria)
    elif negozio == "Prozis":
        return scrapa_prozis(categoria)
    else:
        return scrapa_con_requests(negozio, categoria)

def ottieni_tutte_categorie(negozio: str, callback=None) -> dict:
    risultati = {}
    totale = len(CATEGORIE)
    
    for i, categoria in enumerate(CATEGORIE, start=1):
        print(f"📡 {negozio} ({i}/{totale}) {categoria}...")
        prodotti = scrapa_categoria(negozio, categoria)
        risultati[categoria] = prodotti
        
        if callback:
            callback(categoria, prodotti, i, totale)
        
        # Pause Anti-Ban per i server
        if NEGOZI[negozio].get("usa_api"):
            time.sleep(0.5)
        elif negozio == "Prozis" or negozio == "MyProtein":
            attesa = random.uniform(3.5, 6.0)
            time.sleep(attesa)
        else:
            time.sleep(1.5)
            
    return risultati

if __name__ == "__main__":
    tutti = ottieni_tutte_categorie("MyProtein")
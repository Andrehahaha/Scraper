import flet as ft
import threading
import re
from scraper import NEGOZI, CATEGORIE, ottieni_tutte_categorie
from database import (
    carica_categoria, offerte_convenienti, ultimo_aggiornamento,
    salva_categoria, storico_prezzi_prodotto, get_conn,
)
from notifiche import (
    carica_wishlist, aggiungi_wishlist, rimuovi_wishlist,
)

SOGLIA_DEFAULT = 0
COLORI_NEGOZI = {n: cfg["colore"] for n, cfg in NEGOZI.items()}


# ── STATS ────────────────────────────────────────────────────
def stats_prodotti_per_negozio():
    with get_conn() as conn:
        rows = conn.execute("SELECT negozio, COUNT(*) FROM prodotti GROUP BY negozio").fetchall()
    return {r[0]: r[1] for r in rows}

def stats_sconto_medio_per_categoria(negozio=None):
    with get_conn() as conn:
        if negozio:
            rows = conn.execute("""SELECT categoria, AVG(sconto_percentuale) FROM prodotti
                WHERE negozio=? AND sconto_percentuale>0 GROUP BY categoria""", (negozio,)).fetchall()
        else:
            rows = conn.execute("""SELECT categoria, AVG(sconto_percentuale) FROM prodotti
                WHERE sconto_percentuale>0 GROUP BY categoria""").fetchall()
    return {r[0]: round(r[1],1) for r in rows if r[1]}

def stats_offerte_per_fascia(negozio=None):
    with get_conn() as conn:
        q = "SELECT sconto_percentuale FROM prodotti WHERE sconto_percentuale>0"
        params = (negozio,) if negozio else ()
        if negozio: q += " AND negozio=?"
        rows = conn.execute(q, params).fetchall()
    fasce = {"0-25%":0,"25-50%":0,"50-75%":0,"75%+":0}
    for (s,) in rows:
        if s<25: fasce["0-25%"]+=1
        elif s<50: fasce["25-50%"]+=1
        elif s<75: fasce["50-75%"]+=1
        else: fasce["75%+"]+=1
    return fasce

def cerca_prodotti(query: str, negozio=None, limite=50) -> list:
    q = query.lower().strip()
    with get_conn() as conn:
        sql = "SELECT negozio,categoria,nome,prezzo,prezzo_originale,sconto,sconto_percentuale,immagine,link FROM prodotti WHERE LOWER(nome) LIKE ?"
        params = [f"%{q}%"]
        if negozio:
            sql += " AND negozio=?"
            params.append(negozio)
        sql += " ORDER BY sconto_percentuale DESC LIMIT ?"
        params.append(limite)
        rows = conn.execute(sql, params).fetchall()
    return [{"negozio":r[0],"categoria":r[1],"nome":r[2],"prezzo":r[3],
             "prezzo_originale":r[4],"sconto":r[5],"sconto_percentuale":r[6],
             "immagine":r[7],"link":r[8]} for r in rows]


def main(page: ft.Page):
    page.title = "Tracker Integratori"
    page.theme_mode = ft.ThemeMode.DARK
    page.window_width = 440
    page.window_height = 870
    page.padding = 0
    page.scroll = None

    stato = {
        "negozio": list(NEGOZI.keys())[0] if NEGOZI else "Tutti",
        "categoria": "🔥 Offerte",
        "soglia": SOGLIA_DEFAULT,
        "vista": "prodotti",
    }
    cache = {}

    # ── WIDGETS ──────────────────────────────────────────────
    lista_ui     = ft.ListView(expand=True, spacing=8, padding=10, item_extent=95)
    grafici_ui   = ft.ListView(expand=True, spacing=16, padding=16)
    wishlist_ui  = ft.ListView(expand=True, spacing=8, padding=10)
    ricerca_ui   = ft.ListView(expand=True, spacing=8, padding=ft.Padding.only(top=8))

    testo_stato    = ft.Text("", italic=True, size=12, color="#888888")
    testo_prossimo = ft.Text("", italic=True, size=11, color="#555555")
    progress_bar   = ft.ProgressBar(visible=False, color="#5C6BC0", bgcolor="#1A1A2E")
    riga_negozi    = ft.Row(spacing=0, alignment="start")
    riga_categorie = ft.ListView(horizontal=True, height=40, spacing=6,
                                  padding=ft.Padding.only(left=12, right=12))

    label_soglia = ft.Text(f"{SOGLIA_DEFAULT}%", size=13, color="#FF6B6B", weight="bold", width=42)
    def on_slider(e):
        stato["soglia"] = int(e.control.value)
        label_soglia.value = f"{stato['soglia']}%"
        _ricarica_offerte(); page.update()
    slider_soglia = ft.Slider(min=0,max=90,divisions=18,value=SOGLIA_DEFAULT,
                               label="{value}%",active_color="#5C6BC0",expand=True,
                               on_change_end=on_slider)
    barra_soglia = ft.Container(visible=False,
        padding=ft.Padding.only(left=8,right=8,top=4,bottom=4),
        content=ft.Row([ft.Text("Sconti oltre:", size=12, color="#AAAAAA"),
                        slider_soglia, label_soglia], spacing=6))

    # Barra di ricerca
    campo_ricerca = ft.TextField(
        hint_text="🔍  Cerca prodotto...", height=40, border_radius=20,
        border_color="#2A2A3E", focused_border_color="#5C6BC0",
        text_size=13, content_padding=ft.Padding.only(left=16, right=8, top=8, bottom=8),
        on_submit=lambda e: _esegui_ricerca(e.control.value),
        suffix=ft.TextButton("🔍", on_click=lambda e: _esegui_ricerca(campo_ricerca.value),
                             style=ft.ButtonStyle(padding=4)),
    )
    barra_ricerca = ft.Container(
        visible=False, padding=ft.Padding.only(left=10, right=10, top=6, bottom=4),
        content=campo_ricerca)

    corpo = ft.Column(expand=True, spacing=0, controls=[barra_soglia, lista_ui])

    # ── HELPERS ──────────────────────────────────────────────
    def _parse_prezzo(s: str) -> float:
        try:
            return float(re.sub(r"[^\d.,]","",s).replace(",",".") or "0")
        except: return 0.0

    def _chiudi(dlg):
        dlg.open = False; page.update()

    # ── STORICO ──────────────────────────────────────────────
    def mostra_storico(nome, negozio):
        storico = storico_prezzi_prodotto(negozio, nome, limite=20)
        rows = [ft.Text("Nessuno storico.", size=12, color="#888")] if not storico else [
            ft.Text(f"{s['data'][:10]}: {s['prezzo_vecchio']} → {s['prezzo_corrente']} "
                    f"{'↓' if s['variazione']=='diminuito' else '↑' if s['variazione']=='aumentato' else '–'}",
                    color="#4CAF50" if s['variazione']=='diminuito' else
                           "#F44336" if s['variazione']=='aumentato' else "#888",
                    size=12) for s in storico
        ]

        # Mini grafico a barre
        prezzi = [_parse_prezzo(s["prezzo_corrente"]) for s in storico if s.get("prezzo_corrente")]
        grafico = ft.Container()
        if len(prezzi) > 1:
            max_p = max(prezzi) or 1
            barre = [ft.Container(
                width=12, height=max(4, int(60 * p / max_p)),
                bgcolor="#5C6BC0" if p > min(prezzi) else "#4CAF50",
                border_radius=2,
                tooltip=f"{p:.2f} €",
            ) for p in prezzi[-12:]]
            grafico = ft.Container(
                height=70, padding=ft.Padding.only(top=8),
                content=ft.Row(barre, spacing=3, vertical_alignment="end"))

        dlg = ft.AlertDialog(
            title=ft.Text(f"📈 {nome[:35]}", size=13, weight="bold"),
            content=ft.Column([grafico] + rows, scroll="always", tight=True, height=250),
            actions=[ft.TextButton("Chiudi", on_click=lambda e: _chiudi(dlg))],
        )
        page.overlay.append(dlg); dlg.open = True; page.update()

    # ── WISHLIST DIALOG ──────────────────────────────────────
    def mostra_dialogo_wishlist(p):
        negozio = p.get("negozio", stato["negozio"])
        nome = p.get("nome","")
        prezzo_attuale = _parse_prezzo(p.get("prezzo","0"))
        campo_target = ft.TextField(
            label="Prezzo target (€)", value=str(round(prezzo_attuale * 0.9, 2)),
            keyboard_type=ft.KeyboardType.NUMBER, width=180,
        )
        def _salva(e):
            try:
                target = float(campo_target.value.replace(",","."))
                aggiungi_wishlist(negozio, nome, target)
                dlg.open = False
                testo_stato.value = f"✅ Aggiunto alla wishlist: {nome[:30]}"
                if stato["vista"] == "wishlist":
                    aggiorna_wishlist_ui()
                page.update()
            except Exception as ex:
                campo_target.error_text = "Prezzo non valido"
                page.update()
        dlg = ft.AlertDialog(
            title=ft.Text("💾 Aggiungi alla wishlist", size=14, weight="bold"),
            content=ft.Column([
                ft.Text(nome[:45], size=12, color="#AAAAAA"),
                ft.Text(f"Prezzo attuale: {p.get('prezzo','N/D')}", size=13, color="#4CAF50"),
                campo_target,
                ft.Text("Riceverai una notifica Telegram quando\nil prezzo scende sotto il target.",
                        size=11, color="#666666"),
            ], spacing=10, tight=True),
            actions=[
                ft.TextButton("Annulla", on_click=lambda e: _chiudi(dlg)),
                ft.Button("Salva", on_click=_salva, bgcolor="#5C6BC0", color="white"),
            ],
        )
        page.overlay.append(dlg); dlg.open = True; page.update()

    # ── CARD ─────────────────────────────────────────────────
    def build_card(p, badge_negozio=False, badge_categoria=False, in_wishlist=False):
        img_url = p.get("immagine","")
        if img_url and img_url.startswith("//"): img_url = "https:"+img_url
        img = (ft.Image(src=img_url,width=66,height=66,fit="contain",border_radius=8)
               if img_url else
               ft.Container(width=66,height=66,bgcolor="#2D2D4E",border_radius=8,
                            content=ft.Icon("fitness_center",color="#7C83FF",size=26),
                            alignment=ft.Alignment(0,0)))
        neg_eff = p.get("negozio", stato["negozio"])
        badges = []
        if badge_negozio and neg_eff and neg_eff!="Tutti":
            badges.append(ft.Container(content=ft.Text(neg_eff,size=10,color="white"),
                bgcolor=COLORI_NEGOZI.get(neg_eff,"#555"),border_radius=4,
                padding=ft.Padding.only(left=5,right=5,top=1,bottom=1)))
        if badge_categoria and p.get("categoria"):
            badges.append(ft.Container(content=ft.Text(p["categoria"],size=10,color="#7C83FF"),
                bgcolor="#1A1A3E",border_radius=4,
                padding=ft.Padding.only(left=4,right=4,top=1,bottom=1)))
        if p.get("sconto"):
            badges.append(ft.Container(content=ft.Text(p["sconto"],size=11,color="#FF6B6B",weight="bold"),
                bgcolor="#2A1010",border_radius=4,
                padding=ft.Padding.only(left=4,right=4,top=1,bottom=1)))
        if in_wishlist and p.get("prezzo_target"):
            badges.append(ft.Container(
                content=ft.Text(f"🎯 {p['prezzo_target']:.2f}€",size=10,color="#FFC107"),
                bgcolor="#2A2000",border_radius=4,
                padding=ft.Padding.only(left=4,right=4,top=1,bottom=1)))

        prezzo_row = [ft.Text(p.get("prezzo","N/D"),color="#4CAF50",size=15,weight="bold")]
        if p.get("prezzo_originale"):
            prezzo_row.append(ft.Text(f"era {p['prezzo_originale']}",color="#888888",size=11,
                style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH)))

        # Azioni a destra
        azioni = ft.Column([
            ft.TextButton("🔖", on_click=lambda e, pp=p: mostra_dialogo_wishlist(pp),
                          tooltip="Wishlist", style=ft.ButtonStyle(padding=4)),
            ft.TextButton("📈", on_click=lambda e, n=p.get("nome",""), nn=neg_eff: mostra_storico(n, nn),
                          tooltip="Storico", style=ft.ButtonStyle(padding=4)),
        ], spacing=0, tight=True)

        if in_wishlist:
            azioni.controls.append(
                ft.TextButton("🗑", on_click=lambda e, n=p.get("nome",""), nn=neg_eff: _rimuovi_da_wishlist(n, nn),
                              tooltip="Rimuovi", style=ft.ButtonStyle(padding=4)))

        return ft.Container(
            bgcolor="#1A1A2E", border_radius=12, padding=12,
            content=ft.Row([
                img,
                ft.Column([
                    ft.Text(p.get("nome",""),weight="bold",size=13,max_lines=2),
                    ft.Column(prezzo_row,spacing=1,tight=True),
                    ft.Row(badges,spacing=4,wrap=True) if badges else ft.Container(),
                ], spacing=4, tight=True, expand=True),
                azioni,
            ], spacing=10, vertical_alignment="center"))

    # ── WISHLIST UI ──────────────────────────────────────────
    def aggiorna_wishlist_ui():
        wishlist_ui.controls.clear()
        items = carica_wishlist()
        if not items:
            wishlist_ui.controls.append(ft.Container(
                padding=20,
                content=ft.Column([
                    ft.Text("💾 Nessun prodotto in wishlist.", color="#666", size=14),
                    ft.Text("Clicca 🔖 su un prodotto per aggiungerlo.",
                            color="#444", size=12, italic=True),
                ], horizontal_alignment="center")))
        else:
            for item in items:
                wishlist_ui.controls.append(build_card(item, badge_negozio=True, in_wishlist=True))
        page.update()

    def _rimuovi_da_wishlist(nome, negozio):
        rimuovi_wishlist(negozio, nome)
        aggiorna_wishlist_ui()

    # ── RICERCA ──────────────────────────────────────────────
    def _esegui_ricerca(query: str):
        if not query or len(query) < 2: return
        ricerca_ui.controls.clear()
        neg = stato["negozio"] if stato["negozio"] != "Tutti" else None
        risultati = cerca_prodotti(query, negozio=neg)
        if not risultati:
            ricerca_ui.controls.append(ft.Text(f"Nessun risultato per «{query}».",
                                                color="#666", italic=True, size=13))
        else:
            ricerca_ui.controls.insert(0, ft.Text(f"{len(risultati)} risultati per «{query}»",
                                                    size=12, color="#888", italic=True))
            for p in risultati:
                ricerca_ui.controls.append(build_card(p, badge_negozio=True, badge_categoria=True))
        page.update()

    # ── GRAFICI ──────────────────────────────────────────────
    def _vai_a(negozio=None, categoria=None, soglia=None):
        switcha_vista("prodotti")
        if negozio and negozio in NEGOZI: cambia_negozio(negozio)
        if soglia is not None:
            stato["soglia"]=soglia; label_soglia.value=f"{soglia}%"; slider_soglia.value=soglia
            mostra_categoria("🔥 Offerte")
        elif categoria: mostra_categoria(categoria)

    def _bar(titolo, dati, colore="#5C6BC0", unita="", max_v=None, onclick=None):
        if not dati:
            return ft.Container(content=ft.Text("Nessun dato.",color="#666",size=12),padding=8)
        mv = max_v or max(dati.values()) or 1
        righe=[]
        for et,v in sorted(dati.items(),key=lambda x:-x[1])[:10]:
            bw=max(4,int(220*v/mv)); lc=(et[:18]+"…") if len(et)>18 else et
            def _r(e=et,b=bw,vv=v):
                r=ft.Container(border_radius=6,padding=ft.Padding.only(top=3,bottom=3),ink=True,
                    content=ft.Row([ft.Text(lc,size=11,color="#AAAAAA",width=130,text_align="right"),
                        ft.Container(width=6),ft.Container(width=b,height=18,bgcolor=colore,border_radius=4),
                        ft.Container(width=4),ft.Text(f"{vv}{unita}",size=11,color="#DDDDDD")],
                    spacing=0,vertical_alignment="center"))
                if onclick: r.on_click=lambda ev,x=e:onclick(x); r.tooltip=f"Vai a: {e}"
                return r
            righe.append(_r())
        return ft.Container(bgcolor="#1A1A2E",border_radius=12,padding=14,
            content=ft.Column([ft.Text(titolo,size=14,weight="bold",color="#EEEEEE"),
                ft.Divider(height=8,color="#2A2A3E"),
                ft.Column(righe,spacing=4,tight=True)],spacing=6,tight=True))

    def _fasce(titolo, dati):
        colori=["#4CAF50","#FFC107","#FF9800","#F44336"]
        soglie={"0-25%":0,"25-50%":25,"50-75%":50,"75%+":75}
        tot=sum(dati.values()) or 1
        blocchi=[]
        for (et,v),col in zip(dati.items(),colori):
            pct=round(v/tot*100); sm=soglie.get(et,0)
            blocchi.append(ft.Container(expand=True,bgcolor=col+"33",border_radius=8,padding=10,
                border=ft.Border.all(1,col),ink=True,tooltip=f"≥{sm}%",
                on_click=lambda e,s=sm:_vai_a(soglia=s),
                content=ft.Column([ft.Text(et,size=11,color=col,weight="bold",text_align="center"),
                    ft.Text(str(v),size=20,color=col,weight="bold",text_align="center"),
                    ft.Text(f"{pct}%",size=11,color="#888",text_align="center")],
                spacing=2,tight=True,horizontal_alignment="center")))
        return ft.Container(bgcolor="#1A1A2E",border_radius=12,padding=14,
            content=ft.Column([ft.Text(titolo,size=14,weight="bold",color="#EEEEEE"),
                ft.Divider(height=8,color="#2A2A3E"),ft.Row(blocchi,spacing=6)],spacing=6,tight=True))

    def _kpi(items):
        cards=[]
        for item in items:
            lbl,val,col=item[0],item[1],item[2]
            fn=item[3] if len(item)>3 else None; tip=item[4] if len(item)>4 else None
            cards.append(ft.Container(expand=True,bgcolor="#1A1A2E",border_radius=10,padding=10,
                border=ft.Border.all(1,col+"66"),ink=bool(fn),tooltip=tip,on_click=fn,
                content=ft.Column([ft.Text(str(val),size=24,weight="bold",color=col,text_align="center"),
                    ft.Text(lbl,size=10,color="#888",text_align="center")],
                spacing=2,tight=True,horizontal_alignment="center")))
        return ft.Container(content=ft.Row(cards,spacing=8),padding=ft.Padding.only(bottom=4))

    def aggiorna_grafici():
        grafici_ui.controls.clear()
        neg=stato["negozio"] if stato["negozio"]!="Tutti" else None
        with get_conn() as conn:
            tot=conn.execute("SELECT COUNT(*) FROM prodotti"+(" WHERE negozio=?" if neg else ""),
                             (neg,) if neg else ()).fetchone()[0]
            tsc=conn.execute("SELECT COUNT(*) FROM prodotti WHERE sconto_percentuale>0"+
                             (" AND negozio=?" if neg else ""),(neg,) if neg else ()).fetchone()[0]
            mx=conn.execute("SELECT MAX(sconto_percentuale),categoria FROM prodotti"+
                            (" WHERE negozio=?" if neg else ""),(neg,) if neg else ()).fetchone()
            sm=conn.execute("SELECT AVG(sconto_percentuale) FROM prodotti WHERE sconto_percentuale>0"+
                            (" AND negozio=?" if neg else ""),(neg,) if neg else ()).fetchone()[0] or 0
        grafici_ui.controls.append(_kpi([
            ("Prodotti",tot,"#5C6BC0",lambda e:_vai_a(negozio=neg or list(NEGOZI.keys())[0]),"Vai"),
            ("In offerta",tsc,"#FF9800",lambda e:_vai_a(soglia=1),"Mostra offerte"),
            ("Sconto max",f"{mx[0] or 0}%","#F44336",
             lambda e:_vai_a(categoria=mx[1]) if mx[1] else None,f"Vai a {mx[1]}"),
            ("Sconto medio",f"{round(sm)}%","#4CAF50",
             lambda e:_vai_a(soglia=max(0,round(sm)-5)),"Filtra"),
        ]))
        if not neg:
            grafici_ui.controls.append(_bar("Prodotti per negozio",
                stats_prodotti_per_negozio(),colore_barre="#5C6BC0",onclick=lambda n:_vai_a(negozio=n)))
        grafici_ui.controls.append(_bar("Sconto medio per categoria (%)",
            stats_sconto_medio_per_categoria(neg),colore="#FF9800",unita="%",max_v=100,
            onclick=lambda c:_vai_a(categoria=c)))
        grafici_ui.controls.append(_fasce("Distribuzione sconti",stats_offerte_per_fascia(neg)))
        top=offerte_convenienti(soglia=50,negozio=neg)[:10]
        if top:
            righe_top=[ft.Container(border_radius=6,ink=True,
                padding=ft.Padding.symmetric(horizontal=4,vertical=3),
                on_click=lambda e,c=p.get("categoria",""),n=p.get("negozio",""):
                    _vai_a(negozio=n if n in NEGOZI else None,categoria=c or None),
                content=ft.Row([ft.Text(f"{i}.",size=12,color="#666",width=20),
                    ft.Text((p["nome"][:28]+"…") if len(p["nome"])>28 else p["nome"],
                            size=12,color="#DDDDDD",expand=True),
                    ft.Text(p["sconto"],size=12,color="#FF6B6B",weight="bold")],spacing=6))
                for i,p in enumerate(top,1)]
            grafici_ui.controls.append(ft.Container(bgcolor="#1A1A2E",border_radius=12,padding=14,
                content=ft.Column([ft.Text("🏆 Top 10 sconti",size=14,weight="bold",color="#EEEEEE"),
                    ft.Divider(height=8,color="#2A2A3E"),
                    ft.Column(righe_top,spacing=2,tight=True)],spacing=6,tight=True)))
        page.update()

    # ── SWITCH VISTA ─────────────────────────────────────────
    btn_prodotti  = ft.TextButton("📦", on_click=lambda e: switcha_vista("prodotti"), tooltip="Prodotti")
    btn_grafici   = ft.TextButton("📊", on_click=lambda e: switcha_vista("grafici"), tooltip="Grafici")
    btn_wishlist  = ft.TextButton("💾", on_click=lambda e: switcha_vista("wishlist"), tooltip="Wishlist")
    btn_cerca     = ft.TextButton("🔍", on_click=lambda e: switcha_vista("ricerca"), tooltip="Cerca")

    def _set_btn_stili(vista):
        for btn, v in [(btn_prodotti,"prodotti"),(btn_grafici,"grafici"),
                       (btn_wishlist,"wishlist"),(btn_cerca,"ricerca")]:
            btn.style = ft.ButtonStyle(color="#FFFFFF" if vista==v else "#555555")

    def switcha_vista(vista):
        stato["vista"] = vista
        _set_btn_stili(vista)
        corpo.controls.clear()
        riga_categorie_container.visible = vista == "prodotti"
        barra_ricerca.visible = vista == "ricerca"
        if vista == "prodotti":
            corpo.controls += [barra_soglia, lista_ui]
            mostra_categoria(stato["categoria"])
        elif vista == "grafici":
            corpo.controls.append(grafici_ui); aggiorna_grafici()
        elif vista == "wishlist":
            corpo.controls.append(wishlist_ui); aggiorna_wishlist_ui()
        elif vista == "ricerca":
            campo_ricerca.value = ""
            ricerca_ui.controls.clear()
            ricerca_ui.controls.append(ft.Text("Digita nella barra qui sopra e premi Invio.",
                                                color="#555",italic=True,size=13))
            corpo.controls.append(ricerca_ui)
        page.update()

    # ── PRODOTTI ─────────────────────────────────────────────
    def _ricarica_offerte():
        lista_ui.controls.clear()
        neg=stato["negozio"] if stato["negozio"]!="Tutti" else None
        prodotti=offerte_convenienti(stato["soglia"],negozio=neg)
        if not prodotti:
            lista_ui.controls.append(ft.Text("Nessuna offerta.",color="#666",italic=True,size=13))
        else:
            for p in prodotti[:50]:
                lista_ui.controls.append(build_card(p,badge_negozio=True,badge_categoria=True))

    def mostra_categoria(cat):
        stato["categoria"]=cat
        lista_ui.controls.clear()
        for btn in riga_categorie.controls:
            attivo=btn.data==cat
            btn.bgcolor=COLORI_NEGOZI.get(stato["negozio"],"#5C6BC0") if attivo else "#1E1E30"
            btn.color="white" if attivo else "#888888"
        if cat=="🔥 Offerte":
            barra_soglia.visible=True; _ricarica_offerte()
        else:
            barra_soglia.visible=False
            neg=stato["negozio"]
            if neg=="Tutti":
                prodotti=[]
                for n in NEGOZI:
                    key=f"{n}/{cat}"
                    if key not in cache: cache[key]=carica_categoria(n,cat)
                    for p in cache[key]: prodotti.append({**p,"negozio":n})
                prodotti.sort(key=lambda x:x.get("sconto_percentuale",0),reverse=True)
            else:
                key=f"{neg}/{cat}"
                if key not in cache: cache[key]=carica_categoria(neg,cat)
                prodotti=cache[key]
            if not prodotti:
                lista_ui.controls.append(ft.Text("Nessun prodotto.",color="#666",italic=True,size=13))
            else:
                mostra_badge=(neg=="Tutti")
                for p in prodotti[:50]:
                    lista_ui.controls.append(build_card(p,badge_negozio=mostra_badge))
        page.update()

    def cambia_negozio(negozio):
        stato["negozio"]=negozio
        for btn in riga_negozi.controls:
            attivo=btn.data==negozio
            col=COLORI_NEGOZI.get(negozio,"#5C6BC0")
            btn.bgcolor=col if attivo else "#1E1E30"; btn.color="white" if attivo else "#888888"
        if negozio!="Tutti":
            ult=ultimo_aggiornamento(negozio)
            testo_stato.value=f"📦 {negozio} — {ult[:16]}" if ult else f"⚠ Nessun dato."
        else:
            testo_stato.value="Tutti i negozi"
        if stato["vista"]=="grafici": aggiorna_grafici()
        elif stato["vista"]=="wishlist": aggiorna_wishlist_ui()
        else: mostra_categoria(stato["categoria"])

    def aggiorna_negozio(e):
        negozi_da_aggiornare=list(NEGOZI.keys())
        bottone_aggiorna.disabled=True; progress_bar.visible=True
        progress_bar.value=None; page.update()
        def worker():
            N=len(negozi_da_aggiornare)
            for idx,negozio in enumerate(negozi_da_aggiornare,1):
                def on_cat(cat,prodotti,i,totale,neg=negozio,ix=idx):
                    try: salva_categoria(neg,cat,prodotti)
                    except Exception as ex: print(f"DB:{ex}")
                    cache[f"{neg}/{cat}"]=prodotti
                    progress_bar.value=(ix-1+i/totale)/N
                    testo_stato.value=f"⏳ [{ix}/{N}] {neg} — {cat} ({i}/{totale})"
                    if cat==stato["categoria"] and neg==stato["negozio"]: mostra_categoria(cat)
                    else: page.update()
                ottieni_tutte_categorie(negozio,callback=on_cat)
            bottone_aggiorna.disabled=False; progress_bar.visible=False
            tot=sum(len(carica_categoria(n,c)) for n in negozi_da_aggiornare for c in NEGOZI[n]["categorie"])
            ult=ultimo_aggiornamento()
            testo_stato.value=f"✅ {tot} prodotti — {ult[:16] if ult else ''}"
            if stato["vista"]=="grafici": aggiorna_grafici()
            elif stato["vista"]=="wishlist": aggiorna_wishlist_ui()
            else: mostra_categoria(stato["categoria"])
        threading.Thread(target=worker,daemon=True).start()

    # ── BUILD UI ─────────────────────────────────────────────
    riga_negozi.controls.append(ft.Button("Tutti",data="Tutti",bgcolor="#1E1E30",color="#888888",
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=0)),
        on_click=lambda e:cambia_negozio(e.control.data),expand=True))
    for nome in NEGOZI:
        riga_negozi.controls.append(ft.Button(nome,data=nome,bgcolor="#1E1E30",color="#888888",
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=0)),
            on_click=lambda e:cambia_negozio(e.control.data),expand=True))

    for cat in ["🔥 Offerte"]+CATEGORIE:
        riga_categorie.controls.append(ft.Button(cat,data=cat,bgcolor="#1E1E30",color="#888888",
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=20)),
            on_click=lambda e:mostra_categoria(e.control.data)))

    bottone_aggiorna=ft.Button("Aggiorna",on_click=aggiorna_negozio,
        bgcolor="#5C6BC0",color="white",icon="download")

    riga_categorie_container=ft.Container(padding=ft.Padding.only(top=6,bottom=6),content=riga_categorie)

    page.add(ft.Column(expand=True,spacing=0,controls=[
        ft.Container(bgcolor="#12121E",
            padding=ft.Padding.only(left=16,right=16,top=14,bottom=8),
            content=ft.Column(spacing=6,controls=[
                ft.Row([ft.Text("💪 Tracker Integratori",size=18,weight="bold"),
                    ft.Row([btn_prodotti,btn_cerca,btn_wishlist,btn_grafici],spacing=0),
                    bottone_aggiorna],alignment="spaceBetween"),
                ft.Row([testo_stato,ft.Container(expand=True),testo_prossimo],spacing=4),
                progress_bar])),
        ft.Container(bgcolor="#12121E",content=riga_negozi),
        ft.Divider(height=1,color="#2A2A3E"),
        barra_ricerca,
        riga_categorie_container,
        ft.Divider(height=1,color="#2A2A3E"),
        corpo,
    ]))

    _set_btn_stili("prodotti")
    cambia_negozio(stato["negozio"])


ft.run(main)
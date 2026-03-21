import flet as ft
import threading
from scraper import NEGOZI, CATEGORIE, ottieni_tutte_categorie
from database import (
    carica_categoria,
    offerte_convenienti,
    ultimo_aggiornamento,
    salva_categoria,
    storico_prezzi_prodotto,
    get_conn,
)

SOGLIA_DEFAULT = 0
COLORI_NEGOZI = {n: cfg["colore"] for n, cfg in NEGOZI.items()}


# ── STATISTICHE DAL DB ───────────────────────────────────────
def stats_prodotti_per_negozio() -> dict:
    """{ negozio: count }"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT negozio, COUNT(*) FROM prodotti GROUP BY negozio"
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def stats_sconto_medio_per_categoria(negozio: str = None) -> dict:
    """{ categoria: sconto_medio% }"""
    with get_conn() as conn:
        if negozio:
            rows = conn.execute("""
                SELECT categoria, AVG(sconto_percentuale)
                FROM prodotti
                WHERE negozio=? AND sconto_percentuale > 0
                GROUP BY categoria
            """, (negozio,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT categoria, AVG(sconto_percentuale)
                FROM prodotti
                WHERE sconto_percentuale > 0
                GROUP BY categoria
            """).fetchall()
    return {r[0]: round(r[1], 1) for r in rows if r[1]}


def stats_offerte_per_fascia(negozio: str = None) -> dict:
    """{ '0-25%': n, '25-50%': n, '50-75%': n, '75+%': n }"""
    with get_conn() as conn:
        q = "SELECT sconto_percentuale FROM prodotti WHERE sconto_percentuale > 0"
        params = ()
        if negozio:
            q += " AND negozio=?"
            params = (negozio,)
        rows = conn.execute(q, params).fetchall()
    fasce = {"0-25%": 0, "25-50%": 0, "50-75%": 0, "75%+": 0}
    for (s,) in rows:
        if s < 25:
            fasce["0-25%"] += 1
        elif s < 50:
            fasce["25-50%"] += 1
        elif s < 75:
            fasce["50-75%"] += 1
        else:
            fasce["75%+"] += 1
    return fasce


# ────────────────────────────────────────────────────────────
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
        "vista": "prodotti",   # "prodotti" | "grafici"
    }
    cache = {}

    # ── WIDGETS PERSISTENTI ──────────────────────────────────
    lista_ui   = ft.ListView(expand=True, spacing=8, padding=10, item_extent=95)
    grafici_ui = ft.ListView(expand=True, spacing=16, padding=16)

    testo_stato  = ft.Text("", italic=True, size=12, color="#888888")
    progress_bar = ft.ProgressBar(visible=False, color="#5C6BC0", bgcolor="#1A1A2E")

    riga_negozi    = ft.Row(spacing=0, alignment="start")
    riga_categorie = ft.ListView(
        horizontal=True, height=40, spacing=6,
        padding=ft.padding.only(left=12, right=12),
    )

    label_soglia = ft.Text(f"{SOGLIA_DEFAULT}%", size=13, color="#FF6B6B",
                           weight="bold", width=42)

    def on_slider_change(e):
        stato["soglia"] = int(e.control.value)
        label_soglia.value = f"{stato['soglia']}%"
        _ricarica_offerte()
        page.update()

    slider_soglia = ft.Slider(
        min=0, max=90, divisions=18, value=SOGLIA_DEFAULT,
        label="{value}%", active_color="#5C6BC0", expand=True,
        on_change_end=on_slider_change,
    )
    barra_soglia = ft.Container(
        visible=False,
        padding=ft.padding.only(left=8, right=8, top=4, bottom=4),
        content=ft.Row([
            ft.Text("Filtra sconti oltre il:", size=12, color="#AAAAAA"),
            slider_soglia, label_soglia,
        ], spacing=6),
    )

    # Contenitore principale che switcha tra lista e grafici
    corpo = ft.Column(expand=True, spacing=0, controls=[
        barra_soglia,
        lista_ui,
    ])

    # ── STORICO PREZZI ───────────────────────────────────────
    def mostra_storico(nome, negozio):
        storico = storico_prezzi_prodotto(negozio, nome, limite=15)
        rows = []
        if not storico:
            rows.append(ft.Text("Nessuno storico disponibile.", size=12, color="#888"))
        else:
            for s in storico:
                col = "#4CAF50" if s["variazione"] == "diminuito" else \
                      "#F44336" if s["variazione"] == "aumentato" else "#888888"
                icona = "↓" if s["variazione"] == "diminuito" else \
                        "↑" if s["variazione"] == "aumentato" else "–"
                rows.append(ft.Text(
                    f"{s['data'][:10]}: {s['prezzo_vecchio']} → {s['prezzo_corrente']} {icona}",
                    color=col, size=12,
                ))
        dlg = ft.AlertDialog(
            title=ft.Text(f"Storico: {nome[:40]}", size=13, weight="bold"),
            content=ft.Column(rows, scroll="always", tight=True, height=200),
            actions=[ft.TextButton("Chiudi", on_click=lambda e: _chiudi(dlg))],
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    def _chiudi(dlg):
        dlg.open = False
        page.update()

    # ── CARD PRODOTTO ────────────────────────────────────────
    def build_card(p, badge_negozio=False, badge_categoria=False):
        img_url = p.get("immagine", "")
        if img_url and img_url.startswith("//"):
            img_url = "https:" + img_url
        img = (ft.Image(src=img_url, width=66, height=66, fit="contain", border_radius=8)
               if img_url else
               ft.Container(width=66, height=66, bgcolor="#2D2D4E", border_radius=8,
                            content=ft.Icon("fitness_center", color="#7C83FF", size=26),
                            alignment=ft.Alignment(0, 0)))

        neg_eff = p.get("negozio", stato["negozio"])
        badges = []
        if badge_negozio and neg_eff and neg_eff != "Tutti":
            badges.append(ft.Container(
                content=ft.Text(neg_eff, size=10, color="white"),
                bgcolor=COLORI_NEGOZI.get(neg_eff, "#555"), border_radius=4,
                padding=ft.padding.only(left=5, right=5, top=1, bottom=1),
            ))
        if badge_categoria and p.get("categoria"):
            badges.append(ft.Container(
                content=ft.Text(p["categoria"], size=10, color="#7C83FF"),
                bgcolor="#1A1A3E", border_radius=4,
                padding=ft.padding.only(left=4, right=4, top=1, bottom=1),
            ))
        if p.get("sconto"):
            badges.append(ft.Container(
                content=ft.Text(p["sconto"], size=11, color="#FF6B6B", weight="bold"),
                bgcolor="#2A1010", border_radius=4,
                padding=ft.padding.only(left=4, right=4, top=1, bottom=1),
            ))

        prezzo_row = [ft.Text(p.get("prezzo", "N/D"), color="#4CAF50", size=15, weight="bold")]
        if p.get("prezzo_originale"):
            prezzo_row.append(ft.Text(
                f"era {p['prezzo_originale']}", color="#888888", size=11,
                style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH),
            ))

        card = ft.Container(
            bgcolor="#1A1A2E", border_radius=12, padding=12,
            content=ft.Row([
                img,
                ft.Column([
                    ft.Text(p.get("nome", ""), weight="bold", size=13, max_lines=2),
                    ft.Column(prezzo_row, spacing=1, tight=True),
                    ft.Row(badges, spacing=4, wrap=True) if badges else ft.Container(),
                ], spacing=4, tight=True, expand=True),
            ], spacing=10, vertical_alignment="center"),
        )
        card.on_click = lambda e, n=p.get("nome",""), nn=neg_eff: mostra_storico(n, nn)
        return card

    # ── GRAFICI ──────────────────────────────────────────────
    def _colore_hex_a_flet(hex_str: str):
        """Converte '#RRGGBB' in ft.Color (usa stringa diretta)."""
        return hex_str

    def _vai_a(negozio: str = None, categoria: str = None, soglia: int = None):
        """Naviga alla vista prodotti con negozio/categoria/offerte specifici."""
        switcha_vista("prodotti")
        if negozio and negozio in NEGOZI:
            cambia_negozio(negozio)
        if soglia is not None:
            stato["soglia"] = soglia
            label_soglia.value = f"{soglia}%"
            slider_soglia.value = soglia
            mostra_categoria("🔥 Offerte")
        elif categoria:
            mostra_categoria(categoria)

    def _barchart_orizzontale(titolo: str, dati: dict, colore_barre: str = "#5C6BC0",
                               unita: str = "", max_valore: float = None,
                               on_click_fn=None) -> ft.Container:
        """Barchart orizzontale — ogni barra è cliccabile."""
        if not dati:
            return ft.Container(
                content=ft.Text("Nessun dato disponibile.", color="#666", size=12),
                padding=8,
            )

        max_val = max_valore or max(dati.values()) or 1
        righe = []
        for etichetta, valore in sorted(dati.items(), key=lambda x: -x[1])[:10]:
            pct = valore / max_val
            bar_width = max(4, int(220 * pct))
            label_corta = (etichetta[:18] + "…") if len(etichetta) > 18 else etichetta

            # Riga cliccabile
            def _make_riga(et=etichetta, bw=bar_width, v=valore):
                riga = ft.Container(
                    border_radius=6,
                    padding=ft.padding.only(top=3, bottom=3),
                    ink=True,
                    content=ft.Row([
                        ft.Text(label_corta if et == etichetta else (et[:18]+"…"),
                                size=11, color="#AAAAAA", width=130, text_align="right"),
                        ft.Container(width=6),
                        ft.Container(width=bw, height=18, bgcolor=colore_barre, border_radius=4),
                        ft.Container(width=4),
                        ft.Text(f"{v}{unita}", size=11, color="#DDDDDD"),
                    ], spacing=0, vertical_alignment="center"),
                )
                if on_click_fn:
                    riga.on_click = lambda e, x=et: on_click_fn(x)
                    riga.tooltip = f"Vai a: {et}"
                return riga

            righe.append(_make_riga())

        return ft.Container(
            bgcolor="#1A1A2E", border_radius=12, padding=14,
            content=ft.Column([
                ft.Text(titolo, size=14, weight="bold", color="#EEEEEE"),
                ft.Divider(height=8, color="#2A2A3E"),
                ft.Column(righe, spacing=4, tight=True),
            ], spacing=6, tight=True),
        )

    def _piechart_fasce(titolo: str, dati: dict) -> ft.Container:
        """Fasce di sconto — ogni blocco è cliccabile e filtra le offerte."""
        colori = ["#4CAF50", "#FFC107", "#FF9800", "#F44336"]
        # Mappa fascia → soglia minima
        soglie = {"0-25%": 0, "25-50%": 25, "50-75%": 50, "75%+": 75}
        totale = sum(dati.values()) or 1
        blocchi = []
        for (etichetta, valore), colore in zip(dati.items(), colori):
            pct = round(valore / totale * 100)
            soglia_min = soglie.get(etichetta, 0)
            blocchi.append(
                ft.Container(
                    expand=True,
                    bgcolor=colore + "33",
                    border_radius=8,
                    padding=10,
                    border=ft.border.all(1, colore),
                    ink=True,
                    tooltip=f"Mostra offerte ≥ {soglia_min}%",
                    on_click=lambda e, s=soglia_min: _vai_a(soglia=s),
                    content=ft.Column([
                        ft.Text(etichetta, size=11, color=colore, weight="bold",
                                text_align="center"),
                        ft.Text(str(valore), size=20, color=colore, weight="bold",
                                text_align="center"),
                        ft.Text(f"{pct}%", size=11, color="#888888",
                                text_align="center"),
                    ], spacing=2, tight=True, horizontal_alignment="center"),
                )
            )

        return ft.Container(
            bgcolor="#1A1A2E", border_radius=12, padding=14,
            content=ft.Column([
                ft.Text(titolo, size=14, weight="bold", color="#EEEEEE"),
                ft.Divider(height=8, color="#2A2A3E"),
                ft.Row(blocchi, spacing=6),
            ], spacing=6, tight=True),
        )

    def _kpi_row(dati_kpi: list) -> ft.Container:
        """KPI cliccabili: [(label, valore, colore, on_click), ...]"""
        cards = []
        for item in dati_kpi:
            label, valore, colore = item[0], item[1], item[2]
            click_fn = item[3] if len(item) > 3 else None
            c = ft.Container(
                expand=True,
                bgcolor="#1A1A2E", border_radius=10, padding=10,
                border=ft.border.all(1, colore + "66"),
                ink=True if click_fn else False,
                tooltip=item[4] if len(item) > 4 else None,
                on_click=click_fn,
                content=ft.Column([
                    ft.Text(str(valore), size=24, weight="bold", color=colore,
                            text_align="center"),
                    ft.Text(label, size=10, color="#888888", text_align="center"),
                ], spacing=2, tight=True, horizontal_alignment="center"),
            )
            cards.append(c)
        return ft.Container(
            content=ft.Row(cards, spacing=8),
            padding=ft.padding.only(bottom=4),
        )

    def aggiorna_grafici():
        grafici_ui.controls.clear()

        neg = stato["negozio"] if stato["negozio"] != "Tutti" else None

        with get_conn() as conn:
            tot_prodotti = conn.execute(
                "SELECT COUNT(*) FROM prodotti" + (" WHERE negozio=?" if neg else ""),
                (neg,) if neg else ()
            ).fetchone()[0]

            tot_scontati = conn.execute(
                "SELECT COUNT(*) FROM prodotti WHERE sconto_percentuale > 0" +
                (" AND negozio=?" if neg else ""),
                (neg,) if neg else ()
            ).fetchone()[0]

            sconto_max_row = conn.execute(
                "SELECT MAX(sconto_percentuale), categoria FROM prodotti" +
                (" WHERE negozio=?" if neg else ""),
                (neg,) if neg else ()
            ).fetchone()
            sconto_max = sconto_max_row[0] or 0
            cat_max = sconto_max_row[1] or ""

            sconto_medio = conn.execute(
                "SELECT AVG(sconto_percentuale) FROM prodotti WHERE sconto_percentuale > 0" +
                (" AND negozio=?" if neg else ""),
                (neg,) if neg else ()
            ).fetchone()[0] or 0

        grafici_ui.controls.append(_kpi_row([
            ("Prodotti totali", tot_prodotti, "#5C6BC0",
             lambda e: _vai_a(negozio=neg or (list(NEGOZI.keys())[0] if NEGOZI else None)),
             "Vai ai prodotti"),
            ("In offerta", tot_scontati, "#FF9800",
             lambda e: _vai_a(soglia=1),
             "Mostra offerte"),
            ("Sconto max", f"{sconto_max}%", "#F44336",
             lambda e: _vai_a(categoria=cat_max) if cat_max else None,
             f"Vai a: {cat_max}"),
            ("Sconto medio", f"{round(sconto_medio)}%", "#4CAF50",
             lambda e: _vai_a(soglia=max(0, round(sconto_medio) - 5)),
             "Mostra offerte vicine alla media"),
        ]))

        # Prodotti per negozio (cliccabile → cambia negozio)
        if not neg:
            grafici_ui.controls.append(_barchart_orizzontale(
                "Prodotti per negozio",
                stats_prodotti_per_negozio(),
                colore_barre="#5C6BC0",
                on_click_fn=lambda n: _vai_a(negozio=n),
            ))

        # Sconto medio per categoria (cliccabile → vai a categoria)
        grafici_ui.controls.append(_barchart_orizzontale(
            "Sconto medio per categoria (%)",
            stats_sconto_medio_per_categoria(neg),
            colore_barre="#FF9800",
            unita="%",
            max_valore=100,
            on_click_fn=lambda cat: _vai_a(categoria=cat),
        ))

        # Fasce di sconto (cliccabile → filtra offerte)
        grafici_ui.controls.append(_piechart_fasce(
            "Distribuzione sconti — clicca per filtrare",
            stats_offerte_per_fascia(neg),
        ))

        # Top 10 offerte (cliccabili → vai alle offerte filtrate)
        top = offerte_convenienti(soglia=50, negozio=neg)[:10]
        if top:
            righe_top = []
            for i, p in enumerate(top, 1):
                nome_troncato = (p["nome"][:28] + "…") if len(p["nome"]) > 28 else p["nome"]
                righe_top.append(ft.Container(
                    border_radius=6, ink=True,
                    padding=ft.padding.symmetric(horizontal=4, vertical=3),
                    tooltip=f"Vai a: {p.get('categoria', '')}",
                    on_click=lambda e, cat=p.get("categoria",""), nn=p.get("negozio",""):
                        (_vai_a(negozio=nn if nn in NEGOZI else None,
                                categoria=cat if cat else None)),
                    content=ft.Row([
                        ft.Text(f"{i}.", size=12, color="#666", width=20),
                        ft.Text(nome_troncato, size=12, color="#DDDDDD", expand=True),
                        ft.Text(p["sconto"], size=12, color="#FF6B6B", weight="bold"),
                    ], spacing=6),
                ))
            grafici_ui.controls.append(ft.Container(
                bgcolor="#1A1A2E", border_radius=12, padding=14,
                content=ft.Column([
                    ft.Text("🏆 Top 10 sconti — clicca per aprire",
                            size=14, weight="bold", color="#EEEEEE"),
                    ft.Divider(height=8, color="#2A2A3E"),
                    ft.Column(righe_top, spacing=2, tight=True),
                ], spacing=6, tight=True),
            ))

        page.update()

    # ── SWITCH VISTA ─────────────────────────────────────────
    btn_vista_prodotti = ft.TextButton(
        "📦 Prodotti", on_click=lambda e: switcha_vista("prodotti"),
    )
    btn_vista_grafici = ft.TextButton(
        "📊 Grafici", on_click=lambda e: switcha_vista("grafici"),
    )

    def switcha_vista(vista: str):
        stato["vista"] = vista
        btn_vista_prodotti.style = ft.ButtonStyle(
            color="#FFFFFF" if vista == "prodotti" else "#666666"
        )
        btn_vista_grafici.style = ft.ButtonStyle(
            color="#FFFFFF" if vista == "grafici" else "#666666"
        )
        corpo.controls.clear()
        if vista == "grafici":
            corpo.controls.append(grafici_ui)
            aggiorna_grafici()
            # Nascondi barra categorie
            riga_categorie_container.visible = False
            barra_soglia.visible = False
        else:
            corpo.controls.append(barra_soglia)
            corpo.controls.append(lista_ui)
            riga_categorie_container.visible = True
            mostra_categoria(stato["categoria"])
        page.update()

    # ── LOGICA PRODOTTI ──────────────────────────────────────
    def _ricarica_offerte():
        lista_ui.controls.clear()
        neg = stato["negozio"] if stato["negozio"] != "Tutti" else None
        prodotti = offerte_convenienti(stato["soglia"], negozio=neg)
        if not prodotti:
            lista_ui.controls.append(ft.Text(
                "Nessuna offerta." if stato["soglia"] == 0
                else f"Nessuna offerta ≥ {stato['soglia']}%.",
                color="#666", italic=True, size=13,
            ))
        else:
            for p in prodotti[:50]:
                lista_ui.controls.append(build_card(p, badge_negozio=True, badge_categoria=True))

    def mostra_categoria(cat):
        stato["categoria"] = cat
        lista_ui.controls.clear()
        for btn in riga_categorie.controls:
            attivo = btn.data == cat
            btn.bgcolor = COLORI_NEGOZI.get(stato["negozio"], "#5C6BC0") if attivo else "#1E1E30"
            btn.color = "white" if attivo else "#888888"

        if cat == "🔥 Offerte":
            barra_soglia.visible = True
            _ricarica_offerte()
        else:
            barra_soglia.visible = False
            neg = stato["negozio"]
            if neg == "Tutti":
                lista_ui.controls.append(ft.Text(
                    "Seleziona un negozio per le categorie.", color="#666", italic=True, size=13
                ))
            else:
                key = f"{neg}/{cat}"
                if key not in cache:
                    cache[key] = carica_categoria(neg, cat)
                prodotti = cache[key]
                if not prodotti:
                    lista_ui.controls.append(ft.Text(
                        "Nessun prodotto. Clicca 'Aggiorna'.", color="#666", italic=True, size=13
                    ))
                else:
                    for p in prodotti[:50]:
                        lista_ui.controls.append(build_card(p))
        page.update()

    def cambia_negozio(negozio):
        stato["negozio"] = negozio
        for btn in riga_negozi.controls:
            attivo = btn.data == negozio
            col = COLORI_NEGOZI.get(negozio, "#5C6BC0")
            btn.bgcolor = col if attivo else "#1E1E30"
            btn.color = "white" if attivo else "#888888"
        if negozio != "Tutti":
            ult = ultimo_aggiornamento(negozio)
            testo_stato.value = (f"📦 {negozio} — {ult[:16]}" if ult
                                 else f"⚠ Nessun dato. Clicca Aggiorna.")
        else:
            testo_stato.value = "Tutti i negozi"
        if stato["vista"] == "grafici":
            aggiorna_grafici()
        else:
            mostra_categoria(stato["categoria"])

    def aggiorna_negozio(e):
        negozio = stato["negozio"]
        if negozio == "Tutti":
            testo_stato.value = "Seleziona un negozio da aggiornare."
            page.update()
            return
        bottone_aggiorna.disabled = True
        progress_bar.visible = True
        progress_bar.value = None
        testo_stato.value = f"⏳ {negozio}..."
        page.update()

        def on_cat(cat, prodotti, i, totale):
            try:
                salva_categoria(negozio, cat, prodotti)
            except Exception as ex:
                print(f"DB error: {ex}")
            cache[f"{negozio}/{cat}"] = prodotti
            progress_bar.value = i / totale
            testo_stato.value = f"⏳ {negozio} ({i}/{totale}) {cat}..."
            if cat == stato["categoria"] and negozio == stato["negozio"]:
                mostra_categoria(cat)
            else:
                page.update()

        def worker():
            ottieni_tutte_categorie(negozio, callback=on_cat)
            bottone_aggiorna.disabled = False
            progress_bar.visible = False
            ult = ultimo_aggiornamento(negozio)
            tot = sum(len(carica_categoria(negozio, c)) for c in NEGOZI[negozio]["categorie"])
            testo_stato.value = f"✅ {negozio}: {tot} prodotti — {ult[:16] if ult else ''}"
            if stato["vista"] == "grafici":
                aggiorna_grafici()
            else:
                mostra_categoria(stato["categoria"])

        threading.Thread(target=worker, daemon=True).start()

    # ── BUILD UI ─────────────────────────────────────────────
    # Bottone "Tutti"
    riga_negozi.controls.append(ft.ElevatedButton(
        "Tutti", data="Tutti", bgcolor="#1E1E30", color="#888888",
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=0)),
        on_click=lambda e: cambia_negozio(e.control.data), expand=True,
    ))
    for nome in NEGOZI:
        riga_negozi.controls.append(ft.ElevatedButton(
            nome, data=nome, bgcolor="#1E1E30", color="#888888",
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=0)),
            on_click=lambda e: cambia_negozio(e.control.data), expand=True,
        ))

    for cat in ["🔥 Offerte"] + CATEGORIE:
        riga_categorie.controls.append(ft.ElevatedButton(
            cat, data=cat, bgcolor="#1E1E30", color="#888888",
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=20)),
            on_click=lambda e: mostra_categoria(e.control.data),
        ))

    bottone_aggiorna = ft.ElevatedButton(
        "Aggiorna", on_click=aggiorna_negozio,
        bgcolor="#5C6BC0", color="white", icon="download",
    )

    riga_categorie_container = ft.Container(
        padding=ft.padding.only(top=6, bottom=6),
        content=riga_categorie,
    )

    page.add(
        ft.Column(expand=True, spacing=0, controls=[
            # Header
            ft.Container(
                bgcolor="#12121E",
                padding=ft.padding.only(left=16, right=16, top=14, bottom=8),
                content=ft.Column(spacing=6, controls=[
                    ft.Row([
                        ft.Text("💪 Tracker Integratori", size=18, weight="bold"),
                        ft.Row([btn_vista_prodotti, btn_vista_grafici], spacing=0),
                        bottone_aggiorna,
                    ], alignment="spaceBetween"),
                    testo_stato,
                    progress_bar,
                ]),
            ),
            # Tab negozi
            ft.Container(bgcolor="#12121E", content=riga_negozi),
            ft.Divider(height=1, color="#2A2A3E"),
            # Tab categorie
            riga_categorie_container,
            ft.Divider(height=1, color="#2A2A3E"),
            # Corpo (prodotti o grafici)
            corpo,
        ])
    )

    # Init stile bottoni vista
    btn_vista_prodotti.style = ft.ButtonStyle(color="#FFFFFF")
    btn_vista_grafici.style  = ft.ButtonStyle(color="#666666")
    cambia_negozio(stato["negozio"])


ft.app(target=main)
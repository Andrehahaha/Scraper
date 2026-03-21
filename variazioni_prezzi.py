#!/usr/bin/env python3
"""
Interfaccia per visualizzare le variazioni di prezzo dei prodotti.
Legge dallo storico_prezzi nel database.
"""

import sys
import io

# Fix encoding Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from database import storico_variazioni, variazioni_per_marca, negozi_nel_db

# Icone ASCII per compatibilità Windows
ICON_DOWN = "[DOWN]"
ICON_UP = "[UP]"
ICON_NEUTRAL = "[=]"

def stampa_variazioni_recenti(limite=30):
    """Mostra le ultime variazioni di prezzo da tutti i negozi."""
    print("\n" + "="*70)
    print("ULTIME VARIAZIONI DI PREZZO")
    print("="*70)

    variazioni = storico_variazioni(limite=limite)

    if not variazioni:
        print("Nessuna variazione registrata.")
        return

    for v in variazioni:
        icona = ICON_DOWN if v["variazione"] == "diminuito" else ICON_UP if v["variazione"] == "aumentato" else ICON_NEUTRAL

        print(f"\n{icona} {v['negozio']} - {v['nome']}")
        print(f"   Prezzo: {v['prezzo_vecchio']} → {v['prezzo_corrente']}")
        if v["prezzo_originale"]:
            print(f"   Prezzo originale: {v['prezzo_originale']}")
        print(f"   Data: {v['data']}")
        print("-"*50)


def stampa_variazioni_marca(negozio: str):
    """Mostra le variazioni di prezzo per una specifica marca."""
    print("\n" + "="*70)
    print(f"VARIAZIONI DI PREZZO - {negozio.upper()}")
    print("="*70)

    variazioni = variazioni_per_marca(negozio)

    if not variazioni:
        print("Nessuna variazione registrata per questa marca.")
        return

    # Raggruppa per tipo di variazione
    diminuiti = [v for v in variazioni if v["variazione"] == "diminuito"]
    aumentati = [v for v in variazioni if v["variazione"] == "aumentato"]

    if diminuiti:
        print(f"\n{ICON_DOWN} PREZZI DIMINUITI ({len(diminuiti)}):")
        for v in diminuiti[:10]:
            print(f"  {v['nome']}: {v['prezzo_vecchio']} -> {v['prezzo_corrente']} ({v['data']})")

    if aumentati:
        print(f"\n{ICON_UP} PREZZI AUMENTATI ({len(aumentati)}):")
        for v in aumentati[:10]:
            print(f"  {v['nome']}: {v['prezzo_vecchio']} -> {v['prezzo_corrente']} ({v['data']})")

    print("\n" + "="*70)


def stampa_riepilogo():
    """Mostra un riepilogo delle variazioni per marca."""
    print("\n" + "="*70)
    print("RIEPILOGO VARIAZIONI PER MARCA")
    print("="*70)

    negozi = negozi_nel_db()

    for negozio in negozi:
        variazioni = variazioni_per_marca(negozio)
        diminuiti = len([v for v in variazioni if v["variazione"] == "diminuito"])
        aumentati = len([v for v in variazioni if v["variazione"] == "aumentato"])

        print(f"\n{negozio}:")
        print(f"  📉 Diminuiti: {diminuiti}")
        print(f"  📈 Aumentati: {aumentati}")
        print(f"  Totale variazioni: {diminuiti + aumentati}")


def main():
    import sys

    if len(sys.argv) > 1:
        comando = sys.argv[1]

        if comando == "marca" and len(sys.argv) > 2:
            marca = sys.argv[2]
            stampa_variazioni_marca(marca)
        elif comando == "riepilogo":
            stampa_riepilogo()
        else:
            limite = int(sys.argv[1]) if comando.isdigit() else 30
            stampa_variazioni_recenti(limite)
    else:
        # Interattivo
        print("\n=== INTERFACCIA VARIAZIONI PREZZI ===")
        print("\n1. Vedi ultime variazioni")
        print("2. Vedi riepilogo per marca")
        print("3. Vedi variazioni marca specifica")
        print("q. Esci")

        while True:
            scelta = input("\nScelta: ").strip()

            if scelta == "1":
                limite = input("Numero di risultati (default 30): ").strip()
                limite = int(limite) if limite.isdigit() else 30
                stampa_variazioni_recenti(limite)
            elif scelta == "2":
                stampa_riepilogo()
            elif scelta == "3":
                print("\nMarche disponibili:", negozi_nel_db())
                marca = input("Marca: ").strip()
                if marca:
                    stampa_variazioni_marca(marca)
            elif scelta.lower() == "q":
                break
            else:
                print("Comando non valido")


if __name__ == "__main__":
    main()

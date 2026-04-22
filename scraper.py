"""
scraper.py — B2B Industrial Component Price Scraper
======================================================
Autore  : Senior Data Engineer
Versione: 1.0.0
Scopo   : Estrae prezzi del codice MPN specificato da RS Components,
          Farnell e TME. In caso di blocco (403, Cloudflare, timeout)
          inserisce automaticamente dati mock realistici senza crashare.

Dipendenze:
    pip install requests beautifulsoup4
"""

import json
import time
import random
import logging
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIGURAZIONE LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# COSTANTI GLOBALI
# ---------------------------------------------------------------------------
MPN_TARGET = "LRS-100-12"           # Codice MPN da cercare
OUTPUT_FILE = "data.json"           # File di output
REQUEST_TIMEOUT = 12                # Secondi prima del timeout
RETRY_ATTEMPTS = 2                  # Tentativi per ciascun distributore
DELAY_BETWEEN_REQUESTS = (2, 5)     # Pausa casuale in secondi (min, max)

# Pool di User-Agent realistici (desktop + mobile) — ruotati ad ogni richiesta
USER_AGENTS = [
    # Chrome su Windows 10
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox su Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Edge su Windows 10
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Safari su macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    # Chrome su Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
]

# ---------------------------------------------------------------------------
# DATI MOCK REALISTICI
# Usati come fallback quando il sito blocca la richiesta (403, Cloudflare, ecc.)
# Consentono di testare l'interfaccia a valle anche senza dati reali.
# ---------------------------------------------------------------------------
MOCK_DATA: dict[str, dict] = {
    "RS Components": {
        "prezzo_unitario": 32.45,
        "disponibilita": 540,
        "consegna": "24h",
        "url": "https://it.rs-online.com/web/p/alimentatori-switching/LRS-100-12",
        "_mock": True,
    },
    "Farnell": {
        "prezzo_unitario": 29.80,
        "disponibilita": 120,
        "consegna": "48h",
        "url": "https://it.farnell.com/mean-well/lrs-100-12/p/3528649",
        "_mock": True,
    },
    "TME": {
        "prezzo_unitario": 21.90,
        "disponibilita": 1200,
        "consegna": "48h",
        "url": "https://www.tme.eu/it/details/lrs-100-12/",
        "_mock": True,
    },
}


# ---------------------------------------------------------------------------
# UTILITÀ: HEADERS E RICHIESTE
# ---------------------------------------------------------------------------

def get_headers() -> dict:
    """
    Costruisce un set di headers HTTP realistici con User-Agent casuale.
    Simula un browser desktop standard per ridurre il rischio di blocco
    da parte dei sistemi anti-bot (Cloudflare, Akamai, ecc.).
    """
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        # Sec-Fetch headers: presenti in tutti i browser moderni (Chrome 80+)
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "DNT": "1",   # Do Not Track — flag standard, presente in molti browser
    }


def safe_get(url: str, session: requests.Session) -> Optional[requests.Response]:
    """
    Esegue una richiesta GET con retry automatico e backoff esponenziale.

    Gestisce esplicitamente:
    - Timeout di connessione/lettura
    - Errori di connessione (DNS, refused, ecc.)
    - Status code 403 (Forbidden) e 429 (Too Many Requests)
    - Qualsiasi altra eccezione di rete

    Restituisce la Response se status == 200, altrimenti None.
    """
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            log.info(f"  → GET {url}  (tentativo {attempt}/{RETRY_ATTEMPTS})")
            resp = session.get(
                url,
                headers=get_headers(),   # Nuovo User-Agent ad ogni tentativo
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            if resp.status_code == 200:
                log.info(f"  ✓ HTTP 200 OK — ricevuti {len(resp.content):,} byte")
                return resp

            elif resp.status_code in (403, 429):
                # 403 = Forbidden (Cloudflare/IP ban), 429 = Rate limited
                log.warning(
                    f"  ⚠ HTTP {resp.status_code} — "
                    f"probabile protezione anti-bot o rate-limit"
                )
                return None   # Inutile riprovare, il blocco è intenzionale

            else:
                log.warning(f"  ⚠ HTTP {resp.status_code} inatteso")

        except requests.exceptions.Timeout:
            log.warning(f"  ⚠ Timeout ({REQUEST_TIMEOUT}s) al tentativo {attempt}")

        except requests.exceptions.ConnectionError as e:
            log.warning(f"  ⚠ Errore di connessione: {e}")

        except requests.exceptions.TooManyRedirects:
            log.warning("  ⚠ Troppi redirect — URL probabilmente non valido")
            return None   # Inutile riprovare

        except requests.exceptions.RequestException as e:
            log.error(f"  ✗ Errore requests imprevisto: {type(e).__name__}: {e}")
            return None

        # Backoff esponenziale tra i retry (es. 2-4s, poi 4-8s)
        if attempt < RETRY_ATTEMPTS:
            wait = attempt * random.uniform(2.0, 4.0)
            log.info(f"  … attendo {wait:.1f}s prima del retry\n")
            time.sleep(wait)

    log.warning(f"  ✗ Tutti i {RETRY_ATTEMPTS} tentativi esauriti")
    return None


# ---------------------------------------------------------------------------
# PARSER DEDICATI PER OGNI DISTRIBUTORE
# ---------------------------------------------------------------------------
# Ogni funzione riceve l'HTML grezzo e il codice MPN.
# Restituisce un dict con i campi necessari, oppure lancia un'eccezione
# (ValueError / AttributeError) che viene catturata dal caller.
#
# NOTA: I selettori CSS/attributi riflettono la struttura HTML osservata
# nei siti a Maggio 2024. Se il layout cambia, aggiornare solo queste funzioni.
# ---------------------------------------------------------------------------

def parse_rs_components(html: str, mpn: str) -> dict:
    """
    Parser per RS Components (it.rs-online.com).
    RS mostra il prezzo nella scheda prodotto con classi CSS stabili.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Tentativi multipli di selector per resilienza ai cambi di layout
    price_tag = (
        soup.find("span", class_="price-info__price")
        or soup.find("span", attrs={"data-testid": "price"})
        or soup.find("span", class_="price")
    )
    if not price_tag:
        raise ValueError("Selettore prezzo non trovato nella pagina RS Components")

    # Normalizza il testo: "32,45 €" → 32.45
    raw_price = price_tag.get_text(strip=True)
    price = _parse_price(raw_price)

    # Disponibilità a stock
    stock_tag = (
        soup.find("span", class_="stock-info__quantity")
        or soup.find("td", attrs={"data-column": "availability"})
    )
    stock = _parse_stock(stock_tag.get_text() if stock_tag else "0")

    # URL canonico del prodotto (evita URL di ricerca)
    canonical = soup.find("link", rel="canonical")
    url = canonical["href"] if canonical else f"https://it.rs-online.com/web/c/?searchTerm={mpn}"

    return {
        "prezzo_unitario": price,
        "disponibilita": stock,
        "consegna": "24h",
        "url": url,
        "_mock": False,
    }


def parse_farnell(html: str, mpn: str) -> dict:
    """
    Parser per Farnell / element14 (it.farnell.com).
    Farnell usa microdata schema.org (itemprop="price") oltre ai tag CSS.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Priorità al microdata (più stabile dei nomi CSS)
    price_tag = (
        soup.find("span", attrs={"itemprop": "price"})
        or soup.find("span", class_="price")
        or soup.find("p", class_="productPrice")
    )
    if not price_tag:
        raise ValueError("Selettore prezzo non trovato nella pagina Farnell")

    # Il microdata usa l'attributo content per il valore numerico
    raw_price = price_tag.get("content") or price_tag.get_text(strip=True)
    price = _parse_price(str(raw_price))

    # Disponibilità
    stock_tag = (
        soup.find("span", class_="availability")
        or soup.find("span", attrs={"itemprop": "availability"})
    )
    stock = _parse_stock(stock_tag.get_text() if stock_tag else "0")

    return {
        "prezzo_unitario": price,
        "disponibilita": stock,
        "consegna": "48h",
        "url": f"https://it.farnell.com/search?st={mpn}",
        "_mock": False,
    }


def parse_tme(html: str, mpn: str) -> dict:
    """
    Parser per TME — Transfer Multisort Elektronik (tme.eu).
    TME usa una tabella prezzi con classi CSS specifiche.
    """
    soup = BeautifulSoup(html, "html.parser")

    price_tag = (
        soup.find("div", class_="price-value")
        or soup.find("span", class_="priceGross")
        or soup.find("td", class_="price")
    )
    if not price_tag:
        raise ValueError("Selettore prezzo non trovato nella pagina TME")

    price = _parse_price(price_tag.get_text(strip=True))

    # TME mostra la disponibilità in magazzino EU
    stock_tag = (
        soup.find("span", class_="availability-value")
        or soup.find("td", class_="availability")
    )
    stock = _parse_stock(stock_tag.get_text() if stock_tag else "0")

    return {
        "prezzo_unitario": price,
        "disponibilita": stock,
        "consegna": "48h",
        "url": f"https://www.tme.eu/it/katalog/?search={mpn}",
        "_mock": False,
    }


# ---------------------------------------------------------------------------
# HELPER DI PARSING
# ---------------------------------------------------------------------------

def _parse_price(raw: str) -> float:
    """
    Converte stringhe di prezzo eterogenee in float.
    Gestisce: "32,45 €", "€ 32.45", "32.450,00", "GBP 28.99", ecc.
    """
    # Rimuovi simboli valuta e spazi
    cleaned = raw.replace("€", "").replace("£", "").replace("$", "").strip()

    # Gestione del formato europeo con punto come separatore migliaia
    # Es: "1.234,56" → 1234.56
    if "," in cleaned and "." in cleaned:
        if cleaned.index(".") < cleaned.index(","):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    # Estrai solo la prima sequenza numerica valida
    digits = "".join(c for c in cleaned if c.isdigit() or c == ".")
    if not digits:
        raise ValueError(f"Impossibile estrarre prezzo da: '{raw}'")

    return round(float(digits), 2)


def _parse_stock(raw: str) -> int:
    """
    Estrae un numero intero di disponibilità da testo come
    "Disponibili: 540 pz" o "In stock: 1,200".
    """
    digits = "".join(filter(str.isdigit, raw.replace(",", "").replace(".", "")))
    return int(digits) if digits else 0


# ---------------------------------------------------------------------------
# CONFIGURAZIONE DISTRIBUTORI
# Mappa: nome → { url di ricerca, funzione parser }
# Per aggiungere un nuovo distributore: aggiungi qui + scrivi il parser sopra.
# ---------------------------------------------------------------------------
DISTRIBUTORS: dict[str, dict] = {
    "RS Components": {
        "url": f"https://it.rs-online.com/web/c/?searchTerm={MPN_TARGET}",
        "parser": parse_rs_components,
    },
    "Farnell": {
        "url": f"https://it.farnell.com/search?st={MPN_TARGET}",
        "parser": parse_farnell,
    },
    "TME": {
        "url": f"https://www.tme.eu/it/katalog/?search={MPN_TARGET}&s_order=1",
        "parser": parse_tme,
    },
}


# ---------------------------------------------------------------------------
# LOGICA DI SCRAPING PRINCIPALE
# ---------------------------------------------------------------------------

def scrape_distributor(
    name: str,
    config: dict,
    mpn: str,
    session: requests.Session,
) -> dict:
    """
    Tenta di scrapare un singolo distributore seguendo questo flusso:

        1. GET della pagina di ricerca via safe_get()
        2. Parsing dell'HTML con la funzione parser dedicata
        3. Se qualsiasi passo fallisce → attiva fallback mock (NO crash)

    Restituisce sempre un dict valido, reale o mock.
    """
    log.info(f"\n[{name}] ─────────────────────────────────────────")
    log.info(f"[{name}] Avvio scraping...")

    try:
        # STEP 1: recupero pagina
        response = safe_get(config["url"], session)
        if response is None:
            # safe_get ha già loggato il motivo del fallimento
            raise ConnectionError(f"Nessuna risposta valida da {name}")

        # STEP 2: parsing HTML
        data = config["parser"](response.text, mpn)
        log.info(
            f"[{name}] ✓ Dati REALI estratti — "
            f"prezzo: €{data['prezzo_unitario']:.2f} | "
            f"stock: {data['disponibilita']} pz"
        )
        return data

    # Errori di parsing: struttura HTML diversa dall'attesa
    except (ValueError, AttributeError, TypeError, KeyError) as e:
        log.warning(f"[{name}] ⚠ Parsing fallito → {type(e).__name__}: {e}")

    # Errori di rete non gestiti da safe_get
    except (ConnectionError, OSError) as e:
        log.warning(f"[{name}] ⚠ Errore rete → {e}")

    # Safety net: nessun errore deve mai far crashare l'intero script
    except Exception as e:
        log.error(f"[{name}] ✗ Errore imprevisto → {type(e).__name__}: {e}")

    # FALLBACK: dati mock realistici
    mock = MOCK_DATA[name].copy()
    log.info(
        f"[{name}] 📦 Uso dati MOCK — "
        f"prezzo: €{mock['prezzo_unitario']:.2f} | "
        f"stock: {mock['disponibilita']} pz"
    )
    return mock


def scrape_all(mpn: str) -> dict:
    """
    Orchestra lo scraping su tutti i distributori configurati.

    - Usa una sessione requests condivisa (efficienza: cookie, keep-alive)
    - Inserisce pause casuali tra richieste per mimare comportamento umano
    - Restituisce il dict componente completo con tutti i fornitori
    """
    log.info(f"\n{'═'*60}")
    log.info(f"  Avvio scraping | MPN: {mpn}")
    log.info(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"{'═'*60}")

    supplier_results = []

    with requests.Session() as session:
        # Header base sulla sessione (sovrascritti per ogni singola GET)
        session.headers.update(get_headers())

        distributor_list = list(DISTRIBUTORS.items())

        for i, (name, config) in enumerate(distributor_list):
            raw = scrape_distributor(name, config, mpn, session)

            # Costruisce il record fornitore nel formato JSON di output
            entry = {
                "nome": name,
                "prezzo_unitario": raw["prezzo_unitario"],
                "disponibilita": raw["disponibilita"],
                "consegna": raw["consegna"],
                "url": raw["url"],
            }
            # Flag di debug visibile nel JSON solo se i dati sono mock
            if raw.get("_mock"):
                entry["_dati_mock"] = True

            supplier_results.append(entry)

            # Pausa tra richieste (non dopo l'ultimo distributore)
            if i < len(distributor_list) - 1:
                delay = random.uniform(*DELAY_BETWEEN_REQUESTS)
                log.info(f"\n  ⏱  Pausa {delay:.1f}s prima del prossimo distributore...")
                time.sleep(delay)

    # Riepilogo finale
    real_count = sum(1 for r in supplier_results if not r.get("_dati_mock"))
    mock_count = len(supplier_results) - real_count

    log.info(f"\n{'═'*60}")
    log.info(f"  Scraping completato")
    log.info(f"  Dati reali : {real_count}/{len(supplier_results)}")
    log.info(f"  Dati mock  : {mock_count}/{len(supplier_results)}")
    if mock_count:
        log.warning(f"  ⚠  Alcuni distributori hanno restituito dati mock")
    log.info(f"{'═'*60}\n")

    return {
        "mpn": mpn,
        "nome": "Alimentatore Mean Well 100W 12V",
        "fornitori": supplier_results,
    }


def save_to_json(data: list, filepath: str) -> None:
    """
    Serializza la lista dei componenti in un file JSON UTF-8 formattato.
    Sovrascrive il file se già esistente.
    """
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"✅ Risultati salvati in '{filepath}'")


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    # 1. Esegui lo scraping per il codice MPN target
    component = scrape_all(MPN_TARGET)

    # 2. Impacchetta in array (il formato JSON supporta più componenti)
    output_data = [component]

    # 3. Salva su disco
    save_to_json(output_data, OUTPUT_FILE)

    # 4. Preview a terminale per verifica rapida
    print("\n📄 Contenuto di data.json:")
    print(json.dumps(output_data, ensure_ascii=False, indent=2))

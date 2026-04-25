"""
scraper.py — B2B Price Fetcher via API ufficiali
=================================================
Autore  : Senior Data Engineer
Versione: 2.0.0 (API-based, no scraping)

Distributori e metodo di accesso:
  • Farnell / element14 → REST API pubblica (gratuita con registrazione)
      Doc: https://partner.element14.com/docs/Product_Search_API_REST__Description
  • TME                 → REST API pubblica (gratuita con registrazione)
      Doc: https://developers.tme.eu/documentation/
  • RS Components       → Nessuna API pubblica gratuita disponibile.
      Fallback: dati mock. Se sei cliente business puoi richiedere
      la RS Procurement API al tuo account manager.

Setup credenziali:
  1. Registrati su partner.element14.com → copia la tua API key
  2. Registrati su developers.tme.eu     → copia API Token e Secret
  3. Salvali come GitHub Secrets (Settings → Secrets → Actions):
       ELEMENT14_API_KEY
       TME_API_TOKEN
       TME_API_SECRET
  4. Il workflow li espone come variabili d'ambiente (vedi update_data.yml)

Dipendenze:
  pip install requests
"""

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from datetime import datetime
from typing import Optional

import requests

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURAZIONE LOGGING
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# COSTANTI
# ──────────────────────────────────────────────────────────────────────────────
MPN_TARGET   = "LRS-100-12"
OUTPUT_FILE  = "data.json"
TIMEOUT      = 15   # secondi per ogni chiamata API

# Credenziali lette da variabili d'ambiente (mai hardcodate nel codice)
ELEMENT14_API_KEY = os.environ.get("ELEMENT14_API_KEY", "")
TME_API_TOKEN     = os.environ.get("TME_API_TOKEN",     "")
TME_API_SECRET    = os.environ.get("TME_API_SECRET",    "")

# ──────────────────────────────────────────────────────────────────────────────
# DATI MOCK — fallback quando le credenziali mancano o l'API è irraggiungibile
# ──────────────────────────────────────────────────────────────────────────────
MOCK: dict[str, dict] = {
    "Farnell": {
        "prezzo_unitario": 29.80,
        "disponibilita":   120,
        "consegna":        "48h",
        "url":             f"https://it.farnell.com/search?st={MPN_TARGET}",
        "_mock":           True,
    },
    "TME": {
        "prezzo_unitario": 21.90,
        "disponibilita":   1200,
        "consegna":        "48h",
        "url":             f"https://www.tme.eu/it/katalog/?search={MPN_TARGET}",
        "_mock":           True,
    },
    "RS Components": {
        "prezzo_unitario": 32.45,
        "disponibilita":   540,
        "consegna":        "24h",
        # RS non ha API pubblica: questo è sempre mock.
        # Sostituibile con RS Procurement API per clienti business.
        "url":             f"https://it.rs-online.com/web/c/?searchTerm={MPN_TARGET}",
        "_mock":           True,
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# CLIENT FARNELL / ELEMENT14
# Documentazione: https://partner.element14.com/docs/Product_Search_API_REST__Description
# ──────────────────────────────────────────────────────────────────────────────

class FarnellClient:
    """
    Wrapper per la element14 Product Search API (REST).

    Endpoint base per l'Italia: it.farnell.com
    Autenticazione: API Key come parametro query (&callsign=...)
    Risposta: JSON con lista prodotti, prezzi per scaglione e stock.
    """

    # Endpoint per area geografica — usa quello più vicino alla tua sede
    BASE_URL = (
        "https://api.element14.com/catalog/products"
        "?callsign=it.farnell"          # Negozio italiano
        "&resultsSettings.offset=0"
        "&resultsSettings.numberOfResults=1"
        "&resultsSettings.responseGroup=large"  # Include prezzi e stock
        "&term=manuPartNum:{mpn}"        # Ricerca per codice MPN esatto
        "&storeInfo.id=it.farnell"
    )

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
        })

    def fetch(self, mpn: str) -> Optional[dict]:
        """
        Chiama l'API e restituisce un dict normalizzato oppure None.
        """
        if not self.api_key:
            log.warning("[Farnell] API key non impostata → mock")
            return None

        url = self.BASE_URL.format(mpn=urllib.parse.quote(mpn))
        url += f"&callsign=it.farnell&storeInfo.id=it.farnell&resultsSettings.offset=0"
        # Aggiunge la key come parametro separato per chiarezza
        params = {"callsign": "it.farnell", "id": self.api_key}

        # URL completo corretto (la key va in header o come query param a seconda
        # della versione API — consulta la tua dashboard partner.element14.com)
        full_url = (
            f"https://api.element14.com/catalog/products"
            f"?callsign=it.farnell"
            f"&resultsSettings.offset=0"
            f"&resultsSettings.numberOfResults=1"
            f"&resultsSettings.responseGroup=large"
            f"&term=manuPartNum:{urllib.parse.quote(mpn)}"
            f"&storeInfo.id=it.farnell"
            f"&id={self.api_key}"
        )

        log.info(f"[Farnell] GET {full_url[:80]}…")
        resp = self.session.get(full_url, timeout=TIMEOUT)
        resp.raise_for_status()

        data = resp.json()

        # Struttura risposta:
        # { "manufacturerPartNumberSearchReturn": { "products": [...] } }
        products = (
            data
            .get("manufacturerPartNumberSearchReturn", {})
            .get("products", [])
        )
        if not products:
            log.warning("[Farnell] Nessun prodotto trovato per MPN")
            return None

        product = products[0]

        # Prezzo: prendi il primo scaglione (qty minima)
        price_list = product.get("prices", [])
        price = float(price_list[0]["cost"]) if price_list else 0.0

        # Stock
        stock_info = product.get("stock", {})
        stock = int(stock_info.get("level", 0))

        # URL scheda prodotto
        sku = product.get("sku", "")
        product_url = f"https://it.farnell.com/{sku}" if sku else MOCK["Farnell"]["url"]

        return {
            "prezzo_unitario": round(price, 2),
            "disponibilita":   stock,
            "consegna":        "48h",
            "url":             product_url,
            "_mock":           False,
        }


# ──────────────────────────────────────────────────────────────────────────────
# CLIENT TME
# Documentazione: https://developers.tme.eu/documentation/
# Autenticazione: HMAC-SHA1 sulla query string (firma ogni richiesta)
# ──────────────────────────────────────────────────────────────────────────────

class TMEClient:
    """
    Wrapper per la TME REST API.

    TME usa un sistema di firma HMAC-SHA1:
    ogni richiesta deve includere un parametro Signature calcolato
    sull'URL + parametri + secret. Questo client lo gestisce in automatico.
    """

    BASE_URL = "https://api.tme.eu"

    def __init__(self, api_token: str, api_secret: str):
        self.token  = api_token
        self.secret = api_secret
        self.session = requests.Session()

    def _sign(self, method_url: str, params: dict) -> str:
        """
        Calcola la firma HMAC-SHA1 richiesta da TME.
        Algoritmo ufficiale: https://developers.tme.eu/documentation/authorization
        """
        # Ordina i parametri e codificali
        encoded = urllib.parse.urlencode(sorted(params.items()))
        # Stringa da firmare: METHOD&URL_encoded&params_encoded
        base = f"POST&{urllib.parse.quote(method_url, safe='')}&{urllib.parse.quote(encoded, safe='')}"
        # Firma con HMAC-SHA1
        signature = hmac.new(
            self.secret.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        import base64
        return base64.b64encode(signature).decode("utf-8")

    def fetch(self, mpn: str) -> Optional[dict]:
        """
        Cerca il MPN tramite API TME e restituisce un dict normalizzato o None.
        """
        if not self.token or not self.secret:
            log.warning("[TME] Credenziali non impostate → mock")
            return None

        endpoint = f"{self.BASE_URL}/Products/Search/json"

        params = {
            "Token":          self.token,
            "Language":       "IT",
            "Country":        "IT",
            "SearchPlain":    mpn,
            "SearchCategory": "",
            "SearchWithStock": 1,
        }

        # Aggiunge la firma HMAC
        params["Signature"] = self._sign(endpoint, params)

        log.info(f"[TME] POST {endpoint} (MPN={mpn})")
        resp = self.session.post(endpoint, data=params, timeout=TIMEOUT)
        resp.raise_for_status()

        data = resp.json()

        # Struttura risposta TME:
        # { "Status": "OK", "Data": { "ProductList": [...] } }
        if data.get("Status") != "OK":
            log.warning(f"[TME] API error: {data.get('Status')}")
            return None

        products = data.get("Data", {}).get("ProductList", [])
        if not products:
            log.warning("[TME] Nessun prodotto trovato")
            return None

        # Primo risultato più rilevante
        product = products[0]
        symbol  = product.get("Symbol", mpn)

        # Recupera dettagli prezzi e stock in una seconda chiamata
        return self._fetch_prices(symbol)

    def _fetch_prices(self, symbol: str) -> Optional[dict]:
        """
        Seconda chiamata API TME: recupera prezzi e disponibilità per simbolo.
        """
        endpoint = f"{self.BASE_URL}/Products/GetPricesAndStocks/json"
        params = {
            "Token":    self.token,
            "Language": "IT",
            "Country":  "IT",
            "SymbolList[0]": symbol,
        }
        params["Signature"] = self._sign(endpoint, params)

        resp = self.session.post(endpoint, data=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if data.get("Status") != "OK":
            return None

        product_list = data.get("Data", {}).get("ProductList", [])
        if not product_list:
            return None

        product = product_list[0]
        # Prendi il primo scaglione di prezzo
        price_list = product.get("PriceList", [])
        price = float(price_list[0]["PriceValue"]) if price_list else 0.0
        stock = int(product.get("Amount", 0))

        return {
            "prezzo_unitario": round(price, 2),
            "disponibilita":   stock,
            "consegna":        "48h",
            "url":             f"https://www.tme.eu/it/details/{symbol.lower()}/",
            "_mock":           False,
        }


# ──────────────────────────────────────────────────────────────────────────────
# RS COMPONENTS — placeholder
# RS non offre API pubblica gratuita. Se sei cliente business, contatta
# il tuo account manager per accesso alla RS Procurement/PUNCHOUT API.
# Documentazione (richiede accesso): https://uk.rs-online.com/web/generalDisplay.html?id=about/api
# ──────────────────────────────────────────────────────────────────────────────

def fetch_rs_components(mpn: str) -> dict:
    """
    Placeholder RS Components.
    Restituisce sempre dati mock con una nota nel log.
    Da sostituire con la RS Procurement API se si dispone delle credenziali.
    """
    log.info(
        "[RS Components] Nessuna API pubblica disponibile. "
        "Usa la RS Procurement API (clienti business) per dati reali. "
        "Inserisco dati mock."
    )
    return MOCK["RS Components"].copy()


# ──────────────────────────────────────────────────────────────────────────────
# ORCHESTRATORE PRINCIPALE
# ──────────────────────────────────────────────────────────────────────────────

def fetch_all(mpn: str) -> dict:
    """
    Interroga tutte le fonti e restituisce il componente completo.
    Ogni fetch è isolato in un try/except: un errore su un distributore
    non blocca gli altri e non fa crashare lo script.
    """
    log.info(f"\n{'═'*60}")
    log.info(f"  Avvio fetch API | MPN: {mpn}")
    log.info(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"{'═'*60}")

    suppliers = []

    # ── Farnell ──────────────────────────────────────────────────────────────
    try:
        client = FarnellClient(ELEMENT14_API_KEY)
        result = client.fetch(mpn)
        if result is None:
            raise ValueError("Nessun risultato dall'API Farnell")
        result["nome"] = "Farnell"
        log.info(f"[Farnell] ✓ Prezzo reale: €{result['prezzo_unitario']:.2f}")
    except Exception as e:
        log.warning(f"[Farnell] ⚠ {type(e).__name__}: {e} → mock")
        result = {**MOCK["Farnell"], "nome": "Farnell"}

    suppliers.append({
        "nome":             result["nome"],
        "prezzo_unitario":  result["prezzo_unitario"],
        "disponibilita":    result["disponibilita"],
        "consegna":         result["consegna"],
        "url":              result["url"],
        **( {"_dati_mock": True} if result.get("_mock") else {} ),
    })

    time.sleep(0.8)   # pausa cortese tra chiamate API

    # ── TME ──────────────────────────────────────────────────────────────────
    try:
        client = TMEClient(TME_API_TOKEN, TME_API_SECRET)
        result = client.fetch(mpn)
        if result is None:
            raise ValueError("Nessun risultato dall'API TME")
        result["nome"] = "TME"
        log.info(f"[TME] ✓ Prezzo reale: €{result['prezzo_unitario']:.2f}")
    except Exception as e:
        log.warning(f"[TME] ⚠ {type(e).__name__}: {e} → mock")
        result = {**MOCK["TME"], "nome": "TME"}

    suppliers.append({
        "nome":             result["nome"],
        "prezzo_unitario":  result["prezzo_unitario"],
        "disponibilita":    result["disponibilita"],
        "consegna":         result["consegna"],
        "url":              result["url"],
        **( {"_dati_mock": True} if result.get("_mock") else {} ),
    })

    time.sleep(0.8)

    # ── RS Components (mock) ──────────────────────────────────────────────────
    try:
        result = fetch_rs_components(mpn)
        result["nome"] = "RS Components"
    except Exception as e:
        log.warning(f"[RS Components] ⚠ {e} → mock")
        result = {**MOCK["RS Components"], "nome": "RS Components"}

    suppliers.append({
        "nome":             result["nome"],
        "prezzo_unitario":  result["prezzo_unitario"],
        "disponibilita":    result["disponibilita"],
        "consegna":         result["consegna"],
        "url":              result["url"],
        **( {"_dati_mock": True} if result.get("_mock") else {} ),
    })

    # ── Riepilogo ─────────────────────────────────────────────────────────────
    real  = sum(1 for s in suppliers if not s.get("_dati_mock"))
    mocks = len(suppliers) - real
    log.info(f"\n{'═'*60}")
    log.info(f"  Completato — Dati reali: {real} | Mock: {mocks}")
    log.info(f"{'═'*60}\n")

    return {
        "mpn":      mpn,
        "nome":     "Alimentatore Mean Well 100W 12V",
        "fornitori": suppliers,
    }


def save_json(data: list, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"✅ Salvato in '{path}'")


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Carica dati esistenti per recuperare lo storico
    existing = load_existing(OUTPUT_FILE)

    # Fetch prezzi aggiornati
    component = fetch_all(MPN_TARGET)

    # Merge con storico accumulato
    component_with_history = update_history(existing, component)

    # Impacchetta (supporta più MPN in futuro)
    output_data = [component_with_history]

    # Salva
    save_json(output_data, OUTPUT_FILE)
    print("
📄 data.json:")
    print(json.dumps(output_data, ensure_ascii=False, indent=2))
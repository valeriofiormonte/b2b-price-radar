import asyncio
import json
from playwright.async_api import async_playwright

# Configurazione prodotti da monitorare
TARGETS = [
    {"id": "LRS-100-12", "cat": "Elettronica", "name": "Alimentatore Mean Well"},
    {"id": "Farina 00 25kg", "cat": "Ristorazione", "name": "Farina professionale per Pizza"}
]

async def get_price(browser, url, selector):
    page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    try:
        await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        # Simula un piccolo scroll per sembrare umano
        await page.mouse.wheel(0, 500)
        await asyncio.sleep(2)
        
        element = await page.wait_for_selector(selector, timeout=10000)
        price_text = await element.inner_text()
        return price_text.strip()
    except:
        return "N/A"
    finally:
        await page.close()

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        results = []

        for target in TARGETS:
            # Esempio logica: qui dovresti mettere gli URL reali dei fornitori
            # Per ora creiamo il database strutturato per il nuovo frontend
            item_data = {
                "mpn": target["id"],
                "nome": target["name"],
                "categoria": target["cat"],
                "fornitori": [
                    {
                        "nome": "Fornitore A",
                        "prezzo_unitario": 15.50 if target["cat"] == "Ristorazione" else 25.00,
                        "disponibilita": 500,
                        "consegna": "24h",
                        "url": "#"
                    },
                    {
                        "nome": "Fornitore B",
                        "prezzo_unitario": 14.80 if target["cat"] == "Ristorazione" else 27.50,
                        "disponibilita": 20,
                        "consegna": "48h",
                        "url": "#"
                    }
                ]
            }
            results.append(item_data)

        with open('data.json', 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=4, ensure_ascii=False)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())

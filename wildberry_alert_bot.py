"""wildberry_alert_bot.py  –  FINAL+ DRY PURCHASES ONLY (2025-05)
Multi-source DRY BUYER radar bot
===========================================================
• Kaynaklar: OLX (Çoklu Ülkeler), Facebook Grupları, SEAP RSS, Agrobiznis.ro, Google Alerts, eBay, Alibaba
• Ürünler: Kuru Kuşburnu, Aronya, Mürver, Deniz İğdesi, Lavanta, Kekik
• Yalnızca “dried” / “uscat” içeren ALICI ilanları (cumpăr/buy) yakalar
• Gördüklerini seen.json’da saklar, Telegram’da bildirir
• Her yeni ilan tespitinde last_alert.txt UTC zaman damgası yazar
• GitHub Actions cron (*/15 dk) ile ücretsiz çalışır
"""
from __future__ import annotations
import os, re, json, hashlib, logging, requests
from dataclasses import dataclass
from typing import Iterable, List
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ➤ 1) GLOBAL CONFIG
TG_TOKEN  = os.getenv("TG_TOKEN", "")
TG_CHAT   = os.getenv("TG_CHAT", "")
HEADERS   = {"User-Agent": "WildBerryBot/1.0"}

# ➤ Anahtar sözcükler
KEYWORDS: List[str] = [
    r"cump[ăa]r macese uscat[ăa]", r"buy dried rosehip",
    r"cump[ăa]r aronia uscat[ăa]",    r"buy dried aronia",    r"buy dried chokeberry",
    r"cump[ăa]r soc uscat",           r"buy dried elderberry",
    r"cump[ăa]r c[ăa]tin[ăa] uscat[ăa]", r"buy dried sea[- ]?buckthorn",
    r"cump[ăa]r lavand[ăa] uscat[ăa]", r"buy dried lavender",
    r"cump[ăa]r cimbru uscat",        r"buy dried thyme",
]

SEEN_FILE = Path("seen.json")
_seen: set[str] = set(json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else [])

@dataclass
class Advert:
    adv_id: str
    platform: str
    title: str
    price: str
    url: str

class BaseCrawler:
    platform: str = "base"
    def crawl(self) -> Iterable[Advert]:
        raise NotImplementedError

# ➤ Crawlers
class OLXCrawler(BaseCrawler):
    platform = "OLX"
    URLS = [
        "https://www.olx.ro/oferte/q-{kw}/",
        "https://www.olx.pl/oferty/q-{kw}/",
        "https://www.olx.hu/allas/q-{kw}/",
    ]
    def crawl(self):
        for url in self.URLS:
            for pat in KEYWORDS:
                slug = re.sub(r"[^\w]+", "-", pat.split()[1])
                try:
                    resp = requests.get(url.format(kw=slug), headers=HEADERS, timeout=15)
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for card in soup.select("div[data-testid='offer-card']"):
                        title = card.select_one("h6").text.strip()
                        if not re.search(r"uscat|dried", title, re.I):
                            continue
                        if not any(re.search(pat, title, re.I) for pat in KEYWORDS):
                            continue
                        a = card.find("a", href=True)
                        link = a["href"].split("#")[0]
                        adv_id = link.split("-")[-1].rstrip(".html")
                        price = (card.select_one("p[data-testid='ad-price']") or {}).get_text(strip=True, default="-")
                        yield Advert(adv_id, self.platform, title, price, link)
                except Exception as e:
                    logging.error("OLX error: %s", e)

class FBGroupCrawler(BaseCrawler):
    platform = "Facebook"
    GROUPS = [
        "https://m.facebook.com/groups/987430631290887",
        "https://m.facebook.com/groups/1413126708753304",
        "https://m.facebook.com/groups/1292085071431761",
    ]
    def crawl(self):
        for url in self.GROUPS:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=20)
                soup = BeautifulSoup(resp.text, "html.parser")
                for post in soup.find_all(string=re.compile(r"cump[ăa]r.*uscat|buy.*dried", re.I)):
                    link = post.find_parent("a", href=True)
                    adv_id = hashlib.md5(link["href"].encode()).hexdigest()[:12]
                    title = post.strip()
                    yield Advert(adv_id, self.platform, title, "-", "https://m.facebook.com" + link["href"])
            except Exception as e:
                logging.error("Facebook error: %s", e)

class SEAPCrawler(BaseCrawler):
    platform = "SEAP"
    FEED = "https://e-licitatie.ro/pub/notices-rss?tip=3&cuvinte_cheie=macese"
    def crawl(self):
        try:
            resp = requests.get(self.FEED, timeout=15)
            soup = BeautifulSoup(resp.text, "xml")
            for itm in soup.find_all("item"):
                title = itm.title.text
                if not re.search(r"macese.*uscat", title, re.I):
                    continue
                adv_id = itm.guid.text.strip()
                yield Advert(adv_id, self.platform, title, "-", itm.link.text)
        except Exception as e:
            logging.error("SEAP error: %s", e)

class AgroCrawler(BaseCrawler):
    platform = "Agrobiznis"
    URL = "https://agrobiznis.ro/category/anunturi/?s=macese"
    def crawl(self):
        try:
            resp = requests.get(self.URL, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            for art in soup.select("article"):
                title = art.h2.text.strip()
                if not re.search(r"cump[ăa]r.*uscat", title, re.I):
                    continue
                link = art.find("a", href=True)["href"]
                adv_id = link.split("/")[-1]
                yield Advert(adv_id, self.platform, title, "-", link)
        except Exception as e:
            logging.error("Agro error: %s", e)

class GoogleAlertCrawler(BaseCrawler):
    platform = "Google"
    FEEDS = ["https://alerts.google.com/rss/16720972385691715855/9476993441358872255"]
    def crawl(self):
        for feed in self.FEEDS:
            try:
                resp = requests.get(feed, timeout=15)
                soup = BeautifulSoup(resp.text, "xml")
                for itm in soup.find_all("item"):
                    title = itm.title.text
                    if not re.search(r"dried|dry", title, re.I):
                        continue
                    adv_id = hashlib.md5(itm.link.text.encode()).hexdigest()[:12]
                    yield Advert(adv_id, self.platform, title, "-", itm.link.text)
            except Exception as e:
                logging.error("GoogleAlerts error: %s", e)

class EbayCrawler(BaseCrawler):
    platform = "eBay"
    URL = "https://www.ebay.com/sch/i.html?_nkw={kw}"
    def crawl(self):
        for pat in KEYWORDS:
            slug = "+".join(pat.split()[1:])
            try:
                resp = requests.get(self.URL.format(kw=slug), headers=HEADERS, timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")
                for item in soup.select("li.s-item"):
                    a = item.select_one("a.s-item__link")
                    if not a:
                        continue
                    title = a.get_text(strip=True)
                    # Sadece anahtar kelime kalıplarıyla eşleşen ilanlar
                    if not any(re.search(pat, title, re.I) for pat in KEYWORDS):
                        continue
                    link = a["href"]
                    price_el = item.select_one(".s-item__price")
                    price = price_el.get_text(strip=True) if price_el else "-"
                    adv_id = hashlib.md5(link.encode()).hexdigest()[:12]
                    yield Advert(adv_id, self.platform, title, price, link)
            except Exception as e:
                logging.error("eBay error: %s", e)

class AlibabaCrawler(BaseCrawler):
    platform = "Alibaba"
    URL = "https://www.alibaba.com/trade/search?fsb=y&IndexArea=product_en&SearchText={kw}"
    def crawl(self):
        for pat in KEYWORDS:
            slug = "+".join(pat.split()[1:])
            try:
                resp = requests.get(self.URL.format(kw=slug), headers=HEADERS, timeout=20)
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("div.J-offer-list-row"):
                    a = card.select_one("a.PortalCard__img-link")
                    if not a:
                        continue
                    title = card.select_one("h2").get_text(strip=True)
                    # Sadece anahtar kelime kalıplarıyla eşleşen ilanlar
                    if not any(re.search(pat, title, re.I) for pat in KEYWORDS):
                        continue
                    href = a.get("href", "")
                    link = f"https:{href}" if href.startswith("//") else href
                    adv_id = hashlib.md5(link.encode()).hexdigest()[:12]
                    yield Advert(adv_id, self.platform, title, "-", link)
            except Exception as e:
                logging.error("Alibaba error: %s", e)

ALL_CRAWLERS: List[BaseCrawler] = [
    OLXCrawler(), FBGroupCrawler(), SEAPCrawler(), AgroCrawler(),
    GoogleAlertCrawler(), EbayCrawler(), AlibabaCrawler()
]

def run_once():
    new_count = 0
    for crawler in ALL_CRAWLERS:
        for adv in crawler.crawl():
            if adv.adv_id in _seen:
                continue
            _seen.add(adv.adv_id)
            new_count += 1
            tg(f"📢 BUYER • {adv.platform}\n{adv.title}\n{adv.price}\n{adv.url}")
    if new_count:
        Path("last_alert.txt").write_text(datetime.utcnow().isoformat())
    logging.info("run_once done – %d new", new_count)
    SEEN_FILE.write_text(json.dumps(list(_seen)))

if __name__ == "__main__":
    run_once()

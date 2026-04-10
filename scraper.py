"""
Araç İhale Radar — Scraper
Tüm ihale kaynaklarından veri çekme mantığı.
"""
import asyncio
import hashlib
import random
import re
from datetime import datetime
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

from config import settings
from database import save_auction, update_scraper_health, cache_price, get_cached_price
from utils import (
    log, parse_price, parse_km, parse_year, extract_marka_model,
    normalize_sehir, KNOWN_MARKALAR,
)

ua = UserAgent(fallback="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

# İhale anahtar kelimeleri — filtreleme için
IHALE_KEYWORDS = [
    "ihale", "icra", "satış", "müzayede", "açık artırma", "haciz",
    "tasfiye", "satılık", "muhammen", "bedel",
]

ARAC_KEYWORDS = [
    "araç", "otomobil", "araba", "taşıt", "kamyon", "kamyonet",
    "motorsiklet", "motosiklet", "minibüs", "otobüs", "traktör",
    "binek", "hususi", "ticari", "suv",
]


async def fetch(session: aiohttp.ClientSession, url: str, retries: int = 3) -> Optional[str]:
    """URL'den içerik çeker — retry ve rate limiting ile."""
    for attempt in range(retries):
        try:
            await asyncio.sleep(
                random.uniform(settings.SCRAPE_DELAY_MIN, settings.SCRAPE_DELAY_MAX)
            )
            headers = {
                "User-Agent": ua.random,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            }
            timeout = aiohttp.ClientTimeout(total=20)
            async with session.get(url, headers=headers, timeout=timeout, ssl=False) as resp:
                if resp.status == 200:
                    return await resp.text()
                elif resp.status == 429:
                    log(f"Rate limited on {url}, waiting 30s")
                    await asyncio.sleep(30)
                else:
                    log(f"HTTP {resp.status} from {url}")
        except asyncio.TimeoutError:
            log(f"Timeout fetching {url} (attempt {attempt + 1})")
        except Exception as e:
            log(f"Fetch error {url}: {e}", "error")
        if attempt < retries - 1:
            await asyncio.sleep(5 * (attempt + 1))
    return None


def make_auction_id(source: str, title: str, date: str) -> str:
    """Benzersiz ihale ID'si oluşturur (MD5 hash)."""
    raw = f"{source}_{title}_{date}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def text_has_keywords(text: str, keywords: list[str]) -> bool:
    """Metinde verilen anahtar kelimelerden en az biri var mı?"""
    if not text:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def build_auction_dict(
    source: str,
    source_label: str,
    title: str,
    sehir: str = None,
    acilis_fiyati: float = None,
    ihale_tarihi: str = None,
    ihale_saati: str = None,
    ihale_yeri: str = None,
    ihale_url: str = None,
    description: str = None,
    **extra,
) -> dict:
    """Standart ihale dict'i oluşturur."""
    full_text = f"{title} {description or ''}"
    marka, model = extract_marka_model(full_text)
    yil = parse_year(full_text)
    km = parse_km(full_text) if description else None

    auction_id = make_auction_id(source, title, ihale_tarihi or "")

    return {
        "id": auction_id,
        "source": source,
        "source_label": source_label,
        "title": title,
        "marka": marka,
        "model": model,
        "yil": yil,
        "km": km,
        "sehir": normalize_sehir(sehir) if sehir else None,
        "ilce": extra.get("ilce"),
        "ihale_tarihi": ihale_tarihi,
        "ihale_saati": ihale_saati,
        "ihale_yeri": ihale_yeri,
        "acilis_fiyati": acilis_fiyati,
        "market_value": None,
        "discount_pct": None,
        "gap_tl": None,
        "hasar_durumu": extra.get("hasar_durumu"),
        "plaka": extra.get("plaka"),
        "renk": extra.get("renk"),
        "yakit": extra.get("yakit"),
        "vites": extra.get("vites"),
        "ihale_url": ihale_url,
        "ai_summary": None,
        "used_ai": 0,
        "scraped_at": datetime.now().isoformat(),
        "sent_count": 0,
        "is_active": 1,
    }


# ══════════════════════════════════════════════════════════════════
# Kaynak 1: ilan.gov.tr
# ══════════════════════════════════════════════════════════════════

async def scrape_ilan_gov(session: aiohttp.ClientSession) -> int:
    """
    ilan.gov.tr'den araç ihale ilanlarını çeker.
    Taşıt araçları ve ihale kategorilerini tarar.
    """
    source = "ilan_gov"
    source_label = "Resmi İlan"
    count = 0

    urls = [
        "https://www.ilan.gov.tr/ilan/kategori/ihale",
        "https://www.ilan.gov.tr/ilan/kategori/tasinir-mal-satisi",
    ]

    try:
        for base_url in urls:
            for page in range(1, 6):  # İlk 5 sayfa
                url = f"{base_url}?page={page}" if page > 1 else base_url
                html = await fetch(session, url)
                if not html:
                    break

                soup = BeautifulSoup(html, "lxml")

                # İlan kartlarını bul
                listings = soup.select(".ilan-list-item, .listing-item, .card, article")
                if not listings:
                    # Alternatif seçiciler
                    listings = soup.find_all("div", class_=re.compile(r"ilan|listing|item"))

                if not listings:
                    break

                for item in listings:
                    try:
                        # Başlık
                        title_el = item.select_one("h3, h4, .title, .ilan-baslik, a")
                        if not title_el:
                            continue
                        title = title_el.get_text(strip=True)

                        # Araç ile ilgili mi kontrol et
                        desc_el = item.select_one(".description, .aciklama, p")
                        desc = desc_el.get_text(strip=True) if desc_el else ""
                        full_text = f"{title} {desc}"

                        if not text_has_keywords(full_text, ARAC_KEYWORDS):
                            continue
                        if not text_has_keywords(full_text, IHALE_KEYWORDS):
                            continue

                        # URL
                        link_el = item.select_one("a[href]")
                        ihale_url = link_el["href"] if link_el else ""
                        if ihale_url and not ihale_url.startswith("http"):
                            ihale_url = f"https://www.ilan.gov.tr{ihale_url}"

                        # Fiyat
                        price_el = item.select_one(".price, .fiyat, .bedel")
                        fiyat = parse_price(price_el.get_text()) if price_el else None

                        # Şehir
                        loc_el = item.select_one(".location, .sehir, .il")
                        sehir = loc_el.get_text(strip=True) if loc_el else None

                        # Tarih
                        date_el = item.select_one(".date, .tarih, time")
                        tarih = date_el.get_text(strip=True) if date_el else None

                        auction = build_auction_dict(
                            source=source,
                            source_label=source_label,
                            title=title,
                            sehir=sehir,
                            acilis_fiyati=fiyat,
                            ihale_tarihi=tarih,
                            ihale_url=ihale_url,
                            description=desc,
                        )

                        if await save_auction(auction):
                            count += 1
                    except Exception as e:
                        log(f"ilan.gov.tr listing parse error: {e}")
                        continue

                # Sonraki sayfa var mı?
                next_btn = soup.select_one(".pagination .next, a[rel='next']")
                if not next_btn:
                    break

        await update_scraper_health(source, success=True, found_count=count)
        log(f"ilan.gov.tr: {count} yeni ilan bulundu")
    except Exception as e:
        log(f"ilan.gov.tr scraper hatası: {e}", "error")
        await update_scraper_health(source, success=False)

    return count


# ══════════════════════════════════════════════════════════════════
# Kaynak 2: adalet.gov.tr İcra İhaleleri
# ══════════════════════════════════════════════════════════════════

async def scrape_adalet_gov(session: aiohttp.ClientSession) -> int:
    """
    Adalet Bakanlığı icra satış ilanlarını çeker.
    Mahkeme kararıyla satışa çıkan araçlar — genelde %30-60 ucuz.
    """
    source = "adalet_gov"
    source_label = "İcra İhalesi"
    count = 0

    try:
        # Ana ilan sayfası
        urls_to_try = [
            "https://www.adalet.gov.tr/Duyurular/ilan",
            "https://ilan.adalet.gov.tr/",
            "https://www.adalet.gov.tr/ilan",
        ]

        html = None
        for url in urls_to_try:
            html = await fetch(session, url)
            if html:
                break

        if not html:
            await update_scraper_health(source, success=False)
            return 0

        soup = BeautifulSoup(html, "lxml")

        # İcra satış ilanları bölümünü bul
        listings = soup.select(
            ".ilan-item, .list-group-item, tr, .row, article, .card"
        )
        if not listings:
            listings = soup.find_all(
                ["div", "tr", "li"], string=re.compile(r"icra|satış|taşınır", re.I)
            )

        for item in listings:
            try:
                text = item.get_text(" ", strip=True)
                if not text_has_keywords(text, ARAC_KEYWORDS):
                    continue

                # Başlık — ilk satır veya link text
                link = item.select_one("a[href]")
                title = link.get_text(strip=True) if link else text[:120]
                ihale_url = ""
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"https://www.adalet.gov.tr{href}"
                    ihale_url = href

                # Muhammen bedel (açılış fiyatı)
                bedel_match = re.search(
                    r"muhammen\s+bedel[:\s]*([\d.,]+)\s*(TL|₺)?", text, re.I
                )
                fiyat = parse_price(bedel_match.group(1)) if bedel_match else None
                if not fiyat:
                    fiyat = parse_price(text)

                # İhale tarihi
                tarih_match = re.search(
                    r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text
                )
                tarih = tarih_match.group(1) if tarih_match else None

                # Şehir (genelde dosya numarasında veya metinde)
                sehir = None
                for city in ["İstanbul", "Ankara", "İzmir", "Bursa", "Antalya",
                             "Adana", "Konya", "Gaziantep", "Mersin", "Kayseri"]:
                    if city.lower() in text.lower():
                        sehir = city
                        break

                auction = build_auction_dict(
                    source=source,
                    source_label=source_label,
                    title=title,
                    sehir=sehir,
                    acilis_fiyati=fiyat,
                    ihale_tarihi=tarih,
                    ihale_url=ihale_url,
                    description=text,
                )

                if await save_auction(auction):
                    count += 1
            except Exception as e:
                log(f"adalet.gov.tr listing parse error: {e}")
                continue

        await update_scraper_health(source, success=True, found_count=count)
        log(f"adalet.gov.tr: {count} yeni ilan bulundu")
    except Exception as e:
        log(f"adalet.gov.tr scraper hatası: {e}", "error")
        await update_scraper_health(source, success=False)

    return count


# ══════════════════════════════════════════════════════════════════
# Kaynak 3: GİB (Gelir İdaresi Başkanlığı) Vergi İcra
# ══════════════════════════════════════════════════════════════════

async def scrape_gib(session: aiohttp.ClientSession) -> int:
    """
    GİB vergi icra ilanlarını çeker.
    Vergi borcu sebebiyle el konulan araçlar — %25-50 ucuz.
    """
    source = "gib"
    source_label = "Vergi İcra"
    count = 0

    try:
        urls_to_try = [
            "https://www.gib.gov.tr/ihale-ilanlari",
            "https://www.gib.gov.tr/duyurular",
        ]

        html = None
        for url in urls_to_try:
            html = await fetch(session, url)
            if html:
                break

        if not html:
            await update_scraper_health(source, success=False)
            return 0

        soup = BeautifulSoup(html, "lxml")

        listings = soup.select(
            ".view-content .views-row, .item-list li, .list-group-item, article, tr"
        )
        if not listings:
            listings = soup.find_all(["div", "li", "tr"])

        for item in listings:
            try:
                text = item.get_text(" ", strip=True)
                if not text_has_keywords(text, ARAC_KEYWORDS):
                    continue

                link = item.select_one("a[href]")
                title = link.get_text(strip=True) if link else text[:120]
                ihale_url = ""
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"https://www.gib.gov.tr{href}"
                    ihale_url = href

                fiyat = parse_price(text)
                tarih_match = re.search(r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text)
                tarih = tarih_match.group(1) if tarih_match else None

                sehir = None
                for city in ["İstanbul", "Ankara", "İzmir", "Bursa", "Antalya",
                             "Adana", "Konya", "Gaziantep", "Mersin", "Kayseri"]:
                    if city.lower() in text.lower():
                        sehir = city
                        break

                auction = build_auction_dict(
                    source=source,
                    source_label=source_label,
                    title=title,
                    sehir=sehir,
                    acilis_fiyati=fiyat,
                    ihale_tarihi=tarih,
                    ihale_url=ihale_url,
                    description=text,
                )

                if await save_auction(auction):
                    count += 1
            except Exception as e:
                log(f"gib listing parse error: {e}")
                continue

        await update_scraper_health(source, success=True, found_count=count)
        log(f"GİB: {count} yeni ilan bulundu")
    except Exception as e:
        log(f"GİB scraper hatası: {e}", "error")
        await update_scraper_health(source, success=False)

    return count


# ══════════════════════════════════════════════════════════════════
# Kaynak 4: Gümrük İdaresi
# ══════════════════════════════════════════════════════════════════

async def scrape_gumruk(session: aiohttp.ClientSession) -> int:
    """
    Ticaret Bakanlığı / Gümrük ihalelerini çeker.
    Sınırda el konulan araçlar — %40-70 ucuz olabilir.
    """
    source = "gumruk"
    source_label = "Gümrük İhalesi"
    count = 0

    try:
        urls_to_try = [
            "https://www.ticaret.gov.tr/ihaleler",
            "https://www.gtb.gov.tr/ihaleler",
            "https://www.ticaret.gov.tr/gumruk-islemleri/ihaleler",
        ]

        html = None
        for url in urls_to_try:
            html = await fetch(session, url)
            if html:
                break

        if not html:
            await update_scraper_health(source, success=False)
            return 0

        soup = BeautifulSoup(html, "lxml")
        listings = soup.select(
            ".list-group-item, article, .card, tr, .item, .row"
        )
        if not listings:
            listings = soup.find_all(["div", "li", "tr"])

        for item in listings:
            try:
                text = item.get_text(" ", strip=True)
                if not text_has_keywords(text, ARAC_KEYWORDS):
                    continue

                link = item.select_one("a[href]")
                title = link.get_text(strip=True) if link else text[:120]
                ihale_url = ""
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"https://www.ticaret.gov.tr{href}"
                    ihale_url = href

                fiyat = parse_price(text)
                tarih_match = re.search(r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text)
                tarih = tarih_match.group(1) if tarih_match else None

                auction = build_auction_dict(
                    source=source,
                    source_label=source_label,
                    title=title,
                    acilis_fiyati=fiyat,
                    ihale_tarihi=tarih,
                    ihale_url=ihale_url,
                    description=text,
                )

                if await save_auction(auction):
                    count += 1
            except Exception as e:
                log(f"gümrük listing parse error: {e}")
                continue

        await update_scraper_health(source, success=True, found_count=count)
        log(f"Gümrük: {count} yeni ilan bulundu")
    except Exception as e:
        log(f"Gümrük scraper hatası: {e}", "error")
        await update_scraper_health(source, success=False)

    return count


# ══════════════════════════════════════════════════════════════════
# Kaynak 5: Belediye İhaleleri
# ══════════════════════════════════════════════════════════════════

MUNICIPALITIES = [
    ("ibb", "https://ibb.istanbul/ilan", "İstanbul"),
    ("ankara_bel", "https://www.ankara.bel.tr/ihaleler", "Ankara"),
    ("izmir_bel", "https://www.izmir.bel.tr/tr/Ihaleler/", "İzmir"),
    ("bursa_bel", "https://www.bursa.bel.tr/ihaleler/", "Bursa"),
    ("antalya_bel", "https://www.antalya.bel.tr/ihaleler", "Antalya"),
    ("adana_bel", "https://www.adana.bel.tr/ihaleler", "Adana"),
    ("konya_bel", "https://www.konya.bel.tr/ihaleler", "Konya"),
    ("gaziantep_bel", "https://www.gaziantep.bel.tr/ihaleler", "Gaziantep"),
    ("mersin_bel", "https://www.mersin.bel.tr/ihaleler", "Mersin"),
    ("kayseri_bel", "https://www.kayseri.bel.tr/ihaleler", "Kayseri"),
]


async def scrape_municipality(
    session: aiohttp.ClientSession, muni_id: str, url: str, sehir: str
) -> int:
    """Tek bir belediye sitesinden araç ihalelerini çeker."""
    source = muni_id
    source_label = f"{sehir} Belediyesi İhalesi"
    count = 0

    try:
        html = await fetch(session, url)
        if not html:
            await update_scraper_health(source, success=False)
            return 0

        soup = BeautifulSoup(html, "lxml")

        # Genel seçiciler — belediye siteleri farklı yapılarda olabilir
        listings = soup.select(
            ".ihale-item, .ilan-item, .list-group-item, article, .card, "
            "table tbody tr, .row .col, .item"
        )
        if not listings:
            listings = soup.find_all(["div", "li", "tr", "article"])

        for item in listings:
            try:
                text = item.get_text(" ", strip=True)
                if len(text) < 20:
                    continue
                if not text_has_keywords(text, ARAC_KEYWORDS):
                    continue

                link = item.select_one("a[href]")
                title = link.get_text(strip=True) if link else text[:120]
                ihale_url = ""
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        # URL base'i tahmin et
                        from urllib.parse import urljoin
                        ihale_url = urljoin(url, href)
                    else:
                        ihale_url = href

                fiyat = parse_price(text)
                tarih_match = re.search(r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text)
                tarih = tarih_match.group(1) if tarih_match else None

                auction = build_auction_dict(
                    source=source,
                    source_label=source_label,
                    title=title,
                    sehir=sehir,
                    acilis_fiyati=fiyat,
                    ihale_tarihi=tarih,
                    ihale_url=ihale_url,
                    description=text,
                )

                if await save_auction(auction):
                    count += 1
            except Exception as e:
                log(f"{sehir} belediye listing parse error: {e}")
                continue

        await update_scraper_health(source, success=True, found_count=count)
        log(f"{sehir} Belediyesi: {count} yeni ilan bulundu")
    except Exception as e:
        log(f"{sehir} Belediyesi scraper hatası: {e}", "error")
        await update_scraper_health(source, success=False)

    return count


async def scrape_all_municipalities(session: aiohttp.ClientSession) -> int:
    """Tüm belediye sitelerini tarar."""
    total = 0
    for muni_id, url, sehir in MUNICIPALITIES:
        try:
            found = await scrape_municipality(session, muni_id, url, sehir)
            total += found
        except Exception as e:
            log(f"Belediye {sehir} scrape failed: {e}", "error")
    return total


# ══════════════════════════════════════════════════════════════════
# Piyasa Değeri Araştırma (Sahibinden)
# ══════════════════════════════════════════════════════════════════

async def get_market_value(
    session: aiohttp.ClientSession, marka: str, model: str, yil: int
) -> tuple[Optional[float], int]:
    """
    Sahibinden'den piyasa değeri çeker.
    Önce cache kontrol eder. Bulunamazsa (None, 0) döner.
    """
    if not marka or not yil:
        return None, 0

    cache_key = f"{marka}_{model or 'any'}_{yil}".lower().replace(" ", "_")
    cached = await get_cached_price(cache_key)
    if cached:
        return cached, 5  # Cache'den gelen yaklaşık örnek sayısı

    try:
        # Sahibinden'e ekstra dikkatli yaklaş
        await asyncio.sleep(5)

        marka_slug = marka.lower().replace(" ", "-").replace("ş", "s").replace("ç", "c")
        model_slug = (model or "").lower().replace(" ", "-") if model else ""
        url = f"https://www.sahibinden.com/otomobil?a20={marka_slug}"
        if model_slug:
            url += f"&a21={model_slug}"
        url += f"&a4={yil - 1}_{yil + 1}"

        html = await fetch(session, url, retries=2)
        if not html:
            return None, 0

        soup = BeautifulSoup(html, "lxml")
        prices = []

        # Fiyat elementlerini bul
        price_elements = soup.select(
            ".searchResultsPriceValue, .listing-price, td.searchResultsPriceValue"
        )
        for el in price_elements:
            p = parse_price(el.get_text())
            if p and p > 10000:  # Çok düşük fiyatları filtrele
                prices.append(p)

        if len(prices) < 3:
            return None, 0

        # Outlier'ları çıkar (%10 üst ve alt)
        prices.sort()
        trim = max(1, len(prices) // 10)
        trimmed = prices[trim:-trim] if len(prices) > 4 else prices

        # Medyan
        mid = len(trimmed) // 2
        if len(trimmed) % 2 == 0:
            median_price = (trimmed[mid - 1] + trimmed[mid]) / 2
        else:
            median_price = trimmed[mid]

        # Cache'le
        await cache_price(cache_key, median_price, len(prices))
        return median_price, len(prices)

    except Exception as e:
        log(f"Sahibinden piyasa değeri hatası ({marka} {model} {yil}): {e}")
        return None, 0


async def enrich_auction_prices(
    session: aiohttp.ClientSession, auctions: list[dict]
) -> list[dict]:
    """İhalelere piyasa değeri ve indirim yüzdesi ekler."""
    for auction in auctions:
        if auction.get("market_value"):
            continue
        if not auction.get("marka") or not auction.get("acilis_fiyati"):
            continue

        market_value, sample_count = await get_market_value(
            session,
            auction["marka"],
            auction.get("model"),
            auction.get("yil", 2020),
        )

        if market_value and market_value > 0:
            auction["market_value"] = market_value
            gap = market_value - auction["acilis_fiyati"]
            auction["gap_tl"] = gap
            auction["discount_pct"] = (gap / market_value) * 100

    return auctions


# ══════════════════════════════════════════════════════════════════
# Ana Scraper Fonksiyonu
# ══════════════════════════════════════════════════════════════════

async def run_all_scrapers() -> dict:
    """
    Tüm scraper'ları eş zamanlı çalıştırır.
    Bir kaynak çökerse diğerleri etkilenmez.
    """
    summary = {
        "total_new": 0,
        "by_source": {},
        "errors": [],
    }

    connector = aiohttp.TCPConnector(limit=5, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Her scraper'ı ayrı try/except ile çalıştır
        scrapers = [
            ("ilan_gov", scrape_ilan_gov(session)),
            ("adalet_gov", scrape_adalet_gov(session)),
            ("gib", scrape_gib(session)),
            ("gumruk", scrape_gumruk(session)),
            ("belediyeler", scrape_all_municipalities(session)),
        ]

        results = await asyncio.gather(
            *[s[1] for s in scrapers],
            return_exceptions=True,
        )

        for (name, _), result in zip(scrapers, results):
            if isinstance(result, Exception):
                summary["errors"].append(f"{name}: {result}")
                summary["by_source"][name] = 0
                log(f"Scraper {name} exception: {result}", "error")
            else:
                summary["by_source"][name] = result
                summary["total_new"] += result

        # Piyasa değeri zenginleştirme
        # Sadece yeni bulunan ihaleler için yapılacak (scheduler'da çağrılır)
        log(
            f"Scraper tamamlandı: {summary['total_new']} yeni ilan, "
            f"{len(summary['errors'])} hata"
        )

    return summary

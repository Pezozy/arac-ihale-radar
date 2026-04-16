"""
Araç İhale Radar — Yardımcı Fonksiyonlar
Loglama, format, parse işlemleri.
"""
import re
import logging
import sys
from datetime import datetime
from typing import Optional
import telegram

from config import settings

# ── Loglama ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("arac_ihale")


def log(msg: str, level: str = "info"):
    """Basit loglama wrapper'ı."""
    getattr(logger, level, logger.info)(msg)


# ── Telegram mesaj gönderme ──────────────────────────────────────

_bot_instance: Optional[telegram.Bot] = None


def set_bot_instance(bot: telegram.Bot):
    """Bot instance'ını ayarlar — main.py'den çağrılır."""
    global _bot_instance
    _bot_instance = bot


async def send_telegram_message(chat_id: int, text: str, **kwargs) -> bool:
    """Telegram mesajı gönderir. Hata durumunda False döner."""
    if not _bot_instance:
        log("Bot instance henüz ayarlanmadı", "error")
        return False
    try:
        await _bot_instance.send_message(
            chat_id=chat_id,
            text=text,
            disable_web_page_preview=True,
            **kwargs,
        )
        return True
    except Exception as e:
        log(f"Telegram mesaj gönderme hatası ({chat_id}): {e}", "error")
        return False


# ── Bilinen Markalar ─────────────────────────────────────────────

KNOWN_MARKALAR = [
    "Volkswagen", "Renault", "Ford", "Toyota", "Honda", "Hyundai", "Kia",
    "Fiat", "Peugeot", "Opel", "BMW", "Mercedes", "Audi", "Volvo", "Skoda",
    "Seat", "Citroen", "Nissan", "Mitsubishi", "Suzuki", "Dacia", "Mazda",
    "Subaru", "Jeep", "Land Rover", "Chevrolet", "Tofas", "Dogan", "Sahin",
    "Tofaş", "Doğan", "Şahin", "Alfa Romeo", "Mini", "Porsche", "Lexus",
    "Isuzu", "Iveco", "MAN", "Scania", "DAF", "Temsa",
]


def extract_marka_model(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    Metinden araç marka ve modelini çıkarır.
    Bilinen marka listesini tarar, sonraki kelime(ler)i model olarak alır.
    """
    if not text:
        return None, None
    text_lower = text.lower()
    for marka in KNOWN_MARKALAR:
        marka_lower = marka.lower()
        idx = text_lower.find(marka_lower)
        if idx == -1:
            continue
        # Markadan sonraki kısmı al
        after = text[idx + len(marka):].strip()
        # İlk 1-2 kelimeyi model olarak al
        words = after.split()
        model = " ".join(words[:2]).strip(".,;:-/()") if words else None
        return marka, model
    return None, None


# ── Fiyat parse ──────────────────────────────────────────────────

def parse_price(text: str) -> Optional[float]:
    """
    Türkçe fiyat metninden sayı çıkarır.
    Desteklenen formatlar: ₺385.000, 385,000 TL, 385.000,00 TL
    """
    if not text:
        return None
    # Sadece sayısal karakterleri ve noktalama ayıklama
    text = text.replace("₺", "").replace("TL", "").replace("tl", "").strip()
    # 385.000,00 formatı → 385000.00
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    # 385.000 formatı → 385000
    elif "." in text:
        parts = text.split(".")
        if len(parts[-1]) == 3:
            text = text.replace(".", "")
        # else: ondalık nokta olarak bırak
    # 385,000 formatı
    elif "," in text:
        text = text.replace(",", "")

    try:
        return float(re.sub(r"[^\d.]", "", text))
    except (ValueError, TypeError):
        return None


def parse_km(text: str) -> Optional[int]:
    """
    Metinden kilometre değerini çıkarır.
    Desteklenen: 85.000 km, 85000km, 85 bin km
    """
    if not text:
        return None
    text = text.lower()
    # "85 bin km" formatı
    m = re.search(r"(\d+)\s*bin\s*km", text)
    if m:
        return int(m.group(1)) * 1000
    # "85.000 km" veya "85000 km" formatı
    m = re.search(r"([\d.]+)\s*km", text)
    if m:
        val = m.group(1).replace(".", "")
        try:
            return int(val)
        except ValueError:
            return None
    return None


def parse_year(text: str) -> Optional[int]:
    """Metinden 1990-2026 arası yıl çıkarır."""
    if not text:
        return None
    years = re.findall(r"\b(19[9]\d|20[0-2]\d)\b", text)
    return int(years[0]) if years else None


# ── Format yardımcıları ──────────────────────────────────────────

def format_price(amount: Optional[float]) -> str:
    """Fiyatı Türk formatında gösterir: ₺385.000"""
    if amount is None:
        return "Belirtilmemiş"
    return f"₺{amount:,.0f}".replace(",", ".")


def format_km(km: Optional[int]) -> str:
    """KM'yi formatlar: 85.000 km"""
    if km is None:
        return "Belirtilmemiş"
    return f"{km:,} km".replace(",", ".")


def format_date_tr(iso_date: Optional[str]) -> str:
    """ISO tarihini Türkçe formatlar: 15 Mart 2025"""
    if not iso_date:
        return "Belirtilmemiş"
    months_tr = {
        1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan",
        5: "Mayıs", 6: "Haziran", 7: "Temmuz", 8: "Ağustos",
        9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
    }
    try:
        dt = datetime.fromisoformat(iso_date[:10])
        return f"{dt.day} {months_tr[dt.month]} {dt.year}"
    except Exception:
        return iso_date[:10]


# ── Türkiye şehir listesi ───────────────────────────────────────

SEHIRLER = [
    "Istanbul", "Ankara", "Izmir", "Bursa", "Antalya", "Adana",
    "Konya", "Gaziantep", "Mersin", "Kayseri", "Trabzon", "Samsun",
    "Eskisehir", "Denizli", "Diyarbakir", "Sanliurfa", "Malatya",
    "Erzurum", "Sakarya", "Manisa", "Hatay", "Balikesir", "Mugla",
    "Tekirdag", "Kahramanmaras", "Van", "Aydin", "Elazig",
]

SEHIRLER_TR = [
    "İstanbul", "Ankara", "İzmir", "Bursa", "Antalya", "Adana",
    "Konya", "Gaziantep", "Mersin", "Kayseri", "Trabzon", "Samsun",
    "Eskişehir", "Denizli", "Diyarbakır", "Şanlıurfa", "Malatya",
    "Erzurum", "Sakarya", "Manisa", "Hatay", "Balıkesir", "Muğla",
    "Tekirdağ", "Kahramanmaraş", "Van", "Aydın", "Elazığ",
]


def normalize_sehir(sehir: str) -> Optional[str]:
    """Şehir adını normalize eder (İstanbul, Ankara vs.)."""
    if not sehir:
        return None
    sehir_lower = sehir.lower().strip()
    for tr in SEHIRLER_TR:
        if tr.lower() == sehir_lower or tr.lower() in sehir_lower:
            return tr
    for en in SEHIRLER:
        if en.lower() == sehir_lower or en.lower() in sehir_lower:
            idx = SEHIRLER.index(en)
            return SEHIRLER_TR[idx]
    return sehir.strip().title()

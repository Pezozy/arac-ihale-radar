"""
Araç İhale Radar — Konfigürasyon
Tüm ayarlar .env dosyasından okunur.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _safe_int(key: str, default: int = 0) -> int:
    val = os.getenv(key, str(default))
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


class Settings:
    """Uygulama ayarları — .env dosyasından yüklenir."""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_ADMIN_ID: int = _safe_int("TELEGRAM_ADMIN_ID", 0)
    PUBLIC_CHANNEL_ID: str = os.getenv("PUBLIC_CHANNEL_ID", "")

    # AI
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

    # Stripe
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PAYMENT_LINK_BASIC: str = os.getenv("STRIPE_PAYMENT_LINK_BASIC", "")
    STRIPE_PAYMENT_LINK_PRO: str = os.getenv("STRIPE_PAYMENT_LINK_PRO", "")
    STRIPE_CUSTOMER_PORTAL_URL: str = os.getenv("STRIPE_CUSTOMER_PORTAL_URL", "")

    # Uygulama ayarları
    TRIAL_DAYS: int = int(os.getenv("TRIAL_DAYS", "7"))
    PRICE_BASIC_TL: int = int(os.getenv("PRICE_BASIC_TL", "149"))
    PRICE_PRO_TL: int = int(os.getenv("PRICE_PRO_TL", "299"))
    MIN_DISCOUNT_PCT: int = int(os.getenv("MIN_DISCOUNT_PCT", "15"))
    MAX_AUCTIONS_PER_BROADCAST: int = int(os.getenv("MAX_AUCTIONS_PER_BROADCAST", "5"))
    SCRAPE_DELAY_MIN: float = float(os.getenv("SCRAPE_DELAY_MIN", "1.5"))
    SCRAPE_DELAY_MAX: float = float(os.getenv("SCRAPE_DELAY_MAX", "4.0"))
    TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Istanbul")
    PORT: int = int(os.getenv("PORT", "8080"))
    DB_PATH: str = os.getenv("DB_PATH", "./arac_ihale.db")

    # Bot adı (BotFather'dan alınan)
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "AracIhaleRadarBot")
    SUPPORT_USERNAME: str = os.getenv("SUPPORT_USERNAME", "AracIhaleDestek")


settings = Settings()

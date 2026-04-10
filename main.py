"""
Araç İhale Radar — Ana Giriş Noktası
Bot, scheduler ve Stripe webhook sunucusunu başlatır.
"""
import asyncio
import signal
import sys
import threading
import traceback

import uvicorn
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from bot import start, ayarlar, abone, durum, ornek, davet, destek, yardim, istatistik, button_handler
from config import settings
from database import init_db
from payments import payments_app
from scheduler import setup_scheduler
from utils import log, set_bot_instance, send_telegram_message


def run_webhook_server():
    """Stripe webhook sunucusunu ayrı thread'de çalıştırır."""
    uvicorn.run(
        payments_app,
        host="0.0.0.0",
        port=settings.PORT,
        log_level="error",
    )


async def main():
    """Ana fonksiyon — her şeyi başlatır."""
    # 1. Veritabanını başlat
    await init_db()
    log("✅ Veritabanı hazır")

    # 2. Telegram uygulamasını oluştur
    app = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .build()
    )

    # 3. Komut handler'larını kaydet
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayarlar", ayarlar))
    app.add_handler(CommandHandler("abone", abone))
    app.add_handler(CommandHandler("durum", durum))
    app.add_handler(CommandHandler("ornek", ornek))
    app.add_handler(CommandHandler("davet", davet))
    app.add_handler(CommandHandler("destek", destek))
    app.add_handler(CommandHandler("yardim", yardim))
    app.add_handler(CommandHandler("istatistik", istatistik))
    app.add_handler(CallbackQueryHandler(button_handler))

    # 4. Stripe webhook sunucusunu arka planda başlat
    webhook_thread = threading.Thread(target=run_webhook_server, daemon=True)
    webhook_thread.start()
    log(f"✅ Stripe webhook sunucusu port {settings.PORT}'da çalışıyor")

    # 5. Scheduler'ı başlat
    scheduler = setup_scheduler()
    scheduler.start()
    log("✅ Zamanlanmış görevler aktif")

    for job in scheduler.get_jobs():
        log(f"  📅 {job.name}: {job.trigger}")

    # 6. Bot'u başlat (low-level API — event loop çakışması önlenir)
    await app.initialize()
    set_bot_instance(app.bot)

    # Admin'e başlangıç bildirimi
    if settings.TELEGRAM_ADMIN_ID:
        await send_telegram_message(
            settings.TELEGRAM_ADMIN_ID,
            "🚀 Araç İhale Radar başlatıldı!\n\n"
            "✅ Scraper: Aktif\n"
            "✅ Scheduler: Aktif\n"
            "✅ Stripe webhook: Aktif\n\n"
            "📅 Sabah bülteni: 08:00\n"
            "📅 Akşam bülteni: 18:00\n\n"
            "/istatistik ile detayları görebilirsiniz.",
        )

    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log("🚀 Telegram botu başlatıldı — tüm sistemler aktif")

    # Sonsuz bekle — sinyal gelene kadar
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    # Temiz kapanış
    log("Bot kapatılıyor...")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    scheduler.shutdown(wait=False)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log(f"Kritik başlatma hatası: {e}", "error")
        traceback.print_exc()
        sys.exit(1)

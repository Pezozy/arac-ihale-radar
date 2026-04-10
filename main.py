"""
Araç İhale Radar — Ana Giriş Noktası
Bot, scheduler ve Stripe webhook sunucusunu başlatır.
"""
import asyncio
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


async def post_init(app: Application):
    """Bot başladıktan sonra çalışır — admin'e bildirim gönderir."""
    set_bot_instance(app.bot)

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
    log("🚀 Bot başlatıldı — tüm sistemler aktif")


async def main():
    """Ana fonksiyon — her şeyi başlatır."""
    # Global exception handler
    def handle_exception(loop, context):
        msg = context.get("exception", context["message"])
        log(f"Yakalanmamış hata: {msg}", "error")
        # Admin'e bildir (sync olmadan)
        asyncio.create_task(
            send_telegram_message(
                settings.TELEGRAM_ADMIN_ID,
                f"⚠️ Kritik hata:\n{msg}",
            )
        )

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_exception)

    # 1. Veritabanını başlat
    await init_db()
    log("✅ Veritabanı hazır")

    # 2. Telegram uygulamasını oluştur
    app = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
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

    # Zamanlanmış görevleri listele
    jobs = scheduler.get_jobs()
    for job in jobs:
        log(f"  📅 {job.name}: {job.trigger}")

    # 6. Bot'u başlat (sonsuz döngü)
    log("🚀 Telegram botu başlatılıyor...")
    await app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Bot kapatılıyor...")
        sys.exit(0)
    except Exception as e:
        log(f"Kritik başlatma hatası: {e}", "error")
        traceback.print_exc()
        sys.exit(1)

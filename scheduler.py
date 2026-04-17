"""
Araç İhale Radar — Zamanlanmış Görevler
Tüm otomasyon: scrape, broadcast, trial/sub kontrolü, raporlama.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta

import aiohttp
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from database import (
    get_users_for_broadcast, get_auctions_for_user, mark_sent, already_sent,
    log_broadcast, get_weekly_stats, get_best_auction, update_user,
    get_expired_trial_users, get_expired_subscribers, get_expiring_subscribers,
    get_scraper_alerts, get_total_sent_count, get_active_users,
)
from scraper import run_all_scrapers, enrich_auction_prices
from analyzer import process_auction_summaries, generate_template_summary
from utils import log, send_telegram_message, format_price

ISTANBUL = pytz.timezone(settings.TIMEZONE)


# ══════════════════════════════════════════════════════════════════
# Sabah Bülteni — 08:00
# ══════════════════════════════════════════════════════════════════

async def morning_broadcast():
    """Sabah 08:00'de çalışır — scrape + kullanıcılara ilan gönderir."""
    log("🌅 Sabah bülteni başlıyor...")
    started_at = datetime.now().isoformat()
    stats = {"started_at": started_at, "errors": []}

    try:
        # 1. Scrape
        scrape_result = await run_all_scrapers()
        stats["auctions_found"] = scrape_result["total_new"]
        log(
            f"🔍 Scrape tamamlandı: {scrape_result['total_new']} yeni ilan "
            f"(hatalar: {scrape_result.get('errors', [])})"
        )
        # Sıfır sonuç normal olabilir (tüm ilanlar zaten DB'de).
        # Kalıcı sorunlar için health_check (her 6 saatte) admin'e bildirir.

        # 2. Piyasa değeri zenginleştirme
        connector = aiohttp.TCPConnector(limit=3, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            from database import get_db
            db = await get_db()
            try:
                cursor = await db.execute(
                    "SELECT * FROM auctions WHERE is_active = 1 AND market_value IS NULL LIMIT 20"
                )
                rows = await cursor.fetchall()
                new_auctions = [dict(r) for r in rows]
            finally:
                await db.close()

            if new_auctions:
                new_auctions = await enrich_auction_prices(session, new_auctions)
                # Veritabanını güncelle
                for a in new_auctions:
                    if a.get("market_value"):
                        from database import get_db as gdb
                        db2 = await gdb()
                        try:
                            await db2.execute(
                                """UPDATE auctions SET market_value=?, discount_pct=?, gap_tl=?
                                   WHERE id=?""",
                                (a["market_value"], a.get("discount_pct"), a.get("gap_tl"), a["id"]),
                            )
                            await db2.commit()
                        finally:
                            await db2.close()

        # 3. AI özetleri
        from database import get_db as gdb2
        db3 = await gdb2()
        try:
            cursor = await db3.execute(
                "SELECT * FROM auctions WHERE is_active = 1 AND ai_summary IS NULL LIMIT 20"
            )
            rows = await cursor.fetchall()
            unprocessed = [dict(r) for r in rows]
        finally:
            await db3.close()

        if unprocessed:
            processed = await process_auction_summaries(unprocessed)
            for a in processed:
                if a.get("ai_summary"):
                    db4 = await gdb2()
                    try:
                        await db4.execute(
                            "UPDATE auctions SET ai_summary=?, used_ai=? WHERE id=?",
                            (a["ai_summary"], a.get("used_ai", 0), a["id"]),
                        )
                        await db4.commit()
                    finally:
                        await db4.close()

        # 4. Kullanıcılara gönder
        users = await get_users_for_broadcast()
        alerts_sent = 0
        users_reached = 0

        for user in users:
            try:
                auctions = await get_auctions_for_user(
                    user, limit=settings.MAX_AUCTIONS_PER_BROADCAST
                )

                # Zaten gönderilenleri filtrele
                unsent = []
                for a in auctions:
                    if not await already_sent(user["telegram_id"], a["id"]):
                        unsent.append(a)

                if not unsent:
                    continue  # Gönderilecek ilan yoksa sessizce atla

                # Header
                today = datetime.now(ISTANBUL).strftime("%d.%m.%Y")
                await send_telegram_message(
                    user["telegram_id"],
                    f"🌅 Araç İhale Radar — Sabah Bülteni\n"
                    f"📅 {today}\n"
                    f"📬 {len(unsent)} yeni fırsat:",
                )

                # Her ilanı ayrı mesaj olarak gönder
                for auction in unsent:
                    summary = auction.get("ai_summary") or generate_template_summary(auction)
                    success = await send_telegram_message(user["telegram_id"], summary)
                    if success:
                        await mark_sent(user["telegram_id"], auction["id"])
                        alerts_sent += 1
                    await asyncio.sleep(0.05)  # Rate limit

                users_reached += 1
            except Exception as e:
                stats["errors"].append(f"User {user['telegram_id']}: {e}")
                log(f"Broadcast error for user {user['telegram_id']}: {e}", "error")

        # 5. Public kanala en iyi fırsatı gönder
        if settings.PUBLIC_CHANNEL_ID:
            best = await get_best_auction()
            if best:
                # AI özeti yoksa şablon kullan (kullanıcı bültenindeki mantıkla aynı)
                summary = best.get("ai_summary") or generate_template_summary(best)
                ok = await send_telegram_message(
                    settings.PUBLIC_CHANNEL_ID,
                    f"🏆 Günün Fırsatı\n\n{summary}\n\n"
                    f"🔔 Tüm fırsatlar için: @{settings.BOT_USERNAME}",
                )
                if ok:
                    log(
                        f"📢 Kanal güncellendi: {best.get('marka','?')} "
                        f"{best.get('model','')} — "
                        f"{'AI özeti' if best.get('ai_summary') else 'şablon özeti'}"
                    )
                else:
                    log("📢 Kanal güncelleme başarısız (Telegram API hatası)", "error")
            else:
                log("📢 Kanal: son 48 saatte uygun ilan bulunamadı — kanal atlandı")

        stats["alerts_sent"] = alerts_sent
        stats["users_reached"] = users_reached
        stats["finished_at"] = datetime.now().isoformat()
        await log_broadcast("morning", stats)

        log(
            f"🌅 Sabah bülteni tamamlandı: {alerts_sent} mesaj, "
            f"{users_reached} kullanıcı, {stats['auctions_found']} yeni ilan"
        )
    except Exception as e:
        log(f"Sabah bülteni kritik hata: {e}", "error")
        stats["errors"].append(str(e))
        stats["finished_at"] = datetime.now().isoformat()
        await log_broadcast("morning", stats)
        # Admin'e bildir
        await send_telegram_message(
            settings.TELEGRAM_ADMIN_ID,
            f"⚠️ Sabah bülteni hatası:\n{e}",
        )


# ══════════════════════════════════════════════════════════════════
# Akşam Bülteni — 18:00
# ══════════════════════════════════════════════════════════════════

async def evening_broadcast():
    """Akşam 18:00'de çalışır — yeni ilanlar + gönderilmemiş sabah ilanları."""
    log("🌆 Akşam bülteni başlıyor...")
    started_at = datetime.now().isoformat()
    stats = {"started_at": started_at, "errors": []}

    try:
        # Yeni scrape
        scrape_result = await run_all_scrapers()
        stats["auctions_found"] = scrape_result["total_new"]

        # Kullanıcılara gönder (aynı mantık)
        users = await get_users_for_broadcast()
        alerts_sent = 0
        users_reached = 0

        for user in users:
            try:
                auctions = await get_auctions_for_user(
                    user, limit=settings.MAX_AUCTIONS_PER_BROADCAST
                )
                unsent = [
                    a for a in auctions
                    if not await already_sent(user["telegram_id"], a["id"])
                ]

                if not unsent:
                    continue  # Akşam boş gönderim yapma

                today = datetime.now(ISTANBUL).strftime("%d.%m.%Y")
                await send_telegram_message(
                    user["telegram_id"],
                    f"🌆 Araç İhale Radar — Akşam Bülteni\n"
                    f"📅 {today}\n"
                    f"📬 {len(unsent)} yeni fırsat:",
                )

                for auction in unsent:
                    summary = auction.get("ai_summary") or generate_template_summary(auction)
                    success = await send_telegram_message(user["telegram_id"], summary)
                    if success:
                        await mark_sent(user["telegram_id"], auction["id"])
                        alerts_sent += 1
                    await asyncio.sleep(0.05)

                users_reached += 1
            except Exception as e:
                stats["errors"].append(f"User {user['telegram_id']}: {e}")
                log(f"Akşam broadcast error: {e}", "error")

        stats["alerts_sent"] = alerts_sent
        stats["users_reached"] = users_reached
        stats["finished_at"] = datetime.now().isoformat()
        await log_broadcast("evening", stats)

        log(f"🌆 Akşam bülteni tamamlandı: {alerts_sent} mesaj, {users_reached} kullanıcı")
    except Exception as e:
        log(f"Akşam bülteni kritik hata: {e}", "error")
        stats["finished_at"] = datetime.now().isoformat()
        await log_broadcast("evening", stats)


# ══════════════════════════════════════════════════════════════════
# Trial Süre Kontrolü — 09:00
# ══════════════════════════════════════════════════════════════════

async def check_trial_expiry():
    """Süresi dolan trial kullanıcıları expire eder."""
    log("⏰ Trial süre kontrolü...")
    try:
        expired_users = await get_expired_trial_users()
        for user in expired_users:
            await update_user(user["telegram_id"], subscription="expired")
            total = await get_total_sent_count(user["telegram_id"])
            await send_telegram_message(
                user["telegram_id"],
                "🕐 Ücretsiz deneme süreniz sona erdi!\n\n"
                f"7 günde {total} fırsat gönderdik.\n"
                "Bu fırsatları kaçırmaya devam etmeyin 👇\n\n"
                "/abone — Abonelik planları",
            )
        if expired_users:
            log(f"⏰ {len(expired_users)} trial süresi doldu")
    except Exception as e:
        log(f"Trial kontrol hatası: {e}", "error")


# ══════════════════════════════════════════════════════════════════
# Abonelik Süre Kontrolü — 09:05
# ══════════════════════════════════════════════════════════════════

async def check_subscription_expiry():
    """Abonelik süreleri: 3 gün uyarı + expire."""
    log("💳 Abonelik süre kontrolü...")
    try:
        # 3 gün kala uyarı
        expiring = await get_expiring_subscribers(days_ahead=3)
        for user in expiring:
            await send_telegram_message(
                user["telegram_id"],
                "⚠️ Aboneliğiniz 3 gün sonra bitiyor!\n"
                "Devam etmek için herhangi bir işlem yapmanıza gerek yok — "
                "Stripe otomatik yenileme açıksa kendiliğinden uzayacak.\n"
                f"İptal etmek isterseniz: {settings.STRIPE_CUSTOMER_PORTAL_URL}",
            )

        # Süresi dolanları expire et
        expired = await get_expired_subscribers()
        for user in expired:
            await update_user(user["telegram_id"], subscription="expired")
            await send_telegram_message(
                user["telegram_id"],
                "⏰ Aboneliğiniz sona erdi. Yenilemek için /abone",
            )
        if expired:
            log(f"💳 {len(expired)} abonelik süresi doldu")
    except Exception as e:
        log(f"Abonelik kontrol hatası: {e}", "error")


# ══════════════════════════════════════════════════════════════════
# Referral Ödülleri — 10:00
# ══════════════════════════════════════════════════════════════════

async def check_referral_rewards():
    """Referral sayısı 3'ün katı olan kullanıcılara ücretsiz ay ver."""
    log("🎁 Referral ödül kontrolü...")
    try:
        users = await get_active_users()
        for user in users:
            count = user.get("referral_count", 0)
            earned = user.get("free_months_earned", 0)
            deserved = count // 3
            if deserved > earned:
                new_months = deserved - earned
                # Abonelik süresini uzat
                current_expiry = user.get("sub_expiry")
                if current_expiry:
                    try:
                        exp_dt = datetime.fromisoformat(current_expiry)
                    except ValueError:
                        exp_dt = datetime.now()
                else:
                    exp_dt = datetime.now()
                new_expiry = (exp_dt + timedelta(days=31 * new_months)).isoformat()
                await update_user(
                    user["telegram_id"],
                    free_months_earned=deserved,
                    sub_expiry=new_expiry,
                )
                await send_telegram_message(
                    user["telegram_id"],
                    f"🎉 {new_months * 3} davet tamamlandı! "
                    f"{new_months} ay ücretsiz abonelik hesabınıza eklendi.",
                )
    except Exception as e:
        log(f"Referral kontrol hatası: {e}", "error")


# ══════════════════════════════════════════════════════════════════
# Haftalık Admin Rapor — Pazartesi 08:30
# ══════════════════════════════════════════════════════════════════

async def weekly_admin_report():
    """Haftalık istatistik raporu admin'e gönderir."""
    log("📊 Haftalık rapor hazırlanıyor...")
    try:
        stats = await get_weekly_stats()
        best = stats.get("best_auction")
        best_text = "Yok"
        if best:
            best_text = (
                f"{best.get('marka', '?')} {best.get('model', '')} {best.get('yil', '')}\n"
                f"  Fiyat: {format_price(best.get('acilis_fiyati'))}\n"
                f"  İndirim: %{best.get('discount_pct', 0):.0f}"
            )

        week_end = datetime.now(ISTANBUL).strftime("%d.%m.%Y")
        week_start = (datetime.now(ISTANBUL) - timedelta(days=7)).strftime("%d.%m.%Y")

        text = (
            f"📊 Haftalık Rapor — Araç İhale Radar\n"
            f"📅 {week_start} – {week_end}\n\n"
            f"👥 Kullanıcılar:\n"
            f"• Yeni bu hafta: +{stats.get('new_users', 0)}\n"
            f"• Toplam aktif abone: {stats.get('total_active', 0)}\n\n"
            f"📨 Bu hafta:\n"
            f"• Gönderilen mesaj: {stats.get('messages_sent', 0):,}\n"
            f"• Bulunan araç ilanı: {stats.get('auctions_found', 0):,}\n"
            f"• Ortalama indirim: %{stats.get('avg_discount', 0):.0f}\n\n"
            f"🏆 Haftanın en iyi fırsatı:\n{best_text}"
        )
        await send_telegram_message(settings.TELEGRAM_ADMIN_ID, text)
        log("📊 Haftalık rapor gönderildi")
    except Exception as e:
        log(f"Haftalık rapor hatası: {e}", "error")


# ══════════════════════════════════════════════════════════════════
# Sağlık Kontrolü — Her 6 saatte
# ══════════════════════════════════════════════════════════════════

async def health_check():
    """Scraper sağlık durumunu kontrol eder, sorun varsa admin'e bildirir."""
    try:
        alerts = await get_scraper_alerts()
        for alert in alerts:
            last_success = alert.get("last_success", "Hiç")
            await send_telegram_message(
                settings.TELEGRAM_ADMIN_ID,
                f"⚠️ Scraper Hatası: {alert['source']} kaynağı "
                f"{alert['consecutive_fails']} ardışık hata yapıyor.\n"
                f"Son başarılı: {last_success}\n"
                f"Kontrol edin.",
            )
    except Exception as e:
        log(f"Health check hatası: {e}", "error")


# ══════════════════════════════════════════════════════════════════
# Haftalık SEO Blog Yazısı — Çarşamba 10:00
# ══════════════════════════════════════════════════════════════════

async def weekly_seo_post():
    """Haftalık blog yazısı üretir (Markdown olarak kaydeder)."""
    log("📝 Haftalık SEO blog yazısı hazırlanıyor...")
    try:
        from analyzer import groq_client
        from database import get_db

        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT * FROM auctions
                   WHERE scraped_at >= ? AND discount_pct IS NOT NULL
                   ORDER BY discount_pct DESC LIMIT 3""",
                ((datetime.now() - timedelta(days=7)).isoformat(),),
            )
            top_auctions = [dict(r) for r in await cursor.fetchall()]
        finally:
            await db.close()

        if not top_auctions:
            log("SEO yazısı: Bu hafta yeterli ilan yok")
            return

        # Özet metni oluştur
        auctions_text = ""
        for i, a in enumerate(top_auctions, 1):
            auctions_text += (
                f"{i}. {a.get('marka', '?')} {a.get('model', '')} {a.get('yil', '')} — "
                f"{a.get('sehir', '?')} — {format_price(a.get('acilis_fiyati'))} "
                f"(Piyasa: {format_price(a.get('market_value'))}, "
                f"%{a.get('discount_pct', 0):.0f} indirim)\n"
            )

        # Blog yazısını kaydet
        date_str = datetime.now(ISTANBUL).strftime("%Y-%m-%d")
        blog_dir = "./output/blog"
        os.makedirs(blog_dir, exist_ok=True)

        content = (
            f"# Bu Haftanın En İyi Araç İhale Fırsatları — {date_str}\n\n"
            f"Bu hafta Türkiye genelinde resmi ihale sitelerinden derlediğimiz "
            f"en avantajlı araç fırsatları:\n\n"
            f"{auctions_text}\n"
            f"Bu ilanlar kamuya açık resmi ihale sitelerinden derlenmiştir.\n\n"
            f"Günlük fırsat bildirimleri almak için: @{settings.BOT_USERNAME}\n"
        )

        filepath = f"{blog_dir}/{date_str}.md"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        log(f"📝 SEO blog yazısı oluşturuldu: {filepath}")
    except Exception as e:
        log(f"SEO blog yazısı hatası: {e}", "error")


# ══════════════════════════════════════════════════════════════════
# Scheduler Kurulumu
# ══════════════════════════════════════════════════════════════════

def setup_scheduler() -> AsyncIOScheduler:
    """Tüm zamanlanmış görevleri yapılandırır ve scheduler döndürür."""
    scheduler = AsyncIOScheduler(timezone=ISTANBUL)

    # Sabah bülteni — 08:00
    scheduler.add_job(
        morning_broadcast,
        CronTrigger(hour=8, minute=0, timezone=ISTANBUL),
        id="morning_broadcast",
        name="Sabah Bülteni",
    )

    # Akşam bülteni — 18:00
    scheduler.add_job(
        evening_broadcast,
        CronTrigger(hour=18, minute=0, timezone=ISTANBUL),
        id="evening_broadcast",
        name="Akşam Bülteni",
    )

    # Trial süre kontrolü — 09:00
    scheduler.add_job(
        check_trial_expiry,
        CronTrigger(hour=9, minute=0, timezone=ISTANBUL),
        id="trial_check",
        name="Trial Kontrol",
    )

    # Abonelik süre kontrolü — 09:05
    scheduler.add_job(
        check_subscription_expiry,
        CronTrigger(hour=9, minute=5, timezone=ISTANBUL),
        id="sub_check",
        name="Abonelik Kontrol",
    )

    # Referral ödülleri — 10:00
    scheduler.add_job(
        check_referral_rewards,
        CronTrigger(hour=10, minute=0, timezone=ISTANBUL),
        id="referral_check",
        name="Referral Kontrol",
    )

    # Haftalık admin rapor — Pazartesi 08:30
    scheduler.add_job(
        weekly_admin_report,
        CronTrigger(day_of_week="mon", hour=8, minute=30, timezone=ISTANBUL),
        id="weekly_report",
        name="Haftalık Rapor",
    )

    # Sağlık kontrolü — Her 6 saatte
    scheduler.add_job(
        health_check,
        CronTrigger(hour="*/6", timezone=ISTANBUL),
        id="health_check",
        name="Sağlık Kontrol",
    )

    # Haftalık SEO blog — Çarşamba 10:00
    scheduler.add_job(
        weekly_seo_post,
        CronTrigger(day_of_week="wed", hour=10, minute=0, timezone=ISTANBUL),
        id="seo_post",
        name="SEO Blog",
    )

    return scheduler

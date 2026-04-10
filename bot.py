"""
Araç İhale Radar — Telegram Bot Komutları
python-telegram-bot v20+ async handler'ları.
"""
import json
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import settings
from database import (
    get_user, create_user, update_user, get_best_auction,
    get_today_sent_count, get_total_sent_count, get_dashboard_stats,
)
from utils import log


# ── /start ───────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yeni kullanıcı karşılama ve kayıt."""
    user = update.effective_user
    telegram_id = user.id
    existing = await get_user(telegram_id)

    if existing:
        await update.message.reply_text(
            f"Tekrar hoş geldiniz {user.first_name}! "
            f"/durum ile hesabınızı görebilirsiniz."
        )
        await update_user(telegram_id, last_active=datetime.now().isoformat())
        return

    # Referral kontrolü
    referrer_id = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0].replace("ref_", ""))
        except ValueError:
            pass

    await create_user(
        telegram_id=telegram_id,
        username=user.username,
        first_name=user.first_name,
        referrer_id=referrer_id,
    )

    welcome = (
        f"👋 Merhaba {user.first_name}!\n\n"
        f"Araç İhale Radar'a hoş geldiniz 🚗\n\n"
        f"Her gün sabah 08:00 ve akşam 18:00'de Türkiye'nin en iyi araç ihale "
        f"fırsatlarını otomatik olarak size gönderiyorum.\n\n"
        f"🏛 Takip ettiğim kaynaklar:\n"
        f"• İcra ihaleleri (mahkemeler)\n"
        f"• Vergi icra satışları\n"
        f"• Gümrük ihaleleri\n"
        f"• Belediye araç ihaleleri\n\n"
        f"📊 Her araç için Sahibinden piyasa fiyatıyla karşılaştırma yapıyorum.\n"
        f"Sadece gerçek fırsatlar, gereksiz bilgi yok.\n\n"
        f"🎁 7 günlük ücretsiz denemeniz başladı!\n"
        f"Deneme süresinde tüm özelliklere erişebilirsiniz.\n\n"
        f"Kullanılabilir komutlar:\n"
        f"/ayarlar — Şehir, km ve diğer tercihler\n"
        f"/abone — Abonelik planları ve ödeme\n"
        f"/durum — Hesap durumunuz\n"
        f"/ornek — Örnek bir ilan görün\n"
        f"/davet — Arkadaşlarınızı davet edin (1 ay ücretsiz kazanın)\n"
        f"/yardim — Yardım"
    )
    await update.message.reply_text(welcome)
    log(f"Yeni kullanıcı: {user.first_name} (@{user.username}) [{telegram_id}]")


# ── /ayarlar ─────────────────────────────────────────────────────

async def ayarlar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kullanıcı tercihlerini ayarlama menüsü."""
    if not await _check_active(update):
        return

    keyboard = [
        [
            InlineKeyboardButton("🏙️ Şehir Filtresi", callback_data="pref_city"),
            InlineKeyboardButton("🚗 Araç Tipi", callback_data="pref_cartype"),
        ],
        [
            InlineKeyboardButton("📏 Maksimum KM", callback_data="pref_km"),
            InlineKeyboardButton("📉 Min İndirim %", callback_data="pref_discount"),
        ],
    ]
    await update.message.reply_text(
        "⚙️ Tercihlerinizi ayarlayın:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── /abone ───────────────────────────────────────────────────────

async def abone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Abonelik planları ve ödeme linkleri."""
    telegram_id = update.effective_user.id

    basic_link = f"{settings.STRIPE_PAYMENT_LINK_BASIC}?client_reference_id={telegram_id}"
    pro_link = f"{settings.STRIPE_PAYMENT_LINK_PRO}?client_reference_id={telegram_id}"

    text = (
        "💳 Abonelik Planları\n\n"
        f"🥉 Temel Plan — ₺{settings.PRICE_BASIC_TL}/ay\n"
        "✅ Günde 2 gönderim (sabah + akşam)\n"
        "✅ Şehir ve KM filtresi\n"
        "✅ İndirim % filtresi\n\n"
        f"🥇 Pro Plan — ₺{settings.PRICE_PRO_TL}/ay\n"
        "✅ Temel planın her şeyi\n"
        "✅ Detaylı piyasa değeri analizi\n"
        "✅ Hasar ve teknik detaylar\n"
        "✅ Öncelikli gönderim (30 dk önce)\n"
        "✅ Haftalık en iyi fırsatlar özeti\n\n"
        "📌 Not: Ödeme Stripe güvenli altyapısıyla işlenir.\n"
        "Dilediğiniz zaman iptal edebilirsiniz.\n\n"
        "Ödeme sonrası aboneliğiniz otomatik olarak aktif olacaktır.\n"
        "Sorun yaşarsanız: /destek"
    )

    keyboard = [
        [InlineKeyboardButton(f"💳 Temel — ₺{settings.PRICE_BASIC_TL}/ay", url=basic_link)],
        [InlineKeyboardButton(f"💳 Pro — ₺{settings.PRICE_PRO_TL}/ay", url=pro_link)],
    ]
    await update.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ── /durum ───────────────────────────────────────────────────────

async def durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kullanıcı hesap durumu."""
    telegram_id = update.effective_user.id
    user = await get_user(telegram_id)
    if not user:
        await update.message.reply_text("Henüz kayıtlı değilsiniz. /start ile başlayın.")
        return

    plan_labels = {
        "trial": "Ücretsiz Deneme 🎁",
        "active_basic": "Temel Plan 🥉",
        "active_pro": "Pro Plan 🥇",
        "expired": "Süresi Dolmuş ⏰",
    }
    plan_label = plan_labels.get(user["subscription"], user["subscription"])

    if user["subscription"] == "trial":
        from datetime import timedelta
        trial_end = datetime.fromisoformat(user["trial_start"]) + timedelta(days=settings.TRIAL_DAYS)
        expiry_text = f"Deneme bitiş: {trial_end.strftime('%d.%m.%Y')}"
    elif user.get("sub_expiry"):
        expiry_text = f"Abonelik bitiş: {user['sub_expiry'][:10]}"
    else:
        expiry_text = ""

    cities = json.loads(user.get("cities", '["all"]'))
    cities_text = "Tüm Türkiye" if "all" in cities else ", ".join(cities)
    car_types = json.loads(user.get("car_types", '["all"]'))
    car_types_text = "Tümü" if "all" in car_types else ", ".join(car_types)

    today_sent = await get_today_sent_count(telegram_id)
    total_sent = await get_total_sent_count(telegram_id)

    text = (
        f"📊 Hesap Durumunuz\n\n"
        f"👤 {user.get('first_name', '')} (@{user.get('username', '')})\n"
        f"📋 Plan: {plan_label}\n"
        f"📅 {expiry_text}\n\n"
        f"⚙️ Tercihleriniz:\n"
        f"🏙️ Şehir: {cities_text}\n"
        f"🚗 Araç tipi: {car_types_text}\n"
        f"📏 Max KM: {user.get('max_km', 250000):,} km\n"
        f"📉 Min indirim: %{user.get('min_discount', 20)}\n\n"
        f"📨 Bugün gönderilen: {today_sent} ilan\n"
        f"🏆 Toplam gönderilen: {total_sent} ilan\n\n"
        f"/ayarlar ile tercihlerinizi değiştirebilirsiniz."
    )
    await update.message.reply_text(text)


# ── /ornek ───────────────────────────────────────────────────────

async def ornek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Örnek ihale ilanı gösterir."""
    best = await get_best_auction()
    if best and best.get("ai_summary"):
        await update.message.reply_text(best["ai_summary"])
        await update.message.reply_text(
            "💡 Bu gerçek bir ilan örneğidir. Aboneler sabah 08:00 ve "
            "akşam 18:00'de bu formatta ilanlar alır."
        )
    else:
        # Örnek statik ilan
        sample = (
            "🚗 Volkswagen Passat 2019 | 85.000 km\n"
            "📍 İstanbul — İcra İhalesi\n"
            "📅 İhale: 25.04.2026\n"
            "💰 Açılış: ₺385.000\n"
            "📊 Piyasa: ₺620.000 | ✅ Fark: ₺235.000 (%38)\n"
            "✅ Hasar kaydı: Belirtilmemiş\n"
            "🔗 ornek-link.gov.tr\n\n"
            "💡 Bu bir örnek ilandır. Gerçek ilanlar abonelere "
            "sabah 08:00 ve akşam 18:00'de gönderilir."
        )
        await update.message.reply_text(sample)


# ── /davet ───────────────────────────────────────────────────────

async def davet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Referral davet sistemi."""
    if not await _check_active(update):
        return

    telegram_id = update.effective_user.id
    user = await get_user(telegram_id)
    referral_count = user.get("referral_count", 0) if user else 0
    free_months = user.get("free_months_earned", 0) if user else 0

    link = f"https://t.me/{settings.BOT_USERNAME}?start=ref_{telegram_id}"
    text = (
        "🎁 Arkadaşlarınızı Davet Edin!\n\n"
        f"Davet linkiniz:\n{link}\n\n"
        "🏆 Nasıl çalışır?\n"
        "• 3 arkadaşınız bota katılırsa → 1 ay ücretsiz abonelik kazanırsınız\n"
        f"• Kazandığınız aylar: {free_months}\n"
        f"• Davet ettiğiniz kişi sayısı: {referral_count}\n\n"
        "Linki araç gruplarında, arkadaşlarınızla paylaşın!"
    )
    await update.message.reply_text(text)


# ── /destek ──────────────────────────────────────────────────────

async def destek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Destek bilgileri."""
    text = (
        "📞 Destek\n\n"
        f"Sorun veya öneriniz için:\n"
        f"@{settings.SUPPORT_USERNAME} adresine yazabilirsiniz.\n\n"
        "⏰ Yanıt süresi: 24 saat içinde\n\n"
        "Sık sorulan sorular:\n"
        "❓ Ödeme yaptım ama aktif olmadı → Biraz bekleyin, "
        "otomatik aktif olacaktır. Sorun devam ederse destek yazın.\n"
        "❓ İlan gelmiyor → /ayarlar ile filtrelerinizi kontrol edin\n"
        "❓ İptal etmek istiyorum → Stripe üzerinden iptal edebilirsiniz."
    )
    await update.message.reply_text(text)


# ── /yardim ──────────────────────────────────────────────────────

async def yardim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yardım mesajı."""
    text = (
        "📖 Araç İhale Radar — Yardım\n\n"
        "🤖 Bu bot, Türkiye'deki resmi araç ihalelerini takip eder "
        "ve size en iyi fırsatları gönderir.\n\n"
        "📋 Komutlar:\n"
        "/start — Bota başla\n"
        "/ayarlar — Tercihlerinizi ayarlayın\n"
        "/abone — Abonelik planları\n"
        "/durum — Hesap durumunuz\n"
        "/ornek — Örnek ilan\n"
        "/davet — Arkadaşlarınızı davet edin\n"
        "/destek — Destek\n\n"
        "📅 İlanlar sabah 08:00 ve akşam 18:00'de gönderilir.\n"
        "⚙️ /ayarlar ile şehir, KM ve indirim filtresi belirleyebilirsiniz."
    )
    await update.message.reply_text(text)


# ── /istatistik (ADMIN) ─────────────────────────────────────────

async def istatistik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin istatistik paneli."""
    if update.effective_user.id != settings.TELEGRAM_ADMIN_ID:
        await update.message.reply_text("⛔ Bu komuta erişiminiz yok.")
        return

    stats = await get_dashboard_stats()

    scraper_lines = []
    for sh in stats.get("scraper_health", []):
        status = "✅" if sh["consecutive_fails"] == 0 else f"❌ ({sh['consecutive_fails']} hata)"
        scraper_lines.append(f"  • {sh['source']}: {status}")
    scraper_text = "\n".join(scraper_lines) if scraper_lines else "  Henüz veri yok"

    text = (
        "📊 Admin Paneli — Araç İhale Radar\n\n"
        "👥 Kullanıcılar:\n"
        f"• Toplam: {stats['total_users']}\n"
        f"• Aktif abone (Basic): {stats['active_basic']}\n"
        f"• Aktif abone (Pro): {stats['active_pro']}\n"
        f"• Deneme: {stats['trial_users']}\n"
        f"• Süresi dolmuş: {stats['expired_users']}\n\n"
        "💰 Gelir:\n"
        f"• Tahmini aylık: ₺{stats['estimated_monthly']:,.0f}\n\n"
        "📨 Bugünkü broadcast:\n"
        f"• Sabah gönderilen: {stats['morning_sent']}\n"
        f"• Akşam gönderilen: {stats['evening_sent']}\n"
        f"• Bulunan araç: {stats['auctions_today']}\n\n"
        f"🔧 Scraper durumu:\n{scraper_text}"
    )
    await update.message.reply_text(text)


# ── Inline Keyboard Handler ─────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tüm inline buton callback'lerini yönetir."""
    query = update.callback_query
    await query.answer()
    data = query.data
    telegram_id = query.from_user.id

    # ── Şehir filtresi ──
    if data == "pref_city":
        cities = [
            "İstanbul", "Ankara", "İzmir", "Bursa", "Antalya",
            "Adana", "Konya", "Gaziantep", "Mersin", "Kayseri",
            "Trabzon", "Samsun", "Eskişehir", "Denizli",
        ]
        keyboard = []
        row = []
        for city in cities:
            row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🇹🇷 Tüm Türkiye", callback_data="city_all")])
        await query.edit_message_text(
            "🏙️ Hangi şehirlerden ilan almak istiyorsunuz?\n"
            "(Birden fazla seçebilirsiniz, son olarak 'Kaydet' butonuna basın)",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("city_"):
        city = data.replace("city_", "")
        if city == "all":
            await update_user(telegram_id, cities=json.dumps(["all"]))
            await query.edit_message_text("✅ Şehir filtresi: Tüm Türkiye olarak ayarlandı.")
        else:
            user = await get_user(telegram_id)
            current = json.loads(user.get("cities", '["all"]')) if user else ["all"]
            if "all" in current:
                current = []
            if city in current:
                current.remove(city)
            else:
                current.append(city)
            if not current:
                current = ["all"]
            await update_user(telegram_id, cities=json.dumps(current))
            display = "Tüm Türkiye" if "all" in current else ", ".join(current)
            await query.edit_message_text(f"✅ Şehir filtresi güncellendi: {display}")

    # ── Araç tipi ──
    elif data == "pref_cartype":
        types = [
            ("Otomobil", "type_otomobil"),
            ("SUV/Crossover", "type_suv"),
            ("Minivan", "type_minivan"),
            ("Ticari", "type_ticari"),
            ("Motosiklet", "type_moto"),
            ("Tümü", "type_all"),
        ]
        keyboard = [[InlineKeyboardButton(t[0], callback_data=t[1])] for t in types]
        await query.edit_message_text(
            "🚗 Hangi araç türlerini takip etmek istiyorsunuz?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("type_"):
        car_type = data.replace("type_", "")
        if car_type == "all":
            await update_user(telegram_id, car_types=json.dumps(["all"]))
            await query.edit_message_text("✅ Araç tipi: Tümü olarak ayarlandı.")
        else:
            user = await get_user(telegram_id)
            current = json.loads(user.get("car_types", '["all"]')) if user else ["all"]
            if "all" in current:
                current = []
            if car_type in current:
                current.remove(car_type)
            else:
                current.append(car_type)
            if not current:
                current = ["all"]
            await update_user(telegram_id, car_types=json.dumps(current))
            display = "Tümü" if "all" in current else ", ".join(current)
            await query.edit_message_text(f"✅ Araç tipi güncellendi: {display}")

    # ── Maksimum KM ──
    elif data == "pref_km":
        options = [
            ("50.000 km", "km_50000"),
            ("100.000 km", "km_100000"),
            ("150.000 km", "km_150000"),
            ("200.000 km", "km_200000"),
            ("Sınırsız", "km_999999"),
        ]
        keyboard = [[InlineKeyboardButton(o[0], callback_data=o[1])] for o in options]
        await query.edit_message_text(
            "📏 Maksimum kaç KM'ye kadar araç görmek istiyorsunuz?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("km_"):
        km = int(data.replace("km_", ""))
        await update_user(telegram_id, max_km=km)
        km_text = "Sınırsız" if km >= 999999 else f"{km:,} km".replace(",", ".")
        await query.edit_message_text(f"✅ Maksimum KM: {km_text} olarak ayarlandı.")

    # ── Minimum indirim ──
    elif data == "pref_discount":
        options = [
            ("%15+", "disc_15"),
            ("%20+", "disc_20"),
            ("%30+", "disc_30"),
            ("%40+", "disc_40"),
        ]
        keyboard = [[InlineKeyboardButton(o[0], callback_data=o[1])] for o in options]
        keyboard.append([
            InlineKeyboardButton("ℹ️ Bilgi", callback_data="disc_info")
        ])
        await query.edit_message_text(
            "📉 Minimum kaç % indirimli ilanları görmek istiyorsunuz?\n"
            "Daha yüksek seçerseniz daha az ama daha kaliteli ilan gelir.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("disc_"):
        if data == "disc_info":
            await query.edit_message_text(
                "ℹ️ İndirim yüzdesi, aracın ihale açılış fiyatının "
                "Sahibinden'deki piyasa fiyatına göre ne kadar ucuz olduğunu gösterir.\n\n"
                "Örnek: %30 = Piyasa fiyatı ₺500.000 olan araç ₺350.000'den satışa çıkıyor."
            )
            return
        disc = int(data.replace("disc_", ""))
        await update_user(telegram_id, min_discount=disc)
        await query.edit_message_text(f"✅ Minimum indirim: %{disc} olarak ayarlandı.")


# ── Yardımcı: aktif kullanıcı kontrolü ──────────────────────────

async def _check_active(update: Update) -> bool:
    """Kullanıcının aktif olup olmadığını kontrol eder."""
    telegram_id = update.effective_user.id
    user = await get_user(telegram_id)

    if not user:
        await update.message.reply_text("Henüz kayıtlı değilsiniz. /start ile başlayın.")
        return False

    if user["subscription"] == "expired":
        total = await get_total_sent_count(telegram_id)
        await update.message.reply_text(
            "⏰ Ücretsiz deneme süreniz doldu.\n\n"
            "Araç ihale fırsatlarını almaya devam etmek için:\n"
            "/abone\n\n"
            f"Şimdiye kadar {total} ilan gönderdik size.\n"
            "Kaçırmak istemezsiniz! 🚗"
        )
        return False

    await update_user(telegram_id, last_active=datetime.now().isoformat())
    return True

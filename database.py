"""
Araç İhale Radar — Veritabanı
SQLite şeması ve tüm async sorgu fonksiyonları.
"""
import json
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional
from config import settings

DB_PATH = settings.DB_PATH


async def get_db() -> aiosqlite.Connection:
    """Veritabanı bağlantısı döndürür."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    """Tüm tabloları oluşturur (varsa atlar)."""
    db = await get_db()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id     INTEGER PRIMARY KEY,
                username        TEXT,
                first_name      TEXT,
                subscription    TEXT DEFAULT 'trial',
                trial_start     TEXT,
                sub_expiry      TEXT,
                stripe_customer TEXT,
                cities          TEXT DEFAULT '["all"]',
                max_km          INTEGER DEFAULT 250000,
                min_discount    INTEGER DEFAULT 20,
                car_types       TEXT DEFAULT '["all"]',
                referrer_id     INTEGER,
                referral_count  INTEGER DEFAULT 0,
                free_months_earned INTEGER DEFAULT 0,
                joined_at       TEXT,
                last_active     TEXT
            );

            CREATE TABLE IF NOT EXISTS auctions (
                id              TEXT PRIMARY KEY,
                source          TEXT,
                source_label    TEXT,
                title           TEXT,
                marka           TEXT,
                model           TEXT,
                yil             INTEGER,
                km              INTEGER,
                sehir           TEXT,
                ilce            TEXT,
                ihale_tarihi    TEXT,
                ihale_saati     TEXT,
                ihale_yeri      TEXT,
                acilis_fiyati   REAL,
                market_value    REAL,
                discount_pct    REAL,
                gap_tl          REAL,
                hasar_durumu    TEXT,
                plaka           TEXT,
                renk            TEXT,
                yakit           TEXT,
                vites           TEXT,
                ihale_url       TEXT,
                ai_summary      TEXT,
                used_ai         INTEGER DEFAULT 0,
                scraped_at      TEXT,
                sent_count      INTEGER DEFAULT 0,
                is_active       INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS price_cache (
                cache_key       TEXT PRIMARY KEY,
                market_value    REAL,
                sample_count    INTEGER,
                cached_at       TEXT
            );

            CREATE TABLE IF NOT EXISTS sent_alerts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id     INTEGER,
                auction_id      TEXT,
                sent_at         TEXT
            );

            CREATE TABLE IF NOT EXISTS broadcast_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_type        TEXT,
                started_at      TEXT,
                finished_at     TEXT,
                auctions_found  INTEGER DEFAULT 0,
                alerts_sent     INTEGER DEFAULT 0,
                users_reached   INTEGER DEFAULT 0,
                errors          TEXT
            );

            CREATE TABLE IF NOT EXISTS scraper_health (
                source          TEXT PRIMARY KEY,
                last_success    TEXT,
                last_attempt    TEXT,
                consecutive_fails INTEGER DEFAULT 0,
                total_found_today INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS referrals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id     INTEGER,
                referred_id     INTEGER,
                created_at      TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_auctions_active ON auctions(is_active);
            CREATE INDEX IF NOT EXISTS idx_auctions_sehir ON auctions(sehir);
            CREATE INDEX IF NOT EXISTS idx_auctions_scraped ON auctions(scraped_at);
            CREATE INDEX IF NOT EXISTS idx_sent_alerts_user ON sent_alerts(telegram_id, auction_id);
        """)
        await db.commit()
    finally:
        await db.close()


# ── Kullanıcı sorguları ──────────────────────────────────────────

async def get_user(telegram_id: int) -> Optional[dict]:
    """Kullanıcıyı telegram_id ile getirir."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def create_user(
    telegram_id: int,
    username: str = None,
    first_name: str = None,
    referrer_id: int = None,
) -> dict:
    """Yeni kullanıcı oluşturur ve döndürür."""
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            """INSERT OR IGNORE INTO users
               (telegram_id, username, first_name, subscription, trial_start,
                referrer_id, joined_at, last_active)
               VALUES (?, ?, ?, 'trial', ?, ?, ?, ?)""",
            (telegram_id, username, first_name, now, referrer_id, now, now),
        )
        # Referral kaydı
        if referrer_id:
            await db.execute(
                "INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
                (referrer_id, telegram_id, now),
            )
            await db.execute(
                "UPDATE users SET referral_count = referral_count + 1 WHERE telegram_id = ?",
                (referrer_id,),
            )
        await db.commit()
    finally:
        await db.close()
    return await get_user(telegram_id)


async def update_user(telegram_id: int, **kwargs):
    """Kullanıcı alanlarını günceller."""
    if not kwargs:
        return
    db = await get_db()
    try:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [telegram_id]
        await db.execute(
            f"UPDATE users SET {sets} WHERE telegram_id = ?", vals
        )
        await db.commit()
    finally:
        await db.close()


async def get_active_users() -> list[dict]:
    """Aktif abonelikleri olan kullanıcıları döndürür (trial + active)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM users WHERE subscription IN ('trial', 'active_basic', 'active_pro')"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_users_for_broadcast() -> list[dict]:
    """Broadcast gönderilecek kullanıcıları döndürür."""
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT * FROM users
               WHERE subscription IN ('trial', 'active_basic', 'active_pro')
               AND (subscription != 'trial'
                    OR trial_start >= ?)""",
            ((datetime.now() - timedelta(days=settings.TRIAL_DAYS)).isoformat(),),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_user_by_stripe_customer(stripe_customer: str) -> Optional[dict]:
    """Stripe müşteri ID'si ile kullanıcı bulur."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM users WHERE stripe_customer = ?", (stripe_customer,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ── İhale sorguları ──────────────────────────────────────────────

async def save_auction(auction: dict) -> bool:
    """İhaleyi kaydeder. Zaten varsa False döner."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT 1 FROM auctions WHERE id = ?", (auction["id"],)
        )
        if await cursor.fetchone():
            return False
        cols = ", ".join(auction.keys())
        placeholders = ", ".join("?" for _ in auction)
        await db.execute(
            f"INSERT INTO auctions ({cols}) VALUES ({placeholders})",
            tuple(auction.values()),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def get_auctions_for_user(user: dict, limit: int = 5) -> list[dict]:
    """Kullanıcı tercihlerine göre filtrelenmiş ihaleleri döndürür."""
    cities = json.loads(user.get("cities", '["all"]'))
    car_types = json.loads(user.get("car_types", '["all"]'))
    max_km = user.get("max_km", 250000)
    min_discount = user.get("min_discount", 20)

    db = await get_db()
    try:
        query = "SELECT * FROM auctions WHERE is_active = 1"
        params = []

        if "all" not in cities:
            placeholders = ", ".join("?" for _ in cities)
            query += f" AND LOWER(sehir) IN ({placeholders})"
            params.extend([c.lower() for c in cities])

        query += " AND (km IS NULL OR km <= ?)"
        params.append(max_km)

        query += " AND (discount_pct IS NULL OR discount_pct >= ?)"
        params.append(min_discount)

        query += " ORDER BY discount_pct DESC NULLS LAST LIMIT ?"
        params.append(limit)

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def mark_sent(telegram_id: int, auction_id: str):
    """Gönderilen ilanı kaydet."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO sent_alerts (telegram_id, auction_id, sent_at) VALUES (?, ?, ?)",
            (telegram_id, auction_id, datetime.now().isoformat()),
        )
        await db.execute(
            "UPDATE auctions SET sent_count = sent_count + 1 WHERE id = ?",
            (auction_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def already_sent(telegram_id: int, auction_id: str) -> bool:
    """Bu ilan bu kullanıcıya daha önce gönderilmiş mi?"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT 1 FROM sent_alerts WHERE telegram_id = ? AND auction_id = ?",
            (telegram_id, auction_id),
        )
        return bool(await cursor.fetchone())
    finally:
        await db.close()


# ── Broadcast log ────────────────────────────────────────────────

async def log_broadcast(run_type: str, stats: dict):
    """Broadcast çalıştırma kaydını yazar."""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO broadcast_log
               (run_type, started_at, finished_at, auctions_found, alerts_sent, users_reached, errors)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                run_type,
                stats.get("started_at"),
                stats.get("finished_at"),
                stats.get("auctions_found", 0),
                stats.get("alerts_sent", 0),
                stats.get("users_reached", 0),
                json.dumps(stats.get("errors", []), ensure_ascii=False),
            ),
        )
        await db.commit()
    finally:
        await db.close()


# ── İstatistik sorguları ─────────────────────────────────────────

async def get_weekly_stats() -> dict:
    """Haftalık istatistikleri döndürür."""
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    db = await get_db()
    try:
        stats = {}

        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE joined_at >= ?", (week_ago,)
        )
        stats["new_users"] = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE subscription IN ('active_basic', 'active_pro')"
        )
        stats["total_active"] = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM sent_alerts WHERE sent_at >= ?", (week_ago,)
        )
        stats["messages_sent"] = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM auctions WHERE scraped_at >= ?", (week_ago,)
        )
        stats["auctions_found"] = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT AVG(discount_pct) FROM auctions WHERE scraped_at >= ? AND discount_pct IS NOT NULL",
            (week_ago,),
        )
        avg = (await cursor.fetchone())[0]
        stats["avg_discount"] = avg or 0

        # En iyi fırsat
        cursor = await db.execute(
            """SELECT * FROM auctions
               WHERE scraped_at >= ? AND discount_pct IS NOT NULL
               ORDER BY discount_pct DESC LIMIT 1""",
            (week_ago,),
        )
        best = await cursor.fetchone()
        stats["best_auction"] = dict(best) if best else None

        return stats
    finally:
        await db.close()


async def get_dashboard_stats() -> dict:
    """Admin dashboard istatistikleri."""
    today = datetime.now().date().isoformat()
    db = await get_db()
    try:
        stats = {}

        cursor = await db.execute("SELECT COUNT(*) FROM users")
        stats["total_users"] = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE subscription = 'active_basic'"
        )
        stats["active_basic"] = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE subscription = 'active_pro'"
        )
        stats["active_pro"] = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE subscription = 'trial'"
        )
        stats["trial_users"] = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE subscription = 'expired'"
        )
        stats["expired_users"] = (await cursor.fetchone())[0]

        stats["estimated_monthly"] = (
            stats["active_basic"] * settings.PRICE_BASIC_TL
            + stats["active_pro"] * settings.PRICE_PRO_TL
        )

        # Bugünkü broadcast
        cursor = await db.execute(
            "SELECT * FROM broadcast_log WHERE started_at >= ? ORDER BY started_at DESC",
            (today,),
        )
        rows = await cursor.fetchall()
        stats["morning_sent"] = 0
        stats["evening_sent"] = 0
        for row in rows:
            r = dict(row)
            if r["run_type"] == "morning":
                stats["morning_sent"] = r["alerts_sent"]
            elif r["run_type"] == "evening":
                stats["evening_sent"] = r["alerts_sent"]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM auctions WHERE scraped_at >= ?", (today,)
        )
        stats["auctions_today"] = (await cursor.fetchone())[0]

        # Scraper sağlığı
        cursor = await db.execute("SELECT * FROM scraper_health")
        stats["scraper_health"] = [dict(r) for r in await cursor.fetchall()]

        return stats
    finally:
        await db.close()


# ── Scraper health ───────────────────────────────────────────────

async def update_scraper_health(source: str, success: bool, found_count: int = 0):
    """Scraper sağlık durumunu günceller."""
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        if success:
            await db.execute(
                """INSERT INTO scraper_health (source, last_success, last_attempt, consecutive_fails, total_found_today)
                   VALUES (?, ?, ?, 0, ?)
                   ON CONFLICT(source) DO UPDATE SET
                   last_success = ?, last_attempt = ?, consecutive_fails = 0,
                   total_found_today = total_found_today + ?""",
                (source, now, now, found_count, now, now, found_count),
            )
        else:
            await db.execute(
                """INSERT INTO scraper_health (source, last_attempt, consecutive_fails)
                   VALUES (?, ?, 1)
                   ON CONFLICT(source) DO UPDATE SET
                   last_attempt = ?, consecutive_fails = consecutive_fails + 1""",
                (source, now, now),
            )
        await db.commit()
    finally:
        await db.close()


# ── Fiyat cache ──────────────────────────────────────────────────

async def cache_price(key: str, value: float, sample_count: int):
    """Piyasa fiyatını cache'e yazar."""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO price_cache (cache_key, market_value, sample_count, cached_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(cache_key) DO UPDATE SET
               market_value = ?, sample_count = ?, cached_at = ?""",
            (
                key, value, sample_count, datetime.now().isoformat(),
                value, sample_count, datetime.now().isoformat(),
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def get_cached_price(key: str) -> Optional[float]:
    """24 saatten yeni cache'lenmiş fiyatı döndürür."""
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT market_value FROM price_cache WHERE cache_key = ? AND cached_at >= ?",
            (key, cutoff),
        )
        row = await cursor.fetchone()
        return row[0] if row else None
    finally:
        await db.close()


# ── Yardımcı sorgular ───────────────────────────────────────────

async def get_today_sent_count(telegram_id: int) -> int:
    """Bugün kullanıcıya gönderilen ilan sayısı."""
    today = datetime.now().date().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM sent_alerts WHERE telegram_id = ? AND sent_at >= ?",
            (telegram_id, today),
        )
        return (await cursor.fetchone())[0]
    finally:
        await db.close()


async def get_total_sent_count(telegram_id: int) -> int:
    """Kullanıcıya toplam gönderilen ilan sayısı."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM sent_alerts WHERE telegram_id = ?",
            (telegram_id,),
        )
        return (await cursor.fetchone())[0]
    finally:
        await db.close()


async def get_best_auction() -> Optional[dict]:
    """En yüksek indirimli aktif ihaleyi döndürür."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT * FROM auctions
               WHERE is_active = 1 AND discount_pct IS NOT NULL
               ORDER BY discount_pct DESC LIMIT 1"""
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_expired_trial_users() -> list[dict]:
    """Süresi dolmuş trial kullanıcıları döndürür."""
    cutoff = (datetime.now() - timedelta(days=settings.TRIAL_DAYS)).isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM users WHERE subscription = 'trial' AND trial_start <= ?",
            (cutoff,),
        )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_expiring_subscribers(days_ahead: int = 3) -> list[dict]:
    """Belirtilen gün içinde süresi dolacak aboneleri döndürür."""
    target = (datetime.now() + timedelta(days=days_ahead)).date().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT * FROM users
               WHERE subscription LIKE 'active_%'
               AND sub_expiry IS NOT NULL
               AND DATE(sub_expiry) = ?""",
            (target,),
        )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_expired_subscribers() -> list[dict]:
    """Süresi dolmuş aktif aboneleri döndürür."""
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT * FROM users
               WHERE subscription LIKE 'active_%'
               AND sub_expiry IS NOT NULL
               AND sub_expiry <= ?""",
            (now,),
        )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_scraper_alerts() -> list[dict]:
    """3+ ardışık hata yapan scraper'ları döndürür."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM scraper_health WHERE consecutive_fails >= 3"
        )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()

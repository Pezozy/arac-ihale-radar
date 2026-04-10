# Araç İhale Radar 🚗

Türkiye'deki resmi araç ihalelerini otomatik takip eden ve Telegram üzerinden fırsat bildirimleri gönderen bot.

**English:** Automated Turkish vehicle auction alert Telegram bot. Scrapes government auction sites twice daily, generates AI-powered opportunity summaries, and sends them to subscribers.

---

## Özellikler

- **Otomatik Scraping**: İcra ihaleleri, vergi icra, gümrük ihaleleri, belediye ihaleleri
- **Piyasa Karşılaştırma**: Sahibinden'den piyasa değeri çekerek indirim % hesaplama
- **AI Özetler**: Groq (Llama 3.1 70B) ile Türkçe fırsat özetleri
- **Günde 2 Bülten**: Sabah 08:00 ve akşam 18:00 (İstanbul saati)
- **Abonelik Sistemi**: 7 gün ücretsiz deneme + Stripe ile ₺149/ay ve ₺299/ay planlar
- **Referral**: 3 davet = 1 ay ücretsiz
- **Admin Paneli**: Sadece Telegram komutları ile (/istatistik)
- **Sıfır Bakım**: Deploy et, unut — her şey otomatik

---

## Teknoloji

- Python 3.11+
- python-telegram-bot v20 (async)
- APScheduler
- aiohttp + BeautifulSoup4
- aiosqlite (SQLite)
- Groq SDK (ücretsiz AI)
- FastAPI (Stripe webhook)
- Stripe (ödeme)

---

## Kurulum / Setup

### 1. Repo'yu klonla

```bash
git clone <repo-url>
cd arac-ihale-radar
pip install -r requirements.txt
```

### 2. Telegram Bot Oluştur

1. Telegram'da [@BotFather](https://t.me/BotFather) ile konuş
2. `/newbot` komutu ile yeni bot oluştur
3. Bot adı: `Araç İhale Radar`
4. Bot username: `AracIhaleRadarBot` (veya istediğiniz isim)
5. Verilen **token**'ı kaydet

### 3. Public Kanal Oluştur

1. Telegram'da yeni kanal oluştur: `@AracIhaleRadar`
2. Bot'u kanala admin olarak ekle
3. Kanal ID'sini al (@ ile: `@AracIhaleRadar`)

### 4. Groq API Key (Ücretsiz)

1. [console.groq.com](https://console.groq.com) adresine git
2. Kayıt ol (Google ile hızlı)
3. API Keys > Create API Key
4. Key'i kaydet

### 5. Stripe Ayarları

1. [dashboard.stripe.com](https://dashboard.stripe.com) hesabı aç
2. **Payment Links** bölümünden:
   - Temel Plan: ₺149/ay recurring link oluştur
   - Pro Plan: ₺299/ay recurring link oluştur
3. **Developers > Webhooks**:
   - Endpoint URL: `https://<railway-url>/webhook/stripe`
   - Events: `checkout.session.completed`, `invoice.payment_failed`, `customer.subscription.deleted`
   - Webhook signing secret'ı kaydet

### 6. .env Dosyası

```bash
cp .env.example .env
```

Tüm değerleri doldur. (Detaylar `.env.example` dosyasında)

### 7. Lokal Test

```bash
python main.py
```

Bot çalışırsa Telegram'da admin ID'ye bildirim gelir.

### 8. Railway'e Deploy

```bash
# Railway CLI kur
npm install -g @railway/cli

# Giriş yap
railway login

# Proje oluştur
railway init

# Environment variable'ları ekle
railway variables set TELEGRAM_BOT_TOKEN=...
railway variables set TELEGRAM_ADMIN_ID=...
# ... diğer tüm .env değişkenleri

# Deploy
railway up
```

Alternatif: Railway dashboard'dan GitHub repo bağla, otomatik deploy.

### 9. Stripe Webhook URL'ini Güncelle

Railway deploy sonrası verilen URL'yi (ör: `https://arac-ihale-radar-production.up.railway.app`) Stripe webhook endpoint'ine ekle:

```
https://<railway-url>/webhook/stripe
```

---

## Deploy Sonrası

- **Laptop kapatılabilir** — bot Railway sunucusunda çalışır
- Sabah 08:00 ve akşam 18:00'de otomatik bülten gönderilir
- `/istatistik` komutu ile Telegram'dan takip edin
- Haftalık rapor her Pazartesi 08:30'da admin'e gelir
- Scraper hatası olursa otomatik bildirim gelir

---

## Komutlar

| Komut | Açıklama |
|-------|----------|
| `/start` | Bota başla (7 gün ücretsiz deneme) |
| `/ayarlar` | Şehir, araç tipi, KM, indirim filtresi |
| `/abone` | Abonelik planları ve ödeme |
| `/durum` | Hesap durumu |
| `/ornek` | Örnek ilan |
| `/davet` | Referral linki |
| `/destek` | Destek bilgileri |
| `/yardim` | Yardım |
| `/istatistik` | Admin paneli (sadece admin) |

---

## Proje Yapısı

```
arac-ihale-radar/
├── main.py           # Giriş noktası — her şeyi başlatır
├── bot.py            # Telegram komut handler'ları
├── scraper.py        # Tüm scraping mantığı
├── analyzer.py       # AI özet + template fallback
├── database.py       # SQLite şema ve sorgular
├── scheduler.py      # Zamanlanmış görev tanımları
├── payments.py       # Stripe webhook (FastAPI)
├── config.py         # .env ayarları
├── utils.py          # Yardımcı fonksiyonlar
├── requirements.txt
├── railway.json
├── Procfile
└── .env.example
```

---

## Dayanıklılık

- Her scraper bağımsız — biri çökerse diğerleri çalışmaya devam eder
- Groq API çökerse template fallback devreye girer
- Her Telegram mesajı try/except içinde — hata scheduler'ı durdurmaz
- Stripe webhook başarısız olursa abonelik manuel aktif edilebilir
- 3+ ardışık scraper hatası → admin'e otomatik uyarı

---

## Yasal Not

Tüm veriler kamuya açık Türkiye Cumhuriyeti resmi web sitelerinden toplanmaktadır. Bu bot sadece kamuya açık bilgileri derler ve kullanıcılara sunar. Herhangi bir gizli veya özel bilgiye erişim sağlanmamaktadır.

---

## Lisans

MIT

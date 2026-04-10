"""
Araç İhale Radar — AI Özet Üretici
Groq (Llama 3.1 70B) ile Türkçe fırsat özetleri.
Template fallback her zaman çalışır.
"""
import asyncio
from typing import Optional

from groq import AsyncGroq
from config import settings
from utils import log, format_price, format_km

# Groq client — None olabilir eğer API key yoksa
groq_client: Optional[AsyncGroq] = None
if settings.GROQ_API_KEY:
    groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)

SYSTEM_PROMPT = """Sen Türkiye'deki araç ihale fırsatlarını analiz eden bir uzmansın.
Kullanıcılara kısa, net, faydalı Türkçe özetler yazıyorsun.
Emojiler kullan. Maksimum 6 satır. Abartma yapma, sadece gerçekleri yaz.
Fırsatın gerçek mi sahte mi olduğunu değerlendir."""


def generate_template_summary(auction: dict) -> str:
    """
    Template tabanlı özet — API gerekmez, her zaman çalışır.
    Bu güvenlik ağıdır; %100 güvenilir olmalıdır.
    """
    marka = auction.get("marka") or "Bilinmeyen Marka"
    model = auction.get("model") or ""
    yil = auction.get("yil") or ""
    km = auction.get("km")
    sehir = auction.get("sehir") or "Türkiye"
    source_label = auction.get("source_label") or "İhale"
    ihale_tarihi = auction.get("ihale_tarihi") or "Belirtilmemiş"
    ihale_saati = auction.get("ihale_saati") or ""
    acilis_fiyati = auction.get("acilis_fiyati")
    market_value = auction.get("market_value")
    gap_tl = auction.get("gap_tl")
    discount_pct = auction.get("discount_pct")
    hasar_durumu = auction.get("hasar_durumu")
    ihale_url = auction.get("ihale_url") or ""

    lines = []

    # Araç bilgisi
    arac_line = f"🚗 {marka} {model} {yil}".strip()
    if km:
        arac_line += f" | {format_km(km)}"
    lines.append(arac_line)

    # Konum ve kaynak
    lines.append(f"📍 {sehir} — {source_label}")

    # İhale tarihi
    tarih_line = f"📅 İhale: {ihale_tarihi}"
    if ihale_saati:
        tarih_line += f" {ihale_saati}"
    lines.append(tarih_line)

    # Fiyat bilgileri
    if acilis_fiyati:
        fiyat_line = f"💰 Açılış: {format_price(acilis_fiyati)}"
        if market_value and gap_tl and discount_pct:
            fiyat_line += (
                f"\n📊 Piyasa: {format_price(market_value)} | "
                f"✅ Fark: {format_price(gap_tl)} (%{discount_pct:.0f})"
            )
        else:
            fiyat_line += "\n📊 Piyasa değeri: Hesaplanamadı"
        lines.append(fiyat_line)
    else:
        lines.append("💰 Açılış fiyatı: Belirtilmemiş")

    # Hasar durumu
    if hasar_durumu:
        lines.append(f"⚠️ Hasar: {hasar_durumu}")
    else:
        lines.append("✅ Hasar kaydı: Belirtilmemiş")

    # URL
    if ihale_url:
        lines.append(f"🔗 {ihale_url}")

    return "\n".join(lines)


async def generate_ai_summary(auction: dict) -> tuple[str, bool]:
    """
    Groq ile AI özet üretir. Başarısızlıkta template'e düşer.
    Returns: (özet_metni, ai_kullanıldı_mı)
    """
    if not groq_client:
        return generate_template_summary(auction), False

    marka = auction.get("marka") or "Bilinmeyen"
    model = auction.get("model") or ""
    yil = auction.get("yil") or ""
    km = auction.get("km")
    renk = auction.get("renk") or "Belirtilmemiş"
    yakit = auction.get("yakit") or "Belirtilmemiş"
    vites = auction.get("vites") or "Belirtilmemiş"
    hasar = auction.get("hasar_durumu") or "Belirtilmemiş"
    sehir = auction.get("sehir") or ""
    ilce = auction.get("ilce") or ""
    source_label = auction.get("source_label") or ""
    ihale_yeri = auction.get("ihale_yeri") or "Belirtilmemiş"
    acilis_fiyati = auction.get("acilis_fiyati")
    market_value = auction.get("market_value")
    gap_tl = auction.get("gap_tl")
    discount_pct = auction.get("discount_pct")
    ihale_tarihi = auction.get("ihale_tarihi") or ""
    ihale_url = auction.get("ihale_url") or ""

    # Fiyat bilgileri metni
    piyasa_text = format_price(market_value) if market_value else "Bulunamadı"
    fark_text = (
        f"{format_price(gap_tl)} (%{discount_pct:.0f} daha ucuz)"
        if market_value and gap_tl and discount_pct
        else "Hesaplanamadı"
    )

    user_prompt = f"""Bu araç ihalesi için fırsat özeti yaz:

Araç: {marka} {model} {yil}
Kilometre: {format_km(km) if km else 'Belirtilmemiş'}
Renk: {renk}
Yakıt: {yakit}
Vites: {vites}
Hasar Durumu: {hasar}
Şehir / İlçe: {sehir} {ilce}
İhale Türü: {source_label}
İhale Yeri: {ihale_yeri}
Açılış Fiyatı: {format_price(acilis_fiyati) if acilis_fiyati else 'Belirtilmemiş'}
Sahibinden Piyasa Değeri: {piyasa_text}
Tahmini Fark: {fark_text}
İhale Tarihi: {ihale_tarihi}
İlan URL: {ihale_url}

Formatı şöyle kullan:
🚗 [araç adı ve yıl]
📍 [şehir] | [ihale türü]
📅 İhale: [tarih]
💰 Açılış: ₺X | Piyasa: ₺Y | Fark: ₺Z (%N)
⚠️ [dikkat edilmesi gereken önemli 1 husus]
🔗 [url]"""

    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=250,
            temperature=0.2,
        )
        summary = response.choices[0].message.content.strip()
        if summary and len(summary) > 20:
            return summary, True
        # AI boş veya çok kısa döndüyse template kullan
        return generate_template_summary(auction), False

    except Exception as e:
        error_str = str(e).lower()
        if "rate" in error_str or "limit" in error_str:
            log("Groq rate limit — 10sn bekleyip tekrar deniyor")
            await asyncio.sleep(10)
            try:
                response = await groq_client.chat.completions.create(
                    model="llama-3.1-70b-versatile",
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=250,
                    temperature=0.2,
                )
                summary = response.choices[0].message.content.strip()
                if summary and len(summary) > 20:
                    return summary, True
            except Exception:
                pass
        log(f"Groq AI özet hatası: {e}", "error")
        return generate_template_summary(auction), False


async def process_auction_summaries(auctions: list[dict]) -> list[dict]:
    """
    Bir grup ihale için özetler üretir — rate limiting ile.
    Saniyede max 1 Groq isteği (free tier uyumluluğu).
    """
    for auction in auctions:
        if auction.get("ai_summary"):
            continue
        summary, used_ai = await generate_ai_summary(auction)
        auction["ai_summary"] = summary
        auction["used_ai"] = 1 if used_ai else 0
        # Rate limit: Groq free tier için güvenli aralık
        if used_ai:
            await asyncio.sleep(1.2)
    return auctions

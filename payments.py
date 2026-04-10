"""
Araç İhale Radar — Stripe Ödeme Webhook
FastAPI endpoint — Stripe'dan gelen ödeme bildirimlerini işler.
"""
from datetime import datetime, timedelta

import stripe
from fastapi import FastAPI, Request, HTTPException

from config import settings
from database import update_user, get_user, get_user_by_stripe_customer
from utils import log, send_telegram_message

stripe.api_key = settings.STRIPE_SECRET_KEY

payments_app = FastAPI(title="Araç İhale Radar Payments")


@payments_app.get("/health")
async def health():
    """Railway health check endpoint."""
    return {"status": "ok", "service": "arac-ihale-radar"}


@payments_app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Stripe webhook handler — ödeme olaylarını işler."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature")

    if not sig:
        raise HTTPException(400, "Missing stripe-signature header")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig, settings.STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        log("Stripe webhook: Geçersiz imza", "error")
        raise HTTPException(400, "Invalid signature")
    except Exception as e:
        log(f"Stripe webhook hata: {e}", "error")
        raise HTTPException(400, str(e))

    event_type = event["type"]
    log(f"Stripe event: {event_type}")

    # ── Ödeme başarılı ──
    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        telegram_id_str = session.get("client_reference_id")
        if not telegram_id_str:
            log("Stripe: client_reference_id yok, atlanıyor")
            return {"status": "no_telegram_id"}

        try:
            telegram_id = int(telegram_id_str)
        except ValueError:
            log(f"Stripe: Geçersiz client_reference_id: {telegram_id_str}")
            return {"status": "invalid_id"}

        # Plan belirleme (tutara göre)
        amount = session.get("amount_total", 0)  # Kuruş cinsinden
        if amount >= 29900:
            plan = "active_pro"
            plan_label = "Pro"
        else:
            plan = "active_basic"
            plan_label = "Temel"

        # 31 günlük abonelik
        expiry = (datetime.now() + timedelta(days=31)).isoformat()
        stripe_customer = session.get("customer")

        await update_user(
            telegram_id,
            subscription=plan,
            sub_expiry=expiry,
            stripe_customer=stripe_customer,
        )

        # Kullanıcıya bildirim
        await send_telegram_message(
            telegram_id,
            f"✅ Aboneliğiniz aktif!\n\n"
            f"📋 Plan: {plan_label}\n"
            f"📅 Geçerlilik: {expiry[:10]} tarihine kadar\n\n"
            f"Sabah 08:00 ve akşam 18:00'de araç fırsatları gelecek.\n"
            f"Tercihlerinizi ayarlamak için /ayarlar",
        )

        # Admin'e bildirim
        user = await get_user(telegram_id)
        username = user.get("username", "?") if user else "?"
        await send_telegram_message(
            settings.TELEGRAM_ADMIN_ID,
            f"💰 Yeni abone!\n"
            f"👤 @{username} [{telegram_id}]\n"
            f"📋 Plan: {plan_label}\n"
            f"💵 Tutar: ₺{amount / 100:.0f}",
        )

        log(f"Yeni abone: {telegram_id} — {plan_label}")

    # ── Ödeme başarısız ──
    elif event_type == "invoice.payment_failed":
        customer_id = event["data"]["object"].get("customer")
        if customer_id:
            user = await get_user_by_stripe_customer(customer_id)
            if user:
                await send_telegram_message(
                    user["telegram_id"],
                    "⚠️ Abonelik ödemesi başarısız oldu.\n"
                    "Kart bilgilerinizi güncellemek için:\n"
                    f"{settings.STRIPE_CUSTOMER_PORTAL_URL}",
                )
                log(f"Ödeme başarısız: {user['telegram_id']}")

    # ── Abonelik iptal ──
    elif event_type == "customer.subscription.deleted":
        customer_id = event["data"]["object"].get("customer")
        if customer_id:
            user = await get_user_by_stripe_customer(customer_id)
            if user:
                await update_user(user["telegram_id"], subscription="expired")
                await send_telegram_message(
                    user["telegram_id"],
                    "😔 Aboneliğiniz iptal edildi.\n"
                    "Tekrar abone olmak için: /abone",
                )
                log(f"Abonelik iptal: {user['telegram_id']}")

    return {"status": "ok"}

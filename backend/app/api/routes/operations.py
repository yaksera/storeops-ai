"""Read models for the Inventory and Recovery screens."""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.agents.inventory import local_today, needs_reorder, sales_history, variant_forecast
from app.api.deps import DbSession, ShopViewer
from app.models import ActionProposal, Checkout, Customer, Order, Product, Variant
from app.models.base import utcnow
from app.models.enums import CheckoutStatus, ProposalStatus
from app.services.metrics import day_start

router = APIRouter(prefix="/api/shops/{shop_id}", tags=["operations"])


@router.get("/inventory")
async def inventory(ctx: ShopViewer, db: DbSession) -> dict[str, Any]:
    now = utcnow()
    rows = (
        await db.execute(
            select(Variant, Product)
            .join(Product, Product.id == Variant.product_id)
            .where(Variant.shop_id == ctx.shop.id)
            .order_by(Product.title, Variant.title)
        )
    ).all()
    history = await sales_history(db, ctx.shop, [v.id for v, _ in rows], now)
    pending = set(
        (
            await db.scalars(
                select(ActionProposal.target_id).where(
                    ActionProposal.shop_id == ctx.shop.id,
                    ActionProposal.action_type == "draft_po",
                    ActionProposal.status == ProposalStatus.PROPOSED,
                )
            )
        ).all()
    )
    today = local_today(ctx.shop, now)
    items = []
    for variant, product in rows:
        fc = variant_forecast(variant, history[variant.id], today)
        items.append(
            {
                "variant_id": str(variant.id),
                "product": product.title,
                "variant": variant.title,
                "sku": variant.sku,
                "price_minor": variant.price_minor,
                "on_hand": variant.inventory_quantity,
                "reorder_point": fc.reorder_point,
                "daily_rate": fc.daily_rate_30d,
                "days_to_stockout": fc.days_to_stockout,
                "lead_time_days": variant.lead_time_days,
                "suggested_order_qty": fc.suggested_order_qty if needs_reorder(variant, fc) else 0,
                "needs_reorder": needs_reorder(variant, fc),
                "po_pending": str(variant.id) in pending,
            }
        )
    items.sort(key=lambda i: (i["days_to_stockout"] is None, i["days_to_stockout"] or 0))
    return {"currency": ctx.shop.currency, "items": items}


@router.get("/recovery")
async def recovery(ctx: ShopViewer, db: DbSession) -> dict[str, Any]:
    now = utcnow()
    start_today = day_start(ctx.shop, now)
    since = start_today - timedelta(days=13)
    recent = Checkout.shopify_created_at >= since

    abandoned = int(
        await db.scalar(
            select(func.count(Checkout.id)).where(
                Checkout.shop_id == ctx.shop.id,
                recent,
                Checkout.status.in_([CheckoutStatus.ABANDONED, CheckoutStatus.RECOVERED]),
            )
        )
        or 0
    )
    emailed = int(
        await db.scalar(
            select(func.count(Checkout.id)).where(
                Checkout.shop_id == ctx.shop.id, recent, Checkout.reminders_sent > 0
            )
        )
        or 0
    )
    emails = int(
        await db.scalar(
            select(func.coalesce(func.sum(Checkout.reminders_sent), 0)).where(
                Checkout.shop_id == ctx.shop.id, recent
            )
        )
        or 0
    )
    local_day = func.date(func.timezone(ctx.shop.timezone, Order.processed_at))
    by_day: dict[Any, Any] = dict(
        (
            await db.execute(
                select(local_day, func.sum(Order.total_minor))
                .join(Checkout, Checkout.recovered_order_id == Order.id)
                .where(Checkout.shop_id == ctx.shop.id, Order.processed_at >= since)
                .group_by(local_day)
            )
        ).all()
    )
    recovered_orders = int(
        await db.scalar(
            select(func.count(Checkout.id)).where(
                Checkout.shop_id == ctx.shop.id, recent, Checkout.status == CheckoutStatus.RECOVERED
            )
        )
        or 0
    )
    first_day = since.date()
    series = [
        {
            "date": (first_day + timedelta(days=i)).isoformat(),
            "recovered_minor": int(by_day.get(first_day + timedelta(days=i), 0) or 0),
        }
        for i in range(14)
    ]
    carts = (
        await db.execute(
            select(Checkout, Customer)
            .outerjoin(Customer, Customer.id == Checkout.customer_id)
            .where(
                Checkout.shop_id == ctx.shop.id,
                Checkout.status.in_([CheckoutStatus.ABANDONED, CheckoutStatus.RECOVERED]),
                Checkout.shopify_created_at >= start_today - timedelta(days=2),
            )
            .order_by(Checkout.shopify_created_at.desc())
            .limit(40)
        )
    ).all()
    return {
        "currency": ctx.shop.currency,
        "stats": {
            "abandoned_14d": abandoned,
            "emails_sent_14d": emails,
            "recovered_orders_14d": recovered_orders,
            "recovered_revenue_14d_minor": sum(p["recovered_minor"] for p in series),
            "carts_emailed_14d": emailed,
            # Share of carts we followed up on that turned into an order.
            "recovery_rate": round(recovered_orders / emailed, 4) if emailed else 0.0,
        },
        "series": series,
        "carts": [
            {
                "id": str(c.id),
                "customer_name": cust.first_name if cust else None,
                "email": c.email,
                "total_minor": c.total_minor,
                "items": [li.get("title") for li in c.line_items][:4],
                "status": c.status.value,
                "reminders_sent": c.reminders_sent,
                "last_reminder_at": c.last_reminder_at.isoformat() if c.last_reminder_at else None,
                "created_at": c.shopify_created_at.isoformat() if c.shopify_created_at else None,
                "consent": bool(cust and cust.accepts_marketing and not cust.unsubscribed_at),
            }
            for c, cust in carts
        ],
    }

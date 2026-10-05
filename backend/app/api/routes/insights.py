from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.agents.reviews import weekly_summary
from app.api.deps import DbSession, ShopViewer
from app.models import Notification, Review
from app.models.base import utcnow

router = APIRouter(prefix="/api/shops/{shop_id}/insights", tags=["insights"])


@router.get("")
async def insights(ctx: ShopViewer, db: DbSession) -> dict[str, Any]:
    now = utcnow()
    anomalies = (
        await db.scalars(
            select(Notification)
            .where(Notification.shop_id == ctx.shop.id, Notification.kind == "insight.anomaly")
            .order_by(Notification.created_at.desc())
            .limit(30)
        )
    ).all()
    sentiment = dict(
        (
            await db.execute(
                select(Review.sentiment_label, func.count(Review.id))
                .where(Review.shop_id == ctx.shop.id, Review.sentiment_label.is_not(None))
                .group_by(Review.sentiment_label)
            )
        ).all()
    )
    return {
        "anomalies": [
            {
                "id": str(n.id),
                "title": n.title,
                "body": n.body,
                "severity": n.severity.value,
                "direction": n.data.get("anomaly"),
                "change": n.data.get("change"),
                "at": n.created_at.isoformat(),
            }
            for n in anomalies
        ],
        "reviews": await weekly_summary(db, ctx.shop, now),
        "sentiment": {k: int(v) for k, v in sentiment.items()},
    }

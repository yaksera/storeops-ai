import json
import random
import uuid
from datetime import timedelta

import fakeredis
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.effects import outbox_key
from app.agents.pricing import round_price
from app.agents.reviews import sentiment, themes
from app.agents.runner import run_agent
from app.agents.support import classify, escalation_reasons
from app.demo.simulator import DemoStore, Line
from app.models import (
    ActionProposal,
    Message,
    Notification,
    Order,
    Review,
    Shop,
    Ticket,
    User,
    Variant,
)
from app.models.base import utcnow
from app.models.enums import (
    AgentName,
    MessageAuthor,
    MessageDirection,
    ProposalStatus,
    ShopMode,
    TicketChannel,
    TicketStatus,
)
from app.pipeline.normalize import Change
from app.services.shops import create_shop
from tests.conftest import InlineQueue, Session, signup

# ---- units ------------------------------------------------------------------------------------


def test_classify_intents() -> None:
    assert classify("Where is my order? Tracking hasn't moved")[0] == "order_status"
    assert classify("Does the jacket run small? What size should I get")[0] == "sizing"
    assert classify("How do I return these for an exchange?")[0] == "returns"
    assert classify("Hello there") == (None, 0.0)


def test_escalation_rules() -> None:
    big = Order(total_minor=25_000, currency="USD")
    assert "customer is upset" in escalation_reasons(
        "THIS IS UNACCEPTABLE, WHERE IS IT", "order_status", 0.9, None
    )
    assert "legal or chargeback language" in escalation_reasons(
        "I'm disputing the charge", "order_status", 0.9, None
    )
    assert escalation_reasons("I want a refund please, return it", "returns", 0.9, big) == [
        "refund request above 100 USD"
    ]
    assert escalation_reasons("hi", None, 0.0, None) == ["not confident about what they need"]
    assert escalation_reasons("where is my order", "order_status", 0.9, None) == []


def test_review_sentiment_and_themes() -> None:
    assert sentiment(5, "Love it, warm and comfortable")[1] == "positive"
    assert sentiment(1, "Zipper broke, seam leaked")[1] == "negative"
    assert sentiment(3, "Okay")[1] == "neutral"
    assert themes("Arrived late and the zipper broke") == [
        "Shipping & delivery",
        "Quality & defects",
    ]


def test_round_price() -> None:
    assert round_price(18_733) == 18_700
    assert round_price(26_910) == 26_900
    assert round_price(10) == 100


# ---- helpers ----------------------------------------------------------------------------------


async def _shop(db: AsyncSession, data: dict[str, str]) -> Shop:
    shop = await db.get(Shop, uuid.UUID(data["id"]))
    assert shop is not None
    return shop


async def _store(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, shop: Shop
) -> DemoStore:
    return await DemoStore(shop, db, redis, queue, rng=random.Random(11)).load()


async def _email(store: DemoStore, subject: str, body: str, order: str | None = None) -> Ticket:
    await store.emit(
        "support/message",
        {
            "id": uuid.uuid4().hex,
            "from_email": "jules@customers.example",
            "first_name": "Jules",
            "subject": subject,
            "body": body,
            "order_name": order,
        },
    )
    ticket = await store.db.scalar(
        select(Ticket)
        .where(Ticket.shop_id == store.shop.id, Ticket.subject == subject)
        .execution_options(populate_existing=True)
    )
    assert ticket is not None
    return ticket


# ---- Support ----------------------------------------------------------------------------------


async def test_support_answers_order_status_with_tracking(
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    store = await _store(db, redis, queue, shop)
    name = await store.place_order(await store.random_customer(), [Line(store.stock[0], 1)])
    order = await db.scalar(select(Order).where(Order.shop_id == shop.id, Order.name == name))
    assert order is not None
    await store.emit(
        "fulfillments/create",
        {
            "order_id": order.shopify_id,
            "status": "success",
            "tracking_number": "1Z999",
            "tracking_url": "https://t.example/1Z999",
        },
    )
    ticket = await _email(store, "Where is my order?", f"Hi, any tracking for {name}?", order=name)
    assert ticket.status == TicketStatus.RESOLVED

    reply = await db.scalar(
        select(Message).where(
            Message.ticket_id == ticket.id, Message.direction == MessageDirection.OUTBOUND
        )
    )
    assert reply is not None
    assert reply.author_type == MessageAuthor.AGENT
    assert "1Z999" in reply.body
    assert f"order {name} has shipped" in reply.body
    outbox = [json.loads(x) for x in await redis.lrange(outbox_key(shop.id), 0, -1)]  # type: ignore[misc]
    assert outbox[0]["category"] == "support"

    detail = (await owner.get(f"/api/shops/{shop.id}/tickets/{ticket.id}")).json()
    assert detail["order"]["tracking_number"] == "1Z999"
    assert [m["author_type"] for m in detail["messages"]] == ["customer", "agent"]


async def test_support_escalates_angry_customer_to_handover_queue(
    app: FastAPI,
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    store = await _store(db, redis, queue, shop)
    ticket = await _email(
        store, "Worst service ever", "This is unacceptable, I will file a chargeback!!"
    )
    assert ticket.status == TicketStatus.ESCALATED
    assert ticket.priority == 1
    assert (
        await db.scalar(select(ActionProposal).where(ActionProposal.target_id == str(ticket.id)))
        is None
    )
    alert = await db.scalar(select(Notification).where(Notification.kind == "support.escalated"))
    assert alert is not None

    base = f"/api/shops/{shop.id}/tickets"
    handover = (await owner.get(f"{base}?queue=handover")).json()
    assert [t["id"] for t in handover["items"]] == [str(ticket.id)]
    assert handover["counts"]["handover"] == 1

    viewer = await signup(app, "support-viewer@northbound.example")
    await owner.post(
        f"/api/shops/{shop.id}/members", json={"email": "support-viewer@northbound.example"}
    )
    assert (await viewer.post(f"{base}/{ticket.id}/reply", json={"text": "hi"})).status_code == 403
    await viewer.client.aclose()

    sent = await owner.post(
        f"{base}/{ticket.id}/reply", json={"text": "So sorry, refunding now.", "resolve": True}
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "resolved"
    detail = (await owner.get(f"{base}/{ticket.id}")).json()
    assert detail["messages"][-1]["author_type"] == "human"
    assert (await owner.get(f"{base}?queue=handover")).json()["items"] == []


async def test_support_returns_question_waits_for_customer(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    store = await _store(db, redis, queue, shop)
    ticket = await _email(store, "Exchange", "Can I exchange the trail runners for a bigger pair?")
    assert ticket.status == TicketStatus.PENDING


async def test_support_in_suggest_mode_drafts_for_approval(
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    await owner.patch(f"/api/shops/{shop.id}/agents/support", json={"autonomy": "suggest"})
    store = await _store(db, redis, queue, shop)
    ticket = await _email(store, "Sizing question", "Does the down jacket run true to size?")
    assert ticket.status == TicketStatus.OPEN
    detail = (await owner.get(f"/api/shops/{shop.id}/tickets/{ticket.id}")).json()
    assert detail["draft"] is not None
    assert "true to size" in detail["draft"]["text"]


# ---- Review & Reputation ------------------------------------------------------------------------


async def test_reviews_auto_reply_and_escalate_unhappy(
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    store = await _store(db, redis, queue, shop)
    await store.review(rating=5, text=("Love it", "Warm and comfortable on the ridge."))
    await store.review(rating=1, text=("Seam leaked", "Water came through the seam. Disappointed."))

    happy = await db.scalar(select(Review).where(Review.shop_id == shop.id, Review.rating == 5))
    unhappy = await db.scalar(select(Review).where(Review.shop_id == shop.id, Review.rating == 1))
    assert happy is not None
    assert unhappy is not None
    await db.refresh(happy)
    await db.refresh(unhappy)
    assert happy.sentiment_label == "positive"
    assert happy.replied_at is not None
    assert unhappy.sentiment_label == "negative"
    assert unhappy.replied_at is None

    proposal = await db.scalar(
        select(ActionProposal).where(ActionProposal.target_id == str(unhappy.id))
    )
    assert proposal is not None
    assert proposal.status == ProposalStatus.PROPOSED
    assert "Quality & defects" in proposal.rationale
    ticket = await db.scalar(select(Ticket).where(Ticket.review_id == unhappy.id))
    assert ticket is not None
    assert ticket.channel == TicketChannel.REVIEW

    approved = await owner.post(
        f"/api/shops/{shop.id}/proposals/{proposal.id}/approve",
        json={"edits": {"reply": "We're sorry. Our team will reach out today."}},
    )
    assert approved.json()["status"] == "executed"
    await db.refresh(unhappy)
    assert unhappy.reply_draft == "We're sorry. Our team will reach out today."

    insights = (await owner.get(f"/api/shops/{shop.id}/insights")).json()
    assert insights["reviews"]["reviews"] == 2
    assert insights["reviews"]["top_complaints"][0]["theme"] == "Quality & defects"
    assert insights["sentiment"] == {"positive": 1, "negative": 1}


# ---- Revenue Analyst ----------------------------------------------------------------------------


async def test_revenue_analyst_flags_spike_with_likely_cause(
    owner: Session, db: AsyncSession, redis: fakeredis.FakeAsyncRedis
) -> None:
    user = await db.get(User, uuid.UUID(owner.user_id))
    assert user is not None
    shop = await create_shop(
        db, owner=user, name="Pulse", domain="pulse.example", mode=ShopMode.LIVE
    )
    now = utcnow()
    for i in range(4):
        db.add(
            Order(
                shop_id=shop.id,
                shopify_id=i,
                name=f"#{i}",
                currency="USD",
                total_minor=10_000,
                source_name="web",
                processed_at=now - timedelta(days=7, minutes=i + 1),
            )
        )
    for i in range(10):
        db.add(
            Order(
                shop_id=shop.id,
                shopify_id=100 + i,
                name=f"#{100 + i}",
                currency="USD",
                total_minor=10_000,
                source_name="instagram" if i < 8 else "web",
                processed_at=now - timedelta(minutes=i + 1),
            )
        )
    await db.commit()

    run = await run_agent(db, redis, shop, AgentName.REVENUE_ANALYST, Change("order.created", {}))
    assert run is not None
    assert run.output is not None
    assert run.output["anomaly"] == "up"
    assert "instagram" in run.output["cause"]
    note = await db.scalar(select(Notification).where(Notification.kind == "insight.anomaly"))
    assert note is not None
    assert note.title.startswith("Revenue is up 150% vs the same time last week")

    # Throttled: orders keep arriving but the analyst only re-evaluates every few minutes.
    assert (
        await run_agent(db, redis, shop, AgentName.REVENUE_ANALYST, Change("order.created", {}))
        is None
    )
    insights = (await owner.get(f"/api/shops/{shop.id}/insights")).json()
    assert insights["anomalies"][0]["direction"] == "up"


async def test_revenue_analyst_needs_history(
    owner: Session, db: AsyncSession, redis: fakeredis.FakeAsyncRedis
) -> None:
    user = await db.get(User, uuid.UUID(owner.user_id))
    assert user is not None
    shop = await create_shop(db, owner=user, name="New", domain="new.example", mode=ShopMode.LIVE)
    await db.commit()
    run = await run_agent(db, redis, shop, AgentName.REVENUE_ANALYST, Change("order.created", {}))
    assert run is not None
    assert run.output is not None
    assert run.output["skipped"] == "not enough history for this time of day"


# ---- Pricing Advisor ----------------------------------------------------------------------------


async def test_pricing_advisor_suggests_increase_for_fast_mover(
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    store = await _store(db, redis, queue, shop)
    item = next(i for i in store.stock if i.sku == "NB-SOCK-M")
    await store.place_order(await store.random_customer(), [Line(item, item.quantity - 4)])

    proposal = await db.scalar(
        select(ActionProposal).where(
            ActionProposal.shop_id == shop.id, ActionProposal.action_type == "change_price"
        )
    )
    assert proposal is not None
    assert proposal.status == ProposalStatus.PROPOSED
    assert proposal.risk_level.value == "high"
    assert proposal.payload["to_minor"] > proposal.payload["from_minor"]
    assert "Demand is outpacing supply" in proposal.rationale

    below_floor = await owner.post(
        f"/api/shops/{shop.id}/proposals/{proposal.id}/approve", json={"edits": {"to_minor": 100}}
    )
    assert below_floor.json()["status"] == "failed"
    assert "margin floor" in below_floor.json()["error"]


async def test_pricing_advisor_marks_down_slow_mover_above_floor(
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    variant = await db.scalar(
        select(Variant).where(Variant.shop_id == shop.id, Variant.sku == "NB-BAG-LNG")
    )
    assert variant is not None
    store = await _store(db, redis, queue, shop)
    await store.emit(
        "inventory_levels/update",
        {"inventory_item_id": variant.inventory_item_id, "available": 600},
    )
    proposal = await db.scalar(
        select(ActionProposal).where(
            ActionProposal.shop_id == shop.id,
            ActionProposal.target_id == str(variant.id),
            ActionProposal.action_type == "change_price",
        )
    )
    assert proposal is not None
    assert proposal.payload["to_minor"] == 26_900
    assert proposal.payload["to_minor"] >= proposal.payload["floor_minor"]

    approved = await owner.post(f"/api/shops/{shop.id}/proposals/{proposal.id}/approve")
    assert approved.json()["status"] == "executed"
    await db.refresh(variant)
    assert variant.price_minor == 26_900


async def test_revenue_cause_follows_direction(owner: Session, db: AsyncSession) -> None:
    from app.agents.revenue import likely_cause

    user = await db.get(User, uuid.UUID(owner.user_id))
    assert user is not None
    shop = await create_shop(
        db, owner=user, name="Promo", domain="promo.example", mode=ShopMode.LIVE
    )
    now = utcnow()

    def order(source: str, discount: int = 0) -> Order:
        return Order(
            shop_id=shop.id,
            name="#1",
            currency="USD",
            total_minor=10_000,
            discount_minor=discount,
            refunded_minor=0,
            source_name=source,
            processed_at=now,
        )

    baseline = [order("google"), order("google"), order("web")]
    promo = [order("web", discount=3_000) for _ in range(4)]
    assert "discount" in await likely_cause(db, shop, now, promo, baseline, "up")
    assert "drop in google" in await likely_cause(
        db, shop, now, [order("web")] * 3, baseline, "down"
    )
    assert "surge in web" in await likely_cause(db, shop, now, [order("web")] * 3, baseline, "up")

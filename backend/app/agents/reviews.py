"""Review & Reputation: sentiment, reply drafts, tickets for unhappy reviewers, weekly themes."""

import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext, Decision, ProposalDraft
from app.models import Product, Review, Shop, Ticket
from app.models.enums import AgentName, RiskLevel, TicketChannel, TicketStatus
from app.pipeline.normalize import Change

PROMPT_VERSION = "reviews-v1"

POSITIVE = (
    "love", "great", "perfect", "comfortable", "dry", "warm", "worth", "solid", "favourite",
    "favorite", "excellent", "amazing", "quality",
)  # fmt: skip
NEGATIVE = (
    "broke", "broken", "leak", "leaked", "late", "crushed", "disappointed", "refund", "cheap",
    "unacceptable", "stiff", "small", "never", "wet", "tear", "ripped", "defective", "worst",
)  # fmt: skip
THEMES: dict[str, tuple[str, ...]] = {
    "Shipping & delivery": ("late", "shipping", "arrived", "delivery", "crushed", "box", "weeks"),
    "Sizing & fit": ("small", "large", "size", "fit", "tight", "loose"),
    "Quality & defects": (
        "broke",
        "broken",
        "leak",
        "seam",
        "zipper",
        "tear",
        "ripped",
        "defective",
    ),
    "Customer service": ("support", "answer", "respond", "reply", "service"),
    "Price & value": ("price", "expensive", "value", "overpriced"),
}


class ReplyDraft(BaseModel):
    reply: str = Field(max_length=800)


def sentiment(rating: int, text: str) -> tuple[float, str]:
    lowered = text.lower()
    lexical = sum(w in lowered for w in POSITIVE) - sum(w in lowered for w in NEGATIVE)
    score = max(-1.0, min(1.0, (rating - 3) / 2 * 0.7 + max(-3, min(3, lexical)) / 3 * 0.3))
    label = "positive" if score > 0.25 else "negative" if score < -0.25 else "neutral"
    return round(score, 2), label


def themes(text: str) -> list[str]:
    lowered = text.lower()
    return [theme for theme, words in THEMES.items() if any(w in lowered for w in words)]


def draft_reply(rating: int, author: str | None, product: str | None, sender: str) -> str:
    name = (author or "there").split(" ")[0]
    item = product or "your gear"
    if rating >= 4:
        return (
            f"Thanks so much, {name}! We're thrilled the {item} is working out for you. "
            f"See you on the trail. — {sender}"
        )
    if rating == 3:
        return (
            f"Thanks for the honest feedback, {name}. We'd love to make the {item} work better "
            f"for you; our team will reach out with options. — {sender}"
        )
    return (
        f"{name}, we're sorry the {item} let you down. That's not the experience we want for "
        f"you. Our support team has opened a ticket and will contact you today to make it "
        f"right. — {sender}"
    )


class ReviewReputation:
    name: ClassVar[AgentName] = AgentName.REVIEW_REPUTATION
    triggers: ClassVar[frozenset[str]] = frozenset({"review.created"})

    async def decide(self, ctx: AgentContext, change: Change) -> Decision | None:
        review = await ctx.db.get(Review, uuid.UUID(change.data["id"]))
        if review is None:
            return None
        product = await ctx.db.get(Product, review.product_id) if review.product_id else None
        text = f"{review.title or ''} {review.body}"
        score, label = sentiment(review.rating, text)
        sender = ctx.settings.sender_name or ctx.shop.name
        reply = draft_reply(
            review.rating, review.author_name, product.title if product else None, sender
        )
        decision = Decision(
            output={
                "review_id": str(review.id),
                "rating": review.rating,
                "sentiment_score": score,
                "sentiment_label": label,
                "themes": themes(text),
            }
        )
        if ctx.llm is not None:
            result = await ctx.llm.complete_json(
                agent=self.name,
                system=(
                    f"You reply publicly to product reviews for {sender}. Be warm, specific and "
                    "brief (max 3 sentences). Never offer refunds or discounts publicly."
                ),
                user=f"{review.rating}-star review of {product.title if product else 'a product'}: "
                f"{text}\nDraft: {reply}",
                schema=ReplyDraft,
                prompt_version=PROMPT_VERSION,
                model=ctx.config.model,
                agent_run_id=ctx.run_id,
            )
            if result is None:
                decision.used_fallback = True
            else:
                reply = result.value.reply
                decision.model, decision.prompt_version = result.model, PROMPT_VERSION
                decision.cost_usd = float(result.cost_usd)
        decision.output["reply_draft"] = reply
        unhappy = review.rating <= 2
        decision.proposals.append(
            ProposalDraft(
                action_type="reply_to_review",
                title=f"Reply to {review.rating}★ review by {review.author_name or 'a customer'}",
                summary=reply,
                rationale=f"Sentiment {label} ({score:+.2f})"
                + (
                    f"; themes: {', '.join(decision.output['themes'])}."
                    if decision.output["themes"]
                    else "."
                )
                + (" Unhappy reviews always get a human check before posting." if unhappy else ""),
                risk_level=RiskLevel.MEDIUM if unhappy else RiskLevel.LOW,
                payload={"review_id": str(review.id), "reply": reply},
                preview={
                    "review": {"rating": review.rating, "title": review.title, "body": review.body}
                },
                target_type="review",
                target_id=str(review.id),
                always_requires_approval=unhappy,
                expires_in=timedelta(days=3),
            )
        )
        return decision

    async def after_decide(self, ctx: AgentContext, change: Change, decision: Decision) -> None:
        review = await ctx.db.get(Review, uuid.UUID(decision.output["review_id"]))
        if review is None:
            return
        review.sentiment_score = decision.output["sentiment_score"]
        review.sentiment_label = decision.output["sentiment_label"]
        review.reply_draft = decision.output["reply_draft"]
        if review.rating <= 2:
            ctx.db.add(
                Ticket(
                    shop_id=ctx.shop.id,
                    channel=TicketChannel.REVIEW,
                    status=TicketStatus.OPEN,
                    subject=f"{review.rating}★ review: {review.title or review.body[:60]}",
                    review_id=review.id,
                    priority=1,
                    last_message_at=review.published_at,
                )
            )
        await ctx.db.flush()


async def weekly_summary(db: AsyncSession, shop: Shop, now: datetime) -> dict[str, Any]:
    reviews = (
        await db.scalars(
            select(Review).where(
                Review.shop_id == shop.id, Review.published_at >= now - timedelta(days=7)
            )
        )
    ).all()
    complaints: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for review in reviews:
        if review.rating <= 3:
            for theme in themes(f"{review.title or ''} {review.body}"):
                complaints[theme] += 1
                examples.setdefault(theme, review.title or review.body[:80])
    ratings = [r.rating for r in reviews]
    return {
        "reviews": len(reviews),
        "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "negative": sum(1 for r in ratings if r <= 2),
        "top_complaints": [
            {"theme": theme, "count": count, "example": examples[theme]}
            for theme, count in complaints.most_common(3)
        ],
    }

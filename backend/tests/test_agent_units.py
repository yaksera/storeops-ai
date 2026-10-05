import uuid
from datetime import UTC, date, datetime, time, timedelta

from app.agents.forecast import forecast, weekday_factors
from app.agents.fraud import explain, score_order
from app.agents.guardrails import evaluate, in_quiet_hours
from app.models import AgentConfig, Customer, Order, OrderItem, Shop, ShopSettings
from app.models.enums import AgentName

TODAY = date(2026, 10, 5)  # a Monday


def test_forecast_days_to_stockout_and_order_quantity() -> None:
    history = {TODAY - timedelta(days=i): 2 for i in range(1, 91)}
    fc = forecast(on_hand=20, history=history, today=TODAY, lead_time_days=10)
    assert fc.daily_rate_30d == 2.0
    assert fc.days_to_stockout == 10.0
    # 2/day * (10 lead + 5 safety) = 30
    assert fc.reorder_point == 30
    # 2/day * (10 lead + 30 cover) - 20 on hand
    assert fc.suggested_order_qty == 60


def test_forecast_without_sales_never_stocks_out() -> None:
    fc = forecast(on_hand=5, history={}, today=TODAY, lead_time_days=10, reorder_point=3)
    assert fc.days_to_stockout is None
    assert fc.suggested_order_qty == 0
    assert fc.reorder_point == 3


def test_forecast_out_of_stock_is_zero_days() -> None:
    history = {TODAY - timedelta(days=1): 4}
    assert forecast(on_hand=0, history=history, today=TODAY, lead_time_days=5).days_to_stockout == 0


def test_weekday_factors_capture_weekend_peaks() -> None:
    start = TODAY - timedelta(days=28)
    history = {
        start + timedelta(days=i): (6 if (start + timedelta(days=i)).weekday() >= 5 else 2)
        for i in range(28)
    }
    factors = weekday_factors(history, start, TODAY - timedelta(days=1))
    assert factors[5] > 1 > factors[0]
    assert abs(sum(factors) / 7 - 1) < 0.01


def _order(**kwargs: object) -> Order:
    defaults: dict[str, object] = {
        "name": "#1",
        "currency": "USD",
        "total_minor": 10_000,
        "financial_status": "paid",
        "billing_country": "US",
        "shipping_country": "US",
        "email": "kai@customers.example",
    }
    return Order(**(defaults | kwargs))


def test_clean_order_scores_zero() -> None:
    score, factors = score_order(_order(), [OrderItem(title="Socks", quantity=1)], None, 0)
    assert score == 0
    assert factors == []
    assert explain(score, factors) == "Scored 0/100: nothing unusual about this order."


def test_suspicious_order_scores_high_with_reasons() -> None:
    order = _order(
        billing_country="NG",
        shipping_country="US",
        financial_status="pending",
        total_minor=119_600,
        email="x1@quickmail.example",
    )
    items = [OrderItem(title="Polar Sleeping Bag", quantity=4)]
    score, factors = score_order(order, items, Customer(orders_count=1), recent_orders=2)
    codes = {f["code"] for f in factors}
    assert codes == {
        "address_mismatch",
        "high_risk_country",
        "payment_pending",
        "unusual_quantity",
        "new_high_value",
        "disposable_email",
        "velocity",
    }
    assert score == 100
    text = explain(score, factors)
    assert text.startswith("Scored 100/100: billed in NG but shipping to US")
    assert "4x Polar Sleeping Bag" in text


def _guard_inputs() -> tuple[Shop, ShopSettings, AgentConfig]:
    shop = Shop(id=uuid.uuid4(), name="S", domain="s.example", timezone="UTC")
    settings = ShopSettings(
        kill_switch=False,
        max_discount_pct=10,
        refund_ceiling_minor=10_000,
        physical_address="1 Main St",
    )
    config = AgentConfig(agent=AgentName.CART_RECOVERY, daily_action_cap=5)
    return shop, settings, config


NOON = datetime(2026, 10, 5, 12, tzinfo=UTC)


def test_guardrails_pass_for_consenting_customer() -> None:
    shop, settings, config = _guard_inputs()
    result = evaluate(
        shop=shop,
        settings=settings,
        config=config,
        payload={"discount": {"pct": 10}},
        customer_facing=True,
        customer=Customer(accepts_marketing=True),
        executed_today=4,
        now=NOON,
    )
    assert result.ok
    assert result.escalate == []


def test_guardrails_block_and_escalate() -> None:
    shop, settings, config = _guard_inputs()
    settings.kill_switch = True
    settings.physical_address = None
    settings.quiet_hours_start, settings.quiet_hours_end = time(11), time(13)
    result = evaluate(
        shop=shop,
        settings=settings,
        config=config,
        payload={"discount": {"pct": 15}, "refund_minor": 20_000},
        customer_facing=True,
        customer=Customer(accepts_marketing=False),
        executed_today=5,
        now=NOON,
    )
    assert result.blocked == [
        "Kill switch is on",
        "Daily cap of 5 actions reached",
        "Discount 15% exceeds the 10% limit",
        "Customer hasn't consented to marketing email",
        "Set a physical mailing address before sending marketing email",
    ]
    assert result.escalate == [
        "Refund above the auto-approval ceiling",
        "Quiet hours: needs a human to send now",
    ]


def test_unsubscribed_customer_is_blocked() -> None:
    shop, settings, config = _guard_inputs()
    result = evaluate(
        shop=shop,
        settings=settings,
        config=config,
        payload={},
        customer_facing=True,
        customer=Customer(accepts_marketing=True, unsubscribed_at=NOON),
        executed_today=0,
        now=NOON,
    )
    assert result.blocked == ["Customer has unsubscribed"]


def test_quiet_hours_wrap_past_midnight() -> None:
    shop, settings, _ = _guard_inputs()
    shop.timezone = "America/Denver"
    settings.quiet_hours_start, settings.quiet_hours_end = time(21), time(7)
    late = datetime(2026, 10, 6, 5, tzinfo=UTC)  # 23:00 in Denver
    morning = datetime(2026, 10, 6, 15, tzinfo=UTC)  # 09:00 in Denver
    assert in_quiet_hours(settings, shop, late)
    assert not in_quiet_hours(settings, shop, morning)

"""Demand forecasting for the Inventory Planner: moving average with weekday seasonality."""

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class Forecast:
    daily_rate_30d: float
    daily_rate_90d: float
    weekday_factors: tuple[float, ...]
    days_to_stockout: float | None
    reorder_point: int
    suggested_order_qty: int


def weekday_factors(history: dict[date, int], start: date, end: date) -> tuple[float, ...]:
    """Relative demand per weekday (Mon..Sun), normalised so the mean factor is 1."""
    totals = [0.0] * 7
    counts = [0] * 7
    day = start
    while day <= end:
        totals[day.weekday()] += history.get(day, 0)
        counts[day.weekday()] += 1
        day += timedelta(days=1)
    averages = [totals[i] / counts[i] if counts[i] else 0.0 for i in range(7)]
    mean = sum(averages) / 7
    if mean <= 0:
        return (1.0,) * 7
    # Shrink towards 1 so sparse history doesn't produce extreme factors.
    return tuple(round(0.5 + 0.5 * (avg / mean), 3) for avg in averages)


def forecast(
    *,
    on_hand: int,
    history: dict[date, int],
    today: date,
    lead_time_days: int,
    reorder_point: int | None = None,
    cover_days: int = 30,
    horizon_days: int = 365,
) -> Forecast:
    """`history` maps past dates to units sold. Today is excluded (it's partial)."""
    last_30 = [history.get(today - timedelta(days=i), 0) for i in range(1, 31)]
    last_90 = [history.get(today - timedelta(days=i), 0) for i in range(1, 91)]
    rate_30 = sum(last_30) / 30
    rate_90 = sum(last_90) / 90
    # Recent demand matters most, but blend in the longer window for stability.
    rate = 0.7 * rate_30 + 0.3 * rate_90 if rate_90 else rate_30
    factors = weekday_factors(history, today - timedelta(days=90), today - timedelta(days=1))

    days_to_stockout: float | None = None
    if rate > 0:
        remaining = float(on_hand)
        if remaining <= 0:
            days_to_stockout = 0.0
        else:
            for offset in range(horizon_days):
                demand = rate * factors[(today + timedelta(days=offset)).weekday()]
                if demand >= remaining:
                    days_to_stockout = round(offset + remaining / demand, 1)
                    break
                remaining -= demand

    safety_days = max(3, lead_time_days // 2)
    computed_rop = round(rate * (lead_time_days + safety_days))
    rop = reorder_point if reorder_point is not None else computed_rop
    target = rate * (lead_time_days + cover_days)
    suggested = max(0, round(target - on_hand))
    return Forecast(
        daily_rate_30d=round(rate_30, 2),
        daily_rate_90d=round(rate_90, 2),
        weekday_factors=factors,
        days_to_stockout=days_to_stockout,
        reorder_point=rop,
        suggested_order_qty=suggested,
    )

"""Northbound Outdoor Gear: the fictional brand behind demo mode."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DemoVariant:
    sku: str
    title: str
    price_minor: int
    cost_minor: int
    stock: int
    reorder_point: int
    popularity: float


@dataclass(frozen=True, slots=True)
class DemoProduct:
    handle: str
    title: str
    product_type: str
    variants: tuple[DemoVariant, ...]


def _sizes(
    prefix: str,
    price: int,
    cost: int,
    stock: int,
    reorder: int,
    popularity: float,
    sizes: tuple[str, ...] = ("S", "M", "L", "XL"),
) -> tuple[DemoVariant, ...]:
    weights = {"XS": 0.5, "S": 0.8, "M": 1.2, "L": 1.1, "XL": 0.7}
    return tuple(
        DemoVariant(
            sku=f"{prefix}-{size}",
            title=size,
            price_minor=price,
            cost_minor=cost,
            stock=stock,
            reorder_point=reorder,
            popularity=popularity * weights.get(size, 1.0),
        )
        for size in sizes
    )


CATALOG: tuple[DemoProduct, ...] = (
    DemoProduct(
        "summit-3-season-tent",
        "Summit 3-Season Tent",
        "Shelter",
        (
            DemoVariant("NB-TENT-2P", "2-Person", 32900, 14800, 38, 12, 0.9),
            DemoVariant("NB-TENT-3P", "3-Person", 38900, 17600, 24, 10, 0.6),
        ),
    ),
    DemoProduct(
        "ridgeline-down-jacket",
        "Ridgeline Down Jacket",
        "Apparel",
        _sizes("NB-RDJ", 24900, 9800, 30, 10, 1.4),
    ),
    DemoProduct(
        "trailhead-softshell",
        "Trailhead Softshell",
        "Apparel",
        _sizes("NB-TSS", 15900, 6200, 34, 10, 1.0),
    ),
    DemoProduct(
        "basecamp-45l-pack",
        "Basecamp 45L Pack",
        "Packs",
        (
            DemoVariant("NB-PACK-45-SLT", "Slate", 18900, 7400, 42, 14, 1.2),
            DemoVariant("NB-PACK-45-MOS", "Moss", 18900, 7400, 26, 12, 0.9),
        ),
    ),
    DemoProduct(
        "alpine-trekking-poles",
        "Alpine Carbon Trekking Poles",
        "Gear",
        (DemoVariant("NB-POLE-CARB", "Pair", 12900, 4300, 55, 15, 1.1),),
    ),
    DemoProduct(
        "merino-hiking-socks",
        "Merino Hiking Socks (3-pack)",
        "Apparel",
        _sizes("NB-SOCK", 3900, 1100, 120, 40, 2.6, ("S", "M", "L")),
    ),
    DemoProduct(
        "glacier-insulated-bottle",
        "Glacier Insulated Bottle 1L",
        "Gear",
        (
            DemoVariant("NB-BTL-1L-ICE", "Ice", 3400, 900, 90, 30, 1.8),
            DemoVariant("NB-BTL-1L-EMB", "Ember", 3400, 900, 64, 30, 1.3),
        ),
    ),
    DemoProduct(
        "headlamp-400",
        "Lumen 400 Headlamp",
        "Gear",
        (DemoVariant("NB-HL-400", "Default", 4900, 1600, 70, 20, 1.5),),
    ),
    DemoProduct(
        "polar-sleeping-bag",
        "Polar -10°C Sleeping Bag",
        "Sleep",
        (
            DemoVariant("NB-BAG-REG", "Regular", 27900, 11900, 18, 8, 0.7),
            DemoVariant("NB-BAG-LNG", "Long", 29900, 12800, 9, 6, 0.4),
        ),
    ),
    DemoProduct(
        "trail-runner-shoe",
        "Switchback Trail Runner",
        "Footwear",
        _sizes("NB-TRS", 14900, 5900, 22, 8, 1.3, ("8", "9", "10", "11")),
    ),
    DemoProduct(
        "camp-stove-kit",
        "Ember Camp Stove Kit",
        "Cooking",
        (DemoVariant("NB-STOVE-KIT", "Default", 8900, 3100, 31, 10, 0.8),),
    ),
    DemoProduct(
        "packable-rain-shell",
        "Cloudburst Packable Rain Shell",
        "Apparel",
        _sizes("NB-RAIN", 12900, 4700, 28, 10, 1.0),
    ),
)

FIRST_NAMES = (
    "Avery", "Jordan", "Riley", "Quinn", "Harper", "Rowan", "Sage", "Emerson", "Kai", "Reese",
    "Morgan", "Parker", "Skyler", "Dakota", "Hayden", "Finley", "Logan", "Elliot", "Remy", "Blake",
    "Noa", "Mika", "Ari", "Jules", "Sasha", "Taylor", "Casey", "Drew", "Lane", "Robin",
)  # fmt: skip

# (country code, weight). Orders are mostly domestic, as for a US outdoor brand.
COUNTRIES = (("US", 0.78), ("CA", 0.12), ("GB", 0.04), ("DE", 0.03), ("AU", 0.03))
HIGH_RISK_COUNTRIES = ("NG", "RU", "VN", "ID", "BR")

SOURCES = (("web", 0.62), ("instagram", 0.14), ("google", 0.16), ("email", 0.08))

POSITIVE_REVIEWS = (
    ("Bombproof on the ridge", "Took it through a nasty storm above treeline. Stayed bone dry."),
    ("Worth every penny", "Light, warm and packs down tiny. Already ordered one for my partner."),
    ("Great fit", "True to size and comfortable on a 14-mile day."),
    ("Solid gear", "Quality stitching, quick shipping. Would buy again."),
    ("Love it", "My new favourite piece of kit for weekend trips."),
)
NEUTRAL_REVIEWS = (
    ("Good, not great", "Does the job but runs a little small. Size up."),
    ("Okay for the price", "Decent quality, the zipper feels a bit stiff."),
)
NEGATIVE_REVIEWS = (
    ("Seam leaked", "Water came through the seam on the first rainy night. Disappointed."),
    ("Arrived late", "Took almost two weeks to arrive and the box was crushed."),
)
ANGRY_REVIEW = (
    "Absolutely unacceptable",
    "Zipper broke on day two of a week-long trip and support hasn't answered in 5 days. "
    "I want a full refund or I'm disputing the charge with my bank. Never buying again.",
)

SUPPORT_TEMPLATES = (
    ("Where is my order?", "Hi, I ordered {order} last week and haven't seen any tracking yet."),
    ("Sizing question", "Does the Ridgeline Down Jacket run true to size? I'm usually a medium."),
    ("Return request", "The trail runners are a bit tight. How do I exchange them for a size up?"),
    ("Order change", "Can I change the colour on {order}? I meant to pick Moss."),
)
SHIPPING_DELAY_TEMPLATE = (
    "Still waiting on {order}",
    "Tracking for {order} hasn't moved in 4 days. I need it before my trip on Saturday. "
    "What's going on?",
)

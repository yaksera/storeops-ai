from enum import StrEnum


class ShopMode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class ShopStatus(StrEnum):
    ACTIVE = "active"
    UNINSTALLED = "uninstalled"


class Plan(StrEnum):
    FREE = "free"
    GROWTH = "growth"
    PRO = "pro"


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return {Role.VIEWER: 0, Role.ADMIN: 1, Role.OWNER: 2}[self]


class AgentName(StrEnum):
    ORCHESTRATOR = "orchestrator"
    FRAUD_GUARD = "fraud_guard"
    INVENTORY_PLANNER = "inventory_planner"
    CART_RECOVERY = "cart_recovery"
    SUPPORT = "support"
    REVIEW_REPUTATION = "review_reputation"
    REVENUE_ANALYST = "revenue_analyst"
    PRICING_ADVISOR = "pricing_advisor"


class Autonomy(StrEnum):
    OFF = "off"
    SUGGEST = "suggest"
    AUTO = "auto"


class EventSource(StrEnum):
    SHOPIFY = "shopify"
    SIMULATOR = "simulator"
    RECONCILIATION = "reconciliation"


class WebhookStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CheckoutStatus(StrEnum):
    OPEN = "open"
    ABANDONED = "abandoned"
    RECOVERED = "recovered"
    COMPLETED = "completed"


class ReviewSource(StrEnum):
    SHOPIFY = "shopify"
    JUDGEME = "judgeme"
    GOOGLE = "google"
    SIMULATOR = "simulator"


class TicketChannel(StrEnum):
    EMAIL = "email"
    CHAT = "chat"
    REVIEW = "review"


class TicketStatus(StrEnum):
    OPEN = "open"
    PENDING = "pending"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageAuthor(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    HUMAN = "human"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ActorType(StrEnum):
    SYSTEM = "system"
    AGENT = "agent"
    USER = "user"


class NotificationChannel(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    SMS = "sms"
    WHATSAPP = "whatsapp"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class LlmCallStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"


class SubscriptionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    FROZEN = "frozen"
    CANCELLED = "cancelled"


class BillingProvider(StrEnum):
    NONE = "none"
    SHOPIFY = "shopify"
    STRIPE = "stripe"

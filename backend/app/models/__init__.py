"""ORM models.

Every model must be imported here. Alembic's autogenerate walks
``Base.metadata``, which is only populated as a side effect of importing the
module that defines the model — a model that is never imported is silently
omitted from migrations.
"""

from app.models.ai_prompt import AIPrompt, PromptExecution, PromptExecutionStatus
from app.models.analytics import AnalyticsDaily
from app.models.automation import (
    AutomationAction,
    AutomationRule,
    AutomationRun,
    AutomationSchedule,
)
from app.models.base import (
    Base,
    IdentifiedBase,
    ReferenceBase,
    SoftDeleteMixin,
    TenantMixin,
    TenantScopedBase,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.email_verification import EmailVerificationToken
from app.models.integration import AliExpressConnection, IntegrationStatus
from app.models.inventory import (
    InventoryChange,
    InventoryChangeReason,
    InventorySyncRun,
)
from app.models.notification import Notification, NotificationKind
from app.models.order import (
    FulfillmentStatus,
    Order,
    OrderEvent,
    OrderEventType,
    OrderItem,
    OrderSource,
    OrderSyncRun,
    PaymentStatus,
    Shipment,
    ShipmentStatus,
    SyncRunStatus,
    SyncTrigger,
    TrackingEvent,
)
from app.models.pricing import (
    PriceChange,
    PricingRule,
    PricingScope,
    PricingStrategy,
)
from app.models.product import (
    ImportStatus,
    Product,
    ProductAIStatus,
    ProductImage,
    ProductImport,
    ProductSource,
    ProductStatus,
    ProductVariant,
    ProductVersion,
    ProductVersionSource,
)
from app.models.refresh_token import RefreshToken
from app.models.role import Role, RoleName, UserRole
from app.models.rule_application import (
    ApplicationItemOutcome as ApplicationItemOutcome,
)
from app.models.rule_application import (
    ApplicationStatus as ApplicationStatus,
)
from app.models.rule_application import (
    RuleApplication as RuleApplication,
)
from app.models.rule_application import (
    RuleApplicationItem as RuleApplicationItem,
)
from app.models.shopify import ListingSyncStatus, ShopifyConnection, StoreListing
from app.models.store import Store, StorePlatform, StoreStatus
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User

__all__ = [
    "AIPrompt",
    "AliExpressConnection",
    "AnalyticsDaily",
    "AutomationAction",
    "AutomationRule",
    "AutomationRun",
    "AutomationSchedule",
    "Base",
    "EmailVerificationToken",
    "FulfillmentStatus",
    "IdentifiedBase",
    "ImportStatus",
    "IntegrationStatus",
    "InventoryChange",
    "InventoryChangeReason",
    "InventorySyncRun",
    "ListingSyncStatus",
    "Notification",
    "NotificationKind",
    "Order",
    "OrderEvent",
    "OrderEventType",
    "OrderItem",
    "OrderSource",
    "OrderSyncRun",
    "PaymentStatus",
    "PriceChange",
    "PricingRule",
    "PricingScope",
    "PricingStrategy",
    "Product",
    "ProductAIStatus",
    "ProductImage",
    "ProductImport",
    "ProductSource",
    "ProductStatus",
    "ProductVariant",
    "ProductVersion",
    "ProductVersionSource",
    "PromptExecution",
    "PromptExecutionStatus",
    "ReferenceBase",
    "RefreshToken",
    "Role",
    "RoleName",
    "Shipment",
    "ShipmentStatus",
    "ShopifyConnection",
    "SoftDeleteMixin",
    "Store",
    "StoreListing",
    "StorePlatform",
    "StoreStatus",
    "SyncRunStatus",
    "SyncTrigger",
    "Tenant",
    "TenantMixin",
    "TenantScopedBase",
    "TenantStatus",
    "TimestampMixin",
    "TrackingEvent",
    "UUIDPrimaryKeyMixin",
    "User",
    "UserRole",
]

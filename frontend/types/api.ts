/**
 * Types mirroring the backend API contract.
 *
 * These are maintained by hand in Phase 0 because the surface is two endpoints.
 * That does not scale: hand-written types drift from the server silently, and
 * the drift only surfaces as a runtime bug. Before the API grows, generate
 * these from the OpenAPI document the backend already publishes at
 * `/openapi.json` (openapi-typescript) and delete this file.
 */

/** Error envelope returned by every failing endpoint. */
export interface ApiErrorDetail {
  field: string | null;
  message: string;
  type: string | null;
}

export interface ApiErrorResponse {
  /** Stable, machine-readable. Branch on this, never on `message`. */
  code: string;
  /** Human-readable and subject to change without notice. */
  message: string;
  details: ApiErrorDetail[];
  /** Correlates with the server log line. Show it in support surfaces. */
  requestId: string | null;
}

/** Pagination metadata accompanying every list response. */
export interface PageMeta {
  page: number;
  size: number;
  totalItems: number;
  totalPages: number;
  hasNext: boolean;
  hasPrevious: boolean;
}

/** A page of results. Generic over the item type. */
export interface Page<T> {
  items: T[];
  meta: PageMeta;
}

export type RoleName = "owner" | "admin" | "member" | "viewer";

export interface User {
  id: string;
  tenantId: string;
  email: string;
  firstName: string | null;
  lastName: string | null;
  fullName: string | null;
  isActive: boolean;
  isVerified: boolean;
  lastLoginAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export type TenantStatus = "trial" | "active" | "suspended" | "cancelled";

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  status: TenantStatus;
  timezone: string;
  defaultCurrency: string;
}

/** Who the caller is, which tenant they belong to, and what they may do. */
export interface AuthenticatedIdentity {
  user: User;
  tenant: Tenant;
  roles: RoleName[];
}

export interface TokenResponse {
  accessToken: string;
  tokenType: string;
  /** Access token lifetime in seconds. */
  expiresIn: number;
  /**
   * Absent for browser clients: the refresh token lives in an httpOnly cookie
   * that JavaScript cannot read, which is the point.
   */
  refreshToken: string | null;
  accessExpiresAt: string;
  refreshExpiresAt: string;
}

export interface AuthResponse {
  identity: AuthenticatedIdentity;
  tokens: TokenResponse;
}

export type IntegrationStatus = "pending" | "connected" | "expired" | "error";

/**
 * A marketplace connection as returned by the API.
 *
 * Note what is absent: no app secret, no access token, no refresh token, not
 * even a masked one. The server's response model has no field for them, which
 * is why a credential cannot reach the browser.
 */
export interface AliExpressConnection {
  id: string;
  status: IntegrationStatus;
  /** Public identifier, not a secret — it travels in every request URL. */
  appKey: string;
  connectedAt: string | null;
  lastSyncAt: string | null;
  tokenExpiresAt: string | null;
  isTokenExpired: boolean;
  lastError: string | null;
}

/**
 * Whether a connected store's webhooks can be trusted.
 *
 * Derived by the API from `status` and `webhooksRegisteredAt` — it is not a
 * separate stored field, so it cannot disagree with the timestamp. It exists
 * because the client used to have to invent its own rule for what a null
 * timestamp meant, and the rule it invented was "assume connected", which hid
 * stores that were missing every product, inventory and order subscription.
 */
export type ShopifyWebhookHealth = "healthy" | "degraded" | "not_applicable";

export interface ShopifyConnection {
  id: string;
  storeId: string;
  shopDomain: string;
  status: IntegrationStatus;
  scopes: string;
  connectedAt: string;
  lastSyncAt: string | null;
  lastError: string | null;
  webhooksRegisteredAt: string | null;
  webhookHealth: ShopifyWebhookHealth;
}

/** What reconciliation did about one topic. */
export interface ShopifyWebhookTopicResult {
  topic: string;
  status: "already_present" | "created" | "unknown" | "failed";
  webhookGid: string | null;
  detail: string | null;
}

/**
 * The result of a webhook retry.
 *
 * Returned with 200 whether or not the outcome was healthy, so `healthy` — not
 * the HTTP status — is what decides whether the store is fixed.
 */
export interface ShopifyWebhookReconcileResult {
  storeId: string;
  healthy: boolean;
  webhookHealth: ShopifyWebhookHealth;
  topics: ShopifyWebhookTopicResult[];
  warnings: string[];
  listedCount: number;
  createdCount: number;
  webhooksRegisteredAt: string | null;
}

export interface ShopifyStatus {
  configured: boolean;
  connections: ShopifyConnection[];
}

export interface ShopifyAuthorization {
  authorizationUrl: string;
  state: string;
  expiresInSeconds: number;
}

export interface ShopifyConnectPayload {
  shop: string;
  storeName?: string;
}

export interface AliExpressStatus {
  /**
   * Computed server-side. A connection whose token has expired is not
   * connected, so clients must not infer this from `status` alone.
   */
  connected: boolean;
  connection: AliExpressConnection | null;
}

export interface AliExpressAuthorization {
  authorizationUrl: string;
  state: string;
  expiresInSeconds: number;
}

export interface LoginPayload {
  email: string;
  password: string;
  rememberMe?: boolean;
}

export interface RegisterPayload {
  companyName: string;
  email: string;
  password: string;
  firstName?: string;
  lastName?: string;
}

/** Query parameters accepted by every list endpoint. */
export type ProductSource = "aliexpress" | "manual";
export type ProductStatus = "draft" | "active" | "archived" | "unavailable";
export type ImportStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

/**
 * Prices are `string`, not `number`.
 *
 * The backend stores money as `Decimal` and serialises it as a string. Parsing
 * it into a JavaScript number would reintroduce the binary floating-point error
 * the backend went to some trouble to avoid — 3.30 is not representable, and
 * the error compounds across a catalogue and again across a margin calculation.
 * Format for display; do not do arithmetic on these without a decimal library.
 */
export interface ProductVariant {
  id: string;
  externalVariantId: string;
  externalAttributes: string | null;
  label: string | null;
  costPrice: string | null;
  listPrice: string | null;
  currency: string | null;
  stockQuantity: number;
  imageUrl: string | null;
  merchantSku: string | null;
  sellPrice: string | null;
  compareAtPrice: string | null;
  isEnabled: boolean;
}

export interface ProductImage {
  id: string;
  url: string;
  position: number;
  altText: string | null;
  isSupplier: boolean;
}

/** Where a product sits relative to AI optimisation. */
export type ProductAIStatus = "not_optimized" | "optimized" | "failed";

export interface Product {
  id: string;
  source: ProductSource;
  externalId: string;
  externalUrl: string | null;
  title: string;
  /** Number of supplier variants (SKUs) synced for this product — a
   * correlated count from the same list query, always accurate (see
   * `ProductRepository._variant_count_column`), never a placeholder. */
  variantCount: number;
  categoryId: string | null;
  categoryName: string | null;
  brand: string | null;
  status: ProductStatus;
  currency: string | null;
  costPriceMin: string | null;
  costPriceMax: string | null;
  sellPrice: string | null;
  stockQuantity: number;
  packageWeightKg: string | null;
  packageLengthCm: number | null;
  packageWidthCm: number | null;
  packageHeightCm: number | null;
  deliveryTimeDays: number | null;
  shipToCountry: string | null;
  shippingCost: string | null;
  warehouseOrigin: string | null;
  importShipToCountry: string | null;
  importShipToCheckedAt: string | null;
  requiresShipping: boolean;
  hsCode: string | null;
  countryOfOrigin: string | null;
  customsDescription: string | null;
  handlingTimeDays: number | null;
  weightUnit: string | null;
  dimensionUnit: string | null;
  supplierName: string | null;
  rating: string | null;
  reviewCount: number | null;
  orderCount: number | null;
  lastSyncedAt: string | null;
  lastSyncError: string | null;
  createdAt: string;
  /** Optimistic-concurrency token (M2A) — the same database-generated
   * `updated_at` every row already has, echoed back as `expectedUpdatedAt`
   * on the next save. See `ProductUpdatePayload.expectedUpdatedAt`. */
  updatedAt: string;

  // --- SEO / marketplace (Phase 9 stage 3) ---
  seoTitle: string | null;
  seoDescription: string | null;
  /** Legacy; not exported to Shopify. Prefer searchTopics. */
  metaKeywords: string | null;
  searchTopics: string[];
  seoPlanning: Record<string, unknown>;
  ogTitle: string | null;
  ogDescription: string | null;
  redirectOldHandle: boolean;
  slug: string | null;
  vendor: string | null;
  tags: string[];

  // --- AI optimisation (Phase 9 stage 3) ---
  //
  // `optimizedTitle`/`optimizedDescription` are AI-generated text — always
  // `StubProvider` output until a real provider is configured (Phase 9
  // stage 4+). Render as plain text, the same rule that already applies to
  // every other AI-produced field on this platform.
  aiStatus: ProductAIStatus;
  aiLastGeneratedAt: string | null;
  aiProvider: string | null;
  aiVersion: number | null;
  optimizedTitle: string | null;
  optimizedDescription: string | null;
}

export interface ProductDetail extends Product {
  variants: ProductVariant[];
  images: ProductImage[];

  // --- Description (Product Editor stage 1) ---
  //
  // Both already sanitized server-side — never raw supplier HTML. `description`
  // is the merchant-editable field; `supplierDescription` is the always-current
  // supplier snapshot, kept separate so a sync cannot silently overwrite edits.
  description: string | null;
  supplierDescription: string | null;
  supplierTitle: string | null;
  supplierBrand: string | null;
}

/** The existing product a duplicate-import check found — minimal by design,
 * not a full `Product`. `isPublished` decides the frontend's link target
 * (`/drafts/{id}` vs `/products/{id}`). */
export interface ProductDuplicateMatch {
  id: string;
  title: string;
  status: ProductStatus;
  isPublished: boolean;
}

/** Authoritative, tenant-scoped answer to "is this supplier product already
 * imported" — a direct server lookup, not a scan of a cached list page. */
export interface ProductDuplicateCheckResponse {
  exists: boolean;
  product: ProductDuplicateMatch | null;
}

export type DraftPricingApplyMode =
  | "percentage_markup"
  | "fixed_markup"
  | "target_margin"
  | "set_sell_price"
  | "set_compare_at";

export interface DraftVariantPricingRow {
  variantId: string;
  label: string | null;
  isEnabled: boolean;
  supplierCost: string | null;
  supplierCurrency: string | null;
  convertedCost: string | null;
  convertedCurrency: string | null;
  conversionRequired: boolean;
  conversionType: string | null;
  conversionRate: string | null;
  conversionRateTimestamp: string | null;
  fxFetchedAt: string | null;
  fxProvider: string | null;
  fxStatus: string | null;
  fxBaseCurrency: string | null;
  fxQuoteCurrency: string | null;
  fxIsStale: boolean | null;
  supplierShippingCost: string | null;
  shippingCostAvailable: boolean;
  handlingCost: string;
  feeEstimate: string | null;
  sellPrice: string | null;
  compareAtPrice: string | null;
  proposedSellPrice: string | null;
  profit: string | null;
  marginPercent: string | null;
  breakEvenPrice: string | null;
  pricingRuleSource: string | null;
  manualOverride: boolean;
  rowBlocked: boolean;
  rowBlockMessage: string | null;
  /**
   * `sellPrice` was written under a different selling currency than the one
   * this workspace just resolved (a store switch, a newly-verified Shopify
   * sync) — or under no recorded currency at all. The number is not wrong
   * on its own terms, but it is not trustworthy as "the price in today's
   * selling currency" until pricing is re-run for this row.
   */
  needsRecalculation: boolean;
}

export interface DraftPricingWorkspace {
  productId: string;
  currency: string | null;
  sellingCurrency: string | null;
  sellingCurrencySource: string | null;
  destinationStoreId: string | null;
  productSellPrice: string | null;
  costPriceMin: string | null;
  costPriceMax: string | null;
  shippingCost: string | null;
  shippingCostAvailable: boolean;
  shippingWarning: string | null;
  fxNote: string;
  pricingBlocked: boolean;
  pricingBlockCode: string | null;
  pricingBlockMessage: string | null;
  fxProvider: string | null;
  fxStatus: string | null;
  fxRate: string | null;
  fxBaseCurrency: string | null;
  fxQuoteCurrency: string | null;
  fxProviderTimestamp: string | null;
  fxFetchedAt: string | null;
  fxIsStale: boolean | null;
  /** True when any variant's `needsRecalculation` is true. */
  needsRecalculation: boolean;
  variants: DraftVariantPricingRow[];
}

export interface DraftPricingApplyPayload {
  mode: DraftPricingApplyMode;
  markupPercent?: string;
  markupFixed?: string;
  targetMarginPercent?: string;
  minProfit?: string;
  minSellPrice?: string;
  maxSellPrice?: string;
  sellPrice?: string;
  compareAtPrice?: string;
  variantIds?: string[];
  roundToCents?: boolean;
  psychologicalRounding?: boolean;
  handlingCost?: string;
  feePercent?: string;
  includeShippingInCost?: boolean;
}

/** PATCH body for merchant draft/product edits — only sent fields change. */
export interface ProductUpdatePayload {
  title?: string;
  description?: string | null;
  brand?: string | null;
  categoryName?: string | null;
  vendor?: string | null;
  tags?: string[];
  seoTitle?: string | null;
  seoDescription?: string | null;
  metaKeywords?: string | null;
  searchTopics?: string[];
  seoPlanning?: Record<string, unknown>;
  ogTitle?: string | null;
  ogDescription?: string | null;
  redirectOldHandle?: boolean;
  slug?: string | null;
  status?: ProductStatus;
  requiresShipping?: boolean;
  hsCode?: string | null;
  countryOfOrigin?: string | null;
  customsDescription?: string | null;
  handlingTimeDays?: number | null;
  packageWeightKg?: string | null;
  packageLengthCm?: number | null;
  packageWidthCm?: number | null;
  packageHeightCm?: number | null;
  weightUnit?: string | null;
  dimensionUnit?: string | null;
  /** Optimistic-concurrency guard (M2A) — the `updatedAt` this edit was
   * based on. Optional and backward compatible: omit it to get the pre-M2A
   * last-write-wins behaviour. When present, the server rejects the save
   * with a 409 if the row has changed since, rather than overwriting it. */
  expectedUpdatedAt?: string;
}

export interface StoreListing {
  id: string;
  storeId: string;
  productId: string;
  externalProductId: string;
  externalHandle: string | null;
  externalGraphqlId: string | null;
  shopDomain: string | null;
  storefrontUrl: string | null;
  adminUrl: string | null;
  onlineStorePublished: boolean | null;
  status: string;
  lastSyncedAt: string | null;
  lastError: string | null;
  publishedAt: string | null;
  lastFailedSyncAt: string | null;
}

export interface ShopifyPublishResult {
  message: string;
  listingId: string;
  externalProductId: string;
  externalHandle: string | null;
  externalGraphqlId: string | null;
  shopDomain: string | null;
  storefrontUrl: string | null;
  adminUrl: string | null;
  onlineStorePublished: boolean | null;
  updated: boolean;
}

export interface SeoScore {
  score: number;
  status: string;
  sections: Record<string, number>;
  warnings: string[];
  explanations: string[];
  metaKeywordsExported: boolean;
  note: string;
}

/** Where a product's optimisable content came from. */
export type ProductVersionSource = "original" | "ai_generated";

/** One version of a product's title/description — one row of history. */
export interface ProductVersion {
  id: string;
  versionNumber: number;
  source: ProductVersionSource;
  title: string | null;
  description: string | null;
  active: boolean;
  aiProvider: string | null;
  promptExecutionId: string | null;
  createdByUserId: string | null;
  createdAt: string;
}

export interface ProductOptimizePayload {
  tone?: string;
}

export interface ProductOptimizeResult {
  product: ProductDetail;
  version: ProductVersion;
}

export interface ProductImportRecord {
  id: string;
  source: ProductSource;
  externalId: string;
  status: ImportStatus;
  productId: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string;
}

export interface ProductImportPayload {
  externalId: string;
  shipToCountry?: string;
  currency?: string;
  storeId?: string;
}

/** A feed entry: a summary, with no variants and no stock. */
export interface FeedProduct {
  externalId: string;
  title: string | null;
  imageUrl: string | null;
  price: string | null;
  currency: string | null;
  orders: number | null;
  categoryName: string | null;
}

export interface ListQuery {
  page?: number;
  size?: number;
  sortBy?: string;
  sortDir?: "asc" | "desc";
  q?: string;
}

/** Sidebar badge totals for Drafts vs published Products. */
export interface ProductWorkspaceCounts {
  drafts: number;
  products: number;
}

export type OrderSource = "aliexpress" | "manual";

export type FulfillmentStatus =
  | "pending"
  | "awaiting_payment"
  | "paid"
  | "processing"
  | "fulfilled"
  | "shipped"
  | "delivered"
  | "cancelled"
  | "refunded"
  | "disputed";

export type PaymentStatus = "unknown" | "unpaid" | "paid" | "refunded";

export type ShipmentStatus =
  | "pending"
  | "in_transit"
  | "out_for_delivery"
  | "delivered"
  | "exception"
  | "returned";

export type OrderEventType =
  | "created"
  | "synced"
  | "status_change"
  | "payment_change"
  | "shipment_update"
  | "note"
  | "webhook";

export type SyncRunStatus = "running" | "succeeded" | "failed";
export type SyncTrigger = "manual" | "scheduled" | "webhook";

/** Money fields are `string` for the same Decimal reason documented above. */
export interface OrderItem {
  id: string;
  externalItemId: string | null;
  productId: string | null;
  externalProductId: string | null;
  title: string | null;
  skuAttributes: string | null;
  quantity: number;
  unitPrice: string | null;
  currency: string | null;
  externalStatus: string | null;
}

export interface TrackingEvent {
  id: string;
  occurredAt: string;
  status: string | null;
  description: string | null;
  location: string | null;
}

export interface Shipment {
  id: string;
  trackingNumber: string | null;
  carrier: string | null;
  serviceName: string | null;
  status: ShipmentStatus;
  estimatedDeliveryAt: string | null;
  shippedAt: string | null;
  deliveredAt: string | null;
  currentLocation: string | null;
  lastCheckedAt: string | null;
  trackingEvents: TrackingEvent[];
}

export interface Order {
  id: string;
  source: OrderSource;
  externalId: string;
  externalStatus: string | null;
  fulfillmentStatus: FulfillmentStatus;
  paymentStatus: PaymentStatus;
  buyerName: string | null;
  countryCode: string | null;
  currency: string | null;
  totalAmount: string | null;
  itemCount: number;
  externalCreatedAt: string | null;
  lastSyncedAt: string | null;
  lastSyncError: string | null;
  createdAt: string;
}

export interface OrderDetail extends Order {
  buyerCountry: string | null;
  recipientName: string | null;
  recipientPhone: string | null;
  addressLine1: string | null;
  addressLine2: string | null;
  city: string | null;
  province: string | null;
  postalCode: string | null;
  shippingAmount: string | null;
  paidAt: string | null;
  deliveredAt: string | null;
  items: OrderItem[];
  shipments: Shipment[];
}

/** One row of the merged lifecycle-plus-tracking history. */
export interface OrderTimelineEntry {
  kind: "order" | "tracking";
  eventType: OrderEventType | null;
  fromStatus: string | null;
  toStatus: string | null;
  status: string | null;
  description: string | null;
  location: string | null;
  occurredAt: string;
}

export interface OrderSyncRun {
  id: string;
  trigger: SyncTrigger;
  status: SyncRunStatus;
  windowStart: string | null;
  windowEnd: string | null;
  ordersSeen: number;
  ordersCreated: number;
  ordersUpdated: number;
  errorCode: string | null;
  errorMessage: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string;
}

export interface OrderSyncPayload {
  sinceDays?: number;
}

export interface OrderStatistics {
  totalOrders: number;
  byStatus: Record<string, number>;
  pendingFulfillment: number;
  processing: number;
  delivered: number;
  failedSyncsLast7Days: number;
  lastSync: OrderSyncRun | null;
  /** Null when Redis is unavailable — absent rather than a fabricated zero. */
  webhookEventsReceived: number | null;
}

/** Filters accepted by GET /orders beyond the shared list parameters. */
export interface OrderListQuery extends ListQuery {
  status?: FulfillmentStatus;
  source?: OrderSource;
  dateFrom?: string;
  dateTo?: string;
}

export type HealthStatus = "healthy" | "degraded" | "unhealthy";

export interface ComponentHealth {
  name: string;
  status: HealthStatus;
  detail: string | null;
}

export interface HealthResponse {
  status: HealthStatus;
  version: string;
  environment: string;
  components: ComponentHealth[];
}

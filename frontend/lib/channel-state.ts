import type { Store, StorePlatform, StoreStatus } from "@/services/stores";
import type {
  AliExpressStatus,
  EbayStatus,
  ShopifyConnection,
  ShopifyStatus,
} from "@/types/api";

/**
 * One vocabulary for "is this channel connected?" (UX-L2D-06).
 *
 * Every provider card, the channel summary and the store-record list read
 * their words from here, mapped from the fields the API actually returns.
 * Nothing is inferred from the fact that a row exists: a Shopify `Store` row
 * created without OAuth is not a connection, an AliExpress row whose token
 * has expired is not connected, and an eBay row awaiting reconnection is not
 * connected — each of those is a distinct state with a distinct next action.
 *
 * | kind                | Meaning                                              | Who can fix it |
 * |---------------------|------------------------------------------------------|----------------|
 * | checking            | status request in flight, nothing known yet          | —              |
 * | unavailable         | status request failed                                | retry          |
 * | setup-unavailable   | the server has no app credentials for this provider  | operator       |
 * | not-connected       | no connection row                                    | merchant: connect |
 * | pending             | authorization started, not completed                 | merchant: continue / disconnect |
 * | connected           | the provider confirms a usable authorization         | —              |
 * | needs-attention     | connected, but the last operation failed or a setup step is incomplete | merchant: retry / reconnect |
 * | reconnect-required  | the authorization is no longer valid                 | merchant: reconnect |
 *
 * Provider-specific evidence is in each `derive*` function's comment. Raw
 * provider error text is never part of a label or detail: Shopify's
 * `lastError` is `str(exc)` from the sync path and is not shown; AliExpress's
 * `lastError` is a curated message from the backend's exception catalogue and
 * may be shown; eBay's `reconnectReason` is a machine code mapped here.
 */

export type ChannelStateKind =
  | "checking"
  | "unavailable"
  | "setup-unavailable"
  | "not-connected"
  | "pending"
  | "connected"
  | "needs-attention"
  | "reconnect-required";

export type ChannelTone = "neutral" | "success" | "warning" | "danger";

export interface ChannelState {
  kind: ChannelStateKind;
  /** Badge text. */
  label: string;
  tone: ChannelTone;
  /** One sentence for the card body. */
  detail: string;
}

export const CHANNEL_LABEL: Record<ChannelStateKind, string> = {
  checking: "Checking…",
  unavailable: "Status unavailable",
  "setup-unavailable": "Setup unavailable",
  "not-connected": "Not connected",
  pending: "Awaiting authorization",
  connected: "Connected",
  "needs-attention": "Needs attention",
  "reconnect-required": "Reconnect required",
};

export const CHANNEL_TONE: Record<ChannelStateKind, ChannelTone> = {
  checking: "neutral",
  unavailable: "warning",
  "setup-unavailable": "neutral",
  "not-connected": "neutral",
  pending: "warning",
  connected: "success",
  "needs-attention": "warning",
  "reconnect-required": "danger",
};

function state(kind: ChannelStateKind, detail: string, label?: string): ChannelState {
  return { kind, label: label ?? CHANNEL_LABEL[kind], tone: CHANNEL_TONE[kind], detail };
}

export interface StatusQueryState<T> {
  data: T | undefined;
  isPending: boolean;
  isError: boolean;
}

// ---------------------------------------------------------------------------
// Shopify
// ---------------------------------------------------------------------------

export type ShopifyAction = "retry-webhooks" | "reconnect" | "disconnect";

export interface ShopifyConnectionState extends ChannelState {
  connection: ShopifyConnection;
  /** Actions that make sense for this row, in display order. Admin-only. */
  actions: ShopifyAction[];
  /** Secondary badge about webhooks, when the row is connected. */
  webhookLabel: "Webhooks last confirmed" | "Webhooks incomplete" | null;
}

/**
 * One store's state. `status` is the connection row's own lifecycle;
 * `webhookHealth` is derived by the API from `webhooksRegisteredAt`, so a
 * store that is connected but never confirmed its webhooks is "needs
 * attention", not "connected" — product, inventory and order updates would
 * be missed.
 */
export function deriveShopifyConnection(connection: ShopifyConnection): ShopifyConnectionState {
  const degraded = connection.webhookHealth === "degraded";
  const base = { connection, webhookLabel: null as ShopifyConnectionState["webhookLabel"] };

  if (connection.status === "connected") {
    if (degraded) {
      // The row states the condition once; the card's warning alert carries
      // the consequence ("updates may be missed") and the reassurance. The
      // two used to say the same sentence, which read as two problems and
      // broke strict text lookups (Phase 2 CI).
      return {
        ...base,
        ...state("needs-attention", "Webhook setup for this store did not complete."),
        webhookLabel: "Webhooks incomplete",
        actions: ["retry-webhooks", "disconnect"],
      };
    }
    return {
      ...base,
      ...state("connected", "DropPilot can publish products and receive updates from this store."),
      webhookLabel: connection.webhookHealth === "healthy" ? "Webhooks last confirmed" : null,
      actions: ["disconnect"],
    };
  }
  if (connection.status === "pending") {
    return {
      ...base,
      ...state(
        "pending",
        "Authorization on Shopify was started but not completed. Reconnect to finish it.",
      ),
      actions: ["reconnect", "disconnect"],
    };
  }
  if (connection.status === "expired") {
    return {
      ...base,
      ...state("reconnect-required", "Shopify no longer accepts DropPilot's access. Reconnect to restore it."),
      actions: ["reconnect", "disconnect"],
    };
  }
  // `error`: the last sync with Shopify failed. The API's `lastError` is the
  // raw exception text and is not merchant copy; the remedy is the same
  // whatever it says.
  return {
    ...base,
    ...state(
      "needs-attention",
      "The last sync with this store failed. Try reconnecting; if it keeps failing, ask your DropPilot operator to check the store.",
    ),
    actions: ["reconnect", "disconnect"],
  };
}

export interface ShopifyChannelState extends ChannelState {
  configured: boolean;
  connections: ShopifyConnectionState[];
  /** Whether "Connect Shopify" can start a flow. */
  canConnect: boolean;
}

export function deriveShopifyChannel(query: StatusQueryState<ShopifyStatus>): ShopifyChannelState {
  if (query.isPending && query.data === undefined) {
    return { ...state("checking", "Loading Shopify status."), configured: false, connections: [], canConnect: false };
  }
  if (query.data === undefined) {
    return {
      ...state("unavailable", "We could not load Shopify status. Try again."),
      configured: false,
      connections: [],
      canConnect: false,
    };
  }
  const { configured } = query.data;
  const connections = query.data.connections.map(deriveShopifyConnection);
  if (!configured) {
    return {
      ...state(
        "setup-unavailable",
        "Shopify cannot be connected on this server yet. Ask your DropPilot operator to add the Shopify app credentials — there is nothing you need to enter here.",
      ),
      configured,
      connections,
      canConnect: false,
    };
  }
  const connected = connections.filter((c) => c.kind === "connected").length;
  const webhookIssues = connections.filter((c) => c.webhookLabel === "Webhooks incomplete").length;
  if (connections.length === 0) {
    return {
      ...state("not-connected", "No Shopify store is linked to this workspace yet."),
      configured,
      connections,
      canConnect: true,
    };
  }
  if (webhookIssues > 0) {
    return {
      ...state(
        "needs-attention",
        `${webhookIssues} ${webhookIssues === 1 ? "store needs" : "stores need"} webhook setup.`,
        `${webhookIssues} needs webhook setup`,
      ),
      configured,
      connections,
      canConnect: true,
    };
  }
  if (connected === 0) {
    const reconnect = connections.some((c) => c.kind === "reconnect-required");
    return {
      ...state(
        reconnect ? "reconnect-required" : "needs-attention",
        reconnect
          ? "A store needs reconnecting before DropPilot can publish to it."
          : "A store needs attention before DropPilot can publish to it.",
      ),
      configured,
      connections,
      canConnect: true,
    };
  }
  const others = connections.length - connected;
  return {
    ...state(
      others > 0 ? "needs-attention" : "connected",
      others > 0
        ? `${connected} connected · ${others} ${others === 1 ? "store needs" : "stores need"} attention.`
        : `${connected} ${connected === 1 ? "store" : "stores"} connected.`,
      others > 0 ? "Needs attention" : `${connected} connected`,
    ),
    configured,
    connections,
    canConnect: true,
  };
}

// ---------------------------------------------------------------------------
// AliExpress
// ---------------------------------------------------------------------------

export type AliExpressAction = "connect" | "continue" | "reconnect" | "disconnect";

export interface AliExpressChannelState extends ChannelState {
  actions: AliExpressAction[];
  /** The backend's curated message, when the row carries one. */
  message: string | null;
}

/**
 * The status endpoint has no `configured` flag; a missing platform app is
 * only observable when `POST /connect` answers 422. Callers pass
 * `setupUnavailable` once they have seen that answer.
 */
export function deriveAliExpressChannel(
  query: StatusQueryState<AliExpressStatus>,
  options: { setupUnavailable?: boolean } = {},
): AliExpressChannelState {
  if (options.setupUnavailable) {
    return {
      ...state(
        "setup-unavailable",
        "AliExpress cannot be connected on this server yet. Ask your DropPilot operator to add the AliExpress app credentials — there is nothing you need to enter here.",
      ),
      actions: [],
      message: null,
    };
  }
  if (query.isPending && query.data === undefined) {
    return { ...state("checking", "Loading AliExpress status."), actions: [], message: null };
  }
  if (query.data === undefined) {
    return {
      ...state("unavailable", "We could not load AliExpress status. Try again."),
      actions: [],
      message: null,
    };
  }
  const { connected, connection } = query.data;
  if (connected) {
    return {
      ...state("connected", "Supplier products can be imported and orders placed through this account."),
      actions: ["reconnect", "disconnect"],
      message: null,
    };
  }
  if (!connection) {
    return {
      ...state("not-connected", "Connect your AliExpress seller account to import supplier products."),
      actions: ["connect"],
      message: null,
    };
  }
  if (connection.status === "pending") {
    return {
      ...state(
        "pending",
        "Authorization on AliExpress was started but not completed. Continue to finish it, or disconnect to start over.",
      ),
      actions: ["continue", "disconnect"],
      message: null,
    };
  }
  if (connection.status === "expired" || connection.isTokenExpired) {
    return {
      ...state(
        "reconnect-required",
        "AliExpress no longer accepts DropPilot's access. Reconnect to keep importing products and syncing orders.",
      ),
      actions: ["reconnect", "disconnect"],
      message: connection.lastError,
    };
  }
  return {
    ...state(
      "needs-attention",
      "The last request to AliExpress failed. Reconnect if this continues.",
    ),
    actions: ["reconnect", "disconnect"],
    message: connection.lastError,
  };
}

// ---------------------------------------------------------------------------
// eBay
// ---------------------------------------------------------------------------

export type EbayAction = "connect" | "continue" | "reconnect" | "disconnect";

export interface EbayChannelState extends ChannelState {
  configured: boolean;
  actions: EbayAction[];
  /** Why reconnection is needed, in the merchant's terms. */
  reason: string | null;
}

/** The server sends a stable code; this is where it becomes English. */
const EBAY_RECONNECT_REASONS: Record<string, string> = {
  refresh_token_revoked:
    "eBay ended the authorization. This normally happens after an eBay password or username change, and reconnecting is the only way to restore it.",
  no_refresh_token:
    "eBay did not return a renewable authorization, so DropPilot cannot keep the connection alive. Reconnect to issue a new one.",
};

export function deriveEbayChannel(query: StatusQueryState<EbayStatus>): EbayChannelState {
  if (query.isPending && query.data === undefined) {
    return { ...state("checking", "Loading eBay status."), configured: true, actions: [], reason: null };
  }
  if (query.data === undefined) {
    return {
      ...state("unavailable", "We could not load eBay status. Try again."),
      configured: true,
      actions: [],
      reason: null,
    };
  }
  const { configured, connected, connection } = query.data;
  if (!configured) {
    return {
      ...state(
        "setup-unavailable",
        "eBay cannot be connected on this server yet. Ask your DropPilot operator to add the eBay application credentials — there is nothing you need to enter here.",
      ),
      configured,
      actions: [],
      reason: null,
    };
  }
  if (connected) {
    return {
      ...state("connected", "DropPilot can list and fulfil through this seller account."),
      configured,
      actions: ["reconnect", "disconnect"],
      reason: null,
    };
  }
  if (!connection) {
    return {
      ...state("not-connected", "Connect your eBay seller account to list products on eBay."),
      configured,
      actions: ["connect"],
      reason: null,
    };
  }
  if (connection.needsReconnect || connection.status === "reconnect_required") {
    return {
      ...state("reconnect-required", "The eBay authorization is no longer valid. Reconnect to restore it."),
      configured,
      actions: ["reconnect", "disconnect"],
      reason:
        (connection.reconnectReason && EBAY_RECONNECT_REASONS[connection.reconnectReason]) ??
        "The eBay authorization is no longer valid. Reconnect to restore it.",
    };
  }
  if (connection.status === "pending") {
    return {
      ...state(
        "pending",
        "Authorization on eBay was started but not completed. Continue to finish it, or disconnect to start over.",
      ),
      configured,
      actions: ["continue", "disconnect"],
      reason: null,
    };
  }
  return {
    ...state("needs-attention", "The last request to eBay failed. Reconnect if this continues."),
    configured,
    actions: ["reconnect", "disconnect"],
    reason: null,
  };
}

// ---------------------------------------------------------------------------
// Store records (`/stores`)
// ---------------------------------------------------------------------------

export const PLATFORM_LABEL: Record<StorePlatform, string> = {
  shopify: "Shopify",
  woocommerce: "WooCommerce",
  ebay: "eBay",
  etsy: "Etsy",
  tiktok_shop: "TikTok Shop",
  manual: "Manual",
};

/**
 * A `Store` row's own lifecycle, in the shared vocabulary. A row is a record
 * of a store DropPilot has known; it is not proof of authorization — that is
 * the provider connection's job, which is why `pending` reads as "setup
 * incomplete" rather than as anything hopeful.
 */
export function deriveStoreRecordState(store: Pick<Store, "status" | "lastError">): ChannelState {
  const status: StoreStatus = store.status;
  switch (status) {
    case "connected":
      return state("connected", "Authorized through Integrations.");
    case "syncing":
      return state("connected", "A sync is running.", "Syncing");
    case "error":
      return state("needs-attention", "The last sync failed. Manage this store under Integrations.");
    case "disconnected":
      return state(
        "not-connected",
        "DropPilot's access was removed. Products and listings recorded for this store are kept.",
        "Disconnected",
      );
    default:
      return state(
        "pending",
        "This store record was never authorized. Connect it under Integrations, or leave it.",
        "Setup incomplete",
      );
  }
}

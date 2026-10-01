import { ApiError } from "@/lib/api-client";

/**
 * Error classification for AI Studio. Branches on `code` and on the stable
 * reason the backend puts in `details` — never on `message` (plan §29).
 *
 * The backend raises `details={"reason": X}`; the error envelope turns that
 * into a list entry `{ field: null, message: X, type: "reason" }`.
 */

export function reasonOf(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  return error.details.find((detail) => detail.type === "reason")?.message ?? null;
}

export function isStoreNotFound(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.code === "not_found" &&
    error.details.some((detail) => detail.type === "resource" && detail.message === "Store")
  );
}

/** The comma-separated blocker codes on a `publish_blocked` refusal. */
export function blockerCodesOf(error: unknown): string[] {
  if (!(error instanceof ApiError)) return [];
  const entry = error.details.find((detail) => detail.type === "blocker_codes");
  return entry ? entry.message.split(",").map((code) => code.trim()).filter(Boolean) : [];
}

export function requestIdOf(error: unknown): string | null {
  return error instanceof ApiError ? error.requestId : null;
}

/** Reasons that mean "this candidate no longer matches the product". */
export function isStaleCandidateError(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.code !== "conflict") return false;
  const reason = reasonOf(error);
  return reason === "stale_preview" || reason === "draft_version_stale";
}

/**
 * Merchant copy for a Studio failure. Codes and reasons are mapped; anything
 * unrecognised falls back to a generic sentence, with the request id shown
 * separately by the caller.
 */
export function studioErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Something went wrong. Please try again.";
  const reason = reasonOf(error);

  switch (reason) {
    case "stale_preview":
      return "This product changed after the preview was generated.";
    case "draft_version_stale":
      return "The draft changed after the preview was generated.";
    case "not_a_pipeline_candidate":
      return "This version cannot be reviewed as an AI Studio candidate.";
    case "pipeline_candidate_requires_approval":
      return "Approve this version in AI Studio before using it.";
    case "candidate_not_approved":
      return "Approve this version before publishing.";
    case "synthetic_publish_blocked":
      return "Test previews cannot be published.";
    case "ai_provenance_unverified":
      return "This AI version's origin cannot be verified, so it cannot be published.";
    case "candidate_title_not_publishable":
      return "The AI title is too long to publish to Shopify.";
    case "candidate_description_too_long":
      return "The AI description is too long to publish to Shopify.";
    case "candidate_content_invalid":
      return "This AI version's title is empty or too long to approve.";
    case "original_not_approvable":
      return "The original supplier version cannot be approved here.";
    case "publish_blocked":
      return "Shopify publishing is blocked. Resolve the items listed below, then try again.";
    case "destination_mismatch":
      return "This product was imported for a different destination than the selected store.";
    case "selling_currency_mismatch":
      return "This product's selling currency does not match the selected store.";
    case "published_ai_content_unavailable":
      return "The AI text live on Shopify can no longer be read.";
    default:
      break;
  }

  switch (error.code) {
    case "permission_denied":
      return "AI Studio is available to owners and admins.";
    case "not_found":
      return isStoreNotFound(error)
        ? "That store is not connected. Choose another store."
        : "This product or version was not found.";
    case "ai_error":
      return "AI preview is unavailable right now.";
    case "shopify_publish_busy":
      return "Another publish for this product is still running. Try again in a moment.";
    case "pipeline_bulk_run_active":
      return "Another bulk optimization is already running for this workspace.";
    case "rate_limit_exceeded":
      return "Too many bulk runs were started just now. Wait a minute, then try again.";
    case "conflict":
      return "Someone else updated this product. Reload to see the latest version.";
    case "validation_error":
      return error.message;
    case "timeout":
    case "network_error":
      return error.message;
    default:
      return "Something went wrong. Please try again.";
  }
}

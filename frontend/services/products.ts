import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import { toListParams } from "@/services/list-query";
import type {
  ListQuery,
  Page,
  PipelineApproveRequest,
  PipelineBulkRun,
  PipelineBulkRunCreate,
  PipelineBulkRunItem,
  PipelinePreview,
  PipelinePreviewRequest,
  PipelinePublishRequest,
  Product,
  ProductDetail,
  ProductDuplicateCheckResponse,
  ProductImportPayload,
  ProductImportRecord,
  ProductVersion,
  ProductWorkspaceCounts,
  ShopifyPublishResult,
} from "@/types/api";

import { draftKeys } from "@/services/drafts";

/**
 * Product data access.
 *
 * These endpoints now exist, so this module has real fetchers.
 *
 * The query keys keep the hierarchy `services/users.ts` established: identical
 * shapes across services mean cache invalidation behaves predictably instead of
 * each module inventing its own convention.
 */

export const productKeys = {
  all: ["products"] as const,
  lists: () => [...productKeys.all, "list"] as const,
  list: (query: ListQuery) => [...productKeys.lists(), query] as const,
  details: () => [...productKeys.all, "detail"] as const,
  detail: (id: string) => [...productKeys.details(), id] as const,
  imports: () => [...productKeys.all, "imports"] as const,
  importList: (query: ListQuery) => [...productKeys.imports(), query] as const,
  duplicateCheck: (externalId: string) =>
    [...productKeys.all, "duplicate-check", externalId] as const,
  versions: (id: string) => [...productKeys.detail(id), "versions"] as const,
  workspaceCounts: () => [...productKeys.all, "workspace-counts"] as const,
  // AI Studio (Phase 9 Stage 10). The candidate key carries the store because
  // `channelReadiness` and `publishable` are composed for that store: another
  // store is another cache entry, never the previous store's answer.
  pipelineCandidate: (productId: string, versionId: string, storeId: string | null) =>
    [...productKeys.detail(productId), "pipeline", versionId, storeId] as const,
  pipelineRuns: () => [...productKeys.all, "pipeline-runs"] as const,
  pipelineRun: (runId: string) => [...productKeys.pipelineRuns(), runId] as const,
  pipelineRunItems: (runId: string, query: ListQuery) =>
    [...productKeys.pipelineRun(runId), "items", query] as const,
};

/**
 * Generation runs image analysis and three prompt executions inside the
 * request, and publish may wait up to 30 s for the product lock before it
 * calls Shopify. The 30 s client default would report a timeout for work that
 * is still completing on the server, so these two calls get longer budgets.
 * A timeout is still possible and is shown as "outcome unknown", never as a
 * failure.
 */
const PIPELINE_PREVIEW_TIMEOUT_MS = 120_000;
const PIPELINE_PUBLISH_TIMEOUT_MS = 90_000;

async function fetchProducts(query: ListQuery): Promise<Page<Product>> {
  const { data } = await apiClient.get<Page<Product>>("/products", {
    params: toListParams(query),
  });
  return data;
}

export function useProducts(
  query: ListQuery = {},
): UseQueryResult<Page<Product>> {
  return useQuery({
    queryKey: productKeys.list(query),
    queryFn: () => fetchProducts(query),
    // See `useDrafts`: keep the current page visible while the next loads.
    placeholderData: keepPreviousData,
  });
}

async function fetchWorkspaceCounts(): Promise<ProductWorkspaceCounts> {
  const { data } = await apiClient.get<ProductWorkspaceCounts>(
    "/products/workspace-counts",
  );
  return data;
}

/** Draft and published counts for sidebar badges. */
export function useProductWorkspaceCounts(): UseQueryResult<ProductWorkspaceCounts> {
  return useQuery({
    queryKey: productKeys.workspaceCounts(),
    queryFn: fetchWorkspaceCounts,
  });
}

async function fetchProduct(id: string): Promise<ProductDetail> {
  const { data } = await apiClient.get<ProductDetail>(`/products/${id}`);
  return data;
}

export function useProduct(id: string): UseQueryResult<ProductDetail> {
  return useQuery({
    queryKey: productKeys.detail(id),
    queryFn: () => fetchProduct(id),
    enabled: Boolean(id),
  });
}

async function fetchImports(
  query: ListQuery,
): Promise<Page<ProductImportRecord>> {
  const { data } = await apiClient.get<Page<ProductImportRecord>>(
    "/products/imports",
    { params: toListParams(query) },
  );
  return data;
}

export function useProductImports(
  query: ListQuery = {},
): UseQueryResult<Page<ProductImportRecord>> {
  return useQuery({
    queryKey: productKeys.importList(query),
    queryFn: () => fetchImports(query),
  });
}

async function fetchDuplicateCheck(
  externalId: string,
): Promise<ProductDuplicateCheckResponse> {
  const { data } = await apiClient.get<ProductDuplicateCheckResponse>(
    "/products/import/check",
    { params: { external_id: externalId } },
  );
  return data;
}

/**
 * Authoritative, tenant-scoped answer to "is this supplier product already
 * imported" — a direct server lookup, not a scan of whatever page of Drafts
 * happens to be cached client-side. `enabled` gates the call until the caller
 * has a non-empty, debounced value; this hook does not debounce on its own,
 * so every distinct `externalId` it's called with fires immediately.
 */
export function useDuplicateImportCheck(
  externalId: string,
  options: { enabled?: boolean } = {},
): UseQueryResult<ProductDuplicateCheckResponse> {
  return useQuery({
    queryKey: productKeys.duplicateCheck(externalId),
    queryFn: () => fetchDuplicateCheck(externalId),
    enabled: Boolean(externalId) && (options.enabled ?? true),
    staleTime: 10_000,
  });
}

/**
 * Import a product from AliExpress.
 *
 * Invalidates the whole product namespace on success rather than splicing the
 * new item into every cached page. An import can create *or* update a product,
 * and it changes the import history too — reconciling all of that by hand is
 * how a cache starts quietly disagreeing with the server.
 */
export function useImportProduct() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: ProductImportPayload) => {
      const { data } = await apiClient.post<ProductDetail>(
        "/products/import",
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: productKeys.all });
      void queryClient.invalidateQueries({ queryKey: draftKeys.all });
    },
  });
}

/**
 * Retry a specific failed import using its own stored parameters — the
 * merchant never re-types the product id/URL or destination.
 *
 * Same cache-invalidation shape as `useImportProduct`: a retry can create or
 * update a product and always changes import history.
 */
export function useRetryImport() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (importId: string) => {
      const { data } = await apiClient.post<ProductDetail>(
        `/products/imports/${importId}/retry`,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: productKeys.all });
      void queryClient.invalidateQueries({ queryKey: draftKeys.all });
    },
  });
}

/** Refresh price, stock and variants for a product already imported. */
export function useSyncProduct() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (productId: string) => {
      const { data } = await apiClient.post<ProductDetail>(
        `/products/${productId}/sync`,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: productKeys.all });
      void queryClient.invalidateQueries({ queryKey: draftKeys.all });
    },
  });
}

async function fetchProductVersions(id: string): Promise<Page<ProductVersion>> {
  const { data } = await apiClient.get<Page<ProductVersion>>(
    `/products/${id}/versions`,
    { params: { size: 50 } },
  );
  return data;
}

/** Version history for a product's AI optimisation — newest first. */
export function useProductVersions(
  id: string,
  options: { enabled?: boolean } = {},
): UseQueryResult<Page<ProductVersion>> {
  return useQuery({
    queryKey: productKeys.versions(id),
    queryFn: () => fetchProductVersions(id),
    enabled: Boolean(id) && (options.enabled ?? true),
  });
}

/**
 * Activate a version — including the original — rolling the product back
 * or forward to it.
 */
export function useActivateProductVersion(
  productId: string,
  options: {
    /** Runs even if the calling component unmounted while the request was in
     * flight (e.g. the history sheet was closed). A `mutate(..., { onSuccess })`
     * callback would silently not run then, and an open editor would miss the
     * new token (review finding I-1). */
    onActivated?: (product: ProductDetail) => void;
  } = {},
) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (versionId: string) => {
      const { data } = await apiClient.post<ProductDetail>(
        `/products/${productId}/versions/${versionId}/activate`,
      );
      return data;
    },
    onSuccess: (product) => {
      options.onActivated?.(product);
      void queryClient.invalidateQueries({
        queryKey: productKeys.detail(productId),
      });
      void queryClient.invalidateQueries({
        queryKey: productKeys.versions(productId),
      });
      void queryClient.invalidateQueries({ queryKey: productKeys.lists() });
      // The draft editor reads the same Product row through `draftKeys`.
      // This changes its `updatedAt`, so the draft cache must not keep the
      // old token (review finding I-1). An open editor adopts the new token
      // from the response itself; a refetch alone never re-hydrates it.
      void queryClient.invalidateQueries({
        queryKey: draftKeys.detail(productId),
      });
    },
  });
}

// ---------------------------------------------------------------------------
// AI Studio — Phase 9 Stage 10 (PHASE_9_STAGE_10_PLAN.md §27)
// ---------------------------------------------------------------------------

/** Every product cache the pipeline's product-row writes can make stale. */
function invalidateProductRow(queryClient: QueryClient, productId: string): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: productKeys.detail(productId), exact: true }),
    queryClient.invalidateQueries({ queryKey: productKeys.versions(productId) }),
    queryClient.invalidateQueries({ queryKey: productKeys.lists() }),
    // The draft editor reads the same Product row through `draftKeys`; an
    // approval moves its `updatedAt` (review finding I-1, plan §27a).
    queryClient.invalidateQueries({ queryKey: draftKeys.detail(productId) }),
  ]);
}

/** Generate a new inactive candidate. Never approves, never publishes. */
export function usePipelinePreview(productId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: PipelinePreviewRequest) => {
      const { data } = await apiClient.post<PipelinePreview>(
        `/products/${productId}/pipeline/preview`,
        payload,
        { timeout: PIPELINE_PREVIEW_TIMEOUT_MS },
      );
      return data;
    },
    onSuccess: (preview, payload) => {
      // Seed exactly the entry the page reads next, for the store that was
      // sent, so the following render does not GET what was just returned.
      queryClient.setQueryData(
        productKeys.pipelineCandidate(productId, preview.candidateVersionId, payload.storeId ?? null),
        preview,
      );
      // A first preview snapshots the original; history gains a row.
      void queryClient.invalidateQueries({ queryKey: productKeys.versions(productId) });
    },
  });
}

/** Read one exact candidate composed for one store (plan §6). */
export function usePipelineCandidate(
  productId: string,
  versionId: string | null,
  storeId: string | null,
): UseQueryResult<PipelinePreview> {
  return useQuery({
    queryKey: productKeys.pipelineCandidate(productId, versionId ?? "", storeId),
    queryFn: async () => {
      const { data } = await apiClient.get<PipelinePreview>(
        `/products/${productId}/pipeline/versions/${versionId}/preview`,
        { params: storeId ? { storeId } : {} },
      );
      return data;
    },
    enabled: Boolean(productId) && Boolean(versionId),
    // A 4xx here is a classification (not a pipeline candidate, gone), not a
    // blip; retrying would only delay the panel that explains it.
    retry: false,
  });
}

/**
 * Approve exactly one candidate with the inactive approval token (T0).
 *
 * Invalidation lives on the hook, not on `mutate(…, { onSuccess })`: a
 * per-call callback is skipped if the page unmounts mid-request, and the
 * editor's token would then go stale (review finding I-1).
 */
export function useApprovePipelineCandidate(productId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (input: { versionId: string; expectedUpdatedAt: string }) => {
      const body: PipelineApproveRequest = { expectedUpdatedAt: input.expectedUpdatedAt };
      const { data } = await apiClient.post<ProductDetail>(
        `/products/${productId}/pipeline/versions/${input.versionId}/approve`,
        body,
      );
      return data;
    },
    onSuccess: async (_product, input) => {
      await invalidateProductRow(queryClient, productId);
      // Every store's composition of this candidate is now out of date.
      await queryClient.invalidateQueries({
        queryKey: [...productKeys.detail(productId), "pipeline", input.versionId],
      });
    },
  });
}

/** Publish an approved candidate through the existing Shopify publisher. */
export function usePublishPipelineCandidate(productId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (input: { versionId: string; storeId: string; expectedUpdatedAt: string }) => {
      const body: PipelinePublishRequest = {
        storeId: input.storeId,
        expectedUpdatedAt: input.expectedUpdatedAt,
      };
      const { data } = await apiClient.post<ShopifyPublishResult>(
        `/products/${productId}/pipeline/versions/${input.versionId}/publish`,
        body,
        { timeout: PIPELINE_PUBLISH_TIMEOUT_MS },
      );
      return data;
    },
    // Settled, not success: a timed-out publish may still have reached
    // Shopify, so what the store shows is re-read either way.
    onSettled: async (_result, _error, input) => {
      await Promise.all([
        invalidateProductRow(queryClient, productId),
        queryClient.invalidateQueries({ queryKey: draftKeys.listings(productId) }),
        queryClient.invalidateQueries({
          queryKey: [...productKeys.detail(productId), "pipeline", input.versionId],
        }),
      ]);
    },
  });
}

/** Start a bulk preview run (Stage 9). 202 with the run; replays on the same key. */
export function useStartPipelineRun() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: PipelineBulkRunCreate) => {
      const { data } = await apiClient.post<PipelineBulkRun>("/products/pipeline/runs", payload);
      return data;
    },
    onSuccess: (run) => {
      queryClient.setQueryData(productKeys.pipelineRun(run.id), run);
    },
  });
}

/**
 * One bulk run, polled every 5 s until it reaches a terminal status (plan
 * §20). Unmounting the page drops the observer and so stops the interval.
 */
export function usePipelineRun(runId: string | null): UseQueryResult<PipelineBulkRun> {
  return useQuery({
    queryKey: productKeys.pipelineRun(runId ?? ""),
    queryFn: async () => {
      const { data } = await apiClient.get<PipelineBulkRun>(`/products/pipeline/runs/${runId}`);
      return data;
    },
    enabled: Boolean(runId),
    retry: false,
    refetchInterval: (query) => {
      const run = query.state.data;
      if (!run) return false;
      return run.status === "pending" || run.status === "running" ? 5_000 : false;
    },
    refetchIntervalInBackground: false,
  });
}

export function usePipelineRunItems(
  runId: string | null,
  query: ListQuery,
  options: { refetchInterval?: number | false } = {},
): UseQueryResult<Page<PipelineBulkRunItem>> {
  return useQuery({
    queryKey: productKeys.pipelineRunItems(runId ?? "", query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<PipelineBulkRunItem>>(
        `/products/pipeline/runs/${runId}/items`,
        { params: toListParams(query) },
      );
      return data;
    },
    enabled: Boolean(runId),
    placeholderData: keepPreviousData,
    refetchInterval: options.refetchInterval ?? false,
    refetchIntervalInBackground: false,
  });
}

/** Cancel is cooperative (H-2): it may return `running` + `cancelRequestedAt`. */
export function useCancelPipelineRun(runId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post<PipelineBulkRun>(
        `/products/pipeline/runs/${runId}/cancel`,
      );
      return data;
    },
    onSuccess: (run) => {
      queryClient.setQueryData(productKeys.pipelineRun(run.id), run);
      void queryClient.invalidateQueries({ queryKey: productKeys.pipelineRun(run.id) });
    },
  });
}

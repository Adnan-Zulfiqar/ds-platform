import { Skeleton } from "@/components/ui/skeleton";

/**
 * Loading state for authenticated routes.
 *
 * Next.js shows this automatically while a route segment resolves. It sits
 * inside the shell, so the sidebar and top bar stay put and only the content
 * area changes — the page does not appear to reload.
 *
 * The skeleton mirrors the dashboard's real proportions rather than being a
 * generic spinner, so content replaces it without the layout jumping.
 */
export default function ProtectedLoading() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading">
      <div className="space-y-2">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-4 w-80" />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} className="h-[118px] w-full rounded-lg" />
        ))}
      </div>

      <Skeleton className="h-[400px] w-full rounded-lg" />
    </div>
  );
}

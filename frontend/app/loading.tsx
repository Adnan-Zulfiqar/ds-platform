import { Skeleton } from "@/components/ui/skeleton";

/**
 * Route-level loading state.
 *
 * Next.js shows this automatically while a server component streams. The
 * skeleton mirrors the dashboard's real layout so the page does not jump when
 * content replaces it.
 */
export default function Loading() {
  return (
    <div className="space-y-6 p-6" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-8 w-64" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-28 w-full" />
        ))}
      </div>
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

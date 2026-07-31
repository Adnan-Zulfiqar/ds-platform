import { Construction } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";

interface ComingSoonProps {
  title: string;
  description: string;
  /** What the page will do once built. Concrete beats vague. */
  planned?: readonly string[];
}

/**
 * Placeholder for a route whose feature has not been built.
 *
 * **It says so plainly.** A placeholder that mimics a working page — fake rows,
 * disabled buttons, a spinner that never resolves — wastes the user's time and
 * erodes trust in everything around it. Stating the truth costs nothing and
 * keeps the rest of the product credible.
 *
 * Used by routes that exist so navigation never 404s, but whose feature belongs
 * to a later phase.
 */
export function ComingSoon({ title, description, planned }: ComingSoonProps) {
  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader title={title} description={description} />

      <EmptyState
        icon={Construction}
        title="Not available yet"
        description="This section is part of an upcoming release. Nothing here is functional."
        action={
          <Button asChild variant="outline">
            <Link href="/dashboard">Back to dashboard</Link>
          </Button>
        }
      />

      {planned && planned.length > 0 && (
        <div className="rounded-lg border p-5">
          <h2 className="text-sm font-semibold">Planned for this section</h2>
          <ul className="mt-3 space-y-2">
            {planned.map((entry) => (
              <li
                key={entry}
                className="flex items-start gap-2 text-sm text-muted-foreground"
              >
                <span
                  aria-hidden="true"
                  className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/40"
                />
                {entry}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

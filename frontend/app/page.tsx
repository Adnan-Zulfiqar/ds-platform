import { redirect } from "next/navigation";

/**
 * Root route.
 *
 * The application has no marketing surface — that lives on a separate site — so
 * `/` forwards to the dashboard. Once authentication exists, the redirect
 * target becomes conditional: signed-in users to `/dashboard`, everyone else to
 * `/login`.
 */
export default function RootPage() {
  redirect("/dashboard");
}

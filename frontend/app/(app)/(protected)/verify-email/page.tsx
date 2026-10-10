import type { Metadata } from "next";

import { VerifyEmail } from "@/components/profile/verify-email";

export const metadata: Metadata = { title: "Verify email" };

/** Target of the link in the verification email. Protected: the token is
 * consumed for the signed-in user, so an unsigned visitor is sent to sign in
 * first and returned here with the fragment intact. */
export default function VerifyEmailPage() {
  return <VerifyEmail />;
}

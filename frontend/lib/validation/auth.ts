import { z } from "zod";

/**
 * Client-side validation schemas.
 *
 * **These mirror the server's rules; they do not replace them.** Anything here
 * can be bypassed by calling the API directly, so the backend re-validates
 * everything. The value of duplicating the rules is feedback speed: a user
 * learns their password is too short as they type, not after a round trip.
 *
 * Where the two could drift, the server wins. The password rules below match
 * `SecuritySettings` defaults in `backend/app/core/config.py`; if those are
 * reconfigured, the server will reject input this file accepted — a worse error
 * message, but never a security hole.
 */

const PASSWORD_MIN_LENGTH = 12;
const PASSWORD_MAX_LENGTH = 128;

export const emailSchema = z
  .string()
  .min(1, "Email address is required")
  .email("Enter a valid email address")
  // The server stores addresses lowercased, so normalising here keeps what the
  // user sees consistent with what is stored.
  .transform((value) => value.trim().toLowerCase());

export const passwordSchema = z
  .string()
  .min(PASSWORD_MIN_LENGTH, `Password must be at least ${PASSWORD_MIN_LENGTH} characters`)
  .max(PASSWORD_MAX_LENGTH, `Password must be at most ${PASSWORD_MAX_LENGTH} characters`)
  .regex(/[a-z]/, "Password must contain a lowercase letter")
  .regex(/[A-Z]/, "Password must contain an uppercase letter")
  .regex(/[0-9]/, "Password must contain a digit");

export const loginSchema = z.object({
  email: emailSchema,
  // Deliberately only "required" — applying the full policy to a sign-in form
  // would reject a legitimate password set before the rules changed, and tell
  // an attacker the current rules for free.
  password: z.string().min(1, "Password is required"),
  rememberMe: z.boolean().default(false),
});

export type LoginFormValues = z.infer<typeof loginSchema>;

export const registerSchema = z
  .object({
    companyName: z
      .string()
      .trim()
      .min(1, "Company name is required")
      .max(255, "Company name is too long"),
    firstName: z.string().trim().max(128, "First name is too long").optional(),
    lastName: z.string().trim().max(128, "Last name is too long").optional(),
    email: emailSchema,
    password: passwordSchema,
    confirmPassword: z.string().min(1, "Confirm your password"),
  })
  // Cross-field checks must be a refinement on the object: a field-level rule
  // cannot see its siblings.
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords do not match",
    // Attaching the error to the field the user must fix, rather than to the
    // form root where it would appear disconnected from any input.
    path: ["confirmPassword"],
  });

export type RegisterFormValues = z.infer<typeof registerSchema>;

/** Track E4: accepting a team invitation. The address comes from the link. */
export const acceptInvitationSchema = z
  .object({
    firstName: z.string().trim().max(128, "First name is too long").optional(),
    lastName: z.string().trim().max(128, "Last name is too long").optional(),
    password: passwordSchema,
    confirmPassword: z.string().min(1, "Confirm your password"),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords do not match",
    path: ["confirmPassword"],
  });

export type AcceptInvitationFormValues = z.infer<typeof acceptInvitationSchema>;

export const forgotPasswordSchema = z.object({
  email: emailSchema,
});

export type ForgotPasswordFormValues = z.infer<typeof forgotPasswordSchema>;

/**
 * Rough password strength, for the meter on the registration form.
 *
 * Deliberately simple and advisory. It is not a gate — `passwordSchema` is —
 * and it does not attempt real entropy estimation, which needs a dictionary far
 * too large to ship to the browser.
 */
export function estimatePasswordStrength(password: string): {
  score: 0 | 1 | 2 | 3 | 4;
  label: string;
} {
  if (!password) return { score: 0, label: "" };

  let score = 0;
  if (password.length >= PASSWORD_MIN_LENGTH) score++;
  if (password.length >= 16) score++;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score++;
  if (/[0-9]/.test(password)) score++;
  if (/[^A-Za-z0-9]/.test(password)) score++;

  const clamped = Math.min(score, 4) as 0 | 1 | 2 | 3 | 4;
  const labels = ["Very weak", "Weak", "Fair", "Good", "Strong"] as const;
  return { score: clamped, label: labels[clamped] };
}

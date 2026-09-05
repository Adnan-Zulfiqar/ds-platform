import { expect, test } from "@playwright/test";

import { resolveE2eApiUrl } from "./helpers/auth";

/**
 * Guards against the R4 review incident: Playwright must not fall back to
 * `:8000` / production when NEXT_PUBLIC_API_URL is unset or unsafe.
 */
test.describe("E2E API isolation guard", () => {
  test("refuses an empty API URL instead of defaulting to :8000", () => {
    expect(() => resolveE2eApiUrl({})).toThrow(/explicitly/i);
  });

  test("refuses localhost:8000 without an explicit CI allow flag", () => {
    expect(() =>
      resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: "http://localhost:8000" }),
    ).toThrow(/refusing/i);
    expect(() =>
      resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000" }),
    ).toThrow(/refusing/i);
  });

  test("refuses the production API host", () => {
    expect(() =>
      resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: "https://api.whiteto.com" }),
    ).toThrow(/refusing/i);
  });

  test("accepts an isolated non-8000 URL", () => {
    expect(
      resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: "http://127.0.0.1:8105" }),
    ).toBe("http://127.0.0.1:8105");
  });

  test("allows CI ephemeral :8000 only with E2E_ALLOW_LOCAL_8000=1", () => {
    expect(
      resolveE2eApiUrl({
        NEXT_PUBLIC_API_URL: "http://localhost:8000",
        E2E_ALLOW_LOCAL_8000: "1",
      }),
    ).toBe("http://localhost:8000");
  });
});

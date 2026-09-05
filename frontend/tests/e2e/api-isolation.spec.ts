import { expect, test } from "@playwright/test";

import {
  E2eIsolationError,
  isApiReachable,
  peekE2eApiOriginLogForTests,
  resetE2eApiOriginLogForTests,
  resolveE2eApiUrl,
} from "./helpers/auth";

/**
 * Guards against the R4 review incident and closes R5 isolation gaps:
 * Playwright must not fall back to `:8000` / production, and isolation
 * failures must not look like a quiet “API not reachable” skip.
 */

const SAFE_ORIGINS = [
  "http://127.0.0.1:8100",
  "http://127.0.0.1:8105",
  "http://127.0.0.1:8199",
  "http://127.0.0.1:8199/",
] as const;

const UNSAFE_ORIGINS = [
  "",
  "   ",
  "not-a-url",
  "https://api.whiteto.com",
  "http://api.whiteto.com",
  "https://api.whiteto.com:443",
  "https://whiteto.com",
  "http://localhost:8000",
  "http://localhost:8000/api",
  "http://127.0.0.1:8000",
  "http://127.0.0.1:8000/",
  "http://127.0.0.1:8000/api",
  "http://127.1:8000",
  "http://0.0.0.0:8000",
  "http://[::1]:8000",
  "http://127.0.0.1",
  "https://127.0.0.1:8105",
  "http://127.0.0.1:8105/api",
  "http://user:password@127.0.0.1:8105",
  "http://127.0.0.1:8105?x=1",
  "http://127.0.0.1:8105#x",
] as const;

test.describe("E2E API isolation guard", () => {
  test.beforeEach(() => {
    resetE2eApiOriginLogForTests();
  });

  test("safe local isolated origins pass", () => {
    for (const raw of SAFE_ORIGINS) {
      const resolved = resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: raw });
      expect(resolved).toMatch(/^http:\/\/127\.0\.0\.1:81\d{2}$/);
    }
  });

  for (const raw of UNSAFE_ORIGINS) {
    test(`refuses unsafe origin: ${JSON.stringify(raw)}`, () => {
      expect(() => resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: raw })).toThrow(
        E2eIsolationError,
      );
      expect(peekE2eApiOriginLogForTests().logged).toBe(false);
    });
  }

  test("E2E_ALLOW_LOCAL_8000 alone does not unlock :8000", () => {
    expect(() =>
      resolveE2eApiUrl({
        NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
        E2E_ALLOW_LOCAL_8000: "1",
      }),
    ).toThrow(E2eIsolationError);
  });

  test("CI=true + override without GITHUB_ACTIONS fails", () => {
    expect(() =>
      resolveE2eApiUrl({
        NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
        CI: "true",
        E2E_ALLOW_LOCAL_8000: "1",
      }),
    ).toThrow(E2eIsolationError);
  });

  test("GITHUB_ACTIONS=true + override without CI fails", () => {
    expect(() =>
      resolveE2eApiUrl({
        NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
        GITHUB_ACTIONS: "true",
        E2E_ALLOW_LOCAL_8000: "1",
      }),
    ).toThrow(E2eIsolationError);
  });

  test("full CI proof accepts only exact http://127.0.0.1:8000", () => {
    expect(
      resolveE2eApiUrl({
        NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
        CI: "true",
        GITHUB_ACTIONS: "true",
        E2E_ALLOW_LOCAL_8000: "1",
      }),
    ).toBe("http://127.0.0.1:8000");
  });

  test("full CI proof still rejects localhost:8000 and public domains", () => {
    const proof = {
      CI: "true",
      GITHUB_ACTIONS: "true",
      E2E_ALLOW_LOCAL_8000: "1",
    };
    expect(() =>
      resolveE2eApiUrl({
        ...proof,
        NEXT_PUBLIC_API_URL: "http://localhost:8000",
      }),
    ).toThrow(E2eIsolationError);
    expect(() =>
      resolveE2eApiUrl({
        ...proof,
        NEXT_PUBLIC_API_URL: "https://api.whiteto.com",
      }),
    ).toThrow(E2eIsolationError);
    expect(() =>
      resolveE2eApiUrl({
        ...proof,
        NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000/api",
      }),
    ).toThrow(E2eIsolationError);
  });

  test("valid origin is logged once per process", () => {
    const lines: string[] = [];
    const original = console.log;
    console.log = (...args: unknown[]) => {
      lines.push(args.map(String).join(" "));
    };
    try {
      resetE2eApiOriginLogForTests();
      resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: "http://127.0.0.1:8106" });
      resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: "http://127.0.0.1:8106" });
      expect(lines.filter((l) => l.startsWith("[E2E] API origin:"))).toEqual([
        "[E2E] API origin: http://127.0.0.1:8106",
      ]);
      expect(peekE2eApiOriginLogForTests()).toEqual({
        logged: true,
        origin: "http://127.0.0.1:8106",
      });
    } finally {
      console.log = original;
    }
  });

  test("unsafe origin is not logged as accepted", () => {
    const lines: string[] = [];
    const original = console.log;
    console.log = (...args: unknown[]) => {
      lines.push(args.map(String).join(" "));
    };
    try {
      resetE2eApiOriginLogForTests();
      expect(() =>
        resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: "http://localhost:8000" }),
      ).toThrow(E2eIsolationError);
      expect(lines.some((l) => l.includes("[E2E] API origin:"))).toBe(false);
    } finally {
      console.log = original;
    }
  });

  test("missing configuration throws rather than skips via isApiReachable", async () => {
    const previous = process.env.NEXT_PUBLIC_API_URL;
    const previousE2e = process.env.E2E_API_URL;
    delete process.env.NEXT_PUBLIC_API_URL;
    delete process.env.E2E_API_URL;
    try {
      await expect(isApiReachable()).rejects.toBeInstanceOf(E2eIsolationError);
    } finally {
      if (previous === undefined) delete process.env.NEXT_PUBLIC_API_URL;
      else process.env.NEXT_PUBLIC_API_URL = previous;
      if (previousE2e === undefined) delete process.env.E2E_API_URL;
      else process.env.E2E_API_URL = previousE2e;
    }
  });

  test("unsafe configuration throws rather than skips via isApiReachable", async () => {
    const previous = process.env.NEXT_PUBLIC_API_URL;
    process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000";
    try {
      await expect(isApiReachable()).rejects.toBeInstanceOf(E2eIsolationError);
    } finally {
      if (previous === undefined) delete process.env.NEXT_PUBLIC_API_URL;
      else process.env.NEXT_PUBLIC_API_URL = previous;
    }
  });

  test("safe-but-unreachable isolated API returns false without throwing", async () => {
    const previous = process.env.NEXT_PUBLIC_API_URL;
    // Port in range but nothing listening — connection error, not isolation.
    process.env.NEXT_PUBLIC_API_URL = "http://127.0.0.1:8198";
    try {
      await expect(isApiReachable()).resolves.toBe(false);
    } finally {
      if (previous === undefined) delete process.env.NEXT_PUBLIC_API_URL;
      else process.env.NEXT_PUBLIC_API_URL = previous;
    }
  });

  test("validation makes zero network requests", async () => {
    const originalFetch = globalThis.fetch;
    let fetchCalls = 0;
    globalThis.fetch = (async () => {
      fetchCalls += 1;
      throw new Error("fetch must not run during URL validation");
    }) as typeof fetch;
    try {
      expect(() =>
        resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: "https://api.whiteto.com" }),
      ).toThrow(E2eIsolationError);
      expect(() =>
        resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000" }),
      ).toThrow(E2eIsolationError);
      expect(
        resolveE2eApiUrl({ NEXT_PUBLIC_API_URL: "http://127.0.0.1:8110" }),
      ).toBe("http://127.0.0.1:8110");
      expect(fetchCalls).toBe(0);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

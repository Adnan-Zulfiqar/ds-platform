import { describe, expect, it } from "vitest";

import { externalLinkRel, isTrustedShopifyHttpsUrl } from "@/lib/external-link";

describe("isTrustedShopifyHttpsUrl", () => {
  it("accepts a genuine myshopify.com storefront URL", () => {
    expect(
      isTrustedShopifyHttpsUrl("https://demo-shop.myshopify.com/products/lamp"),
    ).toBe(true);
  });

  it("rejects http", () => {
    expect(isTrustedShopifyHttpsUrl("http://demo-shop.myshopify.com/products/lamp")).toBe(
      false,
    );
  });

  it("rejects javascript URLs", () => {
    expect(isTrustedShopifyHttpsUrl("javascript:alert(1)")).toBe(false);
  });

  it("rejects data URLs", () => {
    expect(isTrustedShopifyHttpsUrl("data:text/html,hello")).toBe(false);
  });

  it("rejects userinfo URLs", () => {
    expect(
      isTrustedShopifyHttpsUrl("https://user:pass@demo-shop.myshopify.com/products/lamp"),
    ).toBe(false);
  });

  it("rejects evilmyshopify.com", () => {
    expect(isTrustedShopifyHttpsUrl("https://evilmyshopify.com/products/lamp")).toBe(
      false,
    );
  });

  it("rejects myshopify.com.evil.example", () => {
    expect(
      isTrustedShopifyHttpsUrl("https://demo.myshopify.com.evil.example/products/lamp"),
    ).toBe(false);
  });

  it("rejects bare myshopify.com", () => {
    expect(isTrustedShopifyHttpsUrl("https://myshopify.com/products/lamp")).toBe(false);
  });

  it("rejects punycode/confusable host tricks", () => {
    expect(
      isTrustedShopifyHttpsUrl("https://demo-shop.myshopify.com.evil.example/products/lamp"),
    ).toBe(false);
    expect(
      isTrustedShopifyHttpsUrl("https://notshopify.example/products/lamp"),
    ).toBe(false);
  });

  it("rejects unexpected ports", () => {
    expect(
      isTrustedShopifyHttpsUrl("https://demo-shop.myshopify.com:8443/products/lamp"),
    ).toBe(false);
  });

  it("rejects malformed URLs", () => {
    expect(isTrustedShopifyHttpsUrl("not-a-url")).toBe(false);
    expect(isTrustedShopifyHttpsUrl(null)).toBe(false);
  });

  it("returns noopener noreferrer rel helper", () => {
    expect(externalLinkRel()).toBe("noopener noreferrer");
  });
});

import { describe, expect, it } from "vitest";

import {
  editorTabForSection,
  isKnownEditorSection,
  sellerSectionLabel,
} from "@/lib/editor-section-labels";

describe("editor section labels", () => {
  it("maps known server sections to seller labels", () => {
    expect(sellerSectionLabel("publishing")).toBe("Store connection");
    expect(sellerSectionLabel("media")).toBe("Images");
    expect(sellerSectionLabel("overview")).toBe("Product details");
  });

  it("uses safe fallback for unknown sections", () => {
    expect(sellerSectionLabel("unknown_internal_key")).toBe("Review product");
    expect(sellerSectionLabel(null)).toBe("Review product");
  });

  it("resolves editor tabs only for validated sections", () => {
    expect(editorTabForSection("media")).toBe("media");
    expect(editorTabForSection("not_a_tab")).toBeNull();
    expect(isKnownEditorSection("pricing")).toBe(true);
    expect(isKnownEditorSection("mystery")).toBe(false);
  });
});

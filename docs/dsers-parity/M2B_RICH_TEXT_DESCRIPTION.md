# M2B — Rich-Text Product Description

Status: **Delivered.** Branch: `feature/dsers-parity-m2b-rich-text-v2`.
Built: 2026-08-17. Baseline: `450bb98` (`origin/develop`, M2A integrated).

---

## 1. What this milestone is

Before M2B the merchant description was edited in a `<textarea>` showing raw
HTML source, with a separate read-only "HTML preview" pane beneath it. That
is a developer's tool, not a merchant's: a shop owner writing a product
description should not be typing `<p>` and `<strong>`.

M2B replaces the editing surface with TipTap (ProseMirror) and leaves
everything underneath it alone. **The stored format is unchanged** —
sanitized HTML in the existing `products.description` column — so no
migration, no backfill, no change to the Shopify publish path, and every
description written before M2B keeps working.

What M2B is **not**: it adds no image insertion or upload. That is M2D-A and
is deliberately out of scope here. Images already present in an imported
supplier description are preserved (§5), which is a different thing from
being able to add one.

---

## 2. The defect this milestone fixes

The user-visible change is the editor. The more consequential change is one
line of ordering in `ProductService.update_product`.

M2A added a no-op filter: a save whose values all match the stored row is
dropped before any `UPDATE`, because issuing the statement anyway would still
bump `updated_at` (Postgres fires `onupdate` for every `UPDATE`, changed
values or not) and spuriously invalidate every other open editor's version
token.

That filter compared the **raw submission** against the stored value.
Sanitization ran afterwards, on whatever survived the filter. With a
`<textarea>` that was mostly harmless — the merchant typed the exact bytes
they saw. With a WYSIWYG editor it is not, because ProseMirror re-serialises
the document on every keystroke:

| Stored (sanitizer output) | Editor re-serialisation | Same document? |
|---|---|---|
| `<p>Line<br>Break</p>` | `<p>Line<br />Break</p>` | yes |
| `<p>Text</p>` | `<p class="ProseMirror-trailing">Text</p>` | yes |
| `<a href="…" rel="noopener noreferrer nofollow">` | `<a target="_blank" rel="…" href="…">` | yes |

Every one of those reads as "changed" byte-for-byte and as "identical" once
sanitized. Under the old ordering each would write an identical value and
move `updated_at`. Autosave fires on a 1.8s debounce, so this was not a rare
event — it was a steady stream of pointless writes, each one able to hand a
409 to a second tab that had made a real edit.

**The fix** (`app/services/product.py`): sanitize `description` into a
normalised copy of `changes` *first*, then diff. Nine lines, and the reason
it is safe is that `sanitize_html` is a canonicalisation — deterministic, and
a fixed point on its own output. That property is now asserted directly
(`TestEditorRoundTripCanonicalisation` in `tests/unit/test_html_sanitizer.py`)
rather than assumed, because the no-op filter's correctness rests entirely on
it.

### Red-before / green-after

`test_a_no_op_description_save_is_not_a_conflict_against_a_stale_token`
was run against the pre-M2B ordering with the fix reverted and the test file
otherwise identical:

```
FAILED …::test_a_no_op_description_save_is_not_a_conflict_against_a_stale_token
AssertionError: {"code":"conflict","message":"This item was changed since you loaded it…"}
assert 409 == 200
1 failed, 29 passed
```

With the fix restored: `30 passed`. Same assertions both times.

Why that specific test and not its simpler siblings: inside a single test
transaction Postgres's `now()` is frozen, so an `UPDATE` can land without
`updated_at` appearing to move. A test that only asserts "`updatedAt` did not
change" therefore **passes on the broken code too** — and the three
parametrised cases above it did. Staling the token first removes the
ambiguity: reaching the compare-and-swap at all must produce a 409, so a 200
proves the no-op filter short-circuited first. This is the only assertion in
the file that can distinguish the two orderings, and it is the reason the
others are not sufficient on their own.

---

## 2b. Two further defects, found live and fixed

Both were in the editor, both invisible to the backend suite (they live in
ProseMirror's parse step), and both were found by running the app against a
real draft rather than by reading the code. Recorded in full because the
first one destroyed merchant data.

### Supplier images were being deleted on load

ProseMirror silently discards any node its schema cannot express. The
editor's first schema was StarterKit plus links — no image node — so
**opening the Description tab stripped every `<img>` from the description**,
and the autosave below then wrote the stripped copy back. Supplier
descriptions are mostly images. Observed directly: a seeded description
went in as

```
<p>…</p><img src="https://ae01.alicdn.com/kf/seed-one.jpg" alt="…">
```

and the PATCH that fired ~2.6s after page load carried only `<p>…</p>`.
Nobody had touched the page.

The mistake was treating the editor's schema as "what the toolbar can
create". It also has to be "what can exist in a stored description", and
that set is defined by `app/core/sanitize.py`, not by the toolbar. The
schema now covers the sanitizer's allowlist: images, tables, and all six
heading levels (only H2/H3 are offered as buttons). M2B still adds no way
to *insert* an image — preserving one and creating one are different
guarantees.

### Opening a draft marked it unsaved and autosaved it

Even with a complete schema, ProseMirror emits update events for its own
normalisation as well as for edits — a table gains a `<colgroup>`, a
document ending in a table gains a trailing paragraph. `setContent(...,
{ emitUpdate: false })` suppresses the load transaction itself but not the
follow-up transactions plugins append after it. The parent counted those as
merchant edits, so every page view dirtied the form and fired an autosave:
`updatedAt` moved, and every other open tab's version token was invalidated,
for a draft nobody had edited.

Fixed by classifying edits from *evidence of input* rather than from
document change: `handleDOMEvents` (keydown, beforeinput, compositionstart,
paste, cut, drop) and toolbar commands set a flag, and `onUpdate` only
propagates to the parent when it is set. Time-based suppression ("ignore
updates for the first N ms") was rejected as a race against the merchant.

Both are covered by `loading a draft does not mark it unsaved` and
`supplier images and tables survive being opened and edited`, which seed
the hard case through the API first so they cannot pass on markup that
happens to round-trip byte-identically.

---

## 3. The editor

`frontend/components/drafts/rich-text-description-editor.tsx`. TipTap 3.30.1
(`@tiptap/react`, `@tiptap/starter-kit`, `@tiptap/extension-link`).

**Toolbar:** bold, italic, underline, strikethrough; H2/H3; bulleted list,
numbered list, blockquote; link/unlink; clear formatting; undo/redo.

**Deliberately absent from the schema:** code blocks and horizontal rules
(neither belongs in a product description; a code block there is almost
always a paste accident), H1 (the product title is the page's H1), and any
node or attribute that could express a script, an iframe, or inline styling.
The editor cannot *produce* those, which narrows the blast radius without
being relied on as a security control — see §4.

### Three implementation decisions worth recording

**`immediatelyRender: false`.** `next.config.ts` sets
`output: "standalone"`, so every page server-renders first. Without this flag
TipTap renders on the server and disagrees with the client's first pass —
a hydration mismatch, not a cosmetic one.

**Toolbar buttons suppress `mousedown` and act on `click`.** Two
requirements pull in opposite directions: a mouse click moves focus to the
button and collapses the editor's selection, so "select a word, press Bold"
would bold nothing — which argues for acting on `mousedown`; but a keyboard
user never fires `mousedown` at all, so acting there makes the whole toolbar
reachable by Tab and inert on Enter. Preventing the default on `mousedown`
(stopping the focus shift) while acting on `click` satisfies both. Neither
alone does. Covered by
`the toolbar is fully reachable and operable from the keyboard`.

**Content is adopted from the parent only when genuinely new.** The
component tracks what it last emitted (`lastEmitted`) and re-sets the
document only when the incoming value differs from *that*, not from the
editor's current HTML. Comparing against the editor's HTML would re-set
content on nearly every render — TipTap normalises on parse, so a round-trip
is rarely byte-identical to what the server holds — and re-setting content
destroys the cursor position mid-sentence. `setContent` is called with
`emitUpdate: false`, so hydrating a draft cannot mark it dirty; that is
asserted end-to-end by `loading a draft does not mark it unsaved`, which
also counts PATCH requests to prove no autosave fired.

### Link editing

An inline URL row inside the editor shell, not `window.prompt` and not a
modal dialog. The prompt is unstyleable, blocks the page, reads badly to a
screen reader, and is suppressed outright in some embedded contexts. A modal
would steal focus and discard the selection the link is meant to wrap — the
same problem the `mousedown` handling exists to solve. Apply stays disabled
until the URL matches `https?://`, mirroring the server's allowed schemes;
the server remains the authority, this only avoids offering to build a link
it would strip.

---

## 4. Security

**The server is the only sanitization boundary.** `nh3` (Rust `ammonia`) in
`app/core/sanitize.py`, applied on every write regardless of client, exactly
as it already was for supplier HTML. The editor sanitizes nothing, and this
is not an oversight: anything a browser does can be bypassed by calling the
API directly, so a client-side cleaner would be a convenience at best and a
false sense of safety at worst.

No regular expressions are used for sanitization. `nh3` is a real HTML
parser working from an explicit tag/attribute **allowlist**, which is what
makes it resistant to the case, encoding, and nesting tricks that defeat
pattern-matching filters.

Fourteen hostile payloads are asserted against **the database row**, not the
response body, in
`tests/integration/test_draft_rich_text_description.py::TestMerchantHtmlIsSanitizedOnTheDraftEndpoint`:
`<script>`, `<iframe>`, `<style>`, `onerror`, `onclick`, `<form>`,
`<svg onload>`, `style` attribute, `data:` image src, `vbscript:` href, and
`javascript:` in four spellings — plain, mixed case, and two entity encodings
(`java&#115;cript:`, `&#106;avascript:`). Each case also asserts the visible
text survived, so none of them can pass by the field being emptied.

Paste is covered separately in Playwright
(`pasted hostile markup cannot introduce a script or an unsafe link`), which
dispatches a real `ClipboardEvent` and then checks that no marker global was
set, no `script`/`iframe` node exists, and no `javascript:` href survived —
before *and* after a real save and reload.

Tenant isolation on this path is asserted directly: another tenant's draft
returns **404, not 403**, and the row is unchanged.

Supplier snapshots stay immutable. `supplier_description` is never written by
this path, asserted in both the M2A concurrency suite and again here.

---

## 5. Imported images are preserved

Supplier descriptions are mostly images. The sanitizer allows `<img>` with
`src`/`alt`/`title` on `http`/`https`, so they survive a merchant edit
untouched — asserted by `TestImportedImagesSurviveMerchantEditing`, including
that re-saving an image-only description is still a no-op and that an unsafe
`src` alongside safe ones is the only thing dropped.

The editor's helper text says exactly this ("Existing images are kept.
Scripts, custom styling and unsafe links are removed when the draft is
saved."). An earlier draft of that string said images were removed, which
would have been false and read as a warning.

---

## 6. Size limit

`DESCRIPTION_MAX_LENGTH = 64_000` characters, in `app/schemas/product.py`,
enforced as a Pydantic `max_length` on `ProductUpdateRequest.description`
(422 when exceeded) and mirrored in the editor as a live character counter
plus an over-limit alert.

**Why 64,000.** Shopify's product `body_html` — which
`integrations/shopify/sync.py` publishes this field into — is documented at
65,535 characters. Accepting more here would mean silently truncating at
publish, which is the worst of both outcomes. The limit sits a little below
that ceiling to leave room for theme wrapper markup.

**Measured on the submitted markup, before sanitization**, for two reasons:
sanitizing only ever shrinks the input, so a payload that passes the check
can never grow past the limit in storage; and measuring the cleaned output
would mean parsing arbitrarily large hostile input first, which is part of
what the check exists to avoid. Asserted by
`test_the_limit_is_measured_before_sanitization_not_after`.

**The database column is `Text`** — unbounded in Postgres. This is a product
decision enforced at the API boundary, not a storage constraint, so raising
it later needs no migration.

The frontend constant is a deliberate duplicate of the server's, documented
as such at both sites. A limit that arrives asynchronously cannot be enforced
by the first keystroke, and the client disagreeing with the server fails safe
(the server rejects).

---

## 7. M2A guarantees still hold

- `expectedUpdatedAt` remains mandatory on `PATCH /drafts/{id}`.
- A 409 still freezes autosave and the editor, and offers Reload vs Review.
- The rich-text editor goes read-only during a conflict (`disabled` →
  `contenteditable="false"`) **without discarding the merchant's text**,
  asserted by `a conflict freezes the editor without discarding the
  description`.
- A failed save (500) leaves the text in the editor and re-enables Save.

---

## 8. Verification

See §9 of the delivery report for exit codes. Summary of what was written:

| Layer | File | Count |
|---|---|---|
| Backend integration | `tests/integration/test_draft_rich_text_description.py` | 30 |
| Backend unit | `tests/unit/test_html_sanitizer.py` (M2B additions) | 11 |
| Playwright | `tests/e2e/draft-rich-text-description.spec.ts` | 16 per project (chromium + mobile-chrome) |

Full backend suite after M2B: **1032 passed**, exit 0 (was 991). Alembic
head unchanged at `0022` — M2B adds no migration, which was verified rather
than assumed.

---

## 9. Known limitations

- **No image insertion or upload.** Out of scope by design; M2D-A.
- **No tables in the toolbar.** The sanitizer allows table markup, so an
  imported supplier table renders and survives a save, but the editor
  provides no way to create or restructure one. Not scoped for M2B.
- **The frontend size limit is a hard-coded mirror** of the server's. If the
  server's changes, this must change with it; the mismatch surfaces as a
  counter that stops agreeing with the 422.
- **The editor does not expose an HTML source view.** Merchants who
  previously hand-edited markup in the textarea have no equivalent. This is
  intentional for M2B — two editing surfaces for one field is how the two
  diverge — but it is a real capability removal and is recorded as one.

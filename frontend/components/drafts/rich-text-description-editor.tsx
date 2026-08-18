"use client";

import Image from "@tiptap/extension-image";
import Link from "@tiptap/extension-link";
import { TableKit } from "@tiptap/extension-table";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Rich-text editor for the merchant-facing product description (M2B).
 *
 * Replaces a plain `<textarea>` that showed merchants raw HTML source. The
 * canonical storage format is unchanged — sanitized HTML in the existing
 * `description` column — so this is a change of *editing surface*, not of
 * data model, and every previously stored description keeps working.
 *
 * Security posture: this component sanitizes nothing. `nh3` on the server
 * is the only sanitization boundary (`app/core/sanitize.py`), applied on
 * every write regardless of which client sent it. A browser-side cleaner
 * would be a convenience at best and a false sense of safety at worst,
 * since anything here can be bypassed by calling the API directly. What
 * this component does instead is *narrow what can be produced*: the schema
 * below has no node for a script, an iframe, or a style attribute, so the
 * editor cannot express them in the first place.
 */

/** Mirrors `DESCRIPTION_MAX_LENGTH` in `app/schemas/product.py`, which is the
 * authority — this copy exists only so the merchant is told before saving
 * rather than after a 422. Kept as a literal rather than fetched: a size limit
 * that arrives asynchronously cannot be enforced by the first keystroke, and a
 * client that disagrees with the server here fails safe (the server rejects).
 * If the server's limit changes, change this too; the mismatch is visible as a
 * counter that stops matching the error. */
export const DESCRIPTION_MAX_LENGTH = 64_000;

/** The editor's document schema.
 *
 * **It must cover everything `app/core/sanitize.py` allows, not just what the
 * toolbar can create.** ProseMirror silently discards any node its schema
 * cannot express, so a schema narrower than the sanitizer's allowlist does
 * not merely fail to *offer* a feature — it deletes that content from every
 * description it opens, and the next autosave writes the loss back. Verified
 * against a live draft during the M2B build: with images missing from the
 * schema, loading a supplier description stripped its `<img>` tags and the
 * autosave persisted the stripped copy without the merchant touching
 * anything.
 *
 * "Can be created from the toolbar" and "can exist in the document" are
 * therefore separate questions. Images and tables are in the schema and have
 * no toolbar button: imported ones round-trip intact, and M2B still adds no
 * way to insert one (image insertion is M2D-A).
 */
const EXTENSIONS = [
  StarterKit.configure({
    // All six levels, though only H2/H3 are offered in the toolbar. A
    // supplier description containing `<h1>` or `<h4>` must survive as
    // itself rather than being flattened into a paragraph on load.
    heading: { levels: [1, 2, 3, 4, 5, 6] },
    // No code block or horizontal rule: neither is on the sanitizer's
    // allowlist, so neither can reach the editor from storage, and a code
    // block in a product description is nearly always a paste accident.
    codeBlock: false,
    horizontalRule: false,
    link: false,
  }),
  Link.configure({
    openOnClick: false,
    autolink: true,
    // Mirrors the server's `_ALLOWED_URL_SCHEMES`. The server is still the
    // authority — this only stops the editor offering to build a link it
    // knows would be stripped on save.
    protocols: ["http", "https"],
    HTMLAttributes: { rel: "noopener noreferrer nofollow", target: "_blank" },
  }),
  // Present so supplier images survive, not so merchants can add one.
  Image.configure({ allowBase64: false }),
  TableKit.configure({ table: { resizable: false } }),
];

interface ToolbarButtonProps {
  onPress: () => void;
  active?: boolean;
  disabled?: boolean;
  label: string;
  children: React.ReactNode;
}

function ToolbarButton({ onPress, active, disabled, label, children }: ToolbarButtonProps) {
  return (
    <button
      type="button"
      // The action belongs on `onClick`, but `mousedown` must still be
      // suppressed. Two requirements pulling opposite ways:
      //
      //   - a plain mouse click moves focus to the button first, which
      //     collapses the editor's selection, so "select a word, press
      //     Bold" would bold nothing. Preventing the default on `mousedown`
      //     stops the focus shift while leaving the click itself intact.
      //   - a keyboard user never fires `mousedown` at all. Putting the
      //     action there instead of on `onClick` would make the entire
      //     toolbar mouse-only -- reachable by Tab, and inert on Enter.
      //
      // Suppressing the focus shift on `mousedown` and acting on `click`
      // satisfies both; neither alone does.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onPress}
      disabled={disabled}
      aria-label={label}
      aria-pressed={active ?? false}
      title={label}
      className={cn(
        "inline-flex h-8 min-w-8 items-center justify-center rounded-md px-2 text-sm",
        "transition-colors focus-visible:outline-none focus-visible:ring-2",
        "focus-visible:ring-ring focus-visible:ring-offset-1",
        "disabled:pointer-events-none disabled:opacity-50",
        active
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
      )}
    >
      {children}
    </button>
  );
}

interface ToolbarProps {
  editor: Editor;
  disabled: boolean;
  onEditLink: () => void;
  onUserAction: () => void;
}

function Toolbar({ editor, disabled, onEditLink, onUserAction }: ToolbarProps) {
  /** Wrap a toolbar command so it registers as a merchant edit.
   *
   * A toolbar command reaches ProseMirror directly, without any DOM input
   * event, so it is invisible to the `handleDOMEvents` listeners that
   * classify typing. Without this, pressing Bold would change the document
   * and leave the form looking untouched. */
  const asEdit = (run: () => void) => () => {
    onUserAction();
    run();
  };

  return (
    <div
      role="toolbar"
      aria-label="Text formatting"
      aria-controls="draft-description-editor"
      // Wraps rather than scrolls: a horizontally scrolling toolbar hides
      // controls on exactly the narrow screens where they are hardest to
      // discover, and would be the page's only source of x-overflow.
      className="flex flex-wrap items-center gap-1 border-b bg-muted/30 px-2 py-1.5"
    >
      <ToolbarButton
        label="Bold"
        active={editor.isActive("bold")}
        disabled={disabled}
        onPress={asEdit(() => editor.chain().focus().toggleBold().run())}
      >
        <span className="font-semibold">B</span>
      </ToolbarButton>
      <ToolbarButton
        label="Italic"
        active={editor.isActive("italic")}
        disabled={disabled}
        onPress={asEdit(() => editor.chain().focus().toggleItalic().run())}
      >
        <span className="italic">I</span>
      </ToolbarButton>
      <ToolbarButton
        label="Underline"
        active={editor.isActive("underline")}
        disabled={disabled}
        onPress={asEdit(() => editor.chain().focus().toggleUnderline().run())}
      >
        <span className="underline">U</span>
      </ToolbarButton>
      <ToolbarButton
        label="Strikethrough"
        active={editor.isActive("strike")}
        disabled={disabled}
        onPress={asEdit(() => editor.chain().focus().toggleStrike().run())}
      >
        <span className="line-through">S</span>
      </ToolbarButton>

      <span aria-hidden="true" className="mx-1 h-5 w-px bg-border" />

      <ToolbarButton
        label="Heading 2"
        active={editor.isActive("heading", { level: 2 })}
        disabled={disabled}
        onPress={asEdit(() => editor.chain().focus().toggleHeading({ level: 2 }).run())}
      >
        H2
      </ToolbarButton>
      <ToolbarButton
        label="Heading 3"
        active={editor.isActive("heading", { level: 3 })}
        disabled={disabled}
        onPress={asEdit(() => editor.chain().focus().toggleHeading({ level: 3 }).run())}
      >
        H3
      </ToolbarButton>

      <span aria-hidden="true" className="mx-1 h-5 w-px bg-border" />

      <ToolbarButton
        label="Bulleted list"
        active={editor.isActive("bulletList")}
        disabled={disabled}
        onPress={asEdit(() => editor.chain().focus().toggleBulletList().run())}
      >
        &bull;&nbsp;
      </ToolbarButton>
      <ToolbarButton
        label="Numbered list"
        active={editor.isActive("orderedList")}
        disabled={disabled}
        onPress={asEdit(() => editor.chain().focus().toggleOrderedList().run())}
      >
        1.
      </ToolbarButton>
      <ToolbarButton
        label="Quote"
        active={editor.isActive("blockquote")}
        disabled={disabled}
        onPress={asEdit(() => editor.chain().focus().toggleBlockquote().run())}
      >
        &ldquo;
      </ToolbarButton>

      <span aria-hidden="true" className="mx-1 h-5 w-px bg-border" />

      <ToolbarButton
        label="Add or edit link"
        active={editor.isActive("link")}
        disabled={disabled}
        onPress={onEditLink}
      >
        Link
      </ToolbarButton>
      <ToolbarButton
        label="Remove link"
        disabled={disabled || !editor.isActive("link")}
        onPress={asEdit(() => editor.chain().focus().unsetLink().run())}
      >
        Unlink
      </ToolbarButton>

      <span aria-hidden="true" className="mx-1 h-5 w-px bg-border" />

      <ToolbarButton
        label="Clear formatting"
        disabled={disabled}
        onPress={asEdit(() => editor.chain().focus().unsetAllMarks().clearNodes().run())}
      >
        Clear
      </ToolbarButton>
      <ToolbarButton
        label="Undo"
        disabled={disabled || !editor.can().undo()}
        onPress={asEdit(() => editor.chain().focus().undo().run())}
      >
        Undo
      </ToolbarButton>
      <ToolbarButton
        label="Redo"
        disabled={disabled || !editor.can().redo()}
        onPress={asEdit(() => editor.chain().focus().redo().run())}
      >
        Redo
      </ToolbarButton>
    </div>
  );
}

/** Inline URL row, shown only while a link is being added or edited.
 *
 * Deliberately not `window.prompt`: a native prompt blocks the whole page,
 * cannot be styled to match either theme, is unreadable to a screen reader
 * as part of this form, and is suppressed entirely in some embedded
 * contexts. It is also not a shadcn `Dialog` -- a modal that steals focus
 * would discard the text selection the link is meant to wrap, which is the
 * same problem the toolbar's `mousedown` handling exists to solve.
 */
function LinkEditor({
  initialUrl,
  onApply,
  onRemove,
  onCancel,
}: {
  initialUrl: string;
  onApply: (url: string) => void;
  onRemove: () => void;
  onCancel: () => void;
}) {
  const [url, setUrl] = useState(initialUrl || "https://");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.focus(), []);

  // Mirrors the server's `_ALLOWED_URL_SCHEMES`. The server remains the
  // authority -- this only avoids offering to build a link it would strip.
  const valid = /^https?:\/\/\S+/i.test(url.trim());

  return (
    <div
      className="flex flex-wrap items-center gap-2 border-b bg-muted/20 px-2 py-2"
      data-testid="draft-description-link-editor"
    >
      <label className="sr-only" htmlFor="draft-description-link-url">
        Link URL
      </label>
      <input
        ref={inputRef}
        id="draft-description-link-url"
        data-testid="draft-description-link-url"
        type="url"
        value={url}
        placeholder="https://example.com"
        onChange={(event) => setUrl(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            if (valid) onApply(url.trim());
          }
          if (event.key === "Escape") {
            event.preventDefault();
            onCancel();
          }
        }}
        className={cn(
          "h-8 min-w-0 flex-1 rounded-md border bg-background px-2 text-sm",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      />
      <Button
        type="button"
        size="sm"
        disabled={!valid}
        data-testid="draft-description-link-apply"
        onClick={() => onApply(url.trim())}
      >
        Apply
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        data-testid="draft-description-link-remove"
        onClick={onRemove}
      >
        Remove
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
        Cancel
      </Button>
      {url.trim() && !valid ? (
        <p className="w-full text-xs text-destructive" role="alert">
          Links must start with http:// or https://
        </p>
      ) : null}
    </div>
  );
}

interface RichTextDescriptionEditorProps {
  value: string;
  onChange: (html: string) => void;
  disabled?: boolean;
  maxLength: number;
}

export function RichTextDescriptionEditor({
  value,
  onChange,
  disabled = false,
  maxLength,
}: RichTextDescriptionEditorProps) {
  const [mounted, setMounted] = useState(false);
  const [linkEditorOpen, setLinkEditorOpen] = useState(false);
  useEffect(() => setMounted(true), []);

  // What this component last emitted. Used to distinguish "the parent sent
  // us a genuinely new document" from "the parent is echoing our own last
  // keystroke back", which is the difference between a legitimate reload
  // and an edit-eating render loop.
  const lastEmitted = useRef<string | null>(null);

  // The value at mount, frozen. `useEditor` re-applies changed options on
  // every render, so passing the live `value` as `content` makes TipTap
  // rebuild the document — and *emit an update* — the moment the draft
  // finishes loading. That update carries the re-parsed HTML, so the parent
  // records an edit nobody made, marks the form dirty, and autosaves. Every
  // subsequent content change is applied by the effect below instead, which
  // does it with `emitUpdate: false`.
  const initialContent = useRef(value);

  // Whether the merchant has actually done something to the document since
  // the last time content was loaded into it.
  //
  // This is the difference between "the draft is unsaved" and "the draft is
  // not". ProseMirror emits an update for its *own* normalisation work as
  // well as for edits: parsing stored HTML into the schema and back out
  // again legitimately produces different markup (a table gains a
  // `<colgroup>`, a document ending in a table gains a trailing paragraph),
  // and `emitUpdate: false` on the load itself does not cover the follow-up
  // transactions plugins append afterwards. Treating those as edits made
  // every page view dirty the form and fire an autosave -- observed live
  // during this build, before this guard existed.
  //
  // Time-based suppression was rejected: "ignore updates for the first N
  // ms" is a race with the merchant. Gating on evidence of real input is
  // not — every route into the document is either a DOM event on the
  // editable node (below) or a toolbar command (`asEdit`).
  const sawUserInput = useRef(false);
  const markUserInput = () => {
    sawUserInput.current = true;
  };

  const editor = useEditor({
    extensions: EXTENSIONS,
    content: initialContent.current,
    editable: !disabled,
    // Required for SSR: without it, TipTap renders on the server and the
    // markup disagrees with the client's first pass (`next.config.ts` uses
    // `output: "standalone"`, so every page is server-rendered first).
    immediatelyRender: false,
    editorProps: {
      // Observers only -- every handler returns false so ProseMirror keeps
      // its own behaviour. `beforeinput` covers IME and autocorrect, which
      // `keydown` alone misses.
      handleDOMEvents: {
        keydown: () => (markUserInput(), false),
        beforeinput: () => (markUserInput(), false),
        compositionstart: () => (markUserInput(), false),
        paste: () => (markUserInput(), false),
        cut: () => (markUserInput(), false),
        drop: () => (markUserInput(), false),
      },
      attributes: {
        id: "draft-description-editor",
        role: "textbox",
        "aria-multiline": "true",
        "aria-label": "Product description",
        "data-testid": "draft-description-editor",
        class: cn(
          "prose prose-sm dark:prose-invert max-w-none",
          "min-h-56 px-3 py-2 focus:outline-none",
          "[&_a]:underline [&_h2]:text-lg [&_h3]:text-base",
          "[&_ul]:list-disc [&_ol]:list-decimal [&_ul,&_ol]:pl-5",
          "[&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:italic",
        ),
      },
    },
    onUpdate: ({ editor: instance }) => {
      // Normalisation, not an edit: keep the editor's own bookkeeping in
      // step but leave the parent's value -- and therefore the draft's
      // saved/unsaved state -- exactly as the server sent it.
      if (!sawUserInput.current) {
        lastEmitted.current = instance.isEmpty ? "" : instance.getHTML();
        return;
      }
      const html = instance.isEmpty ? "" : instance.getHTML();
      lastEmitted.current = html;
      onChange(html);
    },
  });

  // Adopt server content only when it is genuinely different from what we
  // last produced. Comparing against `lastEmitted` rather than against the
  // editor's current HTML is deliberate: TipTap normalises markup on parse,
  // so a round-trip through the editor is rarely byte-identical to what the
  // server holds, and comparing the two would re-set content on every
  // render — destroying the cursor position mid-sentence.
  useEffect(() => {
    if (!editor) return;
    if (value === lastEmitted.current) return;
    const current = editor.isEmpty ? "" : editor.getHTML();
    if (value === current) return;
    // A fresh document from the server is a new baseline, so anything the
    // merchant had typed into the old one is gone with it -- the parent
    // only sends a genuinely different value after an explicit "Reload
    // latest version". Clearing the flag here is what stops the
    // normalisation of the *new* document being read as an edit.
    sawUserInput.current = false;
    editor.commands.setContent(value || "", { emitUpdate: false });
    lastEmitted.current = value;
  }, [editor, value]);

  useEffect(() => {
    editor?.setEditable(!disabled);
  }, [editor, disabled]);

  if (!mounted || !editor) {
    return (
      <div
        className="min-h-64 animate-pulse rounded-md border bg-muted/30"
        data-testid="draft-description-editor-loading"
        aria-busy="true"
        aria-label="Loading description editor"
      />
    );
  }

  const length = value.length;
  const overLimit = length > maxLength;

  return (
    <div className="space-y-1.5">
      <div
        className={cn(
          "overflow-hidden rounded-md border bg-background",
          "focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-1",
          overLimit && "border-destructive",
        )}
      >
        <Toolbar
          editor={editor}
          disabled={disabled}
          onEditLink={() => setLinkEditorOpen(true)}
          onUserAction={markUserInput}
        />
        {linkEditorOpen ? (
          <LinkEditor
            initialUrl={(editor.getAttributes("link").href as string | undefined) ?? ""}
            onApply={(url) => {
              markUserInput();
              editor.chain().focus().extendMarkRange("link").setLink({ href: url }).run();
              setLinkEditorOpen(false);
            }}
            onRemove={() => {
              markUserInput();
              editor.chain().focus().extendMarkRange("link").unsetLink().run();
              setLinkEditorOpen(false);
            }}
            onCancel={() => {
              setLinkEditorOpen(false);
              editor.commands.focus();
            }}
          />
        ) : null}
        <EditorContent editor={editor} />
      </div>

      <div className="flex items-center justify-between gap-3 text-xs">
        {/* Says what actually happens, and no more. Images imported from a
            supplier *are* kept -- the sanitizer allows `img` on http/https --
            so a blanket "images are removed" would be a lie that reads as a
            warning. M2B simply provides no way to add a new one. */}
        <p className="text-muted-foreground">
          Existing images are kept. Scripts, custom styling and unsafe links
          are removed when the draft is saved.
        </p>
        <p
          className={cn(
            "shrink-0 tabular-nums",
            overLimit ? "font-medium text-destructive" : "text-muted-foreground",
          )}
          data-testid="draft-description-length"
          aria-live="polite"
        >
          {length.toLocaleString()} / {maxLength.toLocaleString()}
        </p>
      </div>

      {overLimit ? (
        <p className="text-xs text-destructive" data-testid="draft-description-too-long" role="alert">
          This description is too long to publish. Shorten it before saving.
        </p>
      ) : null}
    </div>
  );
}

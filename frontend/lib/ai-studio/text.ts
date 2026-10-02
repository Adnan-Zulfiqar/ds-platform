/**
 * Render stored HTML as plain text.
 *
 * The server sanitises descriptions, and AI Studio still never interprets
 * markup: a candidate or a supplier description is shown as text, so a
 * model or supplier that emits `<img onerror=…>` produces visible characters,
 * not an element. Script and style bodies are dropped with their tags — their
 * contents are not description.
 */
export function stripHtml(html: string): string {
  return html
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/(script|style)>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

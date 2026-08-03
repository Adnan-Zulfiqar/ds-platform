"""Tests for `app.core.sanitize` — the supplier-HTML sanitizer.

Every allowed-content test also asserts a still-dangerous variant is
removed, so this file cannot pass by allowing everything through.
"""

from __future__ import annotations

import pytest

from app.core.sanitize import html_to_plain_text, sanitize_html

pytestmark = pytest.mark.unit


class TestBlankInput:
    def test_none_stays_none(self) -> None:
        assert sanitize_html(None) is None

    def test_empty_string_becomes_none(self) -> None:
        assert sanitize_html("") is None

    def test_whitespace_only_becomes_none(self) -> None:
        assert sanitize_html("   \n\t  ") is None

    def test_markup_that_sanitizes_to_nothing_becomes_none(self) -> None:
        """A description that was *only* a `<script>` block, say, should read
        as "no description" rather than an empty-but-present string."""
        assert sanitize_html("<script>alert(1)</script>") is None


class TestAllowedContentSurvives:
    def test_paragraphs_and_formatting(self) -> None:
        html = "<p>Hello <strong>world</strong>, this is <em>great</em>.</p>"
        assert sanitize_html(html) == html

    def test_headings(self) -> None:
        assert sanitize_html("<h2>Title</h2>") == "<h2>Title</h2>"

    def test_lists(self) -> None:
        html = "<ul><li>One</li><li>Two</li></ul>"
        assert sanitize_html(html) == html

    def test_a_safe_link_survives_with_a_safety_rel(self) -> None:
        cleaned = sanitize_html('<a href="https://example.com">shop</a>')
        assert cleaned is not None
        assert 'href="https://example.com"' in cleaned
        assert "shop" in cleaned
        assert "noopener" in cleaned

    def test_an_image_with_alt_text_survives(self) -> None:
        html = '<img src="https://ae01.alicdn.com/a.jpg" alt="Product photo"/>'
        cleaned = sanitize_html(html)
        assert cleaned is not None
        assert 'src="https://ae01.alicdn.com/a.jpg"' in cleaned
        assert 'alt="Product photo"' in cleaned

    def test_a_table_survives(self) -> None:
        html = "<table><tr><th>Size</th><td>Large</td></tr></table>"
        cleaned = sanitize_html(html)
        assert cleaned is not None
        assert "<table>" in cleaned
        assert "<th>Size</th>" in cleaned


class TestScriptsAndHandlersAreRemoved:
    def test_a_script_tag_is_removed_content_and_all(self) -> None:
        cleaned = sanitize_html("<p>Buy now</p><script>steal(document.cookie)</script>")
        assert cleaned is not None
        assert "script" not in cleaned.lower()
        assert "steal" not in cleaned
        assert "Buy now" in cleaned

    def test_an_event_handler_attribute_is_stripped(self) -> None:
        cleaned = sanitize_html('<img src="https://x/a.jpg" onerror="steal()"/>')
        assert cleaned is not None
        assert "onerror" not in cleaned
        assert "steal" not in cleaned

    def test_an_onclick_on_a_link_is_stripped(self) -> None:
        cleaned = sanitize_html('<a href="https://x" onclick="steal()">click</a>')
        assert cleaned is not None
        assert "onclick" not in cleaned
        assert "steal" not in cleaned

    def test_style_attribute_is_removed_entirely(self) -> None:
        """No CSS-property filtering -- `style` is refused by omission."""
        cleaned = sanitize_html('<p style="background:url(javascript:alert(1))">hi</p>')
        assert cleaned is not None
        assert "style" not in cleaned
        assert "hi" in cleaned

    def test_class_and_id_are_removed(self) -> None:
        cleaned = sanitize_html('<p class="tracker" id="pixel-1">hi</p>')
        assert cleaned is not None
        assert "class" not in cleaned
        assert "id=" not in cleaned

    def test_an_iframe_is_removed_content_and_all(self) -> None:
        cleaned = sanitize_html('<p>Video</p><iframe src="https://evil.example/steal"></iframe>')
        assert cleaned is not None
        assert "iframe" not in cleaned.lower()
        assert "evil.example" not in cleaned

    def test_a_form_is_stripped(self) -> None:
        """`form`/`input` carry no text content here, so the whole thing
        sanitizes down to nothing -- correctly `None`, not an empty string
        with the tags merely removed."""
        cleaned = sanitize_html('<form action="https://evil.example"><input name="cc"/></form>')
        assert cleaned is None

    def test_a_form_with_a_text_label_keeps_the_text_and_drops_the_form(self) -> None:
        cleaned = sanitize_html('<form action="https://evil.example">Enter your card</form>')
        assert cleaned is not None
        assert "form" not in cleaned.lower()
        assert "evil.example" not in cleaned
        assert "Enter your card" in cleaned

    def test_an_object_and_embed_are_removed(self) -> None:
        cleaned = sanitize_html(
            '<object data="https://evil.example/x.swf"></object>'
            '<embed src="https://evil.example/x.swf"/>'
        )
        assert cleaned is None


class TestUnsafeUrlSchemesAreRejected:
    @pytest.mark.parametrize(
        "scheme_prefix",
        [
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
        ],
    )
    def test_dangerous_href_schemes_do_not_survive(self, scheme_prefix: str) -> None:
        cleaned = sanitize_html(f'<a href="{scheme_prefix}">click me</a>')
        assert cleaned is not None
        assert "javascript:" not in cleaned
        assert "vbscript:" not in cleaned
        assert "base64" not in cleaned
        assert "click me" in cleaned

    def test_a_javascript_image_src_does_not_survive(self) -> None:
        cleaned = sanitize_html('<img src="javascript:alert(1)" alt="x"/>')
        assert cleaned is not None
        assert "javascript:" not in cleaned

    def test_a_plain_http_and_https_href_are_unaffected(self) -> None:
        for scheme in ("http", "https"):
            cleaned = sanitize_html(f'<a href="{scheme}://example.com/item">x</a>')
            assert cleaned is not None
            assert f"{scheme}://example.com/item" in cleaned


class TestDivAndSpanAreUnwrappedNotDropped:
    def test_a_div_wrapper_is_removed_but_its_content_remains(self) -> None:
        """This is the real fixture's own shape:
        `<div class="detailmodule_html"><p><img .../></p></div>`."""
        cleaned = sanitize_html('<div class="wrap"><p>Text</p></div>')
        assert cleaned is not None
        assert "<div" not in cleaned
        assert "<p>Text</p>" in cleaned


class TestDeterminism:
    def test_sanitizing_twice_produces_the_same_result(self) -> None:
        html = '<p>Hello <a href="https://x">link</a></p><script>bad()</script>'
        assert sanitize_html(html) == sanitize_html(html)

    def test_sanitizing_already_sanitized_output_is_a_no_op(self) -> None:
        once = sanitize_html('<p>Hello <a href="https://x">link</a></p>')
        assert once is not None
        assert sanitize_html(once) == once


class TestPlainTextExtraction:
    def test_empty_input_is_empty_string_not_none(self) -> None:
        assert html_to_plain_text(None) == ""
        assert html_to_plain_text("") == ""

    def test_tags_are_stripped(self) -> None:
        assert html_to_plain_text("<p>Hello <strong>world</strong></p>") == "Hello world"

    def test_paragraphs_become_separate_lines(self) -> None:
        text = html_to_plain_text("<p>First</p><p>Second</p>")
        assert text == "First\nSecond"

    def test_list_items_become_separate_lines(self) -> None:
        text = html_to_plain_text("<ul><li>One</li><li>Two</li></ul>")
        assert "One" in text
        assert "Two" in text
        assert text.index("One") < text.index("Two")

    def test_line_breaks_become_newlines(self) -> None:
        assert html_to_plain_text("Line one<br/>Line two") == "Line one\nLine two"

    def test_html_entities_are_unescaped(self) -> None:
        assert html_to_plain_text("<p>Fits &amp; flatters</p>") == "Fits & flatters"

    def test_excess_blank_lines_are_collapsed(self) -> None:
        text = html_to_plain_text("<p>A</p><p></p><p></p><p>B</p>")
        assert "\n\n\n" not in text

    def test_output_contains_no_angle_brackets(self) -> None:
        text = html_to_plain_text('<p>Text</p><a href="https://x">link</a><img src="https://x"/>')
        assert "<" not in text
        assert ">" not in text

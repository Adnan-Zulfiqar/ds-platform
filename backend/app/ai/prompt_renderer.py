"""Safe substitution for `{{variable}}` prompt templates.

**Deliberately not a template language.** No conditionals, no loops, no
attribute access, no arbitrary expressions — a fixed grammar of
`{{identifier}}` substitution and nothing else. `str.format()` was rejected
for the same reason: a hostile value routed into a format string can walk
`{0.__class__.__init__.__globals__}`-style attribute chains, and Jinja2 or
similar would add a full expression evaluator neither this stage nor any
planned one needs. Every generation prompt eventually carries supplier-
authored text (`docs/PHASE_9_PLAN.md` §4 flags this as the phase's highest
sustained risk); the smallest possible substitution grammar is the smallest
possible surface for that text to do something other than sit there as data.

Required variables are **derived from the template**, not stored as a
separate declared list. A stored list can drift from what the template text
actually references; parsing the template is the only source of truth that
cannot disagree with itself.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.ai.exceptions import MissingPromptVariablesError

#: `{{name}}`, optionally padded with spaces. Names are restricted to
#: `[a-zA-Z_][a-zA-Z0-9_]*` — the same identifier grammar as a Python name —
#: so a stray `{{` in supplier-authored text that made it into a template by
#: mistake (e.g. `{{ "malicious" }}`) is left untouched rather than matched.
_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class PromptRenderer:
    """Stateless template rendering. No I/O, no database, no provider call."""

    @staticmethod
    def extract_variables(template: str) -> frozenset[str]:
        """Return every variable name the template references."""
        return frozenset(_VARIABLE_PATTERN.findall(template))

    @staticmethod
    def render(template: str, variables: Mapping[str, str]) -> str:
        """Substitute every `{{variable}}` in `template`.

        Raises `MissingPromptVariablesError` if any referenced variable has
        no supplied value — never sends a partially-filled prompt. Extra keys
        in `variables` beyond what the template references are silently
        ignored, so a caller can pass one shared context dict to several
        prompts without trimming it per template.
        """
        required = PromptRenderer.extract_variables(template)
        missing = required - variables.keys()
        if missing:
            raise MissingPromptVariablesError(list(missing))

        def _substitute(match: re.Match[str]) -> str:
            return str(variables[match.group(1)])

        return _VARIABLE_PATTERN.sub(_substitute, template)


__all__ = ["PromptRenderer"]

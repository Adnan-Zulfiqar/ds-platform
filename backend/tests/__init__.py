"""Marks ``tests`` as a regular package.

Without this, pytest's default import mode roots the sys.path insertion at
``backend/tests`` itself rather than at ``backend``, so ``tests.environment``
(imported by ``conftest.py`` before any application module) cannot resolve
under some editable-install layouts that expose only the declared ``app``
package on ``sys.path``.
"""

from __future__ import annotations

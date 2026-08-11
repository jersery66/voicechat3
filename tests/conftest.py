"""Shared pytest configuration.

QT_QPA_PLATFORM must be set BEFORE any Qt import happens anywhere in the
test session, so it lives here (module-level test files may be imported
in any order).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

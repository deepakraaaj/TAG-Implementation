"""Engine-facing domain loader namespace.

Runtime loader code lives under ``app.domains`` while the actual domain packages
live in the repository-level ``domains/`` folder.
"""

from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_DOMAINS_DIR = _PACKAGE_DIR.parent.parent / "domains"

if _REPO_DOMAINS_DIR.exists():
    repo_domains_path = str(_REPO_DOMAINS_DIR)
    if repo_domains_path not in __path__:
        __path__.append(repo_domains_path)

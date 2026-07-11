from __future__ import annotations

import re

_SHAPED = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*:.+/[^/]+$")
"""Matches '<scheme>:<rest>/<entity>' (e.g. salesforce:host/Object, sqlite:file.db/table)."""


def source_key_from_name(source_name: str) -> str:
    """Normalize a source_name into a stable, source-scoped identity key.

    Names shaped like '<scheme>:<rest>/<entity>' collapse to '<scheme>:<rest>' (the part before
    the LAST '/'): 'salesforce:host/Obj' -> 'salesforce:host', 'salesforce-bulk:host/Obj' ->
    'salesforce-bulk:host', 'sqlite:file.db/table' -> 'sqlite:file.db'. Two different Salesforce
    objects on the same org, or two tables in the same sqlite file, therefore share one source_key
    (they can't collide with each other, only across genuinely different sources). Names without
    that shape (a demo/upload csv filename like 'demo_opportunities.csv', or synthetic names like
    'multi:Account+2 related' / 'stream:Account' that have no '/') are not decomposable, so the
    whole name is kept verbatim, prefixed 'src:' to keep the namespace disjoint from the shaped
    keys above.
    """
    if _SHAPED.match(source_name):
        return source_name.rsplit("/", 1)[0]
    return f"src:{source_name}"

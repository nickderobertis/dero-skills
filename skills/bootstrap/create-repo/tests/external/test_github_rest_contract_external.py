"""The gate's GitHub contract, held to GitHub's own OpenAPI description over the network."""

from __future__ import annotations

import json

from github_rest_contract import (
    CURRENT_URL,
    DELETION_RULE,
    OPERATIONS,
    RULE,
    SNAPSHOT,
    SOURCE_URL,
    existing_protection,
    extract,
    problems,
    rendered,
)
from http_fetch import fetch_all


def test_the_snapshot_is_derived_from_the_description_it_names_and_still_holds():
    pinned, current = (
        json.loads(body) for body in fetch_all([SOURCE_URL, CURRENT_URL])
    )
    assert SNAPSHOT.read_text(encoding="utf-8") == rendered(extract(pinned))

    # GitHub moves independently, so the journeys' answers must hold today as well.
    now = extract(current)
    assert {
        (method, path) for path, methods in now["paths"].items() for method in methods
    } == set(OPERATIONS)
    protection, rules, contexts = (
        now["paths"][path][method]["responses"]["200"]
        for method, path in (OPERATIONS[1], OPERATIONS[0], OPERATIONS[2])
    )
    schemas = now["schemas"]
    assert problems(existing_protection(), protection, schemas) == []
    assert problems([RULE, DELETION_RULE], rules, schemas) == []
    assert problems(["lint", "budgets"], contexts, schemas) == []

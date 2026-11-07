"""The gate's GitHub contract, held to GitHub's own OpenAPI description over the network."""

from __future__ import annotations

import json

from pathlib import Path

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


FIGURES = Path(__file__).parents[2] / "assets" / "budgets" / "actions-budget-figures.sh"
# What the figures script's own-run read requires of each listed job: (type, nullable).
JOB_FIELDS = {
    "status": ("string", False),
    "name": ("string", False),
    "workflow_name": ("string", True),
    "conclusion": ("string", True),
    "runner_name": ("string", True),
    "labels": ("array", False),
    "started_at": ("string", False),
    "completed_at": ("string", True),
}


def test_the_figures_scripts_job_listing_is_the_one_github_describes():
    description = json.loads(fetch_all([CURRENT_URL])[0])
    operation = description["paths"]["/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"]
    listing = operation["get"]["responses"]["200"]["content"]["application/json"]
    listing = listing["schema"]
    assert set(listing["required"]) == {"total_count", "jobs"}
    assert listing["properties"]["total_count"]["type"] == "integer"
    assert listing["properties"]["jobs"]["items"]["$ref"].endswith("/job")

    job = description["components"]["schemas"]["job"]
    script = FIGURES.read_text(encoding="utf-8")
    for field, (kind, nullable) in JOB_FIELDS.items():
        assert field in job["required"], field
        assert job["properties"][field]["type"] == kind, field
        assert job["properties"][field].get("nullable", False) is nullable, field
        assert f".{field}" in script, field
    assert "skipped" in job["properties"]["conclusion"]["enum"]
    statuses = job["properties"]["status"]["enum"]
    assert {"in_progress", "completed"} <= set(statuses)


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

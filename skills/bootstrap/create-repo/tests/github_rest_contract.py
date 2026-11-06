"""GitHub's REST contract for the branch-protection calls the additive mode makes.

`e2e/fixtures/github-rest/protection-operations.json` is `extract`'s output over GitHub's own
OpenAPI description at `SOURCE_COMMIT`. The gate holds `setup_github_governance.py`, and the
local server its journeys drive through the real `gh`, to that file; the external tier derives
it again from GitHub and checks the journeys' answers against GitHub's current description.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SNAPSHOT = (
    Path(__file__).resolve().parent
    / "e2e"
    / "fixtures"
    / "github-rest"
    / "protection-operations.json"
)
SOURCE_COMMIT = "f63817a474b4c2963d6ffe2e6efaba90554cac66"
DESCRIPTION = "descriptions/api.github.com/api.github.com.json"
RAW = "https://raw.githubusercontent.com/github/rest-api-description"
CURRENT_URL = f"{RAW}/main/{DESCRIPTION}"
SOURCE_URL = f"{RAW}/{SOURCE_COMMIT}/{DESCRIPTION}"
OPERATIONS = (
    ("get", "/repos/{owner}/{repo}/rules/branches/{branch}"),
    ("get", "/repos/{owner}/{repo}/branches/{branch}/protection"),
    ("post", "/repos/{owner}/{repo}/branches/{branch}/protection/required_status_checks/contexts"),
)  # fmt: skip
_PROSE = {"description", "summary", "title", "example", "examples", "externalDocs"}
_REF = "#/components/schemas/"
TYPES = {
    "string": str,
    "object": dict,
    "array": list,
    "boolean": bool,
    "number": (int, float),
    "integer": int,
}


def _pruned(schema):
    """A schema without its prose; `properties` keys are names, whatever they spell."""
    if isinstance(schema, list):
        return [_pruned(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: {name: _pruned(value) for name, value in part.items()}
        if key == "properties"
        else _pruned(part)
        for key, part in schema.items()
        if key not in _PROSE
    }


def _references(node) -> set[str]:
    if isinstance(node, list):
        return {name for item in node for name in _references(item)}
    if not isinstance(node, dict):
        return set()
    found = {name for part in node.values() for name in _references(part)}
    reference = node.get("$ref")
    if isinstance(reference, str) and reference.startswith(_REF):
        found.add(reference.removeprefix(_REF))
    return found


def _json_schema(part: dict) -> dict:
    return _pruned(part["content"]["application/json"]["schema"])


def extract(description: dict) -> dict:
    """The operations' JSON success answers and bodies, and every schema they reach."""
    paths: dict[str, dict] = {}
    for method, path in OPERATIONS:
        operation = description["paths"][path][method]
        kept: dict = {
            "responses": {
                status: _json_schema(response)
                for status, response in operation["responses"].items()
                if status.startswith("2") and "content" in response
            }
        }
        if "requestBody" in operation:
            kept["requestBody"] = _json_schema(operation["requestBody"])
        paths.setdefault(path, {})[method] = kept
    schemas = description["components"]["schemas"]
    reached: dict[str, dict] = {}
    waiting = _references(paths)
    while waiting:
        name = waiting.pop()
        reached[name] = _pruned(schemas[name])
        waiting |= _references(reached[name]) - reached.keys()
    return {
        "source": {"commit": SOURCE_COMMIT, "path": DESCRIPTION},
        "paths": paths,
        "schemas": dict(sorted(reached.items())),
    }


def rendered(contract: dict) -> str:
    """The snapshot's exact text, so re-deriving it compares byte for byte."""
    return json.dumps(contract, indent=1, sort_keys=True) + "\n"


def load_snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _matches_type(value: object, kind: str) -> bool:
    if isinstance(value, bool):
        return kind == "boolean"
    return isinstance(value, TYPES[kind])


def problems(value: object, schema: dict, schemas: dict, at: str = "$") -> list[str]:
    """Where `value` leaves `schema`, over the OpenAPI 3.0 keywords the description uses."""
    if "$ref" in schema:
        return problems(value, schemas[schema["$ref"].removeprefix(_REF)], schemas, at)
    if value is None:
        return (
            []
            if schema.get("nullable") or "null" in str(schema.get("type"))
            else [f"{at} is null"]
        )
    found: list[str] = []
    for part in schema.get("allOf", []):
        found += problems(value, part, schemas, at)
    for keyword, allowed in (("oneOf", {1}), ("anyOf", None)):
        if keyword in schema:
            held = sum(
                not problems(value, part, schemas, at) for part in schema[keyword]
            )
            if (allowed and held not in allowed) or (not allowed and not held):
                found.append(f"{at} holds {held} of its {keyword} schemas")
    kinds = schema.get("type")
    if kinds is not None:
        names = [kinds] if isinstance(kinds, str) else [k for k in kinds if k != "null"]
        if not any(_matches_type(value, kind) for kind in names):
            return [*found, f"{at} is not {'/'.join(names)}"]
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{at} is none of {schema['enum']}")
    if isinstance(value, dict):
        found += [
            f"{at}.{key} is missing"
            for key in schema.get("required", [])
            if key not in value
        ]
        for key, part in schema.get("properties", {}).items():
            if key in value:
                found += problems(value[key], part, schemas, f"{at}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            found += problems(item, schema["items"], schemas, f"{at}[{index}]")
    return found


def operation_for(contract: dict, method: str, path: str) -> dict | None:
    """The declared operation a concrete `repos/...` path and method call."""
    for template, methods in contract["paths"].items():
        pattern = re.sub(r"\{[^}]+\}", "[^/]+", template.lstrip("/"))
        if method.lower() in methods and re.fullmatch(pattern, path):
            return methods[method.lower()]
    return None


REPO = "acme/w"
PROTECTION = f"repos/{REPO}/branches/main/protection"
RULES = f"repos/{REPO}/rules/branches/main"
SUBRESOURCE = f"{PROTECTION}/required_status_checks/contexts"
API = "https://api.github.com"


def existing_protection() -> dict:
    """Live protection with nondefault settings a full-profile PUT would reset."""
    restrictions = f"{API}/{PROTECTION}/restrictions"
    return {
        "url": f"{API}/{PROTECTION}",
        "required_status_checks": {
            "url": f"{API}/{PROTECTION}/required_status_checks",
            "strict": True,
            "contexts": ["lint", "cross (windows-latest)", "legacy"],
            "contexts_url": f"{API}/{SUBRESOURCE}",
            "checks": [
                {"context": "lint", "app_id": 15368},
                {"context": "cross (windows-latest)", "app_id": 999},
                {"context": "legacy", "app_id": None},
            ],
        },
        "required_pull_request_reviews": {
            "url": f"{API}/{PROTECTION}/required_pull_request_reviews",
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": True,
            "required_approving_review_count": 2,
        },
        "enforce_admins": {
            "url": f"{API}/{PROTECTION}/enforce_admins",
            "enabled": True,
        },
        "required_linear_history": {"enabled": False},
        "required_conversation_resolution": {"enabled": False},
        "allow_force_pushes": {"enabled": True},
        "allow_deletions": {"enabled": False},
        "restrictions": {
            "url": restrictions,
            "users_url": f"{restrictions}/users",
            "teams_url": f"{restrictions}/teams",
            "apps_url": f"{restrictions}/apps",
            "users": [],
            "teams": [],
            "apps": [],
        },
    }


RULE = {
    "type": "required_status_checks",
    "ruleset_source_type": "Organization",
    "ruleset_source": "acme",
    "ruleset_id": 77,
    "parameters": {
        "required_status_checks": [{"context": "org-check"}],
        "strict_required_status_checks_policy": False,
    },
}
DELETION_RULE = {
    "type": "deletion",
    "ruleset_source_type": "Organization",
    "ruleset_source": "acme",
    "ruleset_id": 77,
}

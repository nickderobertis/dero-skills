"""A small typed async client for the external tier's public downloads, over curl."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence


class FetchError(RuntimeError):
    """A URL that gave no body, with curl's own message."""


async def fetch(url: str) -> bytes:
    process = await asyncio.create_subprocess_exec(
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--max-time",
        "300",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    body, error = await process.communicate()
    if process.returncode != 0:
        raise FetchError(f"{url}: {error.decode().strip()}")
    return body


def fetch_all(urls: Sequence[str]) -> list[bytes]:
    """Each URL's body, in order; the first failure raises."""

    async def gathered() -> list[bytes]:
        return list(await asyncio.gather(*(fetch(url) for url in urls)))

    return asyncio.run(gathered())

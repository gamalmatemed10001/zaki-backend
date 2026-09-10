"""Web search tool — spec §3.3.1: "Web search + summarize | Search API
(e.g. Brave Search/Serper) or Claude's built-in web search."

Uses `ddgs` (DuckDuckGo search, no API key needed) rather than each vendor's
own server-side search feature: it's then uniformly available to both the
Gemini and Claude routes through the existing ToolRegistry, with zero
provider-specific wiring — same pattern as every other tool module.

Package note: the PyPI package `duckduckgo-search` (the name usually
associated with this) is deprecated and renamed to `ddgs`, same import
class (`DDGS`) and API — verified at build time, not assumed from training
data (same class of staleness risk as the Gemini/Claude model IDs earlier
in this project). `pip install duckduckgo-search` would install an
unmaintained package; use `ddgs`.
"""

import asyncio
from typing import Any

from ddgs import DDGS

from zaki.tools.base import ToolContext, ToolDefinition

_DEFAULT_COUNT = 5
_MAX_COUNT = 10
_SNIPPET_MAX = 4000  # keep tool results small — this feeds straight into the model's context


async def search_web(args: dict[str, Any], context: ToolContext) -> str:
    query = args["query"]
    count = min(int(args.get("count", _DEFAULT_COUNT)), _MAX_COUNT)

    # DDGS().text() is a synchronous, blocking call (no async client) —
    # run it off the event loop so it doesn't stall other in-flight requests.
    results = await asyncio.to_thread(
        lambda: list(DDGS().text(query=query, max_results=count))
    )

    if not results:
        return f"No web results for '{query}'."

    lines = []
    for r in results[:count]:
        title = r.get("title", "(no title)")
        url = r.get("href", "")
        snippet = r.get("body", "")
        lines.append(f"- {title}\n  {url}\n  {snippet}")

    text = "\n".join(lines)
    return text[:_SNIPPET_MAX]


TOOLS = [
    ToolDefinition(
        name="search_web",
        description=(
            "Search the web for current information (news, facts, prices, "
            "anything outside your own knowledge). Summarize the results in "
            "your own words — don't just read out titles and URLs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "count": {"type": "integer", "description": f"number of results, default {_DEFAULT_COUNT}"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=search_web,
        requires_google=False,
    ),
]

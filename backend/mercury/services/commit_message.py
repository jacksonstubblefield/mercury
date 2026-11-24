"""Write a commit message for the pending changes with a single, tool-less Claude call."""

from __future__ import annotations

import re
from typing import Any, Callable

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock

from .. import config

SYSTEM_PROMPT = """You write git commit messages. You are given a diff and reply with only the commit message: \
no preamble, no explanation, no code fences.

- First line: a summary in the imperative mood ("Add", "Fix", "Rename"), at most 72 characters, no trailing period.
- If the change needs more explanation, add a blank line and then a short body wrapped at 72 characters that \
says what changed and why. Use "-" bullets for several separate changes. Leave the body out for small, obvious changes.
- If the recent commit subjects follow a convention (such as "feat:" / "fix:" prefixes or a component tag), follow it.
- Describe what the diff actually does. Don't invent motivation the diff doesn't show."""


def build_prompt(ctx: dict) -> str:
    parts = []
    if ctx.get("branch"):
        parts.append(f"Branch: {ctx['branch']}")
    if ctx.get("recent_subjects"):
        parts.append("Recent commit subjects, newest first:\n" + "\n".join(f"- {s}" for s in ctx["recent_subjects"]))
    scope = "staged changes" if ctx["staged_only"] else "changes (nothing is staged, so all changes will be committed)"
    parts.append(f"Summary of {scope}:\n{ctx['stat']}")
    diff = ctx["diff"]
    if ctx.get("truncated"):
        diff += "\n[diff truncated; use the summary above for the remaining files]"
    parts.append(f"Diff:\n{diff}")
    parts.append("Write the commit message.")
    return "\n\n".join(parts)


def clean_message(text: str) -> str:
    text = text.strip()
    fenced = re.fullmatch(r"```[a-zA-Z]*\n(.*?)\n?```", text, flags=re.S)
    if fenced:
        text = fenced.group(1).strip()
    return text


async def generate(root: str, ctx: dict, client_factory: Callable[[ClaudeAgentOptions], Any]) -> str:
    options = ClaudeAgentOptions(
        cwd=root,
        model=config.COMMIT_MESSAGE_MODEL,
        system_prompt=SYSTEM_PROMPT,
        tools=[],  # the diff is in the prompt; no file or shell access needed
        max_turns=1,
        setting_sources=[],  # don't load the repo's settings, hooks or CLAUDE.md
        strict_mcp_config=True,
    )
    texts: list[str] = []
    result: str | None = None
    async with client_factory(options) as client:
        await client.query(build_prompt(ctx))
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                texts.extend(b.text for b in message.content if isinstance(b, TextBlock))
            elif isinstance(message, ResultMessage):
                if message.is_error:
                    raise RuntimeError(message.result or f"Claude couldn't write a message ({message.subtype}).")
                result = message.result
    text = clean_message(result or "\n".join(texts))
    if not text:
        raise RuntimeError("Claude returned an empty commit message.")
    return text

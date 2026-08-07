# Browser Implementation Plan

## Overview

Build a Chainlit-based browser interface that provides a frictionless chat experience with a server-hosted LLM. Chainlit acts as an MCP client connecting to `stargazer serve` over stdio for tool discovery and execution. LiteLLM handles LLM inference with provider flexibility.

## Prerequisites

- MCP server (`mcp_server.md`) — completed
- Storage client refactor (`storage_client_refactor.md`) — completed
- STARGAZER_MODE configuration (`stargazer_mode_config.md`) — completed

## Current State

- MCP server exists at `src/stargazer/server.py` with all tools, resources, prompts
- All bioinformatics tasks and workflows exist in Python
- No browser frontend

## Target State

```
src/stargazer/
├── server.py              # MCP server (unchanged — single source of truth for tools)
├── app.py                 # Chainlit application entry point
├── tasks/                 # Unchanged
├── workflows/             # Unchanged
├── types/                 # Unchanged
└── utils/                 # Unchanged

.chainlit/
└── config.toml            # Chainlit configuration (MCP, auth, features)
```

## Implementation Plan

### Phase 1: Chainlit + MCP Connection

1. Add dependencies to `pyproject.toml`:

```toml
[project.optional-dependencies]
browser = ["chainlit", "litellm"]
```

2. Configure Chainlit MCP in `.chainlit/config.toml`:

```toml
[features.mcp.stdio]
    enabled = true
    allowed_executables = ["stargazer"]
```

3. Create `src/stargazer/app.py` — Chainlit entry point with MCP connection:

```python
import json
import chainlit as cl
from mcp import ClientSession
from litellm import acompletion


@cl.on_mcp_connect
async def on_mcp_connect(connection, session: ClientSession):
    """Discover tools from stargazer MCP server on connection."""
    result = await session.list_tools()
    tools = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.inputSchema,
            },
        }
        for t in result.tools
    ]

    mcp_tools = cl.user_session.get("mcp_tools", {})
    mcp_tools[connection.name] = tools
    cl.user_session.set("mcp_tools", mcp_tools)


@cl.on_mcp_disconnect
async def on_mcp_disconnect(name: str, session: ClientSession):
    """Clean up tools on MCP disconnect."""
    mcp_tools = cl.user_session.get("mcp_tools", {})
    mcp_tools.pop(name, None)
    cl.user_session.set("mcp_tools", mcp_tools)
```

4. Verify MCP connection works: `chainlit run src/stargazer/app.py`

### Phase 2: LiteLLM Integration + Tool Loop

1. Configure LiteLLM model via environment variable:

```bash
LITELLM_MODEL=provider/model-name  # e.g. anthropic/claude-haiku-4-5-20251001
```

2. Implement the chat message handler with LLM tool loop:

```python
import os

MODEL = os.environ.get("LITELLM_MODEL", "anthropic/claude-haiku-4-5-20251001")


@cl.on_message
async def on_message(message: cl.Message):
    """Handle user message — run LLM ↔ MCP tool loop."""
    history = cl.user_session.get("history", [])
    mcp_tools = cl.user_session.get("mcp_tools", {})

    # Flatten all MCP tools into a single list for the LLM
    all_tools = [tool for tools in mcp_tools.values() for tool in tools]

    history.append({"role": "user", "content": message.content})

    while True:
        response = await acompletion(
            model=MODEL,
            messages=history,
            tools=all_tools if all_tools else None,
            stream=True,
        )

        assistant_msg, tool_calls = await stream_response(response)
        history.append(assistant_msg)

        if not tool_calls:
            break

        # Execute each tool call via MCP
        for tc in tool_calls:
            result = await execute_tool_via_mcp(tc)
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": result,
                }
            )

    cl.user_session.set("history", history)
```

3. Implement streaming response handler:

```python
async def stream_response(response) -> tuple[dict, list]:
    """Stream LLM response to browser, collect tool calls."""
    msg = cl.Message(content="")
    full_content = ""
    tool_calls = []
    tool_call_chunks = {}

    async for chunk in response:
        delta = chunk.choices[0].delta

        # Stream text tokens
        if delta.content:
            full_content += delta.content
            await msg.stream_token(delta.content)

        # Accumulate tool call chunks
        if delta.tool_calls:
            for tc_chunk in delta.tool_calls:
                idx = tc_chunk.index
                if idx not in tool_call_chunks:
                    tool_call_chunks[idx] = {
                        "id": tc_chunk.id,
                        "function": {"name": "", "arguments": ""},
                    }
                if tc_chunk.id:
                    tool_call_chunks[idx]["id"] = tc_chunk.id
                if tc_chunk.function:
                    if tc_chunk.function.name:
                        tool_call_chunks[idx]["function"]["name"] += (
                            tc_chunk.function.name
                        )
                    if tc_chunk.function.arguments:
                        tool_call_chunks[idx]["function"]["arguments"] += (
                            tc_chunk.function.arguments
                        )

    await msg.send()

    # Build assistant message for history
    assistant_msg = {"role": "assistant", "content": full_content}
    if tool_call_chunks:
        assistant_msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": tc["function"],
            }
            for tc in tool_call_chunks.values()
        ]
        tool_calls = assistant_msg["tool_calls"]

    return assistant_msg, tool_calls
```

### Phase 3: MCP Tool Execution

1. Implement tool execution via MCP session with Chainlit step rendering:

```python
def find_mcp_for_tool(tool_name: str) -> str | None:
    """Find which MCP connection provides a given tool."""
    mcp_tools = cl.user_session.get("mcp_tools", {})
    for mcp_name, tools in mcp_tools.items():
        for tool in tools:
            if tool["function"]["name"] == tool_name:
                return mcp_name
    return None


@cl.step(type="tool")
async def execute_tool_via_mcp(tool_call) -> str:
    """Execute a tool call against the MCP server, render as Chainlit step."""
    name = tool_call["function"]["name"]
    args = json.loads(tool_call["function"]["arguments"])

    mcp_name = find_mcp_for_tool(name)
    if not mcp_name:
        return json.dumps({"error": f"No MCP server provides tool: {name}"})

    mcp_session, _ = cl.context.session.mcp_sessions.get(mcp_name)
    result = await mcp_session.call_tool(name, args)

    # Render in Chainlit step
    cl.context.current_step.input = json.dumps(args, indent=2)
    cl.context.current_step.output = str(result.content)

    return str(result.content)
```

### Phase 4: File Handling

1. **Upload**: Use Chainlit's built-in file upload. Files attached to messages are available as `message.elements`. The LLM is informed of uploads and can use the `upload_file` MCP tool to store them on Pinata:

```python
@cl.on_message
async def on_message(message: cl.Message):
    # Handle file attachments
    if message.elements:
        file_descriptions = []
        for element in message.elements:
            file_descriptions.append(
                f"User uploaded file: {element.name} (path: {element.path})"
            )
        # Prepend file info to the user message for the LLM
        message.content = "\n".join(file_descriptions) + "\n" + message.content

    # ... rest of chat handler
```

2. **Download**: Output files stored on Pinata have gateway URLs. The MCP server returns these URLs in tool results. The LLM includes them in responses as clickable links.

### Phase 5: GitHub OAuth Authentication

1. Register a GitHub OAuth App at https://github.com/settings/apps
   - Set callback URL to `CHAINLIT_URL/auth/oauth/github/callback`
   - For local dev: `http://localhost:8000/auth/oauth/github/callback`

2. Add environment variables:

```bash
OAUTH_GITHUB_CLIENT_ID=...
OAUTH_GITHUB_CLIENT_SECRET=...
CHAINLIT_URL=https://stargazer.example.com  # required for production behind reverse proxy
```

3. Implement OAuth callback in `app.py`:

```python
from typing import Optional


@cl.oauth_callback
def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: dict[str, str],
    default_user: cl.User,
) -> Optional[cl.User]:
    # Allow all authenticated GitHub users (or restrict by org/username)
    return default_user
```

4. Optionally restrict to specific GitHub org members:

```python
@cl.oauth_callback
def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: dict[str, str],
    default_user: cl.User,
) -> Optional[cl.User]:
    # Example: restrict to org members
    # if raw_user_data.get("login") not in ALLOWED_USERS:
    #     return None
    return default_user
```

### Phase 6: Launch Configuration

1. Add CLI entry point for the browser:

```bash
stargazer ui  # launches Chainlit app
```

2. Or run directly:

```bash
chainlit run src/stargazer/app.py --port 8080
```

3. Required environment variables for the hosted server:

```bash
# LLM
LITELLM_MODEL=provider/model-name    # LLM model identifier
PROVIDER_API_KEY=...                  # API key for the LLM provider (var name depends on provider)

# Auth
OAUTH_GITHUB_CLIENT_ID=...           # GitHub OAuth app client ID
OAUTH_GITHUB_CLIENT_SECRET=...       # GitHub OAuth app client secret
CHAINLIT_URL=https://stargazer.example.com  # Public URL (for OAuth callback)

# Infrastructure
PINATA_JWT=...                        # Pinata storage
STARGAZER_MODE=cloud                  # Union execution mode
```

4. For production, run behind a reverse proxy (nginx, Caddy) with TLS

### Phase 7: Testing

1. **Unit tests**:
   - MCP tool definitions are correctly converted to OpenAI function calling format
   - `stream_response()` correctly accumulates tool call chunks
   - `find_mcp_for_tool()` returns correct MCP connection name

2. **Integration tests**:
   - Chainlit connects to `stargazer serve` over stdio
   - Tool discovery returns all expected tools
   - Tool execution round-trips through MCP correctly

3. **Manual / E2E**:
   - Full chat flow in browser
   - LLM calls tools, results render as steps
   - File upload works
   - GitHub OAuth blocks unauthenticated access

## File Changes

| File | Change |
|------|--------|
| `src/stargazer/app.py` | **New** — Chainlit application (MCP client + LiteLLM chat loop) |
| `.chainlit/config.toml` | **New** — Chainlit configuration (MCP stdio, auth) |
| `pyproject.toml` | **Modified** — add `chainlit`, `litellm` optional deps |

## Design Decisions

1. **MCP wire protocol, not direct imports**: Chainlit's native MCP integration only supports wire protocols (stdio, SSE, streamable HTTP). The Chainlit app connects to `stargazer serve` over stdio. This keeps `server.py` as the single source of truth for tool definitions — tools are defined once and consumed by both CLI users (via their MCP client) and browser users (via Chainlit's MCP client).

2. **LiteLLM for provider abstraction**: LiteLLM normalizes 100+ LLM providers to the OpenAI-compatible format. The model is specified as `provider/model-name`. Key capabilities used:
   - `acompletion(stream=True)` for async streaming with tool calls
   - `supports_function_calling(model)` to verify provider capability
   - Token usage tracking for cost monitoring
   - Provider swap by changing one environment variable, no code changes

3. **Chainlit over custom React SPA**: Chainlit provides the full chat UI, streaming, step rendering, file upload, MCP client, and auth out of the box. No TypeScript, no Vite, no separate build pipeline. One Python file.

4. **No `frontend/` directory**: The entire browser frontend is `app.py` plus Chainlit's built-in UI and `.chainlit/config.toml`. No separate frontend codebase.

5. **GitHub OAuth for auth**: Since the server pays for LLM inference, unauthenticated access is not an option. Chainlit's built-in GitHub OAuth handles this — no separate auth service needed. The `@cl.oauth_callback` handler can optionally restrict access to specific users or org members.

6. **Union for all task execution**: The Chainlit server never runs bioinformatics tools locally. All tool calls go through MCP to `stargazer serve`, which submits work to Union. This keeps the web server fit for purpose — it handles chat, not compute.

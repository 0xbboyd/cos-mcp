# Hermes Agent — Memory Provider Plugin System (Build-Ready Research)

> **Purpose:** Single source of truth for building a HydraDB-backed memory provider
> plugin for Hermes Agent, shared across all of the user's profiles.
>
> **Method:** Official docs at
> https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin
> (read in full) + cross-linked pages (memory user-guide, configuration reference)
> cross-referenced against the installed source at `~/.hermes/hermes-agent/`
> (git). All code excerpts are verbatim from the installed source.
>
> **Hermes source root:** `~/.hermes/hermes-agent/` (referred to as `$SRC` below).
> **Profiles:** `~/.hermes/profiles/<name>/`; active profile = `default`.
> **Read-only research — no source files were modified.**

---

## 0. TL;DR — The Contract a New Provider Must Implement

A memory provider is a Python class extending `agent.memory_provider.MemoryProvider`
(ABC), shipped as a plugin directory `plugins/memory/<name>/` (bundled) or
`$HERMES_HOME/plugins/<name>/` (user-installed), selected by the `memory.provider`
config key. The hard requirements are:

1. `name` (property) → unique identifier string.
2. `is_available() -> bool` → **no network calls**; just check creds/deps.
3. `initialize(session_id, **kwargs) -> None` → connect/create resources. `kwargs`
   always includes `hermes_home` and `platform`.
4. `get_tool_schemas() -> list[dict]` → OpenAI function-calling schemas (can be `[]`).
5. `handle_tool_call(tool_name, args, **kwargs) -> str` → JSON-string result (only
   required if `get_tool_schemas()` is non-empty).
6. `get_config_schema() -> list[dict]` → field descriptors for `hermes memory setup`
   (can be `[]`).
7. `save_config(values, hermes_home) -> None` → write non-secret config to your
   native location (no-op if env-var-only).
8. A module-level `register(ctx)` function that calls
   `ctx.register_memory_provider(MyProvider())`.
9. A `plugin.yaml` manifest (name, version, description, optional
   `pip_dependencies` / `external_dependencies` / `requires_env` / `hooks`).

Optional but commonly implemented: `system_prompt_block()`, `prefetch()`,
`queue_prefetch()`, `sync_turn()` (MUST be non-blocking), `shutdown()`,
`on_memory_write()`, `on_session_end()`, `on_pre_compress()`,
`on_session_switch()`, `on_turn_start()`, `on_delegation()`, plus non-ABC hooks
`post_setup()` and `get_status_config()`.

**One external provider at a time.** Built-in `MEMORY.md`/`USER.md` is always
active alongside it.

---

## 1. The Memory Provider Interface — `MemoryProvider` ABC

**File:** `$SRC/agent/memory_provider.py` (296 lines). This is the authoritative
contract. Every method, its signature, return type, and responsibility:

### 1.1 Verbatim ABC definition

```python
# $SRC/agent/memory_provider.py  (verbatim, full file)
"""Abstract base class for pluggable memory providers.

Memory providers give the agent persistent recall across sessions.
The MemoryManager enforces a one-external-provider limit to prevent
tool schema bloat and conflicting memory backends.

External providers (Honcho, Hindsight, Mem0, etc.) are registered
and managed via MemoryManager. Only one external provider runs at a
time.

Registration:
  Plugins ship in plugins/memory/<name>/ and are activated via
  the memory.provider config key.

Lifecycle (called by MemoryManager, wired in run_agent.py):
  initialize()          — connect, create resources, warm up
  system_prompt_block()  — static text for the system prompt
  prefetch(query)        — background recall before each turn
  sync_turn(user, asst)  — async write after each turn
  get_tool_schemas()     — tool schemas to expose to the model
  handle_tool_call()     — dispatch a tool call
  shutdown()             — clean exit

Optional hooks (override to opt in):
  on_turn_start(turn, message, **kwargs) — per-turn tick with runtime context
  on_session_end(messages)               — end-of-session extraction
  on_session_switch(new_session_id, **kwargs) — mid-process session_id rotation
  on_pre_compress(messages) -> str       — extract before context compression
  on_memory_write(action, target, content, metadata=None) — mirror built-in memory writes
  on_delegation(task, result, **kwargs)  — parent-side observation of subagent work
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryProvider(ABC):
    """Abstract base class for memory providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this provider (e.g. 'builtin', 'honcho', 'hindsight')."""

    # -- Core lifecycle (implement these) ------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider is configured, has credentials, and is ready.

        Called during agent init to decide whether to activate the provider.
        Should not make network calls — just check config and installed deps.
        """

    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize for a session.

        Called once at agent startup. May create resources (banks, tables),
        establish connections, start background threads, etc.

        kwargs always include:
          - hermes_home (str): The active HERMES_HOME directory path. Use this
            for profile-scoped storage instead of hardcoding ``~/.hermes``.
          - platform (str): "cli", "telegram", "discord", "cron", etc.

        kwargs may also include:
          - agent_context (str): "primary", "subagent", "cron", or "flush".
            Providers should skip writes for non-primary contexts (cron system
            prompts would corrupt user representations).
          - agent_identity (str): Profile name (e.g. "coder"). Use for
            per-profile provider identity scoping.
          - agent_workspace (str): Shared workspace name (e.g. "hermes").
          - parent_session_id (str): For subagents, the parent's session_id.
          - user_id (str): Platform user identifier (gateway sessions).
          - user_id_alt (str): Optional alternate stable platform user identifier.
        """

    def system_prompt_block(self) -> str:
        """Return text to include in the system prompt.

        Called during system prompt assembly. Return empty string to skip.
        This is for STATIC provider info (instructions, status). Prefetched
        recall context is injected separately via prefetch().
        """
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall relevant context for the upcoming turn.

        Called before each API call. Return formatted text to inject as
        context, or empty string if nothing relevant. Implementations
        should be fast — use background threads for the actual recall
        and return cached results here.

        session_id is provided for providers serving concurrent sessions
        (gateway group chats, cached agents). Providers that don't need
        per-session scoping can ignore it.
        """
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Queue a background recall for the NEXT turn.

        Called after each turn completes. The result will be consumed
        by prefetch() on the next turn. Default is no-op — providers
        that do background prefetching should override this.
        """

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Persist a completed turn to the backend.

        Called after each turn. Should be non-blocking — queue for
        background processing if the backend has latency.

        ``messages`` is the OpenAI-style conversation message list as of the
        completed turn, including any assistant tool calls and tool results.
        Providers that do not need raw turn context can ignore it.
        """

    @abstractmethod
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas this provider exposes.

        Each schema follows the OpenAI function calling format:
        {"name": "...", "description": "...", "parameters": {...}}

        Return empty list if this provider has no tools (context-only).
        """

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """Handle a tool call for one of this provider's tools.

        Must return a JSON string (the tool result).
        Only called for tool names returned by get_tool_schemas().
        """
        raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")

    def shutdown(self) -> None:
        """Clean shutdown — flush queues, close connections."""

    # -- Optional hooks (override to opt in) ---------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Called at the start of each turn with the user message.

        Use for turn-counting, scope management, periodic maintenance.

        kwargs may include: remaining_tokens, model, platform, tool_count.
        Providers use what they need; extras are ignored.
        """

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Called when a session ends (explicit exit or timeout).

        Use for end-of-session fact extraction, summarization, etc.
        messages is the full conversation history.

        NOT called after every turn — only at actual session boundaries
        (CLI exit, /reset, gateway session expiry).
        """

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        """Called when the agent switches session_id mid-process.

        Fires on ``/resume``, ``/branch``, ``/reset``, ``/new`` (CLI), the
        gateway equivalents, and context compression — any path that
        reassigns ``AIAgent.session_id`` without tearing the provider down.

        Providers that cache per-session state in ``initialize()``
        (``_session_id``, ``_document_id``, accumulated turn buffers,
        counters) should update or reset that state here so subsequent
        writes land in the correct session's record.

        Parameters
        ----------
        new_session_id:
            The session_id the agent just switched to.
        parent_session_id:
            The previous session_id, if meaningful — set for ``/branch``
            (fork lineage), context compression (continuation lineage),
            and ``/resume`` (the session we're leaving). Empty string
            when no lineage applies.
        reset:
            ``True`` when this is a genuinely new conversation, not a
            resumption of an existing one. Fired by ``/reset`` / ``/new``.
            Providers should flush accumulated per-session buffers
            (``_session_turns``, ``_turn_counter``, etc.) when this is
            set. ``False`` for ``/resume`` / ``/branch`` / compression
            where the logical conversation continues under the new id.
        rewound:
            ``True`` if session_id is unchanged but the transcript was
            truncated; providers caching per-turn document state should
            invalidate.

        Default is no-op for backward compatibility.
        """

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Called before context compression discards old messages.

        Use to extract insights from messages about to be compressed.
        messages is the list that will be summarized/discarded.

        Return text to include in the compression summary prompt so the
        compressor preserves provider-extracted insights. Return empty
        string for no contribution (backwards-compatible default).
        """
        return ""

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        """Called on the PARENT agent when a subagent completes.

        The parent's memory provider gets the task+result pair as an
        observation of what was delegated and what came back. The subagent
        itself has no provider session (skip_memory=True).

        task: the delegation prompt
        result: the subagent's final response
        child_session_id: the subagent's session_id
        """

    def get_config_schema(self) -> List[Dict[str, Any]]:
        """Return config fields this provider needs for setup.

        Used by 'hermes memory setup' to walk the user through configuration.
        Each field is a dict with:
          key:         config key name (e.g. 'api_key', 'mode')
          description: human-readable description
          secret:      True if this should go to .env (default: False)
          required:    True if required (default: False)
          default:     default value (optional)
          choices:     list of valid values (optional)
          url:         URL where user can get this credential (optional)
          env_var:     explicit env var name for secrets (default: auto-generated)

        Return empty list if no config needed (e.g. local-only providers).
        """
        return []

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Write non-secret config to the provider's native location.

        Called by 'hermes memory setup' after collecting user inputs.
        ``values`` contains only non-secret fields (secrets go to .env).
        ``hermes_home`` is the active HERMES_HOME directory path.

        Providers with native config files (JSON, YAML) should override
        this to write to their expected location. Providers that use only
        env vars can leave the default (no-op).

        All new memory provider plugins MUST implement either:
        - save_config() for native config file formats, OR
        - use only env vars (in which case get_config_schema() fields
          should all have ``env_var`` set and this method stays no-op).
        """

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Called when the built-in memory tool writes an entry.

        action: 'add', 'replace', or 'remove'
        target: 'memory' or 'user'
        content: the entry content
        metadata: structured provenance for the write, when available. Common
          keys include ``write_origin``, ``execution_context``, ``session_id``,
          ``parent_session_id``, ``platform``, and ``tool_name``.

        Use to mirror built-in memory writes to your backend.
        """
```

### 1.2 Method responsibility summary

| Method | Signature | Returns | Abstract? | When called | Responsibility |
|---|---|---|---|---|---|
| `name` | `@property` | `str` | **Yes** | always | unique provider id (e.g. `"mem0"`) |
| `is_available` | `() -> bool` | `bool` | **Yes** | agent init, before activation; `hermes memory status` | check creds/deps installed — **no network** |
| `initialize` | `(session_id: str, **kwargs) -> None` | `None` | **Yes** | once at agent startup (`MemoryManager.initialize_all`) | connect, create resources, warm up, capture `hermes_home`/`user_id`/`agent_identity` from kwargs |
| `system_prompt_block` | `() -> str` | `str` (default `""`) | No | system prompt assembly (`MemoryManager.build_system_prompt`) | STATIC provider info/instructions; NOT recall |
| `prefetch` | `(query: str, *, session_id="") -> str` | `str` (default `""`) | No | before each API call (`MemoryManager.prefetch_all`) | return cached recalled context for this turn; must be fast |
| `queue_prefetch` | `(query: str, *, session_id="") -> None` | `None` | No | after each turn (`MemoryManager.queue_prefetch_all`) | pre-warm recall for the NEXT turn |
| `sync_turn` | `(user_content, assistant_content, *, session_id="", messages=None) -> None` | `None` | No | after each completed turn (`MemoryManager.sync_all`, **background worker**) | persist the turn; **MUST be non-blocking** |
| `get_tool_schemas` | `() -> list[dict]` | `list[dict]` | **Yes** | after init, tool injection | OpenAI function-calling schemas (`{"name","description","parameters"}`); `[]` if context-only |
| `handle_tool_call` | `(tool_name, args, **kwargs) -> str` | `str` (JSON) | No (raises `NotImplementedError` by default) | when the model calls one of your tools | dispatch; return a JSON string |
| `shutdown` | `() -> None` | `None` | No | process/session exit (`MemoryManager.shutdown_all`, reverse order) | flush queues, close connections |
| `on_turn_start` | `(turn_number, message, **kwargs) -> None` | `None` | No | start of each turn (before `prefetch_all`) | turn-counting, periodic maintenance |
| `on_session_end` | `(messages) -> None` | `None` | No | real session boundaries only (CLI exit, `/reset`, gateway expiry) | end-of-session extraction/flush |
| `on_session_switch` | `(new_session_id, *, parent_session_id="", reset=False, rewound=False, **kwargs) -> None` | `None` | No | `/resume`,`/branch`,`/reset`,`/new`, compression | refresh cached per-session state (`_session_id`, buffers) |
| `on_pre_compress` | `(messages) -> str` | `str` (default `""`) | No | before context compression discards messages | extract insights; return text to fold into the compression summary |
| `on_delegation` | `(task, result, *, child_session_id="", **kwargs) -> None` | `None` | No | parent agent, when a subagent completes | observe delegated task + result |
| `get_config_schema` | `() -> list[dict]` | `list[dict]` (default `[]`) | No | `hermes memory setup` / `status` | declare config fields |
| `save_config` | `(values: dict, hermes_home: str) -> None` | `None` | No | after `hermes memory setup` collects inputs | write non-secret config to native location |
| `on_memory_write` | `(action, target, content, metadata=None) -> None` | `None` | No | whenever the built-in `memory` tool writes (add/replace/remove) | mirror built-in writes to your backend |

### 1.3 Non-ABC optional hooks (discovered in source, not in the ABC)

These are conventional hooks the CLI wizard/status check for via `hasattr`. A new
provider **may** implement them but they are not part of the ABC:

- **`post_setup(self, hermes_home: str, config: dict) -> None`** — if present,
  `hermes memory setup` delegates the *entire* setup to it (the provider runs its
  own wizard, connection test, and writes `memory.provider` itself), bypassing the
  generic schema-driven wizard. Used by `honcho`, `hindsight`, `openviking`.
- **`get_status_config(self, provider_config: dict) -> dict`** — if present,
  `hermes memory status` calls it to transform/redact the displayed config block.
  Used by `openviking`.

---

## 2. Plugin Discovery + Registration Mechanism

**File:** `$SRC/plugins/memory/__init__.py` (450 lines) — the discovery engine.
**Wiring:** `$SRC/agent/agent_init.py` (~lines 1138–1198).

### 2.1 Where plugins live (two scan roots)

1. **Bundled:** `$SRC/plugins/memory/<name>/` (shipped with hermes-agent).
2. **User-installed:** `$HERMES_HOME/plugins/<name>/` (i.e.
   `~/.hermes/plugins/<name>/` for the default profile, or
   `~/.hermes/profiles/<name>/plugins/<name>/` when a profile overrides
   `HERMES_HOME`).

> **For a cross-profile shared plugin:** because the user-installed root is
> `$HERMES_HOME/plugins/`, and each profile has its own `HERMES_HOME`, a plugin
> dropped into a *shared* `~/.hermes/plugins/` is only seen by the profile whose
> `HERMES_HOME` is `~/.hermes`. To share one plugin across ALL profiles you must
> either (a) install it into each profile's `$HERMES_HOME/plugins/`, (b) ship it
> in-tree under `$SRC/plugins/memory/<name>/` (built-in precedence, seen by every
> profile), or (c) point every profile's `HERMES_HOME` at a shared data dir. The
> in-tree option is the cleanest "shared across all profiles" path. See §6.

### 2.2 Directory shape

```
plugins/memory/<name>/
├── __init__.py      # MemoryProvider subclass + register(ctx) entry point
├── plugin.yaml      # Metadata: name, version, description, pip_dependencies, hooks
├── README.md        # Setup instructions, config reference, tools
└── cli.py           # OPTIONAL: register_cli(subparser) for `hermes <name> ...`
```

Subdirectories starting with `_` or `.` are skipped. A directory qualifies as a
memory provider if its `__init__.py` contains the text `register_memory_provider`
or `MemoryProvider` (cheap text scan, no import — `_is_memory_provider_dir()`).

### 2.3 How a plugin is loaded (`_load_provider_from_dir`)

The loader tries **two** patterns, in order:

1. **`register(ctx)` pattern (preferred / how all bundled plugins are written):**
   the module defines a top-level `register(ctx)` function. Hermes passes a fake
   context (`_ProviderCollector`) whose `ctx.register_memory_provider(provider)`
   captures the instance.
   ```python
   def register(ctx) -> None:
       ctx.register_memory_provider(MyProvider())
   ```
2. **Fallback (subclass auto-instantiation):** if no `register`, the loader scans
   `dir(mod)` for a class that `issubclass(attr, MemoryProvider) and attr is not
   MemoryProvider` and instantiates it with no args.

Module namespacing: bundled plugins import as `plugins.memory.<name>`; user
plugins import as `_hermes_user_memory.<name>` (a synthetic parent package is
registered in `sys.modules` so relative imports like `from .store import X` work).
Sibling `.py` files in the plugin dir are pre-registered as submodules so
`from . import client` resolves.

### 2.4 How the active provider is selected and activated

1. **Config read:** `memory.provider` in `config.yaml` (read by
   `_get_active_memory_provider()` → `cfg_get(config, "memory", "provider")`).
   Empty/missing ⇒ built-in only, no `MemoryManager` is created.
2. **Load:** `load_memory_provider(name)` → `find_provider_dir(name)` (bundled
   first, then user) → `_load_provider_from_dir`.
3. **Availability gate:** `provider.is_available()` must return `True`, else the
   provider is silently not added (logged).
4. **Register:** `MemoryManager().add_provider(provider)`.
5. **Init:** `MemoryManager.initialize_all(session_id=..., **kwargs)`.

Verbatim wiring from `$SRC/agent/agent_init.py` (lines 1138–1198):

```python
# Memory provider plugin (external — one at a time, alongside built-in)
# Reads memory.provider from config to select which plugin to activate.
agent._memory_manager = None
if not skip_memory:
    try:
        _mem_provider_name = mem_config.get("provider", "") if mem_config else ""

        if _mem_provider_name and _mem_provider_name.strip():
            from agent.memory_manager import MemoryManager as _MemoryManager
            from plugins.memory import load_memory_provider as _load_mem
            agent._memory_manager = _MemoryManager()
            _mp = _load_mem(_mem_provider_name)
            if _mp and _mp.is_available():
                agent._memory_manager.add_provider(_mp)
            if agent._memory_manager.providers:
                _init_kwargs = {
                    "session_id": agent.session_id,
                    "platform": platform or "cli",
                    "hermes_home": str(get_hermes_home()),
                    "agent_context": "primary",
                }
                # ... threads session_title, user_id, user_id_alt, user_name,
                #     chat_id/chat_name/chat_type, thread_id, gateway_session_key,
                #     agent_identity (active profile name), agent_workspace="hermes"
                agent._memory_manager.initialize_all(**_init_kwargs)
```

Key kwargs threaded into `initialize()`:
`session_id`, `platform`, `hermes_home`, `agent_context` (`"primary"`),
`agent_identity` (active **profile name** via
`hermes_cli.profiles.get_active_profile_name()`), `agent_workspace` (`"hermes"`),
`session_title`, `user_id`, `user_id_alt`, `user_name`, `chat_id`, `chat_name`,
`chat_type`, `thread_id`, `gateway_session_key`, `parent_session_id` (subagents).

> **Note:** there is **no `MemoryProvider` subclass named `"builtin"`**. The
> built-in `MEMORY.md`/`USER.md` memory is handled entirely by
> `tools/memory_tool.py:MemoryStore` and injected into the system prompt
> separately (see §4). `MemoryManager` only ever holds the *external* provider.
> The `provider.name == "builtin"` checks in `MemoryManager.add_provider` /
> `on_memory_write` are defensive guards, not a registered builtin provider.

### 2.5 The `MemoryManager` orchestrator (`$SRC/agent/memory_manager.py`)

Single integration point. Enforces the **one-external-provider rule**
(`add_provider` rejects a second non-builtin provider with a warning). It:

- **Rejects core-tool-name collisions:** any provider tool whose `name` is in
  `toolsets._HERMES_CORE_TOOLS` (e.g. `clarify`, `delegate_task`) is dropped from
  the routing table with a warning — "Core tools always win."
- **Runs all post-turn work on a single background worker**
  (`ThreadPoolExecutor(max_workers=1, thread_name_prefix="mem-sync")`, created
  lazily). `sync_all` and `queue_prefetch_all` dispatch to this worker so a slow
  provider never stalls the turn. Writes are serialized (turn N lands before N+1).
- **Fail-open, per-provider:** every provider call is wrapped in `try/except`;
  one provider's failure never blocks another. Failures are logged at `debug`/
  `warning`.
- **Skill-scaffolding stripping:** before fan-out, `_strip_skill_scaffolding`
  extracts just the user's instruction from `/skill`-expanded messages so
  providers don't ingest prompt scaffolding.

### 2.6 Plugin CLI subcommands (optional `cli.py`)

`discover_plugin_cli_commands()` reads `memory.provider`, finds the active
plugin's `cli.py`, and imports `register_cli(subparser)`. The resulting
subcommand tree appears as `hermes <provider-name> <subcommand>` **only when
that provider is the active `memory.provider`** (active-provider gating). Handler
convention: `register_cli` builds the argparse tree and
`set_defaults(func=<handler>)`; the handler is also looked up as
`getattr(cli_mod, f"{provider}_command")`. Reference: `plugins/memory/honcho/cli.py`
(13 subcommands, `--target-profile` cross-profile management).

---

## 3. The Exact Config Schema — every `memory.*` key

### 3.1 `DEFAULT_CONFIG["memory"]` (verbatim, `$SRC/hermes_cli/config.py` ~L1850)

```python
# Persistent memory -- bounded curated memory injected into system prompt
"memory": {
    "memory_enabled": True,
    "user_profile_enabled": True,
    # Approval gate for memory writes (add/replace/remove), applied to BOTH
    # foreground agent turns and the background self-improvement review fork
    # ...
    # To disable memory entirely, use memory_enabled: false instead.
    "write_approval": False,
    "memory_char_limit": 2200,   # ~800 tokens at 2.75 chars/token
    "user_char_limit": 1375,     # ~500 tokens at 2.75 chars/token
    # External memory provider plugin (empty = built-in only).
    # Set to a provider name to activate: "openviking", "mem0",
    # "hindsight", "holographic", "retaindb", "byterover".
    # Only ONE external provider is allowed at a time.
    "provider": "",
},
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `memory.memory_enabled` | bool | `True` | master switch for the `memory` store (MEMORY.md). `false` disables memory entirely. |
| `memory.user_profile_enabled` | bool | `True` | master switch for the `user` store (USER.md). |
| `memory.write_approval` | bool | `False` | approval gate for built-in memory writes (add/replace/remove). `true` ⇒ inline prompt (CLI) / staged writes (background). Managed via `/memory approval on\|off`. |
| `memory.memory_char_limit` | int | `2200` | char budget for MEMORY.md (~800 tokens). |
| `memory.user_char_limit` | int | `1375` | char budget for USER.md (~500 tokens). |
| `memory.provider` | str | `""` | **the selector.** Empty ⇒ built-in only. Set to a provider name (e.g. `"mem0"`, `"honcho"`) to activate exactly one external provider. |

### 3.2 Per-provider config blocks + secrets

The generic wizard stores per-provider **non-secret** config under
`config["memory"][<provider_name>]` (e.g. `memory.mem0.user_id`) AND mirrors it to
the provider's native location via `save_config()` (e.g. mem0 writes
`$HERMES_HOME/mem0.json`). **Secrets** (fields with `secret: True` and `env_var`)
go to `$HERMES_HOME/.env` (chmod 0600), never to config.yaml.

So a provider is configured in up to **three** places:
1. `config.yaml` → `memory.provider` (activation) + `memory.<name>.*` (non-secret
   values the wizard collected).
2. `$HERMES_HOME/.env` → `<ENV_VAR>=...` (secrets).
3. Provider-native file (e.g. `$HERMES_HOME/mem0.json`,
   `$HERMES_HOME/supermemory.json`) → written by `save_config()`; read by the
   provider at runtime via `_load_config()`.

### 3.3 `get_config_schema()` field descriptor keys

From the ABC docstring + the wizard implementation
(`$SRC/hermes_cli/memory_setup.py`), each field dict supports:

| Key | Required | Meaning |
|---|---|---|
| `key` | **yes** | config key name (e.g. `"api_key"`, `"mode"`) |
| `description` | no | human-readable label shown by the wizard |
| `secret` | no (default `False`) | `True` ⇒ written to `.env` (requires `env_var`) |
| `required` | no (default `False`) | marks the field required (informational; wizard doesn't hard-enforce) |
| `default` | no | default value |
| `choices` | no | list of valid values → rendered as a curses radiolist picker |
| `url` | no | "where to get this credential" hint shown before prompting |
| `env_var` | no | explicit env var name for secrets (and for non-secrets that should *also* mirror to `.env`) |
| `when` | no | **dict** of `{field: value}` — the field is only prompted when previously-collected fields match (conditional fields) |
| `default_from` | no | **dict** `{field, map}` — dynamic default derived from another field's chosen value |

> **Minimal-schema guidance (from the docs + Supermemory):** keep
> `get_config_schema()` to only fields the user *must* configure (API key,
> required credentials). Put advanced options in a native config file
> (`$HERMES_HOME/<name>.json`) documented in the README. Supermemory's entire
> schema is one field:
> ```python
> def get_config_schema(self):
>     return [{"key": "api_key", "description": "Supermemory API key",
>              "secret": True, "required": True,
>              "env_var": "SUPERMEMORY_API_KEY", "url": "https://supermemory.ai"}]
> ```

### 3.4 Profile overrides

`HERMES_HOME` resolves per-profile (`hermes_constants.get_hermes_home()`). All
config + `.env` + provider-native files + memory files live under the active
`HERMES_HOME`, so switching profiles swaps the entire memory/config namespace.
`MemoryManager.initialize_all` auto-injects `hermes_home=str(get_hermes_home())`
into `initialize()` kwargs, and threads `agent_identity` = active profile name.
**Providers must never hardcode `~/.hermes`** — use the `hermes_home` kwarg or
`get_hermes_home()` (see §7 gotchas).

Configuration precedence (from the configuration docs): CLI args > `config.yaml`
> `.env` > built-in defaults. `${VAR}` substitution is supported in config.yaml.

---

## 4. The Data Model Hermes Expects

Hermes has **two independent memory systems** that coexist:

### 4.1 Built-in memory (always active) — `tools/memory_tool.py`

**Two stores**, stored as `§`-delimited text files under
`$HERMES_HOME/memories/`:

| File | Target string | Char limit | Default | Typical contents |
|---|---|---|---|---|
| `MEMORY.md` | `"memory"` | `memory.memory_char_limit` (2200) | ~8–15 entries | environment facts, project conventions, tool quirks, lessons learned |
| `USER.md` | `"user"` | `memory.user_char_limit` (1375) | ~5–10 entries | name, role, timezone, communication preferences, pet peeves, skill level |

**Entry shape:** plain strings (may be multiline), joined by `ENTRY_DELIMITER =
"\n§\n"`. Read/split by the same delimiter. Deduplicated on load (order-preserving).

**System-prompt injection (frozen snapshot):** at `load_from_disk()` the store
captures `_system_prompt_snapshot` once and never mutates it mid-session
(preserves the LLM prefix cache). `format_for_system_prompt(target)` returns that
frozen block. Rendering (`_render_block`):

```
══════════════════════════════════════════════
MEMORY (your personal notes) [67% — 1,474/2,200 chars]
══════════════════════════════════════════════
User's project is a Rust web service at ~/code/myapi using Axum + SQLx
§
This machine runs Ubuntu 22.04, has Docker and Podman installed
§
User prefers concise responses, dislikes verbose explanations
```
(`USER PROFILE (who the user is) [...]` header for the `user` target.)

**The `memory` tool** (OpenAI schema `MEMORY_SCHEMA`): single tool with actions
`add` / `replace` / `remove` (no `read` — memory is auto-injected). Supports a
batch shape `operations=[{action, content?, old_text?}, ...]` applied atomically
against the **final** char budget. `replace`/`remove` use short unique substring
matching (`old_text`). On overflow, returns a structured error with
`current_entries` + `usage` so the model consolidates in the same turn. A
write-approval gate (`tools/write_approval.py`) can stage writes. Every mutating
op is scanned for prompt-injection/exfil patterns (`tools/threat_patterns.py`,
`scope="strict"`).

**Built-in writes mirror to external providers:** when the `memory` tool writes,
`MemoryManager.on_memory_write(action, target, content, metadata)` is called
(agent_runtime_helpers.py), which fans out to every external provider's
`on_memory_write` (skipping `name=="builtin"`). The manager detects each
provider's `on_memory_write` signature (`keyword` / `positional` / `legacy`) and
passes `metadata` accordingly. `metadata` common keys: `write_origin`,
`execution_context`, `session_id`, `parent_session_id`, `platform`, `tool_name`.

### 4.2 External provider memory (the plugin you build)

An external provider contributes to the conversation through **three channels**:

1. **`system_prompt_block()` → system prompt.** Collected by
   `MemoryManager.build_system_prompt()`, concatenated (each non-empty block
   labeled), appended to the system prompt at assembly time. Use for STATIC
   instructions/status (e.g. "Mem0 Memory. Active. Use mem0_search …").

2. **`prefetch(query, *, session_id="")` → per-turn recalled context.** Called
   once before the tool loop each turn (`agent/turn_context.py`):
   ```python
   ext_prefetch_cache = agent._memory_manager.prefetch_all(_query) or ""
   ```
   The result is wrapped by `build_memory_context_block()` into a fenced block:
   ```
   <memory-context>
   [System note: The following is recalled memory context, NOT new user input.
   Treat as authoritative reference data — this is the agent's persistent memory
   and should inform all responses.]

   <your prefetch() output here>
   </memory-context>
   ```
   This block is injected as a discrete message/context for the turn. Output is
   sanitized (`sanitize_context`) — any pre-existing `<memory-context>` fences or
   system notes the provider returns are stripped (and logged as a warning) so
   providers must NOT pre-wrap their own output. A streaming scrubber
   (`StreamingContextScrubber`) handles fence spans split across stream deltas.

3. **Tools (`get_tool_schemas()` + `handle_tool_call()`).** Schemas are appended
   to the agent's tool surface (`inject_memory_provider_tools`) — but only when
   the `memory` toolset is enabled. Routed by `MemoryManager.handle_tool_call`
   via the `_tool_to_provider` map. Results must be **JSON strings**.

4. **`sync_turn()` (write path).** After each turn, the (user, assistant) pair
   (+ optional `messages`) is sent to the backend on the background worker.

**Targets:** the built-in model uses `target ∈ {"memory", "user"}`. External
providers are free to model data however they like, but `on_memory_write` and the
built-in memory tool use the `{"memory","user"}` target vocabulary — a provider
that mirrors built-in writes should respect it.

**No char limit is imposed on external providers** — the `memory_char_limit` /
`user_char_limit` apply only to the built-in stores. Providers self-limit (e.g.
mem0 `top_k` caps, prefetch returns top-5).

---

## 5. Existing Provider Implementations

Eight bundled providers in `$SRC/plugins/memory/`. Manifests verbatim from each
`plugin.yaml`:

| Provider | `name` | Backend | pip dep | Hooks declared | Description |
|---|---|---|---|---|---|
| **mem0** | `mem0` | Cloud (Mem0 Platform API) | `mem0ai` | — | Server-side LLM fact extraction, semantic search w/ reranking, auto-dedup. Env: `MEM0_API_KEY`/`MEM0_USER_ID`/`MEM0_AGENT_ID` or `$HERMES_HOME/mem0.json`. |
| **honcho** | `honcho` | Cloud (Honcho API) | `honcho-ai` | `on_session_end` | AI-native cross-session user modeling: dialectic Q&A, semantic search, persistent conclusions. Has full `cli.py` (13 subcommands, `--target-profile`). Uses `post_setup`. |
| **hindsight** | `hindsight` | Cloud (`https://api.hindsight.vectorize.io`) or local external | `hindsight-client>=0.6.1` | `on_session_end` | Long-term memory w/ knowledge graph, entity resolution, multi-strategy retrieval. Fields: mode/api_key/api_url/bank_id/recall_budget. Uses `post_setup`. |
| **openviking** | `openviking` | Context database (HTTP) | `httpx` | `on_session_end` | Session-managed memory, automatic extraction, tiered retrieval, filesystem-style knowledge browsing. Uses `post_setup` + `get_status_config`. |
| **holographic** | `holographic` | **Local** SQLite + FTS5 + HRR | (numpy optional) | `on_session_end` | Local fact store with FTS5 search, trust scoring, HRR-based compositional retrieval. `is_available()` always True (SQLite). Has `store.py`/`retrieval.py` submodules. |
| **retaindb** | `retaindb` | Cloud (RetainDB API) | `requests` | — | Cloud memory API with hybrid search and 7 memory types. Env: `RETAINDB_API_KEY`. |
| **byterover** | `byterover` | Local CLI (`brv`) | (external binary) | `on_pre_compress` | Persistent knowledge tree with tiered retrieval via the `brv` CLI. `external_dependencies` w/ install+check. |
| **supermemory** | `supermemory` | Cloud (Supermemory) | `supermemory` | — | Semantic long-term memory: profile recall, semantic search, explicit memory tools, session ingest. Minimal schema (API key only). |
| **builtin** | — | Local files | — | — | `MEMORY.md`/`USER.md` via `tools/memory_tool.py`. Not a `MemoryProvider` subclass; always active. |

---

## 6. Step-by-Step: How to Add a New Provider

### 6.1 In-tree (bundled) — shared across ALL profiles

1. **Create the directory** `$SRC/plugins/memory/hydradb/`:
   ```
   plugins/memory/hydradb/
   ├── __init__.py      # HydraDBMemoryProvider + register(ctx)
   ├── plugin.yaml      # metadata + pip_dependencies
   └── README.md        # setup + config reference
   ```
   (Optional `cli.py` for `hermes hydradb ...` subcommands.)

2. **`plugin.yaml`** (manifest):
   ```yaml
   name: hydradb
   version: 0.1.0
   description: "HydraDB-backed persistent memory — cross-profile recall via the HydraDB backend."
   pip_dependencies:
     - hydradb-client   # whatever your client package is named
   requires_env:
     - HYDRADB_API_KEY
   hooks:
     - on_session_end
     - on_memory_write
   ```
   `pip_dependencies` are **auto-installed** by `hermes memory setup` (via `uv`
   if present, else `pip`; the wizard maps pip-name→import-name for known
   packages). `external_dependencies` (with `name`/`install`/`check`) are shown
   to the user if the `check` command fails. `requires_env` is informational.

3. **`__init__.py`** — implement the ABC + `register`. Use the mem0
   implementation in §6.3 as the template. The skeleton:
   ```python
   from agent.memory_provider import MemoryProvider

   class HydraDBMemoryProvider(MemoryProvider):
       @property
       def name(self) -> str: return "hydradb"
       def is_available(self) -> bool: ...      # no network
       def initialize(self, session_id, **kwargs) -> None: ...
       def get_tool_schemas(self) -> list: ...
       def handle_tool_call(self, tool_name, args, **kwargs) -> str: ...
       def get_config_schema(self) -> list: ...
       def save_config(self, values, hermes_home) -> None: ...
       # optional: system_prompt_block, prefetch, queue_prefetch,
       #           sync_turn (non-blocking!), shutdown, on_memory_write, ...

   def register(ctx) -> None:
       ctx.register_memory_provider(HydraDBMemoryProvider())
   ```

4. **Activate:** `hermes memory setup` → pick `hydradb` (the wizard discovers it
   automatically from `plugins/memory/`), or `hermes memory setup hydradb`. This
   writes `memory.provider: hydradb` to `config.yaml`, installs pip deps, runs
   your `get_config_schema()` prompts, calls `save_config()`, and writes secrets
   to `.env`. Start a new session to activate.

### 6.2 User-installed (packaged as a plugin, per-profile)

Drop the same directory into `$HERMES_HOME/plugins/hydradb/`. Discovery scans
this root too (`_iter_provider_dirs`), bundled takes precedence on name
collisions. The directory must contain `register_memory_provider` or
`MemoryProvider` text in `__init__.py` to be recognized. Relative imports inside
the plugin work (synthetic `_hermes_user_memory` namespace is registered).

> Because `$HERMES_HOME` is per-profile, a user-installed plugin is only visible
> to one profile at a time. For true cross-profile sharing use the in-tree
> approach (§6.1) or a shared `HERMES_HOME`.

### 6.3 Representative implementation — Mem0 (verbatim, full file)

`$SRC/plugins/memory/mem0/__init__.py` — a clean, complete reference covering:
config loading (env + JSON override), circuit breaker, lazy thread-safe client,
background prefetch/sync threads, three tools, `on_memory_write`-compatible
shape, and `register(ctx)`.

```python
# $SRC/plugins/memory/mem0/__init__.py  (verbatim, full file)
"""Mem0 memory plugin — MemoryProvider interface.

Server-side LLM fact extraction, semantic search with reranking, and
automatic deduplication via the Mem0 Platform API.

Original PR #2933 by kartik-mem0, adapted to MemoryProvider ABC.

Config via environment variables:
  MEM0_API_KEY       — Mem0 Platform API key (required)
  MEM0_USER_ID       — User identifier (default: hermes-user)
  MEM0_AGENT_ID      — Agent identifier (default: hermes)

Or via $HERMES_HOME/mem0.json.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# Circuit breaker: after this many consecutive failures, pause API calls
# for _BREAKER_COOLDOWN_SECS to avoid hammering a down server.
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_SECS = 120


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load config from env vars, with $HERMES_HOME/mem0.json overrides."""
    from hermes_constants import get_hermes_home

    config = {
        "api_key": os.environ.get("MEM0_API_KEY", ""),
        "user_id": os.environ.get("MEM0_USER_ID", "hermes-user"),
        "agent_id": os.environ.get("MEM0_AGENT_ID", "hermes"),
        "rerank": True,
        "keyword_search": False,
    }

    config_path = get_hermes_home() / "mem0.json"
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({k: v for k, v in file_cfg.items()
                           if v is not None and v != ""})
        except Exception:
            pass

    return config


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

PROFILE_SCHEMA = {
    "name": "mem0_profile",
    "description": (
        "Retrieve all stored memories about the user — preferences, facts, "
        "project context. Fast, no reranking. Use at conversation start."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SEARCH_SCHEMA = {
    "name": "mem0_search",
    "description": (
        "Search memories by meaning. Returns relevant facts ranked by similarity. "
        "Set rerank=true for higher accuracy on important queries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "rerank": {"type": "boolean", "description": "Enable reranking for precision (default: false)."},
            "top_k": {"type": "integer", "description": "Max results (default: 10, max: 50)."},
        },
        "required": ["query"],
    },
}

CONCLUDE_SCHEMA = {
    "name": "mem0_conclude",
    "description": (
        "Store a durable fact about the user. Stored verbatim (no LLM extraction). "
        "Use for explicit preferences, corrections, or decisions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "conclusion": {"type": "string", "description": "The fact to store."},
        },
        "required": ["conclusion"],
    },
}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class Mem0MemoryProvider(MemoryProvider):
    """Mem0 Platform memory with server-side extraction and semantic search."""

    def __init__(self):
        self._config = None
        self._client = None
        self._client_lock = threading.Lock()
        self._api_key = ""
        self._user_id = "hermes-user"
        self._agent_id = "hermes"
        self._rerank = True
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread = None
        self._sync_thread = None
        # Circuit breaker state
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0

    @property
    def name(self) -> str:
        return "mem0"

    def is_available(self) -> bool:
        cfg = _load_config()
        return bool(cfg.get("api_key"))

    def save_config(self, values, hermes_home):
        """Write config to $HERMES_HOME/mem0.json."""
        import json
        from pathlib import Path
        config_path = Path(hermes_home) / "mem0.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                pass
        existing.update(values)
        from utils import atomic_json_write
        atomic_json_write(config_path, existing, mode=0o600)

    def get_config_schema(self):
        return [
            {"key": "api_key", "description": "Mem0 Platform API key", "secret": True, "required": True, "env_var": "MEM0_API_KEY", "url": "https://app.mem0.ai"},
            {"key": "user_id", "description": "User identifier", "default": "hermes-user"},
            {"key": "agent_id", "description": "Agent identifier", "default": "hermes"},
            {"key": "rerank", "description": "Enable reranking for recall", "default": "true", "choices": ["true", "false"]},
        ]

    def _get_client(self):
        """Thread-safe client accessor with lazy initialization."""
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                from mem0 import MemoryClient
                self._client = MemoryClient(api_key=self._api_key)
                return self._client
            except ImportError:
                raise RuntimeError("mem0 package not installed. Run: pip install mem0ai")

    def _is_breaker_open(self) -> bool:
        if self._consecutive_failures < _BREAKER_THRESHOLD:
            return False
        if time.monotonic() >= self._breaker_open_until:
            self._consecutive_failures = 0
            return False
        return True

    def _record_success(self):
        self._consecutive_failures = 0

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            self._breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_SECS
            logger.warning(
                "Mem0 circuit breaker tripped after %d consecutive failures. "
                "Pausing API calls for %ds.",
                self._consecutive_failures, _BREAKER_COOLDOWN_SECS,
            )

    def initialize(self, session_id: str, **kwargs) -> None:
        self._config = _load_config()
        self._api_key = self._config.get("api_key", "")
        self._user_id = kwargs.get("user_id") or self._config.get("user_id", "hermes-user")
        self._agent_id = self._config.get("agent_id", "hermes")
        self._rerank = self._config.get("rerank", True)

    def _read_filters(self) -> Dict[str, Any]:
        return {"user_id": self._user_id}

    def _write_filters(self) -> Dict[str, Any]:
        return {"user_id": self._user_id, "agent_id": self._agent_id}

    @staticmethod
    def _unwrap_results(response: Any) -> list:
        if isinstance(response, dict):
            return response.get("results", [])
        if isinstance(response, list):
            return response
        return []

    def system_prompt_block(self) -> str:
        return (
            "# Mem0 Memory\n"
            f"Active. User: {self._user_id}.\n"
            "Use mem0_search to find memories, mem0_conclude to store facts, "
            "mem0_profile for a full overview."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            return ""
        return f"## Mem0 Memory\n{result}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if self._is_breaker_open():
            return

        def _run():
            try:
                client = self._get_client()
                results = self._unwrap_results(client.search(
                    query=query,
                    filters=self._read_filters(),
                    rerank=self._rerank,
                    top_k=5,
                ))
                if results:
                    lines = [r.get("memory", "") for r in results if r.get("memory")]
                    with self._prefetch_lock:
                        self._prefetch_result = "\n".join(f"- {l}" for l in lines)
                self._record_success()
            except Exception as e:
                self._record_failure()
                logger.debug("Mem0 prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(target=_run, daemon=True, name="mem0-prefetch")
        self._prefetch_thread.start()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Send the turn to Mem0 for server-side fact extraction (non-blocking)."""
        if self._is_breaker_open():
            return

        def _sync():
            try:
                client = self._get_client()
                messages = [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": assistant_content},
                ]
                client.add(messages, **self._write_filters())
                self._record_success()
            except Exception as e:
                self._record_failure()
                logger.warning("Mem0 sync failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

        self._sync_thread = threading.Thread(target=_sync, daemon=True, name="mem0-sync")
        self._sync_thread.start()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [PROFILE_SCHEMA, SEARCH_SCHEMA, CONCLUDE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if self._is_breaker_open():
            return json.dumps({
                "error": "Mem0 API temporarily unavailable (multiple consecutive failures). Will retry automatically."
            })

        try:
            client = self._get_client()
        except Exception as e:
            return tool_error(str(e))

        if tool_name == "mem0_profile":
            try:
                memories = self._unwrap_results(client.get_all(filters=self._read_filters()))
                self._record_success()
                if not memories:
                    return json.dumps({"result": "No memories stored yet."})
                lines = [m.get("memory", "") for m in memories if m.get("memory")]
                return json.dumps({"result": "\n".join(lines), "count": len(lines)})
            except Exception as e:
                self._record_failure()
                return tool_error(f"Failed to fetch profile: {e}")

        elif tool_name == "mem0_search":
            query = args.get("query", "")
            if not query:
                return tool_error("Missing required parameter: query")
            rerank = args.get("rerank", False)
            top_k = min(int(args.get("top_k", 10)), 50)
            try:
                results = self._unwrap_results(client.search(
                    query=query,
                    filters=self._read_filters(),
                    rerank=rerank,
                    top_k=top_k,
                ))
                self._record_success()
                if not results:
                    return json.dumps({"result": "No relevant memories found."})
                items = [{"memory": r.get("memory", ""), "score": r.get("score", 0)} for r in results]
                return json.dumps({"results": items, "count": len(items)})
            except Exception as e:
                self._record_failure()
                return tool_error(f"Search failed: {e}")

        elif tool_name == "mem0_conclude":
            conclusion = args.get("conclusion", "")
            if not conclusion:
                return tool_error("Missing required parameter: conclusion")
            try:
                client.add(
                    [{"role": "user", "content": conclusion}],
                    **self._write_filters(),
                    infer=False,
                )
                self._record_success()
                return json.dumps({"result": "Fact stored."})
            except Exception as e:
                self._record_failure()
                return tool_error(f"Failed to store: {e}")

        return tool_error(f"Unknown tool: {tool_name}")

    def shutdown(self) -> None:
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        with self._client_lock:
            self._client = None


def register(ctx) -> None:
    """Register Mem0 as a memory provider plugin."""
    ctx.register_memory_provider(Mem0MemoryProvider())
```

### 6.4 The `check_fn` / requirements pattern

There are **two distinct "requirements" mechanisms** — don't confuse them:

1. **Tool-registry `check_fn`** (`tools/registry.py`): the *built-in* `memory`
   tool registers with `check_fn=check_memory_requirements` (always `True`). This
   is the toolset gate for the built-in tool, not for plugins. Plugins do **not**
   register through `tools.registry`; their tools come from `get_tool_schemas()`.
2. **Provider activation gate = `is_available()`** (no network): the single
   source of truth for whether a plugin can activate. `agent_init.py` only calls
   `add_provider` if `_mp.is_available()` is true. `hermes memory status` also
   calls it to show ✓/✗.
3. **`plugin.yaml` dependencies**: `pip_dependencies` (auto-installed by the
   setup wizard), `external_dependencies` (binary deps with `install`/`check`
   shown to user), `requires_env` (informational env-var list).

For a HydraDB provider: `is_available()` should check
`bool(os.environ.get("HYDRADB_API_KEY"))` (and optionally that the client import
succeeds, like supermemory does `__import__("supermemory")`); declare the client
package in `pip_dependencies`.

---

## 7. Gotchas, Async/Sync, Error Handling, CLI Interaction

### 7.1 Threading contract (critical)

- **`sync_turn()` MUST be non-blocking.** If your backend has latency, run the
  work in a daemon thread (see mem0's `_sync` pattern). The MemoryManager *also*
  dispatches `sync_all`/`queue_prefetch_all` to its own single-worker
  `ThreadPoolExecutor`, but a provider's `sync_turn` should still not block
  internally — a misconfigured backend was observed blocking ~298s inline,
  keeping the agent marked "running" for minutes.
- **`prefetch()` must be fast** — return *cached* results; do the actual recall
  in `queue_prefetch()` (background) and stash results for the next `prefetch()`.
  Join any in-flight prefetch thread with a short timeout (mem0 uses 3.0s).
- **All methods are synchronous** (no `async def`). Concurrency is achieved with
  `threading.Thread(daemon=True)` and `ThreadPoolExecutor`, not asyncio. A
  thread-safe client accessor (`threading.Lock`) is the norm.
- **Shutdown ordering:** `MemoryManager.shutdown_all()` first drains the
  background executor (bounded by `_SYNC_DRAIN_TIMEOUT_S = 5.0s`), then calls
  `shutdown()` on each provider in **reverse** order. Daemon threads still
  running past the drain die with the interpreter. Join your own threads in
  `shutdown()` with a short timeout (mem0: 5.0s).

### 7.2 Error handling expectations

- **Fail-open, never crash the agent.** Every MemoryManager→provider call is
  wrapped in `try/except`; exceptions are logged (`debug` for hooks, `warning`
  for sync/system-prompt) and swallowed. One provider's failure never blocks
  another or the turn. Mirror this inside `handle_tool_call`: catch exceptions
  and return `tool_error(...)` (a JSON string), never raise.
- **`handle_tool_call` returns a JSON string**, always. Use
  `from tools.registry import tool_error` for error envelopes.
- **Circuit breaker** is a good idea for cloud providers (mem0: 5 consecutive
  failures → 120s cooldown) to avoid hammering a down server.
- **`is_available()` must NOT make network calls** — only check env vars,
  installed deps, config files. It's called during agent init and by
  `hermes memory status`.

### 7.3 Profile isolation (critical for a cross-profile plugin)

- **Never hardcode `~/.hermes`.** Always use the `hermes_home` kwarg from
  `initialize()` or `hermes_constants.get_hermes_home()`. The docs call this out
  explicitly:
  ```python
  # CORRECT — profile-scoped
  from hermes_constants import get_hermes_home
  data_dir = get_hermes_home() / "hydradb"
  # WRONG — shared across all profiles
  data_dir = Path("~/.hermes/hydradb").expanduser()
  ```
- `MemoryManager.initialize_all` auto-injects `hermes_home` into kwargs. You also
  receive `agent_identity` (the active **profile name**) — use it for per-profile
  scoping in the backend (e.g. namespace keys by profile) if you want isolation,
  OR deliberately ignore it to share state across profiles (the HydraDB goal).
- For a **shared-across-all-profiles** backend, the cleanest design is: store
  data in HydraDB keyed by a stable user/tenant id (not by profile), and let
  every profile's provider instance point at the same HydraDB project. The
  plugin itself can live in-tree (§6.1) so every profile discovers it.

### 7.4 Single-provider rule + tool-name collisions

- Only **one** external provider active at a time. A second `add_provider` call
  is rejected with a warning. Configure via `memory.provider`.
- Provider tool names must **not** shadow `toolsets._HERMES_CORE_TOOLS`
  (`clarify`, `delegate_task`, `memory`, …). Collisions are dropped at
  registration. Prefix your tools (e.g. `hydradb_search`, `hydradb_profile`).

### 7.5 Signature-compat shims (the manager inspects your signatures)

- **`sync_turn` `messages` kwarg:** the manager checks
  `_provider_sync_accepts_messages` — if your `sync_turn` accepts `messages`
  (or `**kwargs`), it's passed the OpenAI-style turn transcript; otherwise the
  legacy `(user, assistant)` signature is used. Cloud providers should document
  what parts of `messages` (which may contain file paths, command output) are
  sent off-device.
- **`on_memory_write` `metadata` arg:** the manager detects
  `keyword` / `positional` / `legacy` modes and calls accordingly, so older
  3-arg implementations still work. New providers should accept
  `(action, target, content, metadata=None)`.
- **Do NOT pre-wrap `prefetch()` output** in `<memory-context>` fences or system
  notes — `build_memory_context_block` does that for you and will strip + warn
  on any pre-wrapping.

### 7.6 `hermes memory` CLI — exact subcommands

Parser built in `$SRC/hermes_cli/subcommands/memory.py`, handler `cmd_memory` in
`hermes_cli/main.py`, wizard/status in `hermes_cli/memory_setup.py`.

| Command | Effect |
|---|---|
| `hermes memory setup` | Interactive curses picker of discovered providers (+ "Built-in only"). Installs `pip_dependencies`, runs `get_config_schema()` prompts (or delegates to `post_setup` if present), writes `memory.provider` to config.yaml, calls `save_config()`, writes secrets to `.env`. |
| `hermes memory setup <provider>` | Skip the picker; configure `<provider>` directly (`cmd_setup_provider`). |
| `hermes memory status` | Shows active provider, its config (via `get_status_config` if present), `is_available()` ✓/✗, and lists all installed plugins. For an unavailable provider, lists each `env_var` field with ✓/✗ and a `url` hint. |
| `hermes memory off` | Sets `memory.provider: ""` (built-in only). |
| `hermes memory reset [--target all\|memory\|user] [--yes]` | Erases built-in `MEMORY.md`/`USER.md` files (does NOT touch external provider data). |

`hermes doctor` also checks the active provider: warns "run `hermes memory setup`"
if config/creds missing, "run `hermes memory status`" if configured-but-not-
available, "run `hermes memory setup`" if the plugin isn't found.

### 7.7 How `hermes memory setup` interacts with your provider (flow)

1. `discover_memory_providers()` scans both roots → `[(name, desc, is_available)]`.
2. For each, `load_memory_provider(name)` instantiates it (no init) and reads
   `get_config_schema()` to derive a setup hint ("requires API key" / "local" /
   "no setup needed").
3. User picks one (or "Built-in only" → `memory.provider=""`).
4. `_install_dependencies(name)` reads `plugin.yaml` `pip_dependencies`, checks
   imports, installs missing via `uv pip install --python <exe>` (fallback
   `python -m pip`); shows `external_dependencies` install hints.
5. **If `hasattr(provider, "post_setup")`:** delegate entirely to
   `provider.post_setup(hermes_home, config)` — the provider owns everything
   from here (writes its own `memory.provider`, runs its own wizard/connection
   test). Return.
6. **Else generic wizard:** iterate `get_config_schema()` fields:
   - `when` condition → skip if prior fields don't match.
   - `choices` (non-secret) → curses radiolist.
   - `secret` → masked prompt; existing env var shown as `…last4`; written to
     `env_writes[env_var]`.
   - else text prompt with `default`/`default_from`.
   - Non-secret values collected into `provider_config` (under
     `config["memory"][name]`).
7. Write `config["memory"]["provider"] = name` → `save_config(config)`.
8. `provider.save_config(provider_config, hermes_home)` → provider's native file.
9. `_write_env_vars(.env, env_writes)` (chmod 0600).
10. Print "Start a new session to activate." (activation happens at next agent
    init, not live.)

### 7.8 Testing patterns (from the docs)

`tests/agent/test_memory_provider.py`, `test_memory_session_switch.py`,
`test_memory_user_id.py`, `tests/run_agent/test_memory_provider_init.py`,
`tests/agent/test_memory_async_sync.py`. End-to-end pattern:
```python
from agent.memory_manager import MemoryManager
mgr = MemoryManager()
mgr.add_provider(my_provider)
mgr.initialize_all(session_id="test-1", platform="cli")
result = mgr.handle_tool_call("my_tool", {"action": "add", "content": "test"})
mgr.sync_all("user msg", "assistant msg")
mgr.on_session_end([])
mgr.shutdown_all()
```

---

## 8. Key File Map (for the build phase)

| File | Role |
|---|---|
| `$SRC/agent/memory_provider.py` | **The ABC** — the contract. |
| `$SRC/agent/memory_manager.py` | `MemoryManager` orchestrator: registration, single-worker bg executor, prefetch/sync/queue fan-out, tool routing, `on_memory_write`, `build_memory_context_block` fencing, `shutdown_all` drain. |
| `$SRC/agent/agent_init.py` (~L1138–1198) | Wires `memory.provider` → `MemoryManager` + `initialize_all` kwargs. |
| `$SRC/agent/turn_context.py` (~L361–389) | Per-turn `on_turn_start` + `prefetch_all` → `ext_prefetch_cache`. |
| `$SRC/agent/agent_runtime_helpers.py` (~L1864–1881) | `on_memory_write` fan-out on built-in memory writes + external-tool routing. |
| `$SRC/agent/conversation_compression.py` (~L436) | `on_pre_compress` before context compression. |
| `$SRC/plugins/memory/__init__.py` | Discovery: `discover_memory_providers`, `load_memory_provider`, `find_provider_dir`, `discover_plugin_cli_commands`. |
| `$SRC/plugins/memory/<name>/` | The 8 bundled providers (mem0 = best reference). |
| `$SRC/hermes_cli/config.py` (~L1850) | `DEFAULT_CONFIG["memory"]` schema. |
| `$SRC/hermes_cli/memory_setup.py` | `hermes memory setup`/`status` wizard; consumes `get_config_schema`/`save_config`/`post_setup`/`get_status_config`. |
| `$SRC/hermes_cli/subcommands/memory.py` | `hermes memory` argparse (setup/status/off/reset). |
| `$SRC/hermes_cli/main.py` (~L11414) | `cmd_memory` router. |
| `$SRC/hermes_cli/memory_providers.py` | **Desktop UI** declarative config schema (separate, pure-data; only Hindsight declared). Not the CLI wizard. |
| `$SRC/tools/memory_tool.py` | Built-in `MemoryStore` + `memory` tool (add/replace/remove/batch, char limits, write gate, threat scan, `on_memory_write` source). |
| `$SRC/hermes_cli/doctor.py` (~L2200) | `hermes doctor` memory-provider health checks. |

---

## 9. Build Plan Implications for the HydraDB Provider

1. **Ship in-tree** at `$SRC/plugins/memory/hydradb/` so every profile discovers
   it (cross-profile requirement). Alternatively, install into each profile's
   `$HERMES_HOME/plugins/hydradb/`.
2. **Backend scoping:** key HydraDB records by a stable tenant/user id (shared
   across profiles), not by `agent_identity`, to meet the "shared across all
   profiles" goal. Still respect `hermes_home` for any local cache files.
3. **Implement:** `name`=`"hydradb"`, `is_available` (check `HYDRADB_API_KEY` +
   client import, no network), `initialize` (lazy client, capture
   `hermes_home`/`user_id`/`agent_identity`), `get_tool_schemas`
   (`hydradb_search`, `hydradb_profile`, `hydradb_conclude`-style tools),
   `handle_tool_call` (JSON results, `tool_error` on failure), `get_config_schema`
   (minimal — API key + maybe endpoint), `save_config` (write
   `$HERMES_HOME/hydradb.json`).
4. **Non-blocking writes:** `sync_turn` via daemon thread; `queue_prefetch` for
   background recall; `prefetch` returns cached. Mirror mem0's threading +
   circuit-breaker pattern.
5. **`on_memory_write`** to mirror built-in MEMORY.md/USER.md writes into HydraDB
   (so the curated built-in memory and HydraDB stay in sync).
6. **`plugin.yaml`:** `pip_dependencies: [hydradb-client]`, `requires_env:
   [HYDRADB_API_KEY]`, `hooks: [on_session_end, on_memory_write]`.
7. **Activate:** `hermes memory setup hydradb` (or pick via `hermes memory setup`).
   Verify with `hermes memory status` and `hermes doctor`.
8. **Test** with `MemoryManager` directly (§7.8) + add a test under
   `tests/plugins/memory/test_hydradb_provider.py` mirroring
   `test_mem0_v2.py`/`test_supermemory_provider.py`.

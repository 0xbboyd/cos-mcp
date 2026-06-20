---
name: autonomous-hermes
description: "Hermes Agent-specific patterns and pitfalls for GSD autonomous execution — delegate_task constraints, subagent artifact requirements, pragmatic shortcuts."
version: "1.0.0"
allowed-tools:
  - Read
  - Write
  - Bash
  - Glob
  - Grep
  - AskUserQuestion
  - Agent
---

<objective>
Extends the `autonomous` and `plan-phase` GSD skills with Hermes Agent-specific execution patterns. Load alongside `autonomous` when running milestone phases on Hermes Agent.
</objective>

<triggers>
Load this skill when:
- Running GSD autonomous workflow (`/gsd-autonomous`) on Hermes Agent
- Spawning plan-phase or execute-phase subagents via delegate_task
- Debugging missing VERIFICATION.md after delegated execution
- Setting up a Hermes plugin/provider that needs SDK dependencies in the Hermes venv
</triggers>

<hermes_delegate_constraints>
## delegate_task as Agent Replacement

On Hermes Agent, the GSD workflow's `Agent()` spawn primitive maps to `delegate_task`:

- **Max 3 concurrent children.** The `tasks` array accepts at most 3 subagents per call. When a GSD workflow calls for 4 parallel spawns (e.g., map-codebase researchers, new-project research), split into 3+1 batches.
- **Leaf subagents CANNOT nest.** `delegate_task` is unavailable inside a leaf subagent. The formal plan-phase pipeline requires spawning gsd-phase-researcher → gsd-planner → gsd-plan-checker — this chain cannot be delegated. Run plan-phase INLINE in the parent context where delegate_task is available.
- **Flattening workaround:** For gap-filling phases where most requirements are already met by existing code, skip the formal multi-agent pipeline. Delegate a combined plan+execute pass to a single subagent that reads CONTEXT.md, plans changes, applies them, and writes all artifacts (PLAN.md, SUMMARY.md, VERIFICATION.md) in one run.
</hermes_delegate_constraints>

<subagent_artifacts>
## Subagent Artifact Completeness

When delegating execute-phase via delegate_task, explicitly require ALL output files in the task context:

```
TASKS (must produce all of these):
1. Apply code changes to source files
2. Write PLAN.md to phase directory  
3. Write SUMMARY.md to phase directory
4. Write VERIFICATION.md — MUST include YAML frontmatter with `status: passed|gaps_found|human_needed`
5. Run syntax/lint checks
```

Subagents reliably produce SUMMARY.md but often skip VERIFICATION.md. The autonomous post-execution routing (step 3d) depends on VERIFICATION.md existing. If missing, the workflow routes to handle_blocker with "Execute phase N did not produce verification results."
</subagent_artifacts>

<phase_shortcuts>
## Pragmatic Phase Shortcuts (YOLO Mode)

When YOLO mode is active and a phase is mostly gap-filling on existing code:

1. **Skip formal smart discuss.** Write minimal CONTEXT.md directly using the infrastructure-phase pattern with `**Mode:** Auto-generated (infrastructure phase — discuss skipped)`.
2. **Skip formal plan-phase pipeline.** Delegate a combined plan+execute subagent instead of running the full researcher→planner→checker chain.
3. **Pure-testing phases.** Write CONTEXT.md with a "Claude's Discretion" block, delegate the full test file creation and execution to one subagent.

Example: Phase 2 had 26 requirements but 21 were already implemented. A single subagent filled all 5 remaining gaps (circuit breaker split, shutdown drain, delete path, thread tracking) in one pass, avoiding the token overhead of the full plan-phase pipeline.
</phase_shortcuts>

<hermes_env>
## Hermes Python Environment

When testing or deploying Hermes plugins/providers:

- Hermes venv path: `~/.hermes/hermes-agent/venv/` (NOT `.venv/`)
- Install pip dependencies: `~/.hermes/hermes-agent/venv/bin/pip install <pkg>`
- Provider `is_available()` requires both `HYDRA_DB_API_KEY` env var AND SDK importable in this venv
- Load env vars for scripts: `export $(grep -v '^#' ~/.hermes/.env | xargs)`
</hermes_env>

<user_preference>
## Don't Build Unprompted

When the user asks a yes/no or informational question, answer directly. Do NOT scaffold tools, write scripts, or make code changes unless explicitly asked. A question like "is there a way to force recording of a fact?" wants a description of the mechanism, not a CLI tool.

When in doubt: answer the question first, then offer to build if it would help.
</user_preference>

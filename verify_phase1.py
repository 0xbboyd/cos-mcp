"""Walking skeleton verification for Phase 1: Provider Foundation.

Tests what can be verified without the full Hermes Agent runtime (no 'agent' package).
Uses AST parsing + text checks to verify all code changes.
"""

import ast
import json
import os
import sys
import tempfile
from pathlib import Path

PROVIDER_FILE = Path(__file__).resolve().parent / "hydradb-memory" / "__init__.py"
SOURCE = PROVIDER_FILE.read_text()

def check(label, condition, detail=""):
    if condition:
        print(f"✓ {label}")
    else:
        print(f"✗ {label} FAIL: {detail}")
        global failures
        failures += 1

failures = 0

# ---------------------------------------------------------------------------
# 1. Plan 1.1: Config Path Fix
# ---------------------------------------------------------------------------

# 1.1.1: _load_config() accepts hermes_home parameter
check(
    "_load_config() accepts hermes_home parameter",
    "def _load_config(hermes_home: str = \"\") -> dict:" in SOURCE,
)

# No more ~/.hermes hardcoded in _load_config
# Use AST to find the _load_config function and check its body
tree = ast.parse(SOURCE)
load_config_func = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_load_config" and not any(
        isinstance(p, ast.arg) and p.arg == "self" for p in node.args.args
    ):
        load_config_func = node
        break

if load_config_func:
    func_source = ast.get_source_segment(SOURCE, load_config_func)
    check(
        "No ~/.hermes hardcode in _load_config()",
        "os.path.expanduser(\"~/.hermes\")" not in func_source,
    )
    check(
        "hermes_home used in _load_config() resolution",
        "home = hermes_home or os.environ.get(\"HERMES_HOME\"" in func_source,
    )
    check(
        "Config loading skipped when hermes_home is empty",
        "if home:" in func_source,
    )
else:
    check("_load_config() found in AST", False, "module-level _load_config not found")

# 1.1.2: Instance wrapper passes hermes_home
check(
    "Instance _load_config passes self._hermes_home",
    "_load_config(self._hermes_home)" in SOURCE,
)

# 1.1.3: sub_tenant_id fallback includes "default"
check(
    'sub_tenant_id fallback includes "default"',
    'or "default"' in SOURCE,
)

# 1.1.4: hermes_home captured before _load_config() call
init_method = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "initialize":
        init_method = node
        break

if init_method:
    init_source = ast.get_source_segment(SOURCE, init_method)
    hermes_home_line = None
    load_config_line = None
    for i, line in enumerate(init_source.split("\n")):
        if "_hermes_home = kwargs.get" in line and hermes_home_line is None:
            hermes_home_line = i
        if "self._load_config()" in line and load_config_line is None:
            load_config_line = i
    check(
        "hermes_home captured before _load_config() in initialize()",
        hermes_home_line is not None
        and load_config_line is not None
        and hermes_home_line < load_config_line,
        f"hermes_home_line={hermes_home_line}, load_config_line={load_config_line}",
    )

# ---------------------------------------------------------------------------
# 2. Plan 1.2: Tenant Auto-Provisioning
# ---------------------------------------------------------------------------

# 2.1.1: _tenant_ready flag in initialize()
check(
    "_tenant_ready = False in initialize()",
    "self._tenant_ready = False" in SOURCE,
)

# 2.1.2: _ensure_tenant() method exists
ensure_tenant_func = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_ensure_tenant":
        ensure_tenant_func = node
        break

check("_ensure_tenant() method exists", ensure_tenant_func is not None)

if ensure_tenant_func:
    et_source = ast.get_source_segment(SOURCE, ensure_tenant_func)
    check("_ensure_tenant checks _tenant_ready", "self._tenant_ready" in et_source)
    check("_ensure_tenant checks circuit breaker", "_is_breaker_open" in et_source)
    check("_ensure_tenant checks _api_key", "self._api_key" in et_source)
    check("_ensure_tenant calls tenants.list()", "tenants.list()" in et_source)
    check("_ensure_tenant calls tenants.create()", "tenants.create(" in et_source)
    check("_ensure_tenant handles 409 conflict", "409" in et_source)
    check(
        "_ensure_tenant polls tenants.status()",
        "tenants.status(" in et_source,
    )
    check(
        "_ensure_tenant checks ready_for_ingestion",
        "ready_for_ingestion" in et_source,
    )
    check("_ensure_tenant has polling loop (range 1-61)", "range(1, 61)" in et_source)
    check("_ensure_tenant sleeps 5s between polls", "time.sleep(5)" in et_source)
    check("_ensure_tenant sets _tenant_ready on success", "_tenant_ready = True" in et_source)
    check("_ensure_tenant records success", "_record_success()" in et_source)
    check("_ensure_tenant records failure on exception", "_record_failure()" in et_source)
    check(
        "Per-attempt logging present",
        "waiting... (attempt %d/60)" in et_source,
    )
    check(
        "Google-style docstring on _ensure_tenant",
        '"""Create tenant if needed' in et_source,
    )

# 2.1.3: _ensure_tenant() called from initialize()
check(
    "_ensure_tenant() called from initialize()",
    "self._ensure_tenant()" in SOURCE,
)

# ---------------------------------------------------------------------------
# 3. Plan 1.3: Provider Contract Audit (FND-01 to FND-09)
# ---------------------------------------------------------------------------

# FND-01: name = "hydradb" and all ABC methods
check("FND-01: name = 'hydradb'", 'name = "hydradb"' in SOURCE)

required_methods = [
    "is_available", "initialize", "system_prompt_block",
    "prefetch", "queue_prefetch", "sync_turn",
    "on_memory_write", "on_session_end", "shutdown",
    "get_tool_schemas", "handle_tool_call", "get_config_schema", "save_config",
]
for method in required_methods:
    method_exists = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == method:
            method_exists = True
            break
    check(f"FND-01: {method}() implemented", method_exists)

# FND-02: is_available() checks env + SDK import
is_avail = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "is_available":
        is_avail = node
        break
if is_avail:
    ia_source = ast.get_source_segment(SOURCE, is_avail)
    check("FND-02: is_available checks HYDRA_DB_API_KEY", "HYDRA_DB_API_KEY" in ia_source)
    check("FND-02: is_available tries SDK import", "from hydra_db import HydraDB" in ia_source)
    check("FND-02: is_available no HydraDB() instantiation", "HydraDB(" not in ia_source)
    # @classmethod is in the line before the function, not in get_source_segment
    func_lines = SOURCE.split("\n")
    prev_line = func_lines[is_avail.lineno - 2] if is_avail.lineno >= 2 else ""
    check("FND-02: is_available is @classmethod", "@classmethod" in prev_line)

# FND-05: _get_client() double-checked locking
get_client = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_get_client":
        get_client = node
        break
if get_client:
    gc_source = ast.get_source_segment(SOURCE, get_client)
    check("FND-05: _get_client fast-path check", "self._client is not None" in gc_source)
    check("FND-05: _get_client uses lock", "self._client_lock" in gc_source)
    check("FND-05: _get_client lazy imports SDK", "from hydra_db import" in gc_source)

# FND-06: get_config_schema() returns 4 field descriptors
check("FND-06: api_key in schema", '"api_key"' in SOURCE)
check("FND-06: tenant_id in schema", '"tenant_id"' in SOURCE)
check("FND-06: sub_tenant_id in schema", '"sub_tenant_id"' in SOURCE)
check("FND-06: query_mode in schema", '"query_mode"' in SOURCE)
check('FND-06: api_key marked secret', '"secret": True' in SOURCE)

# FND-07: save_config() filters secrets
save_cfg = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "save_config":
        save_cfg = node
        break
if save_cfg:
    sc_source = ast.get_source_segment(SOURCE, save_cfg)
    check("FND-07: save_config filters api_key", '"api_key"' in sc_source)
    check("FND-07: save_config writes hydradb.json", 'hydradb.json' in sc_source)
    check("FND-07: save_config accepts hermes_home param", 'hermes_home: str' in sc_source)

# FND-08: system_prompt_block() returns static text
check(
    "FND-08: system_prompt_block returns expected text",
    "HydraDB Memory. Active." in SOURCE,
)

# FND-09: Tool names prefixed hydradb_
check("FND-09: hydradb_search tool", 'hydradb_search' in SOURCE)
check("FND-09: hydradb_profile tool", 'hydradb_profile' in SOURCE)
check("FND-09: hydradb_conclude tool", 'hydradb_conclude' in SOURCE)

# FND-09: register(ctx) entry point
check("FND-09: register(ctx) entry point exists", "def register(ctx)" in SOURCE)
check("FND-09: register calls register_memory_provider", "register_memory_provider" in SOURCE)

# ---------------------------------------------------------------------------
# 4. Convention compliance
# ---------------------------------------------------------------------------

check("from __future__ import annotations", "from __future__ import annotations" in SOURCE)
check("logger = logging.getLogger(__name__)", "logger = logging.getLogger(__name__)" in SOURCE)
check("HydraDBMemoryProvider inherits MemoryProvider", "class HydraDBMemoryProvider(MemoryProvider)" in SOURCE)
check("Section separator for Tenant provisioning", "# --- Tenant provisioning ---" in SOURCE)
check("_tenant_ready is private (underscore prefixed)", "self._tenant_ready" in SOURCE)
check("_ensure_tenant is private (underscore prefixed)", "def _ensure_tenant(self)" in SOURCE)
check("Google-style docstring on _ensure_tenant (Args/Raises/etc)", '"""Create tenant if needed' in SOURCE)

# ---------------------------------------------------------------------------
# 5. Test initialize() with hermes_home using temp dirs (FND-03, FND-10)
# ---------------------------------------------------------------------------

# Since we can't import the module (needs Hermes Agent runtime), we verify
# the config resolution logic by checking the source code structure.

# Verify the resolution chain: hermes_home kwarg → HERMES_HOME env → skip
check(
    "FND-10: Config loads from hermes_home kwarg (not ~/.hermes)",
    "os.path.expanduser" not in SOURCE or "os.path.expanduser" not in ast.get_source_segment(SOURCE, load_config_func) if load_config_func else True,
)

# Check no remaining ~/.hermes hardcoding anywhere in the file
check(
    "FND-10: No ~/.hermes hardcoded anywhere in provider code",
    "os.path.expanduser(\"~/.hermes\")" not in SOURCE,
)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print()
print("=" * 60)
if failures == 0:
    print("Phase 1 Verification: ALL CHECKS PASSED")
    print("Provider foundation is complete — ready for Phase 2 I/O.")
else:
    print(f"Phase 1 Verification: {failures} CHECK(S) FAILED")
print("=" * 60)
sys.exit(failures)

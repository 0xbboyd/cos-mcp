"""
TestContextConfig — TST-02: is_available, config defaults, JSON overrides,
save_config, hermes_home kwarg for both context engines.

Tests CFG-01 (API key from env), CFG-02 (JSON config), CFG-03 (no ~/.hermes).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from hydradb_context_engine import (
    HydraDBContextEngine, DEFAULT_CONFIG as HYDRA_DEFAULTS,
)
from muninn_context_engine import (
    MuninnDBContextEngine, DEFAULT_CONFIG as MUNINN_DEFAULTS,
)


# ============================================================================
# HydraDB — is_available
# ============================================================================


class TestHydraDBConfig:
    """Config tests for HydraDBContextEngine."""

    def test_is_available_with_key(self, monkeypatch):
        """is_available returns True when API key is set and SDK is importable."""
        monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
        assert HydraDBContextEngine.is_available() is True

    def test_is_available_without_key(self, monkeypatch):
        """is_available returns False when HYDRA_DB_API_KEY is not set."""
        monkeypatch.delenv("HYDRA_DB_API_KEY", raising=False)
        assert HydraDBContextEngine.is_available() is False

    def test_is_available_no_sdk(self, monkeypatch):
        """is_available returns False when hydra_db SDK cannot be imported."""
        monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
        saved = sys.modules.pop("hydra_db", None)
        try:
            keys_to_pop = [k for k in sys.modules if k.startswith("hydra_db")]
            saved_modules = {k: sys.modules.pop(k) for k in keys_to_pop}
            try:
                assert HydraDBContextEngine.is_available() is False
            finally:
                for k, v in saved_modules.items():
                    sys.modules[k] = v
        finally:
            if saved is not None:
                sys.modules["hydra_db"] = saved

    def test_config_defaults(self):
        """DEFAULT_CONFIG has expected keys and default values."""
        assert HYDRA_DEFAULTS["tenant_id"] == "hermes"
        assert HYDRA_DEFAULTS["query_mode"] == "thinking"
        assert HYDRA_DEFAULTS["max_results"] == 10
        assert HYDRA_DEFAULTS["threshold_percent"] == 0.75
        assert HYDRA_DEFAULTS["protect_first_n"] == 3
        assert HYDRA_DEFAULTS["protect_last_n"] == 6
        assert HYDRA_DEFAULTS["entity_extraction_mode"] == "balanced"
        assert HYDRA_DEFAULTS["api_key"] == ""

    def test_config_api_key_from_env(self, fake_hydra_backend, fake_hydra_client, monkeypatch):
        """_load_config reads HYDRA_DB_API_KEY from environment."""
        monkeypatch.setenv("HYDRA_DB_API_KEY", "env-key-456")
        monkeypatch.setattr(HydraDBContextEngine, "_create_backend",
                            lambda self, kwargs: fake_hydra_backend)
        monkeypatch.setattr(HydraDBContextEngine, "_get_client",
                            lambda self: fake_hydra_client)
        engine = HydraDBContextEngine()
        engine.initialize("sess", hermes_home="/tmp/t", agent_identity="p")
        assert engine._config["api_key"] == "env-key-456"

    def test_config_overrides_from_file(self, tmp_path, fake_hydra_backend, fake_hydra_client, monkeypatch):
        """_load_config merges overrides from hydradb-context.json."""
        config_file = tmp_path / "hydradb-context.json"
        config_file.write_text(json.dumps({
            "tenant_id": "custom-tenant",
            "query_mode": "fast",
            "max_results": 20,
            "threshold_percent": 0.8,
            "protect_first_n": 5,
            "protect_last_n": 10,
        }))
        monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
        monkeypatch.setattr(HydraDBContextEngine, "_create_backend",
                            lambda self, kwargs: fake_hydra_backend)
        monkeypatch.setattr(HydraDBContextEngine, "_get_client",
                            lambda self: fake_hydra_client)
        engine = HydraDBContextEngine()
        engine.initialize("sess", hermes_home=str(tmp_path), agent_identity="p")
        assert engine._config["tenant_id"] == "custom-tenant"
        assert engine._config["query_mode"] == "fast"
        assert engine._config["max_results"] == 20
        assert engine.threshold_percent == 0.8
        assert engine.protect_first_n == 5
        assert engine.protect_last_n == 10

    def test_config_broken_json(self, tmp_path, fake_hydra_backend, fake_hydra_client, monkeypatch):
        """_load_config handles corrupt JSON gracefully (falls back to defaults)."""
        config_file = tmp_path / "hydradb-context.json"
        config_file.write_text("{broken json!!")
        monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
        monkeypatch.setattr(HydraDBContextEngine, "_create_backend",
                            lambda self, kwargs: fake_hydra_backend)
        monkeypatch.setattr(HydraDBContextEngine, "_get_client",
                            lambda self: fake_hydra_client)
        engine = HydraDBContextEngine()
        engine.initialize("sess", hermes_home=str(tmp_path), agent_identity="p")
        assert engine._config["tenant_id"] == "hermes"
        assert engine._config["query_mode"] == "thinking"

    def test_config_via_hermes_home_kwarg(self, tmp_path, fake_hydra_backend, fake_hydra_client, monkeypatch):
        """initialize() loads config from the hermes_home kwarg, not ~/.hermes."""
        config_file = tmp_path / "hydradb-context.json"
        config_file.write_text(json.dumps({"tenant_id": "kwarg-tenant"}))
        monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
        monkeypatch.setattr(HydraDBContextEngine, "_create_backend",
                            lambda self, kwargs: fake_hydra_backend)
        monkeypatch.setattr(HydraDBContextEngine, "_get_client",
                            lambda self: fake_hydra_client)
        engine = HydraDBContextEngine()
        engine.initialize("sess", hermes_home=str(tmp_path), agent_identity="p")
        assert engine._config["tenant_id"] == "kwarg-tenant"
        assert engine._hermes_home == str(tmp_path)


# ============================================================================
# MuninnDB — is_available
# ============================================================================


class TestMuninnDBConfig:
    """Config tests for MuninnDBContextEngine."""

    def test_is_available_with_key(self, monkeypatch):
        """is_available returns True when MUNINN_API_KEY is set."""
        monkeypatch.setenv("MUNINN_API_KEY", "test-key")
        assert MuninnDBContextEngine.is_available() is True

    def test_is_available_without_key(self, monkeypatch):
        """is_available returns False when MUNINN_API_KEY is not set."""
        monkeypatch.delenv("MUNINN_API_KEY", raising=False)
        assert MuninnDBContextEngine.is_available() is False

    def test_config_defaults(self):
        """DEFAULT_CONFIG has expected keys and default values."""
        assert MUNINN_DEFAULTS["base_url"] == "http://127.0.0.1:8475"
        assert MUNINN_DEFAULTS["vault"] == "default"
        assert MUNINN_DEFAULTS["max_results"] == 10
        assert MUNINN_DEFAULTS["threshold_percent"] == 0.75
        assert MUNINN_DEFAULTS["protect_first_n"] == 3
        assert MUNINN_DEFAULTS["protect_last_n"] == 6
        assert MUNINN_DEFAULTS["api_key"] == ""

    def test_config_api_key_from_env(self, fake_muninn_backend, monkeypatch):
        """_load_config reads MUNINN_API_KEY from environment."""
        monkeypatch.setenv("MUNINN_API_KEY", "env-key-789")
        monkeypatch.setattr(MuninnDBContextEngine, "_create_backend",
                            lambda self, kwargs: fake_muninn_backend)
        engine = MuninnDBContextEngine()
        engine.initialize("sess", hermes_home="/tmp/t", agent_identity="u")
        assert engine._config["api_key"] == "env-key-789"

    def test_config_overrides_from_file(self, tmp_path, fake_muninn_backend, monkeypatch):
        """_load_config merges overrides from muninn-context.json."""
        config_file = tmp_path / "muninn-context.json"
        config_file.write_text(json.dumps({
            "base_url": "http://localhost:9999",
            "vault": "testing",
            "max_results": 5,
            "threshold_percent": 0.9,
        }))
        monkeypatch.setenv("MUNINN_API_KEY", "test-key")
        monkeypatch.setattr(MuninnDBContextEngine, "_create_backend",
                            lambda self, kwargs: fake_muninn_backend)
        engine = MuninnDBContextEngine()
        engine.initialize("sess", hermes_home=str(tmp_path), agent_identity="u")
        assert engine._config["base_url"] == "http://localhost:9999"
        assert engine._config["vault"] == "testing"
        assert engine._config["max_results"] == 5
        assert engine.threshold_percent == 0.9

    def test_config_broken_json(self, tmp_path, fake_muninn_backend, monkeypatch):
        """_load_config handles corrupt JSON gracefully."""
        config_file = tmp_path / "muninn-context.json"
        config_file.write_text("{corrupt")
        monkeypatch.setenv("MUNINN_API_KEY", "test-key")
        monkeypatch.setattr(MuninnDBContextEngine, "_create_backend",
                            lambda self, kwargs: fake_muninn_backend)
        engine = MuninnDBContextEngine()
        engine.initialize("sess", hermes_home=str(tmp_path), agent_identity="u")
        assert engine._config["base_url"] == "http://127.0.0.1:8475"

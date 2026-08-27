"""Configuration management for Snowflake MCP server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from pydantic import BaseModel, Field


class SnowflakeConfig(BaseModel):
    """Snowflake connection parameters."""

    account: str = Field(default="", description="Snowflake account identifier")
    user: str = Field(default="", description="Snowflake user name")
    password: str | None = Field(default=None, description="Password authentication")
    role: str | None = Field(default=None, description="Role name")
    private_key_path: str | None = Field(default=None, description="Path to RSA private key")
    private_key_passphrase: str | None = Field(default=None, description="Passphrase for RSA key")
    private_key_raw: str | None = Field(default=None, description="Raw PEM encoded private key")
    token: str | None = Field(default=None, description="OAuth / PAT Bearer token")
    authenticator: str | None = Field(default=None, description="Authenticator (e.g. externalbrowser, oauth)")
    warehouse: str | None = Field(default=None, description="Default virtual warehouse")
    database: str | None = Field(default=None, description="Default database")
    schema_name: str | None = Field(default=None, alias="schema", description="Default schema")
    host: str | None = Field(default=None, description="Optional custom host URL")
    port: int | None = Field(default=443, description="Custom port")
    query_timeout: int = Field(default=120, description="Default query execution timeout in seconds")
    max_rows: int = Field(default=1000, description="Max rows returned by queries")
    read_only: bool = Field(default=False, description="Strict read-only safety mode")

    connection_name: str | None = Field(default=None, description="Active connection profile name")

    model_config = {"populate_by_name": True}

    @classmethod
    def list_available_connections(cls, config_path: str | None = None) -> list[str]:
        """Scan and return list of profile names found in connections.toml."""
        toml_paths = []
        if config_path:
            toml_paths.append(Path(config_path).expanduser())
        if os.getenv("SNOWFLAKE_CONNECTIONS_FILE"):
            toml_paths.append(Path(os.getenv("SNOWFLAKE_CONNECTIONS_FILE", "")).expanduser())
        toml_paths.extend(
            [
                Path(os.getenv("SNOWFLAKE_HOME", "~/.snowflake")).expanduser() / "connections.toml",
                Path("~/.snowflake/connections.toml").expanduser(),
            ]
        )

        for p in toml_paths:
            if p.exists():
                try:
                    with open(p, "rb") as f:
                        data = tomllib.load(f)
                        return [k for k, v in data.items() if isinstance(v, dict)]
                except Exception:
                    pass
        return []

    @classmethod
    def from_env_or_config(
        cls,
        connection_name: str | None = None,
        config_path: str | None = None,
    ) -> SnowflakeConfig:
        """Resolve config from environment variables and ~/.snowflake/connections.toml with actionable diagnostics."""
        # 1. Check if connections.toml exists and can be loaded
        conn_data: dict[str, Any] = {}
        target_conn = (
            connection_name or os.getenv("SNOWFLAKE_CONNECTION_NAME") or os.getenv("SNOWFLAKE_DEFAULT_CONNECTION_NAME")
        )

        toml_paths = []
        if config_path:
            toml_paths.append(Path(config_path).expanduser())
        if os.getenv("SNOWFLAKE_CONNECTIONS_FILE"):
            toml_paths.append(Path(os.getenv("SNOWFLAKE_CONNECTIONS_FILE", "")).expanduser())
        toml_paths.extend(
            [
                Path(os.getenv("SNOWFLAKE_HOME", "~/.snowflake")).expanduser() / "connections.toml",
                Path("~/.snowflake/connections.toml").expanduser(),
            ]
        )

        available_profiles: list[str] = []
        resolved_profile: str | None = target_conn

        for p in toml_paths:
            if p.exists():
                try:
                    with open(p, "rb") as f:
                        data = tomllib.load(f)
                        available_profiles = [k for k, v in data.items() if isinstance(v, dict)]
                        chosen = (
                            target_conn
                            or data.get("default_connection_name")
                            or ("default" if "default" in data else None)
                        )
                        if not chosen and available_profiles:
                            chosen = available_profiles[0]
                        if chosen and chosen in data:
                            conn_data = data[chosen]
                            resolved_profile = chosen
                            break
                except Exception:
                    pass

        # 2. Check token_file_path if present in conn_data
        token_val = os.getenv("SNOWFLAKE_TOKEN", conn_data.get("token"))
        if not token_val and conn_data.get("token_file_path"):
            t_path = Path(conn_data["token_file_path"]).expanduser()
            if t_path.exists():
                token_val = t_path.read_text().strip()

        # 3. Environment variables take precedence over config files
        account = os.getenv("SNOWFLAKE_ACCOUNT", str(conn_data.get("account", "")))
        user = os.getenv("SNOWFLAKE_USER", str(conn_data.get("user", "")))
        password = os.getenv("SNOWFLAKE_PASSWORD", conn_data.get("password"))
        private_key_path = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH", conn_data.get("private_key_path"))
        private_key_passphrase = os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", conn_data.get("private_key_passphrase"))
        private_key_raw = os.getenv("SNOWFLAKE_PRIVATE_KEY_RAW", conn_data.get("private_key_raw"))
        token = token_val
        authenticator = os.getenv("SNOWFLAKE_AUTHENTICATOR", conn_data.get("authenticator"))
        warehouse = os.getenv("SNOWFLAKE_WAREHOUSE", conn_data.get("warehouse"))
        database = os.getenv("SNOWFLAKE_DATABASE", conn_data.get("database"))
        schema_name = os.getenv("SNOWFLAKE_SCHEMA", conn_data.get("schema"))
        role = os.getenv("SNOWFLAKE_ROLE", conn_data.get("role"))
        host = os.getenv("SNOWFLAKE_HOST", conn_data.get("host"))
        port_env = os.getenv("SNOWFLAKE_PORT")
        port = int(port_env) if port_env else conn_data.get("port", 443)

        read_only_env = os.getenv("SNOWFLAKE_MCP_READONLY", "").lower() in ("1", "true", "yes")
        read_only = read_only_env or bool(conn_data.get("read_only", False))

        query_timeout = int(os.getenv("SNOWFLAKE_QUERY_TIMEOUT", str(conn_data.get("query_timeout", 120))))
        max_rows = int(os.getenv("SNOWFLAKE_MAX_ROWS", str(conn_data.get("max_rows", 1000))))

        # 4. Proactive Validation & Actionable Diagnostics
        # Allow creating config object, but retain diagnostic profile message
        return cls(
            account=account,
            user=user,
            password=password,
            private_key_path=private_key_path,
            private_key_passphrase=private_key_passphrase,
            private_key_raw=private_key_raw,
            token=token,
            authenticator=authenticator,
            warehouse=warehouse,
            database=database,
            schema=schema_name,
            role=role,
            host=host,
            port=port,
            query_timeout=query_timeout,
            max_rows=max_rows,
            read_only=read_only,
            connection_name=resolved_profile,
        )

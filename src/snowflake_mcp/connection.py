"""Snowflake connection pool and session manager."""

from __future__ import annotations

import logging
import re
from typing import Any

import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from snowflake.connector import SnowflakeConnection
from snowflake.connector.cursor import DictCursor
from snowflake.core import Root

from snowflake_mcp.config import SnowflakeConfig

logger = logging.getLogger("snowflake_mcp")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$.]*$")


def quote_ident(name: str) -> str:
    """Safely format and quote a Snowflake SQL identifier."""
    name = str(name).strip()
    if not name:
        raise ValueError("Identifier name cannot be empty")
    # If standard uppercase or alphanumeric without spaces/quotes, return or safely double-quote
    clean = name.replace('"', '""')
    return f'"{clean}"'


def quote_literal(val: Any) -> str:
    """Safely format a string literal for Snowflake SQL statements."""
    if val is None:
        return "NULL"
    s = str(val).replace("'", "''")
    return f"'{s}'"


def is_sql_read_only(query: str) -> bool:
    """Classify if a SQL statement is strictly read-only."""
    # Strip comments
    q = re.sub(r"--.*?\n", "\n", query)
    q = re.sub(r"/\*.*?\*/", "", q, flags=re.DOTALL).strip()
    if not q:
        return True

    upper = q.upper()
    tokens = upper.split()
    if not tokens:
        return True

    mutating_keywords = (
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "MERGE ",
        "DROP ",
        "TRUNCATE ",
        "ALTER ",
        "CREATE ",
        "CALL ",
        "PUT ",
        "REMOVE ",
        "EXECUTE TASK",
        "UNDROP ",
    )

    first_word = tokens[0]
    if first_word in ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"):
        if any(kw in upper for kw in mutating_keywords):
            return False
        return True

    if first_word == "WITH":
        # Check CTE query ending in SELECT
        if any(kw in upper for kw in mutating_keywords):
            return False
        return "SELECT" in upper

    return False


class SnowflakeClient:
    """Thread-safe client managing Snowflake connection and Root object."""

    def __init__(self, config: SnowflakeConfig | None = None) -> None:
        self.config = config or SnowflakeConfig.from_env_or_config()
        self._conn: SnowflakeConnection | None = None
        self._root: Root | None = None

    def _load_private_key_bytes(self) -> bytes | None:
        """Parse RSA private key from path or raw string."""
        key_data = None
        if self.config.private_key_raw:
            key_data = self.config.private_key_raw.encode("utf-8")
        elif self.config.private_key_path:
            with open(self.config.private_key_path, "rb") as key_file:
                key_data = key_file.read()

        if not key_data:
            return None

        passphrase = self.config.private_key_passphrase.encode("utf-8") if self.config.private_key_passphrase else None

        p_key = serialization.load_pem_private_key(
            key_data,
            password=passphrase,
            backend=default_backend(),
        )

        return p_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def get_connection(self) -> SnowflakeConnection:
        """Retrieve or create an active SnowflakeConnection."""
        if self._conn is not None and not self._conn.is_closed():
            return self._conn

        if not self.config.user and not self.config.account and not self.config.token:
            profiles = SnowflakeConfig.list_available_connections()
            profiles_msg = (
                f"Available profiles in ~/.snowflake/connections.toml: {profiles}. "
                "Specify one with `--connection <profile>` or `SNOWFLAKE_CONNECTION_NAME=<profile>`."
                if profiles
                else (
                    "No profiles found in ~/.snowflake/connections.toml. "
                    "Set `SNOWFLAKE_ACCOUNT` and `SNOWFLAKE_USER` environment variables or run `snowflake-mcp --init`."
                )
            )
            raise ValueError(f"Missing Snowflake credentials. {profiles_msg}")

        conn_params: dict[str, Any] = {
            "account": self.config.account,
            "user": self.config.user,
            "application": "Snowflake_MCP_Server_Universal",
        }

        if self.config.password:
            conn_params["password"] = self.config.password

        pk_bytes = self._load_private_key_bytes()
        if pk_bytes:
            conn_params["private_key"] = pk_bytes

        if self.config.token:
            conn_params["token"] = self.config.token
            # If token provided, use PROGRAMMATIC_ACCESS_TOKEN or custom authenticator
            if self.config.authenticator:
                conn_params["authenticator"] = self.config.authenticator
            else:
                conn_params["authenticator"] = "PROGRAMMATIC_ACCESS_TOKEN"
        elif self.config.authenticator:
            conn_params["authenticator"] = self.config.authenticator

        if self.config.warehouse:
            conn_params["warehouse"] = self.config.warehouse
        if self.config.database:
            conn_params["database"] = self.config.database
        if self.config.schema_name:
            conn_params["schema"] = self.config.schema_name
        if self.config.role:
            conn_params["role"] = self.config.role
        if self.config.host:
            conn_params["host"] = self.config.host
            conn_params["port"] = self.config.port

        self._conn = snowflake.connector.connect(**conn_params)
        return self._conn

    def get_root(self) -> Root:
        """Retrieve or create a snowflake.core.Root instance."""
        if self._root is None:
            conn = self.get_connection()
            self._root = Root(conn)
        return self._root

    def execute_query(
        self,
        query: str,
        params: tuple[Any, ...] | None = None,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        """Execute a SQL query safely and return rows with metadata."""
        limit = max_rows or self.config.max_rows
        conn = self.get_connection()
        cursor = conn.cursor(DictCursor)
        try:
            cursor.execute(query, params)
            rows: list[dict[str, Any]] = cursor.fetchmany(limit)
            query_id = cursor.sfqid or ""
            rowcount = cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else len(rows)
            description = cursor.description or []
            columns = [col[0] for col in description]

            # Normalize data types (e.g. Decimal, datetime, UUID) for JSON-RPC safety
            normalized_rows: list[dict[str, Any]] = []
            for row in rows:
                clean_row: dict[str, Any] = {}
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        clean_row[k] = v.isoformat()
                    elif hasattr(v, "as_tuple") or v.__class__.__name__ == "Decimal":
                        clean_row[k] = float(v) if "." in str(v) else int(v)
                    elif isinstance(v, (bytes, bytearray)):
                        clean_row[k] = v.hex()
                    else:
                        clean_row[k] = v
                normalized_rows.append(clean_row)

            return {
                "query_id": query_id,
                "row_count": rowcount,
                "returned_rows": len(normalized_rows),
                "columns": columns,
                "data": normalized_rows,
                "has_more": len(rows) == limit,
            }
        finally:
            cursor.close()

    def switch_connection(self, connection_name: str, config_path: str | None = None) -> SnowflakeConfig:
        """Switch active Snowflake session to a different connection profile safely."""
        new_config = SnowflakeConfig.from_env_or_config(connection_name=connection_name, config_path=config_path)
        # Test connection validity before replacing current active session
        temp_client = SnowflakeClient(config=new_config)
        temp_client.get_connection()
        temp_client.close()

        # Validation succeeded; now switch active session
        self.close()
        self.config = new_config
        self.get_connection()
        return self.config

    def close(self) -> None:
        """Close active connection."""
        if self._conn is not None and not self._conn.is_closed():
            self._conn.close()
        self._conn = None
        self._root = None

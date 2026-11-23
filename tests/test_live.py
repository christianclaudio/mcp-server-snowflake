"""End-to-end live testing across all dynamically discovered MCP tools.

Fixture ``expect`` values and marker lists are the live-harness policy in
TESTING.md ("Live e2e expect policy").
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.errors import SafetyViolationError
from snowflake_mcp.server import create_server

_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+")
_PEM_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
    re.MULTILINE,
)
_REDACT_PATTERN = re.compile(
    r"(?i)(password|token|secret|key|private_key|authorization)(['\":\s=]+)(?:\"[^\"]*\"|'[^']*'|[^\s,;'\"]+)"
)

# User object names resolved during the live run. Failure output never shows them.
_RESOLVED_LIVE_NAMES: set[str] = set()


def _redact_live_names(text: str) -> str:
    """Mask the login name from SNOWFLAKE_USER and any resolved user object name."""
    names = {os.environ.get("SNOWFLAKE_USER", "").strip()} | _RESOLVED_LIVE_NAMES
    for name in sorted((name for name in names if name), key=len, reverse=True):
        text = re.sub(re.escape(name), "[redacted user]", text, flags=re.IGNORECASE)
    return text


def _redact_secrets(text: str) -> str:
    """Strip credentials, bearer tokens, and private keys from output strings."""
    redacted = _PEM_KEY_PATTERN.sub("[redacted private key]", text)
    redacted = _BEARER_PATTERN.sub("Bearer [redacted]", redacted)
    return _redact_live_names(_REDACT_PATTERN.sub(r"\1\2[redacted]", redacted))


TOOL_FIXTURES: dict[str, tuple[dict[str, Any], str]] = {
    "alerts_create_alert": (
        {
            "alert_name": "E2E_MCP_MISSING_OBJECT",
            "warehouse_name": "E2E_MCP_MISSING_OBJECT",
            "schedule": "1 MINUTE",
            "condition_sql": "SELECT 1",
            "action_sql": "SELECT 1",
            "confirm": False,
        },
        "confirm",
    ),
    "alerts_describe_alert": ({"alert_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "alerts_drop_alert": ({"alert_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "alerts_list_alerts": ({}, "success"),
    "alerts_resume_alert": ({"alert_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "alerts_suspend_alert": ({"alert_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_describe_compute_pool": ({"pool_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "compute_services_describe_streamlit": ({"streamlit_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "compute_services_list_compute_pools": ({}, "success"),
    "compute_services_list_image_repositories": ({}, "success"),
    "compute_services_list_services": ({}, "success"),
    "compute_services_list_streamlits": ({}, "success"),
    "compute_services_resume_compute_pool": ({"pool_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_suspend_compute_pool": ({"pool_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "cortex_analyst_query": ({"question": "e2e probe"}, "cortex"),
    "cortex_complete": ({"prompt": "e2e probe"}, "cortex"),
    "cortex_embed_text_768": ({"text": "e2e probe"}, "cortex"),
    "cortex_extract_answer": ({"source_text": "e2e probe", "question": "e2e probe"}, "cortex"),
    "cortex_search": ({"service_name": "E2E_MCP_MISSING_OBJECT", "query": "SELECT 1"}, "cortex"),
    "cortex_sentiment": ({"text": "e2e probe"}, "cortex"),
    "cortex_summarize": ({"text": "e2e probe"}, "cortex"),
    "cortex_translate": ({"text": "e2e probe", "source_language": "en", "target_language": "fr"}, "cortex"),
    "databases_clone_database": (
        {"source_database": "E2E_MCP_MISSING_DB", "target_database": "E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "databases_create_database": ({"name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "databases_describe_database": ({"database_name": "E2E_MCP_MISSING_DB"}, "rejected"),
    "databases_drop_database": ({"name": "e2e_probe_db", "confirm": False}, "confirm"),
    "databases_get_database_ddl": ({"database_name": "E2E_MCP_MISSING_DB"}, "rejected"),
    "databases_list_databases": ({}, "success"),
    "databases_undrop_database": ({"name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "dynamic_tables_describe_dynamic_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "dynamic_tables_describe_iceberg_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "dynamic_tables_list_catalog_integrations": ({}, "success"),
    "dynamic_tables_list_dynamic_tables": ({}, "success"),
    "dynamic_tables_list_external_volumes": ({}, "success"),
    "dynamic_tables_list_iceberg_tables": ({}, "success"),
    "dynamic_tables_refresh_dynamic_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "dynamic_tables_resume_dynamic_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "dynamic_tables_suspend_dynamic_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "governance_create_role": ({"role_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "governance_create_user": ({"user_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "governance_describe_role": ({"role_name": "E2E_MCP_MISSING_OBJECT"}, "success"),
    "governance_describe_user": ({"user_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "governance_drop_role": ({"role_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "governance_get_current_context": ({}, "success"),
    "governance_list_connections": ({}, "success"),
    "governance_list_grants_to_role": ({"role_name": os.environ.get("SNOWFLAKE_ROLE", "").strip()}, "success"),
    # dispatch_tool_call fills user_name from CURRENT_USER() at run time.
    "governance_list_grants_to_user": ({"user_name": ""}, "success"),
    "governance_list_roles": ({}, "success"),
    "governance_list_users": ({}, "success"),
    "governance_use_connection": ({"connection_name": "e2e_missing_profile"}, "confirm"),
    "horizon_describe_masking_policy": ({"policy_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "horizon_describe_row_access_policy": ({"policy_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "horizon_get_column_lineage": ({"table_name": "E2E_MCP_MISSING_OBJECT", "column_name": "ID"}, "success"),
    "horizon_get_object_lineage": ({"object_name": "E2E_MCP_MISSING_OBJECT"}, "success"),
    "horizon_list_masking_policies": ({}, "success"),
    "horizon_list_row_access_policies": ({}, "success"),
    "network_describe_network_policy": ({"policy_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "network_describe_network_rule": ({"rule_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "network_describe_password_policy": ({"policy_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "network_list_network_policies": ({}, "success"),
    "network_list_network_rules": ({}, "success"),
    "network_list_password_policies": ({}, "success"),
    "pipes_create_pipe": (
        {
            "pipe_name": "E2E_MCP_MISSING_OBJECT",
            "copy_statement": "COPY INTO E2E_MCP_MISSING_OBJECT FROM @E2E_MCP_MISSING_OBJECT",
        },
        "confirm",
    ),
    "pipes_describe_pipe": ({"pipe_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "pipes_drop_pipe": ({"pipe_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "pipes_get_pipe_status": ({"pipe_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "pipes_list_pipes": ({}, "success"),
    "programmability_describe_function": ({"function_signature": "E2E_MCP_MISSING_OBJECT(VARCHAR)"}, "rejected"),
    "programmability_describe_procedure": ({"procedure_signature": "E2E_MCP_MISSING_OBJECT(VARCHAR)"}, "rejected"),
    "programmability_describe_secret": ({"secret_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "programmability_list_event_tables": ({}, "success"),
    "programmability_list_functions": ({}, "success"),
    "programmability_list_integrations": ({}, "success"),
    "programmability_list_notification_integrations": ({}, "success"),
    "programmability_list_procedures": ({}, "success"),
    "programmability_list_secrets": ({}, "success"),
    "programmability_list_sequences": ({}, "success"),
    "queries_begin_transaction": ({}, "confirm"),
    "queries_cancel_query": ({"query_id": "e2e-missing-query-id", "confirm": False}, "confirm"),
    "queries_commit_transaction": ({}, "confirm"),
    "queries_execute_dml": ({"statement": "SELECT 1", "confirm": False}, "confirm"),
    "queries_get_query_history": ({}, "success"),
    "queries_get_query_operator_stats": ({"query_id": "e2e-missing-query-id"}, "rejected"),
    "queries_get_query_plan": ({"query": "SELECT 1"}, "success"),
    "queries_query": ({"query": "SELECT 1"}, "success"),
    "queries_rollback_transaction": ({"confirm": False}, "confirm"),
    "recipes_account_usage_summary": ({}, "success"),
    "recipes_clone_table_recipe": (
        {"source_table": "E2E_MCP_MISSING_OBJECT", "target_table": "E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "recipes_discover_schema_lineage": (
        {"database": "E2E_MCP_MISSING_DB", "schema_name": "E2E_MCP_MISSING_SCHEMA"},
        "rejected",
    ),
    "recipes_export_query_to_stage": (
        {"query": "SELECT 1", "stage_location": "@E2E_MCP_MISSING_DB.E2E_MCP_MISSING_SCHEMA.E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "recipes_health_check": ({}, "success"),
    "recipes_inspect_table_with_sample": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "recipes_profile_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "recipes_warehouse_scale_and_execute": (
        {"warehouse_name": "E2E_MCP_MISSING_OBJECT", "target_size": "XL", "query": "SELECT 1", "confirm": False},
        "confirm",
    ),
    "schemas_clone_schema": (
        {"source_schema": "E2E_MCP_MISSING_SCHEMA", "target_schema": "E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "schemas_create_schema": ({"name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "schemas_describe_schema": ({"schema_name": "E2E_MCP_MISSING_SCHEMA"}, "rejected"),
    "schemas_drop_schema": ({"name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "schemas_list_schemas": ({}, "success"),
    "schemas_undrop_schema": ({"name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "stages_create_stage": ({"stage_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "stages_describe_stage": ({"stage_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "stages_drop_stage": ({"stage_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "stages_list_stage_files": (
        {"stage_location": "@E2E_MCP_MISSING_DB.E2E_MCP_MISSING_SCHEMA.E2E_MCP_MISSING_OBJECT"},
        "rejected",
    ),
    "stages_list_stages": ({}, "success"),
    "stages_remove_stage_file": (
        {
            "stage_file_path": "@E2E_MCP_MISSING_DB.E2E_MCP_MISSING_SCHEMA.E2E_MCP_MISSING_OBJECT/file.csv",
            "confirm": False,
        },
        "confirm",
    ),
    "streams_create_stream": (
        {"stream_name": "E2E_MCP_MISSING_OBJECT", "on_table": "E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "streams_describe_stream": ({"stream_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "streams_drop_stream": ({"stream_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "streams_list_streams": ({}, "success"),
    "streams_read_stream_changes": ({"stream_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tables_clone_table": (
        {"source_table": "E2E_MCP_MISSING_OBJECT", "target_table": "E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "tables_create_table": ({"table_name": "E2E_MCP_MISSING_OBJECT", "columns_sql": "id INT"}, "confirm"),
    "tables_describe_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tables_drop_table": ({"table_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "tables_get_table_ddl": ({"object_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tables_list_tables": ({}, "success"),
    "tables_list_views": ({}, "success"),
    "tables_sample_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tables_truncate_table": ({"table_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "tables_undrop_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "tags_describe_tag": ({"tag_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tags_get_object_tag_references": ({"object_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tags_list_tags": ({}, "success"),
    "tags_set_object_tag": (
        {"object_name": "E2E_MCP_MISSING_OBJECT", "tag_name": "E2E_MCP_MISSING_OBJECT", "tag_value": "e2e"},
        "confirm",
    ),
    "tasks_create_task": ({"task_name": "E2E_MCP_MISSING_OBJECT", "sql_statement": "SELECT 1"}, "confirm"),
    "tasks_describe_task": ({"task_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tasks_drop_task": ({"task_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "tasks_execute_task": ({"task_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "tasks_list_tasks": ({}, "success"),
    "tasks_resume_task": ({"task_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "tasks_suspend_task": ({"task_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "warehouses_create_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "warehouses_describe_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT"}, "success"),
    "warehouses_drop_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "warehouses_get_warehouse_load_history": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "warehouses_list_warehouses": ({}, "success"),
    "warehouses_resize_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT", "size": "XL"}, "confirm"),
    "warehouses_resume_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "warehouses_suspend_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_abort_streamlit_live_version": (
        {"streamlit_name": "E2E_MCP_MISSING_OBJECT", "confirm": False},
        "confirm",
    ),
    "compute_services_add_notebook_live_version": (
        {
            "notebook_name": "E2E_MCP_MISSING_OBJECT",
            "database": "E2E_MCP_MISSING_OBJECT",
            "schema_name": "E2E_MCP_MISSING_OBJECT",
        },
        "confirm",
    ),
    "compute_services_add_streamlit_live_version": ({"streamlit_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_alter_service": ({"service_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_commit_notebook_version": (
        {
            "notebook_name": "E2E_MCP_MISSING_OBJECT",
            "database": "E2E_MCP_MISSING_OBJECT",
            "schema_name": "E2E_MCP_MISSING_OBJECT",
        },
        "confirm",
    ),
    "compute_services_commit_streamlit_version": ({"streamlit_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_create_notebook": ({"notebook_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_create_notebook_project": (
        {"project_name": "E2E_MCP_MISSING_OBJECT", "source": "snow://workspace/demo"},
        "confirm",
    ),
    "compute_services_create_service": (
        {
            "service_name": "E2E_MCP_MISSING_OBJECT",
            "compute_pool": "E2E_MCP_MISSING_OBJECT",
            "stage": "E2E_MCP_MISSING_OBJECT",
            "specification_file": "E2E_MCP_MISSING_OBJECT",
        },
        "confirm",
    ),
    "compute_services_describe_notebook": ({"notebook_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "compute_services_describe_service": ({"service_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "compute_services_drop_notebook": ({"notebook_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "compute_services_drop_service": ({"service_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "compute_services_execute_job_service": (
        {
            "compute_pool": "E2E_MCP_MISSING_OBJECT",
            "service_name": "E2E_MCP_MISSING_OBJECT",
            "stage": "E2E_MCP_MISSING_OBJECT",
            "specification_file": "E2E_MCP_MISSING_OBJECT",
            "confirm": False,
        },
        "confirm",
    ),
    "compute_services_execute_notebook": ({"notebook_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "compute_services_execute_notebook_project": (
        {
            "project_name": "E2E_MCP_MISSING_OBJECT",
            "main_file": "E2E_MCP_MISSING_OBJECT",
            "compute_pool": "E2E_MCP_MISSING_OBJECT",
            "query_warehouse": "E2E_MCP_MISSING_OBJECT",
            "runtime": "E2E_MCP_MISSING_OBJECT",
            "confirm": False,
        },
        "confirm",
    ),
    "compute_services_get_service_logs": (
        {
            "service_name": "E2E_MCP_MISSING_OBJECT",
            "instance_id": "E2E_MCP_MISSING_OBJECT",
            "container_name": "E2E_MCP_MISSING_OBJECT",
        },
        "rejected",
    ),
    "compute_services_list_images": ({"repository_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "compute_services_list_notebook_projects": ({}, "success"),
    "compute_services_list_notebooks": ({}, "success"),
    "compute_services_list_service_endpoints": ({"service_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "compute_services_pull_streamlit_from_git": ({"streamlit_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_push_streamlit_to_git": ({"streamlit_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_resume_service": ({"service_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_suspend_service": ({"service_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "cortex_chat_completion": ({"request": {"prompt": "e2e"}}, "confirm"),
    "cortex_create_search_service": (
        {
            "service_name": "E2E_MCP_MISSING_OBJECT",
            "search_column": "E2E_MCP_MISSING_OBJECT",
            "attributes": ["TITLE"],
            "warehouse": "E2E_MCP_MISSING_OBJECT",
            "target_lag": "1 hour",
            "query": "SELECT 1",
        },
        "confirm",
    ),
    "cortex_describe_search_service": ({"service_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "cortex_drop_search_service": ({"service_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "cortex_generate_response": ({"request": {"prompt": "e2e"}}, "confirm"),
    "cortex_generate_semantic_model": ({"request": {"prompt": "e2e"}}, "cortex"),
    "cortex_get_semantic_model_optimization": ({"optimization_name": "E2E_MCP_MISSING_OBJECT"}, "cortex"),
    "cortex_list_agents": ({}, "success"),
    "cortex_list_models": ({}, "cortex"),
    "cortex_list_search_services": ({}, "success"),
    "cortex_list_semantic_model_optimizations": ({"request": {"prompt": "e2e"}}, "cortex"),
    "cortex_message_completion": ({"request": {"prompt": "e2e"}}, "confirm"),
    "cortex_preselect_semantic_model_tables": ({"request": {"prompt": "e2e"}}, "cortex"),
    "cortex_resume_search_service": ({"service_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "cortex_run_agent": ({"agent_name": "E2E_MCP_MISSING_OBJECT", "request": {"prompt": "e2e"}}, "confirm"),
    "cortex_suggest_search": ({"service_name": "E2E_MCP_MISSING_OBJECT", "query": "SELECT 1"}, "cortex"),
    "cortex_suggest_semantic_model_metrics": ({"request": {"prompt": "e2e"}}, "cortex"),
    "cortex_suggest_verified_queries": ({"request": {"prompt": "e2e"}}, "cortex"),
    "cortex_suspend_search_service": ({"service_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "databases_disable_database_failover": ({"database_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "databases_disable_database_replication": (
        {"database_name": "E2E_MCP_MISSING_OBJECT", "confirm": False},
        "confirm",
    ),
    "databases_enable_database_failover": (
        {"database_name": "E2E_MCP_MISSING_OBJECT", "accounts": ["org.acct"], "confirm": False},
        "confirm",
    ),
    "databases_enable_database_replication": (
        {"database_name": "E2E_MCP_MISSING_OBJECT", "accounts": ["org.acct"], "confirm": False},
        "confirm",
    ),
    "databases_list_replication_databases": ({}, "success"),
    "databases_promote_database": ({"database_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "databases_refresh_database": ({"database_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "dynamic_tables_convert_iceberg_table_to_managed": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "dynamic_tables_create_iceberg_table": (
        {
            "table_name": "E2E_MCP_MISSING_OBJECT",
            "columns": ["ID NUMBER"],
            "external_volume": "E2E_MCP_MISSING_OBJECT",
            "base_location": "E2E_MCP_MISSING_OBJECT",
        },
        "confirm",
    ),
    "dynamic_tables_create_iceberg_table_from_catalog": (
        {"table_name": "E2E_MCP_MISSING_OBJECT", "catalog": "E2E_MCP_MISSING_OBJECT", "catalog_kind": "GLUE"},
        "confirm",
    ),
    "dynamic_tables_refresh_iceberg_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "governance_alter_user": ({"user_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "governance_create_account": (
        {
            "account_name": "E2E_MCP_MISSING_OBJECT",
            "admin_name": "E2E_MCP_MISSING_OBJECT",
            "admin_password": "e2e-dummy-password",
            "email": "e2e@example.com",
            "edition": "ENTERPRISE",
            "confirm": False,
        },
        "confirm",
    ),
    "governance_create_database_role": ({"database_role": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "governance_create_managed_account": (
        {
            "account_name": "E2E_MCP_MISSING_OBJECT",
            "admin_name": "E2E_MCP_MISSING_OBJECT",
            "admin_password": "e2e-dummy-password",
            "confirm": False,
        },
        "confirm",
    ),
    "governance_drop_account": ({"account_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "governance_drop_database_role": ({"database_role": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "governance_drop_managed_account": ({"account_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "governance_drop_user": ({"user_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "governance_grant_database_role": (
        {
            "database_role": "E2E_MCP_MISSING_OBJECT",
            "grantee_kind": "ROLE",
            "grantee": "E2E_MCP_MISSING_OBJECT",
            "confirm": False,
        },
        "confirm",
    ),
    "governance_grant_future_privileges": (
        {
            "privileges": ["SELECT"],
            "object_type": "TABLE",
            "scope_kind": "SCHEMA",
            "scope_name": "E2E_MCP_MISSING_OBJECT",
            "grantee_kind": "ROLE",
            "grantee": "E2E_MCP_MISSING_OBJECT",
            "confirm": False,
        },
        "confirm",
    ),
    "governance_grant_privileges": (
        {
            "privileges": ["SELECT"],
            "object_type": "TABLE",
            "grantee_kind": "ROLE",
            "grantee": "E2E_MCP_MISSING_OBJECT",
            "confirm": False,
        },
        "confirm",
    ),
    "governance_grant_role_to_user": (
        {"role_name": "E2E_MCP_MISSING_OBJECT", "user_name": "E2E_MCP_MISSING_OBJECT", "confirm": False},
        "confirm",
    ),
    "governance_list_accounts": ({}, "success"),
    "governance_list_database_roles": ({}, "success"),
    "governance_list_future_grants": ({}, "success"),
    "governance_list_grants_of_role": ({"role_name": "E2E_MCP_MISSING_OBJECT"}, "success"),
    "governance_list_grants_on_object": ({"object_type": "TABLE"}, "success"),
    "governance_list_grants_to_database_role": ({"database_role": "E2E_MCP_MISSING_OBJECT"}, "success"),
    "governance_list_managed_accounts": ({}, "success"),
    "governance_revoke_database_role": (
        {
            "database_role": "E2E_MCP_MISSING_OBJECT",
            "grantee_kind": "ROLE",
            "grantee": "E2E_MCP_MISSING_OBJECT",
            "confirm": False,
        },
        "confirm",
    ),
    "governance_revoke_future_privileges": (
        {
            "privileges": ["SELECT"],
            "object_type": "TABLE",
            "scope_kind": "SCHEMA",
            "scope_name": "E2E_MCP_MISSING_OBJECT",
            "grantee_kind": "ROLE",
            "grantee": "E2E_MCP_MISSING_OBJECT",
            "confirm": False,
        },
        "confirm",
    ),
    "governance_revoke_privileges": (
        {
            "privileges": ["SELECT"],
            "object_type": "TABLE",
            "grantee_kind": "ROLE",
            "grantee": "E2E_MCP_MISSING_OBJECT",
            "confirm": False,
        },
        "confirm",
    ),
    "governance_revoke_role_from_user": (
        {"role_name": "E2E_MCP_MISSING_OBJECT", "user_name": "E2E_MCP_MISSING_OBJECT", "confirm": False},
        "confirm",
    ),
    "governance_undrop_account": ({"account_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "programmability_cancel_code_bundle_execution": (
        {"execution_id": "E2E_MCP_MISSING_OBJECT", "confirm": False},
        "confirm",
    ),
    "programmability_create_api_integration": (
        {
            "integration_name": "E2E_MCP_MISSING_OBJECT",
            "api_provider": "aws_api_gateway",
            "api_allowed_prefixes": ["https://example.com"],
            "enabled": True,
            "confirm": False,
        },
        "confirm",
    ),
    "programmability_create_artifact_repository": (
        {"repository_name": "E2E_MCP_MISSING_OBJECT", "repository_type": "PYPI"},
        "confirm",
    ),
    "programmability_create_code_bundle": (
        {"bundle_name": "E2E_MCP_MISSING_OBJECT", "source": "snow://workspace/demo"},
        "confirm",
    ),
    "programmability_create_function": (
        {"function_name": "E2E_MCP_MISSING_OBJECT", "arguments": "x NUMBER", "returns": "NUMBER", "body": "SELECT 1"},
        "confirm",
    ),
    "programmability_create_sequence": ({"sequence_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "programmability_describe_api_integration": ({"integration_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "programmability_describe_artifact_repository": ({"repository_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "programmability_drop_artifact_repository": (
        {"repository_name": "E2E_MCP_MISSING_OBJECT", "confirm": False},
        "confirm",
    ),
    "programmability_drop_function": ({"function_signature": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "programmability_drop_sequence": ({"sequence_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "programmability_execute_code_bundle": (
        {"bundle_name": "E2E_MCP_MISSING_OBJECT", "entrypoint": "E2E_MCP_MISSING_OBJECT", "confirm": False},
        "confirm",
    ),
    "programmability_get_code_bundle_execution": ({"execution_id": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "programmability_invoke_function": (
        {"function_signature": "E2E_MCP_MISSING_OBJECT", "confirm": False},
        "confirm",
    ),
    "programmability_list_artifact_repositories": ({}, "success"),
    "programmability_list_code_bundle_executions": ({}, "success"),
    "programmability_list_code_bundles": ({}, "success"),
    "stages_get_presigned_url": (
        {"stage_name": "E2E_MCP_MISSING_OBJECT", "relative_path": "E2E_MCP_MISSING_OBJECT"},
        "success",
    ),
    "tables_create_view": ({"view_name": "E2E_MCP_MISSING_OBJECT", "query": "SELECT 1"}, "confirm"),
    "tables_describe_view": ({"view_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tables_drop_view": ({"view_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "tags_list_tag_assignments": (
        {
            "tag_database": "E2E_MCP_MISSING_OBJECT",
            "tag_schema": "E2E_MCP_MISSING_OBJECT",
            "tag_name": "E2E_MCP_MISSING_OBJECT",
        },
        "success",
    ),
    "tags_unset_object_tag": (
        {"object_name": "E2E_MCP_MISSING_OBJECT", "tag_name": "E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "tasks_list_complete_task_graphs": ({}, "success"),
    "tasks_list_current_task_graphs": ({}, "success"),
    "tasks_list_task_dependents": (
        {"task_name": "E2E_MCP_MISSING_OBJECT", "database": "E2E_MCP_MISSING_OBJECT"},
        "success",
    ),
}

# Tools left out of the live catalog. Each value is a non-empty skip reason.
EXPLICIT_TOOL_SKIPS: dict[str, str] = {}

# Fixtures that name the live run's own role or user. The role comes from the environment at run
# time and the user from CURRENT_USER(), so neither lives in the repo. The probe is skipped when
# the variable is unset.
_ENV_NAMED_FIXTURES: dict[str, str] = {
    "governance_list_grants_to_role": "SNOWFLAKE_ROLE",
    "governance_list_grants_to_user": "SNOWFLAKE_USER",
}

CORTEX_SKIP_REASON = "Cortex is not available on this account"
# Expect policy (success | rejected | confirm | cortex): TESTING.md "Live e2e expect policy".
_SAFE_REJECTION_MARKERS = ("does not exist", "not authorized", "insufficient privileges")
# GET_QUERY_OPERATOR_STATS rejects a non-UUID query id with wording the global markers miss. The
# code bundle execution lookup answers a non-UUID execution id with HTTP 400 code 505167 ("invalid
# notebook configuration: query_id is not a valid uuid"); there is no real execution id to use.
_TOOL_REJECTION_MARKERS: dict[str, tuple[str, ...]] = {
    "programmability_get_code_bundle_execution": ("505167",),
    "queries_get_query_operator_stats": (
        "invalid uuid",
        "invalid query id",
        "invalid value",
        "get_query_operator_stats",
    ),
}
_CONFIRM_MARKERS = ("requires_confirmation", "denied in read-only", "read-only")
# Lineage tools read SNOWFLAKE.ACCOUNT_USAGE. A role without that grant gets isError with this
# privilege error, which is the expected result there; with the grant the call must succeed.
_ACCOUNT_USAGE_TOOLS = frozenset({"horizon_get_object_lineage", "horizon_get_column_lineage"})
_ACCOUNT_USAGE_DENIED_MARKERS = ("schema 'snowflake.account_usage' does not exist or not authorized",)
# SHOW ACCOUNTS needs ORGADMIN. Without it the live run gets 003001 insufficient privileges, which
# is the expected result there (the ACCOUNT_USAGE marker above does not match it); with it the call
# must succeed.
_SHOW_ACCOUNTS_TOOLS = frozenset({"governance_list_accounts"})
_SHOW_ACCOUNTS_DENIED_MARKERS = ("insufficient privileges", "003001")
# Cortex Search Suggest answers HTTP 403 with code 399104 ("Cortex Search Suggest API is not enabled")
# when the account does not offer it.
_CORTEX_UNAVAILABLE_MARKERS = (
    "unknown function",
    "cortex not enabled",
    "not available",
    "399104",
    "suggest api is not enabled",
)
# REST-backed tools refuse password and other authenticators before any request is sent
# (snowflake_mcp.rest.SnowflakeRestClient.headers). Key-pair, PAT and OAuth runs still call them.
REST_AUTH_SKIP_REASON = (
    "REST tools need key-pair, PAT or OAuth-token authentication; this run is configured for another "
    "authentication method."
)
_REST_AUTH_MARKERS = ("snowflake rest tools need key-pair, pat or oauth-token authentication",)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _rejection_markers(tool_name: str) -> tuple[str, ...]:
    return _SAFE_REJECTION_MARKERS + _TOOL_REJECTION_MARKERS.get(tool_name, ())


def _account_usage_denied(tool_name: str, text: str) -> bool:
    return tool_name in _ACCOUNT_USAGE_TOOLS and _contains_any(text, _ACCOUNT_USAGE_DENIED_MARKERS)


def _show_accounts_denied(tool_name: str, text: str) -> bool:
    return tool_name in _SHOW_ACCOUNTS_TOOLS and _contains_any(text, _SHOW_ACCOUNTS_DENIED_MARKERS)


def _cortex_skip_requested() -> bool:
    flag = os.environ.get("SNOWFLAKE_MCP_E2E_SKIP_CORTEX", "").strip().lower()
    return flag in {"1", "true", "yes"}


# Live run with only account, user and token: TESTING.md "Live run with only account, user and token".
# Schema-scoped probes are rewritten to fully qualified names in a database and schema discovered
# read-only (SHOW DATABASES / SHOW SCHEMAS). Never USE <anything>; session state is not changed.
_NS_UNUSED = object()
_NS_FORM_SCHEMA = "schema"
_NS_FORM_DOTTED = "dotted"
_NS_FORM_ARGS = "args"
# tool -> (object argument, form). "dotted" sends <D>.<S>.<object>; "schema" sends <D>.<schema>;
# "args" keeps the bare object and adds database=<D>, schema_name=<S>.
_NAMESPACED: dict[str, tuple[str, str]] = {
    "schemas_describe_schema": ("schema_name", _NS_FORM_SCHEMA),
    "stages_describe_stage": ("stage_name", _NS_FORM_DOTTED),
    "tasks_describe_task": ("task_name", _NS_FORM_DOTTED),
    "dynamic_tables_describe_dynamic_table": ("table_name", _NS_FORM_DOTTED),
    "dynamic_tables_describe_iceberg_table": ("table_name", _NS_FORM_DOTTED),
    "network_describe_network_rule": ("rule_name", _NS_FORM_DOTTED),
    "network_describe_password_policy": ("policy_name", _NS_FORM_DOTTED),
    "compute_services_describe_streamlit": ("streamlit_name", _NS_FORM_DOTTED),
    "programmability_describe_procedure": ("procedure_signature", _NS_FORM_ARGS),
    "programmability_describe_function": ("function_signature", _NS_FORM_ARGS),
    "pipes_describe_pipe": ("pipe_name", _NS_FORM_ARGS),
    "alerts_describe_alert": ("alert_name", _NS_FORM_ARGS),
    "horizon_describe_masking_policy": ("policy_name", _NS_FORM_ARGS),
    "horizon_describe_row_access_policy": ("policy_name", _NS_FORM_ARGS),
    "programmability_describe_secret": ("secret_name", _NS_FORM_ARGS),
    "tags_describe_tag": ("tag_name", _NS_FORM_ARGS),
    "tables_describe_table": ("table_name", _NS_FORM_DOTTED),
    "tables_get_table_ddl": ("object_name", _NS_FORM_DOTTED),
    "tables_sample_table": ("table_name", _NS_FORM_DOTTED),
    "streams_describe_stream": ("stream_name", _NS_FORM_DOTTED),
    "streams_read_stream_changes": ("stream_name", _NS_FORM_DOTTED),
    "recipes_inspect_table_with_sample": ("table_name", _NS_FORM_DOTTED),
    "recipes_profile_table": ("table_name", _NS_FORM_DOTTED),
    "pipes_get_pipe_status": ("pipe_name", _NS_FORM_DOTTED),
    # Cortex Search REST call: without database and schema_name it fails before any request. Stays cortex.
    "cortex_suggest_search": ("service_name", _NS_FORM_ARGS),
}
# Database-scoped list probes. With no current database these tools refuse to run, so the
# discovered database is passed as this argument; with no namespace they skip like _NAMESPACED.
_DATABASE_SCOPED: dict[str, str] = {
    "governance_list_database_roles": "database",
    "governance_list_future_grants": "database",
}
# Live fixture objects created once per account by scripts/live_fixtures.sql (TESTING.md "Live fixture
# objects"). tool -> the object its probe names. The harness stays read-only and never creates them.
_FIXTURE_TABLE = "E2E_MCP_FIXTURE_TABLE"
_FIXTURE_DATABASE_ROLE = "E2E_MCP_FIXTURE_DATABASE_ROLE"
_FIXTURE_STAGE = "E2E_MCP_FIXTURE_STAGE"
_FIXTURE_TASK = "E2E_MCP_FIXTURE_TASK"
_FIXTURE_OBJECTS: dict[str, str] = {
    "governance_list_grants_on_object": _FIXTURE_TABLE,
    "governance_list_grants_to_database_role": _FIXTURE_DATABASE_ROLE,
    "stages_get_presigned_url": _FIXTURE_STAGE,
    "tasks_list_task_dependents": _FIXTURE_TASK,
}
# GET_PRESIGNED_URL returns a URL even when the file does not exist:
# https://docs.snowflake.com/en/sql-reference/functions/get_presigned_url
_FIXTURE_MISSING_FILE = "e2e-missing-file.txt"
NO_FIXTURE_OBJECTS_REASON = "live fixture objects are not present; run scripts/live_fixtures.sql once per account"
_NAMESPACE_CANDIDATES = 10
NO_NAMESPACE_REASON = (
    "No readable database and schema for the current role among the first 10 databases (kind STANDARD) "
    "and their schemas, so the fully qualified probe could not be built."
)

WAREHOUSE_PRESENT = "PRESENT"
WAREHOUSE_NULL = "NULL"
WAREHOUSE_UNKNOWN = "UNKNOWN"
NO_WAREHOUSE_REASON = (
    "No current warehouse: CURRENT_WAREHOUSE() is NULL and none is configured "
    "(SNOWFLAKE_WAREHOUSE or connections.toml); a query cannot be submitted without one."
)
_NO_WAREHOUSE_MARKERS = ("no active warehouse selected",)
# Fallback paths join errors as "<primary>; fallback failed: <e>" (warehouses.py:216 on main, :250 on #57).
# A joined error carries a second, warehouse-independent failure, so it never skips.
_FALLBACK_FAILED_MARKER = ("fallback failed",)
_NO_WAREHOUSE_EXPECTS = frozenset({"success", "rejected", "cortex"})


def quote_name(name: str) -> str:
    """Double-quote one identifier part, doubling any embedded double quote."""
    return '"' + name.replace('"', '""') + '"'


def _only_no_warehouse_error(text: str) -> bool:
    return _contains_any(text, _NO_WAREHOUSE_MARKERS) and not _contains_any(text, _FALLBACK_FAILED_MARKER)


def _no_warehouse_skip(text: str, expect: str, warehouse_state: str, warehouse_configured: bool) -> bool:
    """Skip only when CURRENT_WAREHOUSE() is NULL, none is configured, and the no-warehouse error is the only error."""
    return (
        warehouse_state == WAREHOUSE_NULL
        and not warehouse_configured
        and expect in _NO_WAREHOUSE_EXPECTS
        and _only_no_warehouse_error(text)
    )


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _is_partial_body(body: str) -> bool:
    """A non-error body is checked for the no-warehouse error only when its status is "partial"."""
    parsed = _json_object(body)
    return parsed is not None and parsed.get("status") == "partial"


def _row_value(row: dict[str, Any], key: str) -> Any:
    return row.get(key, row.get(key.upper()))


async def _call_json(srv: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    try:
        res = await srv.call_tool(tool_name, arguments)
    except Exception:
        return None
    if not isinstance(res, CallToolResult) or res.is_error or not res.content:
        return None
    return _json_object(res.content[0].text)


async def _current_user_name(srv: Any) -> str | None:
    """Return CURRENT_USER(), the user object name, which can differ from the login name."""
    payload = await _call_json(srv, "governance_get_current_context", {})
    context = payload.get("context") if payload else None
    user = _row_value(context, "user") if isinstance(context, dict) else None
    return user.strip() if isinstance(user, str) and user.strip() else None


async def resolve_live_user_mask(srv: Any) -> str | None:
    """Resolve CURRENT_USER() once, before the tool loop, and add it to the mask set.

    FAIL messages from tools that run before the grants-to-user probe then mask a user object name
    that differs from the login name. The name is never logged.
    """
    user_name = await _current_user_name(srv)
    if user_name is not None:
        _RESOLVED_LIVE_NAMES.add(user_name)
    return user_name


def _usable_names(rows: Any, *, kind: str | None = None, exclude: tuple[str, ...] = ()) -> list[str]:
    names: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = _row_value(row, "name")
        if not isinstance(name, str) or not name or name in exclude:
            continue
        if kind is not None and _row_value(row, "kind") != kind:
            continue
        # Defensive only: SHOW without HISTORY returns no dropped rows.
        if _row_value(row, "dropped_on"):
            continue
        names.append(name)
    return sorted(names)


async def discover_namespace(srv: Any) -> tuple[str, str] | None:
    """Find a readable (database, schema), both quoted, through the server's own read-only list tools."""
    payload = await _call_json(srv, "databases_list_databases", {})
    if payload is None:
        return None
    databases = _usable_names(payload.get("databases"), kind="STANDARD")[:_NAMESPACE_CANDIDATES]
    for database in databases:
        schemas_payload = await _call_json(srv, "schemas_list_schemas", {"database": quote_name(database)})
        if schemas_payload is None:
            continue
        schemas = _usable_names(schemas_payload.get("schemas"), exclude=("INFORMATION_SCHEMA",))
        if not schemas:
            continue
        schema = "PUBLIC" if "PUBLIC" in schemas else schemas[0]
        return (quote_name(database), quote_name(schema))
    return None


# Read-only list probes for the schema-level fixture objects: (tool, result key, object name).
_FIXTURE_LISTINGS = (
    ("tables_list_tables", "tables", _FIXTURE_TABLE),
    ("stages_list_stages", "stages", _FIXTURE_STAGE),
    ("tasks_list_tasks", "tasks", _FIXTURE_TASK),
)


async def _schema_has_fixture_objects(srv: Any, database: str, schema: str) -> bool:
    for tool_name, key, object_name in _FIXTURE_LISTINGS:
        arguments = {"database": database, "schema_name": schema, "pattern": object_name}
        payload = await _call_json(srv, tool_name, arguments)
        if payload is None or object_name not in _usable_names(payload.get(key)):
            return False
    return True


async def discover_fixture_namespace(srv: Any) -> tuple[str, str] | None:
    """Find the (database, schema), both quoted, that holds all four live fixture objects.

    Every STANDARD database is tried (not only the first 10), through the server's own read-only list
    tools: the database role first, then the table, stage and task in each schema, PUBLIC first.
    The database is not hardcoded: it is wherever scripts/live_fixtures.sql was run.
    """
    payload = await _call_json(srv, "databases_list_databases", {})
    if payload is None:
        return None
    for database in _usable_names(payload.get("databases"), kind="STANDARD"):
        quoted_database = quote_name(database)
        roles = await _call_json(srv, "governance_list_database_roles", {"database": quoted_database})
        if roles is None or _FIXTURE_DATABASE_ROLE not in _usable_names(roles.get("database_roles")):
            continue
        schemas_payload = await _call_json(srv, "schemas_list_schemas", {"database": quoted_database})
        if schemas_payload is None:
            continue
        schemas = _usable_names(schemas_payload.get("schemas"), exclude=("INFORMATION_SCHEMA",))
        for schema in sorted(schemas, key=lambda name: name != "PUBLIC"):
            if await _schema_has_fixture_objects(srv, quoted_database, quote_name(schema)):
                return (quoted_database, quote_name(schema))
    return None


def _fixture_object_arguments(tool_name: str, database: str, schema: str) -> dict[str, Any]:
    """Arguments that name the live fixture object for one ``_FIXTURE_OBJECTS`` tool."""
    if tool_name == "governance_list_grants_on_object":
        return {"object_type": "TABLE", "object_name": f"{database}.{schema}.{_FIXTURE_TABLE}"}
    if tool_name == "governance_list_grants_to_database_role":
        return {"database_role": f"{database}.{_FIXTURE_DATABASE_ROLE}"}
    if tool_name == "stages_get_presigned_url":
        return {
            "stage_name": _FIXTURE_STAGE,
            "relative_path": _FIXTURE_MISSING_FILE,
            "database": database,
            "schema_name": schema,
        }
    # TASK_DEPENDENTS takes the whole name in single quotes (the tool's quote_literal), with the
    # double-quoted parts inside: https://docs.snowflake.com/en/sql-reference/functions/task_dependents
    return {"database": database, "task_name": f"{database}.{schema}.{quote_name(_FIXTURE_TASK)}"}


def namespaced_fixtures(namespace: tuple[str, str]) -> dict[str, tuple[dict[str, Any], str]]:
    """Return a copy of TOOL_FIXTURES with the schema-scoped probes qualified. TOOL_FIXTURES is not changed."""
    database, schema = namespace
    fixtures = dict(TOOL_FIXTURES)
    for tool_name, (argument, form) in _NAMESPACED.items():
        arguments, expect = fixtures[tool_name]
        rewritten = dict(arguments)
        if form == _NS_FORM_SCHEMA:
            rewritten[argument] = f"{database}.{arguments[argument]}"
        elif form == _NS_FORM_DOTTED:
            rewritten[argument] = f"{database}.{schema}.{arguments[argument]}"
        else:
            rewritten["database"] = database
            rewritten["schema_name"] = schema
        fixtures[tool_name] = (rewritten, expect)
    for tool_name, argument in _DATABASE_SCOPED.items():
        arguments, expect = fixtures[tool_name]
        fixtures[tool_name] = ({**arguments, argument: database}, expect)
    for tool_name in _FIXTURE_OBJECTS:
        arguments, expect = fixtures[tool_name]
        fixtures[tool_name] = ({**arguments, **_fixture_object_arguments(tool_name, database, schema)}, expect)
    return fixtures


async def read_warehouse_state(srv: Any, warehouse_configured: bool) -> tuple[str, str | None]:
    """Return (warehouse state, current role) from get_current_context. Only WAREHOUSE and ROLE are read."""
    try:
        res = await srv.call_tool("governance_get_current_context", {})
    except Exception as exc:
        failed_text = str(exc)
    else:
        if not isinstance(res, CallToolResult):
            return (WAREHOUSE_UNKNOWN, None)
        body = res.content[0].text if res.content else ""
        if not res.is_error:
            payload = _json_object(body)
            context = payload.get("context") if payload else None
            if not isinstance(context, dict) or not context:
                return (WAREHOUSE_UNKNOWN, None)
            role = _row_value(context, "role")
            role_text = role if isinstance(role, str) and role else None
            if "WAREHOUSE" in context:
                warehouse = context["WAREHOUSE"]
            elif "warehouse" in context:
                warehouse = context["warehouse"]
            else:
                return (WAREHOUSE_UNKNOWN, role_text)
            if warehouse is None:
                return (WAREHOUSE_NULL, role_text)
            if isinstance(warehouse, str) and warehouse:
                return (WAREHOUSE_PRESENT, role_text)
            return (WAREHOUSE_UNKNOWN, role_text)
        failed_text = body
    # The context SELECT is itself a query; it may fail with the no-warehouse error.
    if not warehouse_configured and _only_no_warehouse_error(failed_text):
        return (WAREHOUSE_NULL, None)
    return (WAREHOUSE_UNKNOWN, None)


def live_log_lines(
    namespace: tuple[str, str] | None,
    role: str | None,
    warehouse_state: str,
    warehouse_configured: bool,
    fixture_objects_present: bool | None = None,
) -> list[str]:
    """Three log lines, plus a fourth for the live fixture objects when that is given.

    The role has its own line; the account identifier and user are never logged. A database or
    schema name that contains the login name or the resolved user object name is masked.
    """
    if namespace is None:
        namespace_line = f"live namespace: none ({NO_NAMESPACE_REASON})"
    else:
        namespace_line = _redact_live_names(f"live namespace: database={namespace[0]} schema={namespace[1]}")
    lines = [
        namespace_line,
        f"live role: {role or 'unknown'}",
        f"live warehouse: state={warehouse_state} configured={str(warehouse_configured).lower()}",
    ]
    if fixture_objects_present is not None:
        state = "present" if fixture_objects_present else f"absent ({NO_FIXTURE_OBJECTS_REASON})"
        lines.append(f"live fixtures: {state}")
    return lines


async def dispatch_tool_call(
    srv: Any,
    tool_name: str,
    is_destructive: bool,
    *,
    fixtures: dict[str, tuple[dict[str, Any], str]] = TOOL_FIXTURES,
    namespace: tuple[str, str] | None | object = _NS_UNUSED,
    warehouse_state: str = WAREHOUSE_UNKNOWN,
    warehouse_configured: bool = True,
    fixture_objects_present: bool = True,
) -> tuple[str, bool, str | None]:
    """Execute one fixture and return (status, is_error, error_message).

    ``is_destructive`` is retained for existing callers. The fixture ``expect``
    value (success, rejected, confirm, or cortex) decides the assertion.
    The keyword-only arguments are set by the live test; their defaults keep
    the earlier behaviour (no namespace skip, no warehouse skip, no fixture-object skip).
    """
    del is_destructive
    if tool_name not in fixtures:
        reason = EXPLICIT_TOOL_SKIPS.get(tool_name)
        if reason:
            return ("SKIP", False, reason)
        return ("FAIL", True, f"No fixture for {tool_name}")

    if namespace is None and any(tool_name in group for group in (_NAMESPACED, _DATABASE_SCOPED, _FIXTURE_OBJECTS)):
        return ("SKIP", False, NO_NAMESPACE_REASON)
    if tool_name in _FIXTURE_OBJECTS and not fixture_objects_present:
        return ("SKIP", False, NO_FIXTURE_OBJECTS_REASON)

    env_name = _ENV_NAMED_FIXTURES.get(tool_name)
    if env_name and not os.environ.get(env_name, "").strip():
        return ("SKIP", False, f"{env_name} is not set")

    arguments, expect = fixtures[tool_name]
    if tool_name == "governance_list_grants_to_user":
        # SHOW GRANTS TO USER takes the user object name, so resolve it here instead of using the login name.
        user_name = await _current_user_name(srv)
        if user_name is None:
            return ("SKIP", False, "CURRENT_USER() is not available")
        _RESOLVED_LIVE_NAMES.add(user_name)
        arguments = {**arguments, "user_name": user_name}
    if expect == "cortex" and _cortex_skip_requested():
        return ("SKIP", False, CORTEX_SKIP_REASON)

    def no_warehouse(text: str) -> bool:
        return _no_warehouse_skip(text, expect, warehouse_state, warehouse_configured)

    try:
        res = await srv.call_tool(tool_name, arguments)
    except SafetyViolationError as exc:
        message = str(exc)
        if no_warehouse(message):
            return ("SKIP", True, NO_WAREHOUSE_REASON)
        if expect == "confirm" and _contains_any(message, _CONFIRM_MARKERS):
            return ("PASS", True, None)
        if expect == "rejected" and _contains_any(message, _rejection_markers(tool_name)):
            return ("PASS", True, None)
        if expect == "cortex" and _contains_any(message, _SAFE_REJECTION_MARKERS):
            return ("PASS", True, None)
        if expect == "cortex" and _contains_any(message, _CORTEX_UNAVAILABLE_MARKERS):
            return ("SKIP", True, CORTEX_SKIP_REASON)
        return ("FAIL", True, f"SafetyViolationError: {_redact_secrets(message)}")
    except Exception as exc:
        message = str(exc)
        if no_warehouse(message):
            return ("SKIP", True, NO_WAREHOUSE_REASON)
        if expect != "confirm" and _contains_any(message, _REST_AUTH_MARKERS):
            return ("SKIP", True, REST_AUTH_SKIP_REASON)
        if expect == "success" and (
            _account_usage_denied(tool_name, message) or _show_accounts_denied(tool_name, message)
        ):
            return ("PASS", True, None)
        if expect == "cortex" and _contains_any(message, _CORTEX_UNAVAILABLE_MARKERS):
            return ("SKIP", True, CORTEX_SKIP_REASON)
        if expect == "rejected" and _contains_any(message, _rejection_markers(tool_name)):
            return ("PASS", True, None)
        if expect == "cortex" and _contains_any(message, _SAFE_REJECTION_MARKERS):
            return ("PASS", True, None)
        sanitized = _redact_secrets(message)
        return ("FAIL", True, f"{type(exc).__name__}: {sanitized}")

    if not isinstance(res, CallToolResult):
        return ("FAIL", True, f"Expected CallToolResult, got {type(res).__name__}")

    body = res.content[0].text if res.content else ""
    if (res.is_error or _is_partial_body(body)) and no_warehouse(body):
        return ("SKIP", True, NO_WAREHOUSE_REASON)
    if res.is_error and expect != "confirm" and _contains_any(body, _REST_AUTH_MARKERS):
        return ("SKIP", True, REST_AUTH_SKIP_REASON)
    if expect == "confirm":
        if _contains_any(body, _CONFIRM_MARKERS):
            return ("PASS", bool(res.is_error), None)
        return (
            "FAIL",
            True,
            f"Destructive safety gate bypassed for {tool_name}: unexpected success response",
        )
    if expect == "rejected":
        if _contains_any(body, _rejection_markers(tool_name)):
            return ("PASS", bool(res.is_error), None)
        if res.is_error:
            return ("FAIL", True, _redact_secrets(body) or None)
        return ("FAIL", True, f"Expected a safe rejection for {tool_name}")
    if expect == "cortex":
        if _contains_any(body, _SAFE_REJECTION_MARKERS):
            return ("PASS", bool(res.is_error), None)
        if _contains_any(body, _CORTEX_UNAVAILABLE_MARKERS):
            return ("SKIP", True, CORTEX_SKIP_REASON)
        if res.is_error:
            return ("FAIL", True, _redact_secrets(body) or None)
        return ("PASS", False, None)

    is_err = bool(res.is_error)
    if is_err and (_account_usage_denied(tool_name, body) or _show_accounts_denied(tool_name, body)):
        return ("PASS", True, None)
    return ("FAIL" if is_err else "PASS", is_err, None)


@pytest.fixture
def mock_snowflake_client() -> SnowflakeClient:
    """Fixture providing a mock SnowflakeClient configured for offline verification."""
    cfg = SnowflakeConfig(account="mock_account", user="mock_user", read_only=False)
    client = SnowflakeClient(config=cfg)
    mock_cursor = MagicMock()
    mock_cursor.sfqid = "mock_qid_001"
    mock_cursor.rowcount = 1
    mock_cursor.description = [("STATUS", 0, 0, 0, 0, 0, 0)]
    mock_cursor.fetchmany.return_value = [{"STATUS": "OK"}]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    client._connection = mock_conn
    client.get_connection = MagicMock(return_value=mock_conn)  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_dispatch_tool_call_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify tool invocation logic and dispatch behavior using AsyncMock."""
    mock_srv = AsyncMock()

    # 1. Successful non-destructive tool
    mock_srv.call_tool.return_value = CallToolResult(content=[TextContent(type="text", text="ok")], is_error=False)
    status, is_err, err = await dispatch_tool_call(mock_srv, "queries_query", is_destructive=False)
    assert status == "PASS"
    assert not is_err
    assert err is None
    mock_srv.call_tool.assert_awaited_with("queries_query", {"query": "SELECT 1"})

    # 2. Destructive tool with safety confirmation gate from fixture map
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"status": "requires_confirmation"}))],
        is_error=False,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "databases_drop_database", is_destructive=True)
    assert status == "PASS"
    assert not is_err
    mock_srv.call_tool.assert_awaited_with("databases_drop_database", {"name": "e2e_probe_db", "confirm": False})

    # 3. Unexpected destructive success must fail the safety check
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"status": "success"}))],
        is_error=False,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "databases_drop_database", is_destructive=True)
    assert status == "FAIL"
    assert is_err
    assert "Destructive safety gate bypassed" in (err or "")

    # 4. Error response with is_error=True
    mock_srv.call_tool.return_value = CallToolResult(content=[TextContent(type="text", text="error")], is_error=True)
    status, is_err, err = await dispatch_tool_call(mock_srv, "queries_query", is_destructive=False)
    assert status == "FAIL"
    assert is_err
    assert err is None

    # 4. Unexpected exception raised with secret redaction
    mock_srv.call_tool.side_effect = RuntimeError(
        "failed with Bearer eyJhbGciOiJIUzI1NiIsIn... and password=supersecret123 and -----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "queries_query", is_destructive=False)
    assert status == "FAIL"
    assert is_err
    assert err is not None
    assert "supersecret123" not in err
    assert "eyJhbGci" not in err
    assert "MIIE" not in err
    assert "[redacted]" in err
    assert "[redacted private key]" in err
    assert "RuntimeError:" in err

    # 5. Non-CallToolResult return value returns failure
    mock_srv.call_tool.side_effect = None
    mock_srv.call_tool.return_value = "plain string output"
    status, is_err, err = await dispatch_tool_call(mock_srv, "queries_query", is_destructive=False)
    assert status == "FAIL"
    assert is_err
    assert "Expected CallToolResult" in (err or "")


# Soft-empty missing-name results stay expect "success". See TESTING.md.
_EMPTY_SUCCESS_TOOLS = (
    "warehouses_describe_warehouse",
    "governance_describe_role",
    "horizon_get_object_lineage",
    "horizon_get_column_lineage",
)


_DENIED = (
    "002003 (02000): SQL compilation error:\\nSchema 'SNOWFLAKE.ACCOUNT_USAGE' does not exist or not "
    "authorized. Your primary role NEXUS_RO must have USAGE or any other privilege granted on SCHEMA "
    "SNOWFLAKE.ACCOUNT_USAGE."
)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(_ACCOUNT_USAGE_TOOLS))
async def test_lineage_expects_the_account_usage_privilege_error_without_the_grant(name: str) -> None:
    """Without the grant the lineage call is isError with the privilege error; other errors still fail."""
    mock_srv = AsyncMock()
    mock_srv.call_tool.side_effect = ToolError(f'{{"status": "error", "error": "{_DENIED}"}}')
    assert await dispatch_tool_call(mock_srv, name, is_destructive=False) == ("PASS", True, None)

    mock_srv.call_tool.side_effect = None
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=f'{{"status": "error", "error": "{_DENIED}"}}')], is_error=True
    )
    assert await dispatch_tool_call(mock_srv, name, is_destructive=False) == ("PASS", True, None)

    # With the grant the call runs for real and must succeed.
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text='{"status": "success", "upstream": []}')], is_error=False
    )
    assert await dispatch_tool_call(mock_srv, name, is_destructive=False) == ("PASS", False, None)

    # Any other error is a real failure.
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text='{"status": "error", "error": "warehouse suspended"}')], is_error=True
    )
    assert (await dispatch_tool_call(mock_srv, name, is_destructive=False))[0] == "FAIL"
    mock_srv.call_tool.side_effect = ToolError('{"status": "error", "error": "syntax error"}')
    assert (await dispatch_tool_call(mock_srv, name, is_destructive=False))[0] == "FAIL"


@pytest.mark.asyncio
async def test_account_usage_error_does_not_pass_other_success_tools() -> None:
    mock_srv = AsyncMock()
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=f'{{"status": "error", "error": "{_DENIED}"}}')], is_error=True
    )
    assert (await dispatch_tool_call(mock_srv, "warehouses_list_warehouses", is_destructive=False))[0] == "FAIL"


@pytest.mark.asyncio
async def test_dispatch_empty_success_and_operator_stats_rejection() -> None:
    """Missing-object describe/lineage calls are empty successes; operator stats is a rejection."""
    mock_srv = AsyncMock()
    empty = CallToolResult(
        content=[TextContent(type="text", text='{"status": "success", "details": null}')],
        is_error=False,
    )
    for name in _EMPTY_SUCCESS_TOOLS:
        assert TOOL_FIXTURES[name][1] == "success"
        mock_srv.call_tool.return_value = empty
        status, is_err, err = await dispatch_tool_call(mock_srv, name, is_destructive=False)
        assert status == "PASS", name
        assert not is_err
        assert err is None
        mock_srv.call_tool.assert_awaited_with(name, TOOL_FIXTURES[name][0])

    assert TOOL_FIXTURES["queries_get_query_operator_stats"][1] == "rejected"
    for text in (
        '{"status": "error", "error": "100037 (22000): Invalid UUID: e2e-missing-query-id"}',
        '{"status": "error", "error": "Invalid query ID e2e-missing-query-id"}',
        '{"status": "error", "error": "Invalid value [e2e-missing-query-id] for function \'get_query_operator_stats\'"}',
    ):
        mock_srv.call_tool.return_value = CallToolResult(
            content=[TextContent(type="text", text=text)],
            is_error=False,
        )
        status, is_err, err = await dispatch_tool_call(
            mock_srv, "queries_get_query_operator_stats", is_destructive=False
        )
        assert status == "PASS"
        assert not is_err
        assert err is None

    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text='{"status": "error", "error": "warehouse suspended"}')],
        is_error=False,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "queries_get_query_operator_stats", is_destructive=False)
    assert status == "FAIL"
    assert is_err
    assert "Expected a safe rejection" in (err or "")


@pytest.mark.asyncio
async def test_server_tools_with_mocked_cursor(mock_snowflake_client: SnowflakeClient) -> None:
    """Exercise create_server() tools through connection and cursor objects."""
    srv = create_server(client=mock_snowflake_client)
    mock_cursor = mock_snowflake_client.get_connection().cursor.return_value

    # 1. Non-destructive query tool exercises cursor execution
    status, is_err, err = await dispatch_tool_call(srv, "queries_query", is_destructive=False)
    assert status == "PASS"
    assert not is_err
    assert err is None
    mock_cursor.execute.assert_called()

    # 2. Destructive drop tool safety gate (confirm=False) is verified as PASS without cursor execution
    mock_cursor.execute.reset_mock()
    status, is_err, err = await dispatch_tool_call(srv, "databases_drop_database", is_destructive=True)
    assert status == "PASS"
    assert is_err
    assert err is None
    mock_cursor.execute.assert_not_called()

    # 3. Read-only rejection on mutation is treated as a valid safety check
    mock_snowflake_client.config.read_only = True
    status, is_err, err = await dispatch_tool_call(srv, "databases_drop_database", is_destructive=True)
    assert status == "PASS"
    assert is_err
    assert err is None
    mock_cursor.execute.assert_not_called()


def _json_result(payload: Any, *, is_error: bool = False) -> CallToolResult:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)


def _listing_server(databases: Any, schemas: dict[str, list[dict[str, Any]]]) -> AsyncMock:
    """Fake server for discovery: databases_list_databases and schemas_list_schemas only."""
    srv = AsyncMock()

    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        if name == "databases_list_databases":
            if isinstance(databases, CallToolResult):
                return databases
            return _json_result({"status": "success", "databases": databases})
        if name == "schemas_list_schemas":
            return _json_result({"status": "success", "schemas": schemas.get(arguments["database"], [])})
        raise AssertionError(f"unexpected tool {name}")

    srv.call_tool.side_effect = call_tool
    return srv


_NS = ('"Db ""1"""', '"PUBLIC"')


@pytest.mark.asyncio
async def test_discover_namespace_picks_a_readable_database_and_schema() -> None:
    srv = _listing_server(
        [
            {"name": "A_IMPORTED", "kind": "IMPORTED DATABASE"},
            {"name": "A_PERSONAL", "kind": "PERSONAL DATABASE"},
            {"name": "AAA_DROPPED", "kind": "STANDARD", "dropped_on": "2026-10-01 00:00:00"},
            {"name": 'Db "1"', "kind": "STANDARD"},
            {"name": "SNOWFLAKE", "kind": "APPLICATION"},
            {"name": "ZZZ", "kind": "STANDARD"},
        ],
        {
            '"Db ""1"""': [{"name": "INFORMATION_SCHEMA"}, {"name": "ANALYTICS"}, {"name": "PUBLIC"}],
            '"ZZZ"': [{"name": "PUBLIC"}],
        },
    )
    assert await discover_namespace(srv) == _NS
    calls = [(c.args[0], c.args[1]) for c in srv.call_tool.await_args_list]
    assert calls == [("databases_list_databases", {}), ("schemas_list_schemas", {"database": '"Db ""1"""'})]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no_standard_database", "no_usable_schema", "databases_is_error"])
async def test_discover_namespace_returns_none(case: str) -> None:
    if case == "no_standard_database":
        srv = _listing_server(
            [{"name": "SNOWFLAKE", "kind": "APPLICATION"}, {"name": "SHARED", "kind": "IMPORTED DATABASE"}], {}
        )
        assert await discover_namespace(srv) is None
        assert [c.args[0] for c in srv.call_tool.await_args_list] == ["databases_list_databases"]
    elif case == "no_usable_schema":
        names = [f"DB_{i:02d}" for i in range(12)]
        srv = _listing_server(
            [{"name": n, "kind": "STANDARD"} for n in names],
            {quote_name(n): [{"name": "INFORMATION_SCHEMA"}, {"name": "OLD", "dropped_on": "x"}] for n in names},
        )
        assert await discover_namespace(srv) is None
        tried = [c.args[1]["database"] for c in srv.call_tool.await_args_list if c.args[0] == "schemas_list_schemas"]
        assert tried == [quote_name(n) for n in names[:10]]
    else:
        srv = _listing_server(_json_result({"status": "error", "error": "denied"}, is_error=True), {})
        assert await discover_namespace(srv) is None


@pytest.mark.asyncio
async def test_namespaced_fixtures_send_fully_qualified_sql() -> None:
    snapshot = copy.deepcopy(TOOL_FIXTURES)
    fixtures = namespaced_fixtures(_NS)
    assert TOOL_FIXTURES == snapshot
    assert len(_NAMESPACED) == 25
    assert sum(form == "args" for _, form in _NAMESPACED.values()) == 9

    client = SnowflakeClient(config=SnowflakeConfig(account="mock_account", user="mock_user"))
    client.execute_query = MagicMock(return_value={"data": [{"name": "ID", "type": "NUMBER"}]})  # type: ignore[method-assign]
    srv = create_server(client=client)
    for tool_name, (argument, form) in _NAMESPACED.items():
        assert TOOL_FIXTURES[tool_name][1] in ("rejected", "cortex") or tool_name in _EMPTY_SUCCESS_TOOLS, tool_name
        arguments = fixtures[tool_name][0]
        if form == "args":
            assert "." not in arguments[argument], tool_name
            assert (arguments["database"], arguments["schema_name"]) == _NS
        if TOOL_FIXTURES[tool_name][1] == "cortex":
            # cortex_suggest_search is a REST call, not SQL; test_suggest_search_gets_namespace_args covers it.
            continue
        client.execute_query.reset_mock()
        try:
            await srv.call_tool(tool_name, arguments)
        except Exception:
            pass
        sqls = [c.args[0] for c in client.execute_query.call_args_list]
        assert sqls, tool_name
        if tool_name == "tags_describe_tag":
            # SHOW TAGS takes the scope as IN SCHEMA <database>.<schema>, not as a name prefix.
            assert sqls == [_TAG_PROBE_SQL], sqls
            continue
        prefix = '"Db ""1""".' if form == "schema" else '"Db ""1"""."PUBLIC".'
        for sql in sqls:
            assert prefix in sql, (tool_name, sql)
    assert TOOL_FIXTURES == snapshot


_TAG_PROBE_SQL = f"SHOW TAGS LIKE 'E2E_MCP_MISSING_OBJECT' IN SCHEMA {_NS[0]}.{_NS[1]}"


@pytest.mark.asyncio
async def test_describe_tag_probe_is_scoped_to_the_discovered_schema() -> None:
    """With no current database, a bare tag_name runs SHOW TAGS outside any schema, so the probe
    passes the discovered database and schema and SHOW TAGS is scoped IN SCHEMA to them."""
    client = SnowflakeClient(config=SnowflakeConfig(account="mock_account", user="mock_user"))
    client.execute_query = MagicMock(return_value={"data": []})  # type: ignore[method-assign]
    srv = create_server(client=client)
    arguments = namespaced_fixtures(_NS)["tags_describe_tag"][0]
    assert arguments == {"tag_name": "E2E_MCP_MISSING_OBJECT", "database": _NS[0], "schema_name": _NS[1]}
    try:
        await srv.call_tool("tags_describe_tag", arguments)
    except Exception:
        pass
    assert [c.args[0] for c in client.execute_query.call_args_list] == [_TAG_PROBE_SQL]
    assert TOOL_FIXTURES["tags_describe_tag"][0] == {"tag_name": "E2E_MCP_MISSING_OBJECT"}


@pytest.mark.asyncio
async def test_namespaced_tools_skip_without_a_namespace() -> None:
    mock_srv = AsyncMock()
    for tool_name in _NAMESPACED:
        result = await dispatch_tool_call(mock_srv, tool_name, False, namespace=None)
        assert result == ("SKIP", False, NO_NAMESPACE_REASON), tool_name
    mock_srv.call_tool.assert_not_awaited()

    mock_srv.call_tool.return_value = _json_result({"status": "success"})
    assert await dispatch_tool_call(mock_srv, "queries_query", False, namespace=None) == ("PASS", False, None)
    mock_srv.call_tool.assert_awaited_once_with("queries_query", {"query": "SELECT 1"})


_NO_WH = (
    "000606 (57P03): No active warehouse selected in the current session.  "
    "Select an active warehouse with the 'use warehouse' command."
)
_SKIP_NO_WH = ("SKIP", True, NO_WAREHOUSE_REASON)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "calls", "state", "configured", "expected"),
    [
        ("i raised", [("queries_query", ToolError(_NO_WH))], WAREHOUSE_NULL, False, _SKIP_NO_WH),
        (
            "ii isError body",
            [("queries_query", _json_result({"status": "error", "error": _NO_WH}, is_error=True))],
            WAREHOUSE_NULL,
            False,
            _SKIP_NO_WH,
        ),
        (
            "iii load history partial",
            [
                (
                    "warehouses_get_warehouse_load_history",
                    _json_result(
                        {
                            "status": "partial",
                            "load_history": [],
                            "warehouse_metadata": [],
                            "load_history_notice": f"Restricted load history access ({_NO_WH}). Returned warehouse metadata.",
                        }
                    ),
                )
            ],
            WAREHOUSE_NULL,
            False,
            _SKIP_NO_WH,
        ),
        ("iv configured but NULL", [("queries_query", ToolError(_NO_WH))], WAREHOUSE_NULL, True, "FAIL"),
        ("v present", [("queries_query", ToolError(_NO_WH))], WAREHOUSE_PRESENT, False, "FAIL"),
        ("vi unknown", [("queries_query", ToolError(_NO_WH))], WAREHOUSE_UNKNOWN, False, "FAIL"),
        (
            "vii confirm never skips",
            [("databases_drop_database", _json_result({"status": "error", "error": _NO_WH}, is_error=True))],
            WAREHOUSE_NULL,
            False,
            "FAIL",
        ),
        (
            "viii fallback failed",
            [
                (
                    "queries_get_query_history",
                    ToolError(
                        f"{_NO_WH}; fallback failed: 001003 (42000): SQL compilation error:\n"
                        "syntax error line 1 at position 5 unexpected 'QUERIES'."
                    ),
                ),
                (
                    "warehouses_get_warehouse_load_history",
                    ToolError(f"{_NO_WH}; fallback failed: 001003 (42000): SQL compilation error: syntax error"),
                ),
            ],
            WAREHOUSE_NULL,
            False,
            "FAIL",
        ),
        ("ix cortex", [("cortex_complete", ToolError(_NO_WH))], WAREHOUSE_NULL, False, _SKIP_NO_WH),
        (
            "x success body echoing the marker",
            [
                (
                    "databases_describe_database",
                    _json_result({"status": "success", "note": "No active warehouse selected"}),
                )
            ],
            WAREHOUSE_NULL,
            False,
            "FAIL",
        ),
    ],
)
async def test_no_warehouse_skip_only_when_unset_and_null(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    calls: list[tuple[str, Any]],
    state: str,
    configured: bool,
    expected: Any,
) -> None:
    monkeypatch.delenv("SNOWFLAKE_MCP_E2E_SKIP_CORTEX", raising=False)
    for tool_name, outcome in calls:
        mock_srv = AsyncMock()
        if isinstance(outcome, BaseException):
            mock_srv.call_tool.side_effect = outcome
        else:
            mock_srv.call_tool.return_value = outcome
        result = await dispatch_tool_call(
            mock_srv, tool_name, False, warehouse_state=state, warehouse_configured=configured
        )
        mock_srv.call_tool.assert_awaited_once()
        if expected == "FAIL":
            assert result[0] == "FAIL", (case, tool_name, result)
            if case.split()[0] == "vii":
                assert "Destructive safety gate bypassed" in (result[2] or "")
            if case.split()[0] == "x":
                assert "Expected a safe rejection" in (result[2] or "")
        else:
            assert result == expected, (case, tool_name, result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "configured", "state"),
    [
        (_json_result({"status": "success", "context": {"WAREHOUSE": "WH", "ROLE": "R"}}), False, WAREHOUSE_PRESENT),
        (_json_result({"status": "success", "context": {"WAREHOUSE": None, "ROLE": "R"}}), False, WAREHOUSE_NULL),
        (_json_result({"status": "success", "context": {}}), False, WAREHOUSE_UNKNOWN),
        (_json_result({"status": "success", "context": {"ROLE": "R"}}), False, WAREHOUSE_UNKNOWN),
        (ToolError('{"status": "error", "error": "390114: token expired"}'), False, WAREHOUSE_UNKNOWN),
        (ToolError(f'{{"status": "error", "error": "{_NO_WH}"}}'), False, WAREHOUSE_NULL),
        (ToolError(f'{{"status": "error", "error": "{_NO_WH}"}}'), True, WAREHOUSE_UNKNOWN),
        # An isError body from the context call is read like a raised error.
        (_json_result({"status": "error", "error": _NO_WH}, is_error=True), False, WAREHOUSE_NULL),
        (_json_result({"status": "error", "error": _NO_WH}, is_error=True), True, WAREHOUSE_UNKNOWN),
        # A joined "fallback failed" error carries a second failure, so it never reads as NULL.
        (
            _json_result({"status": "error", "error": f"{_NO_WH}; fallback failed: syntax error"}, is_error=True),
            False,
            WAREHOUSE_UNKNOWN,
        ),
    ],
)
async def test_warehouse_state_reading(outcome: Any, configured: bool, state: str) -> None:
    ctx_srv = AsyncMock()
    if isinstance(outcome, BaseException):
        ctx_srv.call_tool.side_effect = outcome
    else:
        ctx_srv.call_tool.return_value = outcome
    got, _role = await read_warehouse_state(ctx_srv, configured)
    assert got == state
    ctx_srv.call_tool.assert_awaited_once_with("governance_get_current_context", {})

    # A query that then hits the no-warehouse error skips only for NULL with no configured warehouse.
    query_srv = AsyncMock()
    query_srv.call_tool.side_effect = ToolError(_NO_WH)
    result = await dispatch_tool_call(
        query_srv, "queries_query", False, warehouse_state=got, warehouse_configured=configured
    )
    if got == WAREHOUSE_NULL and not configured:
        assert result == _SKIP_NO_WH
    else:
        assert result[0] == "FAIL"


@pytest.mark.asyncio
async def test_live_log_lines_keep_role_apart_and_never_log_account_or_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_USER", "E2E_FAKE_USER")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "E2E_FAKE_ACCT")
    ctx_srv = AsyncMock()
    ctx_srv.call_tool.return_value = _json_result(
        {
            "status": "success",
            "context": {
                "USER": "E2E_FAKE_USER",
                "ROLE": "E2E_FAKE_ROLE",
                "WAREHOUSE": None,
                "ACCOUNT": "E2E_FAKE_ACCT",
            },
        }
    )
    state, role = await read_warehouse_state(ctx_srv, False)
    fakes = ("E2E_FAKE_USER", "E2E_FAKE_ROLE", "E2E_FAKE_ACCT")

    lines = live_log_lines(('"Db 1"', '"PUBLIC"'), role, state, False)
    assert len(lines) == 3
    assert '"Db 1"' in lines[0] and '"PUBLIC"' in lines[0]
    assert not any(fake in lines[0] for fake in fakes)
    assert [i for i, line in enumerate(lines) if "E2E_FAKE_ROLE" in line] == [1]
    assert not any("E2E_FAKE_USER" in line or "E2E_FAKE_ACCT" in line for line in lines)

    lines = live_log_lines(None, role, state, False)
    assert len(lines) == 3
    assert lines[0] == f"live namespace: none ({NO_NAMESPACE_REASON})"
    assert [i for i, line in enumerate(lines) if "E2E_FAKE_ROLE" in line] == [1]
    assert not any("E2E_FAKE_USER" in line or "E2E_FAKE_ACCT" in line for line in lines)


def test_live_log_lines_mask_user_in_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """A database or schema name that starts with the login or user object name is masked."""
    monkeypatch.setenv("SNOWFLAKE_USER", "E2E_FAKE_USER")
    monkeypatch.setattr(sys.modules[__name__], "_RESOLVED_LIVE_NAMES", {"E2E_FAKE_OBJECT"})

    lines = live_log_lines(('"E2E_FAKE_USER_DB"', '"PUBLIC"'), "E2E_FAKE_ROLE", WAREHOUSE_PRESENT, True, True)
    assert lines[0] == 'live namespace: database="[redacted user]_DB" schema="PUBLIC"'
    assert not any("e2e_fake_user" in line.lower() for line in lines)

    lines = live_log_lines(('"e2e_fake_user_db"', '"E2E_FAKE_OBJECT_SCHEMA"'), None, WAREHOUSE_PRESENT, True)
    assert lines[0] == 'live namespace: database="[redacted user]_db" schema="[redacted user]_SCHEMA"'
    assert not any("e2e_fake_user" in line.lower() or "e2e_fake_object" in line.lower() for line in lines)


@pytest.mark.asyncio
async def test_fixture_catalog_matches_server_tools(mock_snowflake_client: SnowflakeClient) -> None:
    """Every exposed tool has a fixture or an explicit skip reason. Offline only."""
    srv = create_server(client=mock_snowflake_client)
    tools = await srv.list_tools()
    names = {tool.name for tool in tools}
    assert names == set(TOOL_FIXTURES) | set(EXPLICIT_TOOL_SKIPS)
    for reason in EXPLICIT_TOOL_SKIPS.values():
        assert reason.strip()
    counts = {"success": 0, "rejected": 0, "confirm": 0, "cortex": 0}
    for name, (arguments, expect) in TOOL_FIXTURES.items():
        assert expect in counts, name
        counts[expect] += 1
        if "confirm" in arguments:
            assert arguments["confirm"] is False
        if expect == "cortex":
            assert CORTEX_SKIP_REASON.strip()
    assert counts == {"success": 66, "rejected": 44, "confirm": 119, "cortex": 16}
    assert TOOL_FIXTURES["queries_query"] == ({"query": "SELECT 1"}, "success")
    # A tag name with no match is the not-found error, not an empty success.
    assert TOOL_FIXTURES["tags_describe_tag"] == ({"tag_name": "E2E_MCP_MISSING_OBJECT"}, "rejected")
    assert "tags_describe_tag" not in _EMPTY_SUCCESS_TOOLS
    assert TOOL_FIXTURES["databases_drop_database"] == (
        {"name": "e2e_probe_db", "confirm": False},
        "confirm",
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_all_discovered_tools_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dynamically discover and exercise registered tools against live endpoints."""
    # Enforce read-only mode to prevent accidental mutations during live discovery probe
    monkeypatch.setenv("SNOWFLAKE_MCP_READONLY", "1")
    cfg = SnowflakeConfig.from_env_or_config()
    warehouse_configured = bool((cfg.warehouse or "").strip())
    srv = create_server(config=cfg)
    tools = await srv.list_tools()
    assert len(tools) > 0, "No tools registered in MCPServer"

    warehouse_state, role = await read_warehouse_state(srv, warehouse_configured)
    await resolve_live_user_mask(srv)
    # The database where scripts/live_fixtures.sql ran is the namespace for every qualified probe;
    # without the fixture objects, the first readable database and schema is used as before.
    fixture_namespace = await discover_fixture_namespace(srv)
    fixture_objects_present = fixture_namespace is not None
    namespace = fixture_namespace or await discover_namespace(srv)
    for line in live_log_lines(namespace, role, warehouse_state, warehouse_configured, fixture_objects_present):
        print(line)
    fixtures = namespaced_fixtures(namespace) if namespace else TOOL_FIXTURES

    results: list[dict[str, Any]] = []

    for tool in tools:
        t0 = time.perf_counter()
        tool_name = tool.name
        annotations = tool.annotations
        is_destructive = getattr(annotations, "destructive_hint", False)

        status, is_err, err_msg = await dispatch_tool_call(
            srv,
            tool_name,
            is_destructive,
            fixtures=fixtures,
            namespace=namespace,
            warehouse_state=warehouse_state,
            warehouse_configured=warehouse_configured,
            fixture_objects_present=fixture_objects_present,
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        res_entry: dict[str, Any] = {
            "tool": tool_name,
            "status": status,
            "latency_ms": latency_ms,
            "is_error": is_err,
        }
        if err_msg:
            res_entry["error"] = err_msg
        results.append(res_entry)

    failed = [r for r in results if r["status"] == "FAIL"]
    assert not failed, f"E2E live tool verification failed for: {failed}"


_REST_AUTH_TEXT = "Snowflake REST tools need key-pair, PAT or OAuth-token authentication; use one."
_SUGGEST_DISABLED_TEXT = (
    'Snowflake REST request failed (403): {"code": "399104", "message": "Cortex Search Suggest API is not enabled"}'
)
_OTHER_403_TEXT = 'Snowflake REST request failed (403): {"code": "390186", "message": "Forbidden"}'


@pytest.mark.asyncio
async def test_rest_tools_skip_without_rest_auth() -> None:
    srv = AsyncMock()
    srv.call_tool.side_effect = ToolError(_REST_AUTH_TEXT)
    assert await dispatch_tool_call(srv, "cortex_list_agents", False) == ("SKIP", True, REST_AUTH_SKIP_REASON)
    srv.call_tool.side_effect = None
    srv.call_tool.return_value = CallToolResult(content=[TextContent(type="text", text=_REST_AUTH_TEXT)], is_error=True)
    assert await dispatch_tool_call(srv, "cortex_list_agents", False) == ("SKIP", True, REST_AUTH_SKIP_REASON)
    # A confirm probe must still hit its gate, so the marker never skips it.
    status, _is_error, _message = await dispatch_tool_call(srv, "databases_drop_database", True)
    assert status == "FAIL"


@pytest.mark.asyncio
async def test_suggest_search_skips_when_suggest_api_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live, a disabled Suggest API answers HTTP 403 code 399104; that is the Cortex skip, not a FAIL."""
    monkeypatch.delenv("SNOWFLAKE_MCP_E2E_SKIP_CORTEX", raising=False)
    assert TOOL_FIXTURES["cortex_suggest_search"][1] == "cortex"
    skip = ("SKIP", True, CORTEX_SKIP_REASON)
    srv = AsyncMock()
    srv.call_tool.side_effect = ToolError(_SUGGEST_DISABLED_TEXT)
    assert await dispatch_tool_call(srv, "cortex_suggest_search", False) == skip
    srv.call_tool.side_effect = None
    srv.call_tool.return_value = _json_result({"status": "error", "error": _SUGGEST_DISABLED_TEXT}, is_error=True)
    assert await dispatch_tool_call(srv, "cortex_suggest_search", False) == skip
    # Any other 403 still fails.
    srv.call_tool.return_value = _json_result({"status": "error", "error": _OTHER_403_TEXT}, is_error=True)
    assert (await dispatch_tool_call(srv, "cortex_suggest_search", False))[0] == "FAIL"
    srv.call_tool.side_effect = ToolError(_OTHER_403_TEXT)
    assert (await dispatch_tool_call(srv, "cortex_suggest_search", False))[0] == "FAIL"


@pytest.mark.asyncio
async def test_database_scoped_probes_get_the_discovered_database() -> None:
    fixtures = namespaced_fixtures(("DBX", "SCHX"))
    for tool_name, argument in _DATABASE_SCOPED.items():
        assert fixtures[tool_name][0][argument] == "DBX"
        assert argument not in TOOL_FIXTURES[tool_name][0]
        result = await dispatch_tool_call(AsyncMock(), tool_name, False, namespace=None)
        assert result == ("SKIP", False, NO_NAMESPACE_REASON), tool_name


@pytest.mark.asyncio
async def test_grants_to_user_probe_uses_current_user_not_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_USER", "e2e.fake.login@example.com")
    monkeypatch.setattr(sys.modules[__name__], "_RESOLVED_LIVE_NAMES", set())
    srv = AsyncMock()
    srv.call_tool.side_effect = [
        _json_result({"status": "success", "context": {"USER": "E2E_FAKE_OBJECT"}}),
        ValueError("bad identifier E2E_FAKE_OBJECT for e2e.fake.login@example.com"),
    ]
    status, is_error, message = await dispatch_tool_call(srv, "governance_list_grants_to_user", False)
    assert srv.call_tool.await_args_list[1].args == ("governance_list_grants_to_user", {"user_name": "E2E_FAKE_OBJECT"})
    assert (status, is_error) == ("FAIL", True)
    assert message is not None
    assert "E2E_FAKE_OBJECT" not in message and "e2e.fake.login" not in message
    assert "[redacted user]" in message
    assert TOOL_FIXTURES["governance_list_grants_to_user"][0] == {"user_name": ""}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        _json_result({"status": "success", "context": {"ROLE": "E2E_FAKE_ROLE"}}),
        _json_result({"status": "success", "context": {"USER": " "}}),
        _json_result({"status": "error", "error": "denied"}, is_error=True),
        ToolError("context failed"),
    ],
    ids=["no-user-key", "blank-user", "is-error", "raised"],
)
async def test_grants_to_user_skips_when_current_user_unavailable(
    monkeypatch: pytest.MonkeyPatch, outcome: CallToolResult | Exception
) -> None:
    monkeypatch.setenv("SNOWFLAKE_USER", "e2e.fake.login@example.com")
    monkeypatch.setattr(sys.modules[__name__], "_RESOLVED_LIVE_NAMES", set())
    srv = AsyncMock()
    if isinstance(outcome, Exception):
        srv.call_tool.side_effect = outcome
    else:
        srv.call_tool.return_value = outcome
    result = await dispatch_tool_call(srv, "governance_list_grants_to_user", False)
    assert result == ("SKIP", False, "CURRENT_USER() is not available")
    assert [c.args[0] for c in srv.call_tool.await_args_list] == ["governance_get_current_context"]
    assert await resolve_live_user_mask(srv) is None
    assert not _RESOLVED_LIVE_NAMES


@pytest.mark.asyncio
async def test_resolve_live_user_mask_redacts_before_grants_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_USER", "e2e.fake.login@example.com")
    monkeypatch.setattr(sys.modules[__name__], "_RESOLVED_LIVE_NAMES", set())
    srv = AsyncMock()
    srv.call_tool.return_value = _json_result({"status": "success", "context": {"USER": "E2E_FAKE_OBJECT"}})
    assert await resolve_live_user_mask(srv) == "E2E_FAKE_OBJECT"
    assert _RESOLVED_LIVE_NAMES == {"E2E_FAKE_OBJECT"}

    srv.call_tool.side_effect = ValueError("warehouse check failed for E2E_FAKE_OBJECT")
    status, is_error, message = await dispatch_tool_call(srv, "queries_query", False)
    assert (status, is_error) == ("FAIL", True)
    assert message is not None
    assert "E2E_FAKE_OBJECT" not in message
    assert "[redacted user]" in message
    called = [c.args[0] for c in srv.call_tool.await_args_list]
    assert called == ["governance_get_current_context", "queries_query"]


@pytest.mark.asyncio
async def test_grants_probes_skip_when_env_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SNOWFLAKE_ROLE", raising=False)
    monkeypatch.setenv("SNOWFLAKE_USER", " ")
    srv = AsyncMock()
    for tool_name, env_name in _ENV_NAMED_FIXTURES.items():
        assert await dispatch_tool_call(srv, tool_name, False) == ("SKIP", False, f"{env_name} is not set")
    srv.call_tool.assert_not_awaited()


_CODE_BUNDLE_505167 = (
    'Snowflake REST request failed (400): {"code": "505167", "message": "invalid notebook configuration: '
    'query_id is not a valid uuid: invalid uuid length: 22"}'
)


@pytest.mark.asyncio
async def test_get_code_bundle_execution_expects_505167_rejection() -> None:
    """Live, a non-UUID execution id gets HTTP 400 code 505167, which the rejected row passes on."""
    name = "programmability_get_code_bundle_execution"
    assert TOOL_FIXTURES[name][1] == "rejected"
    assert "505167" in _rejection_markers(name)
    mock_srv = AsyncMock()
    mock_srv.call_tool.side_effect = ToolError(_CODE_BUNDLE_505167)
    assert await dispatch_tool_call(mock_srv, name, False) == ("PASS", True, None)
    mock_srv.call_tool.side_effect = None
    mock_srv.call_tool.return_value = _json_result({"status": "error", "error": _CODE_BUNDLE_505167}, is_error=True)
    assert await dispatch_tool_call(mock_srv, name, False) == ("PASS", True, None)
    # Any other error, or a success, is not the expected rejection.
    mock_srv.call_tool.return_value = _json_result({"status": "error", "error": "warehouse suspended"}, is_error=True)
    assert (await dispatch_tool_call(mock_srv, name, False))[0] == "FAIL"
    mock_srv.call_tool.return_value = _json_result({"status": "success", "execution": {}})
    assert (await dispatch_tool_call(mock_srv, name, False))[0] == "FAIL"


_SPCS_ROWS = (
    "compute_services_list_service_endpoints",
    "compute_services_get_service_logs",
    "compute_services_list_images",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", _SPCS_ROWS)
async def test_spcs_rows_expect_rejected(name: str) -> None:
    """Trial accounts do not support Snowpark Container Services, so these probes get the not-found error."""
    assert TOOL_FIXTURES[name][1] == "rejected"
    mock_srv = AsyncMock()
    mock_srv.call_tool.return_value = _json_result(
        {"status": "error", "error": "E2E_MCP_MISSING_OBJECT does not exist or not authorized."}, is_error=True
    )
    assert await dispatch_tool_call(mock_srv, name, False) == ("PASS", True, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denied",
    [
        "SQL access control error: Insufficient privileges to operate on account",
        "003001 (42501): SQL access control error",
    ],
    ids=["insufficient-privileges", "003001"],
)
async def test_show_accounts_privilege_denied_is_success_pass(denied: str) -> None:
    assert TOOL_FIXTURES["governance_list_accounts"] == ({}, "success")
    body = json.dumps({"status": "error", "error": denied})
    mock_srv = AsyncMock()
    for name, expected in (("governance_list_accounts", "PASS"), ("warehouses_list_warehouses", "FAIL")):
        mock_srv.call_tool.side_effect = ToolError(body)
        assert (await dispatch_tool_call(mock_srv, name, False))[0] == expected, name
        mock_srv.call_tool.side_effect = None
        mock_srv.call_tool.return_value = _json_result(body, is_error=True)
        assert (await dispatch_tool_call(mock_srv, name, False))[0] == expected, name

    # With ORGADMIN the call runs and must succeed; any other error still fails.
    mock_srv.call_tool.return_value = _json_result({"status": "success", "accounts": []})
    assert await dispatch_tool_call(mock_srv, "governance_list_accounts", False) == ("PASS", False, None)
    mock_srv.call_tool.return_value = _json_result({"status": "error", "error": "warehouse suspended"}, is_error=True)
    assert (await dispatch_tool_call(mock_srv, "governance_list_accounts", False))[0] == "FAIL"


@pytest.mark.asyncio
async def test_fixture_objects_use_discovered_namespace() -> None:
    snapshot = copy.deepcopy(TOOL_FIXTURES)
    fixtures = namespaced_fixtures(("DBX", "SCHX"))
    assert TOOL_FIXTURES == snapshot
    assert fixtures["governance_list_grants_on_object"] == (
        {"object_type": "TABLE", "object_name": "DBX.SCHX.E2E_MCP_FIXTURE_TABLE"},
        "success",
    )
    assert fixtures["governance_list_grants_to_database_role"] == (
        {"database_role": "DBX.E2E_MCP_FIXTURE_DATABASE_ROLE"},
        "success",
    )
    assert fixtures["stages_get_presigned_url"] == (
        {
            "stage_name": "E2E_MCP_FIXTURE_STAGE",
            "relative_path": "e2e-missing-file.txt",
            "database": "DBX",
            "schema_name": "SCHX",
        },
        "success",
    )
    assert fixtures["tasks_list_task_dependents"] == (
        {"task_name": 'DBX.SCHX."E2E_MCP_FIXTURE_TASK"', "database": "DBX"},
        "success",
    )

    # The SQL names the objects in the discovered (quoted) database and schema.
    client = SnowflakeClient(config=SnowflakeConfig(account="mock_account", user="mock_user"))
    client.execute_query = MagicMock(return_value={"data": []})  # type: ignore[method-assign]
    srv = create_server(client=client)
    quoted = namespaced_fixtures(_NS)
    expected = {
        "governance_list_grants_on_object": ['"Db ""1"""."PUBLIC"."E2E_MCP_FIXTURE_TABLE"'],
        "governance_list_grants_to_database_role": ['"Db ""1"""."E2E_MCP_FIXTURE_DATABASE_ROLE"'],
        "stages_get_presigned_url": ['@"Db ""1"""."PUBLIC"."E2E_MCP_FIXTURE_STAGE"', "'e2e-missing-file.txt'"],
        "tasks_list_task_dependents": [
            '"Db ""1""".INFORMATION_SCHEMA.TASK_DEPENDENTS(',
            'TASK_NAME => \'"Db ""1"""."PUBLIC"."E2E_MCP_FIXTURE_TASK"\'',
        ],
    }
    assert set(expected) == set(_FIXTURE_OBJECTS)
    for tool_name, parts in expected.items():
        client.execute_query.reset_mock()
        await srv.call_tool(tool_name, quoted[tool_name][0])
        sqls = [c.args[0] for c in client.execute_query.call_args_list]
        assert len(sqls) == 1, (tool_name, sqls)
        for part in parts:
            assert part in sqls[0], (tool_name, sqls[0])


@pytest.mark.asyncio
async def test_fixture_object_rows_skip_without_namespace() -> None:
    mock_srv = AsyncMock()
    for tool_name in (*_FIXTURE_OBJECTS, "cortex_suggest_search"):
        result = await dispatch_tool_call(mock_srv, tool_name, False, namespace=None)
        assert result == ("SKIP", False, NO_NAMESPACE_REASON), tool_name
    mock_srv.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_fixture_object_rows_skip_when_objects_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SNOWFLAKE_MCP_E2E_SKIP_CORTEX", raising=False)
    fixtures = namespaced_fixtures(_NS)
    mock_srv = AsyncMock()
    for tool_name in _FIXTURE_OBJECTS:
        result = await dispatch_tool_call(
            mock_srv, tool_name, False, fixtures=fixtures, namespace=_NS, fixture_objects_present=False
        )
        assert result == ("SKIP", False, NO_FIXTURE_OBJECTS_REASON), tool_name
    mock_srv.call_tool.assert_not_awaited()

    # cortex_suggest_search does not depend on the fixture objects.
    mock_srv.call_tool.return_value = _json_result({"status": "success", "suggestions": []})
    result = await dispatch_tool_call(
        mock_srv, "cortex_suggest_search", False, fixtures=fixtures, namespace=_NS, fixture_objects_present=False
    )
    assert result == ("PASS", False, None)
    mock_srv.call_tool.assert_awaited_once_with("cortex_suggest_search", fixtures["cortex_suggest_search"][0])

    # With the objects present the probes run with the fixture-object arguments.
    for tool_name in _FIXTURE_OBJECTS:
        result = await dispatch_tool_call(
            mock_srv, tool_name, False, fixtures=fixtures, namespace=_NS, fixture_objects_present=True
        )
        assert result == ("PASS", False, None), tool_name
        mock_srv.call_tool.assert_awaited_with(tool_name, fixtures[tool_name][0])


def test_suggest_search_gets_namespace_args() -> None:
    assert TOOL_FIXTURES["cortex_suggest_search"] == (
        {"service_name": "E2E_MCP_MISSING_OBJECT", "query": "SELECT 1"},
        "cortex",
    )
    assert namespaced_fixtures(_NS)["cortex_suggest_search"] == (
        {"service_name": "E2E_MCP_MISSING_OBJECT", "query": "SELECT 1", "database": _NS[0], "schema_name": _NS[1]},
        "cortex",
    )


_ALL_SCHEMA_FIXTURES = frozenset({_FIXTURE_TABLE, _FIXTURE_STAGE, _FIXTURE_TASK})
_LISTING_KEYS = {"tables_list_tables": "tables", "stages_list_stages": "stages", "tasks_list_tasks": "tasks"}


def _fixture_server(
    databases: Any,
    roles: dict[str, list[str]],
    schemas: dict[str, list[str]],
    objects: dict[tuple[str, str], frozenset[str]],
) -> AsyncMock:
    """Fake server for fixture discovery: database, database-role, schema, table, stage and task lists."""
    srv = AsyncMock()

    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        if name == "databases_list_databases":
            if isinstance(databases, CallToolResult):
                return databases
            return _json_result({"status": "success", "databases": databases})
        if name == "governance_list_database_roles":
            rows = [{"name": n} for n in roles.get(arguments["database"], [])]
            return _json_result({"status": "success", "database_roles": rows})
        if name == "schemas_list_schemas":
            rows = [{"name": n} for n in schemas.get(arguments["database"], [])]
            return _json_result({"status": "success", "schemas": rows})
        key = _LISTING_KEYS[name]
        present = objects.get((arguments["database"], arguments["schema_name"]), frozenset())
        rows = [{"name": arguments["pattern"]}] if arguments["pattern"] in present else []
        return _json_result({"status": "success", key: rows})

    srv.call_tool.side_effect = call_tool
    return srv


_FIXTURE_DBS = [f"DB_{i:02d}" for i in range(12)]
_FIXTURE_DB = quote_name("DB_11")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("objects", "expected"),
    [
        (
            {
                (_FIXTURE_DB, '"ANALYTICS"'): _ALL_SCHEMA_FIXTURES,
                (_FIXTURE_DB, '"PUBLIC"'): _ALL_SCHEMA_FIXTURES,
            },
            (_FIXTURE_DB, '"PUBLIC"'),
        ),
        (
            {
                (_FIXTURE_DB, '"ANALYTICS"'): _ALL_SCHEMA_FIXTURES,
                (_FIXTURE_DB, '"PUBLIC"'): frozenset({_FIXTURE_TABLE, _FIXTURE_STAGE}),
            },
            (_FIXTURE_DB, '"ANALYTICS"'),
        ),
        ({(_FIXTURE_DB, '"PUBLIC"'): frozenset({_FIXTURE_STAGE, _FIXTURE_TASK})}, None),
    ],
    ids=["public-preferred", "other-schema", "objects-incomplete"],
)
async def test_discover_fixture_namespace_finds_the_database_where_the_script_ran(
    objects: dict[tuple[str, str], frozenset[str]], expected: tuple[str, str] | None
) -> None:
    """Every STANDARD database is searched, past the first 10; the role prunes databases first."""
    srv = _fixture_server(
        [{"name": n, "kind": "STANDARD"} for n in _FIXTURE_DBS] + [{"name": "SHARED", "kind": "IMPORTED DATABASE"}],
        {quote_name("DB_03"): ["OTHER_ROLE"], _FIXTURE_DB: [_FIXTURE_DATABASE_ROLE]},
        {_FIXTURE_DB: ["ANALYTICS", "INFORMATION_SCHEMA", "PUBLIC"]},
        objects,
    )
    assert await discover_fixture_namespace(srv) == expected
    calls = [(c.args[0], c.args[1].get("database")) for c in srv.call_tool.await_args_list]
    role_lookups = [database for name, database in calls if name == "governance_list_database_roles"]
    assert role_lookups == [quote_name(n) for n in _FIXTURE_DBS]
    assert [database for name, database in calls if name == "schemas_list_schemas"] == [_FIXTURE_DB]
    assert all(database == _FIXTURE_DB for name, database in calls if name in _LISTING_KEYS)


@pytest.mark.asyncio
async def test_discover_fixture_namespace_returns_none_when_listing_fails() -> None:
    srv = _fixture_server(_json_result({"status": "error", "error": "denied"}, is_error=True), {}, {}, {})
    assert await discover_fixture_namespace(srv) is None
    assert [c.args[0] for c in srv.call_tool.await_args_list] == ["databases_list_databases"]


def test_live_log_lines_report_fixture_objects() -> None:
    present = live_log_lines(_NS, "E2E_FAKE_ROLE", WAREHOUSE_PRESENT, True, True)
    assert present[3] == "live fixtures: present"
    absent = live_log_lines(_NS, "E2E_FAKE_ROLE", WAREHOUSE_PRESENT, True, False)
    assert absent[3] == f"live fixtures: absent ({NO_FIXTURE_OBJECTS_REASON})"
    assert len(present) == len(absent) == 4
    assert len(live_log_lines(_NS, "E2E_FAKE_ROLE", WAREHOUSE_PRESENT, True)) == 3

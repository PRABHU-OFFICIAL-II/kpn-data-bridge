"""
Builds database connections from a customer profile's connections.json + .env file.
No credentials ever appear outside this module.

Supported connection types:
  teradata    — native Teradata ODBC driver (requires driver installed on OS)
  teradatasql — pure-Python Teradata driver (pip install teradatasql, no OS driver needed)
  sqlserver   — Microsoft SQL Server via ODBC Driver 17 for SQL Server (username/password)
  fabricsql   — Microsoft Fabric via Service Principal (azure-identity token, no ODBC 17 needed)
"""

import json
import os
from pathlib import Path

import pyodbc
from dotenv import load_dotenv


def _load_profile(profile_dir: Path) -> dict:
    env_file = profile_dir / ".env"
    if not env_file.exists():
        raise FileNotFoundError(
            f"No .env file found at {env_file}\n"
            f"Copy .env.example to {env_file} and fill in your credentials."
        )
    load_dotenv(env_file, override=True)

    connections_file = profile_dir / "connections.json"
    if not connections_file.exists():
        raise FileNotFoundError(f"No connections.json found at {connections_file}")

    with open(connections_file) as f:
        return json.load(f)


def _resolve(env_key: str, required: bool = True) -> str:
    value = os.environ.get(env_key, "")
    if required and not value:
        raise EnvironmentError(
            f"Required environment variable '{env_key}' is not set or empty.\n"
            f"Check your profile's .env file."
        )
    return value


# ── Connection builders ───────────────────────────────────────────────────────

def _detect_teradata_driver() -> str | None:
    """Returns the first available Teradata ODBC driver name, or None."""
    available = pyodbc.drivers()
    candidates = [
        "Teradata Database ODBC Driver 20.00",
        "Teradata Database ODBC Driver 17.00",
        "Teradata Database ODBC Driver 16.20",
        "Teradata",
    ]
    for name in candidates:
        if name in available:
            return name
    # Fallback: any driver with 'Teradata' in the name
    return next((d for d in available if "Teradata" in d), None)


def _build_teradata_odbc(cfg: dict) -> pyodbc.Connection:
    """
    Teradata via native ODBC driver.
    Auto-detects the installed Teradata driver name (e.g. 'Teradata Database ODBC Driver 20.00').
    Falls back to teradatasql pure-Python driver if no ODBC driver is found.
    """
    driver = _detect_teradata_driver()

    if not driver:
        import logging
        logging.getLogger(__name__).warning(
            "No Teradata ODBC driver found — falling back to teradatasql."
        )
        return _build_teradatasql(cfg)

    host     = _resolve(cfg["host_env"])
    user     = _resolve(cfg["user_env"])
    password = _resolve(cfg["pass_env"])
    database = _resolve(cfg.get("database_env", ""), required=False)

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"DBCName={host};"
        f"UID={user};"
        f"PWD={password};"
    )
    if database:
        conn_str += f"DATABASE={database};"

    import logging
    logging.getLogger(__name__).info("Using Teradata ODBC driver: %s", driver)
    return pyodbc.connect(conn_str, autocommit=True)


def _build_teradatasql(cfg: dict):
    """
    Teradata via teradatasql pure-Python driver.
    No OS-level ODBC driver installation required.
    Install with: pip install teradatasql
    """
    try:
        import teradatasql
    except ImportError:
        raise ImportError(
            "teradatasql is not installed.\n"
            "Run: pip install teradatasql"
        )

    host     = _resolve(cfg["host_env"])
    user     = _resolve(cfg["user_env"])
    password = _resolve(cfg["pass_env"])
    database = _resolve(cfg.get("database_env", ""), required=False)

    params = {
        "host":     host,
        "user":     user,
        "password": password,
    }
    if database:
        params["database"] = database

    return teradatasql.connect(**params)


def _build_sqlserver(cfg: dict) -> pyodbc.Connection:
    """Microsoft SQL Server via ODBC Driver 17 for SQL Server (username + password)."""
    server   = _resolve(cfg["server_env"])
    database = _resolve(cfg["database_env"])
    user     = _resolve(cfg["user_env"])
    password = _resolve(cfg["pass_env"])
    auth     = _resolve(cfg.get("auth_env", ""), required=False) or "ActiveDirectoryPassword"

    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"Server={server};"
        f"Database={database};"
        f"UID={user};"
        f"PWD={password};"
        f"Authentication={auth};"
    )

    return pyodbc.connect(conn_str, autocommit=True)


def _build_fabricsql(cfg: dict) -> pyodbc.Connection:
    """
    Microsoft Fabric Data Warehouse via Service Principal authentication.

    Strategy:
      1. If ODBC Driver 17 or 18 for SQL Server is installed, use it directly with
         Authentication=ActiveDirectoryServicePrincipal — the cleanest approach.
      2. Otherwise fall back to acquiring an Azure AD token via azure-identity and
         injecting it into the basic 'SQL Server' ODBC driver using the
         SQL_COPT_SS_ACCESS_TOKEN attribute (1256). This avoids needing the
         modern driver installed on the OS.
    """
    try:
        from azure.identity import ClientSecretCredential
    except ImportError:
        raise ImportError(
            "azure-identity is not installed.\n"
            "Run: pip install azure-identity"
        )

    import struct

    server        = _resolve(cfg["server_env"])
    database      = _resolve(cfg["database_env"])
    tenant_id     = _resolve(cfg["tenant_id_env"])
    client_id     = _resolve(cfg["client_id_env"])
    client_secret = _resolve(cfg["client_secret_env"])

    # Check for modern ODBC driver (preferred)
    available_drivers = pyodbc.drivers()
    modern_driver = next(
        (d for d in ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"]
         if d in available_drivers),
        None,
    )

    if modern_driver:
        # Direct Service Principal auth — simplest and most reliable
        conn_str = (
            f"DRIVER={{{modern_driver}}};"
            f"Server={server};"
            f"Database={database};"
            f"UID={client_id};"
            f"PWD={client_secret};"
            f"Authentication=ActiveDirectoryServicePrincipal;"
        )
        return pyodbc.connect(conn_str, autocommit=True)

    # Fallback: token injection via azure-identity
    # Works with the basic built-in 'SQL Server' ODBC driver on Windows
    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    token       = credential.get_token("https://database.windows.net/.default")
    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    conn_str = (
        f"DRIVER={{SQL Server}};"
        f"Server={server};"
        f"Database={database};"
    )
    # SQL_COPT_SS_ACCESS_TOKEN = 1256
    return pyodbc.connect(conn_str, attrs_before={1256: token_struct}, autocommit=True)


_BUILDERS = {
    "teradata":    _build_teradata_odbc,
    "teradatasql": _build_teradatasql,
    "sqlserver":   _build_sqlserver,
    "fabricsql":   _build_fabricsql,
}


# ── Public API ────────────────────────────────────────────────────────────────

def get_connection(connection_name: str, profile_dir: Path):
    """
    Returns an open database connection for the named connection in the profile.

    Usage:
        conn = get_connection("odbc_teradata", Path("profiles/kpn"))
    """
    profile = _load_profile(profile_dir)

    if connection_name not in profile:
        available = ", ".join(profile.keys())
        raise KeyError(
            f"Connection '{connection_name}' not found in connections.json.\n"
            f"Available connections: {available}"
        )

    cfg      = profile[connection_name]
    conn_type = cfg.get("type")

    if conn_type not in _BUILDERS:
        raise ValueError(
            f"Unsupported connection type '{conn_type}' for '{connection_name}'.\n"
            f"Supported types: {', '.join(_BUILDERS.keys())}"
        )

    return _BUILDERS[conn_type](cfg)


def get_all_connections(profile_dir: Path) -> dict:
    """Returns open connections for all entries in connections.json."""
    profile = _load_profile(profile_dir)
    return {name: get_connection(name, profile_dir) for name in profile}

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Callable

from coldtrack.infrastructure import apply_managed_policy, initialize, sync_managed_alerts
from coldtrack.maintenance_infrastructure import initialize_maintenance


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATABASE_PATH = os.environ.get("COLDTRACK_DB_PATH", os.path.join(DATA_DIR, "coldtrack.db"))


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DATABASE_PATH)), exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_column(table_name: str, column_name: str, column_def: str) -> None:
    with _connect() as connection:
        columns = [
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        ]
        if column_name not in columns:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")


def _migrate_legacy_telemetry_table() -> None:
    with _connect() as connection:
        columns = [
            row["name"]
            for row in connection.execute("PRAGMA table_info(telemetry_readings)").fetchall()
        ]

        if not columns:
            return

        if "payload" not in columns:
            if "raw_payload" not in columns:
                connection.execute(
                    "ALTER TABLE telemetry_readings ADD COLUMN raw_payload TEXT NOT NULL DEFAULT '{}'"
                )
            return

        connection.execute("ALTER TABLE telemetry_readings RENAME TO telemetry_readings_legacy")
        connection.execute(
            """
            CREATE TABLE telemetry_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                uptime_ms INTEGER,
                temp_interior REAL,
                temp_condensador REAL,
                temp_succion REAL,
                presion_baja INTEGER,
                presion_alta INTEGER,
                compresor INTEGER,
                fan_solicitado INTEGER,
                fan_pwm INTEGER,
                fan_rpm REAL,
                estado TEXT,
                diagnostico TEXT,
                proteccion_alta INTEGER,
                proteccion_baja INTEGER,
                orden_paro_compresor INTEGER,
                raw_payload TEXT NOT NULL DEFAULT '{}',
                received_at TEXT NOT NULL,
                FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            INSERT INTO telemetry_readings (
                id,
                device_id,
                uptime_ms,
                temp_interior,
                temp_condensador,
                temp_succion,
                presion_baja,
                presion_alta,
                compresor,
                fan_solicitado,
                fan_pwm,
                fan_rpm,
                estado,
                diagnostico,
                proteccion_alta,
                proteccion_baja,
                orden_paro_compresor,
                raw_payload,
                received_at
            )
            SELECT
                id,
                device_id,
                uptime_ms,
                temp_interior,
                temp_condensador,
                temp_succion,
                presion_baja,
                presion_alta,
                compresor,
                fan_solicitado,
                fan_pwm,
                fan_rpm,
                estado,
                diagnostico,
                proteccion_alta,
                proteccion_baja,
                orden_paro_compresor,
                COALESCE(raw_payload, payload),
                received_at
            FROM telemetry_readings_legacy
            """
        )
        connection.execute("DROP TABLE telemetry_readings_legacy")


def init_db() -> None:
    with _connect() as connection:
        initialize(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL UNIQUE,
                name TEXT,
                status TEXT NOT NULL DEFAULT 'offline',
                last_seen TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                uptime_ms INTEGER,
                temp_interior REAL,
                temp_condensador REAL,
                temp_succion REAL,
                presion_baja INTEGER,
                presion_alta INTEGER,
                compresor INTEGER,
                fan_solicitado INTEGER,
                fan_pwm INTEGER,
                fan_rpm REAL,
                estado TEXT,
                diagnostico TEXT,
                proteccion_alta INTEGER,
                proteccion_baja INTEGER,
                orden_paro_compresor INTEGER,
                raw_payload TEXT NOT NULL,
                received_at TEXT NOT NULL,
                FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                title TEXT NOT NULL,
                message TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT,
                FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE SET NULL
            )
            """
        )

        initialize_maintenance(connection)

    _migrate_legacy_telemetry_table()

    _ensure_column("devices", "status", "status TEXT NOT NULL DEFAULT 'offline'")
    _ensure_column("devices", "last_seen", "last_seen TEXT")
    _ensure_column("devices", "latest_payload", "latest_payload TEXT")
    _ensure_column("devices", "name", "name TEXT")
    _ensure_column("devices", "updated_at", "updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z'")
    _ensure_column("telemetry_readings", "uptime_ms", "uptime_ms INTEGER")
    _ensure_column("telemetry_readings", "temp_interior", "temp_interior REAL")
    _ensure_column("telemetry_readings", "temp_condensador", "temp_condensador REAL")
    _ensure_column("telemetry_readings", "temp_succion", "temp_succion REAL")
    _ensure_column("telemetry_readings", "presion_baja", "presion_baja INTEGER")
    _ensure_column("telemetry_readings", "presion_alta", "presion_alta INTEGER")
    _ensure_column("telemetry_readings", "compresor", "compresor INTEGER")
    _ensure_column("telemetry_readings", "fan_solicitado", "fan_solicitado INTEGER")
    _ensure_column("telemetry_readings", "fan_pwm", "fan_pwm INTEGER")
    _ensure_column("telemetry_readings", "fan_rpm", "fan_rpm REAL")
    _ensure_column("telemetry_readings", "proteccion_alta", "proteccion_alta INTEGER")
    _ensure_column("telemetry_readings", "proteccion_baja", "proteccion_baja INTEGER")
    _ensure_column("telemetry_readings", "orden_paro_compresor", "orden_paro_compresor INTEGER")
    _ensure_column("telemetry_readings", "raw_payload", "raw_payload TEXT NOT NULL DEFAULT '{}' ")
    _ensure_column("alerts", "alert_type", "alert_type TEXT NOT NULL DEFAULT 'generic'")
    _ensure_column("alerts", "severity", "severity TEXT NOT NULL DEFAULT 'info'")
    _ensure_column("alerts", "title", "title TEXT NOT NULL DEFAULT 'Alert'")
    _ensure_column("alerts", "is_active", "is_active INTEGER NOT NULL DEFAULT 1")
    _ensure_column("events", "severity", "severity TEXT NOT NULL DEFAULT 'info'")
    _ensure_column("events", "metadata", "metadata TEXT")

    with _connect() as connection:
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_device_time ON telemetry_readings(device_id, received_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(is_active, created_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_device_time ON events(device_id, created_at DESC)"
        )


def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["raw_payload"])
    payload["id"] = row["id"]
    if "deviceId" not in payload and "device_id" in row.keys():
        payload["deviceId"] = row["device_id"]
    return payload


def save_telemetry(
    record: dict[str, Any],
    should_store: Callable[[dict[str, Any], dict[str, Any] | None], bool],
) -> dict[str, Any]:
    device_id = record["deviceId"]
    received_at = record["receivedAt"]
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not apply_managed_policy(connection, record):
            return {**record, "stored": False}
        raw_payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        previous = connection.execute(
            "SELECT latest_payload FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if previous and previous["latest_payload"]:
            previous_record = json.loads(previous["latest_payload"])
            if previous_record["receivedAt"] > received_at:
                return {**record, "stored": False}

        connection.execute(
            """
            INSERT INTO devices(device_id, status, last_seen, updated_at, latest_payload)
            VALUES (?, 'online', ?, datetime('now'), ?)
            ON CONFLICT(device_id) DO UPDATE SET
                status = excluded.status,
                last_seen = excluded.last_seen,
                updated_at = datetime('now'),
                latest_payload = excluded.latest_payload
            """,
            (device_id, received_at, raw_payload),
        )

        if "diagnosticFindings" in record:
            sync_managed_alerts(connection, record)
        else:
            alert_type = record["diagnostico"] if record["state"] == "alert" else None
            connection.execute(
                """UPDATE alerts SET is_active = 0, resolved_at = ?
                   WHERE device_id = ? AND is_active = 1 AND (? IS NULL OR alert_type <> ?)""",
                (received_at, device_id, alert_type, alert_type),
            )
            if alert_type:
                connection.execute(
                    """INSERT INTO alerts(device_id, alert_type, severity, title, created_at)
                       SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS (
                           SELECT 1 FROM alerts WHERE device_id = ? AND alert_type = ? AND is_active = 1
                       )""",
                    (device_id, alert_type, record["severity"], alert_type, received_at, device_id, alert_type),
                )

        last_row = _latest_history_row(connection, device_id)
        if not should_store(record, _row_to_record(last_row) if last_row else None):
            return {**record, "stored": False}

        cursor = connection.execute(
            """
            INSERT INTO telemetry_readings(
                device_id,
                uptime_ms,
                temp_interior,
                temp_condensador,
                temp_succion,
                presion_baja,
                presion_alta,
                compresor,
                fan_solicitado,
                fan_pwm,
                fan_rpm,
                estado,
                diagnostico,
                proteccion_alta,
                proteccion_baja,
                orden_paro_compresor,
                raw_payload,
                received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                record.get("uptimeMs"),
                record.get("tempInterior"),
                record.get("tempCondensador"),
                record.get("tempSuccion"),
                record.get("presionBaja"),
                record.get("presionAlta"),
                record.get("compresor"),
                record.get("fanSolicitado"),
                record.get("fanPWM"),
                record.get("fanRpm"),
                record.get("estado"),
                record.get("diagnostico"),
                record.get("proteccionAlta"),
                record.get("proteccionBaja"),
                record.get("ordenParoCompresor"),
                raw_payload,
                received_at,
            ),
        )

        created_row = connection.execute(
            "SELECT * FROM telemetry_readings WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()

        saved = _row_to_record(created_row)
        connection.execute(
            "UPDATE devices SET latest_payload = ? WHERE device_id = ?",
            (json.dumps(saved, ensure_ascii=False), device_id),
        )
        return {**saved, "stored": True}


def list_telemetry(device_id: str | None = None, limit: int = 50, owner_id: int | None = None) -> list[dict[str, Any]]:
    query = """SELECT t.* FROM telemetry_readings t LEFT JOIN ct_managed_devices m ON m.device_id=t.device_id
               WHERE m.owner_id IS ?"""
    params: list[Any] = [owner_id]
    if device_id:
        query += " AND t.device_id = ?"
        params.append(device_id)
    query += " ORDER BY t.received_at DESC LIMIT ?"
    params.append(limit)

    with _connect() as connection:
        rows = connection.execute(query, params).fetchall()

    return [_row_to_record(row) for row in rows]


def _latest_history_row(connection: sqlite3.Connection, device_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT * FROM telemetry_readings WHERE device_id = ?
           ORDER BY received_at DESC, id DESC LIMIT 1""",
        (device_id,),
    ).fetchone()


def get_latest_by_device(device_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        latest = connection.execute(
            "SELECT latest_payload FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if latest and latest["latest_payload"]:
            return json.loads(latest["latest_payload"])
        row = _latest_history_row(connection, device_id)
        return _row_to_record(row) if row else None


def list_devices(owner_id: int | None = None) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT d.device_id, d.name, d.status, d.last_seen,
                   COALESCE(d.latest_payload, t.raw_payload, '{}') AS latest_payload
            FROM devices d
            LEFT JOIN ct_managed_devices m ON m.device_id=d.device_id
            LEFT JOIN telemetry_readings t
                ON t.id = (
                    SELECT t2.id
                    FROM telemetry_readings t2
                    WHERE t2.device_id = d.device_id
                    ORDER BY t2.received_at DESC
                    LIMIT 1
                )
            WHERE m.owner_id IS ?
            ORDER BY d.device_id ASC
            """, (owner_id,)
        ).fetchall()

    return [
        {
            "deviceId": row["device_id"],
            "name": row["name"],
            "status": row["status"],
            "lastSeen": row["last_seen"],
            "estado": latest.get("estado"),
            "diagnostico": latest.get("diagnostico"),
            "state": latest.get("state"),
            "source": latest.get("source"),
            "receivedAt": latest.get("receivedAt"),
        }
        for row in rows
        for latest in [json.loads(row["latest_payload"])]
    ]


def list_alerts(device_id: str) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """SELECT id, alert_type AS type, severity, message AS description, is_active AS active,
                      created_at AS openedAt, resolved_at AS resolvedAt
               FROM alerts WHERE device_id = ? ORDER BY id DESC LIMIT 50""",
            (device_id,),
        ).fetchall()
        return [{**dict(row), "active": bool(row["active"])} for row in rows]


def add_event(
    device_id: str | None,
    event_type: str,
    severity: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO events(device_id, event_type, severity, message, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (device_id, event_type, severity, message, json.dumps(metadata or {}, ensure_ascii=False)),
        )

        row = connection.execute(
            "SELECT * FROM events WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()

    return {
        "id": row["id"],
        "deviceId": row["device_id"],
        "eventType": row["event_type"],
        "severity": row["severity"],
        "message": row["message"],
        "metadata": json.loads(row["metadata"] or "{}"),
        "createdAt": row["created_at"],
    }


def add_alert(
    device_id: str,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    is_active: bool = True,
) -> dict[str, Any]:
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO alerts(device_id, alert_type, severity, title, message, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (device_id, alert_type, severity, title, message, 1 if is_active else 0),
        )

        row = connection.execute(
            "SELECT * FROM alerts WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()

    return {
        "id": row["id"],
        "deviceId": row["device_id"],
        "alertType": row["alert_type"],
        "severity": row["severity"],
        "title": row["title"],
        "message": row["message"],
        "isActive": bool(row["is_active"]),
        "createdAt": row["created_at"],
        "resolvedAt": row["resolved_at"],
    }


def delete_all_data() -> None:
    init_db()
    with _connect() as connection:
        connection.execute("DELETE FROM events")
        connection.execute("DELETE FROM ct_maintenance_ratings")
        connection.execute("DELETE FROM ct_maintenance_history")
        connection.execute("DELETE FROM ct_maintenance_requests")
        connection.execute("DELETE FROM ct_reviews")
        connection.execute("DELETE FROM ct_managed_devices")
        connection.execute("DELETE FROM alerts")
        connection.execute("DELETE FROM telemetry_readings")
        connection.execute("DELETE FROM devices")


init_db()


__all__ = [
    "DATABASE_PATH",
    "add_alert",
    "add_event",
    "delete_all_data",
    "get_latest_by_device",
    "init_db",
    "list_devices",
    "list_telemetry",
    "save_telemetry",
]

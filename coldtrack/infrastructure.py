import json
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from secrets import token_urlsafe
from time import time

from werkzeug.security import check_password_hash, generate_password_hash

from coldtrack.application import Operations
from coldtrack.domain import CHANNELS, DEFAULT_LIMITS, diagnose, sampling_active


def initialize(connection):
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS ct_attempts (
        action TEXT, identifier TEXT, count INTEGER NOT NULL, expires INTEGER NOT NULL,
        PRIMARY KEY (action, identifier)
    );
    CREATE TABLE IF NOT EXISTS ct_users (
        id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('owner', 'technician')),
        phone TEXT NOT NULL, password_hash TEXT NOT NULL, session_version INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS ct_registrations (
        email TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, phone TEXT NOT NULL,
        password_hash TEXT NOT NULL, code_hash TEXT NOT NULL, expires INTEGER NOT NULL,
        attempts INTEGER NOT NULL, resend_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ct_password_resets (
        user_id INTEGER PRIMARY KEY REFERENCES ct_users(id), code_hash TEXT NOT NULL,
        expires INTEGER NOT NULL, attempts INTEGER NOT NULL, resend_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ct_managed_devices (
        device_id TEXT PRIMARY KEY REFERENCES devices(device_id),
        owner_id INTEGER NOT NULL REFERENCES ct_users(id), token_hash TEXT NOT NULL,
        policy TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ct_devices_owner ON ct_managed_devices(owner_id);
    CREATE TABLE IF NOT EXISTS ct_pairing (
        digest TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES ct_users(id),
        name TEXT NOT NULL, expires INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ct_pairing_expiry ON ct_pairing(expires);
    CREATE TABLE IF NOT EXISTS ct_capture_ack (
        device_id TEXT PRIMARY KEY REFERENCES ct_managed_devices(device_id),
        revision TEXT NOT NULL, applied INTEGER NOT NULL, acknowledged_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ct_notification_preferences (
        device_id TEXT PRIMARY KEY REFERENCES ct_managed_devices(device_id),
        preferences TEXT NOT NULL

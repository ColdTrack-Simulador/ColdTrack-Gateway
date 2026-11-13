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
    );
    CREATE TABLE IF NOT EXISTS ct_contacts (
        owner_id INTEGER REFERENCES ct_users(id), technician_id INTEGER REFERENCES ct_users(id),
        PRIMARY KEY (owner_id, technician_id)
    );
    CREATE TABLE IF NOT EXISTS ct_reviews (
        id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES ct_users(id),
        technician_id INTEGER NOT NULL REFERENCES ct_users(id),
        alert_id INTEGER NOT NULL REFERENCES alerts(id), device_id TEXT NOT NULL,
        description TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE (alert_id, technician_id)
    );
    CREATE TABLE IF NOT EXISTS ct_mail (
        id INTEGER PRIMARY KEY, recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
        expires INTEGER, available INTEGER NOT NULL DEFAULT 0, lease TEXT,
        attempts INTEGER NOT NULL DEFAULT 0, delivered INTEGER NOT NULL DEFAULT 0
    );
    """)
    connection.execute("BEGIN IMMEDIATE")
    columns = {column[1] for column in connection.execute("PRAGMA table_info(ct_users)")}
    if "session_version" not in columns:
        connection.execute("ALTER TABLE ct_users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
    connection.commit()


class WerkzeugPasswords:
    def hash(self, password):
        return generate_password_hash(password)

    def verify(self, digest, password):
        return check_password_hash(digest, password)


class SQLiteRepository:
    def __init__(self, connect):
        self.connect = connect

    @contextmanager
    def transaction(self, write=True):
        with closing(self.connect()) as connection, connection:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield SQLiteData(connection)


class SQLiteData:
    def __init__(self, connection):
        self.connection = connection

    def allow_attempt(self, action, identifier, maximum, seconds):
        now = int(time())
        self.connection.execute("DELETE FROM ct_attempts WHERE expires<=?", (now,))
        row = self.connection.execute(
            "SELECT count FROM ct_attempts WHERE action=? AND identifier=?", (action, identifier)
        ).fetchone()
        if row and row["count"] >= maximum:
            return False
        self.connection.execute(
            """INSERT INTO ct_attempts VALUES (?,?,1,?) ON CONFLICT(action,identifier)
               DO UPDATE SET count=count+1""", (action, identifier, now + seconds)
        )
        return True

    def user_by_email(self, email):
        return self.connection.execute("SELECT * FROM ct_users WHERE email = ?", (email,)).fetchone()

    def user(self, user_id):
        return self.connection.execute("SELECT * FROM ct_users WHERE id = ?", (user_id,)).fetchone()

    def update_profile(self, user_id, name, phone):
        return self.connection.execute(
            """UPDATE ct_users SET name=COALESCE(?, name), phone=COALESCE(?, phone)
               WHERE id=? RETURNING id,email,role,name,phone""",
            (name, phone, user_id),
        ).fetchone()

    def password_reset(self, user_id):
        return self.connection.execute(
            "SELECT code_hash,expires,attempts,resend_at FROM ct_password_resets WHERE user_id=?",
            (user_id,),
        ).fetchone()

    def store_password_reset(self, user_id, digest, now):
        self.connection.execute(
            """INSERT INTO ct_password_resets(user_id,code_hash,expires,attempts,resend_at)
               VALUES (?,?,?,5,?) ON CONFLICT(user_id) DO UPDATE SET
               code_hash=excluded.code_hash, expires=excluded.expires, attempts=5, resend_at=excluded.resend_at""",
            (user_id, digest, now + 600, now + 60),
        )

    def fail_password_reset(self, user_id):
        self.connection.execute(
            "UPDATE ct_password_resets SET attempts=attempts-1 WHERE user_id=?", (user_id,),
        )

    def complete_password_reset(self, user_id, password_hash):
        self.connection.execute(
            "UPDATE ct_users SET password_hash=?, session_version=session_version+1 WHERE id=?",
            (password_hash, user_id),
        )
        self.connection.execute("DELETE FROM ct_password_resets WHERE user_id=?", (user_id,))

    def clear_password_reset_mail(self, email, subject):
        self.connection.execute(
            "UPDATE ct_mail SET delivered=1, body='' WHERE recipient=? AND subject=? AND delivered=0",
            (email, subject),
        )

    def registration(self, email):
        return self.connection.execute("SELECT * FROM ct_registrations WHERE email = ?", (email,)).fetchone()

    def registration_allowed(self, email, now):
        pending = self.registration(email)
        return self.user_by_email(email) is None and (pending is None or pending["resend_at"] <= now)

    def store_registration(self, email, name, role, phone, password_hash, digest, now):
        self.connection.execute(
            """INSERT INTO ct_registrations VALUES (?, ?, ?, ?, ?, ?, ?, 5, ?)
               ON CONFLICT(email) DO UPDATE SET name=excluded.name, role=excluded.role,
               phone=excluded.phone, password_hash=excluded.password_hash, code_hash=excluded.code_hash,
               expires=excluded.expires, attempts=5, resend_at=excluded.resend_at""",
            (email, name, role, phone, password_hash, digest, now + 600, now + 60),
        )
        self.connection.execute(
            """UPDATE ct_mail SET delivered=1, body='' WHERE recipient=? AND subject=?
               AND expires IS NOT NULL AND delivered=0""",
            (email, "Verificá tu cuenta de ColdTrack"),
        )

    def complete_registration(self, email):
        self.connection.execute(
            """INSERT INTO ct_users(email,name,role,phone,password_hash)
               SELECT email,name,role,phone,password_hash FROM ct_registrations WHERE email=?""",
            (email,),
        )
        self.connection.execute("DELETE FROM ct_registrations WHERE email=?", (email,))
        self.connection.execute(
            """UPDATE ct_mail SET delivered=1, body='' WHERE recipient=? AND subject=?
               AND expires IS NOT NULL AND delivered=0""",
            (email, "Verificá tu cuenta de ColdTrack"),
        )

    def fail_registration_attempt(self, email):
        self.connection.execute("UPDATE ct_registrations SET attempts=attempts-1 WHERE email=?", (email,))

    def create_device(self, owner, device_id, name, token_hash, limits):
        policy = {"enabled": True, "channels": list(CHANNELS), "schedule": [], "limits": limits}
        self.connection.execute("INSERT INTO devices(device_id,name) VALUES (?,?)", (device_id, name))
        self.connection.execute(
            "INSERT INTO ct_managed_devices VALUES (?,?,?,?)", (device_id, owner, token_hash, json.dumps(policy))
        )

    def device(self, device_id):
        row = self.connection.execute(
            "SELECT * FROM ct_managed_devices WHERE device_id=?", (device_id,)
        ).fetchone()
        return {**dict(row), "policy": json.loads(row["policy"])} if row else None

    def update_policy(self, device_id, policy):
        self.connection.execute(
            "UPDATE ct_managed_devices SET policy=? WHERE device_id=?", (json.dumps(policy), device_id)
        )

    def save_pairing(self, owner, name, digest, expires):
        self.connection.execute("DELETE FROM ct_pairing WHERE expires<=?", (int(time()),))
        self.connection.execute("INSERT INTO ct_pairing VALUES (?,?,?,?)", (digest, owner, name, expires))

    def claim_pairing(self, digest, owner, name, token_hash, now):
        row = self.connection.execute(
            "SELECT name FROM ct_pairing WHERE digest=? AND owner_id=? AND expires>?", (digest, owner, now)
        ).fetchone()
        if row is None:
            return None
        device_id = "CT-" + token_urlsafe(12)
        label = name or row["name"]
        self.create_device(owner, device_id, label, token_hash, DEFAULT_LIMITS)
        self.connection.execute("DELETE FROM ct_pairing WHERE digest=?", (digest,))
        return {"deviceId": device_id, "name": label}

    def equipment_detail(self, owner, device_id):
        row = self.connection.execute(
            """SELECT d.device_id AS deviceId, d.name, d.last_seen AS lastSeen
               FROM devices d JOIN ct_managed_devices m USING(device_id)
               WHERE d.device_id=? AND m.owner_id=?""", (device_id, owner)
        ).fetchone()
        return dict(row) if row else None

    def equipment_usage(self, device_id, days):
        row = self.connection.execute(
            """WITH readings AS (
                SELECT julianday(received_at) AS observed_at, compresor,
                       lead(julianday(received_at)) OVER w AS next_at,
                       lead(compresor) OVER w AS next_compressor
                FROM telemetry_readings
                WHERE device_id=? AND received_at>=strftime('%Y-%m-%dT%H:%M:%f+00:00','now',?)
                WINDOW w AS (ORDER BY received_at, id)
            ), intervals AS (
                SELECT (next_at-observed_at)*86400 AS seconds, compresor, next_compressor
                FROM readings WHERE compresor>=0 AND next_compressor>=0
            )
            SELECT coalesce(sum(seconds),0), coalesce(sum(CASE WHEN compresor>0 AND next_compressor>0
                THEN seconds ELSE 0 END),0) FROM intervals WHERE seconds BETWEEN 0 AND 360.001""",
            (device_id, f"-{days} days"),
        ).fetchone()
        return row[0], row[1]

    def record_capture_ack(self, device_id, revision, enabled):
        device = self.device(device_id)
        if not device or Operations.policy_response(device["policy"])["revision"] != revision:
            return False
        self.connection.execute(
            """INSERT INTO ct_capture_ack VALUES (?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET
               revision=excluded.revision, applied=excluded.applied, acknowledged_at=excluded.acknowledged_at""",
            (device_id, revision, enabled, datetime.now(timezone.utc).isoformat()),
        )
        return True

    def capture_ack(self, device_id):
        row = self.connection.execute(
            """SELECT revision, applied, acknowledged_at AS acknowledgedAt
               FROM ct_capture_ack WHERE device_id=?""", (device_id,)
        ).fetchone()
        return {**dict(row), "applied": bool(row["applied"])} if row else None

    def notification_preferences(self, device_id):
        row = self.connection.execute(
            "SELECT preferences FROM ct_notification_preferences WHERE device_id=?", (device_id,)
        ).fetchone()
        return json.loads(row[0]) if row else {"emailEnabled": True, "mutedCodes": []}

    def save_notification_preferences(self, device_id, preferences):
        self.connection.execute(
            """INSERT INTO ct_notification_preferences VALUES (?,?) ON CONFLICT(device_id)
               DO UPDATE SET preferences=excluded.preferences""", (device_id, json.dumps(preferences))
        )

    def technicians(self, owner, contacts_only):
        query = """SELECT u.id, u.name, u.email, u.phone FROM ct_users u
                   JOIN ct_contacts c ON c.technician_id=u.id WHERE c.owner_id=? ORDER BY u.name, u.id"""
        if not contacts_only:
            query = "SELECT id, name FROM ct_users WHERE role='technician' ORDER BY name, id LIMIT 100"
        return [dict(row) for row in self.connection.execute(query, (owner,) if contacts_only else ())]

    def save_contact(self, owner, technician):
        self.connection.execute("INSERT OR IGNORE INTO ct_contacts VALUES (?,?)", (owner, technician))

    def saved_technician(self, owner, technician):
        return self.connection.execute(
            "SELECT u.email FROM ct_contacts c JOIN ct_users u ON u.id=c.technician_id WHERE c.owner_id=? AND u.id=?",
            (owner, technician),
        ).fetchone()

    def owned_alert(self, owner, alert_id):
        return self.connection.execute(
            """SELECT a.* FROM alerts a JOIN ct_managed_devices d ON d.device_id=a.device_id
               WHERE a.id=? AND d.owner_id=?""", (alert_id, owner)
        ).fetchone()

    def queue_mail(self, recipient, subject, body, expires=None):
        self.connection.execute(
            "INSERT INTO ct_mail(recipient,subject,body,expires) VALUES (?,?,?,?)",
            (recipient, subject, body, expires),
        )

    def claim_mail(self):
        now = int(time())
        self.connection.execute(
            "UPDATE ct_mail SET delivered=1, body='' WHERE delivered=0 AND expires <= ?", (now,)
        )
        row = self.connection.execute(
            "SELECT * FROM ct_mail WHERE delivered=0 AND available<=? ORDER BY id LIMIT 1", (now,)
        ).fetchone()
        if not row:
            return None
        lease = token_urlsafe(18)
        self.connection.execute(
            "UPDATE ct_mail SET available=?, lease=?, attempts=attempts+1 WHERE id=?",
            (now + 120, lease, row["id"]),
        )
        return {**dict(row), "lease": lease, "attempts": row["attempts"] + 1}

    def complete_mail(self, item, sent):
        current = self.connection.execute(
            "SELECT attempts FROM ct_mail WHERE id=? AND lease=? AND delivered=0 AND available>?",
            (item["id"], item["lease"], int(time())),
        ).fetchone()
        if current is None:
            return False
        if sent:
            self.connection.execute(
                "UPDATE ct_mail SET delivered=1, body='', lease=NULL WHERE id=? AND lease=?",
                (item["id"], item["lease"]),
            )
        else:
            delay = min(3600, 30 * 2 ** min(current["attempts"], 7))
            self.connection.execute(
                "UPDATE ct_mail SET available=?, lease=NULL WHERE id=? AND lease=?",
                (int(time()) + delay, item["id"], item["lease"]),
            )
        return True


def apply_managed_policy(connection, record):
    data = SQLiteData(connection)
    device = data.device(record["deviceId"])
    if device is None:
        return True
    policy = device["policy"]
    now = datetime.fromisoformat(record["receivedAt"])
    if not policy["channels"] or not sampling_active(policy["enabled"], policy["schedule"], now):
        record["samplingActive"] = False
        return False
    for channel in set(CHANNELS) - set(policy["channels"]):
        record.pop(channel, None)
    findings = diagnose(record, policy["limits"])
    record["diagnosticFindings"] = findings
    if findings:
        record.update(state="alert", severity="critical", diagnostico=findings[0]["code"])
    return True


def sync_managed_alerts(connection, record):
    device_id = record["deviceId"]
    received_at = record["receivedAt"]
    findings = {item["code"]: item["description"] for item in record["diagnosticFindings"]}
    if not findings and record["state"] == "alert":
        findings[record["diagnostico"]] = record["diagnostico"]
    active = connection.execute(
        "SELECT id, alert_type FROM alerts WHERE device_id=? AND is_active=1", (device_id,)
    ).fetchall()
    for row in active:
        if row["alert_type"] in findings:
            del findings[row["alert_type"]]
        else:
            connection.execute("UPDATE alerts SET is_active=0, resolved_at=? WHERE id=?", (received_at, row["id"]))
    if not findings:
        return

    owner = connection.execute(
        """SELECT u.email FROM ct_managed_devices d JOIN ct_users u ON u.id=d.owner_id
           WHERE d.device_id=?""", (device_id,)
    ).fetchone()
    data = SQLiteData(connection)
    preferences = data.notification_preferences(device_id)
    for code, description in findings.items():
        connection.execute(
            """INSERT INTO alerts(device_id, alert_type, severity, title, message, created_at)
               VALUES (?,?,?,?,?,?)""", (device_id, code, record["severity"], code, description, received_at),
        )
        if preferences["emailEnabled"] and code not in preferences["mutedCodes"]:
            data.queue_mail(
                owner["email"], f"Alerta ColdTrack · {device_id}",
                f"Equipo {device_id}\n{received_at}\n\n{description}",
            )

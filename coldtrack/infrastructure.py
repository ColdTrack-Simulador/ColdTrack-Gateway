import json
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from secrets import token_urlsafe
from time import time

from werkzeug.security import check_password_hash, generate_password_hash

from coldtrack.domain import CHANNELS, diagnose, sampling_active


def initialize(connection):
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS ct_attempts (
        action TEXT, identifier TEXT, count INTEGER NOT NULL, expires INTEGER NOT NULL,
        PRIMARY KEY (action, identifier)
    );
    CREATE TABLE IF NOT EXISTS ct_users (
        id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('owner', 'technician')),
        phone TEXT NOT NULL, password_hash TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ct_registrations (
        email TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, phone TEXT NOT NULL,
        password_hash TEXT NOT NULL, code_hash TEXT NOT NULL, expires INTEGER NOT NULL,
        attempts INTEGER NOT NULL, resend_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ct_managed_devices (
        device_id TEXT PRIMARY KEY REFERENCES devices(device_id),
        owner_id INTEGER NOT NULL REFERENCES ct_users(id), token_hash TEXT NOT NULL,
        policy TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ct_devices_owner ON ct_managed_devices(owner_id);
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
            "UPDATE ct_mail SET delivered=1, body='' WHERE recipient=? AND expires IS NOT NULL AND delivered=0",
            (email,),
        )

    def complete_registration(self, email):
        self.connection.execute(
            """INSERT INTO ct_users(email,name,role,phone,password_hash)
               SELECT email,name,role,phone,password_hash FROM ct_registrations WHERE email=?""",
            (email,),
        )
        self.connection.execute("DELETE FROM ct_registrations WHERE email=?", (email,))
        self.connection.execute(
            "UPDATE ct_mail SET delivered=1, body='' WHERE recipient=? AND expires IS NOT NULL AND delivered=0",
            (email,),
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

    def create_review(self, owner, technician, alert, email, description, notes):
        now = datetime.now(timezone.utc).isoformat()
        result = self.connection.execute(
            """INSERT INTO ct_reviews(owner_id,technician_id,alert_id,device_id,description,notes,created_at)
               VALUES (?,?,?,?,?,?,?) ON CONFLICT(alert_id,technician_id) DO NOTHING""",
            (owner, technician, alert["id"], alert["device_id"], description, notes, now),
        )
        review = self.connection.execute(
            """SELECT id, alert_id AS alertId, device_id AS deviceId, technician_id AS technicianId,
                      description, notes, created_at AS createdAt
               FROM ct_reviews WHERE alert_id=? AND technician_id=?""", (alert["id"], technician)
        ).fetchone()
        if result.rowcount:
            body = f"Equipo {alert['device_id']}\nSolicitud #{review['id']}\n\n{description}"
            if notes:
                body += f"\n\nNotas del propietario: {notes}"
            self.queue_mail(email, "Nueva solicitud de revisión en ColdTrack", body)
        return dict(review), bool(result.rowcount)

    def reviews(self, actor):
        return [dict(row) for row in self.connection.execute(
            """SELECT id, alert_id AS alertId, device_id AS deviceId, technician_id AS technicianId,
                      description, notes, created_at AS createdAt
               FROM ct_reviews WHERE owner_id=? OR technician_id=? ORDER BY id DESC LIMIT 100""",
            (actor, actor),
        )]

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
    for code, description in findings.items():
        connection.execute(
            """INSERT INTO alerts(device_id, alert_type, severity, title, message, created_at)
               VALUES (?,?,?,?,?,?)""", (device_id, code, record["severity"], code, description, received_at),
        )
        SQLiteData(connection).queue_mail(
            owner["email"], f"Alerta ColdTrack · {device_id}",
            f"Equipo {device_id}\n{received_at}\n\n{description}",
        )

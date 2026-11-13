from contextlib import contextmanager
from datetime import datetime, timezone

from coldtrack.maintenance_domain import REQUESTED


REQUEST_FIELDS = """id, alert_id AS alertId, device_id AS deviceId, technician_id AS technicianId,
                    description, notes, created_at AS createdAt, status, updated_at AS updatedAt"""
PARTICIPANT_COLUMNS = {"owner": "owner_id", "technician": "technician_id"}


def initialize_maintenance(connection):
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS ct_maintenance_requests (
        id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES ct_users(id),
        technician_id INTEGER NOT NULL REFERENCES ct_users(id), alert_id INTEGER REFERENCES alerts(id),
        device_id TEXT NOT NULL REFERENCES ct_managed_devices(device_id), description TEXT NOT NULL,
        notes TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('requested', 'accepted', 'in_progress', 'resolved', 'closed', 'cancelled')),
        UNIQUE(alert_id, technician_id)
    );
    CREATE INDEX IF NOT EXISTS ct_maintenance_by_owner ON ct_maintenance_requests(owner_id, id DESC);
    CREATE INDEX IF NOT EXISTS ct_maintenance_by_technician ON ct_maintenance_requests(technician_id, id DESC);
    CREATE TABLE IF NOT EXISTS ct_maintenance_history (
        id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES ct_maintenance_requests(id),
        actor_id INTEGER NOT NULL REFERENCES ct_users(id), actor_role TEXT NOT NULL,
        previous_status TEXT, status TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ct_maintenance_history_by_request ON ct_maintenance_history(request_id, id);
    CREATE TABLE IF NOT EXISTS ct_maintenance_ratings (
        request_id INTEGER PRIMARY KEY REFERENCES ct_maintenance_requests(id),
        score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5), comment TEXT NOT NULL, created_at TEXT NOT NULL
    );
    BEGIN IMMEDIATE;
    INSERT INTO ct_maintenance_requests
        (id, owner_id, technician_id, alert_id, device_id, description, notes, created_at, updated_at, status)
        SELECT id, owner_id, technician_id, alert_id, device_id, description, notes, created_at, created_at, 'requested'
        FROM ct_reviews WHERE 1
        ON CONFLICT(id) DO NOTHING;
    INSERT INTO ct_maintenance_history
        (request_id, actor_id, actor_role, previous_status, status, notes, created_at)
        SELECT m.id, m.owner_id, 'owner', NULL, 'requested', '', m.created_at
        FROM ct_reviews r JOIN ct_maintenance_requests m ON m.id=r.id
        WHERE NOT EXISTS (SELECT 1 FROM ct_maintenance_history h WHERE h.request_id=m.id);
    COMMIT;
    """)


class SQLiteMaintenanceRepository:
    def __init__(self, repository):
        self.repository = repository

    @contextmanager
    def transaction(self, write=True):
        with self.repository.transaction(write=write) as data:
            if not write:
                data.connection.execute("BEGIN")
            yield SQLiteMaintenanceData(data)


class SQLiteMaintenanceData:
    def __init__(self, shared):
        self.shared = shared
        self.connection = shared.connection

    def owns_device(self, owner, device_id):
        return self.connection.execute(
            "SELECT 1 FROM ct_managed_devices WHERE owner_id=? AND device_id=?", (owner, device_id)
        ).fetchone() is not None

    def saved_technician(self, owner, technician):
        return self.shared.saved_technician(owner, technician)

    def owned_alert(self, owner, alert_id):
        return self.shared.owned_alert(owner, alert_id)

    def request_for_alert(self, owner, alert_id, technician):
        row = self.connection.execute(
            f"""SELECT {REQUEST_FIELDS} FROM ct_maintenance_requests
                WHERE owner_id=? AND alert_id=? AND technician_id=?""", (owner, alert_id, technician)
        ).fetchone()
        return dict(row) if row else None

    def create_request(self, owner, technician, device_id, alert_id, description, notes, email):
        now = datetime.now(timezone.utc).isoformat()
        row = self.connection.execute(
            f"""INSERT INTO ct_maintenance_requests
                    (owner_id, technician_id, alert_id, device_id, description, notes, created_at, updated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING {REQUEST_FIELDS}""",
            (owner, technician, alert_id, device_id, description, notes, now, now, REQUESTED),
        ).fetchone()
        self.add_history(row["id"], {"id": owner, "role": "owner"}, None, REQUESTED, "", now)
        body = f"Equipo {device_id}\nSolicitud #{row['id']}\n\n{description}"
        if notes:
            body += f"\n\nNotas del propietario: {notes}"
        self.shared.queue_mail(email, "Nueva solicitud de revisión en ColdTrack", body)
        return dict(row)

    def list_requests(self, principal, limit, before, alerts_only):
        column = PARTICIPANT_COLUMNS[principal["role"]]
        query = f"SELECT {REQUEST_FIELDS} FROM ct_maintenance_requests WHERE {column}=?"
        params = [principal["id"]]
        if before is not None:
            query += " AND id<?"
            params.append(before)
        if alerts_only:
            query += " AND alert_id IS NOT NULL"
        params.append(limit)
        return [dict(row) for row in self.connection.execute(query + " ORDER BY id DESC LIMIT ?", params)]

    def find_request(self, principal, request_id):
        column = PARTICIPANT_COLUMNS[principal["role"]]
        row = self.connection.execute(
            f"SELECT {REQUEST_FIELDS} FROM ct_maintenance_requests WHERE {column}=? AND id=?",
            (principal["id"], request_id),
        ).fetchone()
        return dict(row) if row else None

    def detail(self, item):
        history = self.connection.execute(
            """SELECT actor_id AS actorId, actor_role AS actorRole, previous_status AS previousStatus,
                      status, notes, created_at AS createdAt
               FROM ct_maintenance_history WHERE request_id=? ORDER BY id""", (item["id"],)
        )
        return {**item, "history": [dict(row) for row in history], "rating": self.rating(item["id"])}

    def add_history(self, request_id, principal, previous, status, notes, now):
        self.connection.execute(
            """INSERT INTO ct_maintenance_history
                   (request_id, actor_id, actor_role, previous_status, status, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (request_id, principal["id"], principal["role"], previous, status, notes, now),
        )

    def transition(self, request_id, principal, previous, status, notes):
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "UPDATE ct_maintenance_requests SET status=?, updated_at=? WHERE id=?", (status, now, request_id)
        )
        self.add_history(request_id, principal, previous, status, notes, now)

    def rating(self, request_id):
        row = self.connection.execute(
            "SELECT score, comment, created_at AS createdAt FROM ct_maintenance_ratings WHERE request_id=?",
            (request_id,),
        ).fetchone()
        return dict(row) if row else None

    def rate(self, request_id, score, comment):
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "INSERT INTO ct_maintenance_ratings (request_id, score, comment, created_at) VALUES (?, ?, ?, ?)",
            (request_id, score, comment, now),
        )
        return {"score": score, "comment": comment, "createdAt": now}

    def remove_contact(self, owner, technician_id):
        self.connection.execute(
            "DELETE FROM ct_contacts WHERE owner_id=? AND technician_id=?", (owner, technician_id)
        )

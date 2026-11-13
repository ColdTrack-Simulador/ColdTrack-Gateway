from datetime import datetime, timezone
from secrets import token_urlsafe

from coldtrack.application import UseCaseError, secret_digest, text


class Equipment:
    def __init__(self, repository):
        self.repository = repository

    def provision(self, owner, payload):
        code = token_urlsafe(32)
        now = int(datetime.now(timezone.utc).timestamp())
        with self.repository.transaction() as data:
            data.save_pairing(owner, text(payload, "name", 100), secret_digest(code), now + 600)
        return {"pairingCode": code, "expiresIn": 600}

    def claim(self, owner, payload):
        code = text(payload, "pairingCode", 64, 32)
        name = text(payload, "name", 100) if "name" in payload else None
        token = token_urlsafe(32)
        now = int(datetime.now(timezone.utc).timestamp())
        with self.repository.transaction() as data:
            result = data.claim_pairing(secret_digest(code), owner, name, secret_digest(token), now)
            if not result:
                raise UseCaseError("Código de vinculación inválido o vencido.", 404)
        return {**result, "gatewayToken": token}

    def detail(self, owner, device_id):
        with self.repository.transaction(write=False) as data:
            device = data.equipment_detail(owner, device_id)
        if not device:
            raise UseCaseError("Equipo no encontrado.", 404)
        last_seen = device["lastSeen"]
        seen_at = datetime.fromisoformat(last_seen) if last_seen else None
        if seen_at is not None and seen_at.tzinfo is None:
            seen_at = seen_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - seen_at).total_seconds() if seen_at else None
        return {**device, "status": "offline" if age is None else "online" if age < 120 else "stale"}

    def usage(self, owner, device_id, days):
        if not 1 <= days <= 30:
            raise UseCaseError("days debe estar entre 1 y 30.")
        with self.repository.transaction(write=False) as data:
            if not data.equipment_detail(owner, device_id):
                raise UseCaseError("Equipo no encontrado.", 404)
            observed, running = data.equipment_usage(device_id, days)
        return {"deviceId": device_id, "days": days, "observedSeconds": round(observed),
                "runningSeconds": round(running), "maxGapSeconds": 360}

    def acknowledge(self, device_id, payload):
        revision = text(payload, "revision", 64, 64)
        enabled = payload.get("applied")
        if type(enabled) is not bool:
            raise UseCaseError("applied debe ser booleano.")
        with self.repository.transaction() as data:
            if not data.record_capture_ack(device_id, revision, enabled):
                raise UseCaseError("La configuración cambió. Consultá la política actual.", 409)

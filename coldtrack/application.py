from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest, new as hmac_digest
from math import isfinite
from re import fullmatch
from secrets import randbelow, token_urlsafe
from typing import Protocol

from coldtrack.domain import CHANNELS, DEFAULT_LIMITS, sampling_active


class Passwords(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, digest: str, password: str) -> bool: ...


class Repository(Protocol):
    def transaction(self): ...


class UseCaseError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def text(payload, key, maximum, minimum=1):
    value = payload.get(key)
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise UseCaseError(f"Revisá el campo {key}.")
    return value.strip()


def secret_digest(value):
    return sha256(value.encode()).hexdigest()


def clock_minutes(value):
    if not isinstance(value, str) or not fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise UseCaseError("Usá horarios HH:MM en UTC.")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


class Operations:
    def __init__(self, repository: Repository, passwords: Passwords):
        self.repository = repository
        self.passwords = passwords

    def register(self, payload, verification_key):
        email = text(payload, "email", 254).lower()
        if not fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", email):
            raise UseCaseError("Ingresá un correo válido.")
        name = text(payload, "name", 100)
        role = payload.get("role", "owner")
        if role not in ("owner", "technician"):
            raise UseCaseError("El rol debe ser owner o technician.")
        password = payload.get("password")
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise UseCaseError("La contraseña debe tener entre 12 y 256 caracteres.")
        phone = text(payload, "phone", 40, minimum=0) if "phone" in payload else ""
        now = int(datetime.now(timezone.utc).timestamp())
        registration_id = token_urlsafe(24)
        code = f"{randbelow(1_000_000):06d}"
        digest = hmac_digest(verification_key.encode(), f"{email}:{registration_id}:{code}".encode(), "sha256").hexdigest()
        password_hash = self.passwords.hash(password)
        with self.repository.transaction() as data:
            if data.registration_allowed(email, now):
                data.store_registration(email, name, role, phone, password_hash, digest, now)
                data.queue_mail(email, "Verificá tu cuenta de ColdTrack",
                                f"Tu código es {code}. Usalo dentro de los próximos 10 minutos.", expires=now + 600)

        return registration_id

    def verify(self, payload, verification_key):
        email = text(payload, "email", 254).lower()
        registration_id = text(payload, "registrationId", 64)
        code = text(payload, "code", 6, 6)
        if not fullmatch(r"[0-9]{6}", code):
            raise UseCaseError("El código debe tener seis dígitos.")
        digest = hmac_digest(verification_key.encode(), f"{email}:{registration_id}:{code}".encode(), "sha256").hexdigest()
        now = int(datetime.now(timezone.utc).timestamp())
        with self.repository.transaction() as data:
            challenge = data.registration(email)
            if challenge is None or challenge["expires"] <= now or challenge["attempts"] <= 0:
                verified = False
            elif compare_digest(challenge["code_hash"], digest):
                data.complete_registration(email)
                verified = True
            else:
                data.fail_registration_attempt(email)
                verified = False
        if not verified:
            raise UseCaseError("El código es inválido o venció.")

    def authenticate(self, identifier, password):
        with self.repository.transaction(write=False) as data:
            user = data.user_by_email(identifier.strip().lower())
        if user and self.passwords.verify(user["password_hash"], password):
            return {"id": user["id"], "role": user["role"], "email": user["email"]}
        return None

    def create_device(self, owner, payload):
        name = text(payload, "name", 100)
        device_id = "CT-" + token_urlsafe(12)
        token = token_urlsafe(32)
        with self.repository.transaction() as data:
            data.create_device(owner, device_id, name, secret_digest(token), DEFAULT_LIMITS)
        return {"deviceId": device_id, "name": name, "gatewayToken": token}

    def owned_device(self, owner, device_id):
        with self.repository.transaction(write=False) as data:
            device = data.device(device_id)
        if device is None or device["owner_id"] != owner:
            raise UseCaseError("Equipo no encontrado.", 404)
        return device

    def update_policy(self, owner, device_id, payload):
        if not payload.keys() <= {"enabled", "channels", "schedule", "limits"} or not payload:
            raise UseCaseError("Enviá enabled, channels, schedule o limits.")
        if "enabled" in payload and type(payload["enabled"]) is not bool:
            raise UseCaseError("enabled debe ser booleano.")
        if "channels" in payload:
            channels = payload["channels"]
            if not isinstance(channels, list) or any(channel not in CHANNELS for channel in channels):
                raise UseCaseError("Revisá los canales de medición.")
            payload["channels"] = list(dict.fromkeys(channels))
        if "schedule" in payload:
            schedule = payload["schedule"]
            if not isinstance(schedule, list) or len(schedule) > 28:
                raise UseCaseError("Enviá hasta 28 ventanas de operación.")
            windows = []
            for window in schedule:
                if not isinstance(window, dict) or type(window.get("day")) is not int or not 0 <= window["day"] <= 6:
                    raise UseCaseError("day debe ser un número entre 0 (lunes) y 6 (domingo).")
                start, end = clock_minutes(window.get("start")), clock_minutes(window.get("end"))
                if start == end:
                    raise UseCaseError("El inicio y el fin deben ser distintos.")
                windows.append({"day": window["day"], "start": start, "end": end})
            payload["schedule"] = windows
        if "limits" in payload:
            limits = payload["limits"]
            if not isinstance(limits, dict):
                raise UseCaseError("Enviá límites por canal.")
            for channel, bounds in limits.items():
                if channel not in CHANNELS or not isinstance(bounds, dict) or not bounds or not bounds.keys() <= {"min", "max"}:
                    raise UseCaseError("Revisá los límites por canal.")
                if any(type(value) not in (int, float) or not -1e6 <= value <= 1e6 or not isfinite(value) for value in bounds.values()):
                    raise UseCaseError("Los límites deben ser números válidos.")
                if "min" in bounds and "max" in bounds and bounds["min"] >= bounds["max"]:
                    raise UseCaseError("El límite mínimo debe ser menor que el máximo.")
        with self.repository.transaction() as data:
            device = data.device(device_id)
            if device is None or device["owner_id"] != owner:
                raise UseCaseError("Equipo no encontrado.", 404)
            policy = {**device["policy"], **payload}
            data.update_policy(device_id, policy)
        return self.policy_response(policy)

    @staticmethod
    def policy_response(policy):
        active = sampling_active(policy["enabled"], policy["schedule"], datetime.now(timezone.utc))
        def hhmm(minutes):
            return f"{minutes // 60:02d}:{minutes % 60:02d}"

        schedule = [{"day": window["day"], "start": hhmm(window["start"]), "end": hhmm(window["end"])}
                    for window in policy["schedule"]]
        return {**policy, "schedule": schedule, "timezone": "UTC",
                "samplingActive": active and bool(policy["channels"])}

    def save_contact(self, owner, technician_id):
        if type(technician_id) is not int or technician_id <= 0:
            raise UseCaseError("Revisá el identificador del técnico.")
        with self.repository.transaction() as data:
            tech = data.user(technician_id)
            if not tech or tech["role"] != "technician":
                raise UseCaseError("Técnico no encontrado.", 404)
            data.save_contact(owner, technician_id)

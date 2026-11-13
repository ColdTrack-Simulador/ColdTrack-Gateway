from datetime import datetime, timezone
from hmac import compare_digest, new as hmac_digest
from re import fullmatch
from secrets import randbelow

from coldtrack.application import UseCaseError, text

RESET_SUBJECT = "Restablecé tu contraseña de ColdTrack"


def public_profile(user):
    return {field: user[field] for field in ("id", "email", "role", "name", "phone")}


def reset_digest(key, email, code):
    return hmac_digest(key.encode(), f"password-reset:{email}:{code}".encode(), "sha256").hexdigest()


class Accounts:
    def __init__(self, repository, passwords):
        self.repository = repository
        self.passwords = passwords

    def profile(self, account_id):
        with self.repository.transaction(write=False) as data:
            user = data.user(account_id)
        if user is None:
            raise UseCaseError("Cuenta no encontrada.", 404)
        return public_profile(user)

    def update_profile(self, account_id, payload):
        if not payload or payload.keys() - {"name", "phone"}:
            raise UseCaseError("Solo podés actualizar name y phone.")
        name = text(payload, "name", 100) if "name" in payload else None
        phone = text(payload, "phone", 40, minimum=0) if "phone" in payload else None
        with self.repository.transaction() as data:
            user = data.update_profile(account_id, name, phone)
        if user is None:
            raise UseCaseError("Cuenta no encontrada.", 404)
        return public_profile(user)

    def request_reset(self, payload, key):
        email = text(payload, "email", 254).lower()
        now = int(datetime.now(timezone.utc).timestamp())
        code = f"{randbelow(1_000_000):06d}"
        digest = reset_digest(key, email, code)
        with self.repository.transaction() as data:
            user = data.user_by_email(email)
            if user is None:
                return
            pending = data.password_reset(user["id"])
            if pending and pending["resend_at"] > now:
                return
            data.store_password_reset(user["id"], digest, now)
            data.clear_password_reset_mail(email, RESET_SUBJECT)
            data.queue_mail(
                email,
                RESET_SUBJECT,
                f"Tu código es {code}. Usalo dentro de los próximos 10 minutos.",
                expires=now + 600,
            )

    def confirm_reset(self, payload, key):
        email = text(payload, "email", 254).lower()
        code = text(payload, "code", 6, 6)
        password = payload.get("password")
        if not fullmatch(r"[0-9]{6}", code):
            raise UseCaseError("El código debe tener seis dígitos.")
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise UseCaseError("La contraseña debe tener entre 12 y 256 caracteres.")
        now = int(datetime.now(timezone.utc).timestamp())
        digest = reset_digest(key, email, code)
        with self.repository.transaction(write=False) as data:
            user = data.user_by_email(email)
            pending = data.password_reset(user["id"]) if user else None
        candidate = None
        if pending and pending["expires"] > now and pending["attempts"] > 0:
            if compare_digest(pending["code_hash"], digest):
                candidate = self.passwords.hash(password)
        verified = False
        with self.repository.transaction() as data:
            current = data.password_reset(user["id"]) if user else None
            now = int(datetime.now(timezone.utc).timestamp())
            unchanged = pending and current and current["code_hash"] == pending["code_hash"]
            if unchanged and current["expires"] > now and current["attempts"] > 0:
                if candidate is not None and compare_digest(current["code_hash"], digest):
                    data.complete_password_reset(user["id"], candidate)
                    data.clear_password_reset_mail(email, RESET_SUBJECT)
                    verified = True
                else:
                    data.fail_password_reset(user["id"])
        if not verified:
            raise UseCaseError("El código es inválido o venció.")

    def session_version_matches(self, principal, version):
        with self.repository.transaction(write=False) as data:
            user = data.user(principal["id"])
        return user is not None and user["session_version"] == version

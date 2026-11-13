from typing import Protocol

from coldtrack.application import UseCaseError, text
from coldtrack.maintenance_domain import STATUSES, can_transition


class MaintenanceRepository(Protocol):
    def transaction(self, write=True): ...


def positive_id(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise UseCaseError("Revisá el identificador.")
    return value


def optional_text(values, key):
    return text(values, key, 1500, minimum=0) if key in values else ""


class Maintenance:
    def __init__(self, repository: MaintenanceRepository):
        self.repository = repository

    def create(self, owner, values):
        if not values.keys() <= {"deviceId", "technicianId", "alertId", "description", "notes"}:
            raise UseCaseError("Revisá los campos de la solicitud.")
        technician = positive_id(values.get("technicianId"))
        alert_id = positive_id(values["alertId"]) if "alertId" in values else None
        notes = optional_text(values, "notes")
        if alert_id is None:
            device_id = text(values, "deviceId", 64)
            description = text(values, "description", 1500)
        elif "description" in values:
            raise UseCaseError("Usá notes para agregar detalles a una alerta.")

        with self.repository.transaction() as data:
            contact = data.saved_technician(owner, technician)
            if not contact:
                raise UseCaseError("Contacto no encontrado.", 404)
            if alert_id is not None:
                alert = data.owned_alert(owner, alert_id)
                if not alert or ("deviceId" in values and values["deviceId"] != alert["device_id"]):
                    raise UseCaseError("Alerta no encontrada.", 404)
                device_id = alert["device_id"]
                description = alert["message"] or alert["title"]
                previous = data.request_for_alert(owner, alert_id, technician)
                if previous:
                    return previous, False
            elif not data.owns_device(owner, device_id):
                raise UseCaseError("Equipo no encontrado.", 404)

            return data.create_request(owner, technician, device_id, alert_id, description, notes, contact["email"]), True

    def list_requests(self, principal, limit, before, alerts_only=False):
        with self.repository.transaction(write=False) as data:
            return data.list_requests(principal, limit, before, alerts_only)

    def get_request(self, principal, request_id):
        with self.repository.transaction(write=False) as data:
            item = self.require_request(data, principal, request_id)
            return data.detail(item)

    def transition(self, principal, request_id, values):
        if not values.keys() <= {"status", "notes"}:
            raise UseCaseError("Revisá los campos del cambio de estado.")
        status = values.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            raise UseCaseError("Estado de solicitud inválido.")
        notes = optional_text(values, "notes")
        with self.repository.transaction() as data:
            item = self.require_request(data, principal, request_id)
            if not can_transition(principal["role"], item["status"], status):
                raise UseCaseError("El cambio de estado no está permitido.", 409)
            data.transition(request_id, principal, item["status"], status, notes)
            return data.detail(self.require_request(data, principal, request_id))

    def rate(self, principal, request_id, values):
        if not values.keys() <= {"score", "comment"}:
            raise UseCaseError("Revisá los campos de la reseña.")
        score = values.get("score")
        if type(score) is not int or not 1 <= score <= 5:
            raise UseCaseError("La puntuación debe ser un entero entre 1 y 5.")
        comment = optional_text(values, "comment")
        with self.repository.transaction() as data:
            item = self.require_request(data, principal, request_id)
            if item["status"] != "closed":
                raise UseCaseError("La solicitud debe estar cerrada.", 409)
            if data.rating(request_id):
                raise UseCaseError("La solicitud ya tiene una reseña.", 409)
            return data.rate(request_id, score, comment)

    def remove_contact(self, owner, technician_id):
        with self.repository.transaction() as data:
            data.remove_contact(owner, positive_id(technician_id))

    @staticmethod
    def require_request(data, principal, request_id):
        item = data.find_request(principal, positive_id(request_id))
        if item is None:
            raise UseCaseError("Solicitud no encontrada.", 404)
        return item

from re import findall
from unicodedata import normalize

from flask import Blueprint, jsonify

from coldtrack.application import UseCaseError, text
from coldtrack.http import actor, payload


ANSWERS = (
    ({"sensor", "sensores", "captura", "horario", "pausar"},
     "En Operaciones elegí tu equipo y ajustá la captura y los horarios. Los horarios se guardan en UTC.",
     "Captura y umbrales"),
    ({"alerta", "alertas", "umbral", "notificacion", "notificaciones", "presion", "temperatura"},
     "Configurá los límites de tu equipo en Captura y umbrales. Consultá los desvíos detectados en Alertas y diagnóstico.",
     "Alertas y diagnóstico"),
    ({"tecnico", "tecnicos", "mantenimiento", "revision", "solicitud", "solicitudes"},
     "Guardá un técnico en Contactos técnicos y asociá una solicitud a una alerta. El técnico asignado puede consultar el problema.",
     "Solicitudes de mantenimiento"),
    ({"equipo", "equipos", "registrar", "qr", "vincular"},
     "Registrá un equipo en Operaciones. Guardá el token de telemetría en un lugar privado para configurar el gateway.",
     "Alta de equipos"),
    ({"correo", "codigo", "contrasena", "verificacion", "registro"},
     "El código se envía al correo de la cuenta y vence a los diez minutos. Si venció, solicitá uno nuevo.",
     "Registro y acceso"),
)


def reply(question):
    normalized = normalize("NFKD", question.casefold()).encode("ascii", "ignore").decode()
    words = set(findall(r"[a-z]+", normalized))
    ranked = [(len(words & keywords), answer, topic) for keywords, answer, topic in ANSWERS]
    score, answer, topic = max(ranked, key=lambda result: result[0])
    if score == 0:
        return {"answered": False, "answer": "Puedo ayudarte con captura, alertas, equipos, correo y mantenimiento.",
                "topic": None}
    return {"answered": True, "answer": answer, "topic": topic}


def create_assistant_blueprint():
    bp = Blueprint("assistant", __name__)

    @bp.errorhandler(UseCaseError)
    def handle_error(error):
        return jsonify({"error": str(error)}), error.status

    @bp.post("/api/assistant/questions")
    def question():
        actor()
        return jsonify(reply(text(payload(), "question", 500)))

    return bp

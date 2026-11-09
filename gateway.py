import json
import logging
import os
from threading import Lock

import requests
from flask import Flask, request


app = Flask(__name__)

# -------------------------------------------
# Configuración
# -------------------------------------------

COLDTRACK_API_URL = os.environ.get("COLDTRACK_API_URL", "http://127.0.0.1:5000/api/telemetry")
COLDTRACK_INGEST_KEY = os.environ.get("COLDTRACK_INGEST_KEY")


# Evitar llenar la consola con cada petición
# HTTP que hace la extensión.
logging.getLogger(
    "werkzeug"
).setLevel(logging.ERROR)


# Última lectura procesada por dispositivo.
ultimo_uptime = {}
envio_lock = Lock()


# =====================================================
# TINKERCAD -> GATEWAY
# =====================================================

@app.get("/send")
def recibir_telemetria():

    salida_serial = request.args.get(
        "out",
        ""
    ).strip()


    # -------------------------------------------
    # Sin datos
    # -------------------------------------------

    if not salida_serial:
        return "", 200


    # -------------------------------------------
    # La extensión suele mandar el contenido
    # mientras el JSON todavía está apareciendo
    # en el Monitor Serie.
    #
    # Ignoramos esos fragmentos.
    # -------------------------------------------

    if not (
        salida_serial.startswith("{")
        and salida_serial.endswith("}")
    ):
        return "", 200


    # -------------------------------------------
    # Parsear JSON
    # -------------------------------------------

    try:

        telemetria = json.loads(
            salida_serial
        )

    except json.JSONDecodeError:

        return "", 200


    # -------------------------------------------
    # Identificar paquete
    # -------------------------------------------

    device_id = telemetria.get("deviceId")
    if not isinstance(device_id, str) or not device_id.strip():
        return "Falta el identificador del dispositivo", 400

    uptime = telemetria.get(
        "uptimeMs"
    )


    # -------------------------------------------
    # Evitar duplicados
    # -------------------------------------------

    with envio_lock:
        if uptime is not None and ultimo_uptime.get(device_id) == uptime:
            return "", 200
        if not enviar_a_api(telemetria):
            return "No se pudo entregar la telemetría", 502
        ultimo_uptime[device_id] = uptime
    return "OK", 200


# =====================================================
# PYTHON -> TINKERCAD
# =====================================================

@app.get("/cmd")
def consultar_comandos():

    # La extensión consulta periódicamente
    # si existe algún comando para Arduino.
    #
    # Esta función queda preparada para una
    # futura comunicación bidireccional.

    return "", 200


# =====================================================
# GATEWAY -> COLDTRACK API
# =====================================================

def enviar_a_api(telemetria):
    headers = {"Authorization": f"Bearer {COLDTRACK_INGEST_KEY}"} if COLDTRACK_INGEST_KEY else {}
    try:
        respuesta = requests.post(COLDTRACK_API_URL, json=telemetria, headers=headers, timeout=5)
        respuesta.raise_for_status()
    except requests.RequestException as error:
        app.logger.warning("Falló el envío a ColdTrack: %s", error)
        return False
    mostrar_telemetria(telemetria)
    return True

# =====================================================
# CONSOLA
# =====================================================

def mostrar_telemetria(telemetria):

    print()
    print(
        "=========================================="
    )
    print(
        "          COLDTRACK GATEWAY"
    )
    print(
        "=========================================="
    )

    print(
        "Dispositivo: ",
        telemetria.get("deviceId")
    )

    print(
        "Uptime:      ",
        telemetria.get("uptimeMs"),
        "ms"
    )

    print(
        "Estado:      ",
        telemetria.get("estado")
    )

    print(
        "Diagnostico: ",
        telemetria.get("diagnostico")
    )

    print(
        "------------------------------------------"
    )

    print(
        "Temp interior:    ",
        telemetria.get("tempInterior"),
        "C"
    )

    print(
        "Temp condensador: ",
        telemetria.get("tempCondensador"),
        "C"
    )

    print(
        "Temp succion:     ",
        telemetria.get("tempSuccion"),
        "C"
    )

    print(
        "Presion baja:     ",
        telemetria.get("presionBaja"),
        "%"
    )

    print(
        "Presion alta:     ",
        telemetria.get("presionAlta"),
        "%"
    )

    print(
        "Compresor:        ",
        telemetria.get("compresor"),
        "%"
    )

    print(
        "Fan solicitado:   ",
        telemetria.get("fanSolicitado"),
        "%"
    )

    print(
        "Fan PWM:          ",
        telemetria.get("fanPWM")
    )

    print(
        "Fan RPM:          ",
        telemetria.get("fanRpm")
    )

    print(
        "------------------------------------------"
    )

    print(
        "Proteccion alta:  ",
        telemetria.get(
            "proteccionAlta"
        )
    )

    print(
        "Proteccion baja:  ",
        telemetria.get(
            "proteccionBaja"
        )
    )

    print(
        "Parar compresor:  ",
        telemetria.get(
            "ordenParoCompresor"
        )
    )

    print(
        "=========================================="
    )


if __name__ == "__main__":

    print()
    print(
        "=========================================="
    )
    print(
        "       COLDTRACK IOT GATEWAY"
    )
    print(
        "=========================================="
    )

    print(
        "Tinkercad:"
    )

    print(
        "http://127.0.0.1:8080"
    )

    print()

    print(
        "ColdTrack API:"
    )

    print(
        COLDTRACK_API_URL
    )

    print(
        "=========================================="
    )


    app.run(
        host="127.0.0.1",
        port=8080,
        debug=False
    )

from datetime import datetime

CHANNELS = ("tempInterior", "tempCondensador", "tempSuccion", "presionAlta", "presionBaja")
DEFAULT_LIMITS = {
    "tempInterior": {"max": 5},
    "tempCondensador": {"max": 65},
    "presionAlta": {"max": 90},
    "presionBaja": {"min": 10},
}


def sampling_active(enabled: bool, windows: list[dict], now: datetime) -> bool:
    if not enabled:
        return False
    if not windows:
        return True

    minute = now.hour * 60 + now.minute
    weekday = now.weekday()
    for window in windows:
        day, start, end = window["day"], window["start"], window["end"]
        if start < end and day == weekday and start <= minute < end:
            return True
        if start > end and (
            (day == weekday and minute >= start)
            or ((day + 1) % 7 == weekday and minute < end)
        ):
            return True
    return False


def diagnose(reading: dict, limits: dict) -> list[dict]:
    findings = []
    exceeded = set()
    for channel, bounds in limits.items():
        value = reading.get(channel)
        if type(value) not in (int, float):
            continue
        for bound, threshold in bounds.items():
            if (bound == "max" and value >= threshold) or (bound == "min" and value <= threshold):
                exceeded.add((channel, bound))
                relation = "alcanzó o superó" if bound == "max" else "alcanzó o cayó por debajo de"
                findings.append({
                    "code": f"{channel}:{bound}",
                    "description": f"{channel} {relation} {threshold}. Lectura: {value}.",
                })

    if {("presionAlta", "max"), ("tempCondensador", "max")} <= exceeded:
        findings.insert(0, {
            "code": "POSIBLE_PROBLEMA_CONDENSACION",
            "description": "Presión alta y temperatura de condensador elevadas. Revisar ventilación y condensador.",
        })
    if {("presionBaja", "min"), ("tempInterior", "max")} <= exceeded:
        findings.insert(0, {
            "code": "POSIBLE_PERDIDA_CAPACIDAD_FRIGORIFICA",
            "description": "Presión baja y cámara caliente. Revisar el circuito y la carga de refrigerante.",
        })
    return findings

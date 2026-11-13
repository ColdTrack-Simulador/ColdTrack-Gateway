# Pruebas manuales de ColdTrack

## Preparación

En una carpeta de ensayo, instalá `requirements.txt`, configurá una base SQLite separada mediante `COLDTRACK_DB_PATH`, una clave de sesión mediante `COLDTRACK_SESSION_SECRET` y `COLDTRACK_REGISTRATION_ENABLED=1`. Ejecutá `python -m flask --app apy run` y abrí `http://127.0.0.1:5000/apidocs/`. Usá el mismo origen para que el navegador conserve la cookie de sesión. Solo para HTTP local, configurá `COLDTRACK_ALLOW_HTTP=1`.

Para verificar un alta o un restablecimiento, configurá SMTP y ejecutá `python -m flask --app apy drain-mail`, o ejecutá el worker n8n con su clave exclusiva. Reemplazá owner@example.test por un buzón real de prueba al que tengas acceso. Guardá los identificadores y tokens que devuelva Swagger durante la sesión de prueba.

## Cuentas

| Método y ruta | Ejemplo de prueba | Resultado |
|---|---|---|
| GET /health | Abrir sin sesión | 200 y estado de servicio |
| POST /api/registration | Enviar `{"name":"Propietario de prueba","email":"owner@example.test","password":"clave-local-larga","role":"owner","phone":""}`; repetir para un técnico | 202 con registrationId. El login falla hasta verificar |
| POST /api/registration/verify | Enviar email, registrationId y code como texto de seis dígitos | 204. Repetir el código devuelve 400 |
| POST /api/session | Enviar `{"username":"owner@example.test","password":"clave-local-larga"}` | 200, cookie y datos básicos de la cuenta |
| GET /api/session | Consultar con la cookie | 200 y authenticated true |
| DELETE /api/session | Cerrar y volver a consultar | 200; authenticated false |
| GET /api/profile | Ingresar con la cuenta registrada | 200 y perfil sin hash |
| PATCH /api/profile | Enviar `{"name":"Nombre actualizado","phone":"+54 381 5550100"}` | 200; nuevo nombre. Un intento de cambiar role o email falla |
| POST /api/password-reset | Enviar `{"email":"owner@example.test"}` | 202 con respuesta genérica |
| POST /api/password-reset/confirm | Enviar email, code y password nueva | 204; la contraseña previa y las sesiones anteriores dejan de servir |

## Equipos, sensores y telemetría

| Método y ruta | Ejemplo de prueba | Resultado |
|---|---|---|
| POST /api/devices | Enviar `{"name":"Cámara 1"}` | 201 con deviceId y gatewayToken, visible en esta respuesta |
| GET /api/devices | Consultar como propietario | 200; solo aparecen los equipos propios |
| POST /api/devices/pairing | Enviar `{"name":"Cámara QR"}` | 201 con pairingCode y expiresIn; el código es el contenido que podrá representar el QR |
| POST /api/devices/claim | Enviar `{"pairingCode":"CODIGO"}` como el mismo propietario | 200 con equipo y token. Reutilizarlo o usar otra cuenta da 404 |
| GET /api/devices/{device_id} | Consultar el detalle antes y después de recibir una lectura | 200; estado offline, online o stale según hora de última lectura |
| GET /api/devices/{device_id}/usage | Enviar days entre 1 y 30 | 200 con segundos observados y de compresor activo. Con dos muestras activas a 60 s se suman 60 s; los huecos mayores a 120 s quedan fuera |
| GET /api/devices/{device_id}/policy | Consultar con sesión propietaria o Bearer del dispositivo | 200 con horario UTC, samplingActive, revision, captureAck y captureAckCurrent |
| PATCH /api/devices/{device_id}/policy | Enviar `{"enabled":false}`; luego `{"enabled":true,"channels":["tempInterior"],"schedule":[],"limits":{"tempInterior":{"max":5}}}` | 200. En pausa se rechaza la captura; al habilitarla solo se guarda el canal permitido |
| PUT /api/devices/{device_id}/capture-ack | Con Bearer del gateway, enviar `{"revision":"REVISION","applied":true}` | 204. Sin token, 401; con revisión obsoleta, 409 |
| POST /api/telemetry | Con Bearer del gateway, enviar `{"deviceId":"ID","estado":"NORMAL","diagnostico":"SIN_FALLAS","compresor":50,"tempInterior":-18}` | 201 si guarda historial y 200 si aplica deduplicación o pausa |
| POST /api/simulator/readings | Con sesión propietaria, enviar `{"deviceId":"ID","tempInterior":20,"tempCondensador":35,"tempSuccion":-20,"presionAlta":55,"presionBaja":30}` | 201 si guarda, o 200 cuando no corresponde guardar |
| GET /api/telemetry | Consultar con deviceId y limit opcional | 200; historial con alcance de la cuenta |
| GET /api/telemetry/latest/{device_id} | Consultar un ID propio que ya reportó | 200 y última lectura. Un equipo ajeno devuelve 404 |
| GET /api/devices/{device_id}/alerts | Inducir `tempInterior:max` en el simulador y luego recuperar el rango | 200; se abre y se resuelve la alerta |
| GET /api/devices/{device_id}/notification-preferences | Consultar como propietario | 200; inicialmente emailEnabled true y mutedCodes vacío |
| PUT /api/devices/{device_id}/notification-preferences | Enviar `{"emailEnabled":true,"mutedCodes":["tempInterior:max"]}` | 200; un episodio nuevo de ese tipo sigue visible y no encola correo |

El backend aplica la política de captura. Para comprobar el control físico se requiere un dispositivo que consulte la política, aplique el cambio y responda con la revisión correspondiente. applied informa lo que declaró el dispositivo. observedSeconds igual a cero indica que faltan intervalos medibles.

## Contactos y mantenimiento

| Método y ruta | Ejemplo de prueba | Resultado |
|---|---|---|
| GET /api/technicians | Consultar como propietario tras verificar una cuenta técnica | 200 y directorio |
| GET /api/technician-contacts | Consultar antes y después de agregar | 200 y contactos propios |
| PUT /api/technician-contacts/{technician_id} | Usar ID del técnico registrado | 204, repetible |
| DELETE /api/technician-contacts/{technician_id} | Quitar el contacto del propietario | 204; desaparece del listado |
| POST /api/reviews | Enviar `{"alertId":1,"technicianId":2,"notes":"Revisar cámara"}` | 201 al crear, 200 si ya existe |
| GET /api/reviews | Consultar como propietario y técnico asignado | 200 y listado de solicitudes visibles |
| POST /api/maintenance-requests | Enviar `{"deviceId":"ID","technicianId":2,"description":"Revisión preventiva"}` o usar alertId y technicianId | 201 y estado requested. Una solicitud duplicada desde la misma alerta devuelve 200 |
| GET /api/maintenance-requests | Consultar con ambos actores; limit de 1 a 100 y beforeId opcional | 200, items y nextBeforeId |
| GET /api/maintenance-requests/{request_id} | Consultar la misma solicitud con un tercero | 404. Para los actores autorizados, 200 con historial |
| PATCH /api/maintenance-requests/{request_id}/status | Como técnico, enviar accepted, in_progress y resolved; como propietario, closed en el campo status | 200 por transición válida, error de conflicto para un salto inválido |
| POST /api/maintenance-requests/{request_id}/rating | Con el propietario y solicitud cerrada, enviar `{"score":5,"comment":"Trabajo completado"}` | 201 y una única reseña. Repetir la operación devuelve 409 |

## Correo y asistente

| Método y ruta | Ejemplo de prueba | Resultado |
|---|---|---|
| POST /api/mail/claim | Enviar Bearer de worker. Generar antes un correo de prueba | 200 con id y lease, o 204 con cola vacía. Token incorrecto da 401 |
| POST /api/mail/{mail_id}/ack | Con Bearer de worker, enviar `{"lease":"RESERVA","sent":true}` | 204. Repetir la confirmación da 409 |
| POST /api/assistant/questions | Con sesión, enviar `{"question":"¿Cómo pauso los sensores?"}` | 200 con respuesta temática. Una consulta ajena devuelve answered false |

Probá también cada ruta privada con otra cuenta y sin sesión. Registrá el SHA, entorno, respuesta esperada y respuesta obtenida. Usá Swagger para explorar formatos y guardar la evidencia sin incluir contraseñas, códigos o tokens.

# ColdTrack Gateway

## Swagger y comprobaciones locales

La API publica Swagger UI en `/apidocs/` y el contrato OpenAPI 3.0.3 en
`/apispec_1.json`. Abrí la interfaz en el mismo dominio que el backend para
que las peticiones autenticadas reutilicen la cookie de `POST /api/session`.
En desarrollo local por HTTP, configurá `COLDTRACK_ALLOW_HTTP=1` antes de
iniciar Flask si utilizás autenticación. Para telemetría y procesamiento de
correo, utilizá **Authorize** con la credencial Bearer de cada operación.

Instalá las dependencias de desarrollo y ejecutá las comprobaciones:

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check --select F,E9 .
python -m pytest -q
```

El contrato principal está en `coldtrack/openapi/base.json`. Cada módulo puede
agregar un archivo JSON en esa carpeta con `paths` y `components`. Para una ruta
existente, agregá solamente métodos que aún no estén declarados. Para
modificar el contrato de un método existente, actualizá su declaración.
Las pruebas verifican el esquema y que todas las operaciones HTTP públicas
estén documentadas.


## Cuentas, sensores y atención técnica

Las reglas de diagnóstico y horarios están en `coldtrack/domain.py`.
Los casos de uso están en `coldtrack/application.py` y
`services/telemetry_service.py`. Flask adapta HTTP, y SQLite y SMTP implementan
la persistencia y el envío. `apy.py` conecta las capas. Las tablas nuevas se
crean al arrancar en la base configurada por `COLDTRACK_DB_PATH`.

| Método y ruta | Uso |
| --- | --- |
| `POST /api/registration` | Enviar `email`, `name`, `password`, rol `owner` o `technician` y `phone` opcional. Guardar el `registrationId` de la respuesta. |
| `POST /api/registration/verify` | Confirmar con `email`, `registrationId` y el `code` de seis dígitos recibido por correo. Vence a los 10 minutos. |
| `POST /api/session` | Ingresar con el correo en `username` y la contraseña en `password`. |
| `POST /api/devices` | Como propietario, enviar `name`. Guardar el `deviceId` y el `gatewayToken` que se entrega al crear. |
| `GET /api/devices/{id}/policy` | Consultar la política como propietario o con el token Bearer del equipo. |
| `PATCH /api/devices/{id}/policy` | Cambiar `enabled`, `channels`, `schedule` o `limits` como propietario. |
| `GET /api/technicians` | Consultar el directorio de técnicos registrados. |
| `PUT /api/technician-contacts/{id}` y `GET /api/technician-contacts` | Guardar y consultar contactos con correo y teléfono. |
| `POST /api/reviews` | Crear una solicitud con `alertId`, `technicianId` guardado y `notes` opcional. |
| `GET /api/reviews` | Consultar las solicitudes propias como propietario o técnico asignado. |

Ejemplo de política para capturar solo temperatura interior y presión alta
los lunes, de 09:00 a 17:00 UTC:

```json
{
  "enabled": true,
  "channels": ["tempInterior", "presionAlta"],
  "schedule": [{"day": 0, "start": "09:00", "end": "17:00"}],
  "limits": {"tempInterior": {"max": 5}, "presionAlta": {"max": 90}}
}
```

Los días van de 0 (lunes) a 6 (domingo). Una lista de horarios vacía permite
captura permanente y `enabled: false` la pausa. Una ventana puede cruzar la
medianoche. `limits` reemplaza el conjunto anterior. Los valores iniciales
son de demostración, con temperaturas en °C y presiones normalizadas en %.
La API aplica la política al aceptar telemetría. El cliente del equipo puede
consultar el estado efectivo en `samplingActive`. Para un equipo registrado,
configurá `COLDTRACK_DEVICE_ID` y su token en `COLDTRACK_INGEST_KEY` en el
gateway. El simulador acepta `deviceId` en el cuerpo para un equipo propio.

El diagnóstico abre una alerta por condición y también detecta combinaciones,
como presión alta junto a temperatura elevada del condensador. Cada apertura
genera un correo al propietario. La solicitud de revisión queda visible para
el técnico y genera otro correo con el equipo, la descripción y las notas.

### Correo

Habilitá `COLDTRACK_REGISTRATION_ENABLED=1` con una
`COLDTRACK_SESSION_SECRET` configurada y un proceso de correo activo.
Para SMTP con STARTTLS, configurá `COLDTRACK_SMTP_HOST`,
`COLDTRACK_SMTP_PORT` (587 por defecto), `COLDTRACK_SMTP_USERNAME`,
`COLDTRACK_SMTP_PASSWORD` y, si corresponde, `COLDTRACK_SMTP_FROM`.
Ejecutá periódicamente `python -m flask --app apy drain-mail` desde un
proceso con acceso a la misma base. Los avisos quedan en SQLite hasta su
entrega y los fallos se reintentan.

En un hosting con restricciones de SMTP, un n8n externo puede ejecutar
`POST /api/mail/claim`, usar su nodo SMTP y confirmar con
`POST /api/mail/{id}/ack` y el JSON `{"lease":"…","sent":true}`.
Las dos rutas usan Bearer con `COLDTRACK_MAIL_WORKER_KEY`, independiente
de las claves del gateway. `204` al reclamar indica que no hay correos
disponibles. Confirmá el envío dentro de dos minutos o indicá `sent:false`
para reintentar. Usá la credencial de n8n para guardar la clave del worker.

## Demo web integrada

El dashboard y Flask se sirven desde el mismo origen en el despliegue. En desarrollo,
Vite reenvía `/api` y `/health` a Flask. El simulador envía valores de sensores para
`SIM-001`. Flask calcula el diagnóstico y guarda las aperturas y resoluciones en
`GET /api/devices/SIM-001/alerts`. Arduino conserva su flujo a través del gateway.

### Ejecutar en Windows

En esta carpeta, con Python 3.13 instalado:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe apy.py
```

En el repositorio hermano `ColdTrack-Gateway_FRONT`, con Node 24:

```powershell
npm ci
npm run dev
```

Abrir `http://localhost:5173`. Para verificar la compilación servida por Flask,
ejecutar `npm run build` en el frontend y abrir `http://127.0.0.1:5000`.
La cuenta de demo puede habilitarse localmente con las variables de despliegue y
`COLDTRACK_ALLOW_HTTP=1` para la cookie en localhost.

### Publicar en Render

1. Publicar las ramas de integración de ambos repositorios en GitHub.
2. Crear un Blueprint en Render desde la rama de integración de este repositorio.
   El archivo `render.yaml` prepara un servicio Starter con un disco persistente.
3. Configurar `COLDTRACK_FRONTEND_REF` con el SHA publicado del frontend.
   Configurar `COLDTRACK_DEMO_PASSWORD_HASH` con el resultado del siguiente comando.
   El usuario de demo del Blueprint es `demo`.

   ```powershell
   .\.venv\Scripts\python.exe -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass()))"
   ```

4. Render genera `COLDTRACK_SESSION_SECRET` y `COLDTRACK_INGEST_KEY`. Para que
   Tinkercad envíe al servicio público, configurar en la terminal local que ejecuta
   `gateway.py` la URL HTTPS completa en `COLDTRACK_API_URL`, terminada en
   `/api/telemetry`, y la misma clave en `COLDTRACK_INGEST_KEY`. La extensión
   continúa apuntando a `http://127.0.0.1:8080`.
5. Probar el enlace HTTPS con una ventana sin sesión y ejecutar **Normal /
   recuperación → Alta presión → Normal / recuperación** en el simulador.
   En el detalle de `SIM-001` debe quedar una alerta resuelta. Repetir una
   lectura de falla dentro de un mismo episodio conserva una sola alerta.

Los umbrales de la demo son presión alta desde 90 %, presión baja hasta 10 %
y temperatura interior desde 5 °C. Son valores del simulador. Las presiones
se expresan como porcentajes normalizados. El primer corte procesa una muestra
por cada envío del formulario.

## Referencia del gateway con Tinkercad

Gateway de integración para el prototipo ColdTrack, encargado de recibir la telemetría generada por un Arduino UNO simulado en Tinkercad [https://www.tinkercad.com/things/2WnGBlR1Mr8-admin-tfi], validarla y reenviarla mediante HTTP a una API local de ColdTrack.

El objetivo de este repositorio es permitir probar el flujo completo sin necesidad de disponer de hardware físico.

```text
Sensores simulados
      ↓
Arduino UNO en Tinkercad
      ↓
Monitor Serie (JSON)
      ↓
tinkercad-serial-bridge
      ↓
ColdTrack Gateway (Python / Flask) :8080
      ↓
HTTP POST
      ↓
ColdTrack API (Python / Flask) :5000
```

---



## 1. Componentes del prototipo

Este repositorio utiliza dos procesos Python:

| Componente | Archivo | Puerto | Responsabilidad |
| --- | --- | --- | --- |
| ColdTrack Gateway | `gateway.py` | `8080` | Recibe la salida del Monitor Serie de Tinkercad y reenvía la telemetría válida a la API |
| ColdTrack API | `apy.py` | `5000` | Recibe la telemetría mediante `POST /api/telemetry` y la persiste en SQLite |

La comunicación entre Tinkercad y el Gateway se realiza mediante la extensión **tinkercad-serial-bridge**.

---

## 2. Requisitos

Para ejecutar el proyecto se necesita:

* Windows 10/11, Linux o macOS.
* Un navegador basado en Chromium compatible con la extensión utilizada, por ejemplo Microsoft Edge o Google Chrome.
* Una cuenta de Autodesk con acceso a Tinkercad Circuits.
* Python 3.10 o superior.
* Acceso al circuito de Tinkercad utilizado por ColdTrack.
* La extensión `tinkercad-serial-bridge` instalada y habilitada.
* *No se necesita un Arduino físico.*

---

## 3. Instalación de Python

### Windows

1. Descargar Python desde el sitio oficial: [https://www.python.org/downloads/](https://www.python.org/downloads/)
2. Ejecutar el instalador.
3. Antes de comenzar la instalación, **marcar**:
   ```text
   Add python.exe to PATH
   ```
4. Finalizar la instalación.
5. Abrir PowerShell o CMD y comprobar:
   ```powershell
   python --version
   ```
   Debería mostrarse una versión similar a `Python 3.12.x`.

> Si Windows no reconoce `python`, probar:
> ```powershell
> py --version
> ```
> En ese caso, los comandos de este README que comienzan con `python` pueden ejecutarse reemplazándolo por `py`.

### Linux
En distribuciones basadas en Debian/Ubuntu:
```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```
Comprobar:
```bash
python3 --version
```

### macOS
Puede utilizarse la versión disponible desde Python.org o instalar Python mediante Homebrew:
```bash
brew install python
```
Comprobar:
```bash
python3 --version
```

---

## 4. Obtener el proyecto

Clonar el repositorio o descargarlo como ZIP. Luego abrir una terminal dentro de la carpeta del proyecto.

Ejemplo en Windows:
```powershell
cd C:\ruta\al\repositorio\ColdTrack-Gateway
```

La carpeta debería contener, como mínimo:
```text
ColdTrack-Gateway/
├── apy.py
├── gateway.py
└── README.md
```

---

## 5. Crear el entorno virtual

Se recomienda utilizar un entorno virtual para mantener aisladas las dependencias del proyecto.

### Windows
Crear el entorno:
```powershell
python -m venv .venv
```
Activarlo:
```powershell
.venv\Scripts\Activate.ps1
```
*Si PowerShell impide ejecutar el script de activación, puede habilitarlo para el usuario actual con:*
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
Luego volver a ejecutar la activación. Cuando esté activo debería aparecer `(.venv)` al comienzo de la terminal.

### Linux/macOS
```bash
python3 -m venv .venv
source .venv/bin/activate
```

---

## 6. Instalar dependencias

Con el entorno virtual activo:
```bash
pip install -r requirements.txt
```
Las dependencias utilizadas actualmente son:
* **Flask**: servidor HTTP para el Gateway y la API.
* **requests**: envío de telemetría desde el Gateway hacia la API.

---

## 7. Instalar la extensión de Tinkercad

El prototipo utiliza la extensión `tinkercad-serial-bridge`[https://microsoftedge.microsoft.com/addons/detail/tinkercadserialbridge/madkdbjchopgbjhjnpoandbmmjfajcmb?hl=es-es]. La extensión permite capturar la salida del Monitor Serie de Tinkercad y enviarla a un servidor local.

1. Instalarla desde la tienda de extensiones compatible con el navegador utilizado.
2. Una vez instalada, abrir el circuito de ColdTrack en Tinkercad.
3. Abrir el popup de `tinkercad-serial-bridge`.
4. Configurar:
   ```text
   Server:
   http://127.0.0.1:8080

   Upload Interval(ms):
   2000

   Cmd Poll Interval(ms):
   2000

   Enable Bridge:
   ON
   ```
5. Presionar `Save Settings`.

El valor `2000 ms` es adecuado para el prototipo actual y evita generar tráfico HTTP innecesario durante las pruebas.
> **Nota:** Si se utilizaron anteriormente otras extensiones para capturar el Monitor Serie de Tinkercad, se recomienda deshabilitarlas para evitar interferencias.

---

## 8. Formato enviado por Arduino

El Arduino virtual genera una única línea JSON con la telemetría del equipo. Ejemplo:

```json
{
  "deviceId": "CT-001",
  "uptimeMs": 5070,
  "tempInterior": 0.8,
  "tempCondensador": 24.8,
  "tempSuccion": 24.8,
  "presionBaja": 47,
  "presionAlta": 10,
  "compresor": 100,
  "fanSolicitado": 0,
  "fanPWM": 0,
  "fanRpm": 0.0,
  "estado": "NORMAL",
  "diagnostico": "SIN_FALLAS",
  "proteccionAlta": false,
  "proteccionBaja": false,
  "ordenParoCompresor": false
}
```
En el Monitor Serie de Tinkercad el JSON aparece en una sola línea. El diagnóstico es determinado por Arduino. El Gateway no recalcula fallas: únicamente recibe, valida y transporta la información.

---

## 9. Ejecutar ColdTrack API

Abrir una **primera terminal** dentro del proyecto y activar el entorno virtual si todavía no está activo.

### Windows
```powershell
.venv\Scripts\Activate.ps1
python apy.py
```

### Linux/macOS
```bash
source .venv/bin/activate
python3 apy.py
```

Debería mostrarse algo similar a:
```text
==========================================
          COLDTRACK API
==========================================
Servidor: http://127.0.0.1:5000
Endpoint: POST /api/telemetry
==========================================
```
La API debe permanecer ejecutándose.

**Comprobar que la API funciona**
Abrir en el navegador: `http://127.0.0.1:5000/health`.
La respuesta esperada es similar a:
```json
{
  "service": "ColdTrack API",
  "status": "ok"
}
```

---

## 10. Ejecutar ColdTrack Gateway

Abrir una **segunda terminal** dentro de la carpeta del proyecto y activar nuevamente el entorno virtual.

### Windows
```powershell
.venv\Scripts\Activate.ps1
python gateway.py
```

### Linux/macOS
```bash
source .venv/bin/activate
python3 gateway.py
```

Debería mostrarse:
```text
==========================================
       COLDTRACK IOT GATEWAY
==========================================
Tinkercad:
http://127.0.0.1:8080

ColdTrack API:
http://127.0.0.1:5000/api/telemetry
==========================================
```
El Gateway debe permanecer ejecutándose junto con `apy.py`.

---

## 11. Iniciar la simulación de Tinkercad

Con `apy.py` y `gateway.py` ejecutándose:
1. Abrir el circuito ColdTrack en Tinkercad.
2. Abrir el Monitor en serie.
3. Confirmar que `tinkercad-serial-bridge` esté habilitado.
4. Verificar que el servidor configurado sea `http://127.0.0.1:8080`.
5. Presionar **Iniciar simulación**.

El Arduino comenzará a publicar líneas JSON en el Monitor Serie. La extensión enviará el contenido al Gateway mediante `GET /send?out=...`.

La misma extensión consulta periódicamente `GET /cmd`. Esta segunda ruta está reservada para una futura comunicación desde Python hacia Arduino. Actualmente devuelve una respuesta vacía y es normal observar estas consultas.

---

## 12. Flujo esperado

Cuando el Gateway recibe un JSON válido:
```text
Tinkercad → GET /send → gateway.py → parseo y deduplicación → POST /api/telemetry → apy.py
```

El Gateway debería mostrar, por ejemplo:
```text
==========================================
          COLDTRACK GATEWAY
==========================================
Dispositivo:  CT-001
Uptime:       4070 ms
Estado:       NORMAL
Diagnostico:  SIN_FALLAS
------------------------------------------
Temp interior:     0.8 C
Temp condensador:  24.8 C
Temp succion:      24.8 C
Presion baja:      47 %
Presion alta:      10 %
Compresor:         100 %
Fan solicitado:    0 %
Fan PWM:           0
Fan RPM:           0.0
------------------------------------------
Proteccion alta:   False
Proteccion baja:   False
Parar compresor:   False
==========================================

[API] Telemetria enviada correctamente (201)
```

En la terminal de la API debería aparecer:
```text
==========================================
          COLDTRACK API
==========================================
Dispositivo:  CT-001
Estado:       NORMAL
Diagnostico:  SIN_FALLAS
Recibido:     2026-...
==========================================
```
y la petición `POST /api/telemetry HTTP/1.1 201`.

---

## 13. Consultar la última telemetría

Mientras `apy.py` permanezca ejecutándose se puede consultar la última lectura de un dispositivo. Para `CT-001`:
```text
http://127.0.0.1:5000/api/telemetry/latest/CT-001
```

La API devolverá el último paquete registrado. Ejemplo:
```json
{
  "deviceId": "CT-001",
  "estado": "NORMAL",
  "diagnostico": "SIN_FALLAS",
  "receivedAt": "2026-08-21T09:33:39.583864+00:00"
}
```

---

## 14. Endpoints disponibles

### Gateway — puerto `8080`
| Método | Endpoint | Descripción |
| --- | --- | --- |
| `GET` | `/send` | Recibe la salida del Monitor Serie enviada por la extensión |
| `GET` | `/cmd` | Endpoint de polling reservado para futuros comandos hacia Arduino |

### ColdTrack API — puerto `5000`
| Método | Endpoint | Descripción |
| --- | --- | --- |
| `GET` | `/health` | Comprueba que la API esté disponible |
| `POST` | `/api/telemetry` | Recibe telemetría desde el Gateway |
| `GET` | `/api/telemetry/latest/<device_id>` | Devuelve la última lectura del dispositivo indicado |

---

## 15. Prueba recomendada

Para verificar el flujo completo:
1. Ejecutar `apy.py`.
2. Ejecutar `gateway.py`.
3. Abrir Tinkercad.
4. Activar `tinkercad-serial-bridge`.
5. Abrir el Monitor Serie.
6. Iniciar la simulación.
7. Modificar el sensor de temperatura interior (Por ejemplo: `24.8 °C`). Esto puede generar:
   ```text
   Estado: CRITICO
   Diagnostico: TEMPERATURA_ELEVADA
   ```
8. Luego modificarlo a un valor normal (por ejemplo: `0.8 °C`) y comprobar que el Gateway y la API reciben:
   ```text
   Estado: NORMAL
   Diagnostico: SIN_FALLAS
   ```

Si ambos cambios aparecen automáticamente en las dos terminales, el flujo está funcionando correctamente.

### Perfil y recuperación de contraseña

El propietario y el técnico autenticados consultan su cuenta con `GET /api/profile`. Devuelve `id`, `email`, `role`, `name` y `phone`. Con `PATCH /api/profile` pueden editar `name` y `phone`; enviar `{"phone":""}` borra el teléfono. Los datos de la sesión, el rol y el correo se mantienen.

Para recuperar una contraseña, habilitá el registro y configurá el proceso de correo. Enviá `POST /api/password-reset` con `{"email":"propietario@example.com"}`. La respuesta `202` es genérica. Si la cuenta existe, el servicio pone en cola un código con vigencia de diez minutos y espera al menos 60 segundos entre envíos para esa cuenta. Después enviá `POST /api/password-reset/confirm` con `email`, `code` de seis dígitos y `password` de 12 a 256 caracteres. El éxito devuelve `204`, consume el código y revoca las sesiones de la cuenta. Un código inválido, vencido o agotado devuelve `400`. Las solicitudes están limitadas por IP y los códigos admiten cinco intentos.

El primer arranque actualiza SQLite de forma idempotente. Las sesiones de cuentas emitidas antes de esta versión requieren un nuevo ingreso. Las credenciales del gateway y de la cuenta de demostración conservan su flujo.


## Funciones y verificación manual

El inventario de métodos, ejemplos y resultados esperados está en [PRUEBAS-MANUALES.md](PRUEBAS-MANUALES.md). Swagger UI se sirve en `/apidocs/` y el contrato OpenAPI en `/apispec_1.json`. El login crea una cookie HttpOnly. Para las operaciones de gateway y worker de correo usá la credencial Bearer correspondiente.

- Cuentas de propietario y técnico, edición de nombre y teléfono, y recuperación por correo. Las sesiones emitidas antes de incorporar su versionado deben iniciar sesión otra vez. El restablecimiento revoca las sesiones de la cuenta.
- Alta manual y QR de aprovisionamiento emitido y canjeado por el mismo propietario. El código dura 10 minutos y se consume una vez. El frontend podrá representar ese código en un QR.
- La captura puede pausarse por equipo, canal u horario. El gateway puede enviar su acuse junto con la revisión de la política. El acuse registra una declaración del dispositivo. El control físico requiere firmware compatible.
- Las horas de funcionamiento se estiman entre muestras con estado conocido y separación de hasta 360 segundos. Las muestras archivadas con ambos extremos activos cuentan como tiempo de compresor activo.
- Las preferencias de correo de alertas se guardan por equipo. Los códigos predefinidos se pueden silenciar por separado y el correo se puede desactivar en su conjunto. Las alertas quedan registradas.
- Mantenimiento con alta manual o desde alertas, cambios de estado por rol, historial y reseña del propietario al cerrar. El contrato anterior de solicitudes por alerta permanece accesible en `/api/reviews`.
- El asistente responde preguntas frecuentes de manera local. Las reglas de diagnóstico existentes siguen generando las alertas. El prototipo n8n/Gemini de diagnóstico asistido requiere validar casos y configurar sus servicios antes de integrarlo.

Hacé una copia verificada de SQLite antes de desplegar. Si se vuelve temporalmente al backend anterior a mantenimiento, evitá nuevas escrituras de solicitudes hasta conciliar los IDs y datos entre versiones. Conservá la base actual para recuperar las operaciones posteriores a la copia.

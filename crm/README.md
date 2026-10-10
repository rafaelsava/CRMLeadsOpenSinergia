# CRM OpenSinergia — MVP

CRM sencillo para la gerencia de OpenSinergia, con las automatizaciones en **n8n**.

- **El CRM** (Python + SQLite, sin dependencias) es el registro central y el front: leads, clientes, servicios, condiciones, pagos, historial e indicadores. Su diseño sigue el estilo de opensinergia.com.
- **n8n** ejecuta tres flujos:
  1. Leer el formulario web en Gmail.
  2. Enviar el resumen diario.
  3. Avisar si algún flujo falla.

```
Formulario web ─► Gmail ─(IMAP)─► n8n «Leads del formulario web» ─► CRM: crea el lead (sin duplicados)
                                                                └─► correo a gerencia: «Nuevo lead desde la web»

n8n «Resumen diario» (7:00, o botón «Enviar resumen» del CRM)
      ─► pide el resumen al CRM ─► lo envía a gerencia ─► confirma el envío al CRM (alimenta los KPIs)

n8n «Alerta de errores» ─► correo a gerencia si cualquiera de los flujos falla
```

## Qué resuelve del AS-IS (trazabilidad, criterio de control 2)

| Funcionalidad | Dónde | Problema del AS-IS | KPI que mueve |
|---|---|---|---|
| Lectura automática del formulario en Gmail, solo lectura | n8n | Transcripción manual del correo a Word o agenda | Tiempo de registro, % de transcripción manual |
| Aviso inmediato de lead nuevo | n8n | Pérdida de continuidad del lead (riesgo Alto) | % de leads con seguimiento |
| Registro central de leads y clientes, con buscador | CRM | Información repartida entre Word, agenda, Calendar y notas | Tiempo para localizar información |
| Tablero de estados + próxima acción con fecha | CRM | Seguimiento de memoria | % de leads con seguimiento |
| Servicios con plan, % de descuento, valor final y condiciones particulares | CRM | Descuentos y planes en notas dispersas | Tiempo para localizar información |
| Registro de pagos; la próxima fecha se calcula sola | CRM | Pagos controlados con Google Calendar y anotaciones | Clientes con pagos vencidos |
| Resumen diario por correo (pagos y seguimientos) | n8n + CRM | Recordatorios que dependen de la memoria (riesgo Alto) | Pagos vencidos, % de vencimientos alertados |
| Historial de eventos, indicadores y exportación a CSV | CRM | No se puede reconstruir el recorrido ni medir | Comparación AS-IS vs. TO-BE |

## 0. Modo demo con Docker (sin Gmail, para revisar el sistema completo)

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build
```

Levanta el sistema real (CRM + n8n 2.42.6) y además:
- `correo-entrante` (GreenMail): hace de bandeja de Gmail. Ahí llegan los formularios y n8n los lee por IMAP.
- `correo-saliente` (Mailpit): recibe los correos que n8n envía a gerencia y los muestra en http://localhost:8025.
- `formulario`: un formulario de contacto simulado con los campos de opensinergia.com, en http://localhost:8090.

Los contenedores de preparación hacen el resto solos:
- `demo-preparar`: carga datos ficticios y crea el usuario del CRM.
- `n8n-importar`: importa y publica los flujos.
- `demo-cuenta-n8n`: crea la cuenta del editor de n8n.

Accesos:

| Qué | Dónde | Acceso |
|---|---|---|
| CRM | http://localhost:8080 | `demo` / `demo2026` |
| n8n | http://localhost:5678 | `demo@opensinergia.demo` / `Demo2026!` |

Usa un proyecto de Docker separado (`opensinergia-crm-demo`), así que no se mezcla con una instalación real en la misma máquina. Para apagarla: `docker compose -f docker-compose.yml -f docker-compose.demo.yml down` (con `-v` borra sus datos).

## 1. Probarlo en local con datos ficticios (2 minutos, sin n8n)

```bash
cd crm
python3 app.py seed               # clientes y leads de prueba, todos marcados "(demo)"
python3 app.py adduser rafael     # pide la contraseña
python3 app.py serve              # http://127.0.0.1:8080
python3 app.py ingest-eml samples/formulario_ejemplo.eml   # simula un formulario recibido
```

## 2. Ponerlo en marcha en el VPS con n8n

Requisitos:
- Docker con `docker compose`.
- Python 3 en el servidor, solo para el instalador de flujos.
- En la cuenta de Gmail que recibe los formularios: **verificación en 2 pasos** activada y una **contraseña de aplicación** (myaccount.google.com → Seguridad → Contraseñas de aplicaciones).

```bash
cp .env.example .env              # complete los valores; ver abajo
docker compose up -d --build      # levanta el CRM (puerto 8080) y n8n (puerto 5678), solo en 127.0.0.1
docker compose exec crm python app.py adduser rafael
python3 n8n/instalar.py           # crea credenciales, importa los 3 flujos y los activa
```

Valores clave del `.env`:

| Variable | Qué poner |
|---|---|
| `N8N_API_TOKEN` | Token largo y aleatorio: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `N8N_FORM_SENDER` | Remitente fijo con el que llegan los correos del formulario |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | Cuenta de la empresa y su contraseña de aplicación |
| `SUMMARY_TO`, `SUMMARY_FROM`, `SUMMARY_HOUR` | A quién llegan los correos de gerencia, desde qué cuenta y a qué hora el resumen |
| `APP_URL` | URL pública del CRM, para los enlaces en los correos |

Después abra n8n y cree la cuenta de dueño (la primera vez lo pide). Los tres flujos ya aparecen activos. Mientras no haya HTTPS, abra n8n con un túnel SSH:

```bash
ssh -L 5678:127.0.0.1:5678 usuario@servidor   # luego abra http://localhost:5678
```

`instalar.py` se puede repetir sin problema, por ejemplo si cambia la contraseña de Gmail o el remitente: reescribe las mismas credenciales y flujos.

### Los flujos de n8n (`n8n/flujos/`)

**CRM · Leads del formulario web**
- **Correo del formulario (Gmail):** nodo Email Trigger (IMAP).
  - Escucha la bandeja de entrada y solo toma los correos de `N8N_FORM_SENDER`.
  - No los marca como leídos y recuerda cuáles ya procesó.
- **Registrar lead en el CRM:** HTTP, con 3 reintentos.
  - El CRM extrae Nombre completo, Correo Electrónico, Teléfono, Empresa, Servicio de interés y Mensaje.
  - Evita duplicados por `Message-ID`, correo y teléfono.
- **¿Es un lead nuevo?** Si es nuevo, va a **Avisar a gerencia** (correo con los datos y el enlace a la ficha). Si la persona ya existía, el mensaje queda en su historial.

**CRM · Resumen diario para gerencia**
- Se dispara **todos los días a las 7:00** o con el **botón «Enviar resumen»** del CRM (webhook protegido con el token).
- Hace tres pasos: pide el resumen al CRM, envía el correo y confirma el envío al CRM.
- Esa confirmación alimenta el indicador "vencimientos alertados antes de la fecha".

**CRM · Alerta de errores**
- Es el flujo de error de los otros dos.
- Si el CRM está caído, avisa por correo con el enlace a la ejecución fallida. En n8n se pulsa «Reintentar» y el lead se recupera.

Correos que no son del formulario (otro remitente) no entran. Si un formulario llega sin correo ni teléfono, el lead se crea marcado "Por revisar" y guarda el correo original.

## 3. Pasar a datos reales

```bash
docker compose exec crm python app.py reset --yes   # borra los datos de prueba (los usuarios se conservan)
```

Después cargue los clientes actuales desde **Clientes → Nuevo cliente**, con servicio, valor, % de descuento, periodicidad, próxima fecha de pago y condiciones.

## 4. HTTPS (recomendado)

Con Caddy en el VPS:

```
crm.opensinergia.com {
    reverse_proxy 127.0.0.1:8080
}
n8n.opensinergia.com {
    reverse_proxy 127.0.0.1:5678
}
```

Luego, en `.env`:

```
APP_URL=https://crm.opensinergia.com
COOKIE_SECURE=1
TRUST_PROXY=1
N8N_HOST=n8n.opensinergia.com
N8N_PROTOCOL=https
N8N_WEBHOOK_URL=https://n8n.opensinergia.com/
```

Aplique los cambios con `docker compose up -d`.

Copia de seguridad del CRM:

```bash
docker compose cp crm:/data/crm.db ./respaldo-crm.db
```

## 5. Guion de aceptación (demo de punta a punta)

1. **Lead automático:** llenar el formulario de opensinergia.com.
   - En segundos, n8n lo registra (pestaña Executions del flujo) y llega el correo «Nuevo lead desde la web».
   - En el CRM, el lead aparece en Leads → Nuevo, con origen Web y el mensaje original.
2. **Seguimiento:** moverlo a "Contactado" con próxima acción para hoy. Aparece en Hoy → Seguimientos.
3. **Resumen:** pulsar "Enviar resumen" en el CRM.
   - n8n ejecuta el flujo y llega el correo.
   - En Indicadores sube "Resúmenes enviados".
4. **Conversión:** "Convertir en cliente" y agregar el servicio con plan especial, 15% de descuento y pago en 2 días. Aparece en "Pagos que vencen" con el valor ya descontado.
5. **Pago:** registrar el pago. Sale de las alertas y la próxima fecha se calcula sola.
6. **Búsqueda y medición:**
   - Buscar al cliente (tecla `/`) y cronometrar cuánto toma reunir su información.
   - Guardar ese tiempo en Indicadores y exportar a CSV para el informe.

## Comandos del CRM

| Comando | Para qué |
|---|---|
| `python3 app.py serve [-v]` | Inicia el CRM |
| `python3 app.py adduser <usuario>` | Crea un usuario o cambia su contraseña |
| `python3 app.py seed` / `reset --yes` | Carga datos ficticios / borra los datos |
| `python3 app.py ingest-eml <archivo.eml>` | Procesa un correo como si hubiera llegado del formulario |
| `python3 app.py summary --preview` | Genera el HTML del resumen sin enviarlo |
| `python3 -m unittest discover -s tests -t .` | Corre las pruebas (39) |

En Docker, anteponga `docker compose exec crm` y use `python` en lugar de `python3`.

## API que usan los flujos de n8n

Todas requieren `Authorization: Bearer <N8N_API_TOKEN>`.

| Método y ruta | Uso |
|---|---|
| `POST /api/n8n/form-email` | Recibe el correo tal como lo entrega el nodo IMAP (formato *resolved*) y crea o actualiza el lead |
| `GET /api/n8n/summary` | Devuelve el resumen del día ya armado (asunto, texto, HTML, destinatarios) |
| `POST /api/n8n/summary-sent` | Registra que el resumen se envió |

## Modelo de datos

- `contacts`: prospectos y clientes. Guarda origen, estado, próxima acción, mensaje y correo original, y si se capturó de forma automática, manual o importada.
- `services`: servicios contratados por cliente (plan, valor base, % de descuento, condiciones, periodicidad, próxima fecha de pago).
- `payments`: pagos registrados (fecha en que vencía, fecha de pago, valor).
- `events`: historial de todo lo que pasa; es la fuente de los indicadores.
- `inbound_messages`: correos ya procesados (evita duplicados).

## Alternativa sin n8n

El CRM también puede leer Gmail y enviar el resumen por sí mismo. Para eso:
- Defina `GMAIL_SEARCH` en `.env`.
- Quite `N8N_SUMMARY_WEBHOOK` de `docker-compose.yml`.

Sirve como respaldo si n8n no está disponible.

## Fuera de este MVP (siguientes pasos)

- **Chatwoot en n8n:** un cuarto flujo con un nodo Webhook que reciba `conversation_created` de Chatwoot y llame al CRM. Así WhatsApp y redes también entran solos; hoy esos leads se registran con "Nuevo lead".
- **Recordatorio de pago directo al cliente**, como otro flujo de n8n. Por WhatsApp requiere plantillas aprobadas por Meta.
- **Aviso de privacidad (Ley 1581 de 2012)**, porque el CRM centraliza datos personales de prospectos y clientes.

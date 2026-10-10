# CRM Leads OpenSinergia

Proyecto de **Gerencia de Operaciones de TI** (Universidad de La Sabana, 2026): automatización del proceso CRM de OpenSinergia.

**Equipo:** Rafael Andrés Salcedo Valdivieso · Tomas Barrios Guevara · Fermin Alejandro Escalona Guillen · Santiago Urrego Rodriguez
**Profesor:** Mateo Barón Rojas

## Contenido del repositorio

| Ruta | Qué es |
|---|---|
| [`OpenSinergia_Entregable_1_Final_v2.pdf`](OpenSinergia_Entregable_1_Final_v2.pdf) | Entregable 1: acta de constitución, AS-IS, planeación, benchmarking y modelo de negocio (capítulos 1–5) |
| [`crm/`](crm/) | MVP funcional: CRM (registro central + front) y flujos de **n8n** para las automatizaciones |
| [`crm/README.md`](crm/README.md) | Cómo probarlo, desplegarlo en el VPS y el guion de demo |
| [`crm/n8n/flujos/`](crm/n8n/flujos/) | Los 3 flujos de n8n (JSON importable) |
| [`crm/docker-compose.demo.yml`](crm/docker-compose.demo.yml) | Modo demo: todo el sistema con correos de prueba, sin Gmail |

## El MVP en una frase

Un lead que llena el formulario de opensinergia.com entra solo al CRM vía n8n, con origen y estado. Gerencia lleva ahí el seguimiento y, cuando el lead se vuelve cliente, sus servicios, descuentos, condiciones y fechas de pago. Cada mañana llega un resumen con pagos vencidos o por vencer y seguimientos pendientes. La pantalla **Indicadores** compara la línea base AS-IS (§2.5) con el proceso nuevo, según las metas de la §5.4.

```
Formulario web ─► Gmail ─(IMAP)─► n8n ─► CRM (leads, clientes, pagos, historial, KPIs)
                                     └─► correo a gerencia (lead nuevo, resumen diario, alertas de error)
```

## Ver la demo completa con Docker (recomendado para revisar)

Levanta todo el sistema en su computador: CRM, n8n con los 3 flujos ya activos, un formulario de contacto simulado y correos de prueba en lugar de Gmail. No necesita contraseñas ni configurar nada.

Requisitos:
- Docker Desktop (o Docker Engine con `docker compose` 2.24 o superior).
- Los puertos 8080, 5678, 8025 y 8090 libres.
- La primera vez descarga unos 2 GB de imágenes.

```bash
git clone https://github.com/rafaelsava/CRMLeadsOpenSinergia.git
cd CRMLeadsOpenSinergia/crm
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build
```

En menos de un minuto abra **http://localhost:8090**:

| Qué | Dónde | Acceso |
|---|---|---|
| Formulario simulado + guía de la demo | http://localhost:8090 | — |
| CRM | http://localhost:8080 | `demo` / `demo2026` |
| n8n (los flujos) | http://localhost:5678 | `demo@opensinergia.demo` / `Demo2026!` |
| Bandeja de correo (lo que n8n envía a gerencia) | http://localhost:8025 | — |

Recorrido sugerido:
1. Envíe el formulario ("Usar datos de ejemplo" lo llena).
2. En segundos el lead aparece en el CRM (Leads → Nuevo) y el aviso «Nuevo lead desde la web» llega a la bandeja.
3. En n8n, la pestaña *Executions* de cada flujo muestra lo que pasó.
4. En el CRM, pulse **Enviar resumen** en la pantalla Hoy.

Para apagarla: `docker compose -f docker-compose.yml -f docker-compose.demo.yml down`. Agregue `-v` para borrar también los datos de la demo.

## Solo el CRM, sin Docker (2 minutos)

Requiere Python 3.9 o superior. No hay dependencias que instalar.

```bash
cd crm
python3 app.py seed               # datos de prueba, marcados "(demo)"
python3 app.py adduser <usuario>  # pide la contraseña
python3 app.py serve              # abrir http://127.0.0.1:8080
```

Despliegue real en el VPS con Docker + n8n + Gmail: ver [`crm/README.md`](crm/README.md).

## Qué revisar

- **Recorrido del front:** Hoy → Leads (tablero) → Clientes → ficha de un cliente → Indicadores.
- **Trazabilidad:** tabla *Qué resuelve del AS-IS* en `crm/README.md` (criterio de control 2 del documento).
- **Flujos de n8n:** importarlos o abrir los JSON en `crm/n8n/flujos/`.
- **Indicadores:** que coincidan con los KPIs y metas del Entregable 1.

## Pendiente para salir a producción

- Remitente exacto del formulario web (`N8N_FORM_SENDER`).
- Contraseña de aplicación del Gmail de la empresa.
- Cargar los clientes reales y validar el MVP con gerencia (criterio de control 5).

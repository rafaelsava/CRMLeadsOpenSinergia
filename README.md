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

## El MVP en una frase

Un lead que llena el formulario de opensinergia.com entra solo al CRM vía n8n, con origen y estado. Gerencia lleva ahí el seguimiento y, cuando el lead se vuelve cliente, sus servicios, descuentos, condiciones y fechas de pago. Cada mañana llega un resumen con pagos vencidos o por vencer y seguimientos pendientes. La pantalla **Indicadores** compara la línea base AS-IS (§2.5) con el proceso nuevo, según las metas de la §5.4.

```
Formulario web ─► Gmail ─(IMAP)─► n8n ─► CRM (leads, clientes, pagos, historial, KPIs)
                                     └─► correo a gerencia (lead nuevo, resumen diario, alertas de error)
```

## Probarlo en 2 minutos (local, con datos ficticios)

Requiere Python 3.9 o superior. No hay dependencias que instalar.

```bash
cd crm
python3 app.py seed               # datos de prueba, marcados "(demo)"
python3 app.py adduser <usuario>  # pide la contraseña
python3 app.py serve              # abrir http://127.0.0.1:8080
```

Despliegue completo con Docker + n8n: ver [`crm/README.md`](crm/README.md).

## Qué revisar

- **Recorrido del front:** Hoy → Leads (tablero) → Clientes → ficha de un cliente → Indicadores.
- **Trazabilidad:** tabla *Qué resuelve del AS-IS* en `crm/README.md` (criterio de control 2 del documento).
- **Flujos de n8n:** importarlos o abrir los JSON en `crm/n8n/flujos/`.
- **Indicadores:** que coincidan con los KPIs y metas del Entregable 1.

## Pendiente para salir a producción

- Remitente exacto del formulario web (`N8N_FORM_SENDER`).
- Contraseña de aplicación del Gmail de la empresa.
- Cargar los clientes reales y validar el MVP con gerencia (criterio de control 5).

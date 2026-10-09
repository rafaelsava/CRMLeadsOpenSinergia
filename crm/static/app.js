"use strict";

/* CRM OpenSinergia — front sin dependencias. Todo dato que viene del servidor pasa por esc(). */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const state = { me: null, options: null, leadSource: "", kpiSince: "" };

const STAGES = ["nuevo", "contactado", "propuesta", "ganado", "perdido"];
const STAGE_LABEL = { nuevo: "Nuevo", contactado: "Contactado", propuesta: "Propuesta", ganado: "Ganado", perdido: "Perdido" };
const PERIOD_LABEL = { mensual: "Mensual", bimestral: "Bimestral", trimestral: "Trimestral", semestral: "Semestral", anual: "Anual", unico: "Pago único" };
const PERIOD_MONTHS = { mensual: 1, bimestral: 2, trimestral: 3, semestral: 6, anual: 12, unico: 0 };
const EVENT_LABEL = {
  lead_creado: "Lead creado", cliente_creado: "Cliente creado", estado: "Cambio de estado",
  convertido: "Convertido en cliente", seguimiento: "Próxima acción", accion_hecha: "Acción realizada",
  nota: "Nota", servicio: "Servicio", pago: "Pago registrado", datos: "Datos actualizados",
  formulario: "Nuevo mensaje", revisar: "Por revisar", revisado: "Revisado",
};
const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const MONTHS_LONG = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const WEEKDAYS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];

/* ---------------------------------------------------------------- utilidades */

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const pad = (n) => String(n).padStart(2, "0");
const moneyFormat = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });
const money = (value) => moneyFormat.format(value || 0);
const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function parseISODate(iso) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}
function fmtDate(iso) {
  if (!iso) return "—";
  const d = parseISODate(iso);
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}
function fmtDateTime(iso) {
  return iso ? `${fmtDate(iso)}, ${iso.slice(11, 16)}` : "—";
}
function longDate(iso) {
  const d = parseISODate(iso);
  return `${WEEKDAYS[d.getDay()]} ${d.getDate()} de ${MONTHS_LONG[d.getMonth()]}`;
}
function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
function ago(hours) {
  if (hours < 1) return "hace minutos";
  if (hours < 48) return `hace ${hours} h`;
  return `hace ${Math.floor(hours / 24)} días`;
}
function dueText(days) {
  if (days == null) return "";
  if (days < 0) return `Vencido hace ${plural(-days, "día", "días")}`;
  if (days === 0) return "Vence hoy";
  if (days === 1) return "Vence mañana";
  return `Vence en ${days} días`;
}
const displayName = (c) => c.name || c.email || c.phone || "Sin nombre";
const finalPrice = (base, discount) => Math.round((Number(base) || 0) * (1 - (Number(discount) || 0) / 100));

function waLink(phone) {
  let digits = String(phone || "").replace(/\D/g, "");
  if (digits.length === 10) digits = "57" + digits;
  return `https://wa.me/${digits}`;
}

function paymentPill(status, days) {
  switch (status) {
    case "vencido": return `<span class="pill danger">${esc(dueText(days))}</span>`;
    case "por_vencer": return `<span class="pill warn">${esc(dueText(days))}</span>`;
    case "al_dia": return `<span class="pill ok">Al día</span>`;
    case "sin_pendiente": return `<span class="pill">Sin cobros pendientes</span>`;
    default: return `<span class="pill">Inactivo</span>`;
  }
}
function actionPill(c) {
  switch (c.action_status) {
    case "atrasada": return `<span class="pill danger">Atrasada ${plural(-c.action_days, "día", "días")}</span>`;
    case "hoy": return `<span class="pill warn">Para hoy</span>`;
    case "programada": return `<span class="pill info">${esc(fmtDate(c.next_action_date))}</span>`;
    default: return c.stale ? `<span class="pill warn">Sin próxima acción</span>` : `<span class="pill">Sin próxima acción</span>`;
  }
}
const errorBox = (message) => `<div class="notice danger" role="alert">${esc(message)}</div>`;

/* ----------------------------------------------------------------------- API */

class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

async function api(method, path, body) {
  const options = { method, headers: { "X-Requested-With": "crm" }, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, options);
  } catch {
    throw new ApiError("No hay conexión con el servidor", 0);
  }
  const data = (res.headers.get("content-type") || "").includes("application/json") ? await res.json() : null;
  if (res.status === 401 && path !== "/api/login") {
    state.me = null;
    renderLogin();
    throw new ApiError("La sesión venció. Ingrese de nuevo.", 401);
  }
  if (!res.ok) throw new ApiError((data && data.error) || `Error ${res.status}`, res.status, data);
  return data;
}

/* ------------------------------------------------------------ toast y modal */

let toastTimer;
function toast(message, kind = "ok") {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 3500);
}

function openModal({ title, body, submitLabel = "Guardar", onSubmit, onOpen, wide = false }) {
  const root = $("#modal-root");
  const lastFocus = document.activeElement;
  root.innerHTML = `
    <div class="modal-backdrop" data-backdrop>
      <form class="modal${wide ? " wide" : ""}" role="dialog" aria-modal="true" aria-labelledby="modal-title" novalidate>
        <header class="modal-head">
          <h2 id="modal-title">${esc(title)}</h2>
          <button type="button" class="icon-btn" data-close aria-label="Cerrar">×</button>
        </header>
        <div class="modal-body">${body}</div>
        <div class="form-error" role="alert" hidden></div>
        <footer class="modal-foot">
          <button type="button" class="btn" data-close>Cancelar</button>
          <button type="submit" class="btn btn-primary" data-submit>${esc(submitLabel)}</button>
        </footer>
      </form>
    </div>`;
  const form = $("form", root);
  const errorEl = $(".form-error", form);
  const submitBtn = $("[data-submit]", form);
  const close = () => {
    root.innerHTML = "";
    document.removeEventListener("keydown", onKey);
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  $$("[data-close]", root).forEach((el) => el.addEventListener("click", close));
  $("[data-backdrop]", root).addEventListener("mousedown", (e) => { if (e.target === e.currentTarget) close(); });
  const modal = {
    form, close,
    showError(html) { errorEl.innerHTML = html; errorEl.hidden = false; },
    setSubmitLabel(label) { submitBtn.textContent = label; },
  };
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.hidden = true;
    submitBtn.disabled = true;
    try {
      const values = Object.fromEntries(new FormData(form));
      const keepOpen = await onSubmit(values, modal);
      if (!keepOpen) close();
    } catch (err) {
      modal.showError(esc(err.message));
    } finally {
      submitBtn.disabled = false;
    }
  });
  if (onOpen) onOpen(modal);
  const first = $("input:not([type=hidden]), select, textarea", form);
  if (first) first.focus();
  return modal;
}

function field(label, name, value = "", { type = "text", placeholder = "", required = false, span = "", attrs = "" } = {}) {
  return `<label class="field ${span}"><span>${esc(label)}${required ? ' <b class="req">*</b>' : ""}</span>
    <input type="${type}" name="${name}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${required ? "required" : ""} ${attrs}></label>`;
}
function selectField(label, name, options, selected, { span = "" } = {}) {
  const opts = options.map(([value, text]) => `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(text)}</option>`).join("");
  return `<label class="field ${span}"><span>${esc(label)}</span><select name="${name}">${opts}</select></label>`;
}
function textareaField(label, name, value = "", { rows = 3, placeholder = "", span = "span-all" } = {}) {
  return `<label class="field ${span}"><span>${esc(label)}</span><textarea name="${name}" rows="${rows}" placeholder="${esc(placeholder)}">${esc(value)}</textarea></label>`;
}
const sourceOptions = () => state.options.sources.map((s) => [s, s]);

/* ----------------------------------------------------------- modales de negocio */

function serviceFields(s = {}, required = true) {
  return `<div class="form-grid">
    ${field("Servicio", "service_name", s.name || "", { required, span: "span-2", placeholder: "Ej.: Infraestructura administrada (VPS)" })}
    ${selectField("Plan", "plan", [["estandar", "Estándar"], ["especial", "Especial"]], s.plan || "estandar")}
    ${field("Valor base (COP)", "base_price", s.base_price ?? "", { type: "number", attrs: 'min="0" step="1000" inputmode="numeric"' })}
    ${field("Descuento (%)", "discount_pct", s.discount_pct ?? 0, { type: "number", attrs: 'min="0" max="100" step="0.5"' })}
    <div class="field"><span>Valor final</span><output class="price-preview" data-price-preview>—</output></div>
    ${selectField("Periodicidad", "period", Object.entries(PERIOD_LABEL), s.period || "mensual")}
    ${field("Próximo pago", "next_due_date", s.next_due_date || "", { type: "date" })}
    ${textareaField("Condiciones particulares", "conditions", s.conditions || "", { rows: 2, placeholder: "Descuentos pactados, tarifas congeladas, acuerdos de pago…" })}
  </div>`;
}
function bindPricePreview(form) {
  const update = () => {
    const out = $("[data-price-preview]", form);
    const base = form.elements.base_price.value;
    out.textContent = base === "" ? "—" : money(finalPrice(base, form.elements.discount_pct.value));
  };
  form.elements.base_price.addEventListener("input", update);
  form.elements.discount_pct.addEventListener("input", update);
  update();
}
function serviceFromValues(v) {
  return {
    name: v.service_name, plan: v.plan, base_price: v.base_price, discount_pct: v.discount_pct || 0,
    period: v.period, next_due_date: v.next_due_date || null, conditions: v.conditions,
  };
}

function openNewLeadModal() {
  let force = false;
  openModal({
    title: "Nuevo lead",
    submitLabel: "Crear lead",
    wide: true,
    body: `<p class="muted small">Para leads que llegan por WhatsApp, redes o referidos. Los del formulario web entran solos desde Gmail.</p>
      <div class="form-grid">
        ${field("Nombre", "name", "", { span: "span-2", placeholder: "Nombre de la persona" })}
        ${selectField("Origen", "source", sourceOptions(), "WhatsApp")}
        ${field("Teléfono", "phone", "", { type: "tel", placeholder: "300 000 0000" })}
        ${field("Correo", "email", "", { type: "email" })}
        ${field("Empresa", "company")}
        ${field("Servicio de interés", "interest", "", { span: "span-all" })}
        ${textareaField("Mensaje o necesidad", "message", "", { placeholder: "Lo que pidió el cliente" })}
        ${field("Próxima acción", "next_action", "", { span: "span-2", placeholder: "Ej.: Llamar para entender el proyecto" })}
        ${field("Fecha", "next_action_date", "", { type: "date" })}
      </div>`,
    async onSubmit(values, modal) {
      try {
        const res = await api("POST", "/api/contacts", { ...values, kind: "prospecto", force });
        toast("Lead creado");
        location.hash = `#/contacto/${res.id}`;
      } catch (err) {
        if (err.status !== 409) throw err;
        const d = err.data.duplicate;
        force = true;
        modal.setSubmitLabel("Crear de todas formas");
        modal.showError(`Ya existe un contacto con ese correo o teléfono: <a href="#/contacto/${Number(d.id)}">${esc(d.name || "ver contacto")}</a>${d.company ? " (" + esc(d.company) + ")" : ""}. Ábralo para no duplicar, o confirme para crear uno nuevo.`);
        return true;
      }
    },
  });
}

function openNewClientModal() {
  let force = false;
  openModal({
    title: "Nuevo cliente",
    submitLabel: "Crear cliente",
    wide: true,
    body: `<p class="muted small">Para cargar los clientes que OpenSinergia ya tiene, con su servicio y fecha de pago.</p>
      <h3 class="form-section">Cliente</h3>
      <div class="form-grid">
        ${field("Nombre", "name", "", { required: true, span: "span-2" })}
        ${selectField("Origen", "source", sourceOptions(), "Referido")}
        ${field("Empresa", "company")}
        ${field("Teléfono", "phone", "", { type: "tel" })}
        ${field("Correo", "email", "", { type: "email" })}
      </div>
      <h3 class="form-section">Servicio contratado <span class="muted small">(opcional)</span></h3>
      ${serviceFields({}, false)}`,
    onOpen: (modal) => bindPricePreview(modal.form),
    async onSubmit(values, modal) {
      const body = { kind: "cliente", force, name: values.name, source: values.source, company: values.company, phone: values.phone, email: values.email };
      if (values.service_name) body.service = serviceFromValues(values);
      try {
        const res = await api("POST", "/api/contacts", body);
        toast("Cliente creado");
        location.hash = `#/contacto/${res.id}`;
      } catch (err) {
        if (err.status !== 409) throw err;
        const d = err.data.duplicate;
        force = true;
        modal.setSubmitLabel("Crear de todas formas");
        modal.showError(`Ya existe un contacto con ese correo o teléfono: <a href="#/contacto/${Number(d.id)}">${esc(d.name || "ver contacto")}</a>. Ábralo para no duplicar, o confirme para crear uno nuevo.`);
        return true;
      }
    },
  });
}

function openServiceModal(contactId, service, onDone) {
  openModal({
    title: service ? "Editar servicio" : "Agregar servicio",
    submitLabel: service ? "Guardar cambios" : "Agregar servicio",
    wide: true,
    body: serviceFields(service || {}) + (service ? `<label class="check"><input type="checkbox" name="active" value="1" ${service.active ? "checked" : ""}> Servicio activo (si se desactiva, deja de generar alertas de pago)</label>` : ""),
    onOpen: (modal) => bindPricePreview(modal.form),
    async onSubmit(values) {
      const body = serviceFromValues(values);
      if (service) {
        body.active = values.active ? 1 : 0;
        await api("PATCH", `/api/services/${service.id}`, body);
        toast("Servicio actualizado");
      } else {
        await api("POST", `/api/contacts/${contactId}/services`, body);
        toast("Servicio agregado");
      }
      onDone();
    },
  });
}

function openPaymentModal(service, clientName, onDone) {
  const final = service.final_price ?? finalPrice(service.base_price, service.discount_pct);
  openModal({
    title: "Registrar pago",
    submitLabel: "Registrar pago",
    body: `<div class="pay-summary">
        <div><span class="muted small">Cliente</span><strong>${esc(clientName)}</strong></div>
        <div><span class="muted small">Servicio</span><strong>${esc(service.name)}</strong></div>
        <div><span class="muted small">Pago que vencía</span><strong>${esc(fmtDate(service.next_due_date))}</strong></div>
      </div>
      ${service.conditions ? `<p class="conditions small"><strong>Condiciones:</strong> ${esc(service.conditions)}</p>` : ""}
      <div class="form-grid">
        ${field("Valor pagado (COP)", "amount", final, { type: "number", attrs: 'min="0" step="1000" inputmode="numeric"' })}
        ${field("Fecha de pago", "paid_date", todayISO(), { type: "date", required: true })}
        ${field("Nota", "note", "", { span: "span-all", placeholder: "Ej.: transferencia Bancolombia" })}
      </div>
      <p class="muted small">${service.period === "unico" ? "Es un pago único: el servicio queda sin cobros pendientes." : `La próxima fecha de pago se calcula sola (${esc(PERIOD_LABEL[service.period].toLowerCase())}).`}</p>`,
    async onSubmit(values) {
      await api("POST", `/api/services/${service.id}/payments`, values);
      toast("Pago registrado");
      onDone();
    },
  });
}

function openActionModal(contact, { done = false } = {}, onDone) {
  const isLead = contact.kind === "prospecto";
  openModal({
    title: done ? "Acción realizada" : "Programar seguimiento",
    submitLabel: "Guardar",
    body: `${done ? `<p class="done-line">Hecho: <strong>${esc(contact.next_action || "seguimiento")}</strong></p>` : ""}
      <p class="muted small">${esc(displayName(contact))}${contact.company ? " · " + esc(contact.company) : ""}</p>
      <div class="form-grid">
        ${isLead ? selectField("Estado", "stage", STAGES.map((s) => [s, STAGE_LABEL[s]]), contact.stage) : ""}
        ${field(done ? "¿Qué sigue?" : "Próxima acción", "next_action", done ? "" : contact.next_action, { span: isLead ? "span-2" : "span-all", placeholder: "Ej.: Enviar propuesta" })}
        ${field("Fecha", "next_action_date", done ? "" : contact.next_action_date || "", { type: "date" })}
      </div>
      ${done ? '<p class="muted small">Si no hay un paso siguiente, deje la acción vacía.</p>' : ""}`,
    async onSubmit(values) {
      const body = { next_action: values.next_action, next_action_date: values.next_action_date || null, action_done: done };
      if (isLead) body.stage = values.stage;
      await api("PATCH", `/api/contacts/${contact.id}`, body);
      toast(body.stage === "ganado" && contact.stage !== "ganado" ? "Convertido en cliente" : "Seguimiento guardado");
      onDone();
    },
  });
}

/* --------------------------------------------------------------- login y shell */

function renderLogin() {
  $("#modal-root").innerHTML = "";
  $("#app").innerHTML = `
    <main class="login">
      <form class="login-card" novalidate>
        <img class="brand-logo lg" src="/static/logo.png" alt="OpenSinergia" width="72" height="72">
        <h1>CRM <span class="gradient-text">OpenSinergia</span></h1>
        <p class="muted">Ingrese con su usuario</p>
        ${field("Usuario", "username", "", { required: true, attrs: 'autocomplete="username" autocapitalize="none"' })}
        ${field("Contraseña", "password", "", { type: "password", required: true, attrs: 'autocomplete="current-password"' })}
        <div class="form-error" role="alert" hidden></div>
        <button class="btn btn-primary btn-block">Entrar</button>
      </form>
    </main>`;
  const form = $(".login-card");
  $("input", form).focus();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorEl = $(".form-error", form);
    errorEl.hidden = true;
    try {
      await api("POST", "/api/login", Object.fromEntries(new FormData(form)));
      await boot();
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.hidden = false;
    }
  });
}

function renderShell() {
  $("#app").innerHTML = `
    <header class="topbar">
      <a class="brand" href="#/hoy" aria-label="CRM OpenSinergia, inicio"><img class="brand-logo" src="/static/logo.png" alt="" width="38" height="38"><span class="brand-name">OpenSinergia<span class="brand-tag">CRM</span></span></a>
      <nav class="nav" aria-label="Principal">
        <a href="#/hoy" data-nav="hoy">Hoy</a>
        <a href="#/leads" data-nav="leads">Leads</a>
        <a href="#/clientes" data-nav="clientes">Clientes</a>
        <a href="#/indicadores" data-nav="indicadores">Indicadores</a>
      </nav>
      <div class="search" role="search">
        <input id="search" type="search" placeholder="Buscar nombre, empresa, teléfono…" autocomplete="off" aria-label="Buscar contactos" aria-controls="search-results">
        <kbd class="search-kbd" aria-hidden="true">/</kbd>
        <div class="search-results" id="search-results" role="listbox" hidden></div>
      </div>
      <div class="user"><span class="muted">${esc(state.me.username)}</span><button class="btn btn-ghost btn-sm" id="logout">Salir</button></div>
    </header>
    <main id="view" class="container" tabindex="-1"></main>`;
  $("#logout").addEventListener("click", async () => {
    await api("POST", "/api/logout").catch(() => {});
    state.me = null;
    renderLogin();
  });
  bindSearch();
}

function bindSearch() {
  const input = $("#search");
  const box = $("#search-results");
  let timer, results = [], active = -1;
  const hide = () => { box.hidden = true; active = -1; };
  const paint = () => {
    $$(".search-item", box).forEach((el, i) => el.classList.toggle("active", i === active));
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return hide();
    timer = setTimeout(async () => {
      results = await api("GET", `/api/search?q=${encodeURIComponent(q)}`).catch(() => []);
      active = results.length ? 0 : -1;
      box.innerHTML = results.length
        ? results.map((r) => `<a class="search-item" role="option" href="#/contacto/${Number(r.id)}">
            <strong>${esc(displayName(r))}</strong>
            <span class="muted small">${esc([r.company, r.kind === "cliente" ? "Cliente" : `Lead · ${STAGE_LABEL[r.stage]}`, r.phone].filter(Boolean).join(" · "))}</span></a>`).join("")
        : `<p class="search-empty muted">Sin resultados para “${esc(q)}”</p>`;
      box.hidden = false;
      paint();
    }, 180);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && results.length) { active = (active + 1) % results.length; paint(); e.preventDefault(); }
    else if (e.key === "ArrowUp" && results.length) { active = (active - 1 + results.length) % results.length; paint(); e.preventDefault(); }
    else if (e.key === "Enter" && active >= 0 && results[active]) { location.hash = `#/contacto/${results[active].id}`; input.value = ""; hide(); input.blur(); }
    else if (e.key === "Escape") { input.value = ""; hide(); input.blur(); }
  });
  input.addEventListener("blur", () => setTimeout(hide, 150));
  box.addEventListener("click", () => { input.value = ""; hide(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName) && !$("#modal-root").children.length) {
      e.preventDefault();
      input.focus();
    }
  });
}

/* ------------------------------------------------------------------- vista Hoy */

function integrationLine(st) {
  const g = st.gmail;
  const n = st.n8n;
  let gmail;
  if (n.enabled && !g.enabled) {
    const last = n.last_ingest;
    gmail = last
      ? `<span class="dot good"></span>Formulario web vía n8n · último correo procesado ${esc(fmtDateTime(last.at))}${last.name ? ` (${esc(last.name)})` : ""}`
      : `<span class="dot wait"></span>Formulario web vía n8n · esperando el primer correo`;
  } else if (!g.enabled) gmail = `<span class="dot off"></span>Lectura del formulario sin configurar`;
  else if (g.last_error) gmail = `<span class="dot bad"></span>Gmail con error: ${esc(g.last_error)}`;
  else if (g.last_run) {
    const r = g.last_result || {};
    gmail = `<span class="dot good"></span>Gmail revisado ${esc(fmtDateTime(g.last_run))}${r.created ? ` · ${plural(r.created, "lead nuevo", "leads nuevos")}` : ""}`;
  } else gmail = `<span class="dot wait"></span>Gmail conectado, primera revisión en curso`;
  const s = st.summary;
  let summary;
  const lastSummary = s.last_date ? ` · último: ${esc(fmtDate(s.last_date))}` : " · aún no se ha enviado";
  if (!s.enabled) summary = `<span class="dot off"></span>Envío del resumen sin configurar`;
  else if (s.last_error) summary = `<span class="dot bad"></span>Resumen con error: ${esc(s.last_error)}`;
  else if (s.via === "n8n") summary = `<span class="dot good"></span>Resumen diario vía n8n para ${esc(s.to.join(", "))}${lastSummary}`;
  else summary = `<span class="dot good"></span>Resumen diario a las ${s.hour}:00 para ${esc(s.to.join(", "))}${lastSummary}`;
  return `<p class="integration">${gmail}</p><p class="integration">${summary}</p>`;
}

function stat(id, label, value, tone, detail) {
  return `<a class="stat ${tone}" href="#${id}" data-jump="${id}"><span class="stat-label">${esc(label)}</span><span class="stat-value">${value}</span><span class="stat-detail">${detail}</span></a>`;
}

async function renderToday() {
  const d = await api("GET", "/api/today");
  const st = d.status;
  const overdueTotal = d.overdue.reduce((sum, s) => sum + s.final_price, 0);
  const soonTotal = d.due_soon.reduce((sum, s) => sum + s.final_price, 0);
  const late = d.actions.filter((c) => c.action_status === "atrasada").length;
  const stale = d.no_action.filter((c) => c.stale).length;

  const paymentRow = (s) => `<li class="row">
      <div class="row-main"><a class="row-title" href="#/contacto/${Number(s.contact_id)}">${esc(s.client_name)}</a>
        <span class="row-sub">${esc(s.name)} · ${esc(money(s.final_price))}</span></div>
      ${paymentPill(s.status, s.days)}
      <button class="btn btn-sm" data-pay="${Number(s.id)}">Registrar pago</button></li>`;
  const actionRow = (c) => `<li class="row">
      <div class="row-main"><a class="row-title" href="#/contacto/${Number(c.id)}">${esc(displayName(c))}</a>
        <span class="row-sub">${esc(c.next_action || "Seguimiento")}${c.kind === "prospecto" ? " · " + esc(STAGE_LABEL[c.stage]) : " · cliente"}</span></div>
      ${actionPill(c)}
      <button class="btn btn-sm" data-done="${Number(c.id)}">Hecho</button></li>`;
  const leadRow = (c) => `<li class="row">
      <div class="row-main"><a class="row-title" href="#/contacto/${Number(c.id)}">${esc(displayName(c))}</a>
        <span class="row-sub">${esc([c.source, c.interest].filter(Boolean).join(" · "))}</span></div>
      <span class="pill ${c.stale ? "warn" : ""}">Llegó ${esc(ago(c.age_hours))}</span>
      <button class="btn btn-sm" data-plan="${Number(c.id)}">Programar</button></li>`;
  const panel = (id, title, tone, items, rowFn, empty) => `
    <section class="panel" id="${id}">
      <div class="panel-head"><h2><span class="tone-bar ${tone}"></span>${esc(title)}</h2><span class="count">${items.length}</span></div>
      ${items.length ? `<ul class="rows">${items.map(rowFn).join("")}</ul>` : `<p class="empty">${esc(empty)}</p>`}
    </section>`;

  view().innerHTML = `
    <div class="page-head">
      <div><span class="eyebrow">Panel de gerencia</span><h1>${esc(capitalize(longDate(d.date)))}</h1>
        <p class="muted">${plural(d.new_leads_24h, "lead nuevo", "leads nuevos")} en las últimas 24 horas</p></div>
      <div class="actions">
        ${st.gmail.enabled ? '<button class="btn" id="run-gmail">Revisar Gmail ahora</button>' : ""}
        <a class="btn" href="/api/summary/preview" target="_blank" rel="noopener">Ver resumen del día</a>
        <button class="btn btn-primary" id="send-summary" ${st.summary.enabled ? "" : "disabled"}>Enviar resumen</button>
      </div>
    </div>
    <div class="stats">
      ${stat("p-overdue", "Pagos vencidos", d.overdue.length, d.overdue.length ? "danger" : "ok", d.overdue.length ? `${esc(money(overdueTotal))} · ${plural(d.overdue_clients, "cliente", "clientes")}` : "Todo al día")}
      ${stat("p-soon", `Vencen en ${d.due_soon_days} días`, d.due_soon.length, d.due_soon.length ? "warn" : "ok", d.due_soon.length ? esc(money(soonTotal)) : "Nada próximo")}
      ${stat("p-actions", "Seguimientos para hoy", d.actions.length, late ? "danger" : d.actions.length ? "warn" : "ok", late ? plural(late, "atrasado", "atrasados") : "Al día")}
      ${stat("p-noaction", "Leads sin próxima acción", d.no_action.length, stale ? "warn" : "neutral", stale ? `${stale} con más de ${d.stale_lead_hours} h` : "Recién llegados")}
    </div>
    ${d.review.length ? `<div class="notice warn">${plural(d.review.length, "formulario", "formularios")} sin correo ni teléfono: ${d.review.map((c) => `<a href="#/contacto/${Number(c.id)}">${esc(displayName(c))}</a>`).join(", ")}. Revise el correo original.</div>` : ""}
    <div class="grid-2">
      ${panel("p-overdue", "Pagos vencidos", "danger", d.overdue, paymentRow, "No hay pagos vencidos.")}
      ${panel("p-soon", `Pagos que vencen en ${d.due_soon_days} días`, "warn", d.due_soon, paymentRow, "No hay pagos próximos.")}
      ${panel("p-actions", "Seguimientos para hoy o atrasados", "info", d.actions, actionRow, "No hay seguimientos pendientes para hoy.")}
      ${panel("p-noaction", "Leads sin próxima acción", "neutral", d.no_action, leadRow, "Todos los leads abiertos tienen una próxima acción.")}
    </div>
    <footer class="integrations">${integrationLine(st)}</footer>`;

  const services = Object.fromEntries([...d.overdue, ...d.due_soon].map((s) => [s.id, s]));
  const contacts = Object.fromEntries([...d.actions, ...d.no_action].map((c) => [c.id, c]));
  $$("[data-pay]").forEach((b) => b.addEventListener("click", () => {
    const s = services[b.dataset.pay];
    openPaymentModal(s, s.client_name, renderToday);
  }));
  $$("[data-done]").forEach((b) => b.addEventListener("click", () => openActionModal(contacts[b.dataset.done], { done: true }, renderToday)));
  $$("[data-plan]").forEach((b) => b.addEventListener("click", () => openActionModal(contacts[b.dataset.plan], {}, renderToday)));
  $$("[data-jump]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    document.getElementById(a.dataset.jump).scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  $("#run-gmail")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Revisando…";
    try {
      const r = await api("POST", "/api/jobs/gmail");
      toast(r.busy ? "Ya se está revisando Gmail" : `Gmail: ${plural(r.found, "correo encontrado", "correos encontrados")}, ${plural(r.created, "lead nuevo", "leads nuevos")}`);
    } catch (err) {
      toast(err.message, "error");
    }
    renderToday();
  });
  $("#send-summary").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const r = await api("POST", "/api/jobs/summary");
      toast(r.via === "n8n" ? `n8n está enviando el resumen a ${r.to.join(", ")}` : `Resumen enviado a ${r.to.join(", ")}`);
    } catch (err) {
      toast(err.message, "error");
    }
    renderToday();
  });
}

/* ------------------------------------------------------------------ vista Leads */

async function renderLeads() {
  const all = await api("GET", "/api/leads");
  const leads = state.leadSource ? all.filter((c) => c.source === state.leadSource) : all;
  const auto = all.filter((c) => c.capture !== "manual").length;
  const columns = STAGES.map((stage) => {
    let items = leads.filter((c) => c.stage === stage);
    const closed = stage === "ganado" || stage === "perdido";
    const hidden = closed ? Math.max(0, items.length - 12) : 0;
    if (closed) items = items.slice(0, 12);
    return `<section class="column" data-stage="${stage}" aria-label="${STAGE_LABEL[stage]}">
      <header class="column-head"><h2>${STAGE_LABEL[stage]}</h2><span class="count">${items.length + hidden}</span></header>
      <div class="column-body">
        ${items.map((c) => `<article class="card lead-card" draggable="true" data-id="${Number(c.id)}">
            <a class="card-title" href="#/contacto/${Number(c.id)}">${esc(displayName(c))}</a>
            ${c.company || c.interest ? `<p class="card-sub">${esc([c.company, c.interest].filter(Boolean).join(" · "))}</p>` : ""}
            <div class="card-meta"><span class="chip">${esc(c.source)}</span>${c.capture !== "manual" ? '<span class="chip auto" title="Registrado automáticamente desde Gmail">Auto</span>' : ""}${c.needs_review ? '<span class="pill warn">Revisar</span>' : ""}</div>
            ${closed ? "" : `<div class="card-action">${c.next_action ? `<span class="small">${esc(c.next_action)}</span>` : ""}${actionPill(c)}</div>`}
          </article>`).join("") || `<p class="column-empty">Sin leads</p>`}
        ${hidden ? `<p class="column-empty">y ${hidden} más</p>` : ""}
      </div></section>`;
  }).join("");

  view().innerHTML = `
    <div class="page-head">
      <div><span class="eyebrow">Embudo comercial</span><h1>Leads</h1><p class="muted">${plural(all.length, "lead", "leads")} · ${auto} registrados automáticamente. Arrastre una tarjeta para cambiar su estado.</p></div>
      <div class="actions">
        <label class="inline-field"><span class="muted small">Origen</span>
          <select id="lead-source"><option value="">Todos</option>${state.options.sources.map((s) => `<option ${s === state.leadSource ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></label>
        <button class="btn btn-primary" id="new-lead">Nuevo lead</button>
      </div>
    </div>
    <div class="board">${columns}</div>`;

  $("#new-lead").addEventListener("click", openNewLeadModal);
  $("#lead-source").addEventListener("change", (e) => { state.leadSource = e.target.value; renderLeads(); });
  bindBoardDrag();
}

function bindBoardDrag() {
  let dragged = null;
  $$(".lead-card").forEach((card) => {
    card.addEventListener("dragstart", (e) => {
      dragged = card;
      card.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", card.dataset.id);
    });
    card.addEventListener("dragend", () => { card.classList.remove("dragging"); dragged = null; });
  });
  $$(".column").forEach((col) => {
    col.addEventListener("dragover", (e) => { if (dragged) { e.preventDefault(); col.classList.add("drop"); } });
    col.addEventListener("dragleave", (e) => { if (!col.contains(e.relatedTarget)) col.classList.remove("drop"); });
    col.addEventListener("drop", async (e) => {
      e.preventDefault();
      col.classList.remove("drop");
      const id = e.dataTransfer.getData("text/plain");
      const stage = col.dataset.stage;
      if (!id || dragged?.closest(".column") === col) return;
      if (stage === "ganado" && !confirm("¿Convertir este lead en cliente?")) return;
      try {
        await api("PATCH", `/api/contacts/${id}`, { stage });
        toast(stage === "ganado" ? "Convertido en cliente" : `Movido a ${STAGE_LABEL[stage]}`);
      } catch (err) {
        toast(err.message, "error");
      }
      renderLeads();
    });
  });
}

/* --------------------------------------------------------------- vista Clientes */

const STATUS_ORDER = { vencido: 0, por_vencer: 1, al_dia: 2, sin_pendiente: 3, inactivo: 4, ninguno: 5 };

async function renderClients() {
  const clients = await api("GET", "/api/clients");
  const rows = [];
  for (const c of clients) {
    if (!c.services.length) rows.push({ c, s: null, order: STATUS_ORDER.ninguno });
    for (const s of c.services) rows.push({ c, s, order: STATUS_ORDER[s.status] });
  }
  rows.sort((a, b) => a.order - b.order || (a.s?.next_due_date || "9").localeCompare(b.s?.next_due_date || "9") || a.c.name.localeCompare(b.c.name));
  const active = clients.flatMap((c) => c.services).filter((s) => s.active);
  const monthly = active.reduce((sum, s) => sum + (PERIOD_MONTHS[s.period] ? s.final_price / PERIOD_MONTHS[s.period] : 0), 0);
  const overdue = new Set(active.filter((s) => s.status === "vencido").map((s) => s.contact_id)).size;

  const body = rows.map(({ c, s }) => `<tr>
      <td><a class="row-title" href="#/contacto/${Number(c.id)}">${esc(c.name)}</a>${c.company && c.company !== c.name ? `<div class="muted small">${esc(c.company)}</div>` : ""}</td>
      ${s ? `<td>${esc(s.name)}<div class="muted small">${esc(PERIOD_LABEL[s.period])}${s.plan === "especial" ? ' · <span class="pill accent">Plan especial</span>' : ""}</div></td>
        <td class="conditions-cell">${s.conditions ? esc(s.conditions) : '<span class="muted">—</span>'}</td>
        <td class="num">${esc(money(s.final_price))}${s.discount_pct ? `<div class="muted small"><s>${esc(money(s.base_price))}</s> −${esc(s.discount_pct)}%</div>` : ""}</td>
        <td>${esc(fmtDate(s.next_due_date))}<div>${paymentPill(s.status, s.days)}</div></td>
        <td class="actions-cell">${s.active && s.next_due_date ? `<button class="btn btn-sm" data-pay="${Number(s.id)}">Registrar pago</button>` : ""}</td>`
      : `<td colspan="4" class="muted">Sin servicios registrados</td><td class="actions-cell"><button class="btn btn-sm" data-add-service="${Number(c.id)}">Agregar servicio</button></td>`}
    </tr>`).join("");

  view().innerHTML = `
    <div class="page-head">
      <div><span class="eyebrow">Cartera y pagos</span><h1>Clientes</h1><p class="muted">${plural(clients.length, "cliente", "clientes")} · ${plural(active.length, "servicio activo", "servicios activos")} · ingreso mensual estimado ${esc(money(monthly))}${overdue ? ` · <strong class="text-danger">${plural(overdue, "cliente con pagos vencidos", "clientes con pagos vencidos")}</strong>` : ""}</p></div>
      <div class="actions"><button class="btn btn-primary" id="new-client">Nuevo cliente</button></div>
    </div>
    ${rows.length ? `<div class="table-wrap"><table class="table">
      <thead><tr><th>Cliente</th><th>Servicio</th><th>Condiciones particulares</th><th class="num">Valor</th><th>Próximo pago</th><th><span class="sr-only">Acciones</span></th></tr></thead>
      <tbody>${body}</tbody></table></div>`
      : `<div class="empty-state"><h2>Aún no hay clientes</h2><p class="muted">Cargue los clientes actuales con su servicio, descuento y fecha de pago para empezar a recibir alertas.</p><button class="btn btn-primary" id="new-client-empty">Cargar el primer cliente</button></div>`}`;

  const byId = Object.fromEntries(rows.filter((r) => r.s).map((r) => [r.s.id, r]));
  $$("[data-pay]").forEach((b) => b.addEventListener("click", () => {
    const { c, s } = byId[b.dataset.pay];
    openPaymentModal(s, c.name, renderClients);
  }));
  $$("[data-add-service]").forEach((b) => b.addEventListener("click", () => openServiceModal(b.dataset.addService, null, renderClients)));
  $("#new-client").addEventListener("click", openNewClientModal);
  $("#new-client-empty")?.addEventListener("click", openNewClientModal);
}

/* ---------------------------------------------------------------- vista Contacto */

function serviceCard(s) {
  const last = s.payments[0];
  return `<article class="service${s.active ? "" : " inactive"}">
    <div class="service-head">
      <div><h3>${esc(s.name)}</h3>
        <p class="muted small">${esc(PERIOD_LABEL[s.period])}${s.plan === "especial" ? ' · <span class="pill accent">Plan especial</span>' : ""}</p></div>
      ${paymentPill(s.status, s.days)}
    </div>
    <dl class="facts">
      <div><dt>Valor</dt><dd>${esc(money(s.final_price))}${s.discount_pct ? ` <span class="muted small"><s>${esc(money(s.base_price))}</s> −${esc(s.discount_pct)}%</span>` : ""}</dd></div>
      <div><dt>Próximo pago</dt><dd>${esc(fmtDate(s.next_due_date))}</dd></div>
      <div><dt>Último pago</dt><dd>${last ? `${esc(fmtDate(last.paid_date))} · ${esc(money(last.amount))}` : "—"}</dd></div>
    </dl>
    ${s.conditions ? `<p class="conditions"><strong>Condiciones:</strong> ${esc(s.conditions)}</p>` : ""}
    <div class="service-actions">
      ${s.active && s.next_due_date ? `<button class="btn btn-primary btn-sm" data-pay="${Number(s.id)}">Registrar pago</button>` : ""}
      <button class="btn btn-sm" data-edit-service="${Number(s.id)}">Editar</button>
    </div>
    ${s.payments.length ? `<details class="payments"><summary>Historial de pagos (${s.payments.length})</summary>
      <table class="table compact"><thead><tr><th>Vencía</th><th>Pagado</th><th class="num">Valor</th><th>Nota</th></tr></thead><tbody>
      ${s.payments.map((p) => {
        const late = p.due_date ? Math.round((parseISODate(p.paid_date) - parseISODate(p.due_date)) / 86400000) : 0;
        return `<tr><td>${esc(fmtDate(p.due_date))}</td><td>${esc(fmtDate(p.paid_date))}${late > 0 ? ` <span class="pill danger">${late} d tarde</span>` : ""}</td><td class="num">${esc(money(p.amount))}</td><td>${esc(p.note)}</td></tr>`;
      }).join("")}</tbody></table></details>` : ""}
  </article>`;
}

async function renderContact(id) {
  const c = await api("GET", `/api/contacts/${id}`);
  const isClient = c.kind === "cliente";
  const reload = () => renderContact(id);
  setActiveNav(isClient ? "clientes" : "leads");

  view().innerHTML = `
    <a class="back" href="${isClient ? "#/clientes" : "#/leads"}">← ${isClient ? "Clientes" : "Leads"}</a>
    <div class="page-head">
      <div>
        <span class="eyebrow">${isClient ? "Ficha del cliente" : "Ficha del lead"}</span>
        <h1>${esc(displayName(c))}</h1>
        <div class="chips">
          <span class="pill ${isClient ? "ok" : "info"}">${isClient ? "Cliente" : "Lead · " + esc(STAGE_LABEL[c.stage])}</span>
          <span class="chip">Origen: ${esc(c.source)}</span>
          ${c.capture === "auto" ? '<span class="chip auto">Registrado automáticamente</span>' : c.capture === "importado" ? '<span class="chip auto">Importado de Gmail</span>' : ""}
          <span class="muted small">Llegó ${esc(fmtDateTime(c.received_at || c.created_at))}</span>
        </div>
      </div>
      <div class="actions">
        ${c.phone ? `<a class="btn" href="${esc(waLink(c.phone))}" target="_blank" rel="noopener">WhatsApp</a>` : ""}
        ${c.email ? `<a class="btn" href="mailto:${esc(c.email)}">Escribir correo</a>` : ""}
        ${isClient ? "" : '<button class="btn btn-primary" id="convert">Convertir en cliente</button>'}
      </div>
    </div>
    <div class="detail-grid">
      <div class="stack">
        ${c.needs_review ? `<section class="notice warn"><strong>Revise este lead.</strong> El formulario no traía correo ni teléfono. Complete los datos con el correo original y márquelo como revisado. <button class="btn btn-sm" id="mark-reviewed">Marcar como revisado</button></section>` : ""}
        <section class="panel">
          <div class="panel-head"><h2>Seguimiento</h2>${actionPill(c)}</div>
          <form id="follow-form" class="form-grid">
            ${isClient ? "" : selectField("Estado", "stage", STAGES.map((s) => [s, STAGE_LABEL[s]]), c.stage)}
            ${field("Próxima acción", "next_action", c.next_action, { span: "span-2", placeholder: "Ej.: Llamar para enviar la propuesta" })}
            ${field("Fecha", "next_action_date", c.next_action_date || "", { type: "date" })}
            <div class="form-actions span-all">
              ${c.next_action ? '<button type="button" class="btn" id="action-done">Marcar como hecha</button>' : ""}
              <button class="btn btn-primary">Guardar seguimiento</button>
            </div>
          </form>
        </section>
        ${isClient ? `<section class="panel">
          <div class="panel-head"><h2>Servicios, condiciones y pagos</h2><button class="btn btn-sm" id="add-service">Agregar servicio</button></div>
          ${c.services.length ? c.services.map(serviceCard).join("") : '<p class="empty">Este cliente aún no tiene servicios. Agregue uno para controlar sus pagos.</p>'}
        </section>` : ""}
        ${c.message || c.raw_email ? `<section class="panel">
          <h2>${c.capture === "manual" ? "Necesidad del cliente" : "Mensaje del formulario"}</h2>
          ${c.message ? `<p class="message">${esc(c.message)}</p>` : ""}
          ${c.raw_email ? `<details><summary>Ver correo original</summary><pre class="raw">${esc(c.raw_email)}</pre></details>` : ""}
        </section>` : ""}
        <section class="panel">
          <h2>Datos de contacto</h2>
          <form id="data-form" class="form-grid">
            ${field("Nombre", "name", c.name, { span: "span-2" })}
            ${selectField("Origen", "source", sourceOptions(), c.source)}
            ${field("Teléfono", "phone", c.phone, { type: "tel" })}
            ${field("Correo", "email", c.email, { type: "email" })}
            ${field("Empresa", "company", c.company)}
            ${field("Servicio de interés", "interest", c.interest, { span: "span-all" })}
            <div class="form-actions span-all"><button class="btn">Guardar datos</button></div>
          </form>
        </section>
      </div>
      <aside class="stack">
        <section class="panel">
          <h2>Historial</h2>
          <form id="note-form" class="note-form">
            <textarea name="text" rows="2" placeholder="Agregar nota: llamada, reunión, acuerdo…" aria-label="Nueva nota"></textarea>
            <button class="btn btn-sm">Agregar nota</button>
          </form>
          <ol class="timeline">
            ${c.events.map((ev) => `<li class="ev ev-${esc(ev.type)}">
              <div class="ev-head"><strong>${esc(EVENT_LABEL[ev.type] || ev.type)}</strong><time>${esc(fmtDateTime(ev.created_at))}</time></div>
              <p>${esc(ev.detail)}</p>${ev.username ? `<span class="muted small">${esc(ev.username)}</span>` : ""}</li>`).join("")}
          </ol>
        </section>
        <button class="btn btn-danger-ghost" id="delete-contact">Eliminar contacto</button>
      </aside>
    </div>`;

  $("#follow-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const v = Object.fromEntries(new FormData(e.target));
    try {
      await api("PATCH", `/api/contacts/${id}`, { ...v, next_action_date: v.next_action_date || null });
      toast(v.stage === "ganado" && c.stage !== "ganado" ? "Convertido en cliente" : "Seguimiento guardado");
      reload();
    } catch (err) { toast(err.message, "error"); }
  });
  $("#action-done")?.addEventListener("click", () => openActionModal(c, { done: true }, reload));
  $("#data-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("PATCH", `/api/contacts/${id}`, Object.fromEntries(new FormData(e.target)));
      toast("Datos guardados");
      reload();
    } catch (err) { toast(err.message, "error"); }
  });
  $("#note-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = e.target.elements.text.value.trim();
    if (!text) return;
    try {
      await api("POST", `/api/contacts/${id}/notes`, { text });
      reload();
    } catch (err) { toast(err.message, "error"); }
  });
  $("#mark-reviewed")?.addEventListener("click", async () => {
    await api("PATCH", `/api/contacts/${id}`, { needs_review: false });
    reload();
  });
  $("#convert")?.addEventListener("click", async () => {
    try {
      await api("PATCH", `/api/contacts/${id}`, { stage: "ganado" });
      toast("Convertido en cliente. Agregue el servicio contratado.");
      await reload();
      openServiceModal(id, null, reload);
    } catch (err) { toast(err.message, "error"); }
  });
  $("#add-service")?.addEventListener("click", () => openServiceModal(id, null, reload));
  const services = Object.fromEntries(c.services.map((s) => [s.id, s]));
  $$("[data-pay]").forEach((b) => b.addEventListener("click", () => openPaymentModal(services[b.dataset.pay], displayName(c), reload)));
  $$("[data-edit-service]").forEach((b) => b.addEventListener("click", () => openServiceModal(id, services[b.dataset.editService], reload)));
  $("#delete-contact").addEventListener("click", async () => {
    if (!confirm(`¿Eliminar a ${displayName(c)} con todo su historial, servicios y pagos? No se puede deshacer.`)) return;
    await api("DELETE", `/api/contacts/${id}`);
    toast("Contacto eliminado");
    location.hash = isClient ? "#/clientes" : "#/leads";
  });
}

/* ------------------------------------------------------------- vista Indicadores */

function fmtKpi(value, unit) {
  if (value == null) return "—";
  if (unit === "%") return `${value}%`;
  if (unit === "min") return `${String(value).replace(".", ",")} min`;
  if (unit === "h") return `${String(value).replace(".", ",")} h`;
  return String(value);
}
function metPill(row) {
  if (row.met === true) return '<span class="pill ok">Cumple</span>';
  if (row.met === false) return '<span class="pill danger">No cumple</span>';
  return row.current == null ? '<span class="pill">Sin datos</span>' : '<span class="pill info">Informativo</span>';
}

async function renderKpis() {
  const k = await api("GET", `/api/kpis${state.kpiSince ? `?desde=${encodeURIComponent(state.kpiSince)}` : ""}`);
  const t = k.totals;
  const maxSource = Math.max(1, ...k.by_source.map(([, n]) => n));
  view().innerHTML = `
    <div class="page-head">
      <div><span class="eyebrow">AS-IS vs. TO-BE</span><h1>Indicadores</h1><p class="muted">Línea base AS-IS (Entregable 1, §2.5) frente al proceso con el CRM. Metas de la §5.4.</p></div>
      <div class="actions">
        <label class="inline-field"><span class="muted small">Medir desde</span><input type="date" id="kpi-since" value="${esc(state.kpiSince)}"></label>
        ${state.kpiSince ? '<button class="btn btn-ghost btn-sm" id="kpi-clear">Todo el periodo</button>' : ""}
      </div>
    </div>
    <div class="table-wrap"><table class="table kpis">
      <thead><tr><th>Indicador</th><th class="num">Línea base</th><th class="num">Con el CRM</th><th>Meta</th><th>Estado</th></tr></thead>
      <tbody>${k.rows.map((r) => `<tr>
        <td><strong>${esc(r.name)}</strong><div class="muted small">${esc(r.note)}</div></td>
        <td class="num">${r.baseline == null ? '<span class="muted small">No se medía</span>' : esc(fmtKpi(r.baseline, r.unit))}</td>
        <td class="num">${r.manual ? `<form class="measure" data-key="${esc(r.key)}"><input type="number" name="value" min="0" step="0.1" value="${r.current ?? ""}" aria-label="Valor medido (${esc(r.unit)})"><span class="small muted">${esc(r.unit)}</span><button class="btn btn-sm">Guardar</button></form>${r.measured_on ? `<div class="muted small">medido ${esc(fmtDate(r.measured_on))}</div>` : ""}` : `<strong class="kpi-value">${esc(fmtKpi(r.current, r.unit))}</strong>`}</td>
        <td>${esc(r.target)}</td>
        <td>${metPill(r)}</td></tr>`).join("")}</tbody></table></div>
    <div class="grid-2 kpi-extra">
      <section class="panel">
        <h2>Leads ${k.since ? `desde el ${esc(fmtDate(k.since))}` : "registrados"}</h2>
        <div class="mini-stats">
          <div><span class="stat-value">${t.leads}</span><span class="muted small">Leads</span></div>
          <div><span class="stat-value">${t.auto}</span><span class="muted small">Automáticos</span></div>
          <div><span class="stat-value">${t.manual}</span><span class="muted small">Manuales</span></div>
          <div><span class="stat-value">${t.won}</span><span class="muted small">Ganados</span></div>
          <div><span class="stat-value">${t.lost}</span><span class="muted small">Perdidos</span></div>
          <div><span class="stat-value">${t.summaries_sent}</span><span class="muted small">Resúmenes enviados</span></div>
        </div>
        ${t.imported ? `<p class="muted small">${plural(t.imported, "lead importado", "leads importados")} del historial de Gmail (no cuentan para tiempos ni transcripción).</p>` : ""}
        <h3 class="form-section">Por origen</h3>
        ${k.by_source.length ? `<ul class="bars">${k.by_source.map(([source, n]) => `<li><span>${esc(source)}</span><span class="bar"><span data-w="${(100 * n) / maxSource}"></span></span><span class="num">${n}</span></li>`).join("")}</ul>` : '<p class="empty">Sin leads en el periodo.</p>'}
      </section>
      <section class="panel">
        <h2>Exportar datos</h2>
        <p class="muted small">CSV para el informe y para recalcular los indicadores en Excel.</p>
        <div class="export-links">
          <a class="btn" href="/api/export/contactos.csv">Contactos</a>
          <a class="btn" href="/api/export/servicios.csv">Servicios</a>
          <a class="btn" href="/api/export/pagos.csv">Pagos</a>
          <a class="btn" href="/api/export/eventos.csv">Historial</a>
        </div>
      </section>
    </div>`;

  $$(".bars [data-w]").forEach((el) => { el.style.width = `${el.dataset.w}%`; });
  $("#kpi-since").addEventListener("change", (e) => { state.kpiSince = e.target.value; renderKpis(); });
  $("#kpi-clear")?.addEventListener("click", () => { state.kpiSince = ""; renderKpis(); });
  $$("form.measure").forEach((form) => form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("PUT", "/api/kpis/manual", { key: form.dataset.key, value: form.elements.value.value, desde: state.kpiSince || null });
      toast("Medición guardada");
      renderKpis();
    } catch (err) { toast(err.message, "error"); }
  }));
}

/* --------------------------------------------------------------------- router */

const view = () => $("#view");
function setActiveNav(name) {
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
}
const ROUTES = [
  [/^#\/hoy$/, "hoy", renderToday],
  [/^#\/leads$/, "leads", renderLeads],
  [/^#\/clientes$/, "clientes", renderClients],
  [/^#\/indicadores$/, "indicadores", renderKpis],
  [/^#\/contacto\/(\d+)$/, null, (m) => renderContact(Number(m[1]))],
];

async function router() {
  if (!state.me) return;
  $("#modal-root").innerHTML = "";
  const hash = location.hash || "#/hoy";
  for (const [pattern, nav, render] of ROUTES) {
    const match = hash.match(pattern);
    if (!match) continue;
    if (nav) setActiveNav(nav);
    try {
      await render(match);
      window.scrollTo(0, 0);
    } catch (err) {
      if (err.status !== 401) view().innerHTML = errorBox(err.message);
    }
    return;
  }
  location.hash = "#/hoy";
}

async function boot() {
  try {
    const me = await api("GET", "/api/me");
    state.me = me;
    state.options = me.options;
    renderShell();
    await router();
  } catch (err) {
    if (err.status !== 401) $("#app").innerHTML = `<main class="container">${errorBox(err.message)}</main>`;
  }
}

window.addEventListener("hashchange", router);
boot();

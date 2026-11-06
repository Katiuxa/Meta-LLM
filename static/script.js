const BACKEND_URL = "https://raspserver.tail7d29c9.ts.net";

const API = BACKEND_URL.includes("IP_DE_TAILSCALE") ? "" : BACKEND_URL.replace(/\/$/, "");
const STORE = "bot-ia-history";
const TOKEN_KEY = "bot-ia-token";

const login = document.getElementById("login");
const loginForm = document.getElementById("login-form");
const app = document.getElementById("app");
const log = document.getElementById("log");
const model = document.getElementById("model");
const modelDescription = document.getElementById("model-description");
const headerModel = document.getElementById("header-model");
const text = document.getElementById("text");
const preview = document.getElementById("preview");
const drawer = document.getElementById("drawer");
const backdrop = document.getElementById("backdrop");
const menuBtn = document.getElementById("menu-btn");

let pendingFile = null;
let history = [];

marked.setOptions({
  highlight: function (code, lang) {
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
    return hljs.highlightAuto(code).value;
  }
});

function api(path, options) {
  const opts = options || {};
  const headers = Object.assign({}, opts.headers || {});
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = "Bearer " + token;
  return fetch(API + path, Object.assign({}, opts, { headers: headers }));
}

function save() {
  try { localStorage.setItem(STORE, JSON.stringify(history.slice(-30))); } catch (err) { /* cuota llena */ }
}

function setDrawer(open) {
  drawer.classList.toggle("open", open);
  backdrop.classList.toggle("hidden", !open);
  menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
}

function decorate(article) {
  article.querySelectorAll("pre").forEach(function (pre) {
    const button = document.createElement("button");
    button.className = "copy";
    button.type = "button";
    button.textContent = "Copiar";
    button.addEventListener("click", function () {
      const code = pre.querySelector("code");
      navigator.clipboard.writeText(code ? code.innerText : pre.innerText);
      button.textContent = "Copiado";
    });
    pre.appendChild(button);
  });
}

function render(item) {
  const article = document.createElement("article");
  article.className = "msg " + (item.role || "assistant");
  if (item.image) {
    const image = document.createElement("img");
    image.src = item.image;
    image.alt = item.alt || "Imagen";
    article.appendChild(image);
  } else if (item.role === "user") {
    article.textContent = item.text || "";
  } else {
    article.innerHTML = marked.parse(item.text || "");
    decorate(article);
  }
  log.appendChild(article);
}

function push(item) {
  history.push(item);
  render(item);
  save();
  log.scrollTop = log.scrollHeight;
}

function showModelDescription() {
  const option = model.options[model.selectedIndex];
  const about = option ? (option.dataset.descripcion || "") : "";
  modelDescription.textContent = about;
  headerModel.textContent = option ? option.textContent : "";
}

function showApp(data) {
  login.classList.add("hidden");
  app.classList.remove("hidden");
  model.innerHTML = "";
  (data.models || []).forEach(function (item) {
    const id = typeof item === "string" ? item : item.id;
    const option = document.createElement("option");
    option.value = id;
    option.textContent = typeof item === "string" ? item : (item.nombre || item.label || id);
    option.dataset.descripcion = typeof item === "string" ? "" : (item.descripcion || item.about || "");
    if (id === data.model) option.selected = true;
    model.appendChild(option);
  });
  showModelDescription();
  history = [];
  try { history = JSON.parse(localStorage.getItem(STORE) || "[]"); } catch (err) { history = []; }
  log.innerHTML = "";
  history.forEach(render);
  log.scrollTop = log.scrollHeight;
}

function armPhoto(file) {
  if (!file || !file.type || !file.type.startsWith("image/")) return;
  pendingFile = file;
  preview.classList.remove("hidden");
  preview.innerHTML = "";
  const image = document.createElement("img");
  image.src = URL.createObjectURL(file);
  image.alt = "Foto a editar";
  preview.appendChild(image);
  const note = document.createElement("p");
  note.textContent = "Describe los cambios y pulsa Enviar.";
  preview.appendChild(note);
  text.focus();
  setDrawer(false);
}

async function session() {
  if (!sessionStorage.getItem(TOKEN_KEY)) return;
  const response = await api("/api/session");
  if (!response.ok) {
    sessionStorage.removeItem(TOKEN_KEY);
    return;
  }
  showApp(await response.json());
}

menuBtn.addEventListener("click", function () { setDrawer(true); });
document.getElementById("close-drawer").addEventListener("click", function () { setDrawer(false); });
backdrop.addEventListener("click", function () { setDrawer(false); });

loginForm.addEventListener("submit", async function (event) {
  event.preventDefault();
  const error = document.getElementById("login-error");
  if (!API) {
    error.textContent = "Esta página no tiene la dirección de la Raspberry. La contraseña no se está comprobando.";
    return;
  }
  let response;
  try {
    response = await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: document.getElementById("password").value })
    });
  } catch (err) {
    error.textContent = "No llego a la Raspberry. La página de Netlify no puede hablar con el servidor.";
    return;
  }
  if (response.status === 401) {
    error.textContent = "Contraseña incorrecta.";
    return;
  }
  if (!response.ok) {
    error.textContent = "No llego a la Raspberry (HTTP " + response.status + ").";
    return;
  }
  const data = await response.json();
  if (data.token) sessionStorage.setItem(TOKEN_KEY, data.token);
  error.textContent = "";
  showApp(data);
});

model.addEventListener("change", async function () {
  showModelDescription();
  await api("/api/model", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: model.value })
  });
});

document.getElementById("clear").addEventListener("click", async function () {
  await api("/api/reset", { method: "POST" });
  history = [];
  log.innerHTML = "";
  pendingFile = null;
  preview.classList.add("hidden");
  preview.innerHTML = "";
  save();
  setDrawer(false);
});

document.getElementById("status-btn").addEventListener("click", async function () {
  const response = await api("/api/status");
  const data = await response.json();
  const head = data.ok
    ? "OpenRouter conectado (HTTP " + data.code + ")"
    : "Sin conexión con OpenRouter (HTTP " + (data.code || 0) + ")";
  document.getElementById("status").textContent = data.gasto ? head + "\n" + data.gasto : head;
});

log.addEventListener("dragover", function (event) {
  event.preventDefault();
  log.classList.add("drag");
});
log.addEventListener("dragleave", function () { log.classList.remove("drag"); });
log.addEventListener("drop", function (event) {
  event.preventDefault();
  log.classList.remove("drag");
  const file = event.dataTransfer && event.dataTransfer.files[0];
  if (file) armPhoto(file);
});
document.getElementById("attach").addEventListener("click", function () {
  document.getElementById("file").click();
});
document.getElementById("file").addEventListener("change", function () {
  armPhoto(this.files[0]);
  this.value = "";
});

text.addEventListener("keydown", function (event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    document.getElementById("composer").requestSubmit();
  }
});

document.getElementById("composer").addEventListener("submit", async function (event) {
  event.preventDefault();
  const message = text.value.trim();
  if (pendingFile) {
    if (!message) return;
    const file = pendingFile;
    pendingFile = null;
    preview.classList.add("hidden");
    preview.innerHTML = "";
    text.value = "";
    push({ role: "user", text: message });
    const body = new FormData();
    body.append("image", file);
    body.append("prompt", message);
    const response = await api("/api/edit", { method: "POST", body: body });
    const data = await response.json();
    if (!response.ok || !data.image) {
      push({ role: "assistant", text: data.error || "No pude editar la imagen." });
      return;
    }
    push({ role: "assistant", image: data.image, alt: "Imagen editada" });
    return;
  }
  if (!message) return;
  text.value = "";
  push({ role: "user", text: message });
  const response = await api("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: message })
  });
  const data = await response.json();
  push({ role: "assistant", text: data.reply || data.error || "Sin respuesta." });
});

function showTab(name) {
  const chat = name === "chat";
  document.getElementById("view-chat").classList.toggle("hidden", !chat);
  document.getElementById("view-audit").classList.toggle("hidden", chat);
  document.getElementById("tab-chat").classList.toggle("active", chat);
  document.getElementById("tab-audit").classList.toggle("active", !chat);
  document.getElementById("tab-chat").setAttribute("aria-selected", chat ? "true" : "false");
  document.getElementById("tab-audit").setAttribute("aria-selected", chat ? "false" : "true");
  setDrawer(false);
}

document.getElementById("tab-chat").addEventListener("click", function () { showTab("chat"); });
document.getElementById("tab-audit").addEventListener("click", function () { showTab("audit"); });

function addAudit(text) {
  const block = document.createElement("pre");
  block.textContent = text;
  const box = document.getElementById("audit-log");
  box.appendChild(block);
  box.scrollTop = box.scrollHeight;
}

function setAuditProgress(pct, step) {
  const wait = document.getElementById("audit-wait");
  wait.classList.remove("hidden");
  document.getElementById("audit-bar").style.width = Math.max(0, Math.min(100, pct)) + "%";
  document.getElementById("audit-pct").textContent = pct + "%";
  document.getElementById("audit-label").textContent = step;
}

async function runAudit(url, instruccion, refetch) {
  const button = document.getElementById("audit-run");
  const more = document.getElementById("audit-more");
  button.disabled = true;
  if (more) more.disabled = true;
  setAuditProgress(3, "Empieza la lectura…");
  try {
    const response = await api("/api/audit_url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url, instruccion: instruccion, refetch: refetch })
    });
    if (!response.ok || !response.body) {
      let message = "Error 500: Objetivo inaccesible";
      try {
        const data = await response.json();
        if (data.error) message = data.error;
      } catch (err) { /* cuerpo vacío */ }
      addAudit(message);
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalData = null;
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let cut = buffer.indexOf("\n");
      while (cut >= 0) {
        const line = buffer.slice(0, cut).trim();
        buffer = buffer.slice(cut + 1);
        cut = buffer.indexOf("\n");
        if (!line) continue;
        const msg = JSON.parse(line);
        if (msg.type === "progress") setAuditProgress(msg.pct, msg.step);
        if (msg.type === "done") finalData = msg;
      }
    }
    if (!finalData || finalData.ok === false) {
      addAudit((finalData && finalData.error) || "Error 500: Objetivo inaccesible");
      return;
    }
    setAuditProgress(100, "Listo.");
    const meta = document.getElementById("audit-meta");
    if (finalData.bytes) meta.textContent = "Código descargado: " + finalData.bytes + " caracteres. El chat general no se toca.";
    addAudit(finalData.result || "No encontrado");
    document.getElementById("audit-follow").classList.remove("hidden");
  } catch (err) {
    addAudit("Error 500: Objetivo inaccesible");
  } finally {
    document.getElementById("audit-wait").classList.add("hidden");
    button.disabled = false;
    if (more) more.disabled = false;
  }
}

document.getElementById("audit-form").addEventListener("submit", async function (event) {
  event.preventDefault();
  const url = document.getElementById("audit-url").value.trim();
  const instruccion = document.getElementById("audit-note").value.trim();
  if (!url || !instruccion) return;
  document.getElementById("audit-log").innerHTML = "";
  document.getElementById("audit-follow").classList.add("hidden");
  await runAudit(url, instruccion, true);
});

document.getElementById("audit-follow").addEventListener("submit", async function (event) {
  event.preventDefault();
  const url = document.getElementById("audit-url").value.trim();
  const next = document.getElementById("audit-next");
  const instruccion = next.value.trim();
  if (!instruccion) return;
  next.value = "";
  await runAudit(url, instruccion, false);
});

session();

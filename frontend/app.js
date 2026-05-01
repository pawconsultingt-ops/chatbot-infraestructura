/**
 * app.js — Chat interface logic
 *
 * Auth strategy
 * -------------
 * We use a "first-emission" Promise wrapper around onAuthStateChanged.
 * Firebase guarantees that onAuthStateChanged fires exactly once with the
 * definitive initial auth state (user or null) after loading IndexedDB.
 * Calling unsubscribe() inside the callback prevents any later spurious
 * null-emissions from triggering a redirect.
 *
 * Backend errors (4xx/5xx) NEVER call logout(). Only an explicit Firebase
 * Auth sign-out event should redirect to index.html.
 */

import { initializeApp }                    from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getAuth, onAuthStateChanged, signOut }
  from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

// ---------------------------------------------------------------------------
// Firebase config
// ---------------------------------------------------------------------------
const firebaseConfig = {
  apiKey:            "AIzaSyBkDpQUVlTM522gYdUjdINsF1LtifNR6nA",
  authDomain:        "chatbotapp-59c2b.firebaseapp.com",
  projectId:         "chatbotapp-59c2b",
  storageBucket:     "chatbotapp-59c2b.firebasestorage.app",
  messagingSenderId: "1031895725223",
  appId:             "1:1031895725223:web:9394e3cc61b1600f82d782",
  measurementId:     "G-LP8T0M4CTM",
};

const firebaseApp = initializeApp(firebaseConfig);
const auth        = getAuth(firebaseApp);

// ---------------------------------------------------------------------------
// Backend base URL
// ---------------------------------------------------------------------------
//const API_URL = "http://localhost:8001";
const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8001";

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------
const messagesEl  = document.getElementById("messages");
const sidebarList = document.getElementById("sidebarList");
const msgInput    = document.getElementById("msgInput");
const btnSend     = document.getElementById("btnSend");
const btnClear    = document.getElementById("btnClear");
const btnLogout   = document.getElementById("btnLogout");
const btnToggle   = document.getElementById("btnToggle");
const sidebar     = document.getElementById("sidebar");
const badgeRole   = document.getElementById("badgeRole");
const userEmailEl = document.getElementById("userEmail");
const toastEl     = document.getElementById("toast");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let currentUser = null;
let userRole    = null;
let isWaiting   = false;

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------

/**
 * Returns a Promise that resolves with the Firebase User (or null) after
 * Firebase has loaded its persisted auth state from IndexedDB.
 * Unsubscribing inside the callback ensures no later spurious null-calls
 * can trigger an accidental redirect.
 */
function getInitialAuthState() {
  return new Promise((resolve) => {
    const unsub = onAuthStateChanged(auth, (user) => {
      unsub();        // stop listening after the first definitive emission
      resolve(user);
    });
  });
}

// ---------------------------------------------------------------------------
// JWT decode (base64, client-side only — no signature verification)
// ---------------------------------------------------------------------------
function decodeJwtPayload(token) {
  try {
    const base64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const json   = decodeURIComponent(
      atob(base64)
        .split("")
        .map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0"))
        .join("")
    );
    return JSON.parse(json);
  } catch {
    return {};
  }
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
let toastTimer = null;

function showToast(msg, ms = 3000) {
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), ms);
}

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------
function appendBubble(role, text, usedSearch = false) {
  const row    = document.createElement("div");
  row.className = `msg-row ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;

  if (role === "assistant" && usedSearch) {
    const badge       = document.createElement("span");
    badge.className   = "badge-search";
    badge.textContent = "🔍 Búsqueda web usada";
    bubble.appendChild(document.createElement("br"));
    bubble.appendChild(badge);
  }

  row.appendChild(bubble);
  messagesEl.appendChild(row);
  scrollToBottom();
  return row;
}

function showTyping() {
  const row    = document.createElement("div");
  row.className = "msg-row assistant typing-row";
  row.id        = "typingIndicator";
  const bubble  = document.createElement("div");
  bubble.className = "typing-bubble";
  bubble.innerHTML  = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  scrollToBottom();
}

function removeTyping() {
  document.getElementById("typingIndicator")?.remove();
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function populateSidebar(messages) {
  if (!messages.length) {
    sidebarList.innerHTML = '<p class="sidebar-empty">Sin mensajes aún.</p>';
    return;
  }
  sidebarList.innerHTML = "";
  messages.forEach((msg) => sidebarList.appendChild(buildSidebarItem(msg.role, msg.content)));
}

function buildSidebarItem(role, content) {
  const item    = document.createElement("div");
  item.className = "sidebar-item";
  const tag      = document.createElement("span");
  tag.className  = `si-role ${role}`;
  tag.textContent = role === "user" ? "Tú" : "Bot";
  const text     = document.createElement("span");
  text.className = "si-text";
  text.textContent = content ?? "";
  item.appendChild(tag);
  item.appendChild(text);
  return item;
}

// ---------------------------------------------------------------------------
// Logout — only called by explicit user action or Firebase sign-out event
// ---------------------------------------------------------------------------
async function logout() {
  await signOut(auth).catch(() => {});
  sessionStorage.clear();
  window.location.href = "index.html";
}

// ---------------------------------------------------------------------------
// Load history
// ---------------------------------------------------------------------------
async function loadHistory(token) {
  try {
    const res = await fetch(`${API_URL}/history`, {
      headers: { Authorization: `Bearer ${token}` },
    });

    if (res.status === 403) {
      // User authenticated but no role assigned yet — show helpful message
      showToast("Rol no asignado. Ejecuta: python assign_role.py <uid> assistant_user", 6000);
      showWelcome();
      return;
    }

    if (res.status === 503) {
      showToast("Backend no configurado (service-account.json). El chat no funcionará.", 6000);
      showWelcome();
      return;
    }

    if (!res.ok) {
      // Any other error (401, 500, etc.) — show message, DO NOT logout
      const body = await res.json().catch(() => ({}));
      showToast(`No se pudo cargar el historial: ${body.detail ?? res.status}`, 5000);
      showWelcome();
      return;
    }

    const data     = await res.json();
    const messages = data.messages ?? [];
    messagesEl.innerHTML = "";
    messages.forEach((msg) => appendBubble(msg.role, msg.content));
    populateSidebar(messages);
    if (!messages.length) showWelcome();

  } catch (err) {
    console.error("[loadHistory]", err);
    showToast("No se pudo conectar con el backend. ¿Está corriendo uvicorn?", 5000);
    showWelcome();
  }
}

function showWelcome() {
  if (messagesEl.querySelector(".welcome-msg")) return;
  const p = document.createElement("p");
  p.className = "welcome-msg";
  p.style.cssText = "color:#9ca3af;font-size:0.875rem;text-align:center;margin-top:2rem;";
  p.textContent = "¡Hola! ¿En qué puedo ayudarte hoy?";
  messagesEl.appendChild(p);
}

// ---------------------------------------------------------------------------
// Send message
// ---------------------------------------------------------------------------
async function sendMessage() {
  const text = msgInput.value.trim();
  if (!text || isWaiting) return;

  messagesEl.querySelector(".welcome-msg")?.remove();
  appendBubble("user", text);
  msgInput.value = "";
  autoResizeTextarea();

  isWaiting = true;
  btnSend.disabled = true;
  showTyping();

  try {
    const token = await currentUser.getIdToken(false);
    sessionStorage.setItem("firebase_token", token);

    const res = await fetch(`${API_URL}/chat`, {
      method:  "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization:  `Bearer ${token}`,
      },
      body: JSON.stringify({ message: text }),
    });

    removeTyping();

    if (res.status === 403) {
      appendBubble("assistant", "⚠ Sin permisos. Asigna el rol assistant_user con assign_role.py.");
      return;
    }

    if (!res.ok) {
      // Surface error as a message — never redirect
      const body = await res.json().catch(() => ({}));
      appendBubble("assistant", `⚠ Error del servidor (${res.status}): ${body.detail ?? res.statusText}`);
      return;
    }

    const data = await res.json();
    appendBubble("assistant", data.reply, data.used_search === true);

    sidebarList.querySelector(".sidebar-empty")?.remove();
    sidebarList.appendChild(buildSidebarItem("user",      text));
    sidebarList.appendChild(buildSidebarItem("assistant", data.reply));

  } catch (err) {
    removeTyping();
    console.error("[sendMessage]", err);
    appendBubble("assistant", "⚠ No se pudo conectar con el servidor.");
  } finally {
    isWaiting        = false;
    btnSend.disabled = false;
    msgInput.focus();
  }
}

// ---------------------------------------------------------------------------
// Clear history
// ---------------------------------------------------------------------------
async function clearHistory() {
  if (!confirm("¿Seguro que quieres borrar todo el historial?")) return;
  try {
    const token = await currentUser.getIdToken(false);
    const res   = await fetch(`${API_URL}/history`, {
      method:  "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) { showToast("Error al borrar el historial."); return; }
    messagesEl.innerHTML  = "";
    sidebarList.innerHTML = '<p class="sidebar-empty">Sin mensajes aún.</p>';
    showWelcome();
    showToast("Historial borrado.");
  } catch (err) {
    console.error("[clearHistory]", err);
    showToast("Error de red al borrar el historial.");
  }
}

// ---------------------------------------------------------------------------
// Auto-resize textarea
// ---------------------------------------------------------------------------
function autoResizeTextarea() {
  msgInput.style.height = "42px";
  msgInput.style.height = Math.min(msgInput.scrollHeight, 120) + "px";
}

// ---------------------------------------------------------------------------
// Main init
// ---------------------------------------------------------------------------
async function init() {
  // Wait for Firebase to load the persisted session (IndexedDB).
  // getInitialAuthState() unsubscribes after the first callback, so no
  // future null-emission can accidentally redirect the user.
  const user = await getInitialAuthState();

  console.log("[auth] Initial state resolved. user =", user?.email ?? "null");

  if (!user) {
    console.log("[auth] No user — redirecting to login.");
    window.location.href = "index.html";
    return;
  }

  currentUser = user;

  const token = await currentUser.getIdToken(true);
  sessionStorage.setItem("firebase_token", token);

  const payload = decodeJwtPayload(token);
  userRole = payload.role ?? null;

  userEmailEl.textContent = currentUser.email ?? currentUser.uid;
  badgeRole.textContent   = userRole ?? "sin rol";

  if (userRole === "assistant_user") btnClear.style.display = "block";

  await loadHistory(token);

  // Watch for real sign-out events AFTER init is complete.
  // Because getInitialAuthState already consumed the first emission,
  // this listener will NOT fire immediately with the current user —
  // it only fires on future auth-state changes (sign-out, token revoke).
  onAuthStateChanged(auth, (u) => {
    if (!u) {
      console.log("[auth] Sign-out detected — redirecting.");
      logout();
    }
  });
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------
btnSend.addEventListener("click", sendMessage);

msgInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

msgInput.addEventListener("input", autoResizeTextarea);
btnClear.addEventListener("click", clearHistory);
btnLogout.addEventListener("click", logout);
btnToggle.addEventListener("click", () => sidebar.classList.toggle("collapsed"));

// Start
init();

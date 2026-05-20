const API = "/api/events";
let allEvents = [];

const THAI_MONTHS = ["ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."];

function thaiDate(str) {
  const d = new Date(str + "T00:00:00");
  if (isNaN(d)) return str;
  return `${d.getDate()} ${THAI_MONTHS[d.getMonth()]} ${d.getFullYear() + 543}`;
}

function thaiDuration(minutes) {
  if (minutes < 60) return `${minutes} นาที`;
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return m === 0 ? `${h} ชั่วโมง` : `${h} ชั่วโมง ${m} นาที`;
}

function peopleHtml(pipe) {
  if (!pipe) return "—";
  const items = pipe.split("|").map(s => s.trim()).filter(Boolean);
  if (!items.length) return "—";
  return `<div class="people-list">${items.map(i => `<span>${esc(i)}</span>`).join("")}</div>`;
}

// ─── Fetch & render ──────────────────────────────────────────────────────────

async function loadEvents() {
  try {
    const res = await fetch(API);
    if (!res.ok) throw new Error(res.statusText);
    allEvents = await res.json();
    renderStats();
    renderTable();
  } catch (err) {
    document.getElementById("eventsBody").innerHTML =
      `<tr><td colspan="9" class="empty">⚠️ โหลดข้อมูลไม่สำเร็จ: ${err.message}</td></tr>`;
  }
}

function renderStats() {
  const counts = { active: 0, sent: 0, cancelled: 0 };
  allEvents.forEach(e => { if (counts[e.status] !== undefined) counts[e.status]++; });
  document.getElementById("stats").innerHTML = [
    ["active", "กำลังจะถึง", counts.active],
    ["sent", "แจ้งเตือนแล้ว", counts.sent],
    ["cancelled", "ยกเลิกแล้ว", counts.cancelled],
  ].map(([cls, label, val]) =>
    `<div class="stat-card ${cls}"><div class="stat-value">${val}</div><div class="stat-label">${label}</div></div>`
  ).join("");
}

function renderTable() {
  const statusFilter = document.getElementById("filterStatus").value;
  const search = document.getElementById("filterSearch").value.toLowerCase();

  const filtered = allEvents.filter(e => {
    const matchStatus = !statusFilter || e.status === statusFilter;
    const matchSearch = !search ||
      e.event_name.toLowerCase().includes(search) ||
      (e.location || "").toLowerCase().includes(search);
    return matchStatus && matchSearch;
  });

  const tbody = document.getElementById("eventsBody");
  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty">ไม่พบรายการ</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(e => {
    const badge = `<span class="badge badge--${e.status}">${statusLabel(e.status)}</span>`;
    const editBtn = e.status === "active"
      ? `<button class="btn--icon" title="แก้ไข" onclick="openEdit('${e.id}')">✏️</button>` : "";
    const delBtn = e.status === "active"
      ? `<button class="btn--icon" title="ยกเลิก" onclick="cancelEvent('${e.id}','${esc(e.event_name)}')">🗑️</button>` : "";
    return `<tr>
      <td><code>${e.id}</code></td>
      <td>${esc(e.event_name)}${e.details ? `<br><small style="color:var(--muted)">${esc(e.details.substring(0,40))}${e.details.length>40?"…":""}</small>` : ""}</td>
      <td style="white-space:nowrap">${thaiDate(e.event_date)}<br>${e.event_time} น.</td>
      <td>${e.location ? esc(e.location) : "—"}</td>
      <td>${peopleHtml(e.responsible)}</td>
      <td style="white-space:nowrap">ก่อน ${thaiDuration(e.reminder_minutes)}</td>
      <td title="${esc(e.user_id)}">${esc(e.display_name || e.user_id)}</td>
      <td>${badge}</td>
      <td><div class="action-group">${editBtn}${delBtn}</div></td>
    </tr>`;
  }).join("");
}

function statusLabel(s) {
  return { active: "กำลังจะถึง", sent: "แจ้งเตือนแล้ว", cancelled: "ยกเลิกแล้ว" }[s] || s;
}

function esc(str) {
  return String(str ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// ─── Modal ───────────────────────────────────────────────────────────────────

function openModal(title = "เพิ่มงานใหม่") {
  document.getElementById("modalTitle").textContent = title;
  document.getElementById("modalOverlay").classList.add("open");
}

function closeModal() {
  document.getElementById("modalOverlay").classList.remove("open");
  document.getElementById("eventForm").reset();
  document.getElementById("editEventId").value = "";
}

function openEdit(id) {
  const e = allEvents.find(ev => ev.id === id);
  if (!e) return;
  document.getElementById("editEventId").value = id;
  document.getElementById("fName").value = e.event_name;
  document.getElementById("fDate").value = e.event_date;
  document.getElementById("fTime").value = e.event_time;
  document.getElementById("fRemind").value = e.reminder_minutes;
  document.getElementById("fLocation").value = e.location || "";
  document.getElementById("fResponsible").value = e.responsible || "";
  document.getElementById("fParticipants").value = e.participants || "";
  document.getElementById("fDetails").value = e.details || "";
  document.getElementById("fUserId").value = e.user_id !== "dashboard" ? e.user_id : "";
  openModal("แก้ไขงาน");
}

async function cancelEvent(id, name) {
  if (!confirm(`ยืนยันการยกเลิกงาน "${name}" ?`)) return;
  const res = await fetch(`${API}/${id}`, { method: "DELETE" });
  if (res.ok) loadEvents();
  else alert("ยกเลิกงานไม่สำเร็จ");
}

// ─── Form submit ─────────────────────────────────────────────────────────────

document.getElementById("eventForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const editId = document.getElementById("editEventId").value;
  const userId = document.getElementById("fUserId").value.trim() || "dashboard";

  const payload = {
    event_name: document.getElementById("fName").value.trim(),
    event_date: document.getElementById("fDate").value,
    event_time: document.getElementById("fTime").value,
    reminder_minutes: parseInt(document.getElementById("fRemind").value),
    location: document.getElementById("fLocation").value.trim(),
    responsible: document.getElementById("fResponsible").value.trim(),
    participants: document.getElementById("fParticipants").value.trim(),
    details: document.getElementById("fDetails").value.trim(),
  };

  let res;
  if (editId) {
    res = await fetch(`${API}/${editId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } else {
    res = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, user_id: userId, display_name: userId }),
    });
  }

  if (res.ok) { closeModal(); loadEvents(); }
  else {
    const err = await res.json().catch(() => ({}));
    alert(`เกิดข้อผิดพลาด: ${err.detail || res.statusText}`);
  }
});

// ─── Health check ─────────────────────────────────────────────────────────────

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    document.getElementById("statusDot").className = res.ok ? "dot dot--ok" : "dot dot--err";
  } catch {
    document.getElementById("statusDot").className = "dot dot--err";
  }
}

// ─── Wiring ──────────────────────────────────────────────────────────────────

document.getElementById("openModalBtn").addEventListener("click", () => openModal());
document.getElementById("closeModalBtn").addEventListener("click", closeModal);
document.getElementById("cancelBtn").addEventListener("click", closeModal);
document.getElementById("refreshBtn").addEventListener("click", loadEvents);
document.getElementById("filterStatus").addEventListener("change", renderTable);
document.getElementById("filterSearch").addEventListener("input", renderTable);
document.getElementById("modalOverlay").addEventListener("click", (e) => {
  if (e.target === document.getElementById("modalOverlay")) closeModal();
});

const today = new Date().toISOString().split("T")[0];
document.getElementById("fDate").min = today;

checkHealth();
loadEvents();
setInterval(loadEvents, 60_000);
setInterval(checkHealth, 30_000);

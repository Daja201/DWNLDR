const socket = io();

let currentFolder = window.DEFAULT_FOLDER || "";
let browseFolder = currentFolder;

document.getElementById("folderLabel").textContent = "/" + currentFolder;

function esc(s) {
  return (s || "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function fmtBytes(n) {
  if (!n) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(1) + " " + units[i];
}

// ---------- queue / history ----------

async function refreshQueue() {
  const res = await fetch("/api/queue");
  const jobs = await res.json();
  const el = document.getElementById("queue");
  if (!jobs.length) { el.innerHTML = '<div class="empty">Nothing queued</div>'; return; }
  el.innerHTML = jobs.map(j => `
    <div class="item">
      <div class="item-top">
        <div class="item-title">${esc(j.title || j.url)}</div>
        <div class="actions">
          <button onclick="stopJob(${j.id})">Stop</button>
          <button onclick="deleteJob(${j.id})">Remove</button>
        </div>
      </div>
      <div class="item-meta">${j.source} / ${j.mode}${j.is_playlist ? " / playlist" : ""} - ${j.status}${j.progress_label ? " - " + esc(j.progress_label) : ""}</div>
      <div class="bar"><div class="bar-fill" style="width:${j.progress || 0}%"></div></div>
    </div>
  `).join("");
}

async function refreshHistory() {
  const res = await fetch("/api/history");
  const items = await res.json();
  const el = document.getElementById("history");
  if (!items.length) { el.innerHTML = '<div class="empty">No history yet</div>'; return; }
  el.innerHTML = items.map(h => `
    <div class="item">
      <div class="item-top">
        <div class="item-title">${esc(h.title || h.url)}</div>
        <div class="actions">
          ${h.status === "completed" ? `
            <button onclick="playItem(${h.id})">Play</button>
            <button onclick="fetchItem(${h.id})">Download</button>
          ` : ""}
        </div>
      </div>
      <div class="item-meta">${h.source} / ${h.mode} - ${h.status}${h.file_size ? " - " + fmtBytes(h.file_size) : ""}</div>
    </div>
  `).join("");
}

async function stopJob(id) {
  await fetch(`/api/stop/${id}`, { method: "POST" });
  refreshQueue();
}

async function deleteJob(id) {
  await fetch(`/api/queue/${id}`, { method: "DELETE" });
  refreshQueue();
}

function playItem(id) {
  window.open(`/api/play/${id}`, "_blank");
}

function fetchItem(id) {
  window.location.href = `/api/fetch/${id}`;
}

// ---------- add ----------

document.getElementById("addBtn").addEventListener("click", async () => {
  const url = document.getElementById("url").value.trim();
  const mode = document.getElementById("mode").value;
  if (!url) return;
  const res = await fetch("/api/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, mode, folder: currentFolder }),
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  document.getElementById("url").value = "";
  refreshQueue();
});

// ---------- folder browser ----------

const modal = document.getElementById("folderModal");

document.getElementById("folderBtn").addEventListener("click", () => {
  browseFolder = currentFolder;
  modal.classList.remove("hidden");
  loadFolder(browseFolder);
});

document.getElementById("folderClose").addEventListener("click", () => {
  modal.classList.add("hidden");
});

document.getElementById("folderUse").addEventListener("click", () => {
  currentFolder = browseFolder;
  document.getElementById("folderLabel").textContent = "/" + currentFolder;
  modal.classList.add("hidden");
  fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ default_folder: currentFolder }),
  });
});

document.getElementById("folderUp").addEventListener("click", () => {
  const parts = browseFolder.split("/").filter(Boolean);
  parts.pop();
  loadFolder(parts.join("/"));
});

document.getElementById("mkdirBtn").addEventListener("click", async () => {
  const name = document.getElementById("newFolderName").value.trim();
  if (!name) return;
  const res = await fetch("/api/browse/mkdir", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: browseFolder, name }),
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  document.getElementById("newFolderName").value = "";
  loadFolder(browseFolder);
});

async function loadFolder(path) {
  const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  browseFolder = data.path;
  document.getElementById("folderPath").textContent = "/" + browseFolder;
  const dirs = data.entries.filter(e => e.is_dir);
  const el = document.getElementById("folderList");
  el.innerHTML = dirs.length
    ? dirs.map(d => `<div class="entry" onclick="loadFolder('${(browseFolder ? browseFolder + "/" : "") + d.name}')">${esc(d.name)}</div>`).join("")
    : '<div class="empty">No subfolders</div>';
}

// ---------- log ----------

async function loadLogTail() {
  const res = await fetch("/api/logs/tail");
  const data = await res.json();
  const el = document.getElementById("log");
  el.textContent = (data.lines || []).join("");
  el.scrollTop = el.scrollHeight;
}

socket.on("log", (entry) => {
  const el = document.getElementById("log");
  el.textContent += `[${entry.level}] ${entry.message}\n`;
  el.scrollTop = el.scrollHeight;
});

socket.on("queue_update", () => {
  refreshQueue();
  refreshHistory();
});

setInterval(refreshQueue, 3000);

refreshQueue();
refreshHistory();
loadLogTail();

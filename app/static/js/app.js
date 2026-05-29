const state = {
  chats: [],
  selectedChatId: null,
  searchTimer: null,
};

const els = {
  chatList: document.getElementById("chat-list"),
  thread: document.getElementById("thread"),
  threadTitle: document.getElementById("thread-title"),
  threadSubtitle: document.getElementById("thread-subtitle"),
  search: document.getElementById("search"),
  mediaToggle: document.getElementById("media-toggle"),
};

async function api(path) {
  const r = await fetch(path, { credentials: "same-origin" });
  if (r.status === 401) {
    window.location.href = "/login";
    return null;
  }
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

function fmtDate(unixSec) {
  if (!unixSec) return "";
  const d = new Date(unixSec * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  const diffDays = (now - d) / (1000 * 60 * 60 * 24);
  if (diffDays < 7) {
    return d.toLocaleDateString([], { weekday: "short" });
  }
  return d.toLocaleDateString([], { month: "numeric", day: "numeric", year: "2-digit" });
}

function fmtDay(unixSec) {
  if (!unixSec) return "";
  return new Date(unixSec * 1000).toLocaleDateString([], {
    weekday: "long", month: "long", day: "numeric", year: "numeric",
  });
}

function fmtTime(unixSec) {
  if (!unixSec) return "";
  return new Date(unixSec * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function renderChatList(chats) {
  els.chatList.innerHTML = "";
  if (!chats.length) {
    els.chatList.innerHTML = `<li class="empty-state">No conversations</li>`;
    return;
  }
  for (const chat of chats) {
    const li = document.createElement("li");
    li.className = "chat-item" + (chat.chat_id === state.selectedChatId ? " selected" : "");
    li.dataset.chatId = chat.chat_id;
    li.innerHTML = `
      <span class="name"></span>
      <span class="meta">
        <span class="participants"></span>
        <span class="when">${fmtDate(chat.last_date)}</span>
      </span>
    `;
    li.querySelector(".name").textContent = chat.display_name || "(no name)";
    li.querySelector(".participants").textContent =
      chat.participants.length > 1 ? `${chat.participants.length} people` : (chat.participants[0] || "");
    li.addEventListener("click", () => selectChat(chat.chat_id));
    els.chatList.appendChild(li);
  }
}

async function loadChats() {
  const chats = await api("/api/chats");
  if (!chats) return;
  state.chats = chats;
  renderChatList(chats);
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function attachmentHtml(att) {
  const url = `/api/attachments/${att.attachment_id}`;
  const mime = att.mime_type || "";
  if (mime.startsWith("image/")) {
    return `<img src="${url}" alt="${escapeHtml(att.transfer_name || "")}" loading="lazy" />`;
  }
  if (mime.startsWith("video/")) {
    return `<video src="${url}" controls preload="metadata"></video>`;
  }
  if (mime.startsWith("audio/")) {
    return `<audio src="${url}" controls preload="metadata"></audio>`;
  }
  const label = att.transfer_name || att.filename || "Attachment";
  return `<a class="attachment-link" href="${url}" target="_blank" rel="noopener">📎 ${escapeHtml(label)}</a>`;
}

function bubbleHtml(msg) {
  const sender = msg.is_from_me ? "" : (msg.sender_name || msg.sender_id || "");
  const senderHtml = sender ? `<span class="sender">${escapeHtml(sender)}</span>` : "";
  const text = msg.text ? escapeHtml(msg.text) : (msg.attachment_count ? "" : "<em class='muted'>(no text)</em>");
  let atts = "";
  if (msg.attachments && msg.attachments.length) {
    atts = `<div class="attachments">${msg.attachments.map(attachmentHtml).join("")}</div>`;
  }
  const time = fmtTime(msg.date);
  return `
    <div class="bubble-row ${msg.is_from_me ? "from-me" : "from-them"}">
      <div class="bubble">
        ${senderHtml}${text}${atts}
        <span class="timestamp">${time}</span>
      </div>
    </div>
  `;
}

async function selectChat(chatId) {
  state.selectedChatId = chatId;
  galleryOpen = false;
  if (els.mediaToggle) els.mediaToggle.classList.remove("active");
  document.querySelectorAll(".chat-item").forEach((el) => {
    el.classList.toggle("selected", Number(el.dataset.chatId) === chatId);
  });
  const chat = state.chats.find((c) => c.chat_id === chatId);
  els.threadTitle.textContent = chat ? (chat.display_name || "(no name)") : "";
  els.threadSubtitle.textContent = chat
    ? `${chat.message_count} message${chat.message_count === 1 ? "" : "s"} · ${chat.participants.join(", ")}`
    : "";
  els.thread.innerHTML = `<div class="empty-state">Loading…</div>`;
  let messages = await api(`/api/chats/${chatId}/messages?limit=2000`);
  if (!messages) return;
  els.mediaToggle.disabled = false;
  renderMessages(messages);
}

function renderMessages(messages) {
  if (!messages.length) {
    els.thread.innerHTML = `<div class="empty-state">No messages</div>`;
    return;
  }
  const parts = [];
  let lastDay = null;
  for (const msg of messages) {
    const day = msg.date ? new Date(msg.date * 1000).toDateString() : null;
    if (day && day !== lastDay) {
      parts.push(`<div class="day-divider">${escapeHtml(fmtDay(msg.date))}</div>`);
      lastDay = day;
    }
    parts.push(bubbleHtml(msg));
  }
  els.thread.innerHTML = parts.join("");
  els.thread.scrollTop = els.thread.scrollHeight;
}

async function runSearch(query) {
  if (!query.trim()) {
    renderChatList(state.chats);
    return;
  }
  const results = await api(`/api/search?q=${encodeURIComponent(query)}`);
  if (!results) return;
  renderSearchResults(results);
}

function renderSearchResults(results) {
  els.chatList.innerHTML = "";
  if (!results.length) {
    els.chatList.innerHTML = `<li class="empty-state">No results</li>`;
    return;
  }
  for (const r of results) {
    const li = document.createElement("li");
    li.className = "chat-item";
    li.dataset.chatId = r.chat_id;
    li.innerHTML = `
      <span class="name"></span>
      <span class="meta">
        <span class="participants"></span>
        <span class="when">${fmtDate(r.date)}</span>
      </span>
    `;
    li.querySelector(".name").textContent = r.chat_name || "(no name)";
    li.querySelector(".participants").textContent = (r.text || "").slice(0, 60);
    li.addEventListener("click", () => selectChat(r.chat_id));
    els.chatList.appendChild(li);
  }
}

els.search.addEventListener("input", (e) => {
  clearTimeout(state.searchTimer);
  const query = e.target.value;
  state.searchTimer = setTimeout(() => runSearch(query), 250);
});

let galleryOpen = false;

async function openGallery() {
  if (!state.selectedChatId) return;
  const atts = await api(`/api/chats/${state.selectedChatId}/attachments`);
  if (!atts) return;
  if (!atts.length) {
    els.thread.innerHTML = `<div class="empty-state">No media in this conversation</div>`;
    return;
  }
  const cells = atts.map((a) => `<div class="gallery-cell">${attachmentHtml(a)}</div>`).join("");
  els.thread.innerHTML = `<div class="media-gallery">${cells}</div>`;
}

els.mediaToggle.addEventListener("click", () => {
  galleryOpen = !galleryOpen;
  els.mediaToggle.classList.toggle("active", galleryOpen);
  if (galleryOpen) {
    openGallery();
  } else if (state.selectedChatId) {
    selectChat(state.selectedChatId);
  }
});

const refreshBtn = document.getElementById("refresh-cache");
if (refreshBtn) {
  refreshBtn.addEventListener("click", async () => {
    refreshBtn.disabled = true;
    const original = refreshBtn.textContent;
    refreshBtn.textContent = "Refreshing…";
    try {
      const r = await fetch("/api/cache/refresh", { method: "POST", credentials: "same-origin" });
      if (!r.ok) throw new Error(`status ${r.status}`);
      await loadChats();
      if (state.selectedChatId) await selectChat(state.selectedChatId);
    } catch (err) {
      alert(`Refresh failed: ${err.message}`);
    } finally {
      refreshBtn.textContent = original;
      refreshBtn.disabled = false;
    }
  });
}

loadChats().catch((err) => {
  els.chatList.innerHTML = `<li class="empty-state">Error: ${escapeHtml(err.message)}</li>`;
});

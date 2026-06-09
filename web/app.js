const form = document.querySelector("#search-form");
const query = document.querySelector("#query");
const source = document.querySelector("#source");
const kind = document.querySelector("#kind");
const year = document.querySelector("#year");
const results = document.querySelector("#results");
const summary = document.querySelector("#summary");
const statusNode = document.querySelector("#status");
const clearButton = document.querySelector("#clear");
const template = document.querySelector("#result-template");

function text(value, fallback = "") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function list(value) {
  return Array.isArray(value) ? value.filter(Boolean) : [];
}

function firstTitle(hit) {
  return list(hit.titles)[0] || list(hit.aliases)[0] || hit.id;
}

function shortList(items, limit = 4) {
  const clean = list(items);
  if (clean.length <= limit) return clean.join(", ");
  return `${clean.slice(0, limit).join(", ")} +${clean.length - limit}`;
}

function setHtml(node, html) {
  node.innerHTML = html || "";
}

function escapeHtml(value) {
  return text(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function highlightHtml(value) {
  return escapeHtml(value)
    .replaceAll("&lt;mark&gt;", "<mark>")
    .replaceAll("&lt;/mark&gt;", "</mark>");
}

function highlightedList(value, limit = 4) {
  const clean = list(value);
  const shown = clean.slice(0, limit).map(highlightHtml);
  if (clean.length > limit) shown.push(`+${clean.length - limit}`);
  return shown.join(", ");
}

function renderHit(hit) {
  const item = template.content.firstElementChild.cloneNode(true);
  const title = item.querySelector("h2");
  const poster = item.querySelector(".poster-slot");
  const yearNode = item.querySelector(".year");
  const aliases = item.querySelector(".aliases");
  const badges = item.querySelector(".badges");
  const people = item.querySelector(".people");
  const description = item.querySelector(".description");
  const sourceLine = item.querySelector(".source-line");
  const formatted = hit._formatted || {};

  setHtml(title, highlightHtml(list(formatted.titles)[0] || firstTitle(hit)));
  yearNode.textContent = hit.year ? String(hit.year) : "";

  if (hit.poster) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = "";
    img.addEventListener("error", () => img.remove(), { once: true });
    img.src = hit.poster;
    poster.append(img);
  }

  const aliasText = highlightedList(formatted.aliases || hit.aliases, 5);
  aliases.innerHTML = aliasText ? `Aliases: ${aliasText}` : "";

  for (const value of [...list(hit.sources), hit.kind].filter(Boolean).slice(0, 8)) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = value;
    badges.append(badge);
  }

  const peopleText = highlightedList(formatted.people || hit.people, 6);
  people.innerHTML = peopleText ? `People: ${peopleText}` : "";
  description.textContent = text(hit.description);

  const ids = Object.entries(hit.source_ids || {})
    .map(([key, value]) => `${key}:${value}`)
    .join("  ");
  sourceLine.textContent = ids || hit.id;
  return item;
}

function renderResults(data) {
  results.replaceChildren();
  const hits = data.hits || [];
  summary.textContent = `${data.estimatedTotalHits ?? hits.length} results`;
  if (!hits.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No works found.";
    results.append(empty);
    return;
  }
  for (const hit of hits) {
    results.append(renderHit(hit));
  }
}

async function search() {
  const params = new URLSearchParams();
  params.set("q", query.value.trim());
  params.set("limit", "30");
  if (source.value) params.set("source", source.value);
  if (kind.value) params.set("kind", kind.value);
  if (year.value) params.set("year", year.value);
  summary.textContent = "Searching";
  const response = await fetch(`/api/search?${params.toString()}`);
  if (!response.ok) {
    summary.textContent = "Search failed";
    results.replaceChildren();
    const error = document.createElement("div");
    error.className = "empty";
    error.textContent = await response.text();
    results.append(error);
    return;
  }
  renderResults(await response.json());
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();
    const state = data.state || {};
    const count = data.index?.numberOfDocuments;
    const pieces = [];
    if (state.status) pieces.push(state.status);
    if (Number.isFinite(count)) pieces.push(`${count.toLocaleString()} works`);
    if (state.finished_at) pieces.push(new Date(state.finished_at).toLocaleString());
    statusNode.textContent = pieces.join(" | ") || "Index unavailable";
  } catch {
    statusNode.textContent = "Index unavailable";
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});

for (const node of [source, kind, year]) {
  node.addEventListener("change", search);
}

clearButton.addEventListener("click", () => {
  query.value = "";
  source.value = "";
  kind.value = "";
  year.value = "";
  results.replaceChildren();
  summary.textContent = "Ready";
  query.focus();
});

loadStatus();
search();

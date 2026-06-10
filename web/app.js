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

function numeric(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

const sourceLabels = {
  douban: "Douban",
  imdb: "IMDb",
  bangumi: "Bangumi",
  steam: "Steam",
  epic: "Epic",
  indienova: "Indienova",
};

function sourceUrl(source, sourceId) {
  const value = text(sourceId).trim();
  if (!value) return "";
  const encoded = encodeURIComponent(value);
  if (source === "douban") return `https://movie.douban.com/subject/${encoded}/`;
  if (source === "imdb") return `https://www.imdb.com/title/${encoded}/`;
  if (source === "bangumi") return `https://bgm.tv/subject/${encoded}`;
  if (source === "steam") return `https://store.steampowered.com/app/${encoded}/`;
  if (source === "epic") return `https://store.epicgames.com/en-US/p/${encoded}`;
  if (source === "indienova") return `https://indienova.com/game/${encoded}`;
  return "";
}

function renderSourceLinks(node, hit) {
  node.replaceChildren();
  const entries = Object.entries(hit.source_ids || {}).filter(([, value]) => text(value).trim());
  if (!entries.length) {
    node.textContent = hit.id;
    return;
  }

  for (const [source, sourceId] of entries) {
    const label = sourceLabels[source] || source;
    const textValue = `${label} ${sourceId}`;
    const url = sourceUrl(source, sourceId);
    const item = url ? document.createElement("a") : document.createElement("span");
    item.textContent = textValue;
    if (url) {
      item.href = url;
      item.target = "_blank";
      item.rel = "noreferrer";
    }
    node.append(item);
  }
}

function renderHit(hit) {
  const item = template.content.firstElementChild.cloneNode(true);
  const title = item.querySelector("h2");
  const poster = item.querySelector(".poster-slot");
  const yearNode = item.querySelector(".year");
  const scoreNode = item.querySelector(".score");
  const aliases = item.querySelector(".aliases");
  const people = item.querySelector(".people");
  const description = item.querySelector(".description");
  const sourceLine = item.querySelector(".source-line");
  const formatted = hit._formatted || {};

  setHtml(title, highlightHtml(list(formatted.titles)[0] || firstTitle(hit)));
  yearNode.textContent = hit.year ? String(hit.year) : "";
  const score = numeric(hit.rating_score);
  if (score !== null) {
    scoreNode.textContent = score.toFixed(1);
    const votes = numeric(hit.rating_votes);
    if (votes !== null) scoreNode.title = `${votes.toLocaleString()} votes`;
  }

  const posterUrl = hit.poster_ptgen || hit.poster;
  if (posterUrl) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = "";
    img.addEventListener("error", () => img.remove(), { once: true });
    img.src = posterUrl;
    poster.append(img);
  }

  const aliasText = highlightedList(formatted.aliases || hit.aliases, 5);
  aliases.innerHTML = aliasText ? `Aliases: ${aliasText}` : "";

  const peopleText = highlightedList(formatted.people || hit.people, 6);
  people.innerHTML = peopleText ? `People: ${peopleText}` : "";
  description.textContent = text(hit.description);

  renderSourceLinks(sourceLine, hit);
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

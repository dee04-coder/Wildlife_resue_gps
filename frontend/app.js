"use strict";

/*
 * Ranger Rescue GPS: front end.
 * Plain JavaScript, no build step. It talks to the FastAPI backend on the same origin.
 *
 * Security notes:
 *  - Everything that comes from the API is inserted with textContent / DOM methods,
 *    never innerHTML, so a malicious trail note or label cannot inject markup.
 *  - The admin API key lives only in the password field; it is never stored.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

const state = {
  reserveId: null,
  graph: null,        // {reserve, nodes, trails}
  plan: null,         // {route: [...], order: [...]} currently drawn on the map
  lastRequest: null,  // body of the last plan, reused to re-plan after a trail change
  selectedTrail: null,
};

const $ = (id) => document.getElementById(id);
const enc = encodeURIComponent;

/* ---------- small helpers ---------- */

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

function svgEl(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function fmt(n) {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/** Risk 0 (green) to 5 (red). */
function riskColor(risk) {
  const clamped = Math.min(5, Math.max(0, risk));
  return `hsl(${120 - (clamped / 5) * 120} 65% 42%)`;
}

function priorityLabel(weight) {
  if (weight === 0) return "Fastest";
  if (weight === 1) return "Balanced";
  if (weight >= 1000) return "Safest";
  return `Risk weight ${fmt(weight)}`;
}

function describe(node) {
  return node.label ? `${node.label} (${node.name})` : node.name;
}

function hasPos(node) {
  return Boolean(node) && node.x !== null && node.y !== null;
}

function nodesByName() {
  return Object.fromEntries(state.graph.nodes.map((n) => [n.name, n]));
}

function showStatus(message, kind = "error") {
  const box = $("status");
  box.textContent = message;
  box.className = `status ${kind}`;
  box.hidden = false;
}

function clearStatus() {
  $("status").hidden = true;
}

async function api(path, { method = "GET", body, key } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers["X-API-Key"] = key;
  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (_) {
    throw new Error("Cannot reach the server. Is uvicorn running?");
  }
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    /* empty or non-JSON body */
  }
  if (!res.ok) {
    if (data && typeof data.detail === "string") throw new Error(data.detail);
    if (data && Array.isArray(data.detail)) throw new Error(data.detail.map((d) => d.msg).join("; "));
    throw new Error(`Request failed (${res.status})`);
  }
  return data;
}

/* ---------- loading ---------- */

async function init() {
  $("reserve").addEventListener("change", (e) => loadReserve(e.target.value));
  $("plan-btn").addEventListener("click", planMission);
  $("show-labels").addEventListener("change", drawMap);
  $("save-trail").addEventListener("click", saveTrailFields);
  $("toggle-trail").addEventListener("click", toggleTrail);
  $("close-panel").addEventListener("click", closeTrailPanel);

  try {
    const reserves = await api("/reserves");
    $("reserve").replaceChildren(...reserves.map((r) => el("option", { value: r.id }, r.name)));
    if (!reserves.length) {
      showStatus("The server has no reserves yet.");
      return;
    }
    const preferred = reserves.find((r) => r.id === "great-savannah") || reserves[0];
    $("reserve").value = preferred.id;
    await loadReserve(preferred.id);
  } catch (err) {
    showStatus(err.message);
  }
}

async function fetchGraph() {
  state.graph = await api(`/reserves/${enc(state.reserveId)}/graph`);
}

async function loadReserve(id) {
  clearStatus();
  state.reserveId = id;
  state.plan = null;
  state.lastRequest = null;
  state.selectedTrail = null;
  $("result").hidden = true;
  $("trail-panel").hidden = true;
  try {
    await fetchGraph();
    renderStations();
    renderEnds();
    drawMap();
    await loadHistory();
  } catch (err) {
    showStatus(err.message);
  }
}

function renderEnds() {
  const base = state.graph.nodes.find((n) => n.kind === "base");
  const incident = state.graph.nodes.find((n) => n.kind === "incident");
  $("route-ends").textContent = base && incident ? `From ${describe(base)} to ${describe(incident)}` : "";
}

function renderStations() {
  const box = $("stations");
  box.replaceChildren(el("legend", {}, "Stations to visit (any order)"));
  const stations = state.graph.nodes.filter((n) => n.kind === "station");
  if (!stations.length) {
    box.append(el("p", { class: "muted" }, "No stations here, so the plan is a direct route."));
    return;
  }
  for (const s of stations) {
    const input = el("input", { type: "checkbox", value: s.name, checked: "" });
    box.append(el("label", { class: "check" }, input, `${s.name}: ${s.label || "Station"}`));
  }
}

/* ---------- map ---------- */

function drawMap() {
  const map = $("map");
  map.replaceChildren();
  const graph = state.graph;
  if (!graph) return;

  if (!graph.nodes.some(hasPos)) {
    map.append(svgEl("text", { x: 50, y: 50, "text-anchor": "middle", class: "map-empty" },
      "This reserve has no map coordinates yet."));
    return;
  }

  const nodes = nodesByName();
  const showLabels = $("show-labels").checked;
  const trailLayer = svgEl("g");
  const routeLayer = svgEl("g");
  const nodeLayer = svgEl("g");
  const labelLayer = svgEl("g");
  const badgeLayer = svgEl("g");

  for (const t of graph.trails) {
    const a = nodes[t.node_a];
    const b = nodes[t.node_b];
    if (!hasPos(a) || !hasPos(b)) continue;
    const closed = t.status === "closed";
    const coords = { x1: a.x, y1: a.y, x2: b.x, y2: b.y };

    const lineAttrs = {
      ...coords,
      class: "trail" + (closed ? " closed" : "") + (state.selectedTrail === t.id ? " selected" : ""),
    };
    if (!closed) lineAttrs.stroke = riskColor(t.risk);
    trailLayer.append(svgEl("line", lineAttrs));

    const hit = svgEl("line", {
      ...coords,
      class: "trail-hit",
      tabindex: "0",
      role: "button",
      "aria-label": `Trail ${t.node_a} to ${t.node_b}, ${fmt(t.time_min)} minutes, risk ${fmt(t.risk)}, ${t.status}`,
    });
    hit.addEventListener("click", () => selectTrail(t.id));
    hit.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectTrail(t.id);
      }
    });
    trailLayer.append(hit);

    if (showLabels) {
      labelLayer.append(svgEl("text", {
        x: (a.x + b.x) / 2,
        y: (a.y + b.y) / 2,
        class: "trail-label",
        "text-anchor": "middle",
        "dominant-baseline": "central",
      }, closed ? "closed" : `${fmt(t.time_min)}m \u00b7 r${fmt(t.risk)}`));
    }
  }

  if (state.plan) {
    const points = state.plan.route
      .map((name) => nodes[name])
      .filter(hasPos)
      .map((n) => `${n.x},${n.y}`)
      .join(" ");
    routeLayer.append(
      svgEl("polyline", { points, class: "route-halo" }),
      svgEl("polyline", { points, class: "route-line" }),
    );
    state.plan.order.slice(1, -1).forEach((name, i) => {
      const n = nodes[name];
      if (!hasPos(n)) return;
      badgeLayer.append(
        svgEl("circle", { cx: n.x + 3.6, cy: n.y - 3.6, r: 1.9, class: "badge" }),
        svgEl("text", {
          x: n.x + 3.6,
          y: n.y - 3.6,
          class: "badge-text",
          "text-anchor": "middle",
          "dominant-baseline": "central",
        }, String(i + 1)),
      );
    });
  }

  for (const n of graph.nodes) {
    if (!hasPos(n)) continue;
    const g = svgEl("g", { class: `node ${n.kind}` });
    g.append(
      svgEl("title", {}, describe(n)),
      svgEl("circle", { cx: n.x, cy: n.y, r: 3.4 }),
      svgEl("text", {
        x: n.x, y: n.y, class: "node-name", "text-anchor": "middle", "dominant-baseline": "central",
      }, n.name),
    );
    if (n.label) {
      g.append(svgEl("text", { x: n.x, y: n.y + 6.6, class: "node-label", "text-anchor": "middle" }, n.label));
    }
    nodeLayer.append(g);
  }

  map.append(trailLayer, routeLayer, nodeLayer, labelLayer, badgeLayer);
}

/* ---------- mission planning ---------- */

function planMission() {
  const stations = [...document.querySelectorAll("#stations input:checked")].map((i) => i.value);
  return runPlan({ risk_weight: Number($("priority").value), stations });
}

/** Returns true when a plan was produced. */
async function runPlan(body) {
  clearStatus();
  $("plan-btn").disabled = true;
  try {
    const plan = await api(`/reserves/${enc(state.reserveId)}/missions`, { method: "POST", body });
    state.plan = { route: plan.route, order: plan.visiting_order };
    state.lastRequest = body;
    renderResult(plan);
    drawMap();
    await loadHistory();
    return true;
  } catch (err) {
    state.plan = null;
    $("result").hidden = true;
    drawMap();
    showStatus(err.message);
    return false;
  } finally {
    $("plan-btn").disabled = false;
  }
}

function stat(label, value) {
  return el("div", { class: "stat" },
    el("span", { class: "stat-value" }, value),
    el("span", { class: "stat-label" }, label));
}

const METHOD_TEXT = {
  direct: "Direct route (no stations).",
  brute_force: "Every station order was checked; this one is the cheapest.",
  held_karp: "Best station order found with Held-Karp dynamic programming.",
};

/** Works for both a fresh plan (has legs) and a saved mission summary (no legs). */
function renderResult(plan) {
  const nodes = nodesByName();
  $("totals").replaceChildren(
    stat("Time", `${fmt(plan.total_time)} min`),
    stat("Risk", fmt(plan.total_risk)),
    stat("Time + risk", fmt(plan.total_time + plan.total_risk)),
  );
  $("plan-note").textContent =
    `${priorityLabel(plan.risk_weight)} priority. ${METHOD_TEXT[plan.method] || ""}`;
  $("order").replaceChildren(...plan.visiting_order.map((name) => {
    const node = nodes[name];
    return el("li", {}, node && node.kind === "station" ? `${name} ${node.label || ""}`.trim() : name);
  }));

  const legs = plan.legs || [];
  $("legs-block").hidden = legs.length === 0;
  $("legs").replaceChildren(...legs.map((leg) => el("li", {},
    `${leg.start} \u2192 ${leg.end}: ${fmt(leg.time)} min, risk ${fmt(leg.risk)}`,
    el("span", { class: "path" }, leg.path.join(" \u203a ")))));
  $("result").hidden = false;
}

async function loadHistory() {
  const missions = await api(`/reserves/${enc(state.reserveId)}/missions`);
  const list = $("history");
  list.replaceChildren();
  $("history-empty").hidden = missions.length > 0;
  for (const m of missions.slice(0, 8)) {
    const button = el("button", { type: "button", class: "link" },
      `#${m.id} \u00b7 ${priorityLabel(m.risk_weight)} \u00b7 ${fmt(m.total_time)} min, risk ${fmt(m.total_risk)}`);
    button.addEventListener("click", () => {
      state.plan = { route: m.route, order: m.visiting_order };
      state.lastRequest = { risk_weight: m.risk_weight, stations: m.stations };
      renderResult(m);
      drawMap();
    });
    list.append(el("li", {}, button, el("span", { class: "muted" }, m.visiting_order.join(" \u2192 "))));
  }
}

/* ---------- trail editing (admin) ---------- */

function selectedTrailData() {
  return state.graph.trails.find((t) => t.id === state.selectedTrail);
}

function selectTrail(id) {
  state.selectedTrail = id;
  const t = selectedTrailData();
  if (!t) return;
  $("trail-panel").hidden = false;
  $("trail-name").textContent = `${t.node_a} \u2013 ${t.node_b}`;
  $("trail-facts").textContent = `${fmt(t.time_min)} min \u00b7 risk ${fmt(t.risk)} \u00b7 ${t.status}`;
  $("risk-input").value = t.risk;
  $("note-input").value = t.note || "";
  $("toggle-trail").textContent = t.status === "open" ? "Close trail" : "Reopen trail";
  drawMap();
  $("trail-title").focus();
}

function closeTrailPanel() {
  state.selectedTrail = null;
  $("trail-panel").hidden = true;
  drawMap();
}

async function updateTrail(changes) {
  const key = $("admin-key").value.trim();
  if (!key) {
    showStatus("Enter the admin API key first (the RANGER_ADMIN_KEY the server was started with).");
    return;
  }
  const trailId = state.selectedTrail;
  clearStatus();
  try {
    await api(`/reserves/${enc(state.reserveId)}/trails/${trailId}`, { method: "PATCH", body: changes, key });
    await fetchGraph();
    selectTrail(trailId);
    if (state.plan && state.lastRequest) {
      if (await runPlan(state.lastRequest)) {
        showStatus("Trail updated. The mission was re-planned around the change.", "ok");
      }
    } else {
      showStatus("Trail updated.", "ok");
    }
  } catch (err) {
    showStatus(err.message);
  }
}

function saveTrailFields() {
  const risk = Number($("risk-input").value);
  if (!Number.isFinite(risk) || risk < 0 || risk > 5) {
    showStatus("Risk must be a number from 0 to 5.");
    return;
  }
  const changes = { risk };
  const note = $("note-input").value.trim();
  if (note) changes.note = note;
  return updateTrail(changes);
}

function toggleTrail() {
  const t = selectedTrailData();
  if (t) return updateTrail({ status: t.status === "open" ? "closed" : "open" });
}

init();

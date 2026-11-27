import { BrowserListener, Mic, Speaker } from "/static/voice.js";

const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function fill(table, rows, columns) {
  const body = $(table).querySelector("tbody");
  body.replaceChildren(...(rows.length ? rows
    : [el("tr", {}, el("td", { class: "muted", colspan: String(columns), text: "Nothing yet." }))]));
}

const rupees = (n) => "₹" + Number(n || 0).toLocaleString("en-IN");
const clock = (iso) => (iso ? iso.slice(11, 19) : "");

const app = {
  config: null,
  retailers: [],
  callId: null,
  ended: true,
  busy: false,
  mode: "handsfree",
  listenLanguage: "hi-IN",
  serverVoice: false,
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    body = null;
  }
  if (!response.ok) {
    const detail = body && body.detail;
    throw new Error(typeof detail === "string" ? detail : `${response.status} ${response.statusText}`);
  }
  return body;
}

const post = (path, data) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });

function banner(message, kind = "error") {
  const box = $("banner");
  if (!message) {
    box.classList.add("hidden");
    return;
  }
  box.textContent = message;
  box.className = kind === "info" ? "banner info" : "banner";
}

const speaker = new Speaker({
  onStart: () => {
    mic.agentSpeaking = true;
    listener.pause();
    setCallState("speaking");
  },
  onEnd: () => {
    mic.agentSpeaking = false;
    afterSpeaking();
  },
});

const mic = new Mic({
  onSpeechStart: () => {
    if (speaker.busy) {
      speaker.stop();
      mic.agentSpeaking = false;
    }
    setCallState("hearing");
  },
  onUtterance: (wav) => sendAudio(wav),
  onLevel: (level) => {
    $("level").style.width = `${Math.min(100, level * 500)}%`;
  },
});

const listener = new BrowserListener({
  onText: (text, confidence) => sendText(text, { unclear: confidence > 0 && confidence < 0.35 }),
  onError: (error) => banner(`Browser speech recognition: ${error}`, "info"),
});

function setCallState(state) {
  const pill = $("call-state");
  pill.textContent = state;
  pill.className = "pill" + (["listening", "hearing", "speaking", "thinking"].includes(state) ? " live" : "")
    + (state === "error" ? " bad" : "");
}

function setControls(active) {
  $("say").disabled = !active;
  $("send").disabled = !active;
  $("hangup").disabled = !active;
  $("talk").disabled = !active || app.mode !== "push";
  $("dial").disabled = active || !app.config?.llm.ready;
}

function startListening() {
  if (app.ended || app.busy) return;
  setCallState("listening");
  if (app.serverVoice) {
    mic.enabled = true;
  } else if (app.mode === "handsfree") {
    listener.listen(app.listenLanguage);
  }
}

function stopListening() {
  mic.enabled = false;
  listener.pause();
}

function afterSpeaking() {
  if (app.ended) {
    finishCall();
    return;
  }
  startListening();
}

function finishCall() {
  app.ended = true;
  stopListening();
  setCallState("ended");
  setControls(false);
  loadRetailers();
}

async function dial() {
  const retailerId = $("retailer").value;
  if (!retailerId) return;
  banner("");
  $("retry").classList.add("hidden");
  setCallState("dialing");
  try {
    if (app.serverVoice) await mic.start();
    const result = await post("/api/calls", { retailer_id: retailerId, speak: true });
    app.callId = result.call_id;
    app.ended = false;
    app.busy = false;
    setControls(true);
    handleTurn(result);
  } catch (error) {
    setCallState("idle");
    banner(error.message);
  }
}

function handleTurn(result) {
  app.busy = false;
  render(result.state);
  const reply = result.reply || {};
  if (reply.browser_language) app.listenLanguage = reply.browser_language;
  if (reply.tts_error) banner(`Server voice failed, using the browser voice. ${reply.tts_error}`, "info");
  app.ended = Boolean(result.state && result.state.ended);
  if (reply.text) speaker.play(reply);
  else afterSpeaking();
}

async function runTurn(request, pendingText) {
  if (!app.callId || app.ended || app.busy) return;
  app.busy = true;
  stopListening();
  setCallState("thinking");
  $("retry").classList.add("hidden");
  if (pendingText) addBubble("retailer", pendingText, "");
  addBubble("system", "…", "");
  try {
    handleTurn(await request());
  } catch (error) {
    app.busy = false;
    setCallState("error");
    banner(error.message);
    $("retry").classList.remove("hidden");
    refreshCall();
  }
}

function sendAudio(wav) {
  const form = new FormData();
  form.append("file", wav, "turn.wav");
  runTurn(() => api(`/api/calls/${app.callId}/audio`, { method: "POST", body: form }), null);
}

function sendText(text, options = {}) {
  const clean = text.trim();
  if (!clean) return;
  runTurn(() => post(`/api/calls/${app.callId}/say`, { text: clean, unclear: Boolean(options.unclear), speak: true }), clean);
}

async function retry() {
  if (!app.callId) return;
  app.busy = false;
  runTurn(() => post(`/api/calls/${app.callId}/resume`), null);
}

async function hangup() {
  if (!app.callId) return;
  speaker.stop();
  try {
    const result = await post(`/api/calls/${app.callId}/hangup`);
    render(result.state);
  } catch (error) {
    banner(error.message);
  }
  finishCall();
}

async function refreshCall() {
  if (!app.callId) return;
  try {
    render(await api(`/api/calls/${app.callId}`));
  } catch (error) {
    return;
  }
}

function addBubble(speaker, text, language) {
  const who = speaker === "agent" ? "Agent" : speaker === "retailer" ? "Retailer" : "";
  const bubble = el("div", { class: `bubble ${speaker}` },
    who ? el("span", { class: "who", text: `${who}${language ? " · " + language : ""}` }) : null, text);
  $("transcript").append(bubble);
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

function render(state) {
  if (!state) return;
  $("call-name").textContent = `${state.retailer_name} (${state.retailer_id})`;
  const identity = state.identity === true ? "identity confirmed" : state.identity === false ? "wrong person" : "identity not confirmed";
  $("call-meta").textContent = `call ${state.call_id} · speaking ${state.language} · ${identity}`;
  const transcript = $("transcript");
  transcript.replaceChildren();
  for (const turn of state.transcript) addBubble(turn.speaker, turn.text, turn.language);
  renderOrder(state);
  renderLog(state.events);
  renderBrief(state.brief);
}

const SOURCES = { suggested: ["usual", "src-usual"], retailer_request: ["asked", "src-asked"],
  cross_sell: ["add-on", "src-addon"], alternative: ["swap", "src-swap"] };

const BASKET = { accepted: "usual basket accepted", accepted_plus: "usual basket + add-on",
  edited: "usual basket edited", declined: "usual basket declined", not_offered: "no basket offered" };

const ORDER_STATES = {
  draft: ["building", "warn"],
  read_back: ["read back, waiting for yes", "warn"],
  confirmed: ["confirmed", "good"],
  cancelled: ["cancelled", "bad"],
};

function renderOrder(state) {
  const order = state.order;
  const body = $("order-table").querySelector("tbody");
  body.replaceChildren(...order.lines.map((line) => el("tr", {},
    el("td", {}, line.product, " ", el("span", { class: `tag ${(SOURCES[line.source] || ["", ""])[1]}`,
      text: (SOURCES[line.source] || [line.source])[0] })),
    el("td", { class: "num", text: line.quantity }),
    el("td", { class: "num", text: rupees(line.unit_price_inr) }),
    el("td", { class: "num", text: rupees(line.line_value_inr) }),
    el("td", { class: "num", text: state.order.state === "confirmed" ? "–" : line.available_after }),
  )));
  $("order-total").textContent = rupees(order.total_inr);
  const [label, tone] = order.lines.length || order.state !== "draft" ? ORDER_STATES[order.state] : ["empty", ""];
  $("order-state").textContent = label;
  $("order-state").className = `pill ${tone}`;
  const outcome = $("outcome");
  outcome.replaceChildren();
  if (state.basket) outcome.append(el("span", { class: "pill live",
    text: state.ended || state.order.state === "confirmed" ? BASKET[state.basket.outcome]
      : `usual basket offered ${rupees(state.basket.offered_value_inr)}` }));
  if (state.order_id) outcome.append(el("span", { class: "pill good", text: `order ${state.order_id}` }));
  if (state.outcome) outcome.append(el("span", { class: "pill", text: state.outcome.replaceAll("_", " ") }));
  for (const task of state.tasks) outcome.append(el("span", { class: "pill warn", text: task }));
}

function describe(event) {
  const a = event.args || {};
  const parts = [event.name];
  if (a.sku) parts.push(a.sku);
  if (a.quantity !== undefined) parts.push(`x ${a.quantity}${a.unit && a.unit !== "piece" ? " " + a.unit : ""}`);
  if (a.query) parts.push(`"${a.query}"`);
  if (a.is_right_shop !== undefined) parts.push(a.is_right_shop ? "yes" : "no");
  if (a.category) parts.push(a.category);
  if (a.outcome) parts.push(a.outcome);
  if (a.when) parts.push(a.when);
  return parts.join(" ");
}

function renderLog(events) {
  const items = [];
  for (const event of events.slice().reverse()) {
    if (event.kind !== "tool") continue;
    const ok = event.ok;
    const said = event.args && event.args.retailer_said ? `heard: "${event.args.retailer_said}"` : "";
    let detail = ok ? said : event.error;
    if (ok && event.result && event.result.pack_tip) detail = `${said} · ${event.result.pack_tip}`;
    items.push(el("li", {},
      el("span", { class: "t", text: clock(event.t) }),
      el("div", {},
        el("div", { class: ok ? "ok" : "guard", text: `${ok ? "✓" : "✗"} ${describe(event)}` }),
        detail ? el("div", { class: "args", text: detail }) : null)));
  }
  $("log").replaceChildren(...items);
}

function renderBrief(brief) {
  const box = $("brief");
  if (!brief) return;
  const meta = el("dl", { class: "brief-meta" },
    el("dt", { text: "Shop" }), el("dd", { text: `${brief.name}, ${brief.city} (${brief.segment})` }),
    el("dt", { text: "Language" }), el("dd", { text: brief.language }),
    el("dt", { text: "Calling window" }), el("dd", { text: brief.window }),
    el("dt", { text: "Order cycle" }), el("dd", { text: `every ${brief.cycle_days} days` }),
    el("dt", { text: "Typical order" }), el("dd", { text: rupees(brief.typical_order_value) }),
    el("dt", { text: "Preferred brands" }), el("dd", { text: brief.preferred_brands.join(", ") }),
    el("dt", { text: "Distributor" }), el("dd", { text: brief.distributor_id }),
  );
  const parts = [el("div", { class: "card-head" }, el("h2", { text: "Call brief" }),
    brief.do_not_call ? el("span", { class: "pill bad", text: "do not call" })
      : el("span", { class: "pill", text: `priority ${brief.priority.toLocaleString("en-IN")}` })), meta];

  if (brief.skip_reason) parts.push(el("p", { class: "muted", text: brief.skip_reason }));

  if (brief.lead.length) {
    parts.push(el("div", { class: "section-title", text: `Usual basket, offered in one question (${rupees(brief.expected_value)})` }));
    parts.push(el("table", { class: "table" },
      el("thead", {}, el("tr", {}, ["Product", "Last / prev", "Days", "Suggest", "Stock"].map((h, i) =>
        el("th", { class: i ? "num" : "", text: h })))),
      el("tbody", {}, brief.lead.map((item) => el("tr", { title: item.why },
        el("td", { text: item.product }),
        el("td", { class: "num", text: `${item.last_qty} / ${item.previous_qty}` }),
        el("td", { class: "num", text: item.days_since }),
        el("td", { class: "num", text: item.suggested_qty }),
        el("td", { class: "num", text: item.stock_status === "Available" ? item.available : `${item.available} low` }),
      )))));
  }
  if (brief.others.length) {
    parts.push(el("div", { class: "section-title", text: "Also due" }));
    parts.push(el("div", { class: "small", text: brief.others.map((i) => `${i.product} (${i.suggested_qty})`).join(" · ") }));
  }
  if (brief.unavailable.length) {
    parts.push(el("div", { class: "section-title", text: "Out of stock at distributor" }));
    parts.push(el("div", { class: "small" }, brief.unavailable.map((i) =>
      el("div", { text: `${i.product}${i.alternative ? " → offer " + i.alternative.split(" ").slice(1).join(" ") : ""}` }))));
  }
  if (brief.cross_sell) {
    parts.push(el("div", { class: "section-title", text: `Cross-sell (${brief.cross_sell.kind.replace("_", " ")})` }));
    parts.push(el("div", { class: "cross" }, el("strong", { text: `${brief.cross_sell.product} x ${brief.cross_sell.suggest_qty}` }),
      el("div", { text: brief.cross_sell.pitch })));
  }
  box.replaceChildren(...parts);
}

async function loadRetailers() {
  app.retailers = await api("/api/retailers");
  const select = $("retailer");
  const current = select.value || "R003";
  select.replaceChildren(...app.retailers.map((r) => el("option", { value: r.id,
    text: `${r.id} · ${r.name} · ${r.city} · ${r.language}${r.do_not_call ? " · do not call" : ""}` })));
  select.value = app.retailers.some((r) => r.id === current) ? current : app.retailers[0].id;
  showRetailer();
}

async function showRetailer() {
  const id = $("retailer").value;
  const retailer = app.retailers.find((r) => r.id === id);
  if (!retailer) return;
  $("retailer-hint").textContent = `${retailer.segment} · distributor ${retailer.distributor_id} · window ${retailer.window}`
    + (retailer.do_not_call ? " · on the do-not-call list" : "");
  if (app.ended) {
    try {
      renderBrief(await api(`/api/retailers/${id}/brief`));
    } catch (error) {
      banner(error.message);
    }
  }
}

async function loadPlan() {
  const plan = await api("/api/plan");
  $("plan-summary").textContent = `${plan.calls.length} calls, about ${rupees(plan.expected_value)} in suggested orders, data as of ${plan.date}`;
  $("plan-table").querySelector("tbody").replaceChildren(...plan.calls.map((c) => el("tr", {},
    el("td", { text: c.window }),
    el("td", { text: `${c.retailer_id} ${c.name}` }),
    el("td", { text: c.city }),
    el("td", { text: c.language }),
    el("td", { text: c.segment }),
    el("td", { class: "num", text: c.priority.toLocaleString("en-IN") }),
    el("td", { class: "num", text: rupees(c.expected_value) }),
    el("td", { class: "small", text: c.lead.join(", ") }),
    el("td", { class: "small", text: c.cross_sell || "" }),
    el("td", {}, el("button", { text: "Call", onclick: () => pickRetailer(c.retailer_id) })),
  )));
  fill("skip-table", plan.skipped.map((c) => el("tr", {},
    el("td", { text: `${c.retailer_id} ${c.name}` }), el("td", { text: c.city }), el("td", { text: c.skip_reason }))), 3);
  fill("callback-table", plan.callbacks.map((t) => el("tr", {},
    el("td", { text: t.id }), el("td", { text: `${t.retailer_id} ${t.retailer_name}` }),
    el("td", { text: t.due_at ? t.due_at.replace("T", " ") : t.when }), el("td", { text: t.reason }))), 4);
}

function pickRetailer(id) {
  $("retailer").value = id;
  showRetailer();
  switchTab("call");
}

async function loadOutcomes() {
  const data = await api("/api/outcomes");
  const delivery = Object.fromEntries(data.deliveries.map((d) => [d.order_id, d.status]));
  $("orders").replaceChildren(...(data.orders.length ? data.orders.map((o) => el("div", { class: "order-card" },
    el("div", { class: "row between" },
      el("strong", { text: `${o.order_id} · ${o.retailer_id} ${o.retailer_name}` }),
      el("span", { class: "pill good", text: rupees(o.order_value_inr) })),
    el("div", { class: "small muted", text: `${o.lines.map((l) => `${l.product} x ${l.quantity}`).join(", ")}`
      + (delivery[o.order_id] ? ` · webhook ${delivery[o.order_id]}` : "") }),
    el("details", {}, el("summary", { class: "small", text: "Order record (JSON)" }),
      el("pre", { text: JSON.stringify(o, null, 2) })),
  )) : [el("p", { class: "muted", text: "No orders yet." })]));

  const detail = (t) => t.kind === "callback" ? `${t.when}${t.due_at ? " (" + t.due_at.replace("T", " ") + ")" : ""}: ${t.reason}`
    : t.kind === "complaint" ? `${t.summary}${t.related && t.related.transaction_id ? " · last order " + t.related.transaction_id : ""}`
      : `${t.category}: ${t.summary}`;
  fill("task-table", data.tasks.map((t) => el("tr", {},
    el("td", { text: t.id }), el("td", { text: t.kind }), el("td", { text: `${t.retailer_id} ${t.retailer_name}` }),
    el("td", { class: "small", text: detail(t) }))), 4);
  fill("insight-table", data.insights.map((i) => el("tr", {},
    el("td", { text: i.id }), el("td", { text: i.city }), el("td", { class: "small", text: i.summary }))), 3);
  fill("dnc-table", data.opt_outs.map((d) => el("tr", {},
    el("td", { text: `${d.retailer_id} ${d.retailer_name}` }), el("td", { class: "small", text: d.reason }),
    el("td", { class: "small", text: d.created_at.replace("T", " ").slice(0, 16) }))), 3);
  const offered = data.calls.filter((c) => c.basket && c.basket.outcome !== "not_offered");
  const taken = offered.filter((c) => ["accepted", "accepted_plus", "edited"].includes(c.basket.outcome));
  const asIs = offered.filter((c) => ["accepted", "accepted_plus"].includes(c.basket.outcome));
  $("basket-summary").textContent = offered.length
    ? `Usual basket offered on ${offered.length} call${offered.length === 1 ? "" : "s"}: ordered on ${taken.length} `
      + `(${Math.round(100 * taken.length / offered.length)}%), `
      + `${asIs.length} taken as offered with a single yes`
    : "No usual basket offered yet.";
  fill("calls-table", data.calls.map((c) => el("tr", {},
    el("td", { text: c.call_id }), el("td", { text: `${c.retailer_id} ${c.retailer_name}` }),
    el("td", { text: c.outcome.replaceAll("_", " ") }),
    el("td", { text: c.basket ? BASKET[c.basket.outcome] : "" }),
    el("td", { class: "num", text: c.order_value_inr ? rupees(c.order_value_inr) : "" }),
    el("td", { text: c.languages.join(", ") }), el("td", { class: "num", text: c.turns }),
    el("td", { class: "num", text: c.guard_rejections }))), 8);
}

async function loadNotes() {
  const notes = await api("/api/audit");
  $("notes").replaceChildren(...notes.map((n) => el("li", {},
    el("div", { class: "title", text: n.title }), el("div", { text: n.detail }),
    el("div", { class: "small muted", text: n.impact }))));
}

function switchTab(name) {
  for (const button of $("tabs").querySelectorAll("button")) button.classList.toggle("active", button.dataset.tab === name);
  for (const section of document.querySelectorAll(".tab")) section.classList.toggle("hidden", section.id !== `tab-${name}`);
  const loaders = { plan: loadPlan, outcomes: loadOutcomes, notes: loadNotes };
  if (loaders[name]) loaders[name]().catch((error) => banner(error.message));
}

function setMode(mode) {
  app.mode = mode;
  mic.mode = mode;
  for (const button of $("mode").querySelectorAll("button")) button.classList.toggle("active", button.dataset.mode === mode);
  $("talk").classList.toggle("hidden", mode !== "push");
  $("talk").disabled = app.ended || mode !== "push";
  if (!app.ended && !app.busy && !speaker.busy) {
    stopListening();
    startListening();
  }
}

function pressTalk(down) {
  if (app.mode !== "push" || app.ended) return;
  $("talk").classList.toggle("on", down);
  if (app.serverVoice) {
    mic.pushHeld = down;
    if (down && speaker.busy) {
      speaker.stop();
      mic.agentSpeaking = false;
    }
  } else if (down && !app.busy) {
    speaker.stop();
    listener.listen(app.listenLanguage);
  }
}

function bind() {
  $("tabs").addEventListener("click", (event) => {
    if (event.target.dataset.tab) switchTab(event.target.dataset.tab);
  });
  $("retailer").addEventListener("change", showRetailer);
  $("dial").addEventListener("click", dial);
  $("hangup").addEventListener("click", hangup);
  $("retry").addEventListener("click", retry);
  $("refresh-outcomes").addEventListener("click", () => loadOutcomes().catch((error) => banner(error.message)));
  $("mode").addEventListener("click", (event) => {
    if (event.target.dataset.mode) setMode(event.target.dataset.mode);
  });
  $("barge").addEventListener("change", (event) => {
    mic.bargeIn = event.target.checked;
  });
  $("say-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("say").value;
    $("say").value = "";
    speaker.stop();
    mic.agentSpeaking = false;
    sendText(text);
  });
  const talk = $("talk");
  talk.addEventListener("pointerdown", () => pressTalk(true));
  talk.addEventListener("pointerup", () => pressTalk(false));
  talk.addEventListener("pointerleave", () => pressTalk(false));
  document.addEventListener("keydown", (event) => {
    if (event.code === "Space" && !event.repeat && document.activeElement.tagName !== "INPUT") {
      event.preventDefault();
      pressTalk(true);
    }
  });
  document.addEventListener("keyup", (event) => {
    if (event.code === "Space" && document.activeElement.tagName !== "INPUT") pressTalk(false);
  });
}

async function boot() {
  bind();
  try {
    app.config = await api("/api/config");
  } catch (error) {
    banner(`Server not reachable: ${error.message}`);
    return;
  }
  app.serverVoice = app.config.voice === "sarvam";
  const llm = app.config.llm;
  $("status").replaceChildren(
    el("span", { class: `pill ${llm.ready ? "good" : "bad"}`, text: `${llm.provider}: ${llm.model || "not set"}` }),
    el("span", { class: "pill", text: app.serverVoice ? "voice: Sarvam" : "voice: browser" }),
  );
  if (!llm.ready) banner("No language model key found. Add GEMINI_API_KEY to .env and restart the server.", "info");
  else if (!app.serverVoice && !listener.supported) {
    banner("No SARVAM_API_KEY and this browser has no speech recognition. Typing still works; use Chrome or Edge for voice.", "info");
  }
  setControls(false);
  await loadRetailers();
}

boot();

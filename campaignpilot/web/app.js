/* CampaignPilot web app. Vanilla JS, no build step.
   All model output is inserted with textContent, never innerHTML. */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const POLL_MS = 1200;

  const AGENTS = [
    { key: "planner", node: "plan", name: "Planner", role: "Turns the goal into a brief" },
    { key: "segmenter", node: "segment", name: "Segmenter", role: "Writes SQL to find customers" },
    { key: "researcher", node: "research", name: "Researcher", role: "Brand rules, products, past results" },
    { key: "writer", node: "write", name: "Writer", role: "Drafts two versions" },
    { key: "reviewer", node: "review", name: "Reviewer", role: "Checks rules and quality" },
    { key: "human", node: "human_approval", name: "You", role: "Approve, change or reject" },
    { key: "finalize", node: "finalize", name: "Send", role: "Personalises each message" },
  ];

  const state = { config: {}, run: null, pollTimer: null, busy: false };

  // ------------------------------------------------------------ helpers
  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "style") node.style.cssText = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("data-")) node.setAttribute(k, v);
      else node[k] = v;
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const store = {
    get(k) { try { return window.localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { v == null ? window.localStorage.removeItem(k) : window.localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  };

  async function api(method, path, body) {
    const headers = { "Content-Type": "application/json" };
    const code = $("#access-code").value || store.get("cp-access-code") || "";
    if (code) headers["X-Access-Code"] = code;
    let res;
    try {
      res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    } catch {
      throw new Error("Can't reach the CampaignPilot server. Check that it's running, then try again.");
    }
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    if (!res.ok) {
      let detail = data && data.detail;
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
      const err = new Error(detail || `Request failed with status ${res.status}.`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  const pct = (x) => `${(Number(x || 0) * 100).toFixed(1)}%`;
  const money = (x) => `$${Number(x || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  // Fill the personalisation token for a preview customer (same rule as the backend).
  const personalise = (text, name) => String(text || "").replace(/\{\{\s*first_name\s*\}\}/g, name || "there");
  const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

  // ------------------------------------------------------------- config
  async function loadConfig() {
    try {
      state.config = await api("GET", "/health");
      const llm = state.config.llm_provider === "fake" ? "Offline demo model" : (state.config.model || "Gemini");
      const tools = state.config.tools_mode === "mcp" ? "tools over MCP" : "tools in-process";
      $("#config").replaceChildren(el("b", { text: llm }), ", ", tools);
      $("#access-field").hidden = !state.config.access_code_required;
      const saved = store.get("cp-access-code");
      if (saved) $("#access-code").value = saved;
    } catch (e) {
      $("#config").textContent = e.message;
    }
  }

  // ------------------------------------------------------------- views
  function showView(name) {
    for (const tab of document.querySelectorAll(".view-tab")) {
      if (tab.dataset.view === name) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    }
    $("#view-campaigns").hidden = name !== "campaigns";
    $("#view-analytics").hidden = name !== "analytics";
    if (name === "analytics") loadAnalytics();
  }

  // ----------------------------------------------------------- history
  async function loadHistory() {
    try {
      const rows = await api("GET", "/campaigns?limit=12");
      const list = $("#history-list");
      list.replaceChildren(...rows.map((r) => el("li", { "data-status": r.status },
        el("span", { class: "h-goal", text: r.goal }),
        el("span", { class: "h-meta", text: `${r.status === "approved" ? "Approved" : "Rejected"}, ${r.channel}, ${plural(r.segment_size, "customer")}${r.score ? `, scored ${r.score}/10` : ""}` }),
      )));
      $("#history-empty").hidden = rows.length > 0;
    } catch { /* history is optional */ }
  }

  // --------------------------------------------------------- start a run
  async function startRun(ev) {
    ev.preventDefault();
    const goal = $("#goal").value.trim();
    const channel = new FormData($("#brief-form")).get("channel") || null;
    const err = $("#form-error");
    err.hidden = true;
    if (goal.length < 10) {
      err.textContent = "Describe the goal in at least a few words, for example who to target and what you want them to do.";
      err.hidden = false;
      $("#goal").focus();
      return;
    }
    const code = $("#access-code").value.trim();
    if (state.config.access_code_required && !code) {
      err.textContent = "Enter the access code to run campaigns.";
      err.hidden = false;
      $("#access-code").focus();
      return;
    }
    if (code) store.set("cp-access-code", code);

    setBusy(true);
    try {
      const run = await api("POST", "/campaigns", { goal, channel });
      openRun(run);
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
      if (e.status === 401) store.set("cp-access-code", null);
    } finally {
      setBusy(false);
    }
  }

  function setBusy(busy) {
    state.busy = busy;
    const btn = $("#run-button");
    btn.disabled = busy;
    btn.textContent = busy ? "Starting…" : "Run agents";
  }

  function openRun(run, { scroll = true } = {}) {
    store.set("cp-last-run", run.thread_id);
    $("#empty-state").hidden = true;
    $("#run").hidden = false;
    $("#feedback").value = "";
    render(run);
    schedulePoll(run);
    if (scroll) $("#run").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  }

  function schedulePoll(run) {
    clearTimeout(state.pollTimer);
    if (run.status !== "running") return;
    state.pollTimer = setTimeout(async () => {
      try {
        const next = await api("GET", `/campaigns/${run.thread_id}`);
        render(next);
        schedulePoll(next);
        if (next.status === "approved" || next.status === "rejected") loadHistory();
      } catch (e) {
        showRunError(e.message);
      }
    }, POLL_MS);
  }

  // -------------------------------------------------------------- render
  const STATUS_TEXT = {
    running: "Agents are working",
    awaiting_approval: "Waiting for your decision",
    approved: "Approved and sent to the outbox",
    rejected: "Rejected. Nothing was sent.",
    error: "The run stopped with an error",
  };

  function render(run) {
    state.run = run;
    const s = run.state || {};
    $("#run-status").textContent = STATUS_TEXT[run.status] || run.status;
    $("#run-status").dataset.status = run.status;
    $("#run-goal").textContent = s.goal || "";
    renderStrips(run);
    if (run.error) showRunError(run.error); else $("#run-error").hidden = true;
    renderAudience(s.segment);
    renderMessages(s.brief, s.content, s.segment);
    renderReview(s.review, s.research);
    renderResult(run);
    $("#decision").hidden = run.status !== "awaiting_approval";
  }

  function showRunError(message) {
    const box = $("#run-error");
    box.textContent = `${message} Start a new run, or check the server logs for details.`;
    box.hidden = false;
  }

  function linesFor(trace, key) {
    const prefix = `${key}:`;
    return (trace || []).filter((l) => l.startsWith(prefix)).map((l) => l.slice(prefix.length).trim());
  }

  function renderStrips(run) {
    const s = run.state || {};
    const trace = s.trace || [];
    const activeNode = run.status === "running" ? (run.next || [])[0] : run.status === "awaiting_approval" ? "human_approval" : null;
    const drafts = linesFor(trace, "writer").length;

    const items = AGENTS.map((agent, i) => {
      const lines = linesFor(trace, agent.key);
      const last = lines[lines.length - 1] || "";
      let st = lines.length ? "done" : "wait";
      let tag = lines.length ? "Done" : "";
      let detail = last;

      if (agent.key === "reviewer" && lines.length > 1) tag = `Done, ${plural(lines.length, "review")}`;
      if (agent.key === "writer" && drafts > 1) tag = `${plural(drafts, "draft")}`;
      if (agent.key === "reviewer" && /issue/.test(last) && !/passed/.test(last)) { st = "hold"; tag = "Sent back"; }
      if (agent.key === "human" && last) {
        tag = last.startsWith("approve") ? "Approved" : last.startsWith("reject") ? "Rejected" : "Changes requested";
        st = last.startsWith("reject") ? "hold" : "done";
      }
      if (agent.key === "finalize" && last) tag = /approved/.test(last) ? "Sent" : "Not sent";
      if (agent.key === "finalize" && /rejected/.test(last)) st = "hold";

      if (agent.node === activeNode) {
        st = "active";
        tag = agent.key === "human" ? "Your turn" : "Working";
        if (agent.key === "human") detail = "Read the messages below, then approve, request changes or reject.";
        else if (agent.key === "writer" && drafts) detail = "Rewriting with the reviewer's notes";
        else if (!detail) detail = agent.role + "…";
      }
      if (run.status === "error" && !lines.length && agent.node === (run.next || [])[0]) { st = "hold"; tag = "Failed"; }

      return el("li", { class: "strip", "data-state": st, "aria-label": `${agent.name}: ${tag || "waiting"}` },
        el("span", { class: "strip-no", text: String(i + 1) }),
        el("span", { class: "strip-name" }, el("b", { text: agent.name }), el("small", { text: agent.role })),
        el("span", { class: "strip-detail", text: detail }),
        el("span", { class: "strip-tag", text: tag }),
      );
    });
    $("#strips").replaceChildren(...items);
  }

  function table(target, rows, columns) {
    const head = el("tr", {}, columns.map((c) => el("th", { class: c.num ? "num" : "", text: c.label })));
    const body = rows.map((r) => el("tr", {}, columns.map((c) => el("td", { class: c.num ? "num" : "", text: c.format ? c.format(r[c.key], r) : (r[c.key] ?? "") }))));
    target.replaceChildren(el("thead", {}, head), el("tbody", {}, body));
  }

  function renderAudience(seg) {
    const box = $("#audience");
    if (!seg) { box.hidden = true; return; }
    box.hidden = false;
    $("#audience-count").textContent = Number(seg.count).toLocaleString();
    $("#audience-why").textContent = seg.rationale || "";
    $("#audience-sql").textContent = seg.sql || "";
    const cols = [
      { key: "first_name", label: "Name" }, { key: "country", label: "Country" }, { key: "city", label: "City" },
      { key: "industry", label: "Industry" }, { key: "plan", label: "Plan" }, { key: "last_active_date", label: "Last active" },
    ].filter((c) => seg.sample && seg.sample[0] && c.key in seg.sample[0]);
    table($("#audience-sample"), seg.sample || [], cols);
  }

  function renderVariant(channel, raw, name) {
    const v = { ...raw, subject_or_headline: personalise(raw.subject_or_headline, name), body: personalise(raw.body, name),
      preview_text: personalise(raw.preview_text, name) };
    const label = el("p", { class: "variant-label", text: v.label });
    if (channel === "ads") {
      return el("article", { class: "variant" }, label, el("div", { class: "ad" },
        el("p", { class: "ad-sponsored", text: "Sponsored" }),
        el("p", { class: "ad-url", text: "brightloop.example/pro" }),
        el("h3", { class: "ad-headline", text: v.subject_or_headline }),
        el("p", { class: "ad-desc", text: v.preview_text || v.body }),
        el("span", { class: "ad-cta", text: v.cta }),
      ));
    }
    if (channel === "social") {
      return el("article", { class: "variant" }, label, el("div", { class: "post" },
        el("div", { class: "post-author" }, el("span", { class: "post-avatar", text: "B" }),
          el("span", {}, el("b", { text: "Brightloop" }), el("small", { text: "Promoted post" }))),
        el("p", { class: "post-hook", text: v.subject_or_headline }),
        el("p", { class: "post-body", text: v.body }),
        (v.hashtags || []).length ? el("p", { class: "post-tags", text: v.hashtags.join(" ") }) : null,
        el("p", { class: "post-cta", text: v.cta }),
      ));
    }
    return el("article", { class: "variant" }, label, el("div", { class: "mail" },
      el("div", { class: "mail-head" },
        el("p", { class: "mail-from", text: "From: Brightloop <hello@brightloop.example>" }),
        el("p", { class: "mail-subject", text: v.subject_or_headline }),
        v.preview_text ? el("p", { class: "mail-preview", text: v.preview_text }) : null),
      el("p", { class: "mail-body", text: v.body }),
      el("span", { class: "mail-cta", text: v.cta }),
    ));
  }

  function renderMessages(brief, content, segment) {
    const box = $("#messages");
    if (!content || !content.variants) { box.hidden = true; return; }
    box.hidden = false;
    const channel = (brief && brief.channel) || "email";
    const customer = segment && segment.sample && segment.sample[0];
    const name = customer && customer.first_name;
    const usesToken = content.variants.some((v) => /\{\{\s*first_name\s*\}\}/.test(`${v.subject_or_headline} ${v.body}`));
    $("#variants").replaceChildren(...content.variants.map((v) => renderVariant(channel, v, name)));
    $("#send-time").textContent = [
      usesToken && name ? `Previewed as ${name}, the first customer in the audience.` : "",
      content.send_time_recommendation || "",
    ].filter(Boolean).join(" ");
  }

  function renderReview(review, research) {
    const box = $("#review");
    if (!review) { box.hidden = true; return; }
    box.hidden = false;
    $("#review-score").replaceChildren(String(review.score), el("small", { text: " / 10" }));
    const issues = review.issues || [];
    $("#review-issues").replaceChildren(...(issues.length
      ? issues.map((i) => el("li", { text: i }))
      : [el("li", { class: "ok", text: "Passes every brand rule: banned words, length limits, call to action, unsubscribe line and personalisation." })]));
    $("#review-suggestions").replaceChildren(...(review.suggestions || []).map((s) => el("li", { text: s })));
    const seen = new Set();
    const sources = ((research && research.knowledge) || []).filter((k) => {
      const id = `${k.source}|${k.heading}`;
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    });
    $("#sources").replaceChildren(...sources.map((k) => el("li", { text: `${k.source.replace(/\.md$/, "").replace(/_/g, " ")}: ${k.heading}` })));
  }

  function renderResult(run) {
    const box = $("#result");
    const result = (run.state || {}).result;
    if (!result || (run.status !== "approved" && run.status !== "rejected")) { box.hidden = true; return; }
    box.hidden = false;
    box.dataset.status = run.status;
    if (run.status === "approved") {
      $("#result-title").textContent = "Sent to the outbox";
      $("#result-text").textContent = "Each customer gets their own copy with their name filled in, split between the two versions. In production this step would call an email or ads platform; here it writes a file you can inspect.";
      table($("#result-table"), result.previews || [], [
        { key: "customer_id", label: "Customer", num: true }, { key: "variant", label: "Version" },
        { key: "subject", label: "Subject or headline" }, { key: "cta", label: "Call to action" },
      ]);
    } else {
      $("#result-title").textContent = "Rejected";
      $("#result-text").textContent = "Nothing was sent. Start a new run with a different goal or channel.";
      $("#result-table").replaceChildren();
    }
  }

  // ------------------------------------------------------------ decision
  async function decide(ev) {
    ev.preventDefault();
    const decision = ev.submitter && ev.submitter.dataset.decision;
    const feedback = $("#feedback").value.trim();
    const err = $("#decision-error");
    err.hidden = true;
    if (decision === "revise" && !feedback) {
      err.textContent = "Write what should change so the writer knows what to fix.";
      err.hidden = false;
      $("#feedback").focus();
      return;
    }
    const buttons = [...document.querySelectorAll("#decision button")];
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const run = await api("POST", `/campaigns/${state.run.thread_id}/decision`, { decision, feedback });
      $("#feedback").value = "";
      render(run);
      schedulePoll(run);
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  // ----------------------------------------------------------- analytics
  async function loadAnalytics() {
    const bench = $("#bench");
    try {
      const [rows, past] = await Promise.all([api("GET", "/analytics/benchmarks"), api("GET", "/analytics/campaigns?top=20")]);
      const metrics = [["Open or view", "avg_open_rate"], ["Click-through", "avg_ctr"], ["Conversion", "avg_conversion_rate"]];
      bench.replaceChildren(...rows.map((r) => el("div", { class: "bench-channel" },
        el("h3", { text: r.channel }),
        metrics.map(([label, key]) => el("div", { class: "bench-row" },
          el("span", { text: label }),
          el("span", { class: "bench-track" }, el("span", { class: "bench-fill", style: `width:${Math.min(100, r[key] * 100)}%` })),
          el("span", { class: "num", text: pct(r[key]) }))),
        el("p", { class: "bench-meta", text: `${plural(r.campaigns, "campaign")}, ${money(r.total_revenue)} revenue, return on ad spend ${Number(r.avg_roas).toFixed(2)}` }),
      )));
      table($("#past"), past, [
        { key: "name", label: "Campaign" }, { key: "channel", label: "Channel" },
        { key: "sent", label: "Reached", num: true, format: (v) => Number(v).toLocaleString() },
        { key: "open_rate", label: "Open", num: true, format: pct }, { key: "ctr", label: "CTR", num: true, format: pct },
        { key: "conversion_rate", label: "Conversion", num: true, format: pct },
        { key: "revenue", label: "Revenue", num: true, format: money },
      ]);
    } catch (e) {
      bench.replaceChildren(el("p", { class: "form-error", text: e.message }));
    }
  }

  // ---------------------------------------------------------------- boot
  function init() {
    $("#brief-form").addEventListener("submit", startRun);
    $("#decision").addEventListener("submit", decide);
    for (const b of document.querySelectorAll(".example")) {
      b.addEventListener("click", () => {
        $("#goal").value = b.textContent;
        const radio = document.querySelector(`input[name="channel"][value="${b.dataset.channel}"]`);
        if (radio) radio.checked = true;
        $("#goal").focus();
      });
    }
    for (const tab of document.querySelectorAll(".view-tab")) tab.addEventListener("click", () => showView(tab.dataset.view));
    loadConfig();
    loadHistory();
    const last = store.get("cp-last-run");
    if (last) api("GET", `/campaigns/${last}`).then((run) => openRun(run, { scroll: false })).catch(() => store.set("cp-last-run", null));
  }

  init();
})();

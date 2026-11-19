// Recurring To-do sidebar panel: add, edit, remove and run recurring tasks.
// Plain web component, no build step. Talks to the integration over the
// recurring_todo/* WebSocket commands.

const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const FREQ_UNIT = { daily: ["day", "days"], weekly: ["week", "weeks"], monthly: ["month", "months"] };
const ERRORS = {
  name_required: "Give the task a name.",
  already_exists: "A recurring task with this name already exists in this list.",
  weekdays_required: "Pick at least one weekday.",
  not_found: "This task no longer exists. The list has been refreshed.",
};

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const ICONS = {
  menu: "M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z",
  plus: "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z",
  bell: "M21,19V20H3V19L5,17V11C5,7.9 7.03,5.17 10,4.29C10,4.19 10,4.1 10,4A2,2 0 0,1 12,2A2,2 0 0,1 14,4C14,4.1 14,4.19 14,4.29C16.97,5.17 19,7.9 19,11V17L21,19M14,21A2,2 0 0,1 12,23A2,2 0 0,1 10,21",
  bellOff: "M20.84,22.73L18.11,20H3V19L5,17V11C5,9.89 5.29,8.85 5.78,7.93L1.11,3.27L2.39,2L22.11,21.72L20.84,22.73M19,15.8V11C19,7.9 16.97,5.17 14,4.29C14,4.19 14,4.1 14,4A2,2 0 0,0 12,2A2,2 0 0,0 10,4C10,4.1 10,4.19 10,4.29C9.03,4.58 8.15,5.08 7.43,5.73L19,17.3V15.8M12,23A2,2 0 0,0 14,21H10A2,2 0 0,0 12,23Z",
  play: "M8,5.14V19.14L19,12.14L8,5.14Z",
  pencil: "M20.71,7.04C21.1,6.65 21.1,6 20.71,5.63L18.37,3.29C18,2.9 17.35,2.9 16.96,3.29L15.12,5.12L18.87,8.87M3,17.25V21H6.75L17.81,9.93L14.06,6.18L3,17.25Z",
  trash: "M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z",
  phone: "M17,19H7V5H17M17,1H7C5.89,1 5,1.89 5,3V21A2,2 0 0,0 7,23H17A2,2 0 0,0 19,21V3C19,1.89 18.1,1 17,1Z",
  sync: "M12,18A6,6 0 0,1 6,12C6,11 6.25,10.03 6.7,9.2L5.24,7.74C4.46,8.97 4,10.43 4,12A8,8 0 0,0 12,20V23L16,19L12,15M12,4V1L8,5L12,9V6A6,6 0 0,1 18,12C18,13 17.75,13.97 17.3,14.8L18.76,16.26C19.54,15.03 20,13.57 20,12A8,8 0 0,0 12,4Z",
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[name]}"/></svg>`;

class RecurringTodoPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._lists = [];
    this._entryId = null;
    this._tasks = [];
    this._targets = []; // [{service, name, phone}]
    this._loading = true;
    this._loadError = null;
    this._editing = null; // task being edited, or {} for a new one
    this._form = null;
    this._formError = null;
    this._saving = false;
    this._lastUpdated = null;
    this._rendered = false;
  }

  // ------------------------------------------------------------ HA props
  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._renderShell();
      this._loadLists();
      return;
    }
    // Refresh when the list's entity changes (task fired, edited elsewhere...).
    const list = this._currentList();
    const state = list?.entity_id ? hass.states[list.entity_id] : null;
    if (state && state.last_updated !== this._lastUpdated) {
      const known = this._lastUpdated !== null;
      this._lastUpdated = state.last_updated;
      if (known) this._loadTasks({ quiet: true });
    }
  }
  get hass() {
    return this._hass;
  }
  set narrow(v) {
    this._narrow = v;
    this.shadowRoot.querySelector(".menu")?.toggleAttribute("hidden", !v);
  }
  set panel(p) {
    this._panel = p;
  }

  // ------------------------------------------------------------ data
  async _loadLists() {
    try {
      const [lists, targets] = await Promise.all([
        this._hass.callWS({ type: "recurring_todo/lists" }),
        this._hass.callWS({ type: "recurring_todo/notify_targets" }).catch(() => []),
      ]);
      this._lists = lists;
      this._targets = targets;
      const saved = this._readPref();
      const ids = this._lists.map((l) => l.entry_id);
      this._entryId = ids.includes(this._entryId) ? this._entryId : ids.includes(saved) ? saved : ids[0] || null;
      this._loadError = null;
      await this._loadTasks();
    } catch (err) {
      this._loadError = err?.message || String(err);
      this._loading = false;
      this._renderMain();
    }
  }

  async _loadTasks({ quiet = false } = {}) {
    if (!this._entryId) {
      this._tasks = [];
      this._loading = false;
      this._renderMain();
      return;
    }
    if (!quiet) {
      this._loading = true;
      this._renderMain();
    }
    try {
      this._tasks = await this._hass.callWS({ type: "recurring_todo/tasks", entry_id: this._entryId });
      const list = this._currentList();
      if (list) list.task_count = this._tasks.length;
      const st = list?.entity_id ? this._hass.states[list.entity_id] : null;
      this._lastUpdated = st ? st.last_updated : null;
      this._loadError = null;
    } catch (err) {
      if (err?.code === "not_found") return this._loadLists();
      this._loadError = err?.message || String(err);
    }
    this._loading = false;
    this._renderMain();
  }

  _currentList() {
    return this._lists.find((l) => l.entry_id === this._entryId);
  }

  _readPref() {
    try {
      return localStorage.getItem("recurring_todo_panel_list");
    } catch (e) {
      return null;
    }
  }
  _writePref(v) {
    try {
      localStorage.setItem("recurring_todo_panel_list", v);
    } catch (e) {
      /* ignore */
    }
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  // ------------------------------------------------------------ formatting
  get _lang() {
    return this._hass?.locale?.language || this._hass?.language || navigator.language || "en";
  }
  get _tz() {
    return this._hass?.config?.time_zone;
  }
  get _hour12() {
    // Follow the user's profile setting (12 / 24 hours); otherwise the language decides.
    const tf = this._hass?.locale?.time_format;
    return tf === "12" ? { hour12: true } : tf === "24" ? { hourCycle: "h23" } : {};
  }

  _dayName(wd, style = "short") {
    // 2024-01-01 was a Monday.
    const d = new Date(Date.UTC(2024, 0, 1 + WEEKDAYS.indexOf(wd)));
    return new Intl.DateTimeFormat(this._lang, { weekday: style, timeZone: "UTC" }).format(d);
  }

  _timeText(t) {
    if (!t) return "";
    const [h, m] = t.split(":").map(Number);
    const d = new Date(Date.UTC(2024, 0, 1, h, m));
    return new Intl.DateTimeFormat(this._lang, { hour: "numeric", minute: "2-digit", timeZone: "UTC", ...this._hour12 }).format(d);
  }

  _joinList(parts) {
    try {
      return new Intl.ListFormat(this._lang, { style: "long", type: "conjunction" }).format(parts);
    } catch (e) {
      return parts.join(", ");
    }
  }

  _ordinal(n) {
    if (!this._lang.startsWith("en")) return `${n}.`;
    const s = ["th", "st", "nd", "rd"];
    const v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  _targetName(service) {
    const t = this._targets.find((x) => x.service === service);
    if (t) return t.name;
    const n = service.replace(/^notify\./, "").replace(/^mobile_app_/, "").replace(/_/g, " ");
    return n.charAt(0).toUpperCase() + n.slice(1);
  }

  _targetsText(services) {
    return services?.length ? this._joinList(services.map((s) => this._targetName(s))) : "no devices";
  }

  _defaultServices() {
    return this._currentList()?.notify_services || [];
  }

  _scheduleText(t) {
    const n = Number(t.interval) || 1;
    const [one, many] = FREQ_UNIT[t.frequency] || ["", ""];
    let text = n === 1 ? (t.frequency === "daily" ? "Every day" : `Every ${one}`) : `Every ${n} ${many}`;
    if (t.frequency === "weekly" && t.weekdays?.length) {
      const days = WEEKDAYS.filter((d) => t.weekdays.includes(d));
      text += days.length === 7 ? ", every day" : ` on ${this._joinList(days.map((d) => this._dayName(d)))}`;
    }
    if (t.frequency === "monthly" && t.day_of_month) {
      text += ` on the ${this._ordinal(Number(t.day_of_month))}`;
    }
    if (t.time) text += ` at ${this._timeText(t.time)}`;
    return text;
  }

  _nextText(iso) {
    if (!iso) return { abs: "Not scheduled", rel: "" };
    const d = new Date(iso);
    const opts = { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", ...this._hour12 };
    if (this._tz) opts.timeZone = this._tz;
    if (d.getFullYear() !== new Date().getFullYear()) opts.year = "numeric";
    const abs = new Intl.DateTimeFormat(this._lang, opts).format(d);
    const diff = d.getTime() - Date.now();
    const rtf = new Intl.RelativeTimeFormat(this._lang, { numeric: "auto" });
    const mins = Math.round(diff / 60000);
    let rel;
    if (Math.abs(mins) < 60) rel = rtf.format(mins, "minute");
    else if (Math.abs(mins) < 60 * 24) rel = rtf.format(Math.round(mins / 60), "hour");
    else rel = rtf.format(Math.round(mins / 1440), "day");
    return { abs, rel };
  }

  _todayParts() {
    // Today in Home Assistant's time zone.
    const opts = { year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" };
    if (this._tz) opts.timeZone = this._tz;
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en-US", opts).formatToParts(new Date()).map((p) => [p.type, p.value])
    );
    return { iso: `${parts.year}-${parts.month}-${parts.day}`, day: Number(parts.day), weekday: parts.weekday.slice(0, 3).toLowerCase() };
  }

  // ------------------------------------------------------------ rendering
  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <div class="toolbar">
        <button class="icon-btn menu" title="Menu" ${this._narrow ? "" : "hidden"}>${icon("menu")}</button>
        <div class="toolbar-title">Recurring To-do</div>
      </div>
      <main class="content"></main>
      <dialog class="editor" aria-labelledby="editor-title"></dialog>
      <dialog class="confirm" aria-labelledby="confirm-title"></dialog>
    `;
    const root = this.shadowRoot;
    root.querySelector(".menu").addEventListener("click", () =>
      this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }))
    );
    root.querySelector(".content").addEventListener("click", (e) => this._onMainClick(e));
    const editor = root.querySelector(".editor");
    editor.addEventListener("close", () => {
      this._editing = null;
    });
    editor.addEventListener("click", (e) => {
      if (e.target === editor) editor.close(); // click on backdrop
    });
    this._renderMain();
  }

  _renderMain() {
    const main = this.shadowRoot.querySelector(".content");
    if (!main) return;
    const list = this._currentList();

    if (this._loadError) {
      main.innerHTML = `<div class="empty"><p>Couldn't load your recurring tasks.</p><p class="muted">${esc(this._loadError)}</p>
        <button class="btn" data-action="retry">Try again</button></div>`;
      return;
    }
    if (!this._loading && !this._lists.length) {
      main.innerHTML = `<div class="empty">${icon("sync")}<h2>No recurring to-do lists yet</h2>
        <p class="muted">Add one under Settings → Devices &amp; services → Add integration → Recurring To-do.</p></div>`;
      return;
    }

    const chips =
      this._lists.length > 1
        ? `<div class="chips" role="tablist">${this._lists
            .map(
              (l) => `<button role="tab" class="chip ${l.entry_id === this._entryId ? "on" : ""}"
                aria-selected="${l.entry_id === this._entryId}" data-action="list" data-id="${esc(l.entry_id)}">
                ${esc(l.title)}<span class="count">${l.task_count}</span></button>`
            )
            .join("")}</div>`
        : "";

    const count = this._tasks.length;
    const header = `
      <div class="head">
        <div>
          <h1>${esc(list?.title || "")}</h1>
          <div class="muted">${this._loading ? "Loading…" : count === 1 ? "1 recurring task" : `${count} recurring tasks`}</div>
        </div>
        <button class="btn primary" data-action="add" ${list ? "" : "disabled"}>${icon("plus")}<span>Add task</span></button>
      </div>`;

    let body;
    if (this._loading && !count) {
      body = `<div class="grid">${'<div class="card skeleton"></div>'.repeat(3)}</div>`;
    } else if (!count) {
      body = `<div class="empty">${icon("sync")}<h2>No recurring tasks in this list</h2>
        <p class="muted">Add things that come back on a schedule, like “Water the plants every Friday” or “Change the filter every 3 months”.</p>
        <button class="btn primary" data-action="add">${icon("plus")}<span>Add your first task</span></button></div>`;
    } else {
      body = `<div class="grid">${this._tasks.map((t) => this._card(t)).join("")}</div>`;
    }
    main.innerHTML = chips + header + body;
  }

  _card(t) {
    const next = this._nextText(t.next_due);
    const soon = t.next_due && new Date(t.next_due).getTime() - Date.now() < 24 * 3600 * 1000;
    return `
      <article class="card">
        <div class="card-top">
          <h3>${esc(t.name)}</h3>
          <span class="notify ${t.notify ? "" : "off"}" title="${t.notify ? "Sends a notification" : "No notification"}">
            ${icon(t.notify ? "bell" : "bellOff")}</span>
        </div>
        <div class="schedule">${icon("sync")}<span>${esc(this._scheduleText(t))}</span></div>
        <div class="next ${soon ? "soon" : ""}"><span class="label">Next</span>
          <span>${esc(next.abs)}</span>${next.rel ? `<span class="muted">· ${esc(next.rel)}</span>` : ""}</div>
        ${
          t.notify
            ? `<div class="targets">${icon("phone")}<span>${esc(
                this._targetsText(t.notify_services?.length ? t.notify_services : this._defaultServices())
              )}${t.notify_services?.length ? "" : ' <span class="muted">(list default)</span>'}</span></div>`
            : ""
        }
        ${t.description ? `<p class="notes">${esc(t.description)}</p>` : ""}
        <div class="actions">
          <button class="btn text" data-action="run" data-id="${esc(t.task_id)}" title="Put it on the list and notify now. The schedule doesn't change.">${icon("play")}<span>Run now</span></button>
          <span class="spacer"></span>
          <button class="icon-btn" data-action="edit" data-id="${esc(t.task_id)}" title="Edit" aria-label="Edit ${esc(t.name)}">${icon("pencil")}</button>
          <button class="icon-btn danger" data-action="delete" data-id="${esc(t.task_id)}" title="Delete" aria-label="Delete ${esc(t.name)}">${icon("trash")}</button>
        </div>
      </article>`;
  }

  _onMainClick(e) {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const id = btn.dataset.id;
    const task = this._tasks.find((t) => t.task_id === id);
    switch (btn.dataset.action) {
      case "retry":
        this._loading = true;
        this._loadLists();
        break;
      case "list":
        if (id !== this._entryId) {
          this._entryId = id;
          this._writePref(id);
          this._tasks = [];
          this._loadTasks();
        }
        break;
      case "add":
        this._openEditor(null);
        break;
      case "edit":
        if (task) this._openEditor(task);
        break;
      case "delete":
        if (task) this._confirmDelete(task);
        break;
      case "run":
        if (task) this._run(task, btn);
        break;
    }
  }

  async _run(task, btn) {
    btn.disabled = true;
    try {
      await this._hass.callWS({ type: "recurring_todo/task/run", entry_id: this._entryId, task_id: task.task_id });
      this._toast(`“${task.name}” is on the list now`);
    } catch (err) {
      this._toast(ERRORS[err?.code] || err?.message || "Couldn't run the task");
      if (err?.code === "not_found") this._loadTasks();
    }
    btn.disabled = false;
  }

  // ------------------------------------------------------------ delete
  _confirmDelete(task) {
    const dlg = this.shadowRoot.querySelector(".confirm");
    dlg.innerHTML = `
      <form method="dialog">
        <h2 id="confirm-title">Delete “${esc(task.name)}”?</h2>
        <p class="muted">It won't be added to the list any more. Items already on the list stay there.</p>
        <div class="dialog-actions">
          <button class="btn text" value="cancel">Cancel</button>
          <button class="btn danger-fill" value="delete">Delete</button>
        </div>
      </form>`;
    dlg.onclose = async () => {
      if (dlg.returnValue !== "delete") return;
      try {
        await this._hass.callWS({ type: "recurring_todo/task/delete", entry_id: this._entryId, task_id: task.task_id });
        this._toast(`Deleted “${task.name}”`);
      } catch (err) {
        if (err?.code !== "not_found") this._toast(err?.message || "Couldn't delete the task");
      }
      this._loadTasks({ quiet: true });
    };
    dlg.returnValue = "";
    dlg.showModal();
  }

  // ------------------------------------------------------------ editor
  _openEditor(task) {
    const today = this._todayParts();
    this._editing = task || {};
    this._formError = null;
    this._saving = false;
    this._form = task
      ? {
          name: task.name,
          frequency: task.frequency,
          interval: task.interval || 1,
          weekdays: task.weekdays?.length ? [...task.weekdays] : [today.weekday],
          day_of_month: task.day_of_month || today.day,
          time: (task.time || "09:00:00").slice(0, 5),
          start_date: task.start_date || "",
          description: task.description || "",
          notify: task.notify !== false,
          notify_services: [...(task.notify_services || [])],
          custom_targets: !!task.notify_services?.length,
        }
      : {
          name: "",
          frequency: "weekly",
          interval: 1,
          weekdays: [today.weekday],
          day_of_month: today.day,
          time: "09:00",
          start_date: "",
          description: "",
          notify: true,
          notify_services: [],
          custom_targets: false,
        };
    this._renderEditor();
    const dlg = this.shadowRoot.querySelector(".editor");
    dlg.showModal();
    dlg.querySelector("#f-name")?.focus();
  }

  _renderEditor() {
    const f = this._form;
    const isNew = !this._editing?.task_id;
    const unit = FREQ_UNIT[f.frequency][Number(f.interval) === 1 ? 0 : 1];
    const dlg = this.shadowRoot.querySelector(".editor");
    dlg.innerHTML = `
      <form class="form" novalidate>
        <h2 id="editor-title">${isNew ? "New recurring task" : "Edit recurring task"}</h2>

        <label class="field">
          <span class="field-label">Task</span>
          <input id="f-name" name="name" type="text" required maxlength="255" autocomplete="off"
            placeholder="e.g. Water the plants" value="${esc(f.name)}">
        </label>

        <div class="field">
          <span class="field-label" id="lbl-freq">Repeats</span>
          <div class="segmented" role="radiogroup" aria-labelledby="lbl-freq">
            ${["daily", "weekly", "monthly"]
              .map(
                (fr) => `<button type="button" role="radio" aria-checked="${f.frequency === fr}"
                  class="${f.frequency === fr ? "on" : ""}" data-freq="${fr}">${fr[0].toUpperCase() + fr.slice(1)}</button>`
              )
              .join("")}
          </div>
        </div>

        <label class="field inline">
          <span class="field-label">Every</span>
          <input name="interval" type="number" min="1" max="365" step="1" inputmode="numeric" value="${esc(f.interval)}">
          <span class="unit">${unit}</span>
        </label>

        ${
          f.frequency === "weekly"
            ? `<div class="field">
                <span class="field-label" id="lbl-days">On</span>
                <div class="days" role="group" aria-labelledby="lbl-days">
                  ${WEEKDAYS.map(
                    (d) => `<button type="button" class="day ${f.weekdays.includes(d) ? "on" : ""}" data-day="${d}"
                      aria-pressed="${f.weekdays.includes(d)}" title="${esc(this._dayName(d, "long"))}">${esc(
                      this._dayName(d, "narrow")
                    )}</button>`
                  ).join("")}
                </div>
              </div>`
            : ""
        }

        ${
          f.frequency === "monthly"
            ? `<label class="field inline">
                <span class="field-label">On day</span>
                <input name="day_of_month" type="number" min="1" max="31" step="1" inputmode="numeric" value="${esc(f.day_of_month)}">
                <span class="unit">of the month</span>
              </label>
              <p class="hint">Days 29–31 fall on the last day in shorter months.</p>`
            : ""
        }

        <div class="row">
          <label class="field">
            <span class="field-label">At</span>
            <input name="time" type="time" required value="${esc(f.time)}">
          </label>
          <label class="field">
            <span class="field-label">Starting from <span class="muted">(optional)</span></span>
            <input name="start_date" type="date" value="${esc(f.start_date)}">
          </label>
        </div>

        <label class="field">
          <span class="field-label">Notes <span class="muted">(optional)</span></span>
          <textarea name="description" rows="2" placeholder="Shown on the to-do item">${esc(f.description)}</textarea>
        </label>

        <label class="switch-row">
          <span>
            <span class="field-label">Send a notification</span>
            <span class="hint">With Done, Later and Not doing buttons.</span>
          </span>
          <input name="notify" type="checkbox" class="switch" ${f.notify ? "checked" : ""}>
        </label>
        ${f.notify ? this._targetsField(f) : ""}

        <div class="preview" aria-live="polite">${icon("sync")}<span>${esc(this._scheduleText({ ...f, weekdays: f.weekdays }))}</span></div>
        <div class="error" role="alert" ${this._formError ? "" : "hidden"}>${esc(this._formError || "")}</div>

        <div class="dialog-actions">
          <button type="button" class="btn text" data-close>Cancel</button>
          <button type="submit" class="btn primary" ${this._saving ? "disabled" : ""}>${
      this._saving ? "Saving…" : isNew ? "Add task" : "Save"
    }</button>
        </div>
      </form>`;

    const form = dlg.querySelector("form");
    form.addEventListener("input", (e) => this._onFormInput(e));
    form.addEventListener("change", (e) => this._onFormInput(e));
    form.addEventListener("click", (e) => this._onFormClick(e));
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      this._save();
    });
  }

  _targetsField(f) {
    const defaults = this._defaultServices();
    // Devices that can be picked, plus any saved ones that no longer exist.
    const known = this._targets.map((t) => t.service);
    const all = [...known, ...f.notify_services.filter((s) => !known.includes(s))];
    let body;
    if (!f.custom_targets) {
      body = `<p class="hint">${
        defaults.length
          ? `Goes to ${esc(this._targetsText(defaults))}, as set for this list.`
          : "This list has no devices set. Choose devices here, or set them in the list's settings."
      }</p>`;
    } else if (!all.length) {
      body = `<p class="hint">No notify services found in Home Assistant.</p>`;
    } else {
      body = `<div class="target-list" role="group" aria-labelledby="lbl-targets">${all
        .map(
          (svc) => `<label class="target">
            <input type="checkbox" data-service="${esc(svc)}" ${f.notify_services.includes(svc) ? "checked" : ""}>
            <span>${esc(this._targetName(svc))}</span>
            <span class="muted svc">${esc(svc)}</span>
          </label>`
        )
        .join("")}</div>
        <p class="hint">Done / Later / Not doing buttons only show on phones with the Companion app.</p>`;
    }
    return `
      <div class="field targets-field">
        <span class="field-label" id="lbl-targets">Send to</span>
        <div class="segmented" role="radiogroup" aria-labelledby="lbl-targets">
          <button type="button" role="radio" aria-checked="${!f.custom_targets}" class="${f.custom_targets ? "" : "on"}" data-targets="default">List default</button>
          <button type="button" role="radio" aria-checked="${f.custom_targets}" class="${f.custom_targets ? "on" : ""}" data-targets="custom">Choose devices</button>
        </div>
        ${body}
      </div>`;
  }

  _onFormInput(e) {
    const el = e.target;
    if (el.dataset?.service) {
      if (e.type !== "change") return;
      const set = new Set(this._form.notify_services);
      el.checked ? set.add(el.dataset.service) : set.delete(el.dataset.service);
      this._form.notify_services = [...set];
      if (this._formError) this._setFormError(null);
      return;
    }
    if (!el.name) return;
    this._form[el.name] = el.type === "checkbox" ? el.checked : el.value;
    if (this._formError) this._setFormError(null);
    if (el.name === "notify" && e.type === "change") {
      this._renderEditor();
      this.shadowRoot.querySelector('.editor input[name="notify"]')?.focus();
      return;
    }
    if (el.name === "interval") {
      const unitEl = el.parentElement.querySelector(".unit");
      if (unitEl) unitEl.textContent = FREQ_UNIT[this._form.frequency][Number(el.value) === 1 ? 0 : 1];
    }
    this._updatePreview();
  }

  _onFormClick(e) {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.hasAttribute("data-close")) {
      this.shadowRoot.querySelector(".editor").close();
    } else if (t.dataset.freq) {
      this._form.frequency = t.dataset.freq;
      this._formError = null;
      this._renderEditor();
      this.shadowRoot.querySelector(`[data-freq="${t.dataset.freq}"]`)?.focus();
    } else if (t.dataset.targets) {
      this._form.custom_targets = t.dataset.targets === "custom";
      if (this._form.custom_targets && !this._form.notify_services.length) {
        this._form.notify_services = [...this._defaultServices()]; // start from the list's choice
      }
      this._formError = null;
      this._renderEditor();
      this.shadowRoot.querySelector(`[data-targets="${t.dataset.targets}"]`)?.focus();
    } else if (t.dataset.day) {
      const d = t.dataset.day;
      const set = new Set(this._form.weekdays);
      set.has(d) ? set.delete(d) : set.add(d);
      this._form.weekdays = WEEKDAYS.filter((x) => set.has(x));
      if (this._formError) this._setFormError(null);
      t.classList.toggle("on", set.has(d));
      t.setAttribute("aria-pressed", set.has(d));
      this._updatePreview();
    }
  }

  _updatePreview() {
    const el = this.shadowRoot.querySelector(".editor .preview span");
    if (el) el.textContent = this._scheduleText(this._form);
  }

  _setFormError(msg) {
    this._formError = msg;
    const el = this.shadowRoot.querySelector(".editor .error");
    if (el) {
      el.textContent = msg || "";
      el.hidden = !msg;
    }
  }

  async _save() {
    const f = this._form;
    const name = f.name.trim();
    const interval = parseInt(f.interval, 10);
    const dom = parseInt(f.day_of_month, 10);
    if (!name) return this._setFormError(ERRORS.name_required);
    if (!(interval >= 1 && interval <= 365)) return this._setFormError("“Every” must be a whole number from 1 to 365.");
    if (f.frequency === "weekly" && !f.weekdays.length) return this._setFormError(ERRORS.weekdays_required);
    if (f.frequency === "monthly" && !(dom >= 1 && dom <= 31)) return this._setFormError("Pick a day of the month from 1 to 31.");
    if (!f.time) return this._setFormError("Pick a time.");
    if (f.notify && f.custom_targets && !f.notify_services.length)
      return this._setFormError("Pick at least one device, or use the list default.");

    const task = { name, frequency: f.frequency, interval, time: f.time, notify: !!f.notify };
    if (f.frequency === "weekly") task.weekdays = f.weekdays;
    if (f.frequency === "monthly") task.day_of_month = dom;
    if (f.start_date) task.start_date = f.start_date;
    if (f.description.trim()) task.description = f.description.trim();
    if (f.notify && f.custom_targets) task.notify_services = f.notify_services;

    const editingId = this._editing?.task_id;
    const submit = this.shadowRoot.querySelector('.editor button[type="submit"]');
    this._saving = true;
    if (submit) {
      submit.disabled = true;
      submit.textContent = "Saving…";
    }
    try {
      const msg = editingId
        ? { type: "recurring_todo/task/update", entry_id: this._entryId, task_id: editingId, task }
        : { type: "recurring_todo/task/create", entry_id: this._entryId, task };
      await this._hass.callWS(msg);
      this.shadowRoot.querySelector(".editor").close();
      this._toast(editingId ? `Saved “${name}”` : `Added “${name}”`);
      this._loadTasks({ quiet: true });
    } catch (err) {
      this._saving = false;
      if (submit) {
        submit.disabled = false;
        submit.textContent = editingId ? "Save" : "Add task";
      }
      this._setFormError(ERRORS[err?.code] || err?.message || "Couldn't save the task.");
      if (err?.code === "not_found") this._loadTasks({ quiet: true });
    }
  }
}

const STYLES = `
  :host {
    display: block;
    min-height: 100vh;
    background: var(--primary-background-color, #fafafa);
    color: var(--primary-text-color, #212121);
    font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif);
    --rt-primary: var(--primary-color, #03a9f4);
    --rt-card: var(--ha-card-background, var(--card-background-color, #fff));
    --rt-muted: var(--secondary-text-color, #727272);
    --rt-divider: var(--divider-color, rgba(0,0,0,.12));
    --rt-error: var(--error-color, #db4437);
    --rt-radius: var(--ha-card-border-radius, 12px);
    --rt-input-bg: var(--input-fill-color, var(--secondary-background-color, rgba(0,0,0,.04)));
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  svg { width: 20px; height: 20px; fill: currentColor; flex: none; }

  .toolbar {
    position: sticky; top: 0; z-index: 2;
    display: flex; align-items: center; gap: 4px;
    height: var(--header-height, 56px); padding: 0 12px;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, #fff);
    border-bottom: var(--app-header-border-bottom, none);
  }
  .toolbar-title { font-size: 20px; font-weight: 400; margin-left: 12px; }
  .toolbar .icon-btn { color: inherit; }

  .content { max-width: 1100px; margin: 0 auto; padding: 16px 16px 48px; }
  h1 { font-size: 24px; font-weight: 500; margin: 0; }
  h2 { font-size: 20px; font-weight: 500; margin: 0 0 8px; }
  h3 { font-size: 17px; font-weight: 500; margin: 0; overflow-wrap: anywhere; }
  .muted { color: var(--rt-muted); font-weight: 400; }

  .chips { display: flex; gap: 8px; overflow-x: auto; padding: 4px 0 12px; scrollbar-width: none; }
  .chip {
    display: inline-flex; align-items: center; gap: 8px; white-space: nowrap;
    border: 1px solid var(--rt-divider); background: var(--rt-card); color: inherit;
    border-radius: 999px; padding: 6px 14px; font: inherit; font-size: 14px; cursor: pointer;
  }
  .chip.on { background: var(--rt-primary); border-color: var(--rt-primary); color: var(--text-primary-color, #fff); }
  .chip .count { font-size: 12px; opacity: .75; }

  .head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin: 8px 0 16px; flex-wrap: wrap; }

  .btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 6px;
    height: 40px; padding: 0 18px; border-radius: 20px; border: none; cursor: pointer;
    font: inherit; font-size: 14px; font-weight: 500; letter-spacing: .02em;
    background: transparent; color: var(--rt-primary);
  }
  .btn:disabled { opacity: .5; cursor: default; }
  .btn.primary { background: var(--rt-primary); color: var(--text-primary-color, #fff); }
  .btn.primary:hover:not(:disabled) { filter: brightness(1.08); }
  .btn.text { padding: 0 12px; }
  .btn.text:hover:not(:disabled) { background: color-mix(in srgb, var(--rt-primary) 10%, transparent); }
  .btn.danger-fill { background: var(--rt-error); color: #fff; }
  .btn svg { width: 18px; height: 18px; }

  .icon-btn {
    display: inline-flex; align-items: center; justify-content: center;
    width: 40px; height: 40px; border-radius: 50%; border: none; cursor: pointer;
    background: transparent; color: var(--rt-muted);
  }
  .icon-btn:hover { background: color-mix(in srgb, currentColor 10%, transparent); }
  .icon-btn.danger:hover { color: var(--rt-error); }
  button:focus-visible, input:focus-visible, textarea:focus-visible { outline: 2px solid var(--rt-primary); outline-offset: 2px; }

  .grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
  .card {
    display: flex; flex-direction: column; gap: 8px;
    background: var(--rt-card); border-radius: var(--rt-radius);
    border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--rt-divider));
    box-shadow: var(--ha-card-box-shadow, none);
    padding: 16px 16px 8px;
  }
  .card.skeleton { min-height: 150px; opacity: .5; animation: pulse 1.2s ease-in-out infinite alternate; }
  @keyframes pulse { to { opacity: .2; } }
  .card-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
  .notify { color: var(--rt-primary); display: inline-flex; }
  .notify.off { color: var(--rt-muted); opacity: .6; }
  .schedule { display: flex; align-items: center; gap: 8px; font-size: 14px; }
  .schedule svg { width: 16px; height: 16px; color: var(--rt-muted); }
  .next { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px; font-size: 14px; }
  .next .label {
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em;
    padding: 2px 8px; border-radius: 6px;
    background: var(--rt-input-bg); color: var(--rt-muted);
  }
  .next.soon .label { background: color-mix(in srgb, var(--rt-primary) 18%, transparent); color: var(--rt-primary); }
  .targets { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--rt-muted); }
  .targets svg { width: 16px; height: 16px; }
  .targets > span { overflow-wrap: anywhere; }
  .targets-field { margin-top: -4px; }
  .targets-field .hint { margin: 0; }
  .target-list { display: flex; flex-direction: column; border: 1px solid var(--rt-divider); border-radius: 12px; overflow: hidden; }
  .target { display: flex; align-items: center; gap: 12px; padding: 10px 12px; cursor: pointer; min-height: 44px; }
  .target + .target { border-top: 1px solid var(--rt-divider); }
  .target input { width: 18px; height: 18px; margin: 0; accent-color: var(--rt-primary); flex: none; }
  .target .svc { margin-left: auto; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .notes { margin: 0; font-size: 13px; color: var(--rt-muted); white-space: pre-wrap; overflow-wrap: anywhere; }
  .actions { display: flex; align-items: center; margin-top: auto; padding-top: 4px; border-top: 1px solid var(--rt-divider); margin-left: -8px; margin-right: -8px; }
  .actions .spacer { flex: 1; }

  .empty { text-align: center; padding: 48px 16px; max-width: 460px; margin: 0 auto; }
  .empty > svg { width: 48px; height: 48px; color: var(--rt-muted); opacity: .6; margin-bottom: 8px; }
  .empty p { line-height: 1.5; }

  dialog {
    border: none; border-radius: 24px; padding: 0;
    background: var(--mdc-theme-surface, var(--rt-card)); color: var(--primary-text-color);
    width: min(520px, calc(100vw - 32px)); max-height: calc(100dvh - 32px);
    box-shadow: 0 12px 40px rgba(0,0,0,.3);
  }
  dialog::backdrop { background: rgba(0,0,0,.4); }
  dialog form { padding: 24px; display: flex; flex-direction: column; gap: 16px; }
  .confirm { width: min(400px, calc(100vw - 32px)); }
  .confirm p { margin: 0; line-height: 1.5; }
  .dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }

  .field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .field.inline { flex-direction: row; align-items: center; gap: 12px; }
  .field.inline .field-label { min-width: 56px; }
  .field-label { font-size: 13px; font-weight: 500; color: var(--rt-muted); }
  .field.inline .field-label { font-size: 14px; color: inherit; }
  .unit { color: var(--rt-muted); font-size: 14px; }
  .hint { display: block; font-size: 12px; color: var(--rt-muted); margin: -10px 0 0; }
  .switch-row .hint { margin: 2px 0 0; }
  input[type=text], input[type=number], input[type=time], input[type=date], textarea {
    font: inherit; font-size: 16px; color: inherit;
    background: var(--rt-input-bg); border: 1px solid transparent; border-bottom: 1px solid var(--rt-muted);
    border-radius: 8px 8px 0 0; padding: 10px 12px; width: 100%; min-width: 0;
    color-scheme: light dark;
  }
  input:focus, textarea:focus { border-bottom: 2px solid var(--rt-primary); outline: none; }
  input[type=number] { width: 88px; }
  textarea { resize: vertical; min-height: 44px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

  .segmented { display: flex; border: 1px solid var(--rt-divider); border-radius: 20px; overflow: hidden; }
  .segmented button {
    flex: 1; height: 40px; border: none; background: transparent; color: inherit; cursor: pointer;
    font: inherit; font-size: 14px; font-weight: 500;
  }
  .segmented button + button { border-left: 1px solid var(--rt-divider); }
  .segmented button.on { background: color-mix(in srgb, var(--rt-primary) 18%, transparent); color: var(--rt-primary); }

  .days { display: flex; gap: 6px; flex-wrap: wrap; }
  .day {
    width: 40px; height: 40px; border-radius: 50%; cursor: pointer;
    border: 1px solid var(--rt-divider); background: transparent; color: inherit;
    font: inherit; font-size: 14px; font-weight: 500;
  }
  .day.on { background: var(--rt-primary); border-color: var(--rt-primary); color: var(--text-primary-color, #fff); }

  .switch-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; cursor: pointer; }
  .switch-row .field-label { color: inherit; font-size: 14px; }
  .switch {
    appearance: none; -webkit-appearance: none; flex: none; cursor: pointer;
    width: 40px; height: 22px; border-radius: 11px; position: relative; margin: 0;
    background: var(--switch-unchecked-track-color, rgba(0,0,0,.3)); transition: background .15s;
  }
  .switch::after {
    content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%;
    background: #fff; transition: transform .15s;
  }
  .switch:checked { background: var(--rt-primary); }
  .switch:checked::after { transform: translateX(18px); }

  .preview {
    display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-radius: 12px; font-size: 14px;
    background: color-mix(in srgb, var(--rt-primary) 10%, transparent);
  }
  .preview svg { width: 18px; height: 18px; color: var(--rt-primary); }
  .error {
    padding: 10px 12px; border-radius: 12px; font-size: 14px;
    color: var(--rt-error); background: color-mix(in srgb, var(--rt-error) 12%, transparent);
  }

  @media (max-width: 600px) {
    .content { padding: 12px 12px 48px; }
    .grid { grid-template-columns: 1fr; }
    .head .btn.primary span { display: inline; }
    dialog { width: 100vw; max-width: 100vw; max-height: 100dvh; height: 100dvh; border-radius: 0; margin: 0; }
    dialog.confirm { height: auto; border-radius: 24px; margin: auto; width: calc(100vw - 32px); }
    dialog form { padding: 20px 16px; min-height: 100%; }
    .editor .dialog-actions { margin-top: auto; }
    .row { grid-template-columns: 1fr; }
  }
`;

if (!customElements.get("recurring-todo-panel")) {
  customElements.define("recurring-todo-panel", RecurringTodoPanel);
}

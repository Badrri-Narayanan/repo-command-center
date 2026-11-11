import { HelpDialog } from "./help.js";
import { Popover } from "./popover.js";
import { OperationRunner } from "./runner.js";
import { esc, homeRelative } from "./util.js";
import * as view from "./view.js";

const TOAST_MS = 4000;
const LOG_LIMIT = 400;
const REFRESH_ON_FOCUS_AFTER_MS = 30000;
const ROOT_PICKER_WIDTH = 460;

/** Connects the store, runner and HTML views to the page: rendering and every DOM event. */
export class Dashboard {
  constructor(doc, { store, api, prefs }) {
    this.doc = doc;
    this.store = store;
    this.api = api;
    this.els = this.findElements(doc);
    this.popover = new Popover(this.els.popover);
    this.help = new HelpDialog(this.els.help, this.els.helpAgain, prefs);
    this.runner = new OperationRunner(api, store, {
      changed: name => (name ? this.updateTile(name) : this.render()),
      logged: entry => this.log(entry),
      confirm: message => confirm(message),
      notify: message => this.toast(message),
    });
  }

  findElements(doc) {
    const ids = ["grid", "chips", "q", "sort", "selAll", "selCount", "bulk", "root", "refreshAll", "fetchAll",
      "popover", "toast", "logList", "logLast", "help", "helpBtn", "helpAgain"];
    return Object.fromEntries(ids.map(id => [id, doc.getElementById(id)]));
  }

  start() {
    this.els.sort.value = this.store.sort;
    this.bindEvents();
    this.help.bind();
    this.help.openOnFirstVisit();
    this.reload();
  }

  reload() {
    return this.runner.loadAll().catch(error => {
      this.els.grid.innerHTML = `<div class="empty">Failed to load: ${esc(error.message)}</div>`;
    });
  }

  render() {
    const visible = this.store.visible();
    this.els.grid.innerHTML = visible.length
      ? visible.map(status => view.tileHTML(status, this.store)).join("")
      : view.emptyStateHTML(this.store);
    this.renderChrome();
  }

  updateTile(name) {
    const tile = this.els.grid.querySelector(`[data-name="${CSS.escape(name)}"]`);
    if (tile) tile.outerHTML = view.tileHTML(this.store.get(name), this.store);
    this.renderChrome();
  }

  renderChrome() {
    const { checked, indeterminate, count } = this.store.selectionState();
    this.els.chips.innerHTML = view.chipsHTML(this.store.filterChips());
    Object.assign(this.els.selAll, { checked, indeterminate });
    this.els.selCount.textContent = count;
    this.els.bulk.querySelectorAll(".btn").forEach(button => { button.disabled = count === 0; });
    this.renderRoot();
  }

  renderRoot() {
    const { root, home, names } = this.store;
    this.els.root.textContent = `${homeRelative(root, home)} · ${names.length} repos`;
    this.els.root.title = `${root}: click to change folder`;
  }

  log(entry) {
    const item = this.doc.createElement("li");
    item.className = entry.kind;
    item.innerHTML = view.logEntryHTML(entry, this.store.opLabel(entry.op), new Date().toLocaleTimeString());
    this.els.logList.prepend(item);
    while (this.els.logList.children.length > LOG_LIMIT) this.els.logList.lastChild.remove();
    this.els.logLast.textContent = `— ${entry.name}: ${entry.message}`;
  }

  toast(message) {
    this.els.toast.textContent = message;
    this.els.toast.hidden = false;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { this.els.toast.hidden = true; }, TOAST_MS);
  }

  run(op, names, extra) {
    return this.runner.run(op, names, extra);
  }

  async showBranches(anchor, name) {
    this.popover.open(anchor, view.BRANCH_PICKER_HTML);
    const input = this.popover.find(".pop-filter");
    const list = this.popover.find(".pop-list");
    input.focus();
    try {
      const { current, branches } = await this.api.branches(name);
      const draw = () => { list.innerHTML = view.branchListHTML(branches, current, input.value); };
      draw();
      this.popover.place();
      input.oninput = draw;
      input.onkeydown = event => { if (event.key === "Enter") this.popover.find("[data-branch]")?.click(); };
      this.popover.onClick(event => this.pickBranch(event, name));
    } catch (error) {
      list.innerHTML = `<div class="muted">${esc(error.message)}</div>`;
    }
  }

  pickBranch(event, name) {
    const item = event.target.closest("[data-branch]");
    if (!item) return;
    this.popover.close();
    this.run("checkout", [name], { branch: item.dataset.branch });
  }

  showMore(anchor, names, status = null) {
    this.popover.open(anchor, view.moreMenuHTML(this.store.openers, status));
    this.popover.onClick(event => this.onMoreItem(event, names, status));
  }

  async onMoreItem(event, names, status) {
    const item = event.target.closest("button");
    if (!item) return;
    this.popover.close();
    const { op, copy, url } = item.dataset;
    if (op === "refresh") return this.runner.refresh(names[0]);
    if (op) return this.run(op, names);
    if (copy) await this.copyPath(status.path);
    if (url) window.open(url, "_blank", "noopener");
  }

  async copyPath(path) {
    await navigator.clipboard.writeText(path);
    this.toast("Path copied");
  }

  showCheckout(anchor) {
    this.popover.open(anchor, view.checkoutFormHTML(this.store.selected.size));
    const input = this.popover.find(".pop-filter");
    input.focus();
    const submit = () => {
      const branch = input.value.trim();
      if (!branch) return;
      this.popover.close();
      this.run("checkout", [...this.store.selected], { branch });
    };
    this.popover.find("#doCheckout").onclick = submit;
    input.onkeydown = event => { if (event.key === "Enter") submit(); };
  }

  showRootPicker(anchor) {
    const { root, depth } = this.store;
    this.popover.open(anchor, view.rootPickerHTML(root, depth, this.store.recentRoots()), ROOT_PICKER_WIDTH);
    const input = this.popover.find(".pop-filter");
    input.focus();
    input.select();
    const open = path => this.changeRoot(path, Number(this.popover.find("#depthSel").value));
    this.popover.find("#goRoot").onclick = () => open(input.value.trim());
    input.onkeydown = event => { if (event.key === "Enter") open(input.value.trim()); };
    this.popover.onClick(event => {
      const recent = event.target.closest("[data-root]");
      if (recent) open(recent.dataset.root);
    });
  }

  async changeRoot(path, depth) {
    try {
      await this.api.changeRoot(path, depth);
      this.popover.close();
      this.store.reset();
      await this.reload();
    } catch (error) {
      this.toast(error.message);
    }
  }

  bindEvents() {
    const { els, store } = this;
    els.grid.addEventListener("click", event => this.onTileClick(event));
    els.grid.addEventListener("dblclick", event => this.onTileDoubleClick(event));
    els.chips.addEventListener("click", event => this.onChipClick(event));
    els.q.addEventListener("input", event => { store.query = event.target.value; this.render(); });
    els.sort.addEventListener("change", event => { store.setSort(event.target.value); this.render(); });
    els.selAll.addEventListener("change", event => this.selectVisible(event.target.checked));
    els.refreshAll.addEventListener("click", () => this.reload());
    els.fetchAll.addEventListener("click", () => this.run("fetch", store.names));
    els.root.addEventListener("click", event => this.showRootPicker(event.currentTarget));
    els.bulk.addEventListener("click", event => this.onBulkClick(event));
    els.helpBtn.addEventListener("click", () => this.openHelp());
    this.doc.addEventListener("click", event => this.closePopoverOnOutsideClick(event));
    this.doc.addEventListener("keydown", event => this.onKey(event));
    window.addEventListener("focus", () => { if (store.needsRefresh(REFRESH_ON_FOCUS_AFTER_MS)) this.reload(); });
  }

  onTileClick(event) {
    const tile = event.target.closest(".tile");
    const control = event.target.closest("button, input.sel");
    if (!tile || !control) return;
    const name = tile.dataset.name;
    if (control.matches("input.sel")) {
      this.store.toggleSelected(name, control.checked);
      return this.updateTile(name);
    }
    if (control.dataset.op) return this.run(control.dataset.op, [name]);
    this.onTileAction(control.dataset.act, control, name);
  }

  onTileAction(action, control, name) {
    const status = this.store.get(name);
    if (action === "pin") {
      this.store.togglePin(name);
      this.render();
    } else if (action === "branches") this.showBranches(control, name);
    else if (action === "more") this.showMore(control, [name], status);
    else if (action === "web") window.open(status.web, "_blank", "noopener");
  }

  onTileDoubleClick(event) {
    const tile = event.target.closest(".tile");
    if (!tile || event.target.closest("button, input")) return;
    this.store.toggleSelected(tile.dataset.name);
    this.updateTile(tile.dataset.name);
  }

  onChipClick(event) {
    const chip = event.target.closest("[data-filter]");
    if (!chip) return;
    this.store.setFilter(chip.dataset.filter);
    this.render();
  }

  onBulkClick(event) {
    const button = event.target.closest("[data-bulk]");
    if (!button || button.disabled) return;
    const names = [...this.store.selected];
    if (button.dataset.bulk === "checkout") return this.showCheckout(button);
    if (button.dataset.bulk === "more") return this.showMore(button, names);
    this.run(button.dataset.bulk, names);
  }

  selectVisible(selected) {
    this.store.setSelected(this.store.visible().map(status => status.name), selected);
    this.render();
  }

  closePopoverOnOutsideClick(event) {
    const outside = !this.popover.contains(event.target) && !event.target.closest("[data-pop]");
    if (this.popover.isOpen && outside) this.popover.close();
  }

  openHelp(tab) {
    this.popover.close();
    this.help.open(tab);
  }

  onKey(event) {
    if (this.help.isOpen) return;
    const typing = event.target.matches("input, select, textarea");
    if (event.key === "Escape") return this.onEscape(event, typing);
    if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
    const shortcuts = {
      "?": () => this.openHelp(),
      "/": () => { event.preventDefault(); this.els.q.focus(); },
      a: () => this.selectVisible(true),
      r: () => this.reload(),
    };
    shortcuts[event.key]?.();
  }

  onEscape(event, typing) {
    if (this.popover.isOpen) return this.popover.close();
    if (typing) return event.target.blur();
    this.store.selected.clear();
    this.render();
  }
}

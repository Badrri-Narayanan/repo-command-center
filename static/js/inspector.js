import * as iv from "./inspector-view.js";
import { branchPill } from "./view.js";

const SEARCH_DEBOUNCE_MS = 250;
const TOAST_MS = 4000;
const TAB_KEYS = { 1: "history", 2: "changes", 3: "stashes", 4: "tags" };

/**
 * The read-only repo inspector: history (with incoming/outgoing views and search), working-tree changes,
 * stashes and tags, each as a list on the left and a diff / file viewer on the right.
 */
export class Inspector {
  constructor(dialog, { api, store, runner }) {
    this.dialog = dialog;
    this.api = api;
    this.store = store;
    this.runner = runner;
    this.el = {
      toast: dialog.querySelector("#inspToast"),
      title: dialog.querySelector("#inspTitle"), branch: dialog.querySelector("#inspBranch"),
      tabs: dialog.querySelector("#inspTabs"), tools: dialog.querySelector("#inspTools"),
      list: dialog.querySelector("#inspList"), detail: dialog.querySelector("#inspDetail"),
    };
    this.state = {};
    this.seq = 0;
  }

  get isOpen() {
    return this.dialog.open;
  }

  bind() {
    this.dialog.addEventListener("click", event => this.onClick(event));
    this.dialog.addEventListener("input", event => this.onInput(event));
    this.dialog.addEventListener("change", event => this.onChange(event));
    this.dialog.addEventListener("keydown", event => this.onKey(event));
  }

  open(name, tab = "history", option = "") {
    this.state = { name, tab, range: tab === "history" && option ? option : "head", query: "", author: "", authors: null, selected: null, data: null };
    this.el.title.textContent = name;
    if (!this.dialog.open) this.dialog.showModal();
    this.showTab(tab);
  }

  status() {
    return this.store.get(this.state.name);
  }

  renderHeader() {
    const status = this.status();
    this.el.branch.innerHTML = status && !status.loading ? branchPill(status) : "";
    this.el.tabs.innerHTML = iv.tabsHTML(this.state.tab, status);
  }

  showTab(tab) {
    Object.assign(this.state, { tab, selected: null, data: null, query: tab === this.state.tab ? this.state.query : "" });
    this.renderHeader();
    this.el.tools.innerHTML = tab === "history" ? iv.historyToolsHTML(this.state.range, this.state.query, this.state.author)
      : tab === "tags" ? iv.tagsToolsHTML(this.state.query) : "";
    this.el.tools.hidden = !this.el.tools.innerHTML;
    this.el.detail.innerHTML = iv.emptyHTML("Select an item on the left.");
    if (tab === "history") this.loadAuthors();
    return this.loadList();
  }

  /** Ignores responses that arrive after the user has moved on (tab switch, newer search). */
  async latest(promise, target) {
    const ticket = ++this.seq;
    target.innerHTML = iv.LOADING_HTML;
    try {
      const result = await promise;
      return ticket === this.seq ? result : null;
    } catch (error) {
      if (ticket === this.seq) target.innerHTML = iv.emptyHTML(error.message);
      return null;
    }
  }

  async loadList() {
    const { name, tab, range, query } = this.state;
    const requests = {
      history: () => this.api.explore("log", name, { range, q: query, author: this.state.author }),
      changes: () => this.api.explore("changes", name),
      stashes: () => this.api.explore("stashes", name),
      tags: () => this.api.explore("tags", name),
    };
    const data = await this.latest(requests[tab](), this.el.list);
    if (!data) return;
    this.state.data = data;
    this.renderList();
    this.selectFirst();
  }

  renderList() {
    const { tab, data, selected, query } = this.state;
    const html = {
      history: () => iv.commitListHTML(data, selected, { query, author: this.state.author }),
      changes: () => iv.changesListHTML(data.files, selected),
      stashes: () => iv.stashListHTML(data.stashes, selected),
      tags: () => iv.tagListHTML(data, selected, query),
    };
    this.el.list.innerHTML = html[tab]();
  }

  selectFirst() {
    const first = this.el.list.querySelector("[data-key]");
    if (first) this.select(first.dataset.key);
  }

  async select(key) {
    this.state.selected = key;
    this.el.list.querySelectorAll("[data-key]").forEach(row => row.classList.toggle("on", row.dataset.key === key));
    this.el.list.querySelector(".irow.on")?.scrollIntoView({ block: "nearest" });
    await this.loadDetail(key);
  }

  async loadDetail(key) {
    const { name, tab, data } = this.state;
    const web = this.status()?.web;
    let html = null;
    if (tab === "history") {
      const commit = await this.latest(this.api.explore("commit", name, { rev: key }), this.el.detail);
      if (commit) html = iv.commitDetailHTML(commit, web);
      this.patch = commit?.patch;
    } else if (tab === "changes") {
      const file = iv.findChange(data.files, key);
      const diff = await this.latest(this.api.explore("diff", name, { path: file.path, staged: file.staged ? 1 : 0 }), this.el.detail);
      if (diff) html = iv.diffHTML(diff, { worktree: true });
      this.patch = diff?.patch;
    } else if (tab === "stashes") {
      const entry = data.stashes.find(s => String(s.index) === key);
      const stash = await this.latest(this.api.explore("stash", name, { index: key }), this.el.detail);
      if (stash) html = iv.stashDetailHTML(stash, entry);
      this.patch = stash?.patch;
    } else {
      const tag = data.tags.find(t => t.name === key);
      const commit = await this.latest(this.api.explore("commit", name, { rev: tag.commit }), this.el.detail);
      if (commit) html = iv.tagHeaderHTML(tag, web) + iv.commitDetailHTML(commit, web);
      this.patch = commit?.patch;
    }
    if (html !== null) {
      this.el.detail.innerHTML = html;
      this.el.detail.scrollTop = 0;
      this.detailHTML = html;
    }
  }

  async loadMore(button) {
    const { name, range, query, data } = this.state;
    button.disabled = true;
    try {
      const page = await this.api.explore("log", name, { range, q: query, author: this.state.author, skip: data.commits.length });
      data.commits.push(...page.commits);
      data.more = page.more;
      this.renderList();
    } catch (error) {
      this.toast(error.message);
      button.disabled = false;
    }
  }

  /** Fills the author picker for the current range. Failures just leave "All authors". */
  async loadAuthors() {
    const { name, range } = this.state;
    try {
      const authors = await this.api.explore("authors", name, { range });
      if (name !== this.state.name || range !== this.state.range) return;
      this.state.authors = authors;
      this.renderAuthors();
    } catch {
      /* the list still works without the picker */
    }
  }

  renderAuthors() {
    const select = this.dialog.querySelector("#inspAuthor");
    if (select) select.innerHTML = iv.authorOptionsHTML(this.state.authors || { authors: [] }, this.state.author);
  }

  /** Filters history to one author ("" clears). From the Tags tab it jumps to History. */
  setAuthor(email) {
    this.state.author = email;
    if (this.state.tab !== "history") return this.showTab("history");
    this.renderAuthors();
    return this.loadList();
  }

  async viewFile(path, rev) {
    const file = await this.latest(this.api.explore("file", this.state.name, { path, rev }), this.el.detail);
    if (!file) return;
    this.el.detail.innerHTML = iv.fileViewHTML(file);
    this.el.detail.scrollTop = 0;
  }

  /** The page toast sits under the modal (top layer), so the inspector has its own. */
  toast(message) {
    this.el.toast.textContent = message;
    this.el.toast.hidden = false;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { this.el.toast.hidden = true; }, TOAST_MS);
  }

  async stashOp(op) {
    const { name, selected } = this.state;
    const tally = await this.runner.run(op, [name], { index: Number(selected) });
    if (!tally) return;
    const result = this.store.results.get(name);
    this.toast(`${this.store.opLabel(op)}: ${result?.message || "done"}`);
    await this.showTab("stashes");
  }

  async copy(text, label) {
    await navigator.clipboard.writeText(text);
    this.toast(`${label} copied`);
  }

  onClick(event) {
    if (event.target === this.dialog || event.target.closest("[data-close]")) return this.dialog.close();
    const author = event.target.closest("[data-author]");
    if (author) return this.setAuthor(author.dataset.author);
    const target = event.target.closest("button");
    if (!target) return;
    const d = target.dataset;
    if (d.itab) return this.showTab(d.itab);
    if (d.key !== undefined) return this.select(d.key);
    if (d.more !== undefined) return this.loadMore(target);
    if (d.viewFile !== undefined) return this.viewFile(d.viewFile, d.rev);
    if (d.back !== undefined) return this.restoreDetail();
    if (d.jump !== undefined) return this.dialog.querySelector(`#dfile-${CSS.escape(d.jump)}`)?.scrollIntoView({ block: "start" });
    if (d.copy) return this.copy(d.copy, "Hash");
    if (d.copyPatch !== undefined) return this.copy(this.patch || "", "Diff");
    if (d.url) return window.open(d.url, "_blank", "noopener");
    if (d.stashOp) return this.stashOp(d.stashOp);
  }

  restoreDetail() {
    this.el.detail.innerHTML = this.detailHTML || "";
  }

  onInput(event) {
    if (event.target.id !== "inspQuery") return;
    this.state.query = event.target.value;
    clearTimeout(this.searchTimer);
    if (this.state.tab === "tags") return this.renderList();
    this.searchTimer = setTimeout(() => this.loadList(), SEARCH_DEBOUNCE_MS);
  }

  onChange(event) {
    if (event.target.id === "inspAuthor") return this.setAuthor(event.target.value);
    if (event.target.id !== "inspRange") return;
    this.state.range = event.target.value;
    this.loadAuthors();
    this.loadList();
  }

  /** j/k or ↑/↓ move through the list, 1–4 switch tabs, / jumps to search. Esc is handled by <dialog> itself. */
  onKey(event) {
    const typing = event.target.matches("input, select, textarea");
    if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
    if (TAB_KEYS[event.key]) return this.showTab(TAB_KEYS[event.key]);
    if (event.key === "/" && this.dialog.querySelector("#inspQuery")) {
      event.preventDefault();
      return this.dialog.querySelector("#inspQuery").focus();
    }
    const step = { j: 1, ArrowDown: 1, k: -1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const rows = [...this.el.list.querySelectorAll("[data-key]")];
    const at = rows.findIndex(row => row.dataset.key === this.state.selected);
    const next = rows[Math.max(0, Math.min(rows.length - 1, at + step))];
    if (next && next.dataset.key !== this.state.selected) this.select(next.dataset.key);
  }
}

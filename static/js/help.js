const SEEN_KEY = "rcc.helpSeen";

/** The "How to use" dialog: tabs, close, and auto-open on first visit. */
export class HelpDialog {
  constructor(dialog, dontShowAgain, prefs) {
    this.dialog = dialog;
    this.dontShowAgain = dontShowAgain;
    this.prefs = prefs;
  }

  get isOpen() {
    return this.dialog.open;
  }

  bind() {
    this.dialog.addEventListener("click", event => this.onClick(event));
    this.dontShowAgain.addEventListener("change", event => this.prefs.set(SEEN_KEY, event.target.checked));
  }

  onClick(event) {
    const clickedBackdrop = event.target === this.dialog;
    if (clickedBackdrop || event.target.closest("[data-close]")) return this.dialog.close();
    const tab = event.target.closest("[data-tab]");
    if (tab) this.showTab(tab.dataset.tab);
  }

  open(tab) {
    if (tab) this.showTab(tab);
    this.dontShowAgain.checked = this.prefs.get(SEEN_KEY, false);
    if (!this.dialog.open) this.dialog.showModal();
  }

  showTab(name) {
    this.dialog.querySelectorAll(".tab").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
    this.dialog.querySelectorAll("[data-panel]").forEach(p => { p.hidden = p.dataset.panel !== name; });
  }

  openOnFirstVisit(delayMs = 300) {
    if (!this.prefs.get(SEEN_KEY, false)) setTimeout(() => this.open("start"), delayMs);
  }
}

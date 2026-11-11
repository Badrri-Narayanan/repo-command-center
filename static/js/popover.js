const MAX_HEIGHT = 360;
const EDGE = 8;
const GAP = 6;

/** The single floating menu used for the branch picker, ⋯ menu, checkout form and folder picker. */
export class Popover {
  constructor(element) {
    this.el = element;
    this.anchor = null;
  }

  get isOpen() {
    return !this.el.hidden;
  }

  open(anchor, html, width = 290) {
    this.anchor = anchor;
    this.el.innerHTML = html;
    this.el.hidden = false;
    this.el.onclick = null;
    this.el.style.width = `min(${width}px, calc(100vw - ${EDGE * 2}px))`;
    this.place();
  }

  /** Below the anchor, or above it when there is clearly more room there. */
  place() {
    const rect = this.anchor.getBoundingClientRect();
    const below = innerHeight - rect.bottom - 14;
    const above = rect.top - 14;
    const up = below < 240 && above > below;
    this.el.style.maxHeight = `${Math.min(MAX_HEIGHT, up ? above : below)}px`;
    this.el.style.left = `${Math.max(EDGE, Math.min(rect.left, innerWidth - this.el.offsetWidth - EDGE))}px`;
    this.el.style.top = `${up ? rect.top - this.el.offsetHeight - GAP : rect.bottom + GAP}px`;
  }

  close() {
    this.el.hidden = true;
  }

  contains(node) {
    return this.el.contains(node);
  }

  find(selector) {
    return this.el.querySelector(selector);
  }

  onClick(handler) {
    this.el.onclick = handler;
  }
}

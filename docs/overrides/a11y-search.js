/* Repair the accessibility of the theme's search widget.
 *
 * The widget renders into a shadow root and ships two defects that axe-core
 * reports (aria-required-attr, button-name):
 *
 *   1. Its text input claims role="combobox" but wires up none of the
 *      contract that role promises: there is no listbox, no aria-expanded,
 *      no aria-controls and no aria-activedescendant. A role that lies is
 *      worse for a screen-reader user than no role, because the reader
 *      announces a control whose advertised state never arrives. Dropping
 *      the attribute leaves an honest search field, which is what the
 *      widget actually is. Completing the contract instead would mean
 *      maintaining listbox/option roles and active-descendant tracking
 *      against a third-party component that re-renders on every keystroke.
 *
 *   2. Its icon-only buttons carry no accessible name, so a screen reader
 *      announces them as an unlabelled "button". The icon each one holds
 *      identifies it, so the name is read off that.
 *
 * The component re-renders on input, which discards these edits, so a
 * MutationObserver re-applies them. Every change is idempotent and the
 * observer only ever adds a name or removes the false role, so it cannot
 * fight the framework for control of any attribute the widget itself sets.
 *
 * Delete this file once the theme wires up its own ARIA and names its own
 * controls, tracked upstream at https://github.com/zensical/backlog/issues/161.
 */

(() => {
  "use strict";

  // Read off the lucide icon each button contains: the widget gives its
  // buttons no text, no title and no distinguishing class of its own.
  const BUTTON_NAMES = {
    "lucide-search": "Search",
    "lucide-list-filter": "Filter results",
  };

  const nameButtons = (root) => {
    for (const button of root.querySelectorAll("button")) {
      if (button.getAttribute("aria-label")) continue;
      if (button.textContent.trim()) continue;
      const icon = button.querySelector("svg[class*='lucide-']");
      if (!icon) continue;
      const match = [...icon.classList].find((name) => name in BUTTON_NAMES);
      if (match) button.setAttribute("aria-label", BUTTON_NAMES[match]);
    }
  };

  const dropFalseCombobox = (root) => {
    for (const input of root.querySelectorAll('input[role="combobox"]')) {
      if (input.getAttribute("aria-controls")) continue;
      input.removeAttribute("role");
    }
  };

  const repair = (root) => {
    dropFalseCombobox(root);
    nameButtons(root);
  };

  const observed = new WeakSet();

  const attach = (shadowRoot) => {
    if (observed.has(shadowRoot)) return;
    observed.add(shadowRoot);
    repair(shadowRoot);
    new MutationObserver(() => repair(shadowRoot)).observe(shadowRoot, {
      childList: true,
      subtree: true,
    });
  };

  const scan = () => {
    for (const element of document.querySelectorAll("*")) {
      if (element.shadowRoot) attach(element.shadowRoot);
    }
  };

  // The widget mounts its shadow host lazily, when search is first opened,
  // so a single pass at load time would find nothing to repair.
  scan();
  new MutationObserver(scan).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
})();

"""Server-side browser engine for the ERP web recorder + headless playback.

A recording session runs a real Chromium (Playwright) inside a dedicated worker
thread that owns the browser for its whole life. Requests submit callables onto the
thread's queue and wait for the result — this keeps one browser alive across many
HTTP requests without fighting FastAPI's event loop or Playwright's thread-affinity.

The frontend polls /screenshot to show the live page and relays clicks/typing by
coordinate; we capture the DOM element under the cursor to build a replayable CSS
selector. Saved steps are replayed headlessly by `play_steps`.
"""

import base64
import logging
import pathlib
import queue
import re
import threading
import uuid
import weakref

logger = logging.getLogger(__name__)

# How old an abandoned browser scratch directory must be before we delete it. Long enough that
# a run in progress is never touched (a run is capped at MAX_RUN_SECONDS).
_STALE_TMP_AGE_SECONDS = 2 * 60 * 60

# How many tabs get their title read on each poll. Every tab is still listed; beyond this
# many, the URL identifies it — title() evaluates JS per tab and would stall the poll loop.
_TITLE_LIMIT = 12


def sweep_stale_browser_tmp() -> int:
    """Delete orphaned Playwright/Chromium scratch directories from /tmp. Returns how many.

    Playwright removes its own temp directory when the driver exits normally — but not when the
    process is killed, and a `pm2 restart` mid-run does exactly that. Left alone these
    accumulate silently: 289 of them built up here and helped fill a 97 GB disk to 100%, which
    took Postgres down with it. A try/finally cannot help, because SIGKILL never runs it, so
    the only reliable cleanup is sweeping stale ones before we start a new run.
    """
    import shutil
    import tempfile
    import time as _t
    from pathlib import Path

    tmp = Path(tempfile.gettempdir())
    cutoff = _t.time() - _STALE_TMP_AGE_SECONDS
    removed = 0
    for pattern in ("playwright*", "org.chromium.Chromium.scoped_dir*"):
        for path in tmp.glob(pattern):
            try:
                if path.stat().st_mtime > cutoff:
                    continue  # recent — could belong to a run happening right now
                shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
                removed += 1
            except Exception:  # noqa: BLE001 — best effort; never break a run over cleanup
                pass
    if removed:
        logger.info("Swept %d stale browser temp dir(s) from %s", removed, tmp)
    return removed


VIEWPORT = {"width": 1280, "height": 800}
# One press of the recorder's scroll button. Just under a screen so a couple of rows stay
# visible across the jump — scrolling a whole viewport loses your place on a long ERP form.
SCROLL_STEP_PX = 600
# How long a replayed step waits for its element to appear. Generous on purpose: an ERP menu
# drawn by script after login can take a couple of seconds, and declaring it missing at 8s
# turns a slow screen into a failed replay.
REPLAY_WAIT_MS = 20000
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# JS: describe the element at a point + build a robust CSS selector.
_ELEMENT_JS = r"""
([x, y]) => {
  function css(e) {
    if (e.id) return '#' + CSS.escape(e.id);
    for (const a of ['name','data-testid','placeholder']) {
      const v = e.getAttribute && e.getAttribute(a);
      if (v) return e.tagName.toLowerCase() + '[' + a + '="' + CSS.escape(v) + '"]';
    }
    // nth-of-type path (bounded depth)
    let node = e, parts = [];
    while (node && node.nodeType === 1 && parts.length < 6 && node.tagName.toLowerCase() !== 'body' && node.tagName.toLowerCase() !== 'html') {
      let sel = node.tagName.toLowerCase();
      const p = node.parentNode;
      if (p && p.children) {
        const same = Array.from(p.children).filter(c => c.tagName === node.tagName);
        if (same.length > 1) sel += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(sel);
      node = node.parentNode;
    }
    return parts.join(' > ');
  }
  // Best human label for an input, resolved within its own document.
  function humanLabel(e, doc) {
    try {
      const parts = [];
      if (e.id) {
        const lab = doc.querySelector('label[for="' + CSS.escape(e.id) + '"]');
        if (lab) parts.push(lab.innerText);
      }
      let p = e.closest && e.closest('label');
      if (p) parts.push(p.innerText);
      for (const a of ['aria-label','placeholder','name','title']) {
        const v = e.getAttribute && e.getAttribute(a);
        if (v) parts.push(v);
      }
      let prev = e.previousElementSibling;
      if (prev && prev.innerText) parts.push(prev.innerText);
      const cell = e.closest && e.closest('td, .form-group, .field, div');
      if (cell) {
        const lab = cell.querySelector && cell.querySelector('label, .label, th');
        if (lab && lab.innerText) parts.push(lab.innerText);
      }
      return parts.map(s => (s || '').replace(/\s+/g, ' ').trim()).filter(Boolean).join(' | ').slice(0, 160);
    } catch (err) { return ''; }
  }

  // Descend through same-origin (nested) iframes so we detect the REAL element the
  // user pointed at, not the <iframe> wrapping it. `frames` records the iframe chain
  // (top -> innermost) so playback can target the right frame.
  let doc = document, cx = x, cy = y, frames = [], guard = 0;
  let el = doc.elementFromPoint(cx, cy);
  while (el && el.tagName === 'IFRAME' && guard++ < 6) {
    let idoc = null;
    try { idoc = el.contentDocument; } catch (err) { idoc = null; }
    if (!idoc) break;  // cross-origin iframe: cannot pierce
    frames.push(css(el));
    const rect = el.getBoundingClientRect();
    const win = el.ownerDocument.defaultView;
    const st = win.getComputedStyle(el);
    cx = cx - rect.left - (parseFloat(st.borderLeftWidth) || 0) - (parseFloat(st.paddingLeft) || 0);
    cy = cy - rect.top - (parseFloat(st.borderTopWidth) || 0) - (parseFloat(st.paddingTop) || 0);
    doc = idoc;
    el = doc.elementFromPoint(cx, cy);
  }
  // A touch is not pixel-perfect, and a 1px gap between two controls returns nothing at all.
  // Feel around the point before giving up: the recorder used to record a step with an EMPTY
  // selector here, which looks like a click in the list but matches nothing on playback.
  if (!el) {
    const ring = [[0,-6],[0,6],[-6,0],[6,0],[-6,-6],[6,-6],[-6,6],[6,6],[0,-12],[0,12],[-12,0],[12,0]];
    for (const [dx, dy] of ring) {
      const probe = doc.elementFromPoint(cx + dx, cy + dy);
      if (probe && probe.tagName && probe.tagName.toLowerCase() !== 'html' &&
          probe.tagName.toLowerCase() !== 'body') { el = probe; break; }
    }
  }
  if (!el) return null;

  // Pierce OPEN shadow roots. A web component keeps its real input behind a shadow
  // boundary and elementFromPoint stops at the HOST, so touching the field used to return
  // the custom element - neither a value field nor a click target. The recorder saw nothing
  // usable for a control the user plainly pressed, which is the long-standing "touch records
  // nothing / does not match what I pressed" complaint. Playwright CSS does pierce open
  // shadow roots, so a selector built from the inner element still replays.
  let sguard = 0;
  while (el && el.shadowRoot && sguard++ < 6) {
    let inner = null;
    try { inner = el.shadowRoot.elementFromPoint(cx, cy); } catch (err) { inner = null; }
    if (!inner || inner === el) break;
    el = inner;
  }
  // A label sits in the same tree as its field, which for a shadow input is the shadow root,
  // not the document - look it up there or humanLabel finds nothing.
  try {
    const root = el.getRootNode && el.getRootNode();
    if (root && root.querySelector) doc = root;
  } catch (err) { /* keep the document we had */ }

  const tag = el.tagName.toLowerCase();
  const itype = (el.getAttribute ? (el.getAttribute('type') || '') : '').toLowerCase();
  const BUTTONISH = ['submit', 'button', 'reset', 'image'];
  // A button/submit is a CLICK target, never a value field.
  const is_button = tag === 'button' || (tag === 'input' && BUTTONISH.includes(itype));
  const is_select = tag === 'select';
  // Only text-entry inputs open the value popup; checkbox/radio/file are click targets.
  const is_input =
    (tag === 'input' || tag === 'textarea') &&
    !is_button && itype !== 'checkbox' && itype !== 'radio' && itype !== 'file';
  // A React/Vue dropdown - react-select, MUI, Chakra - is a stack of divs. The thing that
  // LOOKS like the field is a value-container, and its options live in a menu rendered
  // elsewhere with generated ids like #react-select-2-option-1 that change on every mount.
  // Recording either of those verbatim gives a step that breaks tomorrow. Resolve both back
  // to the CONTROL, and hand the option's TEXT over as the value instead of its id.
  // Is this thing actually clickable, and if so WHAT should the step target? An icon in a
  // nav bar is an <img> inside an <a>: clicking the image works, but recording the image's
  // nth-of-type path is brittle, and judging "clickable" from the tag alone calls it text.
  // Walk up to the real interactive element instead. `cursor: pointer` is the signal a site
  // uses to tell a person something is clickable, so it is the right signal here too.
  function clickableAncestor(e) {
    // TWO passes, and the order matters. `cursor: pointer` is INHERITED, so an icon inside a
    // clickable menu item reports pointer itself - a single pass returns the <img> and the
    // recorded step depends on that image's position in the markup. Look for something
    // genuinely interactive first; only if there is none does the cursor decide.
    function real(n) {
      const tg = n.tagName.toLowerCase();
      const role = (n.getAttribute && n.getAttribute('role')) || '';
      const ity = ((n.getAttribute && n.getAttribute('type')) || '').toLowerCase();
      if (tg === 'a' || tg === 'button') return true;
      if (tg === 'input' && ['submit','button','reset','image'].includes(ity)) return true;
      if (['button','menuitem','tab','link','option','checkbox','radio'].includes(role)) return true;
      if (n.onclick || (n.getAttribute && n.getAttribute('onclick'))) return true;
      if (tg === 'li' && n.closest &&
          n.closest('nav,[role=menu],[role=menubar],[class*=menu],[class*=nav]')) return true;
      return false;
    }
    let n = e, guard = 0;
    while (n && n.nodeType === 1 && guard++ < 7) {
      if (real(n)) return n;
      n = n.parentElement;
    }
    // Nothing declared itself interactive. Fall back to the cursor, but take the OUTERMOST
    // element that still shows a pointer - that is the thing a person is aiming at.
    n = e; guard = 0;
    let best = null;
    while (n && n.nodeType === 1 && guard++ < 7) {
      try {
        if (n.ownerDocument.defaultView.getComputedStyle(n).cursor === 'pointer') best = n;
        else if (best) break;
      } catch (err) {}
      n = n.parentElement;
    }
    return best;
  }
  const clickTarget = clickableAncestor(el);

  const WIDGET_CONTROL = '[class*="-control"],[class*="select__control"],.select2-selection,' +
                         '.ui-selectmenu-button,[role="combobox"],[class*="MuiSelect"],[class*="dropdown-toggle"]';
  const WIDGET_OPTION  = '[class*="-option"],[class*="select__option"],[role="option"],' +
                         '.select2-results__option,.ui-menu-item,[class*="MuiMenuItem"]';
  // A widget control almost never has an id, so css() falls back to a positional path like
  // `div > div > div > div:nth-of-type(1) > div:nth-of-type(2)` — which breaks the moment the
  // page renders one wrapper more or fewer. But these widgets DO carry a stable class:
  // react-select emits `react-select__control` (from classNamePrefix), select2 emits
  // `select2-selection`, jQuery UI `ui-selectmenu-button`. Those are hand-authored and
  // survive rebuilds, unlike the emotion hashes beside them (`css-19bb58m`), which do not.
  // Prefer the stable class, scoped to the nearest identified ancestor so a screen with
  // several dropdowns stays unambiguous.
  function widgetSelector(n) {
    const cls = (n.className || '').toString().trim().split(/\s+/).filter(Boolean);
    const stable =
      cls.find(c => !/^css-/.test(c) && /(^|[-_])control$/.test(c)) ||
      cls.find(c => /select2-selection|ui-selectmenu-button|dropdown-toggle|MuiSelect/.test(c));
    if (!stable) return css(n);
    let sel = '.' + CSS.escape(stable);
    let a = n.parentElement, guard = 0;
    while (a && guard++ < 6) {
      if (a.id) { sel = '#' + CSS.escape(a.id) + ' ' + sel; break; }
      a = a.parentElement;
    }
    return sel;
  }

  let widget_role = '', option_text = '', control_selector = '';
  const optNode = el.closest && el.closest(WIDGET_OPTION);
  if (optNode) {
    widget_role = 'option';
    option_text = (optNode.innerText || '').trim().slice(0, 120);
    // the control this menu belongs to: the only one on screen with an open menu
    const ctl = doc.querySelector('[class*="-control"],[class*="select__control"],[role="combobox"]');
    control_selector = ctl ? widgetSelector(ctl) : '';
  } else {
    const ctlNode = el.closest && el.closest(WIDGET_CONTROL);
    if (ctlNode) {
      widget_role = 'control';
      control_selector = widgetSelector(ctlNode);
    }
  }
  return {
    tag,
    selector: css(el),
    frames,
    text: (el.innerText || el.value || '').trim().slice(0, 80),
    input_type: itype,
    is_select,
    is_input,
    is_button,
    label: (is_input || is_select) ? humanLabel(el, doc) : '',
    options: is_select ? Array.from(el.options).map(o => o.textContent.trim()) : null,
    widget_role,          // '' | 'control' | 'option'
    option_text,          // when an option was touched: what it says
    control_selector,     // the stable element a step should target
    // Decided from the live page, not from the tag name.
    is_clickable: !!clickTarget,
    click_selector: css(clickTarget || el),
    click_text: clickTarget ? (clickTarget.innerText || clickTarget.getAttribute('aria-label')
                               || clickTarget.getAttribute('title') || '').trim().slice(0, 60) : '',
    // WHERE INSIDE THE ELEMENT the person pressed, as a fraction of its width and height.
    //
    // Replaying a click means clicking an element, and Playwright clicks the CENTRE of it. For
    // a table row that is the middle column - which on a results grid is often a link, so a
    // double-click meant to open the row navigated away to another site instead. Where the
    // Super Admin actually pressed was thrown away.
    //
    // A fraction rather than pixels because a grid is not the same width twice: 0.18 across
    // stays on the same column when the table is wider, where "120px from the left" does not.
    // Clamped away from the very edge, since a click exactly on a border can miss the element.
    click_fx: (() => {
      const r = (clickTarget || el).getBoundingClientRect();
      if (!r.width) return null;
      return Math.min(0.98, Math.max(0.02, (cx - r.left) / r.width));
    })(),
    click_fy: (() => {
      const r = (clickTarget || el).getBoundingClientRect();
      if (!r.height) return null;
      return Math.min(0.98, Math.max(0.02, (cy - r.top) / r.height));
    })(),
    // What the element said at the moment it was touched. A row is found again by this rather
    // than by its position in the table, so a search returning a different number of results
    // cannot open a different job.
    click_in_text: ((clickTarget || el).innerText || '').replace(/\s+/g, ' ').trim().slice(0, 120),
    // A tickbox is not a button: pressing one TOGGLES it, so replaying a recorded press against
    // a box that is already ticked turns it OFF. These select which sheets get imported, so a
    // silent un-tick is wrong data rather than a visible failure. Report what it is and the
    // state it is in NOW - before this press - so the recorder can store the state the step
    // should leave it in instead of "press this again".
    is_toggle: (() => {
      const c = clickTarget || el;
      const ty = (c.getAttribute && (c.getAttribute('type') || '')).toLowerCase();
      if (c.tagName === 'INPUT' && (ty === 'checkbox' || ty === 'radio')) return ty;
      const r = (c.getAttribute && c.getAttribute('role') || '').toLowerCase();
      return (r === 'checkbox' || r === 'radio') ? r : null;
    })(),
    checked_now: (() => {
      const c = clickTarget || el;
      if (typeof c.checked === 'boolean') return c.checked;
      const a = c.getAttribute && c.getAttribute('aria-checked');
      return a == null ? null : a === 'true';
    })(),
  };
}
"""


# JS: after typing into a type-ahead field, collect the visible suggestion strings.
# Searches both the field's own (iframe) document and the top document, since some
# autocompletes render their dropdown in either place.
_SUGGEST_JS = r"""
([x, y]) => {
  let doc = document, cx = x, cy = y, guard = 0;
  let el = doc.elementFromPoint(cx, cy);
  while (el && el.tagName === 'IFRAME' && guard++ < 6) {
    let idoc = null;
    try { idoc = el.contentDocument; } catch (e) { idoc = null; }
    if (!idoc) break;
    const r = el.getBoundingClientRect();
    const st = el.ownerDocument.defaultView.getComputedStyle(el);
    cx = cx - r.left - (parseFloat(st.borderLeftWidth) || 0) - (parseFloat(st.paddingLeft) || 0);
    cy = cy - r.top - (parseFloat(st.borderTopWidth) || 0) - (parseFloat(st.paddingTop) || 0);
    doc = idoc; el = doc.elementFromPoint(cx, cy);
  }
  const SPECIFIC = [
    '[role=option]', '[class*=option]', '[class*=result]', '[class*=suggest]',
    '.tt-suggestion', '.select2-results__option', '.ui-menu-item',
    '.dropdown-menu li', '.dropdown-item', 'ul[class*=autocomplete] li', 'li.ui-menu-item'
  ].join(',');
  function collect(d, sel) {
    const out = [], seen = new Set();
    try {
      d.querySelectorAll(sel).forEach(e => {
        const r = e.getBoundingClientRect();
        const vis = r.width > 0 && r.height > 0 && e.offsetParent !== null;
        const t = (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim();
        if (vis && t && t.length < 160 && !seen.has(t)) { seen.add(t); out.push(t); }
      });
    } catch (err) { /* ignore */ }
    return out;
  }
  let out = collect(doc, SPECIFIC);
  if (doc !== document) out = out.concat(collect(document, SPECIFIC));
  if (!out.length) { out = collect(doc, 'li'); if (doc !== document) out = out.concat(collect(document, 'li')); }
  // de-dupe preserving order
  const seen = new Set(); const res = [];
  for (const t of out) { if (!seen.has(t)) { seen.add(t); res.push(t); } }
  return res.slice(0, 15);
}
"""

# JS: enumerate every interactive/clickable element on the page (and same-origin
# iframes) — buttons, links, submit inputs, role=button, onclick handlers, selects.
# Used by "Capture events" and by the AI takeover during playback.
# JS: count editable vs locked (disabled/readonly) form fields on the page + iframes,
# plus the visible text. Used to detect "the form locked after entering a value" — the
# generic signal for a duplicate/blocked entry (no field names or messages hardcoded).
_FIELD_STATE_JS = r"""
() => {
  const SKIP = ['hidden','submit','button','reset','image','checkbox','radio','file'];
  function collect(doc) {
    let editable = 0, locked = 0, text = "";
    try {
      doc.querySelectorAll('input,select,textarea').forEach(e => {
        const t = ((e.getAttribute && e.getAttribute('type')) || '').toLowerCase();
        if (SKIP.includes(t)) return;
        const r = e.getBoundingClientRect();
        if (!(r.width > 0 && r.height > 0)) return;  // visible fields only
        editable++;
        if (e.disabled || e.readOnly || e.getAttribute('aria-disabled') === 'true') locked++;
      });
    } catch (err) { /* cross-origin */ }
    try { text = (doc.body ? doc.body.innerText : '').replace(/\s+/g, ' ').trim(); } catch (e) {}
    let ifr = [];
    try { ifr = Array.from(doc.querySelectorAll('iframe')); } catch (e) {}
    for (const f of ifr) {
      let idoc = null;
      try { idoc = f.contentDocument; } catch (e) { idoc = null; }
      if (idoc) { const s = collect(idoc); editable += s.editable; locked += s.locked; text += ' ' + s.text; }
    }
    return { editable, locked, text: text.slice(0, 1800) };
  }
  return collect(document);
}
"""


_EVENTS_JS = r"""
() => {
  function css(e) {
    if (e.id) return '#' + CSS.escape(e.id);
    for (const a of ['name','data-testid','placeholder']) {
      const v = e.getAttribute && e.getAttribute(a);
      if (v) return e.tagName.toLowerCase() + '[' + a + '="' + CSS.escape(v) + '"]';
    }
    let node = e, parts = [];
    while (node && node.nodeType === 1 && parts.length < 6 && node.tagName.toLowerCase() !== 'body' && node.tagName.toLowerCase() !== 'html') {
      let sel = node.tagName.toLowerCase();
      const p = node.parentNode;
      if (p && p.children) {
        const same = Array.from(p.children).filter(c => c.tagName === node.tagName);
        if (same.length > 1) sel += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(sel);
      node = node.parentNode;
    }
    return parts.join(' > ');
  }
  // Everything a person can press. Checkboxes, radios and file inputs were missing, which is
  // why an import dialog - a file box and one checkbox per sheet - came back almost empty.
  const SEL = 'button, a[href], input[type=submit], input[type=button], input[type=image],' +
              'input[type=checkbox], input[type=radio], input[type=file],' +
              '[role=button], [role=menuitem], [role=option], [role=tab], select, [onclick]';
  // A dialog sits on top of a busy screen. Anything inside one is what the user is looking at,
  // so it is collected first and shown first.
  const DIALOG = '[role=dialog],[class*=modal],[class*=popup],[class*=dialog],' +
                 '[id*=Popup],[id*=popup],[id*=Modal],[id*=modal]';
  // A checkbox has no text of its own. Read its label the way a person does.
  function labelFor(e, doc) {
    const bits = [];
    try {
      if (e.id) {
        const l = doc.querySelector('label[for="' + CSS.escape(e.id) + '"]');
        if (l && l.innerText) bits.push(l.innerText);
      }
      const own = e.closest && e.closest('label');
      if (own && own.innerText) bits.push(own.innerText);
      let sib = e.nextSibling, hops = 0;
      while (sib && hops++ < 3) {
        const txt = (sib.nodeType === 3 ? sib.textContent : (sib.innerText || '')) || '';
        if (txt.trim()) { bits.push(txt); break; }
        sib = sib.nextSibling;
      }
      for (const a of ['aria-label', 'title', 'value', 'name']) {
        const v = e.getAttribute && e.getAttribute(a);
        if (v) bits.push(v);
      }
    } catch (err) { /* fall through to whatever was gathered */ }
    // The same words often arrive twice - once from label[for] and once as the next sibling -
    // which listed as "ITEMS ITEMS". Keep the first of each.
    const seenBits = new Set();
    const kept = [];
    for (const raw of bits) {
      const v = (raw || '').replace(/\s+/g, ' ').trim();
      if (!v || seenBits.has(v.toLowerCase())) continue;
      seenBits.add(v.toLowerCase());
      kept.push(v);
    }
    return kept.join(' ').slice(0, 70);
  }
  // A file box usually has no label of its own. The sentence it sits in is what a person reads
  // ("Step1: Select the appropriate .XLSX file to uploaded"), so use that rather than a blank row.
  function nearbyText(e) {
    let n = e.parentElement, guard = 0;
    while (n && guard++ < 3) {
      const txt = (n.innerText || '').replace(/\s+/g, ' ').trim();
      if (txt) return txt.slice(0, 70);
      n = n.parentElement;
    }
    return '';
  }
  const out = [], seen = new Set();
  function collect(doc, frames) {
    let els = [];
    try { els = Array.from(doc.querySelectorAll(SEL)); } catch (e) { return; }
    for (const e of els) {
      const r = e.getBoundingClientRect();
      if (!(r.width > 0 && r.height > 0 && e.offsetParent !== null)) continue;
      const tag = e.tagName.toLowerCase();
      const ity = ((e.getAttribute && e.getAttribute('type')) || '').toLowerCase();
      let text = (e.innerText || (e.getAttribute && (e.getAttribute('aria-label') || e.getAttribute('title'))) || '').replace(/\s+/g, ' ').trim().slice(0, 70);
      // A tick box, a radio or a file box says nothing about itself; read its label instead.
      if (!text || ['checkbox', 'radio', 'file'].includes(ity)) text = labelFor(e, doc) || text;
      if (!text) text = (e.value || '').replace(/\s+/g, ' ').trim().slice(0, 70);
      if (!text) text = nearbyText(e) || (ity ? ity + ' input' : tag);
      const sel = css(e);
      const key = frames.join('>') + '|' + sel + '|' + text;
      if (seen.has(key)) continue;
      seen.add(key);
      const in_dialog = !!(e.closest && e.closest(DIALOG));
      out.push({ tag, text, selector: sel, frames, input_type: ity, in_dialog });
      // Was 50, which a single ERP screen behind a modal uses up on its own - so the modal's
      // own controls never got collected. High enough now that nothing real is dropped.
      if (out.length >= 400) return;
    }
    let ifr = [];
    try { ifr = Array.from(doc.querySelectorAll('iframe')); } catch (e) { ifr = []; }
    for (const f of ifr) {
      let idoc = null;
      try { idoc = f.contentDocument; } catch (e) { idoc = null; }
      if (idoc) collect(idoc, frames.concat([css(f)]));
    }
  }
  collect(document, []);
  // What is inside the open dialog goes first: it is what the user is looking at.
  out.sort((a, b) => (b.in_dialog ? 1 : 0) - (a.in_dialog ? 1 : 0));
  return out;
}
"""


# Scroll the page by `dy` pixels (negative = up).
#
# Plain window.scrollBy is not enough for the ERPs we drive. A Logi-Sys / ASP.NET screen
# usually puts the whole form inside a fixed-height <div> with overflow-y:auto, so the
# window itself never scrolls and the button would appear dead. So: try the window, and if
# it did not actually move, scroll the largest genuinely-scrollable element on the page
# instead. Reports what moved and where it ended up so the recorder can label the step.
_SCROLL_JS = r"""
([dy]) => {
  // Collect everything on the page that can scroll, then move the biggest one that can
  // still travel the way we were asked to go.
  //
  // Guessing "the window scrolls" is wrong on most ERP screens. A Logi-Sys / ASP.NET entry
  // form is usually a fixed-height <div> with its own scrollbar, or lives inside an iframe,
  // and quite often the <body> is TALLER than the viewport while still refusing to scroll
  // because it is overflow:hidden. So we never assume - we look.
  const cands = [];
  let blocked = 0;   // frames we are not allowed to look inside (cross-origin)

  function addWindow(w, d, area) {
    try {
      const max = Math.round((d.documentElement ? d.documentElement.scrollHeight : 0) - w.innerHeight);
      if (max > 1) cands.push({
        kind: 'window', area: area, max: max,
        get: () => Math.round(w.scrollY || 0),
        set: (v) => w.scrollTo(0, v),
      });
    } catch (e) { /* cross-origin frame - not ours to scroll */ }
  }

  // Elements, shadow roots and nested frames under one root. A root is a document or a
  // shadow root - component libraries (and any web component) hide their scrollable panel
  // inside a shadow root, where a plain querySelectorAll on the document cannot see it.
  function scanRoot(root, w, area, depth) {
    if (depth > 5) return;
    let els = [];
    try { els = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of els) {
      if (el.shadowRoot) scanRoot(el.shadowRoot, w, area, depth + 1);
      const range = el.scrollHeight - el.clientHeight;
      if (range <= 4) continue;
      let st;
      // Styles must be read from the element's OWN window, not the top one.
      try { st = ((el.ownerDocument && el.ownerDocument.defaultView) || w).getComputedStyle(el); }
      catch (e) { continue; }
      if (!st || !/(auto|scroll|overlay)/.test(st.overflowY)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 40) continue;      // ignore tiny widgets
      cands.push({
        kind: 'element', area: r.width * r.height, max: Math.round(range),
        get: () => Math.round(el.scrollTop),
        set: (v) => { el.scrollTop = v; },
      });
    }
    let frames = [];
    try { frames = root.querySelectorAll('iframe,frame'); } catch (e) { frames = []; }
    for (const f of frames) {
      let fd = null, fw = null;
      try { fd = f.contentDocument; fw = f.contentWindow; } catch (e) { fd = null; }
      if (!fd || !fw) { blocked++; continue; }           // cross-origin - a wheel may still work
      const fr = f.getBoundingClientRect();
      walkDoc(fd, fw, Math.max(fr.width * fr.height, 1), depth + 1);
    }
  }

  function walkDoc(d, w, area, depth) {
    if (depth > 5) return;
    addWindow(w, d, area);
    scanRoot(d, w, area, depth);
  }

  walkDoc(document, window, window.innerWidth * window.innerHeight, 0);
  if (!cands.length) {
    return {mode: 'none', y: 0, max: 0, moved: 0, found: 0, movable: 0, blocked: blocked};
  }

  // Something already at the end cannot take us further - try those first.
  const canMove = (c) => dy > 0 ? c.get() < c.max - 1 : c.get() > 1;
  // How much of this request a container could actually absorb. An ERP page is often a
  // few pixels taller than the screen while the form itself sits in an iframe or a panel:
  // by area the outer page wins, but it can only creep a few pixels and the form never
  // moves - which reads as a dead button. Whatever can take the whole scroll goes first,
  // and area only breaks ties.
  const travel = (c) => dy > 0 ? Math.min(c.max - c.get(), Math.abs(dy))
                               : Math.min(c.get(), Math.abs(dy));
  const byUse = (a, b) => (travel(b) - travel(a)) || (b.area - a.area);
  const byArea = (a, b) => b.area - a.area;
  const movable = dy === 0 ? [] : cands.filter(canMove);
  const pool = movable.slice().sort(byUse)
      .concat(cands.filter((c) => movable.indexOf(c) < 0).sort(byArea));

  if (dy === 0) {                                  // report only, move nothing
    const t = pool[0];
    return {mode: t.kind, y: t.get(), max: t.max, moved: 0,
            found: cands.length, movable: movable.length, blocked: blocked};
  }

  // Try each in turn and VERIFY it moved. A container can look scrollable and refuse to
  // budge - most often the window itself, on a page whose <body> is tall but locked, with
  // the real scrollbar on a panel inside it. Trusting the first candidate is what made the
  // buttons appear dead; proving the movement is what makes this reliable.
  for (const t of pool) {
    const was = t.get();
    t.set(was + dy);
    const now = t.get();
    if (now !== was) {
      return {mode: t.kind, y: now, max: t.max, moved: now - was,
              found: cands.length, movable: movable.length, blocked: blocked};
    }
  }
  const t = pool[0];
  return {mode: t.kind, y: t.get(), max: t.max, moved: 0,
          found: cands.length, movable: movable.length, blocked: blocked};
}
"""


def _do_scroll(page, dy: int) -> dict:
    """Scroll the live page and give the lazy-loaded content a moment to render.

    Two attempts, because ERP screens scroll in more ways than one:
      1. Find the real scroll container in JS and move it. Handles the window, a fixed-height
         panel, and anything inside a same-origin iframe.
      2. If that shifted nothing, send a REAL mouse wheel at the middle of the screen. Some
         grids and custom scrollers only react to genuine input, and a wheel event also fires
         the page's own handlers (virtualised tables load their next rows this way).

    Never raises: a scroll that cannot happen (short page, nothing scrollable, already at the
    end) is a no-op, not a failure — failing a replay for that would be wrong.
    """
    dy = int(dy)
    try:
        res = page.evaluate(_SCROLL_JS, [dy]) or {}
    except Exception:  # noqa: BLE001
        res = {}
    if res.get("moved"):
        try:
            page.wait_for_timeout(250)  # grids and virtualised tables render on scroll
        except Exception:  # noqa: BLE001
            pass
        return res

    # Nothing moved — try a real wheel over the centre of the viewport.
    before_y = res.get("y")
    try:
        vp = page.viewport_size or VIEWPORT
        page.mouse.move(int(vp["width"] / 2), int(vp["height"] / 2))
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(300)
        after = page.evaluate(_SCROLL_JS, [0]) or {}     # dy=0: report position, move nothing
        if after and after.get("y") != before_y:
            after["moved"] = (after.get("y") or 0) - (before_y or 0)
            after["via"] = "wheel"
            return after
        if after:
            res = {**res, **{k: v for k, v in after.items()
                             if k in ("found", "movable", "max", "blocked")}}
        # A cross-origin iframe cannot be read OR scrolled from JS, but the wheel is a real
        # input event and the browser delivers it regardless. We cannot measure what happened
        # inside, so take the wheel at its word rather than reporting a dead button — the
        # screenshot the caller returns will show the operator whether it moved.
        if int(res.get("blocked") or 0) > 0:
            return {**res, "mode": "frame", "moved": dy, "via": "wheel-blind"}
    except Exception:  # noqa: BLE001
        pass
    return res


def _scoped(page, frames: list | None, selector: str):
    """Resolve a locator inside the iframe chain (`frames`, top -> innermost)."""
    scope = page
    for f in frames or []:
        scope = scope.frame_locator(f)
    return scope.locator(selector)


def _settle(page, timeout: int = 8000, network_wait: int | None = None) -> None:
    """Best-effort wait for a page to finish loading/settling after an action, so the
    next screenshot shows the loaded page rather than a blank/loading frame.

    `network_wait` caps the networkidle part separately. It matters because plenty of ERPs
    hold a socket open, so networkidle NEVER fires and the wait always runs to its timeout.
    At 8s that made ticking six checkboxes in the recorder cost 48 seconds of dead time. A
    navigation is caught by domcontentloaded above, which keeps the full timeout, so the
    interactive paths can safely give networkidle a second and move on.
    """
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:  # noqa: BLE001
        pass
    # 1.5s by default, NOT the full timeout. Waiting 8s for networkidle looked harmless until
    # it was measured on a real ERP: enterprise apps hold a socket open, so networkidle never
    # arrives and every single settle burned the whole 8 seconds. With ~29 settle points and a
    # 19-step replay that is minutes of nothing happening. The waits that actually know when
    # this ERP has finished are domcontentloaded above and _settle_postback (which reads the
    # ASP.NET async-postback flag), so this one only needs to catch a short burst of XHRs.
    # A caller that genuinely needs longer can still pass network_wait.
    # On an ASP.NET WebForms app the postback flag answers this question exactly, so waiting on
    # the network as well is paying twice for one answer - and the ERP holds a socket open, so
    # the networkidle half of that never returns early. Measured: with this and _settle_postback
    # both waiting, a step that ticked one already-visible checkbox cost 16.5s. The flag is not
    # a guess about whether the page is busy; it is the page saying so.
    if network_wait is None and _is_webforms(page):
        try:
            page.wait_for_load_state("networkidle", timeout=400)
        except Exception:  # noqa: BLE001
            pass
        _await_postback_flag(page, timeout)
        return
    try:
        page.wait_for_load_state(
            "networkidle", timeout=1500 if network_wait is None else network_wait)
    except Exception:  # noqa: BLE001 — many apps keep sockets open; don't block on it
        page.wait_for_timeout(200)


# An AI rule's answer, keyed on everything it depends on: the rule text, the values it is
# given and the options it must choose between. Two presses of "step back" re-run the same
# steps, and paying the model again for an identical question is money for nothing.
_AI_MEMO: dict[tuple, str] = {}
_AI_MEMO_MAX = 256


def resolve_ai_step(step: dict, values: dict) -> str:
    """Answer one AI-rule step, memoised. Safe to call from any thread."""
    from app.core.llm import resolve_ai_value

    prompt = step.get("prompt") or ""
    options = tuple(step.get("options") or ())
    key = (prompt, options, tuple(sorted((str(k), str(v)) for k, v in (values or {}).items())))
    if key in _AI_MEMO:
        return _AI_MEMO[key]
    answer = resolve_ai_value(prompt, values, step.get("options"))
    if len(_AI_MEMO) >= _AI_MEMO_MAX:
        _AI_MEMO.clear()
    _AI_MEMO[key] = answer
    return answer


def prefetch_ai(steps: list, values: dict) -> list:
    """Resolve the AI rules among these steps BEFORE the browser thread is entered.

    The recorder's Chromium lives on one thread that also serves the screenshot poll, so a
    model call made there freezes the live view for its whole duration. Everything an AI rule
    needs is known up front, so it is answered here and carried in on the step.

    Returns copies - the caller's steps are not modified, and a step that is not an AI rule is
    passed through untouched.
    """
    out = []
    for st in steps or []:
        if isinstance(st, dict) and st.get("prompt"):
            out.append({**st, "_resolved": resolve_ai_step(st, values or {})})
        else:
            out.append(st)
    return out


def _resolve_val(step: dict, values: dict) -> str:
    """Resolve a step's value (literal, [[field]] placeholder, or AI rule)."""
    if step.get("prompt"):
        # Already answered off-thread by prefetch_ai. An empty string is a real answer (the
        # model gave something that is not one of the options), so test for the KEY.
        if "_resolved" in step:
            return step["_resolved"]
        return resolve_ai_step(step, values)
    v = step.get("value") or (f"[[{step['field_label']}]]" if step.get("field_label") else None)
    if not v:
        return ""
    if v.startswith("[[") and v.endswith("]]"):
        return values.get(v[2:-2], "")
    return v


def _visible_index(loc) -> tuple[int, int | None]:
    """(how many elements the selector matched, index of the first VISIBLE one or None).

    Separated out so a caller can tell the two failure modes apart: "nothing matched" and
    "several matched but every one of them is hidden". They need different messages, and the
    second must not be clicked — waiting the full click timeout on an invisible element just
    turns a clear problem into a baffling one.
    """
    try:
        n = loc.count()
    except Exception:  # noqa: BLE001
        return (0, None)
    for k in range(min(n, 10)):
        try:
            if loc.nth(k).is_visible():
                return (n, k)
        except Exception:  # noqa: BLE001
            continue
    return (n, None)


def _select_by_option(page, frames: list | None, value: str) -> str | None:
    """A NATIVE <select> that actually contains `value` as one of its options.

    For a dropdown step whose recorded path has gone stale. Safe in a way that guessing a
    text field never is: a <select> offering exactly this option is almost certainly the one
    the step meant, because no other dropdown on the page offers it. Deliberately native
    <select> only — a react-select keeps no options in the DOM until it is opened, so there
    is nothing to match on.
    """
    v = (value or "").strip()
    if not v or len(v) > 60:
        return None
    esc = v.replace("\\", "\\\\").replace('"', '\\"')
    scope = page
    for f in frames or []:
        scope = scope.frame_locator(f)
    for cand in (f'select:has(option:text-is("{esc}"))',
                 f'select:has(option[value="{esc}"])'):
        try:
            loc = scope.locator(cand)
            n, vis = _visible_index(loc)
        except Exception:  # noqa: BLE001
            continue
        if vis is not None:
            return f"{cand} >> nth={vis}"
    return None


def _one(loc):
    """Pin a locator to ONE element — the first VISIBLE match.

    A recorded selector often matches several elements: a positional path made of plain
    <div>s, or a text match that hits both a label and its container. Playwright refuses to
    act on an ambiguous locator — "strict mode violation: resolved to 5 elements" — rather
    than guess, which turns a perfectly workable step into a hard failure.

    _present() already tolerates this by waiting on `.first`, so the ACTION has to tolerate
    it too, or presence and action disagree: the check says the element is there and then
    clicking it throws.

    First *visible* rather than plain `.first`, because widgets that keep hidden copies in the
    DOM — react-select, a hidden template row, a collapsed menu — usually have the invisible
    one at index 0.
    """
    try:
        n = loc.count()
    except Exception:  # noqa: BLE001
        return loc.first
    if n <= 1:
        return loc.first
    for k in range(min(n, 10)):
        try:
            if loc.nth(k).is_visible():
                return loc.nth(k)
        except Exception:  # noqa: BLE001
            continue
    return loc.first


def _present(page, sel, frames, timeout=3000) -> bool:
    if not sel:
        return False
    loc = _scoped(page, frames, sel)
    try:
        loc.first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        pass
    # `.first` can be a HIDDEN template with the real element further down the list — a
    # react-select keeps spare copies, a grid keeps a hidden row to clone. Waiting only on
    # the first match then reports "never appeared" for something plainly on screen.
    try:
        for k in range(min(loc.count(), 10)):
            if loc.nth(k).is_visible():
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _frame_paths(page) -> list[list]:
    """Every iframe chain on the page, innermost last, as lists of selectors.

    An ERP that keeps one window frame - Live Impex has #WinCommon_ifrmCommon - loads screen
    after screen into it, so a control recorded on the top page can turn up inside the frame on
    the next run, or the other way round. Without a way to enumerate the frames there is nothing
    to fall back to and the step just fails.
    """
    out: list[list] = []
    try:
        handles = page.query_selector_all("iframe, frame")
    except Exception:  # noqa: BLE001
        return out
    for h in handles[:12]:                      # a dozen is far more than any real screen has
        try:
            ident = h.get_attribute("id") or ""
            name = h.get_attribute("name") or ""
        except Exception:  # noqa: BLE001
            continue
        if ident:
            out.append([f"#{ident}"])
        elif name:
            out.append([f"iframe[name='{name}']"])
    return out


def _elsewhere(page, sel: str, frames: list | None, timeout: int = 1200) -> list | None:
    """Where else on the page is `sel`? Returns the frame chain that has it, or None.

    Only ever called after the recorded location has already failed, so it costs nothing on a
    normal run. It answers the case the ERP creates for itself: the screen moved into (or out
    of) the shared window frame, and the element is right there under a different chain.
    """
    tried = [list(frames or [])]
    candidates = [[]] + _frame_paths(page)       # the top page, then each frame
    for chain in candidates:
        if chain in tried:
            continue
        tried.append(chain)
        try:
            loc = _scoped(page, chain, sel)
            loc.first.wait_for(state="visible", timeout=timeout)
            return chain
        except Exception:  # noqa: BLE001
            continue
    return None


def _tab_key(url: str) -> str:
    """A tab's identity WITHOUT the query string.

    The recorded URL of a switch_tab step is a real destination but a dead literal:
    `.../ImpDoc.aspx?JobId=2945518&rpp=178752642`. The JobId belongs to the job that was
    recorded, so an exact comparison can only ever match that one job, and every other run
    fell through to picking a tab by ORDER instead. The screen is `ImpDoc.aspx`; the id is
    which job is on it. Compare the first and ignore the second.
    """
    return (url or "").split("?", 1)[0].split("#", 1)[0].rstrip("/").lower()


def _find_tab(pages: list, want: int, target_url: str):
    """The tab a switch_tab step meant, best evidence first.

    Exact URL, then the same SCREEN regardless of which job is on it, then position. Position
    is last because tab order depends on how fast each window opened, which is not something
    the recording controls.
    """
    if target_url:
        for q in pages:
            if q.url == target_url:
                return q
        key = _tab_key(target_url)
        if key:
            same = [q for q in pages if _tab_key(q.url) == key]
            # only when it is unambiguous: two tabs on the same screen say nothing about which
            if len(same) == 1:
                return same[0]
    if 0 <= want < len(pages):
        return pages[want]
    return None


def _row_selector(page, frames: list | None, text: str) -> str | None:
    """The TABLE ROW (or cell) carrying this text - not the link inside it.

    A results grid is the one place where "find the clickable thing with this text" is the wrong
    instinct. The row's text is also the link's text, so the text fallback substituted an
    ICEGATE link for the row a double-click was meant to open, and the run left the ERP
    entirely. A row is also the one thing that must NOT be found by position: search for a
    different job, get a different number of results, and `tr:nth-of-type(2)` opens the wrong
    one silently.

    Matched on a distinctive fragment of the recorded text - a job number - rather than the
    whole cell, because a grid pads and re-orders what it shows around it.
    """
    import re

    raw = (text or "").strip()
    if not raw:
        return None
    # The most identifying thing in a grid row is a reference: MAA/SI/21355/26-27, a container
    # number, an invoice number. Prefer one of those over the whole string, which carries the
    # consignee name and whatever else the grid pads the row with.
    ref = None
    for m in re.findall(r"[A-Z0-9][A-Z0-9/-]{6,}", raw.upper()):
        if any(ch.isdigit() for ch in m):
            ref = m
            break
    needle = ref or raw.split("	")[0].strip()[:60]
    if len(needle) < 4:
        return None
    esc = needle.replace("\\", "\\\\").replace('"', '\\"')
    scope = page
    for f in frames or []:
        scope = scope.frame_locator(f)
    for cand in (f'tr:has-text("{esc}")', f'td:has-text("{esc}")',
                 f'[role=row]:has-text("{esc}")'):
        try:
            loc = scope.locator(cand)
            n = min(loc.count(), 5)
        except Exception:  # noqa: BLE001
            continue
        for k in range(n):
            try:
                if loc.nth(k).is_visible():
                    return f"{cand} >> nth={k}"
            except Exception:  # noqa: BLE001
                continue
    return None


def _text_selector(page, frames: list | None, text: str) -> str | None:
    """A selector that finds a clickable element by the TEXT it was recorded with.

    Menu items and links rarely have an id, so the recorder falls back to a positional path
    like `div > div:nth-of-type(9) > div > section > div > a:nth-of-type(2)`. That says "the
    9th div, then the 2nd link" - and an ERP dashboard that renders a different number of
    widgets, or has not finished loading, moves the element out from under it. The visible
    text ("Operations") is far more stable than its position, so when the recorded selector
    no longer matches we look the element up by its words instead.

    Returns None if nothing suitable is visible, so the caller can fail as it would have.
    """
    t = (text or "").strip()
    # Anything long is a paragraph, not a label - matching on it would hit half the page.
    if not t or len(t) > 80:
        return None
    esc = t.replace("\\", "\\\\").replace('"', '\\"')
    scope = page
    for f in frames or []:
        scope = scope.frame_locator(f)
    # Exact matches first, then the label-on-an-image cases, then loose "contains" - and only
    # on things that are actually clickable, never a bare <div>, or an outer container would
    # swallow the click.
    for cand in (
        # 1. the clickable's own text is the label
        f'a:text-is("{esc}")', f'button:text-is("{esc}")',
        f'[role=button]:text-is("{esc}")', f'[role=link]:text-is("{esc}")',
        f'input[type=submit][value="{esc}"]', f'input[type=button][value="{esc}"]',
        # 2. ICON TILES / IMAGE BUTTONS. An ERP dashboard tile is an <a> wrapped round an
        #    <img>: the link holds no text at all and the words live on the image (alt/title)
        #    or in a caption beside it. Looking only for text that the clickable CONTAINS
        #    misses every one of them.
        f'a[title="{esc}"]', f'a[aria-label="{esc}"]',
        f'button[title="{esc}"]', f'button[aria-label="{esc}"]',
        f'a:has(img[alt="{esc}"])', f'a:has(img[title="{esc}"])',
        f'[onclick]:has(img[alt="{esc}"])',
        f'a:has(:text-is("{esc}"))', f'[onclick]:has(:text-is("{esc}"))',
        # clicking the image itself is fine - the event bubbles to the link around it
        f'img[alt="{esc}"]', f'img[title="{esc}"]',
        # 3. last resort: anything clickable merely containing the words
        f'a:has-text("{esc}")', f'button:has-text("{esc}")',
        f'[onclick][title="{esc}"]', f'[aria-label="{esc}"]',
    ):
        try:
            loc = scope.locator(cand)
            n = min(loc.count(), 5)
        except Exception:  # noqa: BLE001
            continue
        for k in range(n):
            try:
                if loc.nth(k).is_visible():
                    # `>> nth=` pins it to one element: without it a "contains" match with
                    # several hits raises a strict-mode error instead of clicking.
                    return f"{cand} >> nth={k}"
            except Exception:  # noqa: BLE001
                continue
    return None


def _reveal(page, frames, opener: dict | None, sel: str,
            timeout: int = 2500) -> dict | None:
    """Bring back a menu item whose menu has closed, by HOVERING what opened it.

    The Standard Documents list on Live Impex only exists while its button is hovered. A
    recorded press on that button is not a press that holds the list open, so by the time the
    next step looked for `#lnkCheckList` the list had gone - and the run stopped on an item a
    person can plainly see. Nothing was wrong with the selector.

    Hover, and only hover. Pressing the previous step again would also open a click-menu, but
    that step might have been Save: re-pressing it would file the entry twice. A hover cannot
    submit, cannot toggle, and cannot tick anything, so it is safe to try on any step - and if
    it does not help, the caller reports the same error it always did.
    """
    if not opener or not opener.get("selector"):
        return None
    try:
        loc = _scoped(page, opener.get("frames") or [], opener["selector"]).first
        if not loc.is_visible():
            return None
        loc.hover(timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    if _present(page, sel, frames, timeout=timeout):
        return {"selector": opener["selector"], "frames": opener.get("frames") or []}

    # Hovering did nothing, so this menu opens on a PRESS. On Live Impex the Standard Documents
    # list opens when the button is clicked and is then shut again by the postback that follows
    # it, so by the next step the item is gone - which is what "never appeared after 20s" was
    # really reporting.
    #
    # Pressing the previous step again is safe ONLY when that step is not something that
    # commits. Re-pressing Save would file the entry twice, and step 24 of this very script is
    # `#btnSaveCompleted`, so this is not hypothetical. Read what the control says and refuse
    # anything that reads like a commit; a false refusal only costs the error message we
    # already had.
    if not _is_menu_opener(loc):
        return None
    try:
        loc.click(timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    if _present(page, sel, frames, timeout=timeout):
        return {"selector": opener["selector"], "frames": opener.get("frames") or [],
                "press": True}
    return None


# Words that mean "this does something", not "this shows a list". Anything matching is never
# pressed a second time on our initiative.
_COMMITS = re.compile(
    r"\b(save|submit|send|ok|proceed|confirm|continue|approve|delete|remove|"
    r"file|post|finish|complete|yes|accept|pay|generate)\b", re.I)


def _is_menu_opener(loc) -> bool:
    """Is it safe to press this again to re-open a menu it owns?

    Judged on what the control SAYS, because that is what a person judges it on. A button
    reading "Standard Documents" opens a list; one reading "Save" does not, and pressing it
    twice files the entry twice.
    """
    try:
        words = " ".join(x for x in (
            loc.get_attribute("value") or "",
            (loc.inner_text(timeout=1500) or ""),
            loc.get_attribute("title") or "",
            loc.get_attribute("aria-label") or "",
        ) if x)[:200]
    except Exception:  # noqa: BLE001
        return False
    if not words.strip():
        return False          # nothing to judge it on - do not press
    return not _COMMITS.search(words)


def _press_held(page, opener: dict, loc, dbl: bool = False) -> None:
    """Press something inside a hover menu WITHOUT moving the pointer off the opener.

    Two shapes of hover menu exist and they behave differently. When the list sits inside the
    opener's own container, walking the pointer to an item keeps the container hovered and an
    ordinary click works. When the list is a sibling shown by `#btn:hover ~ .menu`, leaving the
    button closes the list mid-way and the click times out on an element that was there a
    moment ago - which is the shape that fails.

    So do not walk to it. Hold the opener hovered and raise the events on the item where it
    stands. mouseover/mousedown/mouseup come along too: plenty of ERP menus commit on mouseup
    rather than click, and a bare click event would do nothing on those.
    """
    _scoped(page, opener.get("frames") or [], opener["selector"]).first.hover(timeout=3000)
    target = loc.first
    for ev in ("mouseover", "mousedown", "mouseup", "click"):
        target.dispatch_event(ev)
    if dbl:
        target.dispatch_event("dblclick")


_OPEN_JS = """() => {
  const hits = [];
  // WHAT IT SAYS, first. Matching on id alone reported four toolbar divs with no text at all
  // - PopupHeader1_ToolBar, PopupHeader1_ToolBarsCtrl1_dvSave - which told us a popup was
  // there and nothing whatever about what it was asking. A popup's chrome carries no words;
  // the box floating over the page does. Highest z-index first: that is the thing on top.
  const floating = [];
  for (const el of document.querySelectorAll('div,section,form,table')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'absolute') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 150 || r.height < 50) continue;
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    const txt = (el.innerText || '').replace(/[\\s\\u00a0]+/g, ' ').trim();
    if (txt.length > 8) floating.push([Number(cs.zIndex) || 0, txt.slice(0, 140)]);
  }
  floating.sort(function (a, b) { return b[0] - a[0]; });
  for (const f of floating.slice(0, 2)) hits.push('SAYS: ' + f[1]);
  // Then the named things, for the selector a missing step could be re-recorded against.
  const sel = '[role=dialog],[role=alertdialog],[id*=alert],[id*=Alert],[id*=popup],'
            + '[id*=Popup],[id*=modal],[id*=Modal],[class*=modal],[class*=popup],'
            + '[class*=alert],[class*=dialog]';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (!r.width || !r.height || cs.visibility === 'hidden' || cs.display === 'none') continue;
    const name = el.id || el.className || el.tagName;
    const txt = (el.innerText || '').replace(/[\\s\\u00a0]+/g, ' ').trim().slice(0, 40);
    hits.push(String(name).slice(0, 40) + (txt ? ':' + txt : ''));
    if (hits.length >= 6) break;
  }
  return hits;
}"""


def _whats_open(page) -> str:
    """What dialog-ish thing IS on screen, when the thing we wanted is not.

    A log line saying an element is "not on screen" tells you what is missing and nothing about
    what is there instead - so two runs that skip the same optional step look identical whether
    the ERP put up a different confirmation, a validation message, or nothing at all. Reading it
    off the page turns the next run into evidence rather than another guess. Best effort, in
    every frame, and never allowed to disturb the run.
    """
    seen: list = []
    try:
        for scope in [page] + list(page.frames or []):
            try:
                for item in (scope.evaluate(_OPEN_JS) or []):
                    if item and item not in seen:
                        seen.append(item)
            except Exception:  # noqa: BLE001
                continue
            if len(seen) >= 4:
                break
    except Exception:  # noqa: BLE001
        return ""
    return ("  (on screen instead: " + "; ".join(seen[:4]) + ")") if seen else            "  (nothing dialog-like is open)"


def _absent_and_settled(page, sel: str, frames: list, budget_ms: int = 15000,
                        min_ms: int = 8000) -> bool:
    """Is this element genuinely not coming - or has the page simply not finished yet?

    An OPTIONAL step is skipped when its element is not there, and that skip used to be a flat
    three-second look. Three seconds is right for a screen that never came up; it is NOT right
    for a confirmation the ERP raises AFTER a save it is still processing. Steps 23 and 24 of
    this customer's script are exactly that - a "Proceed" and an "Ok" that follow a Save - and
    a save that takes longer than three seconds had them skipped before they could appear.

    So the two cases are told apart by asking the page whether it is still working. Absent AND
    idle means it is not coming: skip at once, which is what stopped a live job sitting on step
    23 for ninety seconds. Absent WHILE the page is still posting back means wait - it may yet
    arrive. The budget is the ceiling for that waiting, not the normal cost.
    """
    import time as _t

    deadline = _t.monotonic() + budget_ms / 1000.0
    # A floor as well as a ceiling. Asking "is the page busy?" is not enough on its own: this
    # ERP's postback flag goes quiet the instant a save finishes, and the confirmation it then
    # renders arrives AFTER that. Answering on the flag alone made the skip faster than the
    # flat three seconds it replaced - 0.9s - which is the opposite of the point.
    floor = _t.monotonic() + min_ms / 1000.0
    while True:
        if _present(page, sel, frames, timeout=GLANCE_MS):
            return False                       # it IS there - not absent at all
        try:
            busy = bool(page.evaluate(_POSTBACK_BUSY_JS)) or page.evaluate(
                "() => document.readyState") != "complete"
        except Exception:  # noqa: BLE001 — a page that cannot answer is not a page to wait on
            busy = False
        now = _t.monotonic()
        if now >= deadline or (not busy and now >= floor):
            return True
        page.wait_for_timeout(250)


def _repair_selector(page, step: dict, frames: list, timeout: int = 2500) -> str | None:
    """If a click step's recorded selector is gone but we know the element's text, hand back a
    text-based selector to use instead. Only for clicks — a fill must go in the field it was
    recorded against, never in whatever happens to carry the same words."""
    if (step.get("action") or "") not in ("click", "submit", "double_click"):
        return None
    label = step.get("description") or ""
    if not label:
        return None
    if _present(page, step.get("selector"), frames, timeout=timeout):
        return None                      # the recorded selector is fine - leave it alone
    # A double-click wants the ROW, and a row's text is also its link's text - so the general
    # clickable-by-text search hands back the link and the job run walks off to another site.
    # The one action where "the thing containing these words" beats "the clickable with these
    # words". Same rule in the recorder's replay, so both paths behave the same.
    if (step.get("action") or "") == "double_click":
        row = _row_selector(page, frames, label)
        if row:
            return row
    return _text_selector(page, frames, label)


def _fuzzy_pick(value: str, options: list) -> str | None:
    """Best fuzzy match of `value` among `options` (case-insensitive). ERP dropdowns rarely
    hold the exact extracted text, so match tolerantly: exact -> substring -> closest by
    difflib ratio (>=0.6). Returns the chosen option string, or None if nothing is close."""
    import difflib

    def norm(s: str) -> str:
        # Collapse runs of whitespace and drop the punctuation ERPs sprinkle differently.
        # "SHANGHAI , CHINA" and "SHANGHAI, CHINA" are the same place; "  ultratech   cement"
        # is the same company as "ULTRATECH CEMENT". Only stripping the ends missed both.
        s = re.sub(r"\s+", " ", str(s or "").strip().lower())
        return s

    def bare(s: str) -> str:
        return re.sub(r"[^\w\s]+", " ", norm(s)).strip()

    def toks(s: str) -> set:
        return {t for t in bare(s).split(" ") if t}

    v = norm(value)
    opts = [o for o in (options or []) if o and str(o).strip()]
    if not v or not opts:
        return None
    low = [(o, norm(o)) for o in opts]
    for o, ol in low:                       # exact, once whitespace is normalised
        if ol == v:
            return o
    for o, ol in low:                       # exact ignoring punctuation too
        if bare(ol) == bare(v):
            return o
    subs = [o for o, ol in low if v in ol or ol in v]  # substring either direction
    if subs:
        return min(subs, key=lambda o: abs(len(norm(o)) - len(v)))
    subs = [o for o, ol in low if bare(v) in bare(ol) or bare(ol) in bare(v)]
    if subs:
        return min(subs, key=lambda o: abs(len(bare(o)) - len(bare(v))))
    # WORD OVERLAP, before falling back to character similarity. An extractor can hand back
    # the same words in another order — "españa valencia" for "VALENCIA, ESPAÑA" — which
    # scores badly character-by-character but is obviously the right option.
    vt = toks(v)
    if vt:
        scored = []
        for o, ol in low:
            ot = toks(ol)
            if not ot:
                continue
            shared = len(vt & ot)
            if shared:
                # every word of the shorter side found in the longer one == a full match
                scored.append((shared / min(len(vt), len(ot)), shared, o))
        if scored:
            scored.sort(reverse=True)
            frac, _shared, o = scored[0]
            if frac >= 0.999:
                return o
    best, best_ratio = None, 0.0            # closest by similarity
    for o, ol in low:
        r = difflib.SequenceMatcher(None, v, ol).ratio()
        if r > best_ratio:
            best, best_ratio = o, r
    return best if best_ratio >= 0.6 else None


def _select_fuzzy(loc, value: str) -> bool:
    """Pick the closest <option> in a native <select>, tolerant of ERP wording."""
    for kwargs in ({"label": value}, {"value": value}):   # fast path: exact
        try:
            loc.select_option(timeout=4000, **kwargs)
            return True
        except Exception:  # noqa: BLE001
            pass
    try:
        pairs = loc.evaluate("el => Array.from(el.options).map(o => [o.label || o.textContent || '', o.value])")
    except Exception:  # noqa: BLE001
        pairs = []
    match = _fuzzy_pick(value, [p[0] for p in pairs if p and p[0]])
    if match:
        try:
            loc.select_option(label=str(match).strip(), timeout=4000)
            return True
        except Exception:  # noqa: BLE001
            pass
    vmatch = _fuzzy_pick(value, [p[1] for p in pairs if p and p[1]])
    if vmatch:
        try:
            loc.select_option(value=vmatch, timeout=4000)
            return True
        except Exception:  # noqa: BLE001
            pass
    return False


# STRICT selectors only match real autocomplete widgets — safe to keyboard-select on.
# BROAD adds generic dropdown/list containers; used only for fields the recorder already
# marked as type-aheads (where a suggestion list is expected).
_STRICT_SUGGESTION_SELECTORS = (
    ".ui-menu-item", ".ui-autocomplete li", 'ul.ui-autocomplete li', '[role="option"]',
    ".tt-suggestion", ".autocomplete-suggestion", ".select2-results__option",
    ".easy-autocomplete li", ".awesomplete li", ".typeahead li",
    # Component-library dropdowns render their options as DIVs, not <li>, with the library's
    # own class prefix. None of the jQuery-era selectors above match them, so a react-select
    # or Angular Material list looked empty and the option could never be clicked.
    '[class*="select__option"]', '[class*="-option__"]',
    "mat-option", ".mat-option", "[class*=MuiMenuItem]", "[class*=MuiAutocomplete-option]",
    ".ui-selectonemenu-item", ".ant-select-item-option", "[class*=chakra-menu__menuitem]",
    ".v-list-item", ".p-dropdown-item",
)
_BROAD_SUGGESTION_SELECTORS = _STRICT_SUGGESTION_SELECTORS + (".dropdown-menu li", ".dropdown-item", "li")


def _typeahead_candidates(page, scope, strict: bool = False):
    """Ordered (text, element) of VISIBLE type-ahead suggestions. Searches the field's own
    frame AND the top document (some widgets portal the list out of the input's frame).
    `strict` matches only real autocomplete widgets — never generic page <li> (nav menus),
    so a plain text field is never mistaken for a type-ahead."""
    selectors = _STRICT_SUGGESTION_SELECTORS if strict else _BROAD_SUGGESTION_SELECTORS
    roots = [scope] if page is scope else [scope, page]
    for root in roots:
        for css in selectors:
            try:
                loc = root.locator(css)
                n = min(loc.count(), 30)
            except Exception:  # noqa: BLE001
                continue
            cands = []
            for i in range(n):
                try:
                    el = loc.nth(i)
                    if not el.is_visible():
                        continue
                    t = (el.inner_text(timeout=400) or "").strip()
                except Exception:  # noqa: BLE001
                    continue
                if t:
                    cands.append((t, el))
            if cands:
                return cands
    return []


def _click_option_by_text(page, scope, value: str) -> bool:
    """Click a dropdown item by its visible text, wherever the widget rendered it.

    Custom dropdowns - ASP.NET WebForms, jQuery UI, select2, React - usually paint their open
    list into a container at the END of <body>, not inside the box that was clicked. A search
    scoped to the clicked element therefore finds nothing, which is why typing into the visible
    div looked like it did nothing at all. Search the whole page, most specific markup first.
    """
    needle = (value or "").strip()
    if not needle:
        return False
    exact = re.compile(r"^\s*" + re.escape(needle) + r"\s*$", re.I)
    for root in (scope, page):
        for sel in ('[role="option"]', ".dropdown-item", ".select2-results__option",
                    ".ui-menu-item", "li", "option", "a", "td", "span", "div"):
            try:
                items = root.locator(sel).filter(has_text=exact)
                count = min(items.count(), 8)
            except Exception:  # noqa: BLE001
                continue
            for i in range(count):
                item = items.nth(i)
                try:
                    if item.is_visible():
                        item.click(timeout=2500)
                        page.wait_for_timeout(250)
                        return True
                except Exception:  # noqa: BLE001
                    continue
    return False


def _control_state(loc) -> str | None:
    """What a form control currently shows — its value if it is an input, otherwise its text.

    Used to tell whether an action actually committed. A react-select style control is a
    <div>, not an <input>, so input_value() throws on it and its selection only shows as text.
    """
    try:
        return loc.input_value(timeout=800)
    except Exception:  # noqa: BLE001
        pass
    try:
        return (loc.inner_text(timeout=800) or "").strip()
    except Exception:  # noqa: BLE001
        return None


# The answer a dialog step has armed, per page: [accept?, reply text]. A LIST so the value
# can be updated in place - the listener below reads it at the moment the popup appears, so
# re-arming never has to add or remove a listener. Weak keys: a closed page drops out.
_DIALOG_ANSWERS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _dialog_answer(page) -> tuple[bool, str]:
    """How the popup on this page should be answered: (accept?, text for a prompt).

    Defaults to accepting, which is what a person clicking through by hand would do and what
    keeps the page - and the recorder screenshot - from freezing on an unanswered alert.
    """
    try:
        cell = _DIALOG_ANSWERS.get(page) if page is not None else None
    except TypeError:      # not weak-referenceable
        cell = None
    return (bool(cell[0]), cell[1]) if cell else (True, "")


# One DevTools session per page, reused. Opening one costs a round trip, and this is asked on
# every touch.
_CDP_SESSIONS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _has_click_listener(page, x: float, y: float, hops: int = 3) -> bool:
    """Does anything at this point actually listen for a click?

    Page JavaScript cannot answer this. Listeners attached with addEventListener are not
    enumerable from inside the page, so an ERP tab built as a <td> with its handler attached in
    script - no inline onclick, no cursor:pointer - looks EXACTLY like a plain data cell. The
    recorder therefore offered "Pick data" on the Entity/Shipment/Invoice tabs of a Logi-Sys
    form, as though the intent were to read a value out of them.

    The DevTools protocol can enumerate them. Bubbling means a listener on an ancestor is
    enough - clicking the child still fires it - so this only has to answer yes/no over a few
    levels, not work out which element owns the handler.

    Any failure answers False, which simply leaves the previous behaviour in place.
    """
    try:
        sess = _CDP_SESSIONS.get(page)
        if sess is None:
            sess = page.context.new_cdp_session(page)
            _CDP_SESSIONS[page] = sess
        # Resolve the same element the descriptor did, including through open shadow roots.
        expr = (
            "(() => { let el = document.elementFromPoint(%s, %s); let g = 0;"
            " while (el && el.shadowRoot && g++ < 6) {"
            "   const inner = el.shadowRoot.elementFromPoint(%s, %s);"
            "   if (!inner || inner === el) break; el = inner; }"
            " return el; })()" % (x, y, x, y)
        )
        res = sess.send("Runtime.evaluate", {"expression": expr, "returnByValue": False})
        oid = ((res or {}).get("result") or {}).get("objectId")
        if not oid:
            return False
        for _ in range(max(1, hops + 1)):
            got = sess.send("DOMDebugger.getEventListeners", {"objectId": oid, "depth": 0})
            for listener in (got or {}).get("listeners") or []:
                if listener.get("type") in ("click", "mousedown", "mouseup", "dblclick"):
                    return True
            up = sess.send("Runtime.callFunctionOn", {
                "objectId": oid,
                "functionDeclaration": "function () { return this.parentElement; }",
                "returnByValue": False,
            })
            oid = ((up or {}).get("result") or {}).get("objectId")
            if not oid:
                break
        return False
    except Exception:  # noqa: BLE001 - no DevTools, wrong browser, protocol change: say no
        return False


def _arm_dialog(page, step: dict) -> str:
    """Answer the next native alert/confirm/prompt the way this step recorded it.

    Playwright dismisses dialogs by default, so a confirm-on-submit would silently CANCEL the
    entry. The answer is armed BEFORE the click that triggers it and stays armed until another
    dialog step changes it - the same confirm often reappears once per row of a grid loop.

    Only ever ONE listener per page. page.on() adds rather than replaces, so arming twice used
    to stack handlers and the OLDEST answer won: a draft that accepts its first popup and must
    dismiss its second would accept both. Returns the verb, for the log.
    """
    accept = str(step.get("value") or "accept").strip().lower() != "dismiss"
    reply = step.get("prompt_text") or ""
    cell = _DIALOG_ANSWERS.get(page)
    if cell is None:
        _DIALOG_ANSWERS[page] = [accept, reply]

        def _on_dialog(d, page=page):
            a, r = _dialog_answer(page)
            try:
                d.accept(r) if a else d.dismiss()
            except Exception:  # noqa: BLE001
                pass

        page.on("dialog", _on_dialog)
    else:
        cell[0], cell[1] = accept, reply
    return "accept" if accept else "dismiss"


def _read_text(loc, kind: str | None = None) -> str:
    """What an element reports as its content, honouring the step capture_kind.

    A <select> is why this cannot just be inner_text: inner_text on a dropdown returns EVERY
    option, so a captured dropdown used to store the whole list rather than what was chosen.
    kind "value" asks for what the form submits, "text" for the label shown on screen. A
    react-select style control is a <div>, so input_value() throws on it and the label is all
    there is - hence the fallbacks rather than a single read.
    """
    want = (kind or "").strip().lower()
    try:
        tag = str(loc.evaluate("e => e.tagName") or "").upper()
    except Exception:  # noqa: BLE001
        tag = ""
    if tag == "SELECT":
        try:
            if want == "value":
                return (loc.input_value(timeout=2000) or "").strip()
            return (loc.evaluate(
                """e => { const o = e.selectedOptions ? e.selectedOptions[0] : null;
                          return o ? o.text : ""; }""") or "").strip()
        except Exception:  # noqa: BLE001
            pass
    if want == "value" or tag in ("INPUT", "TEXTAREA"):
        try:
            v = (loc.input_value(timeout=2000) or "").strip()
            if v or want == "value":
                return v
        except Exception:  # noqa: BLE001
            pass
    try:
        t = (loc.inner_text(timeout=6000) or "").strip()
    except Exception:  # noqa: BLE001
        t = ""
    if t:
        return t
    try:
        return (loc.input_value(timeout=2000) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _commit_typeahead(page, scope, loc, value: str, strict: bool = False) -> bool:
    """Select the best-matching suggestion. KEYBOARD first (ArrowDown to the fuzzy-best item
    then Enter — fires the widget's real select handler; 'only-selected' fields ignore a plain
    click), then click that item. With strict=True, if NO real widget list is visible it does
    nothing (never blindly presses Enter on a plain field — that could submit the form)."""
    cands = _typeahead_candidates(page, scope, strict=strict)
    if cands:
        texts = [t for t, _ in cands]
        match = _fuzzy_pick(value, texts)
        idx = texts.index(match) if match in texts else 0
        # What the control shows now, so we can tell whether the keyboard actually committed
        # anything. Pressing keys at a widget that ignores them raises nothing at all, so
        # "no exception" is not evidence the option was chosen — and reporting success with
        # the field still empty is worse than failing.
        before = _control_state(loc)
        try:
            for _ in range(idx + 1):
                loc.press("ArrowDown")
                page.wait_for_timeout(80)
            loc.press("Enter")
            page.wait_for_timeout(250)
            if _control_state(loc) != before:
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            # The keyboard did nothing — a click-only suggestion list (a plain <div> menu, or
            # a react-select rendered outside the input). Click the matched item instead.
            cands[idx][1].click(timeout=3000)
            return True
        except Exception:  # noqa: BLE001
            pass
    if strict:
        return False   # plain field, no widget list → don't risk an Enter/submit
    try:
        loc.press("ArrowDown")   # recorded type-ahead but list not detected → take first item
        loc.press("Enter")
        return True
    except Exception:  # noqa: BLE001
        return False


# Wait until a document stops changing. Resolves once `ms` passes with no mutation, or at the
# hard cap - a page with a spinner or a clock never goes quiet, and a step must not hang on it.
_QUIET_JS = """
(opts) => new Promise((resolve) => {
  const quiet = opts.quiet, cap = opts.cap;
  let timer = null;
  const stop = (why) => { try { obs.disconnect(); } catch (e) {} resolve(why); };
  const obs = new MutationObserver(() => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => stop('quiet'), quiet);
  });
  try {
    obs.observe(document.documentElement,
                {childList: true, subtree: true, attributes: true, characterData: true});
  } catch (e) { resolve('no-observer'); return; }
  timer = setTimeout(() => stop('quiet'), quiet);
  setTimeout(() => stop('cap'), cap);
})
"""


# Everything _settle_postback does INSIDE a frame shares this one budget. Deliberately small:
# it is the difference between a slow step and a job that runs out of time waiting.
_FRAME_SETTLE_BUDGET_MS = 6000


def _frame_for(page, frames):
    """The Frame a step's iframe chain points at, or None if it cannot be resolved."""
    node = page
    for sel in frames or []:
        try:
            handle = node.query_selector(sel)
            if handle is None:
                return None
            inner = handle.content_frame()
            if inner is None:
                return None
            node = inner
        except Exception:  # noqa: BLE001
            return None
    return None if node is page else node


_WEBFORMS_JS = ("() => { try { return !!(window.Sys && Sys.WebForms"
                " && Sys.WebForms.PageRequestManager); } catch (e) { return false; } }")

# An UpdatePanel says so while it is working. This is the only RELIABLE signal that a postback
# has finished: a DOM-quiet check cannot tell "nothing will change" from "the change has not
# started yet", and networkidle never arrives on an app that holds a socket open.
_POSTBACK_BUSY_JS = ("() => { try { const m = window.Sys && Sys.WebForms"
                     " && Sys.WebForms.PageRequestManager;"
                     " return m ? !!m.getInstance().get_isInAsyncPostBack() : false; }"
                     " catch (e) { return false; } }")


def _is_webforms(scope) -> bool:
    """Does this page/frame run ASP.NET WebForms, so its postback flag can be trusted?"""
    try:
        return bool(scope.evaluate(_WEBFORMS_JS))
    except Exception:  # noqa: BLE001
        return False


def _await_postback_flag(scope, budget_ms: int) -> int:
    """Wait while an async postback is in flight. Returns the milliseconds left of the budget.

    Costs nothing when nothing is posting back, which is the ordinary case - the point of
    reading the flag instead of waiting on the network is that it answers immediately.
    """
    left = int(budget_ms)
    while left > 0:
        try:
            if not scope.evaluate(_POSTBACK_BUSY_JS):
                break
        except Exception:  # noqa: BLE001
            break
        scope.wait_for_timeout(150)
        left -= 150
    return max(0, left)


def _settle_postback(page, timeout: int = 6000, frames=None) -> None:
    """Wait out an ASP.NET postback.

    A WebForms control usually calls __doPostBack on change: the whole page reloads, or an
    UpdatePanel swaps part of it over XHR. Either way the element just interacted with is
    replaced, so the next action must not start until it has finished - otherwise it lands on
    a stale node and silently does nothing.

    `frames` matters more than it looks. When the form lives in an iframe - Live Impex puts the
    whole Import Document screen inside #ifrmInfo - the TOP page is already idle, so the waits
    below return at once and the next step starts while the iframe is still rendering. That is
    what made a click on the Shipment tab report ok and leave the screen on Entity: the click
    landed, the engine simply moved on too early. Waiting on the frame's own load state is not
    enough either, because the re-render can be pure JavaScript with no network at all - so the
    frame's DOM is watched until it stops changing.
    """
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:  # noqa: BLE001
        pass
    # This networkidle used to get the WHOLE 6s budget, and an ERP that holds a socket open
    # never fires it - so it burned all 6s, every time. A step settles TWICE, so with _settle's
    # own two waits that came to 1.5+6+1.5+6 = 15s per step. Measured on a page shaped like the
    # ERP's: 16.5s to tick one checkbox that was visible from the start.
    #
    # Where the app IS WebForms, networkidle is not the signal at all - the postback flag below
    # is, and it is authoritative - so a brief look for a burst of XHRs is enough. Where it is
    # not WebForms there is no flag to read, so that case keeps a real wait.
    webforms = _is_webforms(page)
    try:
        page.wait_for_load_state(
            "networkidle", timeout=400 if webforms else min(timeout, 1500))
    except Exception:  # noqa: BLE001
        page.wait_for_timeout(150)
    # The postback flag, on the TOP PAGE as well. It used to be read only inside an iframe, so a
    # WebForms screen whose form is NOT in a frame - which is most of them - was never asked the
    # one question that actually answers "has the postback finished", and the blind networkidle
    # above was all that stood in for it.
    if webforms:
        _await_postback_flag(page, timeout)
    if not frames:
        return
    frame = _frame_for(page, frames)
    if frame is None:
        return
    # ONE budget for all the frame work, not a timeout per wait. Four 6-second waits plus a
    # 6-second poll came to ~31s per call - and a step settles twice, so three slow steps could
    # spend a whole 180s job budget waiting. In the ordinary case every wait below returns at
    # once; this only bounds the pathological page.
    import time as _t

    frame_budget = _FRAME_SETTLE_BUDGET_MS
    started = _t.monotonic()

    def _left() -> int:
        return max(0, frame_budget - int((_t.monotonic() - started) * 1000))

    # Same reasoning as the top page: where the frame is WebForms its flag is the answer, so
    # networkidle only needs to catch a burst; where it is not, keep a real wait.
    frame_webforms = _is_webforms(frame)
    try:
        frame.wait_for_load_state("domcontentloaded", timeout=_left())
    except Exception:  # noqa: BLE001
        pass
    if _left() > 0:
        try:
            frame.wait_for_load_state(
                "networkidle", timeout=min(400 if frame_webforms else 1500, _left()))
        except Exception:  # noqa: BLE001
            pass
    if frame_webforms:
        _await_postback_flag(frame, _left())
    # Then a short quiet window so the re-render has painted before the next step reads it -
    # capped by whatever is left of the budget rather than a fixed 1.5s.
    cap = min(1200, _left())
    if cap > 200:
        try:
            frame.evaluate(_QUIET_JS, {"quiet": 250, "cap": cap})
        except Exception:  # noqa: BLE001
            pass


def _radio_or_check_by_label(scope, value: str) -> bool:
    """Tick the radio/checkbox in a group whose label reads `value`.

    An ASP.NET RadioButtonList / CheckBoxList is a <table> of inputs each followed by a
    <label>. It looks like a dropdown to a person but there is no <select> to choose from.
    """
    needle = (value or "").strip()
    if not needle:
        return False
    exact = re.compile(r"^\s*" + re.escape(needle) + r"\s*$", re.I)
    try:
        labels = scope.locator("label").filter(has_text=exact)
        for i in range(min(labels.count(), 6)):
            lab = labels.nth(i)
            try:
                for_id = lab.get_attribute("for")
            except Exception:  # noqa: BLE001
                for_id = None
            try:
                if for_id:
                    box = scope.locator(f"#{for_id}")
                    if box.count() and box.first.is_visible():
                        box.first.check(timeout=3000)
                        return True
                if lab.is_visible():
                    lab.click(timeout=3000)
                    return True
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return False


def _set_choice(page, scope, loc, value: str) -> tuple[bool, str]:
    """Put `value` into whatever kind of chooser this element turns out to be.

    An ERP renders a dropdown half a dozen ways, and an ASP.NET screen often uses several at
    once: a plain <select>; a styled div wrapping a hidden <select>; a RadioButtonList table;
    a Telerik / select2 / Kendo / jQuery widget that paints its list at the end of <body>; or a
    real type-ahead input. Each needs a different gesture, and using the wrong one looks
    exactly like "nothing happened" - which is how a value silently fails to reach the ERP.

    Returns (applied, action) where `action` is the step type that should be RECORDED:
    'select' for a genuine <select>, otherwise 'autocomplete'.
    """
    # 1 - a real <select>, either this element or one wrapped inside the styled box clicked.
    target = None
    try:
        if (loc.first.evaluate("e => e.tagName") or "").upper() == "SELECT":
            target = loc.first
    except Exception:  # noqa: BLE001
        pass
    if target is None:
        try:
            inner = loc.locator("select")
            if inner.count() > 0:
                target = inner.first
        except Exception:  # noqa: BLE001
            target = None
    if target is not None:
        try:
            if _select_fuzzy(target, value):
                _settle_postback(page)
                return True, "select"
        except Exception:  # noqa: BLE001
            pass

    # 2 - a radio / checkbox list inside this container.
    try:
        if _radio_or_check_by_label(loc, value):
            _settle_postback(page)
            return True, "autocomplete"
    except Exception:  # noqa: BLE001
        pass

    # 3 - a real type-ahead input: type and let the widget commit. strict, so a blind
    #     ArrowDown+Enter can never report success on an element that ignored the typing.
    try:
        loc.click(timeout=6000)
        # WHERE to type. A div-based widget (react-select, select2, Kendo) is not itself
        # typeable — it holds a real <input> for its search box. Typing at the container
        # raises "not an <input>", which used to abort the whole attempt before the fallbacks
        # below ever ran. Find the inner input and type there.
        typer = loc
        try:
            if not loc.first.evaluate(
                "e => ['INPUT','TEXTAREA'].includes(e.tagName) || e.isContentEditable"
            ):
                inner = loc.locator(
                    "input:not([type=hidden]):not([type=checkbox]):not([type=radio])")
                if inner.count() > 0:
                    typer = inner.first
        except Exception:  # noqa: BLE001
            pass
        try:
            typer.fill("")
        except Exception:  # noqa: BLE001
            pass
        try:
            # A filtering widget (react-select, select2, Kendo) narrows its list to whatever
            # is typed. If the extracted value is punctuated or spaced differently from the
            # option — "SHANGHAI, CHINA" against "SHANGHAI , CHINA" — typing all of it filters
            # the list to NOTHING and there is no longer anything to match against. So try the
            # whole value, then progressively less of it: the first two words, then the first
            # word. A person searching that box does exactly the same.
            probes = [value]
            # strip punctuation for the shorter probes: "SHANGHAI," still filters out an
            # option written "SHANGHAI , CHINA", so probe on the bare word instead.
            words = [w for w in re.sub(r"[^\w\s]+", " ",
                     re.sub(r"\s+", " ", (value or "").strip())).split(" ") if w]
            if len(words) > 2:
                probes.append(" ".join(words[:2]))
            if len(words) > 1:
                probes.append(words[0])
            for probe in probes:
                try:
                    typer.fill("")
                except Exception:  # noqa: BLE001
                    pass
                typer.type(probe, delay=30, timeout=6000)
                page.wait_for_timeout(600)
                # match on the FULL value, even though only a prefix was typed
                if _commit_typeahead(page, scope, typer, value, strict=True):
                    _settle_postback(page)
                    return True, "autocomplete"
        except Exception:  # noqa: BLE001
            pass   # not typeable - a div-based widget, handled below
    except Exception:  # noqa: BLE001
        pass

    # 4 - custom widget: the click above opened its list somewhere else in the page.
    if _click_option_by_text(page, scope, value):
        _settle_postback(page)
        return True, "autocomplete"

    # 5 - the label may sit beside the group rather than on an input (some ASP.NET skins).
    if _radio_or_check_by_label(scope, value):
        _settle_postback(page)
        return True, "autocomplete"
    return False, "autocomplete"


_DATE_PATTERNS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y",
    "%Y/%m/%d", "%d-%b-%Y", "%d/%m/%y",
)
_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")


# Finding the file input a control belongs to. An ERP rarely shows the input itself: it styles a
# button that clicks a hidden one, or draws a drop zone with the input tucked inside. Searching
# the whole page for `input[type=file]` picks the first one, which on a screen with two upload
# boxes is the wrong box - and the step still said ok.
_FIND_FILE_INPUT_JS = r"""
(el) => {
  function isFile(n) {
    return n && n.tagName === 'INPUT' && (n.getAttribute('type') || '').toLowerCase() === 'file';
  }
  function hiddenish(n) {
    try {
      const st = getComputedStyle(n);
      if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0)
        return true;
      const r = n.getBoundingClientRect();
      return r.width < 2 || r.height < 2;
    } catch (e) { return false; }
  }
  // Return the ELEMENT, not a flag. Tagging it and then looking the tag up with a page-level
  // selector only searched the top frame, so an upload inside an ERP's dialog frame timed out
  // waiting for something that was there all along.
  function mark(n) { return n; }
  if (!el) return null;
  if (isFile(el)) return mark(el);
  // inside the thing touched - a drop zone usually holds its input
  const inner = el.querySelector && el.querySelector('input[type=file]');
  if (inner) return mark(inner);
  // a <label for=...> pointing at one
  const forId = el.getAttribute && el.getAttribute('for');
  if (forId) {
    const t = document.getElementById(forId);
    if (isFile(t)) return mark(t);
  }
  // A styled button keeps its hidden input right beside it. Look at the SIBLINGS before
  // widening the search: walking out to <body> and taking the first input[type=file] in the
  // document put the file in a completely unrelated box on another part of the screen.
  for (const dir of ['previousElementSibling', 'nextElementSibling']) {
    // IMMEDIATE neighbours only. A button keeps its hidden input right beside it; scanning
    // five siblings along a flat page reached a DIFFERENT upload control entirely.
    let n = el[dir], hops = 0;
    while (n && hops++ < 2) {
      // Only a sibling that IS the input, and only a HIDDEN one. A styled button's partner
      // input is always hidden; a VISIBLE file box beside it is its own control belonging to
      // somebody else - accepting that put the workbook in the next upload box down the page.
      if (isFile(n) && hiddenish(n)) return mark(n);
      n = n[dir];
    }
  }
  // Widen a little, but never as far as <body>, and only accept a HIDDEN input: a visible one
  // is its own control belonging to somebody else.
  let n = el.parentElement, hops = 0;
  while (n && hops++ < 3 && n.tagName !== 'BODY' && n.tagName !== 'HTML') {
    const cands = n.querySelectorAll ? Array.from(n.querySelectorAll('input[type=file]')) : [];
    const hit = cands.find(hiddenish);
    if (hit) return mark(hit);
    n = n.parentElement;
  }
  return null;
}
"""


# Dropping a file on a zone that listens for drop events and has no input at all. The bytes are
# handed in as base64 because a page cannot read a path off the machine running the browser.
_DROP_FILE_JS = r"""
(el, [name, b64, mime]) => {
  if (!el) return 'no element';
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const file = new File([bytes], name, { type: mime });
  const dt = new DataTransfer();
  dt.items.add(file);
  function fire(type) {
    // dispatchEvent returns FALSE when the page called preventDefault - which is exactly how a
    // drop target says "I accept this". Without checking it, dropping on an unrelated button
    // reported success: the event was sent, nobody listened, and the step passed.
    return !el.dispatchEvent(
      new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
  }
  fire('dragenter');
  const willTake = fire('dragover');
  const took = fire('drop');
  if (!willTake && !took) return 'ignored';
  return 'dropped';
}
"""


def _attach_file(page, loc, path: str) -> str:
    """Put `path` into whatever upload control `loc` is, and prove it arrived.

    Everything here goes through the ELEMENT HANDLE rather than a selector, because the control
    is often inside a dialog's own frame - a page-level selector cannot see it, and the first
    version of this timed out for exactly that reason.

    Returns how it was done, for the log. Raises when the file cannot be shown to have landed:
    an upload that quietly attaches nothing is the one outcome that must never pass.
    """
    import base64
    import mimetypes

    want = pathlib.Path(path).name
    handle = None
    try:
        handle = loc.element_handle(timeout=8000)
    except Exception:  # noqa: BLE001
        handle = None

    # 1. the control itself, or the input it belongs to
    if handle is not None:
        target = None
        try:
            found = handle.evaluate_handle(_FIND_FILE_INPUT_JS)
            target = found.as_element() if found else None
        except Exception:  # noqa: BLE001
            target = None
        if target is not None:
            try:
                target.set_input_files(path, timeout=15000)
                # By NAME, not by count: another step may have filled a different box earlier,
                # and "some file is present" would then pass while this one attached nothing.
                got = target.evaluate(
                    "e => (e.files && e.files.length) ? e.files[0].name : ''")
                if got == want:
                    return "set on its own file input"
            except Exception as exc:  # noqa: BLE001
                logger.info("could not set the file directly (%s); trying the other ways",
                            str(exc)[:120])

    # 2. the control opens the OS file chooser when clicked
    try:
        with page.expect_file_chooser(timeout=4000) as fc:
            loc.click(timeout=4000)
        fc.value.set_files(path)
        return "through the file chooser it opened"
    except Exception:  # noqa: BLE001
        pass

    # 3. a drop zone that only listens for drop events
    if handle is not None:
        try:
            data = base64.b64encode(pathlib.Path(path).read_bytes()).decode()
            mime = (mimetypes.guess_type(path)[0]
                    or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            # "ignored" means the page did not accept the drop - not a success.
            if handle.evaluate(_DROP_FILE_JS, [want, data, mime]) == "dropped":
                return "dropped onto it"
        except Exception:  # noqa: BLE001
            pass

    raise RuntimeError(
        f"nothing here would take {want}. It is not a file box, it does not open one when "
        "clicked, and it does not accept a dropped file. Record the step on the ERP's own file "
        "box or its Browse button.")


def _resolve_upload(value: str, uploads: dict | None) -> str | None:
    """Turn an upload step's value into a real path.

    The step stores WHICH document to attach, not a machine path - a script recorded on one
    server has to keep working on another. `uploads` maps the job's document names to where
    they actually landed. An absolute path is still honoured, for a fixed file the ERP always
    needs.
    """
    want = (value or "").strip()
    if not want:
        return None
    for key, path in (uploads or {}).items():
        if key.strip().lower() == want.lower() and pathlib.Path(path).exists():
            return str(path)
    for key, path in (uploads or {}).items():          # loose: "invoice" matches "Invoice.pdf"
        if want.lower() in key.strip().lower() and pathlib.Path(path).exists():
            return str(path)
    return str(want) if pathlib.Path(want).exists() else None


def _parse_date(value: str):
    """Read a date written any of the ways an ERP or a document might write it.

    Day-first is tried before month-first, matching the convention the rest of this product
    uses: 06/12/2026 is 6 December, never 12 June.
    """
    import datetime as _dt

    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in _DATE_PATTERNS:
        try:
            return _dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _set_date(page, scope, loc, value: str) -> bool:
    """Put a date into a field, whether it is a plain box or a pop-up calendar.

    Most ERP date fields accept typed text. A good many do not: ASP.NET Calendar, jQuery UI
    datepicker, Bootstrap datepicker and the React pickers all render a grid of day numbers
    and mark the input readonly, so typing silently does nothing. Try typing first, verify it
    stuck, and only then open the calendar and click the day.
    """
    want = _parse_date(value)
    # A calendar-only field is READONLY on purpose. Typing into it can never work, and every
    # attempt costs Playwright's full editability timeout - seven formats blocked for three
    # minutes before the calendar was even opened. Detect it and go straight to the grid.
    typeable = True
    try:
        typeable = not loc.first.evaluate(
            "el => el.readOnly === true || el.disabled === true || el.getAttribute('readonly') !== null"
        )
    except Exception:  # noqa: BLE001
        typeable = True

    # 1 - typing, in the field's own format if we can guess it, else a few common ones.
    #     Every call is given a short explicit timeout: the default is 30s per call.
    if typeable:
        for text in ([value] if want is None else
                     [value, want.strftime("%d/%m/%Y"), want.strftime("%Y-%m-%d"),
                      want.strftime("%d-%m-%Y"), want.strftime("%d.%m.%Y"), want.strftime("%d-%b-%Y")]):
            try:
                loc.click(timeout=2000)
                loc.fill("", timeout=1500)
                loc.type(text, delay=15, timeout=2500)
                loc.press("Escape", timeout=1500)   # close any calendar the click opened
                page.wait_for_timeout(150)
                got = (loc.input_value(timeout=1500) or "").strip()
                if got and (_parse_date(got) == want if want else got == text):
                    _settle_postback(page)
                    return True
            except Exception:  # noqa: BLE001
                continue
    if want is None:
        return False

    # 2 - a real calendar. Open it, walk to the right month, click the day.
    try:
        loc.click(timeout=4000)
        page.wait_for_timeout(400)
    except Exception:  # noqa: BLE001
        return False
    target_title = f"{_MONTHS[want.month - 1]} {want.year}"
    for _ in range(24):                  # up to two years of stepping, either direction
        title = ""
        for sel in (".ui-datepicker-title", ".datepicker-switch", ".calendar-title",
                    ".react-datepicker__current-month", "[class*=PickersCalendarHeader-label]",
                    ".ant-picker-header-view", "[class*=header] [class*=title]",
                    "caption", "th[colspan]"):
            try:
                node = page.locator(sel).first
                if node.count() and node.is_visible():
                    title = (node.inner_text(timeout=1500) or "").strip().lower()
                    break
            except Exception:  # noqa: BLE001
                continue
        if not title:
            break                        # no recognisable calendar - fall through to the day click
        if target_title.startswith(title.split()[0][:3]) and str(want.year) in title:
            break
        # which way to step
        try:
            cur_year = int("".join(c for c in title if c.isdigit()) or want.year)
        except ValueError:
            cur_year = want.year
        cur_month = next((i + 1 for i, m in enumerate(_MONTHS) if m[:3] in title), want.month)
        forward = (want.year, want.month) > (cur_year, cur_month)
        moved = False
        for sel in ((".ui-datepicker-next", ".react-datepicker__navigation--next",
                     ".ant-picker-header-next-btn", "[aria-label*='Next']", ".next",
                     "[class*=next]", "[title*=Next]")
                    if forward else
                    (".ui-datepicker-prev", ".react-datepicker__navigation--previous",
                     ".ant-picker-header-prev-btn", "[aria-label*='Previous']", ".prev",
                     "[class*=prev]", "[title*=Prev]")):
            try:
                nav = page.locator(sel).first
                if nav.count() and nav.is_visible():
                    nav.click(timeout=1500)
                    page.wait_for_timeout(250)
                    moved = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not moved:
            break
    # click the day number inside the open calendar
    day = re.compile(r"^\s*0?" + str(want.day) + r"\s*$")
    # A day cell is a <td> in jQuery UI and Bootstrap, a <div> in react-datepicker, and a
    # <button> in MUI. Looking only for <td> silently found nothing on a React app.
    for sel in (".ui-datepicker-calendar td a", ".datepicker td.day",
                ".react-datepicker__day", "[class*=PickersDay-root]", ".ant-picker-cell-inner",
                "[role=gridcell] button", "[role=gridcell]", "td a", "td"):
        try:
            cells = page.locator(sel).filter(has_text=day)
            for i in range(min(cells.count(), 6)):
                cell = cells.nth(i)
                cls = (cell.get_attribute("class") or "").lower()
                # A greyed-out day belonging to the neighbouring month. Every library spells
                # it differently: old/new (Bootstrap), outside-month (react-datepicker),
                # dayOutsideMonth (MUI), cell-disabled (antd).
                if any(k in cls for k in ("old", "new", "other", "outside", "disabled", "hidden")):
                    continue
                if cell.is_visible():
                    cell.click(timeout=2000)
                    _settle_postback(page)
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _click_in_row(page, scope, row_text: str, control_text: str) -> bool:
    """Find the table row containing `row_text` and click `control_text` inside it.

    A GridView's Edit / Update / Select links are identical on every row, so a plain selector
    hits row 1 every time. The row has to be located by its own content first.
    """
    needle = (row_text or "").strip()
    if not needle:
        return False
    # A grid row is a <tr> in a classic table, and a div with role="row" in AG Grid, MUI
    # DataGrid and most React tables. Searching only for <tr> finds nothing on those.
    for root in (scope, page):
      for row_sel in ("tr", "[role=row]", "[class*=-row]", "[class*=table-row]"):
        try:
            rows = root.locator(row_sel).filter(has_text=needle)
            count = min(rows.count(), 10)
        except Exception:  # noqa: BLE001
            continue
        for i in range(count):
            row = rows.nth(i)
            for sel in ("a", "button", "input[type=submit]", "input[type=button]", "[role=button]"):
                try:
                    ctrl = row.locator(sel).filter(
                        has_text=re.compile(re.escape((control_text or "").strip()), re.I)
                    ) if control_text else row.locator(sel)
                    if ctrl.count() and ctrl.first.is_visible():
                        ctrl.first.click(timeout=3000)
                        _settle_postback(page)
                        return True
                except Exception:  # noqa: BLE001
                    continue
    return False


# ASP.NET validators and the summary control render their message in place rather than as a
# dialog, so a run would sail past a rejected field without noticing.
_VALIDATION_SELECTORS = (
    "[id*=ValidationSummary]", ".validation-summary-errors", "[class*=validation-summary]",
    "span[style*='color:Red']", "span[style*='color: red']", ".field-validation-error",
    ".text-danger", "[class*=error-message]", "[role=alert]",
    # React and its component libraries
    ".invalid-feedback", "[class*=Mui-error]", "[class*=MuiFormHelperText-root][class*=error]",
    ".ant-form-item-explain-error", "[class*=errorText]", "[class*=errorMessage]",
    "[class*=Toastify__toast--error]", "[data-testid*=error]", "[aria-invalid=true] ~ *",
)


def _validation_errors(page) -> list[str]:
    """Visible validation messages on the page right now, newest markup first."""
    out: list[str] = []
    for sel in _VALIDATION_SELECTORS:
        try:
            nodes = page.locator(sel)
            for i in range(min(nodes.count(), 6)):
                node = nodes.nth(i)
                if not node.is_visible():
                    continue
                txt = (node.inner_text(timeout=1200) or "").strip()
                if txt and len(txt) < 400 and txt not in out:
                    out.append(txt)
        except Exception:  # noqa: BLE001
            continue
    return out


def _scroll_dy(step: dict) -> int:
    """Pixels a scroll step moves: negative up, positive down. Default one screen down."""
    raw = step.get("value")
    try:
        dy = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        dy = SCROLL_STEP_PX
    return dy or SCROLL_STEP_PX


# --- the complete action vocabulary -------------------------------------------------------
# Nothing else may appear in a saved step. ELEMENT_ACTIONS are executed by _perform_step,
# which is the ONE implementation shared by a real job run, the recorder replay and the
# operator step-through. CONTROL_ACTIONS move the browser or read from it rather than driving
# a recorded element, so the run loop handles them.
#
# These sets exist because `action` is a free-form string on a saved step: for a long time an
# action no path recognised fell through every branch, logged "ok", and changed nothing. A
# step that cannot be performed now raises - see the end of _perform_step.
ELEMENT_ACTIONS = frozenset({
    "autocomplete", "check", "clear", "click", "double_click", "fill", "hover", "pick_date",
    "radio", "row_action", "scroll", "select", "send_keys", "submit", "upload",
})
CONTROL_ACTIONS = frozenset({
    "ai_action", "assert_text", "dialog", "download", "get_text", "navigate", "switch_tab",
    "wait", "wait_for", "wait_gone",
})
ALL_ACTIONS = ELEMENT_ACTIONS | CONTROL_ACTIONS


# ---------------------------------------------------------------------------------------------
# THE SUCCESS SCREENSHOT. The proof an operator looks at, so it has to be the whole screen and
# it has to be readable.
#
# What was wrong with the one taken at the end of a run: `full_page=False`, which captures only
# the 1280x800 viewport, and a context with no device scale, so 1x. On a checklist or a filed
# bill of entry - long, scrollable, full of small print - that produced a soft crop of the top
# third and nothing below the fold.
#
# What this does instead:
#   full_page=True        the entire scrollable page, however long
#   scale 2              twice the pixels, so figures and stamps stay legible
#   PNG                  lossless; a JPEG would soften exactly the digits that matter
#
# No browser chrome to remove: Playwright screenshots the PAGE, so there is no address bar, no
# tabs, no window frame in it to begin with. What DOES need removing is anything this engine
# injected while driving the screen, which is why the marker attribute is stripped first.
# ---------------------------------------------------------------------------------------------

_SHOT_CLEAN_JS = r"""
() => {
  // Anything the engine marked while working. A leftover attribute is invisible, but a page
  // that styles [data-lp-upload] would show it, and the screenshot is evidence - it should
  // show the ERP's screen and nothing of ours.
  document.querySelectorAll('[data-lp-upload]').forEach(n => n.removeAttribute('data-lp-upload'));
  return true;
}
"""


def capture_success_shot(page, full_page: bool = True) -> str | None:
    """A base64 PNG of the whole page, at twice the pixel density. None if it cannot be taken.

    Never raises: a screenshot is evidence, not a step of the entry. A run that entered the data
    correctly must not be reported as failed because the picture could not be taken.
    """
    try:
        page.evaluate(_SHOT_CLEAN_JS)
    except Exception:  # noqa: BLE001
        pass
    try:
        # A tall page renders in one pass at scale 2; Playwright stitches it itself.
        png = page.screenshot(type="png", full_page=full_page, scale="device", animations="disabled")
        return base64.b64encode(png).decode()
    except Exception:  # noqa: BLE001
        logger.warning("could not take the success screenshot", exc_info=True)
        # Second try without full_page: an enormous page can exceed the image limit, and the
        # visible screen is worth more than nothing.
        try:
            png = page.screenshot(type="png", full_page=False)
            return base64.b64encode(png).decode()
        except Exception:  # noqa: BLE001
            return None


# How long to wait for a modal dimmer to lift before giving up on a covered control. An ERP
# raises one while it processes an upload or a save; they clear in a second or two when the
# work finishes, and never clear at all when a dialog is waiting for somebody.
# The launch flags for every browser this app opens, in ONE place so the recorder and a real
# job can never be started differently.
#
#   --no-sandbox             the app runs as root on the box; Chromium will not start sandboxed
#   --disable-dev-shm-usage  Chromium keeps its render surfaces in /dev/shm, which is only 64MB
#                            on many small VMs; when it fills, Chromium dies with "Target
#                            crashed". CHECKED on this server: /dev/shm is 3.9G and 1% used, so
#                            this was NOT the cause here - it is cheap insurance for the day the
#                            app is moved onto a smaller box, and nothing more. What memory
#                            pressure actually does to a run is worse than a clean crash: the
#                            browser stops ANSWERING, and a Playwright timeout is counted inside
#                            the browser, so a call given four seconds was measured taking 475,
#                            with the run's own budget check never coming round because the run
#                            never returns from the call. See IDLE_SESSION_SECONDS.
#   --disable-gpu            there is no GPU on a headless box
#   the three backgrounding flags: a headless tab that loses "focus" has its timers throttled,
#                            which stalls the very ERP postbacks a run is waiting on
BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]

# "Is it there right now?" - a glance, not a wait. NOT zero: Playwright reads timeout=0 as NO
# TIMEOUT, so a glance at something absent waits for ever. That is not a fast check with a
# surprising name; it is a hang, and it hung a test for ten minutes before it was spotted.
GLANCE_MS = 250

OVERLAY_WAIT_MS = 20000


def _blocker(page, loc) -> str | None:
    """What is lying on top of this element, if anything. Its id/class, or None.

    Playwright refuses to click through an overlay, and rightly - a click that lands on a
    dimmer is not the click that was recorded. But its message says only "Timeout 8000ms
    exceeded", which reads like the element was missing when it was there all along, visible
    and enabled, under a grey sheet.
    """
    try:
        return loc.evaluate(
            r"""el => {
                 const r = el.getBoundingClientRect();
                 if (!r.width || !r.height) return null;
                 const top = document.elementFromPoint(r.left + r.width / 2,
                                                       r.top + r.height / 2);
                 if (!top || top === el || el.contains(top) || top.contains(el)) return null;
                 return (top.id ? '#' + top.id : '') +
                        (top.className && typeof top.className === 'string'
                           ? '.' + top.className.trim().split(/\s+/).join('.') : '')
                        || top.tagName.toLowerCase();
               }""")
    except Exception:  # noqa: BLE001
        return None


def _wait_clickable(page, loc, timeout: int = OVERLAY_WAIT_MS) -> str | None:
    """Wait for whatever is covering this element to go away. Returns what stayed, or None.

    The ERP puts a `blueFilterDivLayer` over the whole screen while it takes an upload, so the
    Save button behind it cannot be pressed - Playwright retried nineteen times in eight
    seconds and reported a timeout, as if the button were missing. Waiting for the sheet to
    lift is what a person does, and it is nearly always a second or two.
    """
    import time as _t
    deadline = _t.monotonic() + timeout / 1000
    what = _blocker(page, loc)
    while what and _t.monotonic() < deadline:
        page.wait_for_timeout(250)
        what = _blocker(page, loc)
    return what


def _click_spot(loc, step: dict) -> dict:
    """Where in the element to click: the spot that was recorded, or nothing for the centre.

    Playwright clicks the CENTRE of an element. For a results row that is the middle column,
    which on a grid is often a link - so a double-click meant to open the row navigated to
    another site instead, however carefully the Super Admin aimed while recording.

    The recorded position is a fraction of the element's size, so it lands on the same column
    when the table is a different width. Converted to pixels here against the element as it is
    NOW, and dropped entirely if the element cannot be measured - clicking the centre is a
    reasonable fallback, guessing an offset into an unmeasurable box is not.
    """
    fx, fy = step.get("click_fx"), step.get("click_fy")
    if fx is None or fy is None:
        return {}
    try:
        box = loc.bounding_box(timeout=2000)
        if not box or not box.get("width") or not box.get("height"):
            return {}
        return {"position": {"x": float(fx) * box["width"],
                             "y": float(fy) * box["height"]}}
    except Exception:  # noqa: BLE001
        return {}


def _perform_step(page, step: dict, val: str) -> None:
    """Execute one element-bound step (element assumed present)."""
    action = step.get("action")
    sel = step.get("selector")
    frames = step.get("frames") or []
    # Scroll is the one step with no element behind it — resolve the locator after this,
    # because _scoped() on a null selector raises.
    if action == "scroll":
        _do_scroll(page, _scroll_dy(step))
        return
    # Pinned to one element: an ambiguous recorded selector must not blow up as a strict-mode
    # violation when _present() has already accepted it.
    raw = _scoped(page, frames, sel)
    matched, vis = _visible_index(raw)
    if matched and vis is None:
        # Every match is hidden. Clicking one waits out the whole click timeout and then
        # reports "Timeout exceeded", which says nothing useful. For a dropdown we can still
        # find the right control by the option it offers; otherwise say plainly what is wrong.
        alt = (_select_by_option(page, frames, val)
               if action in ("select", "autocomplete", "fill") else None)
        if alt:
            raw = _scoped(page, frames, alt)
        else:
            raise RuntimeError(
                f"{sel!r} matched {matched} element(s) but every one of them is hidden — "
                "the recorded path no longer points at the control on screen. Re-record this "
                "step, or use Insert here")
    loc = _one(raw)
    if action in ("click", "submit"):
        # A step that had to be revealed by hovering is pressed where it stands - moving the
        # pointer to it would close the very menu that just brought it back. See _press_held().
        if step.get("_hover_opener"):
            _press_held(page, step["_hover_opener"], loc)
        else:
            # Wait for any modal dimmer over this control to lift. Without this the click is
            # swallowed by the overlay and reported as a plain timeout, which reads as "the
            # button is missing" when it is on screen and merely covered.
            stuck = _wait_clickable(page, loc.first)
            if stuck:
                raise RuntimeError(
                    f"{step.get('description') or sel!r} is on screen but covered by {stuck} "
                    f"after {OVERLAY_WAIT_MS // 1000}s - the ERP still has something open over "
                    "it. Whatever that is has to be dealt with before this step can run.")
            loc.click(timeout=8000, **_click_spot(loc, step))
    elif action == "double_click":
        if step.get("_hover_opener"):
            _press_held(page, step["_hover_opener"], loc, dbl=True)
        else:
            loc.dblclick(timeout=8000, **_click_spot(loc, step))
    elif action == "fill":
        # Type per-key (not fill) so type-ahead widgets fire their keyup/AJAX and open the
        # suggestion list. If a list appears, commit the fuzzy-best from it (some "plain"
        # fields are really type-aheads that must be SELECTED, not just typed).
        try:
            loc.click(timeout=8000)
            loc.fill("")
        except Exception:  # noqa: BLE001
            pass
        loc.type(val, delay=25, timeout=15000)
        page.wait_for_timeout(600)
        scope = page
        for f in frames:
            scope = scope.frame_locator(f)
        _commit_typeahead(page, scope, loc, val, strict=True)
    elif action == "autocomplete":
        # Use the same component-agnostic chooser the JOB RUNNER uses. This branch used to
        # click the element and type into it, which works for a text field but does nothing at
        # all to a dropdown built from divs — react-select, a Bootstrap dropdown-toggle, an
        # Angular mat-select. The recorder's replay then reported "ok" with the value
        # unchanged, while the same step on a real run worked. Now both take the same path.
        scope = page
        for f in frames:
            scope = scope.frame_locator(f)
        ok, _how = _set_choice(page, scope, loc, val)
        if not ok:
            # Nothing committed. Leave the typed text in place: a plain text field recorded as
            # autocomplete still needs its value, and the value check that follows catches it
            # if the ERP rejected it.
            try:
                loc.click(timeout=15000)
                loc.fill("")
                loc.type(val, delay=35, timeout=15000)
                page.wait_for_timeout(600)
                _commit_typeahead(page, scope, loc, val)
            except Exception:  # noqa: BLE001
                pass
    elif action == "select":
        scope = page
        for f in frames:
            scope = scope.frame_locator(f)
        ok, _how = _set_choice(page, scope, loc, val)
        if not ok:
            _select_fuzzy(loc, val)      # last resort: the plain native path
    # ---- The rest used to fall off the end of this function and do NOTHING, while the step
    # ---- logged "ok". A job run has its own copy of these and works; the recorder's replay
    # ---- and the operator's step-through both come through here, so a recorded date, tick,
    # ---- keypress or upload silently did not happen. Same behaviour as a job run now.
    elif action == "hover":
        loc.hover(timeout=8000)
    elif action == "pick_date":
        scope = page
        for f in frames:
            scope = scope.frame_locator(f)
        if not _set_date(page, scope, loc, val):
            raise RuntimeError(f"could not set the date {val!r} — the field rejected typing "
                               "and no calendar was found")
    elif action == "check":
        # Explicit state, never a blind toggle: clicking a box that is already ticked silently
        # un-ticks it. No value means TICK, which is what "check" asks for.
        want = str(val or "true").strip().lower() not in ("false", "0", "no", "n", "off")
        loc.set_checked(want, timeout=8000)
    elif action == "radio":
        loc.check(timeout=8000)
    elif action == "send_keys":
        # A grid cell often only commits on Tab/Enter, and some ERPs save on Ctrl+S.
        loc.click(timeout=8000)
        for key in [k.strip() for k in (val or "").split("+++") if k.strip()]:
            loc.press(key, timeout=8000)
            page.wait_for_timeout(120)
    elif action == "clear":
        loc.click(timeout=8000)
        loc.fill("")
    elif action == "row_action":
        scope = page
        for f in frames:
            scope = scope.frame_locator(f)
        if not _click_in_row(page, scope, val, step.get("description") or ""):
            raise RuntimeError(f"no grid row containing {val!r}")
    elif action == "upload":
        # `uploads` only exists during a real job run — a replay has no job and therefore no
        # documents. Say so rather than reporting a step that attached nothing as "ok".
        path = _resolve_upload(val, step.get("_uploads"))
        if not path:
            raise RuntimeError(
                f"no file matching {val!r} — an upload step needs a job's documents, which a "
                "replay does not have. It will work on a real run")
        # Not a bare set_input_files with a page-wide fallback: that put the file in the
        # FIRST file box on the screen and reported success either way.
        how = _attach_file(page, loc, path)
        logger.info("attached %s (%s)", pathlib.Path(path).name, how)
        _settle_postback(page)
    elif action in CONTROL_ACTIONS:
        # Reached only if a caller routes a control action here by mistake. Louder than a
        # silent no-op, which is how eight dead actions went unnoticed for months.
        raise RuntimeError(
            f"{action!r} is a control action - the run loop handles it, not _perform_step")
    else:
        raise RuntimeError(
            f"unknown step action {action!r}. Known actions: "
            + ", ".join(sorted(ALL_ACTIONS)))


def _value_present(page, step: dict, val: str) -> bool:
    """Read the field back and check the expected value is actually in it. For a
    fill/type-ahead we compare the input's current value; for a select we compare the
    selected option's label/value. Loose (case-insensitive substring) so trimming/format
    differences from the ERP don't cause false negatives."""
    want = (val or "").strip().lower()
    if not want:
        return True
    action = step.get("action")
    try:
        loc = _scoped(page, step.get("frames") or [], step.get("selector")).first
    except Exception:  # noqa: BLE001
        return True  # can't read it — don't block the flow
    # Read the value back in a way that works for every kind of chooser, not just an <input>.
    # A styled ASP.NET dropdown is a div with no .value at all: asking for input_value() there
    # throws, the check reads as "empty", and a value that DID reach the ERP gets re-entered
    # three times and then fails the job.
    cur = ""
    try:
        cur = loc.evaluate(
            """el => {
                const sel = el.tagName === 'SELECT' ? el : el.querySelector && el.querySelector('select');
                if (sel) {
                    const o = sel.options && sel.options[sel.selectedIndex];
                    return o ? (o.label || o.textContent || o.value || '') : (sel.value || '');
                }
                if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') return el.value || '';
                // radio / checkbox group: the label of whatever is ticked inside it
                const box = el.querySelector && el.querySelector('input:checked');
                if (box) {
                    const lab = box.id && el.querySelector('label[for="' + box.id + '"]');
                    return (lab ? lab.textContent : box.value) || '';
                }
                // custom widget: it shows the chosen option as its own text
                return (el.innerText || el.textContent || '').trim();
            }"""
        )
    except Exception:  # noqa: BLE001
        try:
            cur = loc.input_value(timeout=3000)
        except Exception:  # noqa: BLE001
            return True
    cur = (cur or "").strip().lower()
    if not cur:
        return False
    if want in cur or cur in want:
        return True
    # Fuzzy: a dropdown was matched to the ERP's wording (e.g. "Registered" for
    # "REGISTERED TAXPAYER"), so the field text won't equal the extracted value exactly.
    import difflib

    return difflib.SequenceMatcher(None, want, cur).ratio() >= 0.6


def _element_disabled(page, selector, frames) -> bool:
    """Is the recorded target element present but disabled/read-only right now? A recorded
    EDITABLE field being disabled at replay time means the form stopped accepting input —
    the classic 'record already exists' (duplicate) signal."""
    if not selector:
        return False
    try:
        loc = _scoped(page, frames or [], selector).first
        return bool(loc.evaluate(
            "el => !!(el.disabled || el.readOnly || el.getAttribute('aria-disabled') === 'true')"
        ))
    except Exception:  # noqa: BLE001
        return False


def _detect_block(page) -> dict | None:
    """Generic duplicate/blocked detection (NO hardcoded field names or messages). If the
    form's visible fields nearly all became locked (disabled/read-only) after a value was
    entered, the entry can't be submitted — the AI reads the page to say WHY (usually a
    duplicate). Returns {'outcome', 'reason'} when blocked, else None."""
    try:
        st = page.evaluate(_FIELD_STATE_JS)
    except Exception:  # noqa: BLE001
        return None
    total = st.get("editable", 0)  # total visible entry fields
    locked = st.get("locked", 0)   # of those, how many are disabled/read-only
    # A single value locking (nearly) the whole form is the signal — regardless of field names.
    if total >= 3 and locked >= max(2, total * 0.7):
        try:
            from app.core.llm import ai_classify_outcome

            verdict = ai_classify_outcome(st.get("text", ""), {"editable": total, "locked": locked})
        except Exception:  # noqa: BLE001
            verdict = {}
        outcome = (verdict.get("outcome") or "blocked").lower()
        if outcome in ("ok", "none", "normal", "fine"):
            return None  # AI says the page is fine — not a duplicate/block
        return {
            "outcome": outcome,
            "reason": verdict.get("reason") or "The form locked after entering data — the entry can't be submitted.",
        }
    return None


class RecorderSession:
    def __init__(self, url: str, login: dict | None = None, headless: bool | None = None):
        self._q: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._error: str | None = None
        self.url = url
        self._login = login or {}
        self._ctx = None   # browser context, so tabs can be enumerated
        self._page = None  # the tab the recorder is currently driving
        # Alerts the ERP raised while recording. Playwright auto-DISMISSES any dialog with no
        # handler, so a confirm() the ERP shows was answered "cancel" invisibly - the recorder
        # saw nothing and the Super Admin never learned the flow has a prompt in it.
        self._dialogs: list[dict] = []
        # How many recorded steps have been replayed into this live browser so far. Manual
        # replay walks this cursor one step at a time so a half-finished draft can be picked
        # up exactly where it was left.
        self._replay_idx = 0
        # What the last TOUCH already pressed. Touching an element describes it AND presses it,
        # so the popup can show what happened - then recording the step pressed the very same
        # element a second time. Harmless on a plain button, wrong on anything that TOGGLES: the
        # ERP's Standard Documents menu opened on the first press and shut again on the second,
        # so the next touch landed on the table behind it and described that instead. One touch
        # is one press.
        self._pressed: dict | None = None
        self._headless = headless  # None = use global setting; True/False = force
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=75)
        if self._error:
            raise RuntimeError(self._error)
        if not self._ready.is_set():
            raise RuntimeError("Browser failed to start in time.")

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright

            sweep_stale_browser_tmp()  # reclaim dirs orphaned by killed runs
            with sync_playwright() as pw:
                from app.core.config import get_settings

                hl = self._headless if self._headless is not None else get_settings().browser_headless
                browser = pw.chromium.launch(
                    headless=hl,
                    args=["--disable-blink-features=AutomationControlled",
                          "--start-maximized", *BROWSER_ARGS],
                )
                ctx = browser.new_context(viewport=VIEWPORT, user_agent=_UA, ignore_https_errors=True)
                page = ctx.new_page()

                def _remember_dialog(d):
                    """Record the alert, then ACCEPT it so the page carries on.

                    Accepting matches what a person doing this by hand would do - and leaving
                    it open would freeze the page and the screenshot with it. The message is
                    surfaced to the recorder so a `dialog` step can be added deliberately.
                    """
                    try:
                        self._dialogs.append({"type": d.type, "message": (d.message or "")[:400]})
                    except Exception:  # noqa: BLE001
                        pass
                    # This handler is on the CONTEXT, so it sees every popup in every tab and
                    # used to accept them all - which meant a replayed `dialog` step recorded
                    # as "dismiss" could never dismiss: this fired first and accepted. Ask the
                    # armed answer instead. Nothing armed (i.e. plain recording) = accept.
                    accept, reply = _dialog_answer(getattr(d, "page", None))
                    try:
                        d.accept(reply) if accept else d.dismiss()
                    except Exception:  # noqa: BLE001
                        try:
                            d.dismiss()
                        except Exception:  # noqa: BLE001
                            pass

                ctx.on("dialog", _remember_dialog)   # every tab, not just the first
                # An ERP commonly opens the next screen in a NEW TAB when you submit. Hold the
                # context and the ACTIVE page on the instance so the recorder can enumerate the
                # tabs and switch between them — a single fixed `page` made every tab after the
                # first invisible, and the recording simply stopped following the flow.
                self._ctx = ctx
                self._page = page
                try:
                    page.goto(self.url, wait_until="domcontentloaded", timeout=45000)
                    _settle(page)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("initial goto failed: %s", exc)
                # Best-effort auto-login if credentials were supplied.
                if self._login.get("username"):
                    self._try_login(page, self._login.get("username", ""), self._login.get("password", ""))
                self._ready.set()
                while True:
                    item = self._q.get()
                    if item is None:
                        break
                    fn, holder = item
                    try:
                        # Resolved per command, not captured once — switch_tab moves it.
                        holder["result"] = fn(self._page)
                    except Exception as exc:  # noqa: BLE001
                        holder["error"] = str(exc)
                    holder["event"].set()
                ctx.close()
                browser.close()
        except Exception as exc:  # noqa: BLE001
            self._error = f"Browser engine error: {exc}"
            self._ready.set()

    @staticmethod
    def _try_login(page, username: str, password: str):
        """Heuristic auto-login: fill the first likely user + password field and submit."""
        try:
            for sel in ['input[name="username"]', 'input[name="email"]', 'input[type="email"]', 'input[type="text"]']:
                if page.locator(sel).count():
                    page.fill(sel, username)
                    break
            for sel in ['input[type="password"]', 'input[name="password"]']:
                if page.locator(sel).count():
                    page.fill(sel, password)
                    break
            for sel in ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Login")', 'button:has-text("Sign in")']:
                if page.locator(sel).count():
                    page.locator(sel).first.click()
                    page.wait_for_timeout(1500)
                    break
        except Exception:  # noqa: BLE001
            pass

    def list_tabs(self) -> list[dict]:
        """Every tab currently open in this browser, and which one the recorder is driving.

        There is NO cap on how many are reported — an ERP may open any number and the operator
        cannot know in advance. What is bounded is the work per tab: `page.url` is a cached
        property and free, but `page.title()` evaluates JavaScript in that tab, so it costs a
        round-trip and can block while a tab is mid-navigation. This list is polled every few
        seconds, so titles are read only for the first _TITLE_LIMIT tabs; past that the URL
        identifies the tab and the UI falls back to it. That keeps a 30-tab session responsive
        instead of stalling the recorder behind thirty JavaScript evaluations.

        Runs on the browser thread like every other operation — Playwright objects may only be
        touched from the thread that created them.
        """
        def fn(_page):
            out: list[dict] = []
            for index, pg in enumerate(self._ctx.pages if self._ctx else []):
                try:
                    title = ""
                    if index < _TITLE_LIMIT and not pg.is_closed():
                        try:
                            title = (pg.title() or "")[:80]
                        except Exception:  # noqa: BLE001 — mid-navigation; the URL still IDs it
                            title = ""
                    out.append({
                        "index": index,
                        "url": pg.url,
                        "title": title,
                        "active": pg is self._page,
                        "closed": pg.is_closed(),
                    })
                except Exception:  # noqa: BLE001 — a tab can close while we enumerate
                    out.append({"index": index, "url": "", "title": "(unavailable)",
                                "active": False, "closed": True})
            return out

        return self.submit(fn)

    def switch_tab(self, index: int) -> dict:
        """Make tab `index` the one the recorder drives, so clicks and screenshots follow it."""
        def fn(_page):
            pages = list(self._ctx.pages if self._ctx else [])
            if index < 0 or index >= len(pages):
                raise RuntimeError(f"Tab {index} does not exist — {len(pages)} tab(s) open.")
            target = pages[index]
            if target.is_closed():
                raise RuntimeError(f"Tab {index} has been closed.")
            self._page = target
            try:
                target.bring_to_front()
            except Exception:  # noqa: BLE001 — headless has no real focus; harmless
                pass
            _settle(target)
            return {"index": index, "url": target.url, "title": (target.title() or "")[:80]}

        return self.submit(fn)

    def submit(self, fn, timeout: float = 60):
        holder: dict = {"event": threading.Event()}
        self._q.put((fn, holder))
        if not holder["event"].wait(timeout):
            raise TimeoutError("Browser command timed out.")
        if "error" in holder:
            raise RuntimeError(holder["error"])
        return holder.get("result")

    @property
    def replay_index(self) -> int:
        """How many recorded steps have been replayed into this browser so far.

        Read-only, and public because the API has to know which step a "next" press will run
        so it can prepare that step's data and nothing else.
        """
        return int(self._replay_idx or 0)

    # ---- operations ----
    def screenshot_b64(self) -> str:
        png = self.submit(lambda p: p.screenshot(type="png", full_page=False))
        return base64.b64encode(png).decode()

    def active_viewport(self) -> dict:
        """Size of the tab being driven right now.

        A window opened by the ERP is very often smaller than the main one, and the UI converts
        a click on the screenshot into page coordinates using this. Reporting the session's
        original size for a popup skews every click on it, so it must come from the live page.
        """
        def fn(p):
            try:
                size = p.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
                if size and size.get("width") and size.get("height"):
                    return {"width": int(size["width"]), "height": int(size["height"])}
            except Exception:  # noqa: BLE001
                pass
            return dict(p.viewport_size or VIEWPORT)

        return self.submit(fn)

    @staticmethod
    def _with_listener_check(p, x: float, y: float, info: dict | None) -> dict | None:
        """Correct is_clickable using the DevTools protocol before the UI ever sees it.

        _ELEMENT_JS can only judge clickability from what the page exposes - the tag, a role, an
        inline onclick, cursor:pointer. A tab whose handler was attached with addEventListener
        shows none of those, so it was reported as plain text and the recorder offered to read a
        value out of it. Only asked when the page-side answer was NO, so the common case costs
        nothing.
        """
        if not info or info.get("is_clickable"):
            return info
        if info.get("is_input") or info.get("is_select"):
            return info      # a value field is not a click target, whatever listens on it
        if _has_click_listener(p, x, y):
            info["is_clickable"] = True
            info["click_selector"] = info.get("click_selector") or info.get("selector")
            # Bubbling does the rest: clicking this element reaches the ancestor that listens.
            info["listener_detected"] = True
        return info

    def element_at(self, x: float, y: float) -> dict | None:
        def _do(p):
            return self._with_listener_check(p, x, y, p.evaluate(_ELEMENT_JS, [x, y]))

        return self.submit(_do)

    def inspect_at(self, x: float, y: float) -> dict | None:
        """Focus (not type) the element under the point and return its rich descriptor.
        Used when the user touches an input so we can pop a value dialog + AI suggestion."""
        def _do(p):
            info = self._with_listener_check(p, x, y, p.evaluate(_ELEMENT_JS, [x, y]))
            # Click to focus inputs and to actually press buttons/links — but NOT native
            # <select>, since opening its popup breaks the screenshot and later detection.
            self._pressed = None
            if not (info and info.get("is_select")):
                try:
                    p.mouse.click(x, y)
                except Exception:  # noqa: BLE001
                    pass
                sel = (info or {}).get("click_selector") or (info or {}).get("selector")
                if sel:
                    self._pressed = {"selector": sel,
                                     "frames": list((info or {}).get("frames") or [])}
            p.wait_for_timeout(200)
            return info
        return self.submit(_do)

    def click_at(self, x: float, y: float) -> dict | None:
        self._pressed = None      # a different gesture; the touch's press no longer counts
        def _do(p):
            info = self._with_listener_check(p, x, y, p.evaluate(_ELEMENT_JS, [x, y]))
            p.mouse.click(x, y)
            _settle(p)  # a click may navigate — wait for the new page to load
            return info
        return self.submit(_do)

    def double_click_at(self, x: float, y: float) -> dict | None:
        """Double click the point, as ONE gesture, and return what was under it.

        Not inspect_at + double_click_selector: inspect_at clicks to focus before it reports,
        so that pair fires a single click first. On a row that navigates on a single click the
        double then lands on the next screen. element_at only READS, so this is one gesture.
        """
        # A different gesture, so "the touch already pressed this" no longer applies.
        self._pressed = None
        def _do(p):
            info = self._with_listener_check(p, x, y, p.evaluate(_ELEMENT_JS, [x, y]))
            p.mouse.dblclick(x, y)
            _settle(p)          # a grid row usually opens a screen
            _settle_postback(p)
            return info
        return self.submit(_do)

    def type_at(self, x: float, y: float, value: str) -> dict | None:
        self._pressed = None
        def _do(p):
            info = p.evaluate(_ELEMENT_JS, [x, y])
            # Physical focus + type works whether or not the field is inside an iframe.
            p.mouse.click(x, y)
            try:
                p.keyboard.press("Control+A")
                p.keyboard.press("Delete")
            except Exception:  # noqa: BLE001
                pass
            p.keyboard.type(value)
            p.wait_for_timeout(200)
            return info
        return self.submit(_do)

    def autocomplete_at(self, x: float, y: float, value: str) -> list[str]:
        """Type `value` into a type-ahead field (live) and return the suggestion list it
        shows, so the recorder can display the real options while the user types."""
        def _do(p):
            # A password box is not a dropdown. Typing into one and then scraping whatever
            # happens to be on screen produced a "Dropdown matches (8): 1 2 3 4..." list -
            # page numbers, offered as if they were options for the password.
            try:
                kind = p.evaluate(
                    "([x,y]) => { const e = document.elementFromPoint(x,y);"
                    " return e ? ((e.getAttribute && e.getAttribute('type')) || '').toLowerCase() : ''; }",
                    [x, y],
                )
                if kind in ("password", "hidden", "file", "checkbox", "radio", "submit", "button"):
                    return []
            except Exception:  # noqa: BLE001
                pass
            p.mouse.click(x, y)
            try:
                p.keyboard.press("Control+A")
                p.keyboard.press("Delete")
            except Exception:  # noqa: BLE001
                pass
            if value:
                p.keyboard.type(value, delay=35)  # per-key so the site's keyup handlers fire
            p.wait_for_timeout(800)  # let the dropdown populate
            try:
                return p.evaluate(_SUGGEST_JS, [x, y]) or []
            except Exception:  # noqa: BLE001
                return []
        return self.submit(_do)

    def apply_step(self, step: dict, values: dict | None = None,
                   uploads: dict | None = None) -> dict:
        """Resolve and perform ONE step on the screen as it stands.

        The recorder needs this so a step it has just built actually HAPPENS. An AI rule, or a
        data field mapped onto a dropdown, used to be saved and never applied - the popup said
        so in as many words, "don't type a value now" - which left the ERP box empty. An empty
        box means the ERP will not validate the form, dependent lookups never fire, and the
        steps after it cannot be recorded at all.

        Deliberately not a replay: a replay returns to the start URL first, which would throw
        away the screen the recording is standing on.
        """
        def _do(p):
            val = _resolve_val(step, values or {})
            frames = step.get("frames") or []
            if uploads:
                # An upload step needs a real file on disk. While recording that is a workbook
                # built from the template's sample data, so the ERP accepts the import and the
                # steps after it can be recorded.
                step["_uploads"] = uploads
            _settle(p)
            _settle_postback(p, frames=frames)
            # Was this exact click ALREADY made, by the touch that opened the popup? Then doing
            # it again is not "applying the step", it is a second press of the same control -
            # and a second press of a dropdown button, a checkbox or any other toggle undoes
            # the first. The touch is consumed here so only the FIRST recorded click is
            # skipped: pressing the same button twice on purpose still works.
            already = self._pressed
            self._pressed = None
            if (already
                    and (step.get("action") or "").strip().lower() == "click"
                    and step.get("selector") == already.get("selector")
                    and list(frames) == list(already.get("frames") or [])):
                logger.info("apply_step: %s was already pressed by the touch, not pressing "
                            "again", already.get("selector"))
                return val
            _perform_step(p, step, val)
            _settle(p)
            _settle_postback(p, frames=frames)
            return val

        try:
            # Answer the rule HERE, not inside _do: _do runs on the browser thread, where a
            # model call would freeze the live view and can make the screenshot poll time out.
            step = prefetch_ai([step], values or {})[0]
            val = self.submit(_do, timeout=180)
            return {"ok": True, "value": val}
        except Exception as exc:  # noqa: BLE001
            # The step is still worth keeping: the ERP may simply not accept the sample. Say
            # what went wrong rather than losing the recording.
            return {"ok": False, "value": "", "error": str(exc)[:300]}

    def scroll_by(self, dy: int) -> dict:
        """Scroll the live page. The recorder only ever sees one viewport of a screenshot, so
        without this everything below the fold is unreachable — unclickable and un-mappable
        on a long ERP entry form.

        Returns where the page ended up so the UI can say "at the bottom" rather than leaving
        the user pressing a button that has nothing left to do.
        """
        res = self.submit(lambda p: _do_scroll(p, dy))
        y, mx = int(res.get("y") or 0), int(res.get("max") or 0)
        return {
            "mode": res.get("mode") or "none",
            "y": y,
            "max": mx,
            "moved": int(res.get("moved") or 0),
            "at_top": y <= 0,
            "at_bottom": mx > 0 and y >= mx - 2,
            # Diagnostics: how many scrollable areas the page has and how many could still
            # move. When a button appears to do nothing these say whether we found the wrong
            # container or the page genuinely has none.
            "found": int(res.get("found") or 0),
            "movable": int(res.get("movable") or 0),
            "blocked": int(res.get("blocked") or 0),
            "via": res.get("via") or "js",
        }

    def dialogs(self, clear: bool = True) -> list[dict]:
        """Alerts the ERP raised since this was last asked."""
        out = list(self._dialogs)
        if clear:
            self._dialogs = []
        return out

    # ---- replaying a draft back into the live browser --------------------------------
    #
    # A recording is rarely finished in one sitting: the Super Admin gets part of the way
    # through an ERP, saves a draft and comes back later. Stopping the recorder closes the
    # browser, so reopening it lands on the login page with the whole draft still ahead.
    # Replaying puts the screen back where the draft ended so recording can carry on.
    #
    # Two ways to do it. AUTO runs the draft straight through. MANUAL walks it a step at a
    # time, which is what you want when a step is misbehaving and you need to watch the exact
    # point it goes wrong.

    def _goto_start(self, p, log: list) -> bool:
        """Put the browser back on the script's first page.

        Replaying from step 1 only works from the start screen. Without this, a second
        Replay — or any hand-navigation in between — leaves the browser deep in the ERP and
        step 1 fails looking for a login box that is no longer on screen.
        """
        try:
            p.goto(self.url, wait_until="domcontentloaded", timeout=45000)
            _settle(p)
            return True
        except Exception as exc:  # noqa: BLE001
            log.append(f"could not reload the start page: {str(exc)[:120]}")
            return False

    def _replay_span(self, p, steps: list, values: dict, start: int, end: int, log: list,
                     uploads: dict | None = None,
                     keep_going: bool = False) -> int:
        """Run steps[start:end] in the live page. Returns the new cursor — the number of steps
        now applied. Stops at the first failure: carrying on would compound the drift.

        Every element-bound step follows the same cycle, because an ERP screen is never ready
        the instant the previous action returns:

            1. let the previous action finish  — page load, and any ASP.NET postback
            2. WAIT for this step's element to actually appear (up to REPLAY_WAIT_MS)
            3. if it never appears, look it up by the text it was recorded with
            4. do it
            5. wait again for whatever it started — a navigation, a postback, an AJAX panel

        Without step 1 the click lands on a half-rendered page; without step 2 a menu that
        takes two seconds to draw is declared missing; without step 5 the next step runs
        against the old screen.
        """
        steps = steps or []
        end = min(end, len(steps))
        i = start
        # The page can CHANGE mid-replay: a switch_tab step moves the recorder to another tab
        # and everything after it belongs to that tab. Holding the original page would run the
        # rest of the script against the screen we just left.
        page = p
        while i < end:
            # Guarded: a caller may still hand in a plain list, and timing is a nicety,
            # never a reason for a replay to fail.
            if hasattr(log, "step_begins"):
                log.step_begins(i)
            st = steps[i]
            act = st.get("action") or ""
            frames = st.get("frames") or []
            try:
                if act == "navigate":
                    page.goto(st.get("value") or self.url, wait_until="domcontentloaded", timeout=45000)
                    _settle(page)
                elif act == "wait":
                    page.wait_for_timeout(int(float(st.get("value") or 1) * 1000))
                elif act in ("wait_for", "wait_gone"):
                    state = "visible" if act == "wait_for" else "hidden"
                    secs = float(st.get("timeout") or 15)
                    _scoped(page, frames, st.get("selector")).first.wait_for(
                        state=state, timeout=secs * 1000)
                elif act == "switch_tab":
                    # NOT skippable. This used to sit in the skip list below on the grounds
                    # that it "reads or branches" — but a tab switch is exactly how the flow
                    # REACHES the next screen. Skipping it left the recorder on the old tab
                    # and every following step failed looking for elements that were never
                    # there. The tab may also still be opening: the submit that spawns it can
                    # still be in flight, so wait for it rather than failing.
                    want = int(float(st.get("value") or 0))
                    want_url = st.get("description") or ""
                    found = None
                    for _ in range(20):                      # up to ~10s
                        opens = [q for q in (self._ctx.pages if self._ctx else [])
                                 if not q.is_closed()]
                        if want_url:
                            found = next((q for q in opens if q.url == want_url), None)
                        if found is None and 0 <= want < len(opens):
                            found = opens[want]
                        if found is not None:
                            break
                        page.wait_for_timeout(500)
                    if found is None:
                        raise RuntimeError(
                            f"tab {want + 1} never appeared — the ERP did not open it")
                    page = found
                    self._page = found       # clicks and screenshots follow it from here on
                    try:
                        page.bring_to_front()
                    except Exception:  # noqa: BLE001 — headless has no real focus
                        pass
                    _settle(page)
                    log.append(f"{i + 1}. switch_tab -> {page.url}")
                    i += 1
                    continue
                elif act == "dialog":
                    # Was missing: a dialog step fell through to _perform_step, which has no
                    # branch for it, so the popup went unanswered and Playwright dismissed it -
                    # silently cancelling whatever the next click submitted.
                    verb = _arm_dialog(page, st)
                    log.append(f"{i + 1}. dialog: will {verb} the next popup")
                    i += 1
                    continue
                elif act in ("get_text", "assert_text", "ai_action", "download",
                             "screenshot"):
                    # These only READ or branch — none of them moves the screen.
                    log.append(f"{i + 1}. {act} skipped (not needed to reach this screen)")
                    i += 1
                    continue
                else:
                    # 1. the previous step may still be loading or posting back. Settle inside
                    #    the frame this step targets: with the form in an iframe the top page
                    #    is idle immediately and the step would start mid-render.
                    # _settle_postback alone, NOT _settle then _settle_postback. On the top
                    # page the second is a superset of the first - both wait
                    # domcontentloaded, both wait networkidle, and only the second reads the
                    # WebForms postback flag - so calling both paid for the same answer twice
                    # at every settle point, four times per step.
                    _settle_postback(page, frames=frames)

                    use, note = st, ""
                    sel = st.get("selector")
                    if sel and act != "scroll":
                        # An optional step is for a screen that only shows up sometimes, so it
                        # gets a short look rather than the full wait — there is usually
                        # nothing to find, and 20s per run would be 20s wasted.
                        optional = bool(st.get("is_optional"))
                        wait = 3000 if optional else REPLAY_WAIT_MS
                        # 2. give the element real time to appear.
                        if optional and _absent_and_settled(page, sel, frames):
                            # An optional step that is not there is the ORDINARY case - the
                            # screen it covers simply did not come up. Skip it at once. It used
                            # to fall through the whole repair machinery first: search every
                            # frame, search by text, then hover the PREVIOUS step's element to
                            # re-open a menu. On this ERP that meant hovering the Save button,
                            # twice, on a page still posting back from the save - tens of
                            # seconds spent rescuing two steps that were never expected to be
                            # there.
                            log.append(f"{i + 1}. {act} skipped — optional, and "
                                       f"{(st.get('description') or sel)!r} is not on screen")
                            i += 1
                            continue
                        if not _present(page, sel, frames, timeout=wait):
                            # 3a. the SAME element, somewhere else on the page. This ERP loads
                            #     screen after screen into one window frame, so a control
                            #     recorded on the top page can be inside that frame on the next
                            #     run - the element is fine, only the chain to it changed. Only
                            #     reached after the recorded location has already failed, so a
                            #     normal run never pays for it.
                            moved = _elsewhere(page, sel, frames)
                            if moved is not None:
                                frames = moved
                                # `use` is what actually gets performed, and it carries its own
                                # frame chain. Updating only the local `frames` left the click
                                # aimed at the recorded chain, so the step still failed - just
                                # 8 seconds later, in the click rather than the wait.
                                use = {**st, "frames": moved}
                                where = "the top page" if not moved else " > ".join(moved)
                                note = f" — in {where}, not where it was recorded"
                            # 3b. still nothing — try the words instead. For a DOUBLE-CLICK the
                            #     row is what is wanted, never the link inside it: a grid row's
                            #     text is also its link's text, so the general text search
                            #     substituted an ICEGATE link for the job row and the run left
                            #     the ERP altogether.
                            alt = None
                            if moved is None and act == "double_click":
                                alt = _row_selector(page, frames,
                                                    st.get("description") or "")
                                if alt:
                                    note = (" — found by the reference in it, not by its "
                                            "position in the table")
                            if moved is None and not alt and act in ("click", "submit",
                                                                     "double_click"):
                                alt = _text_selector(page, frames, st.get("description") or "")
                            # 3c. the element is real but the MENU holding it has closed.
                            #     Hover whatever opened it and look again - see _reveal().
                            revealed = False
                            if (moved is None and not alt and i > 0
                                    and act in ("click", "submit", "double_click")):
                                revealed = _reveal(page, frames, steps[i - 1], sel)
                                if revealed:
                                    use = {**st, "_hover_opener": revealed}
                                    prev_desc = (steps[i - 1].get("description")
                                                 or steps[i - 1].get("selector") or "the step before")
                                    note = (" — its menu had closed; held it open by hovering "
                                            f"{prev_desc!r}")
                            if moved is None and not alt and not revealed:
                                if optional:
                                    log.append(
                                        f"{i + 1}. {act} skipped — optional, and "
                                        f"{(st.get('description') or sel)!r} is not on screen")
                                    i += 1
                                    continue
                                raise RuntimeError(
                                    f"{sel!r} never appeared after "
                                    f"{wait // 1000}s"
                                    + (f" (nor anything reading "
                                       f"{(st.get('description') or '')!r})"
                                       if st.get("description") else ""))
                            if alt:
                                use = {**st, "selector": alt}
                                note = (f" — found by its text "
                                        f"{(st.get('description') or '')!r}, the recorded "
                                        "position no longer matches")

                    # 4. do it.
                    val = _resolve_val(st, values or {})
                    if uploads:
                        use = dict(use, _uploads=uploads)
                    # Was the next step's element there BEFORE this click? See the note at 4b:
                    # chaining is for an element this click REVEALED, and that can only be told
                    # by looking first.
                    _peek = steps[i + 1] if (i + 1 < end and i + 1 < len(steps)) else None
                    _nxt_was_there = bool(
                        _peek and _peek.get("selector")
                        and _present(page, _peek["selector"], _peek.get("frames") or [],
                                     timeout=GLANCE_MS))
                    _perform_step(page, use, val)

                    # 4b. A FLOAT MENU shuts itself a moment after it opens. If this click just
                    #     revealed the next step's target, press it NOW - before any settling.
                    #     That wait is what closed the Standard Documents list: the item was
                    #     there when the click landed and gone by the time anything looked for
                    #     it, so the run reported "never appeared after 20s" for something that
                    #     really had appeared. Only the very next recorded step, only when it is
                    #     a click, and only when it is on screen this instant - so nothing is
                    #     ever run early or out of order.
                    # Only when this span actually covers the next step. Pressing Next runs a
                    # single step, and running two would leave the cursor saying one - the
                    # operator's next press would fire the menu item a second time.
                    nxt = steps[i + 1] if (i + 1 < end and i + 1 < len(steps)) else None
                    if (act in ("click", "submit") and nxt and not _nxt_was_there
                            and (nxt.get("action") or "") in ("click", "submit")
                            and nxt.get("selector")
                            and _present(page, nxt["selector"], nxt.get("frames") or [],
                                         timeout=600)):
                        _perform_step(page, nxt, _resolve_val(nxt, values or {}))
                        log.append(f"{i + 2}. {nxt.get('action')} ok — pressed straight after "
                                   f"step {i + 1}, before the menu could close")
                        # Advancing the cursor is what records it as done - _replay_span
                        # returns that, and the caller keeps it. Not every session type has a
                        # done-set to mark, so do not reach for one.
                        done = getattr(self, "_done_set", None)
                        if done is not None:
                            done.add(i + 1)
                        i += 1          # the outer loop advances past this one too

                    # 5. and wait for whatever it set off - in the step's own frame.
                    _settle_postback(page, frames=frames)

                    # 6. a typed value that did not STAY is worse than one that errored: the
                    #    log would say "ok" while the field sat empty, and every later step
                    #    would run against a form missing a value. Read it back, retry once,
                    #    then say so plainly. The value itself is never logged — it may be a
                    #    password.
                    if act == "fill" and val and not _value_present(page, use, val):
                        _perform_step(page, use, val)
                        _settle(page)
                        if not _value_present(page, use, val):
                            raise RuntimeError(
                                f"typed into {use.get('selector')!r} but the value did not "
                                "stay — the field may be read-only, a lookup that rejected "
                                "it, or the page cleared it")
                        note += " (took two tries — the field cleared itself once)"

                    log.append(f"{i + 1}. {act} ok{note}")
                    i += 1
                    continue
                _settle(page)
                log.append(f"{i + 1}. {act} ok")
            except Exception as exc:  # noqa: BLE001
                log.append(f"{i + 1}. {act} FAILED: {str(exc)[:170]}")
                if not keep_going:
                    return i      # this step did NOT apply, so the cursor stays before it
                # "Keep going" is for DIAGNOSING a draft: push past the broken step so one
                # pass reveals every problem, instead of one replay per problem. Deliberately
                # NOT how a real job runs — there, a missing element means the flow has left
                # the rails and carrying on would file an empty entry.
                log.append(f"{i + 1}. skipped and carrying on (keep going is on)")
            i += 1
        return end

    @staticmethod
    def _step_label(steps: list, idx: int) -> str:
        """Short human description of one step, for the recorder's status line."""
        steps = steps or []
        if idx < 0 or idx >= len(steps):
            return ""
        st = steps[idx] or {}
        bits = [str(st.get("action") or "?")]
        if st.get("field_label"):
            bits.append(f"[{st['field_label']}]")
        elif st.get("value"):
            bits.append(f"= {str(st['value'])[:30]}")
        if st.get("selector"):
            bits.append(str(st["selector"])[:40])
        return " ".join(bits)

    def _replay_result(self, steps: list, log: list) -> dict:
        total = len(steps or [])
        return {
            "log": log,
            "steps_run": len(log),
            "index": self._replay_idx,          # steps applied so far
            "total": total,
            "at_end": self._replay_idx >= total,
            "at_start": self._replay_idx <= 0,
            "done_label": self._step_label(steps, self._replay_idx - 1),
            "next_label": self._step_label(steps, self._replay_idx),
        }

    def replay(self, steps: list, values: dict | None = None,
               keep_going: bool = False, uploads: dict | None = None) -> dict:
        """AUTO — run the whole draft from the top, straight through."""
        steps = prefetch_ai(steps or [], values or {})

        def _do(p):
            log = _TimedLog()
            if not self._goto_start(p, log):
                return log
            self._replay_idx = self._replay_span(p, steps, values or {}, 0, len(steps or []),
                                                 log, uploads, keep_going)
            return log
        log = self.submit(_do, timeout=600)
        return self._replay_result(steps, log)

    def replay_next(self, steps: list, values: dict | None = None,
                    keep_going: bool = False, uploads: dict | None = None) -> dict:
        """MANUAL, forward — apply just the next recorded step."""
        total = len(steps or [])
        if self._replay_idx >= total:
            return self._replay_result(steps, ["already at the last recorded step"])
        # Only the ONE step about to run needs answering, so a manual walk never pays for
        # rules it has not reached.
        steps = list(steps or [])
        steps[self._replay_idx:self._replay_idx + 1] = prefetch_ai(
            steps[self._replay_idx:self._replay_idx + 1], values or {})

        def _do(p):
            log = _TimedLog()
            # Starting the walk: make sure step 1 runs on the start screen, wherever the
            # browser happens to have been left.
            if self._replay_idx == 0 and not self._goto_start(p, log):
                return log
            self._replay_idx = self._replay_span(
                p, steps, values or {}, self._replay_idx, self._replay_idx + 1, log,
                uploads, keep_going)
            return log
        log = self.submit(_do, timeout=300)
        return self._replay_result(steps, log)

    def replay_back(self, steps: list, values: dict | None = None,
                    uploads: dict | None = None) -> dict:
        """MANUAL, backward — step one back.

        A browser action cannot be undone: there is no way to un-type a value or un-click a
        button. So going back means starting over and re-running one step fewer, which lands
        on exactly the screen that preceded the step being undone.
        """
        if self._replay_idx <= 0:
            return self._replay_result(steps, ["already at the first step"])
        target = self._replay_idx - 1
        # Stepping back re-runs everything up to `target`, so those are the rules to answer.
        # Memoised, so pressing back repeatedly does not pay for them again.
        steps = list(steps or [])
        steps[:target] = prefetch_ai(steps[:target], values or {})

        def _do(p):
            log = _TimedLog([f"stepping back to step {target} — re-running from the start"])
            if not self._goto_start(p, log):
                return log
            self._replay_idx = self._replay_span(p, steps, values or {}, 0, target, log,
                                                 uploads)
            return log
        log = self.submit(_do, timeout=600)
        return self._replay_result(steps, log)

    def replay_seek(self, steps: list, index: int) -> dict:
        """Move the cursor WITHOUT touching the browser.

        For repairing a draft mid-flight. The ERP has grown a screen that did not exist when
        the script was recorded, so replay stops there. The Super Admin does that one action
        by hand — the browser is now one step further along than the cursor thinks — and the
        new step is spliced into the draft at that position. Only the count needs to catch up;
        re-running anything would undo the very thing they just did.
        """
        total = len(steps or [])
        try:
            want = int(index)
        except (TypeError, ValueError):
            want = self._replay_idx
        self._replay_idx = max(0, min(want, total))
        return self._replay_result(steps, [f"replay position set to {self._replay_idx}"])

    def replay_reset(self, steps: list) -> dict:
        """Start the walk over: cursor back to zero AND the browser back on the first page,
        so the next Next runs step 1 against the screen it was recorded on."""
        def _do(p):
            log = _TimedLog(["replay position reset to the beginning"])
            self._goto_start(p, log)
            return log
        log = self.submit(_do, timeout=120)
        self._replay_idx = 0
        return self._replay_result(steps, log)

    def list_events(self) -> list[dict]:
        """Every clickable element on the page (+iframes) — for 'Capture events'."""
        return self.submit(lambda p: p.evaluate(_EVENTS_JS)) or []

    def click_selector(self, selector: str, frames: list | None) -> dict:
        """Click a captured element by its selector/frame chain (records an event)."""
        # A different gesture, so "the touch already pressed this" no longer applies.
        self._pressed = None
        def _do(p):
            try:
                _scoped(p, frames, selector).click(timeout=8000)
                # A person is waiting on this one, so do not sit through a networkidle that
                # this ERP will never reach. A navigation is still caught by domcontentloaded
                # above, which keeps the full timeout.
                _settle(p, network_wait=400)
                return {"ok": True}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
        return self.submit(_do)

    def double_click_selector(self, selector: str, frames: list | None) -> dict:
        """Double-click a known element in the live browser, so the Super Admin sees the same
        thing happen that playback will do."""
        # A different gesture, so "the touch already pressed this" no longer applies.
        self._pressed = None
        def _do(p):
            try:
                _scoped(p, frames, selector).first.dblclick(timeout=8000)
                _settle(p, network_wait=400)
                return {"ok": True}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)[:200]}
        return self.submit(_do)

    def autocomplete_pick(self, selector: str, frames: list | None, value: str) -> dict:
        """Commit a chosen dropdown option in the live browser, whatever kind of control it is.

        Returns the action that ACTUALLY worked so the recorder saves the right step type -
        a styled div backed by a <select> must be recorded as a select, or playback will try
        to type into an element that cannot be typed into and fail the same way.
        """
        def _do(p):
            scope = p
            for f in frames or []:
                scope = scope.frame_locator(f)
            loc = scope.locator(selector)
            try:
                applied, action = _set_choice(p, scope, loc, value)
            except Exception:  # noqa: BLE001
                applied, action = False, "autocomplete"
            p.wait_for_timeout(250)
            return {"applied": applied, "action": action}
        return self.submit(_do)

    def select_value(self, selector: str, frames: list | None, value: str) -> dict:
        """Apply an option to a <select> using its KNOWN selector (from the earlier
        inspect) rather than re-detecting by coordinate — robust and never leaves the
        native dropdown hanging open."""
        def _do(p):
            loc = _scoped(p, frames, selector)
            applied = _select_fuzzy(loc, value)
            try:
                p.keyboard.press("Escape")  # close any lingering native popup
            except Exception:  # noqa: BLE001
                pass
            p.wait_for_timeout(300)
            return {"applied": applied}
        return self.submit(_do)

    def select_at(self, x: float, y: float, value: str) -> dict | None:
        def _do(p):
            info = p.evaluate(_ELEMENT_JS, [x, y])
            applied = False
            if info and info.get("selector"):
                loc = _scoped(p, info.get("frames"), info["selector"])
                # Try by visible label, then by value, then a loose text match against the
                # captured options — so "Registered" applies even if casing/spacing differs.
                for kwargs in ({"label": value}, {"value": value}):
                    try:
                        loc.select_option(timeout=4000, **kwargs)
                        applied = True
                        break
                    except Exception:  # noqa: BLE001
                        pass
                if not applied:
                    try:
                        opts = info.get("options") or []
                        match = next(
                            (o for o in opts if value.strip().lower() == o.strip().lower()), None
                        ) or next(
                            (o for o in opts if value.strip().lower() in o.strip().lower()), None
                        )
                        if match:
                            loc.select_option(label=match, timeout=4000)
                            applied = True
                    except Exception:  # noqa: BLE001
                        pass
            p.wait_for_timeout(300)
            if info is not None:
                info["applied"] = applied
            return info
        return self.submit(_do)

    # ---- stepped playback (Entry Browser: run one recorded step per "Next") ----
    # Real "components" (assigned elements) — everything that isn't a plain page load.
    _COMPONENT_ACTIONS = ("fill", "autocomplete", "select", "click", "submit", "ai_action")

    def load_steps(self, steps: list, values: dict, uploads: dict | None = None) -> None:
        """`uploads` was missing entirely, so an upload step in the step-by-step browser had
        no file to attach: the ERP's import popup opened on "No file chosen" and stayed open,
        and every step after it failed against a dimmer it could not get past. That was fixed
        for the streamed rerun and not here - the third copy of the same mistake."""
        self._steps = steps or []
        self._values = values or {}
        self._uploads = uploads or {}
        self._idx = 0
        self._done_components = 0
        self._pending_load = False
        self._last_blocked_idx = None
        self._done_set: set[int] = set()  # recorded-step indices already satisfied (by Next or by hand)
        # Every recorded step is one Next press. A click/submit can navigate, so it gets an
        # EXTRA "page load" press right after it — that load is its own visible step.
        self._total_components = len(self._steps) + sum(
            1 for s in self._steps if s.get("action") in ("click", "submit")
        )

    def _counters(self) -> dict:
        """Standard step counters returned by every stepped call."""
        steps = getattr(self, "_steps", [])
        total = getattr(self, "_total_components", 0)
        self._done_components = min(getattr(self, "_done_components", 0), total)
        return {
            "idx": self._done_components,
            "total": total,
            "done": self._idx >= len(steps) and not getattr(self, "_pending_load", False),
        }

    def _skip_done(self) -> None:
        """Advance the pointer past any recorded steps already satisfied by hand."""
        steps = getattr(self, "_steps", [])
        while self._idx < len(steps) and self._idx in self._done_set:
            self._idx += 1

    def note_manual(self, selector: str | None = None) -> dict:
        """The operator entered a component by hand in the panel. Match it to the recorded
        step it belongs to (by selector) and mark ONLY that step done — so the rest of the
        recorded sequence is preserved, not overwritten. If it matches no recorded step it's
        an extra manual action and consumes nothing."""
        def _do(p):
            steps = getattr(self, "_steps", [])
            done = self._done_set
            match_j = None
            if selector:
                for j, s in enumerate(steps):
                    if j in done:
                        continue
                    if s.get("selector") and s.get("selector") == selector:
                        match_j = j
                        break
                if match_j is None:  # loose fallback (frame-scoped selectors can differ slightly)
                    for j, s in enumerate(steps):
                        if j in done:
                            continue
                        sel = s.get("selector") or ""
                        if sel and (sel in selector or selector in sel):
                            match_j = j
                            break
            if match_j is not None:
                done.add(match_j)
                self._done_components += 1
                if steps[match_j].get("action") in ("click", "submit"):
                    self._done_components += 1  # account for its virtual page-load slot
                self._skip_done()
            self._last_blocked_idx = None
            return {"ok": True, "status": "manual", "matched": match_j is not None}
        res = self.submit(_do, timeout=30)
        res.update(self._counters())
        res["screenshot"] = self.screenshot_b64()
        return res

    def run_next(self) -> dict:
        """Run ONE step per press. A component (fill / select / type-ahead / click / submit)
        is one press. After a click/submit the FOLLOWING press is a dedicated 'page load' step
        that waits for the whole page to finish loading before you move on. navigate / wait are
        their own steps too. If a component's element isn't on screen, it stops so the operator
        can add it by hand."""
        steps = getattr(self, "_steps", [])
        values = getattr(self, "_values", {})
        # The operator's step-through shares the browser thread with the screenshot poll too.
        steps = prefetch_ai(steps, values)

        def _do(p):
            ran: list[str] = []
            # A click/submit last press triggered a navigation — THIS press is the page load.
            if getattr(self, "_pending_load", False):
                _settle(p)
                # Give a genuinely slow/blank page more time to paint.
                try:
                    p.wait_for_load_state("load", timeout=8000)
                except Exception:  # noqa: BLE001
                    p.wait_for_timeout(600)
                self._pending_load = False
                self._done_components += 1
                return {"ok": True, "status": "loaded", "action": "page_load",
                        "description": "Page loaded", "ran": ran}
            # Skip any steps the operator already satisfied by hand — don't redo them.
            self._skip_done()
            if self._idx >= len(steps):
                return {"ok": True, "status": "complete", "ran": ran}

            step = steps[self._idx]
            action = step.get("action")
            # A navigate / wait is its own page-load step.
            if action == "navigate":
                p.goto(step.get("value") or p.url, wait_until="domcontentloaded", timeout=45000)
                _settle(p)
                self._done_set.add(self._idx)
                self._idx += 1
                self._done_components += 1
                return {"ok": True, "status": "loaded", "action": "navigate",
                        "description": "Page loaded", "ran": ran}
            if action == "wait":
                p.wait_for_timeout(int(float(step.get("value") or 1) * 1000))
                self._done_set.add(self._idx)
                self._idx += 1
                self._done_components += 1
                return {"ok": True, "status": "loaded", "action": "wait",
                        "description": "Waited", "ran": ran}
            # Scroll has no element, so it must be handled before the _present() check below —
            # otherwise it looks like a missing element and stalls the operator's run.
            if action == "scroll":
                dy = _scroll_dy(step)
                _do_scroll(p, dy)
                self._done_set.add(self._idx)
                self._idx += 1
                self._done_components += 1
                return {"ok": True, "status": "loaded", "action": "scroll",
                        "description": f"Scrolled {'down' if dy > 0 else 'up'}", "ran": ran}
            if action == "switch_tab":
                # The ERP opened the next screen in another tab. Follow it, or every step
                # after this one looks for elements on the screen we just left.
                want = int(float(step.get("value") or 0))
                want_url = step.get("description") or ""
                found = None
                for _ in range(20):
                    opens = [q for q in (self._ctx.pages if self._ctx else [])
                             if not q.is_closed()]
                    found = _find_tab(opens, want, want_url)
                    if found is not None:
                        break
                    p.wait_for_timeout(500)
                if found is None:
                    return {"ok": False, "status": "error", "action": "switch_tab",
                            "note": f"tab {want + 1} never appeared", "ran": ran}
                self._page = found
                try:
                    found.bring_to_front()
                except Exception:  # noqa: BLE001
                    pass
                _settle(found)
                self._done_set.add(self._idx)
                self._idx += 1
                self._done_components += 1
                return {"ok": True, "status": "loaded", "action": "switch_tab",
                        "description": f"Moved to tab {want + 1}", "ran": ran}
            if action == "ai_action":
                self._done_set.add(self._idx)
                self._idx += 1
                self._done_components += 1
                return {"ok": True, "status": "skipped", "action": "ai_action",
                        "note": "AI step — use the auto rerun for AI takeover.", "ran": ran}
            if action == "dialog":
                # A native alert/confirm blocks the page until answered, and Playwright
                # dismisses by default - so a confirm-on-submit would silently CANCEL the
                # entry. Register the recorded answer before the click that triggers it.
                verb = _arm_dialog(p, step)
                self._done_set.add(self._idx)
                self._idx += 1
                self._done_components += 1
                return {"ok": True, "status": "ok", "action": "dialog",
                        "description": f"Will {verb} the next popup", "ran": ran}
            if action in ("wait_for", "wait_gone"):
                want_state = "visible" if action == "wait_for" else "hidden"
                try:
                    _one(_scoped(p, step.get("frames") or [], step.get("selector")))                         .wait_for(state=want_state, timeout=REPLAY_WAIT_MS)
                    note = f"{step.get('selector')!r} is now {want_state}"
                    okk = True
                except Exception:  # noqa: BLE001
                    note = (f"{step.get('selector')!r} did not become {want_state} in "
                            f"{REPLAY_WAIT_MS // 1000}s")
                    okk = False
                self._done_set.add(self._idx)
                self._idx += 1
                self._done_components += 1
                return {"ok": okk, "status": "ok" if okk else "warning", "action": action,
                        "description": note, "note": note, "ran": ran}
            if action in ("get_text", "assert_text", "download", "screenshot"):
                # These read the page rather than drive it. A real job run captures them;
                # stepping through by hand has nowhere to put the value, so move on - but say
                # so, rather than reporting a step that did nothing as done.
                self._done_set.add(self._idx)
                self._idx += 1
                self._done_components += 1
                return {"ok": True, "status": "skipped", "action": action,
                        "description": f"{action} is recorded for the automatic run",
                        "note": f"{action} only reads the screen - skipped in step-through",
                        "ran": ran}
            if action in CONTROL_ACTIONS:
                # Anything control-flow that reaches here is unhandled. Refuse loudly: a step
                # that silently does nothing is how eight dead actions went unnoticed.
                raise RuntimeError(
                    f"{action!r} has no handler in the step-through - report this")
            # One assigned element (component) = one Next. Do just this element's action.
            # Wait for the whole page to finish loading FIRST — the element can appear in
            # the DOM before the page is fully rendered; acting early causes the "shuttering".
            _settle(p)
            val = _resolve_val(step, values)
            desc = step.get("description") or step.get("field_label") or step.get("selector") or action
            if _present(p, step.get("selector"), step.get("frames") or [], timeout=8000):
                # If the recorded value field is present but DISABLED, the form has stopped
                # accepting input — usually because the record already exists → Duplicated.
                if action in ("fill", "autocomplete", "select") and _element_disabled(p, step.get("selector"), step.get("frames") or []):
                    blk = _detect_block(p) or {
                        "outcome": "duplicate",
                        "reason": "The form is locked (fields disabled) — the record already exists.",
                    }
                    return {"ok": False, "status": "duplicated", "action": action, "description": desc,
                            "value": val, "outcome": blk["outcome"], "reason": blk["reason"],
                            "note": f"Duplicated — {blk['reason']}", "ran": ran}
                if action in ("click", "submit"):
                    _perform_step(p, dict(step, _uploads=self._uploads), val)
                    _settle(p)
                    _settle_postback(p, frames=step.get("frames") or [])
                    # A click/submit may navigate — the NEXT press becomes the page-load step.
                    self._pending_load = True
                    self._done_set.add(self._idx)
                    self._idx += 1
                    self._done_components += 1
                    return {"ok": True, "status": "ok", "action": action, "description": desc,
                            "value": val, "verified": True, "ran": ran}
                # Field entry. Enter the value, then verify it STAYS. Some ERP fields are
                # lookups that silently clear a value they don't recognise — so we re-enter up
                # to 3 times. If it still won't hold, the ERP doesn't have this value → error.
                already = _value_present(p, step, val)
                verified = already
                attempts = 0
                while not verified and attempts < 3:
                    _perform_step(p, dict(step, _uploads=self._uploads), val)
                    _settle(p)
                    # let any blur/lookup validation clear it, in the step's own frame
                    _settle_postback(p, frames=step.get("frames") or [])
                    attempts += 1
                    verified = _value_present(p, step, val)
                if not verified:
                    # Value won't stay after 3 tries → surface an error. Press Next again to skip.
                    if getattr(self, "_last_blocked_idx", None) == self._idx:
                        self._last_blocked_idx = None
                        self._done_set.add(self._idx)
                        self._idx += 1
                        self._done_components += 1
                        return {"ok": True, "status": "skipped", "action": action, "description": desc,
                                "value": val, "note": f"Skipped '{desc}' — value '{val}' isn't accepted.", "ran": ran}
                    self._last_blocked_idx = self._idx
                    return {"ok": False, "status": "error", "action": action, "description": desc,
                            "value": val, "attempts": attempts,
                            "note": f"'{desc}': the value '{val}' won't stay after {attempts} tries — the ERP doesn't have this value. Press Next again to skip.",
                            "reason": f"Field '{desc}' rejected value '{val}' (not found / invalid) after {attempts} attempts.",
                            "ran": ran}
                self._done_set.add(self._idx)
                self._idx += 1
                self._done_components += 1
                self._last_blocked_idx = None
                # Duplicate/blocked detection: if entering this value locked (nearly) the
                # whole form, the entry can't be submitted → Duplicated.
                blk = _detect_block(p)
                if blk:
                    return {"ok": False, "status": "duplicated", "action": action, "description": desc,
                            "value": val, "outcome": blk["outcome"], "reason": blk["reason"],
                            "note": f"Duplicated — {blk['reason']}", "ran": ran}
                return {"ok": True, "status": "ok", "action": action, "description": desc,
                        "value": val, "verified": verified, "already": already, "attempts": attempts, "ran": ran}
            # Element not on screen. First time: stop so the operator can add it by hand.
            # If they press Next AGAIN on the same step, skip it and move on (don't get stuck).
            if getattr(self, "_last_blocked_idx", None) == self._idx:
                self._last_blocked_idx = None
                self._done_set.add(self._idx)
                self._idx += 1
                self._done_components += 1
                if action in ("click", "submit"):
                    self._pending_load = True
                return {"ok": True, "status": "skipped", "action": action, "description": desc,
                        "note": "Element not found — skipped. Enter it by hand if it's needed.", "ran": ran}
            self._last_blocked_idx = self._idx
            return {"ok": False, "status": "blocked", "action": action, "description": desc,
                    "note": "This field/button isn't on the screen yet. Add it by hand on the right, then press Next again to skip.", "ran": ran}

        res = self.submit(_do, timeout=120)
        res.update(self._counters())
        res["screenshot"] = self.screenshot_b64()
        return res

    def close(self):
        try:
            self._q.put(None)
        except Exception:  # noqa: BLE001
            pass


# How long a recorder / step-through session may go untouched before it is closed for us.
# The screen polls for a screenshot continuously while it is open, so "untouched" really does
# mean nobody is there: the tab was closed, the network dropped, or the browser was quit.
IDLE_SESSION_SECONDS = 15 * 60


class SessionManager:
    """Live recorder sessions, each holding a real Chromium with a real ERP page in it.

    A session used to be closed ONLY by someone pressing Stop. Close the tab, lose the network,
    refresh the page - and the session stayed in this dict for the life of the process, with its
    browser still running and still rendering. Every abandoned recording left one behind. They
    accumulate, the box starts swapping, and then the damage lands somewhere else entirely: a
    browser under memory pressure stops answering, and a Playwright timeout is counted INSIDE
    the browser - so a call given four seconds was measured taking 475, and a job that looked
    healthy sat there until its budget ran out. There is already a comment further down saying a
    left-open Chromium pegged this server's CPU at 100% once.

    So sessions are now reaped on idle. Nothing else about them changes.
    """

    def __init__(self):
        self._sessions: dict[str, RecorderSession] = {}
        self._touched: dict[str, float] = {}
        self._lock = threading.Lock()
        self._sweeper: threading.Thread | None = None

    def start(self, url: str, login: dict | None = None, headless: bool | None = None) -> str:
        sid = uuid.uuid4().hex
        session = RecorderSession(url, login, headless=headless)
        with self._lock:
            self._sessions[sid] = session
            self._touched[sid] = _time.monotonic()
        self._ensure_sweeper()
        return sid

    def get(self, sid: str) -> RecorderSession | None:
        s = self._sessions.get(sid)
        if s is not None:
            # Every poll for a screenshot comes through here, so this is a true liveness signal.
            self._touched[sid] = _time.monotonic()
        return s

    def stop(self, sid: str) -> None:
        with self._lock:
            s = self._sessions.pop(sid, None)
            self._touched.pop(sid, None)
        if s:
            s.close()

    def _ensure_sweeper(self) -> None:
        with self._lock:
            if self._sweeper is not None and self._sweeper.is_alive():
                return
            self._sweeper = threading.Thread(target=self._sweep, daemon=True,
                                             name="recorder-session-reaper")
            self._sweeper.start()

    def _sweep(self) -> None:
        while True:
            _time.sleep(60)
            cutoff = _time.monotonic() - IDLE_SESSION_SECONDS
            with self._lock:
                stale = [k for k, seen in self._touched.items() if seen < cutoff]
            for sid in stale:
                logger.warning(
                    "closing recorder session %s - nothing has touched it for %s minutes; "
                    "its browser was still running", sid, IDLE_SESSION_SECONDS // 60)
                try:
                    self.stop(sid)
                except Exception:  # noqa: BLE001 — a reaper must never die
                    logger.exception("could not close idle recorder session %s", sid)


manager = SessionManager()


# One step that never moves on. A step's own waits are ~20s, so this is well clear of anything
# legitimate; it exists for the screen that simply never arrives, where the run keeps repainting
# and looks busy while going nowhere.
STALL_SECONDS = 120


class PlaybackManager:
    """Runs an ERP replay in a background thread and streams its screenshots + log, so
    the Super Admin Entry Browser can watch a failed job's rerun live."""

    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, url: str, login: dict | None, steps: list, values: dict,
              # Was 75s - less than half what a real run gets, so the Entry Browser killed a
              # perfectly healthy script minutes before the job runner would have. Watching a
              # run must not be harder than running it.
              time_budget: int = 300, stall_after: int = STALL_SECONDS,
              rows: dict | None = None, uploads: dict | None = None,
              downloads_dir=None, on_done=None) -> str:
        sid = uuid.uuid4().hex
        state = {"screenshot": None, "log": [], "done": False, "status": "running",
                 "result": None, "cancel": False, "total": len(steps or []), "debug": None}
        with self._lock:
            self._sessions[sid] = state

        def _progress(shot, log, debug=None):
            state["screenshot"] = shot
            state["log"] = log
            if debug is not None:
                state["debug"] = debug

        def _run():
            try:
                # Live rerun: headless + streamed, and fail FAST (short budget, fewer AI
                # retries) so a stuck run returns quickly instead of hanging.
                res = play_steps(
                    url, login, steps, values,
                    progress=_progress, headless=True, time_budget=time_budget,
                    ai_attempts=2, cancel=lambda: state["cancel"], stall_after=stall_after,
                    # A replay without the job's files or its line-item rows is not a replay of
                    # that job: the upload step has nothing to attach and every product line
                    # gets the same value.
                    rows=rows, uploads=uploads, downloads_dir=downloads_dir,
                )
                state["result"] = {k: v for k, v in res.items() if k != "screenshot"}
                state["status"] = res.get("status", "ok")
                if res.get("screenshot"):
                    state["screenshot"] = res["screenshot"]
                state["log"] = res.get("log", state["log"])
            except Exception as exc:  # noqa: BLE001
                state["status"] = "error"
                state["result"] = {"status": "error", "error": str(exc)}
            finally:
                state["done"] = True
                # Tell the caller what happened, so the JOB can be written up. Without this the
                # Entry Browser drove a whole entry, watched it finish, and wrote nothing back:
                # a rerun could complete every step and render the ERP's checklist while the job
                # still read "failed" on the admin AND the operator screen, with no log and no
                # captured reference. In `finally`, because a run that ended badly is exactly
                # the one whose outcome must be recorded. Never allowed to break the run.
                if on_done is not None:
                    try:
                        on_done(state.get("result") or {"status": state.get("status")})
                    except Exception:  # noqa: BLE001
                        logger.exception("playback %s: could not record the outcome", sid)

        threading.Thread(target=_run, daemon=True, name=f"playback-{sid}").start()
        return sid

    def get(self, sid: str) -> dict | None:
        return self._sessions.get(sid)

    def stop(self, sid: str) -> bool:
        """Ask a running replay to stop. True if there was one to ask.

        Sets a flag the run checks BETWEEN steps rather than killing the thread: a browser
        torn down mid-action can leave the ERP with half a value typed into a field, and there
        is no way to tell afterwards which half went in. The run notices within one step,
        closes its own browser, and reports what it had done by then.
        """
        st = self._sessions.get(sid)
        if st is None or st.get("done"):
            return False
        st["cancel"] = True
        return True


playback_manager = PlaybackManager()


# The whole run's budget. This has to fit the SLOWEST honest script, not the average one:
# SOFTLINK(EXCEL) is thirty steps including an upload the ERP validates line by line and a Save
# that re-renders the page. A real run of it took 274 seconds and was killed at 180 having
# reached step 22 - so every attempt died on the clock and looked like a hang.
#
# A total that a healthy run cannot meet is not a safety net, it is a guaranteed failure. The
# thing that should stop a run is STALL_SECONDS - one step making no progress - because that
# is what "stuck" actually means. This is only the last resort behind it.
MAX_RUN_SECONDS = 600



# A checkpoint session is only worth reusing while the ERP still considers it logged in.
# Past this age we replay the login instead of jumping in with a cookie the ERP has expired.
CHECKPOINT_MAX_AGE = 1800  # 30 minutes


def load_checkpoint_session(path) -> tuple[dict | None, str | None]:
    """Read a saved checkpoint session: (playwright storage_state, url it was reached on).

    Returns (None, None) when there is nothing usable - no file, unreadable, or too old.
    Never raises: a bad session file must only cost us a login replay, not the run.
    """
    import json
    import pathlib
    import time as _t

    if not path:
        return None, None
    try:
        fp = pathlib.Path(path)
        if not fp.exists():
            return None, None
        blob = json.loads(fp.read_text(encoding="utf-8"))
        age = _t.time() - float(blob.get("saved_at") or 0)
        if age > CHECKPOINT_MAX_AGE:
            logger.info("checkpoint session is %.0fs old (> %ss) - ignoring it", age, CHECKPOINT_MAX_AGE)
            return None, None
        return blob.get("state") or None, blob.get("url") or None
    except Exception:  # noqa: BLE001
        logger.exception("could not read the checkpoint session at %s", path)
        return None, None


def save_checkpoint_session(path, ctx, url: str) -> bool:
    """Store the logged-in session reached at the checkpoint, plus the URL it was reached on.

    Cookies alone are not enough to resume: landing on the login URL with a live cookie
    usually redirects to a dashboard, not the entry screen the checkpoint was standing on.
    """
    import json
    import pathlib
    import time as _t

    if not path:
        return False
    try:
        fp = pathlib.Path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(
            json.dumps({"saved_at": _t.time(), "url": url, "state": ctx.storage_state()}),
            encoding="utf-8",
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception("could not save the checkpoint session to %s", path)
        return False


def discard_checkpoint_session(path) -> None:
    """Throw the saved session away - it no longer gets us to the entry screen."""
    import pathlib

    try:
        if path:
            pathlib.Path(path).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


import time as _time  # noqa: E402  (module-level: _TimedLog stamps outside play_steps)


class _TimedLog(list):
    """The run's log, with each step's own duration written onto its line.

    A log line says WHAT happened but not how long it took, so a script that walks through by
    hand in 44 seconds and burns 600 in production reads exactly the same way:

        step 20 upload ok
        step 21 click ok
        step 22 click ok
        ABORTED: stuck too long (> 600s)

    Four lines, no way to tell which of them ate nine minutes - the slow step has to be caught
    live, and a run that is already over cannot be. Every `step N ...` line now carries its own
    seconds, so the slow one names itself afterwards, from the log the operator already has.

    It is a plain list everywhere else: it serialises to JSON, extends and slices as before.
    """

    def __init__(self, *a):
        super().__init__(*a)
        self._at: int | None = None
        self._since = _time.monotonic()

    def step_begins(self, idx: int) -> None:
        """The run has moved onto step `idx`. Restarting on the SAME index is ignored, so a
        step that loops round to retry is timed from its first attempt, not its last."""
        if idx != self._at:
            self._at, self._since = idx, _time.monotonic()

    def append(self, line) -> None:  # noqa: D102
        text = str(line)
        if text.startswith("step ") or (text[:1].isdigit() and ". " in text[:5]):
            text = f"{text}  [{_time.monotonic() - self._since:.1f}s]"
        super().append(text)


def play_steps(
    url: str,
    login: dict | None,
    steps: list[dict],
    values: dict[str, str],
    progress=None,
    headless: bool | None = None,
    time_budget: int = MAX_RUN_SECONDS,
    ai_attempts: int = 4,
    rows: dict[str, list[str]] | None = None,
    downloads_dir=None,   # pathlib.Path — where captured documents are saved
    uploads: dict | None = None,   # {document name: path on disk} for upload steps
    checkpoint_index: int | None = None,  # step index marked "checkpoint" during recording
    session_file=None,    # pathlib.Path — where this script's checkpoint session is kept
    job_gate=None,        # JobGate — park at the checkpoint and wait for a job (see parked.py)
    cancel=None,          # callable() -> True when somebody has pressed Stop
    stall_after: int = STALL_SECONDS,   # give up on ONE step that never moves on
) -> dict:
    """Replay a saved step sequence. `values` maps field_label -> value; a step's value
    may be a literal or a [[field_label]] placeholder resolved from values. Returns status
    'ok' when the final ERP action ran, else 'failed'. `progress(screenshot_b64, log)` is
    called as the run proceeds (for the live Entry Browser). `headless` overrides the
    global setting (the Entry Browser streams screenshots, so it runs headless)."""
    import time as _time

    from playwright.sync_api import sync_playwright

    # A checkpoint index recorded before the steps were edited can now point past the end.
    # Treat that as no checkpoint rather than skipping the whole script.
    if checkpoint_index is not None and not (0 <= checkpoint_index < len(steps)):
        logger.warning('checkpoint_index %s is outside the %s recorded steps - ignoring it', checkpoint_index, len(steps))
        checkpoint_index = None

    log = _TimedLog()

    def page_url_safe() -> str:
        try:
            return _page_for_url[0].url if _page_for_url else ""
        except Exception:  # noqa: BLE001
            return ""

    _page_for_url: list = []
    start = _time.monotonic()
    executed: set[int] = set()  # indices of steps that actually ran
    # Steps that genuinely FAILED, as opposed to being legitimately skipped. Until now a step
    # could raise, get logged as FAILED, and the run would still report "ok" as long as the
    # final Submit went through - so a job completed with a blank date, an unattached document
    # or a missing captured value, and nobody was told. Optional steps never land here.
    hard_failures: list[str] = []
    # A screenshot step, if the script has one. Whatever it captures is what the operator sees
    stopped = False
    # Which step we have been sitting on, and since when - see the stall check in the loop.
    _stall_step: int | None = None
    _stall_since = _time.monotonic()

    # afterwards, in preference to whatever screen the run happened to finish on.
    success_shot: dict[str, str] = {}

    def diagnose(page, step: dict, idx: int) -> dict:
        """What the run is looking at, and what it is waiting for. Never raises.

        A run that sits on a step tells you nothing from the outside: the screenshot shows a
        page, and the log shows the last step that WORKED. Whether the element is missing, or
        present but invisible, or present and covered by a dialog, or the page simply has not
        finished loading - all four look identical from a screenshot, and each needs a
        different fix. This says which.
        """
        out: dict = {"step": idx + 1, "of": len(steps), "action": step.get("action"),
                     "recorded": {"selector": step.get("selector"),
                                  "description": step.get("description"),
                                  "frames": step.get("frames") or [],
                                  "optional": bool(step.get("is_optional")),
                                  "click_fx": step.get("click_fx"),
                                  "click_fy": step.get("click_fy")}}
        try:
            out["page"] = {
                "url": page.url,
                "ready_state": page.evaluate("() => document.readyState"),
                # An ASP.NET screen can be interactive and still be mid-postback; the flag is
                # the only honest answer to "has the ERP finished?"
                "posting_back": bool(page.evaluate(_POSTBACK_BUSY_JS)),
            }
        except Exception as exc:  # noqa: BLE001
            out["page"] = {"error": str(exc)[:120]}
        sel = step.get("selector")
        if sel:
            try:
                loc = _scoped(page, step.get("frames") or [], sel)
                n = loc.count()
                vis = None
                for k in range(min(n, 10)):
                    if loc.nth(k).is_visible():
                        vis = k
                        break
                out["element"] = {
                    "matches": n,
                    "visible": vis is not None,
                    "which_is_visible": vis,
                    # the thing lying on top, if any - a dimmer is why a visible button
                    # cannot be clicked
                    "covered_by": _blocker(page, loc.nth(vis)) if vis is not None else None,
                }
            except Exception as exc:  # noqa: BLE001
                out["element"] = {"error": str(exc)[:120]}
        return out

    def emit(page, step: dict | None = None, idx: int | None = None) -> None:
        if not progress:
            return
        try:
            shot = base64.b64encode(page.screenshot(type="png", full_page=False)).decode()
        except Exception:  # noqa: BLE001
            return
        dbg = diagnose(page, step, idx) if step is not None and idx is not None else None
        try:
            progress(shot, list(log), dbg)
        except TypeError:
            # a caller written before the diagnostic existed
            try:
                progress(shot, list(log))
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass

    def resolve(v: str | None) -> str:
        """Turn [[label]] into a real value: job data first, then anything PICKED this run.

        A value read out of the ERP is often needed again a few steps later in the SAME ERP —
        copy the reference it just generated into another screen's search box. So a picked
        label behaves exactly like a job data field for every step after it, and the recorder
        offers those labels alongside the template fields.
        """
        if not v:
            return ""
        if v.startswith("[[") and v.endswith("]]"):
            name = v[2:-2]
            if name in values:
                return values[name]
            item = (captured or {}).get(name)
            if isinstance(item, dict):
                return item.get("value") or ""
            return item or ""
        return v

    try:
        sweep_stale_browser_tmp()  # reclaim dirs orphaned by killed runs
        with sync_playwright() as pw:
            from app.core.config import get_settings

            hl = headless if headless is not None else get_settings().browser_headless
            browser = pw.chromium.launch(headless=hl, args=list(BROWSER_ARGS))
            # A script with a checkpoint may already have a logged-in session saved from its
            # last run. Reuse it and skip straight past the login/setup steps. No checkpoint
            # (or no saved session) => this is all None and the run behaves exactly as before.
            sess_state, sess_url = (None, None)
            if checkpoint_index is not None:
                sess_state, sess_url = load_checkpoint_session(session_file)
            ctx = browser.new_context(
                viewport=VIEWPORT, user_agent=_UA, ignore_https_errors=True,
                storage_state=sess_state or None,
                # Without this Playwright cancels every download, so a "pick a document"
                # step could never receive the file the ERP generated.
                accept_downloads=True,
                # Twice the pixels, for the success screenshot. That picture is what an
                # operator checks the entry against, and at 1x a bill of entry's small print
                # and figures are a guess. Set on the JOB context only - the recorder streams
                # a screenshot every second and does not need double the bytes.
                device_scale_factor=2,
            )
            page = ctx.new_page()

            # Answer popups from the FIRST moment, on every tab. Playwright dismisses a dialog
            # nobody handles, and a recorded `dialog` step sits AFTER the click that causes the
            # popup - because that is the order a person sees it happen. So on a real run the
            # answer was armed one step too late and the popup had already been dismissed:
            # "you are logged in from another portal, kill the other session?" got a silent No,
            # and the login stopped there. This listener consults the armed answer at the
            # moment the popup appears and accepts when nothing has been armed yet, which is
            # what a person clicking through would do and what the recorder already did.
            def _answer_dialog(d):
                accept, reply = _dialog_answer(getattr(d, "page", None))
                # Unlike the recorder's own _remember_dialog, this one used to answer the
                # popup and say nothing about it - so "Quotation is mandatory for job", the
                # ERP's own validation message, was accepted and thrown away with no trace.
                # The run then failed a few steps later for a reason that made no sense on
                # its own, because the one line that explained it never made it into the
                # log a failed run's summary is built from (see persist_run_outcome). A
                # dialog that fires but was never recorded as a `dialog` step is exactly the
                # case worth logging loudest - it means the ERP asked something the script
                # was not written to expect.
                try:
                    log.append(
                        f"ALERT: the ERP raised a {d.type} - {(d.message or '').strip()[:300]!r} "
                        f"- {'accepted' if accept else 'dismissed'}"
                    )
                except Exception:  # noqa: BLE001
                    pass
                try:
                    d.accept(reply) if accept else d.dismiss()
                except Exception:  # noqa: BLE001
                    try:
                        d.dismiss()
                    except Exception:  # noqa: BLE001
                        pass

            ctx.on("dialog", _answer_dialog)

            # A DEAD BROWSER must end the run, not be walked through step by step.
            # When Chromium dies its timeouts die with it: they are counted inside the browser,
            # so a wait_for given four seconds was measured blocking for 475. The run cannot be
            # rescued from inside such a call - but it must not go on to the next step and the
            # next, burning the whole budget on a page that no longer exists and finishing with
            # a log full of "ok". These say plainly what happened, and the loop checks them.
            dead: dict = {"why": None}

            def _mark_dead(why):
                def _on(*_a):
                    if not dead["why"]:
                        dead["why"] = why
                return _on

            page.on("crash", _mark_dead("the browser page crashed"))
            browser.on("disconnected", _mark_dead("the browser closed unexpectedly"))
            # Tabs opened later by the ERP need watching too - the entry screen is one.
            ctx.on("page", lambda q: q.on("crash", _mark_dead("a browser tab crashed")))
            from app.core.llm import ai_choose_action, ai_classify_outcome, resolve_ai_value

            def check_blocked() -> dict | None:
                """Duplicate/blocked detection, plus ASP.NET-style validation messages.

                A WebForms validator writes its complaint into the page rather than raising a
                dialog, so without this a run carries on past a field the ERP just rejected and
                only fails later, somewhere confusing."""
                blk = _detect_block(page)
                if blk:
                    return blk
                errs = _validation_errors(page)
                if errs:
                    return {"outcome": "validation",
                            "reason": "The ERP rejected the entry: " + " | ".join(errs[:3])}
                return None

            def ai_takeover(goal_text: str) -> str:
                """AI reads the current page's clickable elements and clicks the one that
                best moves the flow toward `goal_text`. Returns a log line."""
                try:
                    els = page.evaluate(_EVENTS_JS) or []
                except Exception:  # noqa: BLE001
                    els = []
                choice = ai_choose_action(goal_text, els, values)
                idx = choice.get("index")
                if idx is not None and 0 <= idx < len(els):
                    el = els[idx]
                    try:
                        _scoped(page, el.get("frames"), el["selector"]).click(timeout=8000)
                        _settle(page)
                        return f'AI clicked "{(el.get("text") or el.get("selector") or "")[:40]}" ({choice.get("reason", "")})'
                    except Exception as exc:  # noqa: BLE001
                        return f"AI picked an element but the click failed: {exc}"
                return f"AI found nothing to click ({choice.get('reason', '')})"

            page.goto(sess_url or url, wait_until="domcontentloaded", timeout=45000)
            _settle(page)
            emit(page)
            _page_for_url.append(page)
            resumed = bool(sess_state)
            if resumed:
                log.append(f"resumed the saved checkpoint session at {sess_url}")

            def present(sel_, frames_, timeout=3000) -> bool:
                """Is the recorded element visible on the current screen right now?"""
                if not sel_:
                    return False
                try:
                    _scoped(page, frames_, sel_).first.wait_for(state="visible", timeout=timeout)
                    return True
                except Exception:  # noqa: BLE001
                    return False

            def perform(step, val) -> None:
                """Run one element-bound step (element assumed present).

                Everything except the two capture actions is handed to _perform_step, so a real
                job run and the recorder's replay execute the SAME code. They used to be two
                near-identical copies of 120 lines, and every time they drifted a step quietly
                did nothing on one path while working on the other.
                """
                action = step.get("action")
                if action in ("get_text", "assert_text"):
                    # These two read the page rather than drive it, and get_text has to reach
                    # the per-run capture store, so they stay here.
                    loc = _one(_scoped(page, step.get("frames") or [], step.get("selector")))
                    got = _read_text(loc, step.get("capture_kind"))
                    if action == "assert_text":
                        if val and val.lower() not in got.lower():
                            raise AssertionError(
                                f"expected {val!r} on the page, found {got[:120]!r}")
                        return
                    _record_capture(step, got, i)
                    return
                # The uploads map is a per-run closure and _perform_step reads it off the
                # step, so it rides along on every step. No branch here on purpose: the moment
                # this function starts deciding things per action, it is a second copy again.
                _perform_step(page, dict(step, _uploads=uploads), val)

            def _record_capture(step: dict, text: str, index: int, file_name: str | None = None) -> str:
                """Store a picked item as a RECORD, not a bare string.

                The operator sees these on their completed screen, where a raw value with no
                label is meaningless — "1234567" could be a Bill of Entry number or a duty
                amount. The label, the kind and the Super Admin's description travel with the
                value so the completed screen can present it as something a human understands.
                """
                name = step.get("capture_as") or step.get("field_label") or f"value_{index + 1}"
                pattern = step.get("value")  # optional regex to pull the reference out
                if pattern and text:
                    try:
                        m = re.search(pattern, text)
                        if m:
                            text = (m.group(1) if m.groups() else m.group(0)).strip()
                    except re.error:
                        pass  # a bad regex must not lose the raw text
                captured[name] = {
                    "label": name,
                    "value": text,
                    "kind": step.get("capture_kind") or ("document" if file_name else "value"),
                    "description": step.get("capture_description") or "",
                    "file": file_name,
                    # Default to "both": a value picked without saying why is more useful shown
                    # than silently hidden from the operator.
                    "usage": step.get("capture_usage") or "both",
                }
                return text

            def capture_text(step: dict, index: int = 0) -> str:
                """get_text outside the element-bound path: reads the whole page when no
                selector was recorded, so 'find the BE number anywhere' still works."""
                sel = step.get("selector")
                text = ""
                try:
                    if sel:
                        text = _read_text(_scoped(page, step.get("frames") or [], sel).first,
                                          step.get("capture_kind"))
                    else:
                        text = (page.inner_text("body", timeout=6000) or "").strip()
                except Exception:  # noqa: BLE001
                    text = ""
                # _record_capture applies the regex, so the two paths cannot drift apart.
                return _record_capture(step, text, index)

            def capture_download(step: dict, index: int) -> str:
                """Pick a DOCUMENT out of the ERP: click the link and keep the file it returns.

                Customs portals hand back a generated PDF — the filed Bill of Entry, a challan.
                Playwright discards a download unless it is awaited at the moment the click
                happens, so this has to wrap the click rather than run after it.
                """
                sel = step.get("selector")
                if not sel or not downloads_dir:
                    return _record_capture(step, "", index)
                try:
                    with page.expect_download(timeout=60000) as dl:
                        _scoped(page, step.get("frames") or [], sel).first.click(timeout=8000)
                    download = dl.value
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", download.suggested_filename or "file")
                    target = downloads_dir / safe
                    download.save_as(str(target))
                    log.append(f"step {index + 1} download saved: {safe}")
                    return _record_capture(step, safe, index, file_name=safe)
                except Exception as exc:  # noqa: BLE001 — never fail the whole run over a file
                    log.append(f"step {index + 1} download FAILED: {exc}")
                    return _record_capture(step, "", index)

            # Resuming means the setup steps (login, navigation) are already done - the saved
            # session put us where they would have. Start at the first step AFTER the checkpoint.
            i = (checkpoint_index + 1) if resumed else 0
            safety = 0
            aborted = False
            checkpoint_saved = False
            gate_used = False        # the park has already been released for this run
            parked_released = False  # released with no job (idle refresh / cancel), not a failure
            job_ref = ""
            # Picked items, keyed by label: {label, value, kind, description, file}
            captured: dict[str, dict] = {}
            blocked: dict | None = None  # set if the form locks (duplicate/blocked entry)
            value_error: dict | None = None  # set if a field won't keep its value after 3 tries
            entered_fields: list[dict] = []  # every value field we filled — re-checked before submit

            def reverify_entered() -> dict | None:
                """Before committing (submit), re-check every value we entered is STILL there.
                Some ERP fields clear a value later — once you move to the next field. Re-enter
                any that vanished, up to 3 times. Returns the first field that stays empty
                (so the run fails: 'value not present in ERP'), or None if all are present."""
                for ef in entered_fields:
                    st, v = ef["step"], ef["val"]
                    if not v or _value_present(page, st, v):
                        continue
                    ok = False
                    for _ in range(3):
                        if present(st.get("selector"), st.get("frames") or [], timeout=2500):
                            try:
                                perform(st, v)
                            except Exception:  # noqa: BLE001
                                pass
                            page.wait_for_timeout(400)
                        if _value_present(page, st, v):
                            ok = True
                            break
                    if not ok:
                        return {"field": ef["intent"], "field_label": ef.get("field_label"), "value": v, "attempts": 3}
                return None

            def maybe_save_checkpoint() -> None:
                """Once the checkpoint step has run, keep the session it produced.

                Called at the top of each iteration, so `i` has already moved past the
                checkpoint by the time we save - i.e. the setup really did complete.
                """
                nonlocal checkpoint_saved
                if checkpoint_saved or resumed or checkpoint_index is None or session_file is None:
                    return
                if i > checkpoint_index:
                    checkpoint_saved = save_checkpoint_session(session_file, ctx, page.url)
                    log.append(
                        f"checkpoint reached at step {checkpoint_index + 1} - session saved"
                        if checkpoint_saved else
                        f"checkpoint reached at step {checkpoint_index + 1} - session could NOT be saved"
                    )

            if resumed:
                # The setup steps did not run, but they are SATISFIED — the saved session put us
                # exactly where they would have. Record them as executed, or the end-of-run checks
                # read a resumed run as one that skipped half its steps: it would name the login
                # steps as "failed steps" and spend a vision call diagnosing a perfectly good run.
                executed.update(range(0, checkpoint_index + 1))
                # The cookie may be live while the ERP has still logged us out, or the entry
                # screen may have moved. Probe the first recorded element after the checkpoint;
                # if it is not there, throw the session away and replay the whole script.
                probe = next((s for s in steps[i:] if s.get("selector")), None)
                if probe is not None and not present(probe.get("selector"), probe.get("frames") or [], timeout=8000):
                    log.append(
                        "the saved session no longer reaches the entry screen - discarding it "
                        "and replaying the script from step 1"
                    )
                    discard_checkpoint_session(session_file)
                    resumed = False
                    executed.clear()  # they really do have to run now
                    i = 0
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    _settle(page)
                    emit(page)

            while i < len(steps) and safety < len(steps) * 4 + 12:
                maybe_save_checkpoint()
                # ---- PARKED SESSION: wait at the checkpoint for a job to arrive ----------
                # This is what "stay open" means. The setup steps have just finished, so the
                # browser is logged in and sitting on the entry screen. Block here until the
                # manager hands us a job's values, then carry on with the entry steps. The
                # browser closes as normal at the end of the run, and the manager immediately
                # starts another run to park again — close, reopen, replay to here, wait.
                if job_gate is not None and not gate_used and checkpoint_index is not None and i > checkpoint_index:
                    gate_used = True
                    log.append(f"parked at the checkpoint (step {checkpoint_index + 1}) — waiting for a job")
                    emit(page)
                    payload = job_gate.wait()
                    if not payload:
                        # Park cancelled, or the idle refresh fired: give the browser back so
                        # a fresh one can log in again with a clean ERP session.
                        log.append("park released without a job — closing to re-park")
                        parked_released = True
                        break
                    values.update(payload.get("values") or {})
                    if payload.get("rows"):
                        rows = {**(rows or {}), **payload["rows"]}
                    job_ref = payload.get("job_ref") or ""
                    log.append(f"job {job_ref} received — running the entry steps")
                    # Time spent parked must not count against the run budget.
                    start = _time.monotonic()
                safety += 1
                if _time.monotonic() - start > time_budget:
                    log.append(f"ABORTED: stuck too long (> {time_budget}s) — returning failed")
                    aborted = True
                    break
                if dead["why"]:
                    log.append(f"ABORTED: {dead['why']} — returning failed. Every later step "
                               "would have run against a page that no longer exists.")
                    aborted = True
                    break
                # Somebody pressed Stop. Checked between steps rather than mid-action, so the
                # browser is closed at a clean point instead of half way through typing.
                if cancel is not None and cancel():
                    log.append(f"STOPPED by hand at step {i + 1}")
                    stopped = True
                    aborted = True
                    break
                # The run has not moved on from this step for a long time. The overall budget
                # does not catch this on its own: a script with many steps can sit on ONE of
                # them, still repainting, while the total stays under budget - which is how a
                # run appears to be working when it is really stuck on a screen that never
                # came. Fail on the step, and say which one, rather than time out with nothing
                # to point at.
                if i != _stall_step:
                    _stall_step, _stall_since = i, _time.monotonic()
                    log.step_begins(i)
                elif _time.monotonic() - _stall_since > stall_after:
                    what = steps[i].get("description") or steps[i].get("selector") or f"step {i + 1}"
                    log.append(f"ABORTED: step {i + 1} ({what}) made no progress for "
                               f"{stall_after}s — returning failed")
                    aborted = True
                    break
                emit(page, steps[i], i)  # the page AND what this step is waiting for
                step = steps[i]
                action = step.get("action")
                sel = step.get("selector")
                frames = step.get("frames") or []
                goal = step.get("goal")
                intent = goal or step.get("description") or step.get("field_label") or (f"click {sel}" if sel else "continue the flow")
                if step.get("prompt"):
                    val = resolve_ai_value(step["prompt"], values, step.get("options"))
                else:
                    val = resolve(step.get("value") or (f"[[{step['field_label']}]]" if step.get("field_label") else None))
                try:
                    if action == "navigate":
                        page.goto(step.get("value") or url, wait_until="domcontentloaded", timeout=45000)
                        _settle(page)
                        log.append(f"step {i + 1} navigate ok")
                        executed.add(i)
                        i += 1
                        continue
                    if action == "wait":
                        page.wait_for_timeout(int(float(step.get("value") or 1) * 1000))
                        log.append(f"step {i + 1} wait ok")
                        executed.add(i)
                        i += 1
                        continue
                    if action in ("wait_for", "wait_gone"):
                        # Explicit synchronisation. Previously the only option was a fixed
                        # `wait` in seconds, which is either too short on a slow ERP screen or
                        # wastes time on a fast one — and a spinner still overlaying the form
                        # made the next click land on the overlay.
                        want_state = "visible" if action == "wait_for" else "hidden"
                        secs = float(step.get("timeout") or 20)
                        try:
                            if sel:
                                _scoped(page, frames, sel).first.wait_for(
                                    state=want_state, timeout=secs * 1000
                                )
                            else:
                                # No selector: wait on TEXT appearing/disappearing instead.
                                needle = resolve(step.get("value")) or ""
                                deadline = _time.time() + secs
                                while _time.time() < deadline:
                                    body = page.inner_text("body", timeout=5000) or ""
                                    hit = needle.lower() in body.lower()
                                    if hit == (action == "wait_for"):
                                        break
                                    page.wait_for_timeout(400)
                                else:
                                    raise TimeoutError(f"{needle!r} never {want_state}")
                            log.append(f"step {i + 1} {action} ok")
                        except Exception as exc:  # noqa: BLE001
                            log.append(f"step {i + 1} {action} TIMED OUT after {secs}s: {exc}")
                            aborted = True
                            break
                        executed.add(i)
                        i += 1
                        continue
                    if action == "dialog":
                        verb = _arm_dialog(page, step)
                        log.append(f"step {i + 1} dialog: will {verb}")
                        executed.add(i)
                        i += 1
                        continue
                    if action == "screenshot":
                        # The Super Admin marked THIS screen as the one worth keeping. Taken
                        # here rather than at the end of the run because the end is often a
                        # list page the ERP bounced back to, not the confirmation itself.
                        #
                        # SETTLE FIRST. The step before this one is often what PRODUCES the
                        # screen worth keeping - here it is "Regenerate Checklist" - and the
                        # picture was taken half a second later, catching the report the
                        # viewer had rendered BEFORE the regeneration. That stale picture is
                        # not a cosmetic problem: it was read as evidence that a job's data had
                        # not gone into the ERP, when the entry screen behind it showed that it
                        # had. A report is fetched over HTTP, so waiting for the network to go
                        # quiet is the signal that it has actually been redrawn.
                        _settle(page, network_wait=5000)
                        _settle_postback(page)
                        got = capture_success_shot(page)
                        if got:
                            success_shot["png"] = got
                            success_shot["label"] = (step.get("description")
                                                     or step.get("value") or "Success screen")
                            log.append(f"step {i + 1} screenshot: captured "
                                       f"{success_shot['label']!r} ({len(got)//1024} KB)")
                        else:
                            log.append(f"step {i + 1} screenshot: could not be taken")
                        executed.add(i)
                        i += 1
                        continue
                    if action == "download":
                        capture_download(step, i)
                        executed.add(i)
                        i += 1
                        continue
                    if action == "get_text" and not sel:
                        got = capture_text(step, i)
                        name = step.get("capture_as") or step.get("field_label") or "captured"
                        log.append(f"step {i + 1} get_text {name} = {got[:80]!r}")
                        executed.add(i)
                        i += 1
                        continue
                    if action == "assert_text" and not sel:
                        # Whole-page assertion: the success banner's markup often differs run to
                        # run, so checking that the text appears ANYWHERE is the reliable form.
                        want = resolve(step.get("value")) or ""
                        try:
                            body = page.inner_text("body", timeout=6000) or ""
                        except Exception:  # noqa: BLE001
                            body = ""
                        if want and want.lower() not in body.lower():
                            log.append(
                                f"step {i + 1} assert_text FAILED: expected {want!r} "
                                f"but the page does not contain it"
                            )
                            value_error = {
                                "field": f"assert_text: {want}",
                                "field_label": step.get("field_label"),
                                "value": want,
                                "attempts": 1,
                            }
                            break
                        log.append(f"step {i + 1} assert_text ok ({want!r} present)")
                        executed.add(i)
                        i += 1
                        continue
                    if action == "scroll":
                        dy = _scroll_dy(step)
                        res = _do_scroll(page, dy)
                        where = "down" if dy > 0 else "up"
                        log.append(f"step {i + 1} scroll {where} {abs(dy)}px "
                                   f"({res.get('mode') or 'none'} @ {res.get('y', 0)})")
                        executed.add(i)
                        i += 1
                        continue
                    if action == "switch_tab":
                        # The ERP opens the next screen in a new tab, so the tab may not exist
                        # the instant we ask for it — the submit that spawns it is still in
                        # flight. Wait for it rather than failing the run. Prefer the recorded
                        # URL: tab order can differ between runs, but the destination does not.
                        want = int(step.get("value") or 0)
                        target_url = step.get("description") or ""
                        found = None
                        for _ in range(20):  # up to ~10s
                            pages = [p for p in ctx.pages if not p.is_closed()]
                            found = _find_tab(pages, want, target_url)
                            if found is not None:
                                break
                            page.wait_for_timeout(500)
                        if found is None:
                            log.append(f"step {i + 1} switch_tab: tab {want} never appeared — aborting")
                            aborted = True
                            break
                        page = found
                        try:
                            page.bring_to_front()
                        except Exception:  # noqa: BLE001
                            pass
                        _settle(page)
                        log.append(f"step {i + 1} switch_tab -> {page.url}")
                        executed.add(i)
                        i += 1
                        continue
                    if action == "ai_action":
                        msg = ai_takeover(goal or step.get("value") or "continue the flow")
                        log.append(f"step {i + 1} ai_action: {msg}")
                        _settle(page)
                        executed.add(i)
                        i += 1  # then continue the recorded script in order
                        continue
                    # A recorded menu path (`div:nth-of-type(9) > … > a:nth-of-type(2)`) breaks
                    # the moment the ERP renders a different number of widgets. Before handing
                    # the step to AI — slow, costs credits, and may click the wrong thing —
                    # try the element's recorded TEXT, which is deterministic and free.
                    # An OPTIONAL step that is not on screen is the ORDINARY case, and it is
                    # checked FIRST. Below it comes the repair machinery - search by text, then
                    # hover the PREVIOUS step's element to re-open a menu - and step 23 of this
                    # customer's script is optional with step 22 being SAVE. So an absent
                    # "Proceed" made the run hover the Save button on a page that had just
                    # saved, and a live job sat on step 23 for a minute and a half with the
                    # page complete, nothing posting back, and the element simply not there.
                    #
                    # The recorder's replay already skipped first; this path did not, and the
                    # two quietly disagreed. test_no_drift now checks the ordering on both.
                    if step.get("is_optional") and sel and _absent_and_settled(page, sel, frames):
                        note = _whats_open(page)
                        log.append(f"step {i + 1} {action} skipped — optional, and "
                                   f"{(step.get('description') or sel)!r} is not on screen"
                                   + note)
                        # PHOTOGRAPH IT. A skipped optional step is the one moment nobody can
                        # look at afterwards: the live frames are dropped minutes later and the
                        # success screenshot is taken twenty steps too late. Reading the ids off
                        # the page said a popup was open; it could not say what the popup was.
                        # ALWAYS, not only when words were found. The first version took the
                        # picture only if the page had readable text floating over it - which
                        # skipped it precisely in the case that turned out to matter, where
                        # nothing is open at all and the question is what IS there instead.
                        # A skipped optional step is the one moment nobody can look at later.
                        if downloads_dir:
                            try:
                                shot_dir = pathlib.Path(downloads_dir)
                                shot_dir.mkdir(parents=True, exist_ok=True)
                                png = shot_dir / f"step-{i + 1}-skipped.png"
                                png.write_bytes(page.screenshot(type="png", full_page=False))
                                log.append(f"        (what was on screen: {png.name})")
                            except Exception:  # noqa: BLE001 — never fail a run over a picture
                                logger.exception("could not photograph the skipped step")
                        executed.add(i)
                        i += 1
                        continue
                    # The element gets the SAME twenty seconds the recorder's replay gives
                    # it. It used to get four (`present(..., timeout=4000)` below), so a screen
                    # that takes six seconds to draw on a loaded server was declared missing and
                    # handed to the AI - which in production is a real model call with a
                    # screenshot, up to four of them, for an element that was about to appear on
                    # its own. The same script walked through by hand in 44 seconds and "got
                    # stuck" as a job. Only a step that is genuinely absent pays this: present()
                    # returns the moment the element is visible.
                    here = present(sel, frames, timeout=REPLAY_WAIT_MS) if sel else False
                    if sel and not here:
                        # The SAME element, somewhere else. This ERP loads screen after screen
                        # into one window frame, so a control recorded on the top page can be
                        # inside that frame on the next run - the element is fine, only the
                        # chain to it changed. The replay has always tried this FIRST, before
                        # searching by text; the entry did not do it at all, so a moved control
                        # went to the text search (which can match the wrong thing) or to the AI.
                        moved = _elsewhere(page, sel, frames)
                        if moved is not None:
                            frames = moved
                            step = {**step, "frames": moved}
                            where = " > ".join(moved) if moved else "the top page"
                            log.append(f"step {i + 1} {action}: found in {where}, not where it "
                                       "was recorded")
                            here = True
                    repaired = None if here else _repair_selector(page, step, frames)
                    if repaired:
                        log.append(f"step {i + 1} {action}: recorded position no longer matches; "
                                   f"using its text {(step.get('description') or '')!r}")
                        step = {**step, "selector": repaired}
                        sel = repaired
                    # Nothing wrong with the selector - the MENU holding it has closed. The
                    # Standard Documents list only exists while its button is hovered, so a
                    # recorded press on the button leaves nothing for the next step to find.
                    # Hover whatever opened it. Same rule as the recorder's replay.
                    elif (i > 0 and sel
                            and action in ("click", "submit", "double_click")
                            and not present(sel, frames, timeout=GLANCE_MS)
                            and (_held := _reveal(page, frames, steps[i - 1], sel))):
                        step = {**step, "_hover_opener": _held}
                        prev_desc = (steps[i - 1].get("description")
                                     or steps[i - 1].get("selector") or "the step before")
                        log.append(f"step {i + 1} {action}: its menu had closed; held it open "
                                   f"by hovering {prev_desc!r}")
                    # Element-bound step: run it with code if the element is on screen.
                    # `here` is the twenty-second wait above; only a step whose selector was
                    # REPAIRED since then needs asking again.
                    if here or present(sel, frames, timeout=4000):
                        # If the recorded value field is present but DISABLED, the form has
                        # stopped accepting input — usually because the record already exists.
                        # Detect it up front (don't waste the budget filling a disabled field).
                        if action in ("fill", "autocomplete", "select") and _element_disabled(page, sel, frames):
                            blocked = _detect_block(page) or {
                                "outcome": "duplicate",
                                "reason": "The form is locked (fields disabled) — the record already exists.",
                            }
                            log.append(f"step {i + 1} {action}: field disabled — {blocked['outcome']}: {blocked['reason']}")
                            break
                        # Before committing (submit), re-check EVERY value we already entered is
                        # still there — some ERP fields clear a value once you move past them.
                        # Re-enter vanished ones (3× each); if any stays empty → value not in ERP.
                        if action == "submit":
                            ve = reverify_entered()
                            if ve:
                                value_error = ve
                                log.append(f"pre-submit check: value '{ve['value']}' for '{ve['field']}' is not present in the ERP after 3 tries — failing before submit")
                                break
                        # A line-item field carries one value per invoice row, and an ERP form
                        # takes them one at a time: type a value, cross to the next input or
                        # click "add row", type the next. The Super Admin recorded that block
                        # once (row_steps) plus whatever closes the table (end_steps); replay it
                        # for however many rows THIS job has.
                        row_vals = (rows or {}).get(step.get("field_label") or "") or []
                        mode = step.get("multi_mode")
                        if mode == "per_row" and len(row_vals) > 1:
                            row_block = step.get("row_steps") or []

                            def row_value(rs_step, idx: int) -> str:
                                """The value a step inside the row block types for row `idx`.

                                SYNCHRONISED LOOP. An ERP product screen takes one line at a
                                time: description, HS code, quantity, unit, price, amount, then
                                Update. Every one of those comes from the SAME physical invoice
                                row, so they must advance together — row 3's description belongs
                                with row 3's quantity, never row 1's.

                                A step bound to another line-item field therefore takes that
                                field's value for THIS row. A step bound to an ordinary
                                single-value field — a CTH the operator supplied once for the
                                whole invoice — repeats unchanged on every row, which is exactly
                                what such a value means.
                                """
                                lab = rs_step.get("field_label")
                                if lab:
                                    vals = (rows or {}).get(lab)
                                    if vals is not None:
                                        return vals[idx] if idx < len(vals) else ""
                                    # Bound to an ordinary field: same [[label]] fallback the
                                    # main step path uses, or the step would type nothing at all.
                                    return resolve(rs_step.get("value") or f"[[{lab}]]")
                                return resolve(rs_step.get("value"))

                            # Warn once if a synchronised field carries a different number of
                            # rows than the field driving the loop — that means the two columns
                            # were read out of step, and the entry would silently misalign.
                            synced = [
                                (rs.get("field_label"), len((rows or {}).get(rs.get("field_label")) or []))
                                for rs in row_block
                                if rs.get("field_label") and (rows or {}).get(rs.get("field_label")) is not None
                            ]
                            for lab, n in synced:
                                if n != len(row_vals):
                                    log.append(
                                        f"step {i + 1}: '{lab}' has {n} row(s) but "
                                        f"'{step.get('field_label')}' has {len(row_vals)} — "
                                        "rows beyond the shorter list will be left blank"
                                    )
                            if synced:
                                log.append(
                                    f"step {i + 1}: {len(synced)} field(s) advance with this loop — "
                                    + ", ".join(lab for lab, _ in synced)
                                )

                            for rn, rv in enumerate(row_vals, start=1):
                                perform(step, rv)
                                _settle(page)
                                _settle_postback(page, frames=step.get("frames") or [])
                                for rs in row_block:
                                    # A scroll has no element to wait for — checking it would
                                    # read as "not on screen" and kill the whole row loop.
                                    if rs.get("action") != "scroll" and not present(
                                            rs.get("selector"), rs.get("frames") or [], timeout=4000):
                                        log.append(
                                            f"step {i + 1} row {rn}: '{rs.get('description') or rs.get('selector')}'"
                                            " not on screen — stopping the row loop here"
                                        )
                                        break
                                    perform(rs, row_value(rs, rn - 1))
                                    page.wait_for_timeout(200)
                            for es in step.get("end_steps") or []:
                                if es.get("action") == "scroll" or present(
                                        es.get("selector"), es.get("frames") or [], timeout=4000):
                                    perform(es, resolve(es.get("value")))
                                    page.wait_for_timeout(200)
                            log.append(
                                f"step {i + 1} {action}: entered {len(row_vals)} row(s) for "
                                f"'{step.get('field_label')}', {len(row_block)} action(s) per row"
                            )
                            executed.add(i)
                            i += 1
                            continue
                        if mode == "all_at_once" and row_vals:
                            val = (step.get("join_with") or ", ").join(v for v in row_vals if v)
                        # Was the next step's element ALREADY on screen before this click? Only
                        # an element this click brings into being is a menu item worth chasing.
                        # Without this, "is it on screen now" was true of anything permanent -
                        # and step 22 of this customer's script is SAVE, on the page the whole
                        # time. So Save was pressed two seconds after the Upload, before the ERP
                        # had finished loading the imported file, and the entry was saved
                        # without the new data. Looking first is the only way to tell the two
                        # apart.
                        _peek = steps[i + 1] if i + 1 < len(steps) else None
                        nxt_was_there = bool(
                            _peek and _peek.get("selector")
                            and present(_peek["selector"], _peek.get("frames") or [],
                                        timeout=GLANCE_MS))
                        perform(step, val)
                        # A FLOAT MENU shuts itself a moment after it opens. If this click just
                        # revealed the NEXT step's target, press it NOW - before any settling.
                        # The settle immediately below is exactly what closed the Standard
                        # Documents list: CheckList was on screen when the click landed and gone
                        # by the time the next step looked for it, so a job could never reach
                        # step 26 while the same script reached it by hand every time. The
                        # recorder's replay has done this since the menu work; the entry never
                        # did, and that is the whole difference.
                        chained = False
                        nxt = steps[i + 1] if i + 1 < len(steps) else None
                        if (action in ("click", "submit") and nxt and not nxt_was_there
                                and (nxt.get("action") or "") in ("click", "submit")
                                and nxt.get("selector")
                                and present(nxt["selector"], nxt.get("frames") or [],
                                            timeout=600)):
                            perform(nxt, resolve(nxt.get("value")))
                            log.append(f"step {i + 2} {nxt.get('action')} ok — pressed straight "
                                       f"after step {i + 1}, before the menu could close")
                            executed.add(i + 1)
                            chained = True
                        # Wait for the real condition rather than a fixed 300ms: with the form
                        # in an iframe the top page is idle at once, so a blind sleep let the
                        # next step start mid-render and land on a node the postback replaced.
                        _settle(page)
                        _settle_postback(page, frames=step.get("frames") or [])
                        # Value fields: verify the value STAYS (lookups clear unrecognised
                        # values). Re-enter up to 3 times; if it still won't hold → error.
                        if action in ("fill", "autocomplete", "select"):
                            attempts = 0
                            while not _value_present(page, step, val) and attempts < 3:
                                perform(step, val)
                                _settle(page)
                                _settle_postback(page, frames=step.get("frames") or [])
                                attempts += 1
                            if not _value_present(page, step, val):
                                value_error = {
                                    "field": intent,
                                    "field_label": step.get("field_label"),  # operator data field
                                    "value": val,
                                    "attempts": max(attempts, 1),
                                }
                                log.append(f"step {i + 1} {action}: value '{val}' won't stay after {max(attempts, 1)} tries — ERP doesn't have this value")
                                break
                            # Value held — remember it so we can re-verify before submit.
                            entered_fields.append({"step": step, "val": val, "field_label": step.get("field_label"), "intent": intent})
                        log.append(f"step {i + 1} {action} ok")
                        executed.add(i)
                        # The chained step above was really performed, so the cursor must pass
                        # it - otherwise the menu item gets pressed a second time.
                        if chained:
                            i += 1
                        if action in ("fill", "select", "autocomplete", "submit"):
                            blocked = check_blocked()
                            if blocked:
                                log.append(f"Form locked after step {i + 1} — {blocked['outcome']}: {blocked['reason']}")
                                break
                        i += 1
                        continue
                    # ...otherwise something NEW/unexpected is on screen. Let AI drive
                    # through the unexpected screen(s) — it may take SEVERAL clicks — until
                    # the recorded step (the break point) reappears, then hand control back
                    # to the recorded ERP script and continue in order from there.
                    resumed = False
                    for attempt in range(ai_attempts):
                        msg = ai_takeover(
                            f"An unexpected screen/dialog is blocking the recorded flow (e.g. an "
                            f"'already logged in, continue?' prompt, a cookie/confirmation dialog, a "
                            f"notice, or an error). Click ONLY what moves past it toward continuing the "
                            f"flow (continue/yes/ok/proceed/confirm/close). Context: {intent}."
                        )
                        log.append(f"step {i + 1} {action}: unexpected screen -> AI ({attempt + 1}): {msg}")
                        _settle(page)
                        # Did the recorded step (the break point) come back? If so, resume it.
                        if present(sel, frames, timeout=5000):
                            perform(step, val)
                            _settle(page)
                            _settle_postback(page, frames=step.get("frames") or [])
                            log.append(f"step {i + 1} {action} ok (recorded script resumed at break point)")
                            executed.add(i)
                            if action in ("fill", "select", "autocomplete", "submit"):
                                blocked = check_blocked()
                            i += 1
                            resumed = True
                            break
                        # If AI had nothing left to click, stop looping.
                        if not msg.startswith("AI clicked"):
                            break
                    if resumed:
                        continue
                    # The break-point element never reappeared, so this step isn't part of
                    # this run (e.g. the login form when a session already exists). Skip ONLY
                    # this step and continue the recorded sequence in order.
                    log.append(f"step {i + 1} {action}: not on this screen — skipped, continuing recorded sequence")
                    # Skipping a click in a branchy flow is normal. Skipping a CAPTURE is not:
                    # the output field it feeds just silently disappears from the job record.
                    if action in ("get_text", "download") and not step.get("is_optional"):
                        name = step.get("capture_as") or step.get("field_label") or sel
                        hard_failures.append(
                            f"step {i + 1} ({action}): nothing to read for {name!r}, "
                            "so that output was not captured")
                    i += 1
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.append(f"step {i + 1} {action} FAILED: {exc}")
                    if not step.get("is_optional"):
                        what = (step.get("description") or step.get("field_label")
                                or step.get("selector") or action)
                        hard_failures.append(f"step {i + 1} ({action}, {what}): {exc}")
                    i += 1
                emit(page)
            emit(page)
            final_url = page.url
            # The whole page, not the top 800 pixels of it. A filed bill of entry or a
            # checklist runs well past one screen, and the part an operator needs to read is
            # as often below the fold as above it.
            shot = success_shot.get("png") or capture_success_shot(page)
            # KEEP the picture, don't just hand it back. The screenshot was returned in the
            # Submit response and nowhere else: the operator saw it once, and the moment the
            # page was reloaded - or the job opened later, or opened by anybody else - the
            # completed screen showed the picked TEXT and no picture at all. The whole point of
            # recording a "success screenshot" step is that somebody can look at what the ERP
            # ended up showing.
            #
            # Written as a PNG next to the job's other captured documents, NOT as base64 in the
            # jobs table - a screenshot per run per job would bloat it, which is why it was
            # never stored in the first place. It goes into `captured` the same way a captured
            # document does, so the existing download route serves it and the existing screen
            # renders it, with no new column and no migration.
            if shot and downloads_dir:
                try:
                    downloads_dir.mkdir(parents=True, exist_ok=True)
                    png = downloads_dir / "erp-success.png"
                    png.write_bytes(base64.b64decode(shot))
                    captured["erp_success_screenshot"] = {
                        "label": success_shot.get("label") or "The ERP screen at the end",
                        "value": "",
                        "kind": "image",
                        "description": "What the ERP was showing when the entry finished.",
                        "file": png.name,
                        "usage": "both",
                    }
                except Exception:  # noqa: BLE001 — a picture must never fail a run
                    logger.exception("could not save the success screenshot")
            # The run stopped somewhere unexpected. The log only knows which selector was
            # missing, which tells an operator nothing — the screen itself knows whether the
            # session expired, a validation message appeared, or the ERP is down. Read it
            # BEFORE closing the browser, and only when something actually went wrong.
            # Did every element-bound step actually run? Computed here (the same expression is
            # used further down for the failure reason) so the diagnosis can be taken while the
            # browser is still open.
            _element_steps = [
                k for k, s in enumerate(steps)
                if s.get("action") in ("click", "submit", "fill", "select", "autocomplete")
            ]
            _incomplete = bool([k for k in _element_steps if k not in executed])
            if parked_released:
                # No job ever arrived. Nothing was entered, nothing failed: the browser was
                # only ever holding a place. Never diagnose or report this as a failed entry.
                if not get_settings().browser_headless:
                    page.wait_for_timeout(500)
                ctx.close()
                browser.close()
                return {"status": "parked_released", "final_url": page_url_safe(), "log": log,
                        "resumed": resumed, "checkpoint_saved": checkpoint_saved,
                        "reason": "The parked browser was released before a job arrived."}

            if not aborted and blocked is None and value_error is None:
                # The checkpoint can be the final recorded step; the loop-top hook never fires
                # for it because `i` never gets past it inside the loop.
                i = len(steps)
                maybe_save_checkpoint()
            ai_diagnosis = ""
            if shot and (value_error or blocked or aborted or _incomplete):
                try:
                    from app.core.llm import describe_failure_screen

                    ai_diagnosis = describe_failure_screen(shot, log)
                    if ai_diagnosis:
                        log.append(f"AI read the final screen: {ai_diagnosis}")
                except Exception:  # noqa: BLE001
                    pass

            if not get_settings().browser_headless:
                page.wait_for_timeout(3000)  # let the operator see the result before it closes
            # Always close, on every outcome — a left-open Chromium keeps rendering the page
            # and pegged this server's CPU at 100% once already.
            ctx.close()
            browser.close()

            # A field wouldn't keep its value even after 3 tries → the ERP doesn't have this
            # value (invalid lookup). Report as a failure naming the field and value.
            if value_error:
                return {
                    "status": "failed",
                    "final_url": final_url,
                    "log": log,
                    "screenshot": shot,
                    "reason": (
                        f"Field '{value_error['field']}' rejected the value "
                        f"'{value_error['value']}' — the ERP doesn't have this value "
                        f"(re-entered {value_error['attempts']} times, it kept clearing)."
                    ),
                    "failed_steps": [value_error["field"]],
                    "failed_field": value_error.get("field_label"),
                    "failed_value": value_error.get("value"),
                    "captured": captured,
                    "ai_diagnosis": ai_diagnosis,
                    "resumed": resumed, "checkpoint_saved": checkpoint_saved,
                }

            # The form locked after entering a value → can't submit. Usually a DUPLICATE
            # (the record already exists). Report it as its own outcome, not a hard failure.
            if blocked:
                out = blocked.get("outcome")
                status = "duplicated" if out in ("duplicate", "blocked", "unknown") else "failed"
                return {
                    "status": status,
                    "final_url": final_url,
                    "log": log,
                    "screenshot": shot,
                    "reason": blocked.get("reason"),
                    # Anything already read off the ERP is worth keeping even on a bad outcome.
                    "captured": captured,
                    "ai_diagnosis": ai_diagnosis,
                    "resumed": resumed, "checkpoint_saved": checkpoint_saved,
                }

            # Decide success vs failure. Failure = we ran out of time (stuck), or the
            # FINAL ERP action (the last element-bound recorded step, e.g. Submit) could
            # not be completed even after AI takeover. Steps legitimately skipped earlier
            # (e.g. login already done) don't fail the run as long as the final action ran.
            element_idx = [
                k for k, s in enumerate(steps)
                if s.get("action") in ("click", "submit", "fill", "select", "autocomplete")
            ]
            unfinished = [k for k in element_idx if k not in executed]
            last_action_done = (not element_idx) or (element_idx[-1] in executed)
            if aborted or not last_action_done:
                # A run somebody stopped is not the same as one that broke, and the summary
                # must not accuse the ERP of failing when a person pressed the button.
                if stopped:
                    reason = ("Stopped by hand before it finished. Nothing after the last step "
                              "below was entered - check the ERP before re-running.")
                elif aborted:
                    reason = "The web-entry got stuck too long and could not be completed."
                else:
                    reason = ("Could not complete the ERP entry — a required step could not be "
                              "done, even with AI assistance.")
                failed_steps = [
                    (steps[k].get("description") or steps[k].get("field_label") or steps[k].get("selector") or steps[k].get("action"))
                    for k in unfinished
                ]
                return {
                    "status": "stopped" if stopped else "failed",
                    "stopped": stopped,
                    "final_url": final_url,
                    "log": log,
                    "screenshot": shot,
                    "reason": reason,
                    "failed_steps": failed_steps,
                    # What actually got done before it ended - the summary the operator reads
                    # instead of counting log lines.
                    "steps_done": len(executed),
                    "steps_total": len(steps),
                    "captured": captured,
                    "ai_diagnosis": ai_diagnosis,
                    "resumed": resumed, "checkpoint_saved": checkpoint_saved,
                }
            if hard_failures:
                # The entry DID complete - the final action ran - so this is not "failed":
                # calling it that invites a re-run and a duplicate record. But it is not a
                # clean success either, and saying "ok" is how a blank field reached the ERP
                # unnoticed. Report it as its own outcome, with the detail attached.
                return {
                    "status": "partial",
                    "final_url": final_url,
                    "log": log,
                    "screenshot": shot,
                    "captured": captured,
                    "failed_steps": hard_failures,
                    "reason": (
                        f"The entry was submitted, but {len(hard_failures)} step(s) failed: "
                        + " | ".join(hard_failures[:3])
                        + (" …" if len(hard_failures) > 3 else "")
                    ),
                    "ai_diagnosis": ai_diagnosis,
                    "resumed": resumed, "checkpoint_saved": checkpoint_saved,
                }
            return {"status": "ok", "final_url": final_url, "log": log,
                    "resumed": resumed, "checkpoint_saved": checkpoint_saved,
                    "screenshot": shot, "captured": captured}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc), "log": log, "reason": f"Playback crashed: {exc}"}

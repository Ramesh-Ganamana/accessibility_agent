(options = {}) => {
 const ignored = Array.isArray(options) ? options : options.ignored || [];
 const parent = e => e.parentElement || e.getRootNode()?.host || null;
 const within = (e, ancestor) => {
   for (let n=e; n; n=parent(n)) if (n===ancestor) return true;
   return false;
 };
 const matchesAncestor = (e, selector) => {
   for (let n=e; n; n=parent(n)) if (n.matches(selector)) return true;
   return false;
 };
 const excluded = e => ignored.some(s => matchesAncestor(e, s));
 const visible = e => !!e.getClientRects().length &&
   getComputedStyle(e).visibility !== 'hidden' && !excluded(e);
 const path = e => {
   const parts = [];
   while (e && e.nodeType === 1) {
     const tag = e.localName;
     const siblings = e.parentNode?.children
       ? [...e.parentNode.children].filter(x => x.localName === tag) : [e];
     parts.unshift(tag + ':nth-of-type(' + (siblings.indexOf(e) + 1) + ')');
     e = e.parentElement;
   }
   return parts.join(' > ');
 };
 const roots = [];
 const elements = [];
 const elementNodes = [];
 const dom = [];
 let truncated = false;
 let shadowRoots = 0;
 const rootName = (root, e) => {
   const labelled = (e.getAttribute('aria-labelledby') || '').split(/\s+/)
     .map(id => root.getElementById?.(id)?.innerText ||
       document.getElementById(id)?.innerText || '').join(' ').trim();
   return e.getAttribute('aria-label') || labelled ||
     (e.labels ? [...e.labels].map(x => x.innerText).join(' ') : '') ||
     e.innerText || e.getAttribute('alt') ||
     e.getAttribute('title') || '';
 };
 const syntheticHandler = (e, keyboard=false) => {
   if (keyboard ? ['onkeydown','onkeyup','onkeypress'].some(k=>typeof e[k]==='function') :
     typeof e.onclick === 'function') return true;
   // React delegates events through private props and Vue keeps invokers on
   // the element. Inspect only property names/types, never handler code.
   for (const key of [...Object.keys(e), ...Object.getOwnPropertySymbols(e)]) {
     if (!/^__reactProps\$|^_vei$|^Symbol\(_vei\)$/.test(String(key))) continue;
     const value = Object.getOwnPropertyDescriptor(e, key)?.value;
     if (!value || typeof value !== 'object') continue;
     const event = keyboard ? /^on(?:KeyDown|KeyUp|KeyPress)(?:Capture|Once|Passive)*$/i :
       /^onClick(?:Capture|Once|Passive)*$/i;
     if (Object.keys(value).some(name => event.test(name) &&
       typeof Object.getOwnPropertyDescriptor(value, name)?.value === 'function')) {
       return true;
     }
   }
   return false;
 };
 const interactiveRole = e => /^(button|link|tab|menuitem|menuitemcheckbox|menuitemradio|option|combobox|checkbox|radio|switch|textbox|slider|spinbutton|scrollbar|treeitem)$/i
   .test(e.getAttribute('role') || '');
 const extra = (root, e) => {
   if (!visible(e) || ['HTML', 'BODY', 'SCRIPT', 'STYLE', 'NOSCRIPT'].includes(e.tagName)) return false;
   const name = rootName(root, e).trim();
   const direct = syntheticHandler(e) || e.hasAttribute('data-href') || e.hasAttribute('data-url');
   const pointer = getComputedStyle(e).cursor === 'pointer' &&
     (!e.parentElement || getComputedStyle(e.parentElement).cursor !== 'pointer');
   const semantic = interactiveRole(e) ||
     e.hasAttribute('aria-haspopup') || e.hasAttribute('aria-expanded') ||
     e.hasAttribute('popovertarget');
   return (direct || pointer || semantic) && name.length > 0 && name.length <= 300 &&
     !e.parentElement?.closest('a[href],button,input,select,textarea,summary,' +
       '[role="button"],[role="link"],[role="tab"],[role="menuitem"],[onclick],' +
       '[data-href],[data-url]');
 };
 const linkURL = e => {
   if (e.localName !== 'a') return '';
   try {
     const url = new URL(e.href), here = new URL(location.href);
     const anchor = decodeURIComponent(url.hash.slice(1));
     if (url.origin === here.origin && url.pathname === here.pathname &&
       url.search === here.search && anchor &&
       (document.getElementById(anchor) || document.getElementsByName(anchor).length)) {
       return location.href;
     }
   } catch {}
   return e.href;
 };
 const navigationHint = e => {
   const value = e.getAttribute('data-href') || e.getAttribute('data-url');
   if (!value) return '';
   try { return new URL(value, location.href).href; } catch { return ''; }
 };
 const standard = 'a[href],button,input:not([type=hidden]),textarea,select,summary,' +
   '[role],[tabindex],[popovertarget],[draggable="true"],canvas[aria-label],canvas[role],canvas[tabindex]';
 const collect = (root, shadowPath) => {
   if (roots.length >= 64 || shadowPath.length > 16) { truncated=true; return; }
   roots.push(root);
   const remaining = Math.max(0, 10000-dom.length);
   const descendants = [...root.querySelectorAll(root === document ? 'body *' : '*')];
   if (descendants.length>remaining) truncated=true;
   const bounded = descendants.slice(0, remaining);
   dom.push(...bounded.filter(e => !['SCRIPT','STYLE','NOSCRIPT'].includes(e.tagName) && !excluded(e)));
   const candidates = [...root.querySelectorAll(standard)];
   const custom = bounded.filter(e => extra(root, e));
   const all = [...new Set([...candidates, ...custom])]
     .filter(e => visible(e) || (e.localName === 'a' && !excluded(e)));
   if (all.length+elements.length > 2000) truncated=true;
   for (const e of all.slice(0, Math.max(0, 2000-elements.length))) {
     const accessibleName = rootName(root, e).trim();
     elementNodes.push(e);
     elements.push({
       selector: path(e), shadow_path: shadowPath, tag: e.localName,
       role: e.getAttribute('role') || '', visible: visible(e),
       accessible_name: accessibleName, html: e.outerHTML.slice(0, 2000),
       attributes: Object.fromEntries(['role','type','aria-expanded','aria-selected',
         'aria-controls','aria-haspopup','aria-checked','tabindex','popovertarget',
         ].filter(k => e.hasAttribute(k)).map(k => [k, e.getAttribute(k)])),
       href: linkURL(e), download: e.hasAttribute('download'),
       scripted_link: e.localName === 'a' && (e.hasAttribute('onclick') || syntheticHandler(e)),
       clickable: extra(root, e), navigation_hint: navigationHint(e),
       target: e.getAttribute('target') || '',
       disabled: e.matches(':disabled,[aria-disabled=true]'),
       blocked_by_modal: false, inert: matchesAncestor(e, '[inert]'),
       submit: !!e.form && ['submit','image','reset'].includes(e.type),
       input_type: e.type || '', draggable: e.draggable === true,
       keyboard: e.matches('[tabindex],button,a[href],[role]'),
       keyboard_handler: syntheticHandler(e, true),
       hoverable: e.matches('[title],[aria-haspopup],[aria-expanded],[data-hover],canvas'),
       canvas: e.localName === 'canvas',
       options: e.localName === 'select' ? [...e.options].map((o, i) => ({index: i,
         label: o.label, selected: o.selected, disabled: o.disabled || o.parentElement.disabled,
         empty: !o.value})) : [],
       form: e.form ? path(e.form) : null
     });
   }
   for (const host of bounded) {
     if (host.shadowRoot && !excluded(host)) {
       shadowRoots += 1;
       collect(host.shadowRoot, [...shadowPath, path(host)]);
     }
   }
 };
 collect(document, []);
 const modal = roots.flatMap(root => [...root.querySelectorAll('dialog[open],[aria-modal=true]')]).find(visible);
 elements.forEach((item,i) => { item.blocked_by_modal=!!modal && !within(elementNodes[i],modal); });
 const structure = dom.filter(visible).slice(0, 10000).map(e => [e.localName,
   e.getAttribute('role'), e.getAttribute('aria-expanded'), e.getAttribute('aria-selected'),
   visible(e), e.open ?? null, e.checked ?? null, e.selectedIndex ?? null]);
 const textParts = [];
 for (const root of roots) {
   const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
   while (walker.nextNode()) {
     const parent = walker.currentNode.parentElement;
     if (parent && visible(parent) && !['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(parent.tagName)) {
       textParts.push(walker.currentNode.textContent || '');
     }
   }
 }
 const text = textParts.join(' ');
 const content = text.slice(0, 100000);
 const formValues = dom.filter(e => visible(e) && ['INPUT','TEXTAREA','SELECT'].includes(e.tagName) &&
   !['password','hidden','file'].includes(e.type)).map(e => [path(e), e.value, e.checked ?? null]);
 const loading = ['[aria-busy="true"]','[data-loading="true"]','[data-state="loading"]',
   '[role="progressbar"]:not([aria-valuenow])', ...(options.loading_selectors || [])];
 const loadingVisible = loading.some(s => [...document.querySelectorAll(s)].some(visible));
 let readiness = null;
 if (options.check_readiness) {
   const inViewport = e => { const r = e.getBoundingClientRect();
     return visible(e) && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth; };
   readiness = {document_complete: document.readyState === 'complete',
     fonts_loaded: !document.fonts || document.fonts.status === 'loaded',
     images_loaded: [...document.images].filter(e => visible(e) &&
       (e.loading !== 'lazy' || inViewport(e))).every(e => e.complete),
     application_ready: !options.ready_selector ||
       [...document.querySelectorAll(options.ready_selector)].some(visible),
     layout: dom.filter(visible).slice(0, 10000).map(e => {
       const r = e.getBoundingClientRect(), s = getComputedStyle(e);
       return [e.localName, e.getAttribute('class'), e.getAttribute('style'),
         Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height),
         s.opacity, s.color, s.backgroundColor];
     })};
 }
 return {elements: elements.slice(0, 2000), truncated: elements.length > 2000 ||
   truncated || dom.length > 10000 || text.length > 100000, structure,
   text: content, ui: elements.filter(e => e.visible).slice(0, 2000).map(e =>
     [e.selector, e.role, e.accessible_name, e.attributes, e.disabled, e.options]),
   form_values: formValues, dialogs: [...document.querySelectorAll(
       'dialog[open],[role=dialog]')].filter(visible).map(path),
   menus: [...document.querySelectorAll('[role=menu],[role=listbox]')].filter(visible).map(path),
   tabs: [...document.querySelectorAll('[role=tab][aria-selected=true]')].filter(visible).map(path),
   forms: [...document.forms].filter(visible).map(path), frames: document.querySelectorAll('iframe,frame').length,
   shadow: shadowRoots > 0, shadow_roots: shadowRoots, unsupported_shadow: false, readiness,
   busy: loadingVisible || textParts.some(t => /^\s*(loading(?:\s+(?:module|page|data|content|application))?|please wait)[.\s…]*$/i.test(t)),
   render_ready: !!content.trim() || elements.some(e => e.visible)};
}

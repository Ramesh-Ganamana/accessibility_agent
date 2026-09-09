(options = {}) => {
 const ignored = Array.isArray(options) ? options : options.ignored || [];
 const excluded = e => ignored.some(s => e.matches(s) || e.closest(s));
 const visible = e => !!e.getClientRects().length &&
   getComputedStyle(e).visibility !== 'hidden' && !excluded(e);
 const path = e => {
   const parts = [];
   while (e && e.nodeType === 1) {
     const tag = e.localName;
     const siblings = e.parentElement ? [...e.parentElement.children].filter(x=>x.localName===tag) : [e];
     parts.unshift(tag + ':nth-of-type(' + (siblings.indexOf(e)+1) + ')');
     e = e.parentElement;
   }
   return parts.join(' > ');
 };
 const name = e => {
   const labelled = (e.getAttribute('aria-labelledby') || '').split(/\s+/)
     .map(id=>document.getElementById(id)?.innerText || '').join(' ').trim();
   return e.getAttribute('aria-label') || labelled ||
     (e.labels ? [...e.labels].map(x=>x.innerText).join(' ') : '') ||
     e.innerText || e.getAttribute('alt') || e.getAttribute('title') || '';
 };
 const modal = [...document.querySelectorAll('dialog[open],[aria-modal=true]')].find(visible);
 const linkURL = e => {
   if(e.localName !== 'a') return '';
   try {
     const url = new URL(e.href), here = new URL(location.href);
     const anchor = decodeURIComponent(url.hash.slice(1));
     if(url.origin===here.origin && url.pathname===here.pathname && url.search===here.search &&
       anchor && (document.getElementById(anchor) || document.getElementsByName(anchor).length)) {
       return location.href; // In-page scroll targets are not separate application routes.
     }
   } catch {}
   return e.href;
 };
 const standard = 'a[href],button,input:not([type=hidden]),textarea,select,summary,' +
   '[role],[tabindex],[popovertarget]';
 const extra = e => {
   if(!visible(e) || ['HTML','BODY','SCRIPT','STYLE'].includes(e.tagName)) return false;
   const direct = typeof e.onclick === 'function' || e.hasAttribute('data-href') ||
     e.hasAttribute('data-url');
   // A pointer cursor is a signal, never permission to click. Avoid inherited
   // cursors on every child of a card and children of existing controls.
   const pointer = getComputedStyle(e).cursor === 'pointer' &&
     (!e.parentElement || getComputedStyle(e.parentElement).cursor !== 'pointer');
   return (direct || pointer) && !!name(e).trim() && name(e).trim().length <= 300 &&
     !e.parentElement?.closest('a[href],button,input,select,textarea,summary,' +
       '[role="button"],[role="link"],[role="tab"],[role="menuitem"],' +
       '[onclick],[data-href],[data-url]');
 };
 const candidates = [...document.querySelectorAll('body *')].slice(0,10000);
 const all = [...new Set([...document.querySelectorAll(standard), ...candidates.filter(extra)])]
   .filter(e=>visible(e) || (e.localName==='a' && !excluded(e)));
 const navigationHint = e => {
   const value = e.getAttribute('data-href') || e.getAttribute('data-url');
   if(!value) return '';
   try { return new URL(value, location.href).href; } catch { return ''; }
 };
 const elements = all.slice(0, 2000).map(e => ({
   selector: path(e), tag:e.localName, role:e.getAttribute('role') || '', visible:visible(e),
   accessible_name:name(e), html:e.outerHTML.slice(0,2000),
   attributes:Object.fromEntries(['role','type','aria-expanded','aria-selected','aria-controls',
     'aria-haspopup','aria-checked','tabindex','popovertarget'].filter(k=>e.hasAttribute(k))
     .map(k=>[k,e.getAttribute(k)])),
   href:linkURL(e), download:e.hasAttribute('download'),
   scripted_link:e.localName==='a' && e.hasAttribute('onclick'),
   clickable:extra(e), navigation_hint:navigationHint(e),
   target:e.getAttribute('target') || '', disabled:e.matches(':disabled,[aria-disabled=true]'),
   blocked_by_modal:!!modal && !modal.contains(e), inert:!!e.closest('[inert]'),
   submit:!!e.form && ['submit','image','reset'].includes(e.type),
   input_type:e.type || '',
   options:e.localName==='select' ? [...e.options].map((o,i)=>({index:i,
     label:o.label, selected:o.selected, disabled:o.disabled || o.parentElement.disabled,
     empty:!o.value})) : [],
   form: e.form ? path(e.form) : null
 }));
 const dom = [...document.querySelectorAll('body *')].filter(e=>
   !['SCRIPT','STYLE','NOSCRIPT'].includes(e.tagName) && !excluded(e));
 const structure = dom.filter(visible).slice(0,10000).map(e=>[e.localName,e.getAttribute('role'),
   e.getAttribute('aria-expanded'),e.getAttribute('aria-selected'),visible(e),
   e.open ?? null, e.checked ?? null, e.selectedIndex ?? null]);
 const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
 const text = [];
 while(walker.nextNode()) {
   const parent = walker.currentNode.parentElement;
   if (parent && visible(parent) && !['SCRIPT','STYLE','NOSCRIPT'].includes(parent.tagName)) {
     text.push(walker.currentNode.textContent);
   }
 }
 const content = text.join(' ');
 const formValues = dom.filter(e=>visible(e) && ['INPUT','TEXTAREA','SELECT'].includes(e.tagName) &&
   !['password','hidden','file'].includes(e.type)).map(e=>[path(e),e.value,e.checked ?? null]);
 const loading = ['[aria-busy="true"]','[data-loading="true"]','[data-state="loading"]',
   '[role="progressbar"]:not([aria-valuenow])', ...(options.loading_selectors || [])];
 const loadingVisible = loading.some(s=>[...document.querySelectorAll(s)].some(visible));
 let readiness = null;
 if(options.check_readiness) {
   const inViewport = e => {
     const r=e.getBoundingClientRect();
     return visible(e) && r.bottom>0 && r.right>0 && r.top<innerHeight && r.left<innerWidth;
   };
   readiness = {
     document_complete:document.readyState==='complete',
     fonts_loaded:!document.fonts || document.fonts.status==='loaded',
     images_loaded:[...document.images].filter(e=>visible(e) &&
       (e.loading!=='lazy' || inViewport(e))).every(e=>e.complete),
     application_ready:!options.ready_selector ||
       [...document.querySelectorAll(options.ready_selector)].some(visible),
     layout:dom.filter(visible).slice(0,10000).map(e=>{
       const r=e.getBoundingClientRect(), s=getComputedStyle(e);
       return [e.localName,e.getAttribute('class'),e.getAttribute('style'),
         Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height),
         s.opacity,s.color,s.backgroundColor];
     })
   };
 }
 return {elements, truncated:all.length>2000 || dom.length>10000 || content.length>100000,
   structure, text:content.slice(0,100000),
   ui:elements.filter(e=>e.visible).map(e=>[e.selector,e.role,e.accessible_name,e.attributes,e.disabled,e.options]),
   form_values:formValues,
   dialogs:[...document.querySelectorAll('dialog[open],[role=dialog]')].filter(visible).map(path),
   menus:[...document.querySelectorAll('[role=menu],[role=listbox]')].filter(visible).map(path),
   tabs:[...document.querySelectorAll('[role=tab][aria-selected=true]')].filter(visible).map(path),
   forms:[...document.forms].filter(visible).map(path),
   frames:document.querySelectorAll('iframe,frame').length,
   shadow:[...document.querySelectorAll('*')].some(e=>e.shadowRoot),
   readiness,
   busy:loadingVisible ||
     text.some(t=>/^\s*(loading(?:\s+(?:module|page|data|content|application))?|please wait)[.\s…]*$/i.test(t)),
   render_ready:!!content.trim() || elements.some(e=>e.visible)
 };
}

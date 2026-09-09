"""Read-only, bounded descriptions of visible application setup gates.

Descriptions deliberately omit field values and HTML. Detecting a gate never chooses
an account, submits a form, or dismisses a dialog on the user's behalf.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page


@dataclass(frozen=True)
class InputField:
    label: str
    kind: str
    required: bool = False
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Blocker:
    kind: str
    title: str
    selector: str
    fields: tuple[InputField, ...] = ()


_DETECTOR = r"""({configuredSelectors, includeSetupForms}) => {
    const MAX_BLOCKERS = 20, MAX_FIELDS = 30, MAX_OPTIONS = 30;
    const compact = (text, limit = 160) => String(text || '').replace(/\s+/g, ' ').trim()
        .slice(0, limit);
    const visible = el => {
        if (!(el instanceof Element) || el.closest('[hidden], [aria-hidden="true"], [inert]'))
            return false;
        const style = getComputedStyle(el), rect = el.getBoundingClientRect();
        if (style.display === 'none' || style.visibility === 'hidden' ||
            style.visibility === 'collapse' || Number(style.opacity) === 0 ||
            rect.width <= 0 || rect.height <= 0) return false;
        for (let ancestor = el.parentElement; ancestor; ancestor = ancestor.parentElement) {
            const parentStyle = getComputedStyle(ancestor);
            if (Number(parentStyle.opacity) === 0) return false;
        }
        return rect.right > 0 && rect.bottom > 0 && rect.left < innerWidth &&
            rect.top < innerHeight;
    };
    // Never read .value, defaultValue, form data, or contenteditable text.
    const labelText = el => {
        if (!el || el.matches(
            'input, textarea, select, script, style, [contenteditable]'
        )) return '';
        const clone = el.cloneNode(true);
        clone.querySelectorAll('input, textarea, select, script, style, [contenteditable]')
            .forEach(node => node.remove());
        return compact(clone.textContent);
    };
    const explicitName = el => {
        const direct = compact(el.getAttribute('aria-label'));
        if (direct) return direct;
        const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
        return compact(ids.slice(0, 10).map(id =>
            labelText(document.getElementById(id))).join(' '));
    };
    const fieldName = el => explicitName(el) ||
        compact(Array.from(el.labels || []).slice(0, 5).map(labelText).join(' ')) ||
        labelText(el.closest('fieldset')?.querySelector('legend')) ||
        compact(el.getAttribute('placeholder'));
    const path = el => {
        const steps = [];
        for (let node = el; node instanceof Element; node = node.parentElement) {
            const siblings = node.parentElement ?
                Array.from(node.parentElement.children)
                    .filter(item => item.localName === node.localName)
                : [node];
            steps.unshift(`${node.localName}:nth-of-type(${siblings.indexOf(node) + 1})`);
        }
        return steps.join(' > ');
    };
    const controls = 'input:not([type="hidden"]):not([type="submit"]):not([type="button"])' +
        ':not([type="reset"]):not([type="image"]), textarea, select, [contenteditable="true"], ' +
        '[role="textbox"], [role="combobox"], [role="listbox"], [role="tree"], ' +
        '[role="radiogroup"], [role="checkbox"], [role="radio"], ' +
        '[role="spinbutton"], [role="switch"]';
    const groupRoles = new Set(['combobox', 'listbox', 'tree', 'radiogroup']);
    const fieldElements = root => {
        const found = [...(root.matches(controls) ? [root] : []),
            ...root.querySelectorAll(controls)]
            .filter(el => visible(el) && !el.matches(':disabled, [aria-disabled="true"]'));
        return found.filter(el => !found.some(parent => parent !== el && parent.contains(el) &&
            groupRoles.has(parent.getAttribute('role')))).slice(0, MAX_FIELDS);
    };
    const describeField = el => {
        const role = el.getAttribute('role');
        const kind = role || (el.localName === 'input' ? el.type :
            el.localName === 'textarea' ? 'textarea' :
            el.localName === 'select' ? 'select' : 'textbox');
        let options = [];
        if (el.localName === 'select') {
            options = Array.from(el.options).filter(option => !option.disabled)
                .slice(0, MAX_OPTIONS).map(option => compact(option.label || option.textContent));
        } else if (groupRoles.has(role)) {
            let roots = [el];
            const controlledIds = (el.getAttribute('aria-controls') ||
                el.getAttribute('aria-owns') || '')
                .split(/\s+/).filter(Boolean).slice(0, 10);
            roots = roots.concat(controlledIds.map(id =>
                document.getElementById(id)).filter(Boolean));
            options = roots.flatMap(root => Array.from(root.querySelectorAll(
                '[role="option"], [role="treeitem"], [role="radio"]')))
                .filter(option => visible(option) &&
                    option.getAttribute('aria-disabled') !== 'true')
                .slice(0, MAX_OPTIONS).map(option => {
                    if (explicitName(option)) return explicitName(option);
                    const copy = option.cloneNode(true);
                    copy.querySelectorAll('[role="group"], [role="tree"], input, textarea, ' +
                        'select, script, style, [contenteditable]').forEach(node => node.remove());
                    return compact(copy.textContent);
                });
        }
        return {label: fieldName(el) || `${kind.charAt(0).toUpperCase()}${kind.slice(1)} input`,
            kind, required: el.required === true || el.getAttribute('aria-required') === 'true',
            options: [...new Set(options.filter(Boolean))].slice(0, MAX_OPTIONS)};
    };
    const title = (el, fallback) => explicitName(el) || labelText(el.querySelector(
        'h1, h2, h3, h4, h5, h6, [role="heading"], legend')) || fallback;
    const blocksCenter = el => {
        const rect = el.getBoundingClientRect(), x = innerWidth / 2, y = innerHeight / 2;
        const top = document.elementFromPoint(x, y);
        return rect.left <= x && rect.right >= x && rect.top <= y && rect.bottom >= y &&
            !!top && (top === el || el.contains(top));
    };
    const positioned = el => {
        for (let node = el; node && node !== document.body; node = node.parentElement) {
            if (['fixed', 'absolute'].includes(getComputedStyle(node).position)) return true;
        }
        return false;
    };
    const dismissible = el => Array.from(el.querySelectorAll(
        'button, a[href], [role="button"], [role="link"]'
    )).some(control => {
        if (!visible(control) || control.matches(':disabled, [aria-disabled="true"]'))
            return false;
        const name = explicitName(control) || labelText(control) ||
            compact(control.getAttribute('title'));
        return /^(?:close|cancel|dismiss|skip|not now|maybe later|no thanks|continue without)\b/i
            .test(name) || /^[x×✕]$/i.test(name);
    });
    const candidates = [];
    const add = (el, kind) => {
        if (!visible(el) || candidates.some(item => item.el === el)) return;
        candidates.push({el, kind});
    };
    // Explicit selectors are an escape hatch for applications without semantic markup.
    for (const selector of configuredSelectors) {
        try { Array.from(document.querySelectorAll(selector)).slice(0, MAX_BLOCKERS)
            .forEach(el => add(el, 'overlay')); }
        catch { /* Invalid selectors never stop a crawl. */ }
    }
    document.querySelectorAll('dialog[open], [role="dialog"], ' +
        '[role="alertdialog"], [aria-modal="true"]')
        .forEach(el => {
            if (!visible(el)) return;
            const nativeDialog = el.localName === 'dialog';
            const nativeModal = nativeDialog && (() => {
                try { return el.matches(':modal'); } catch { return false; }
            })();
            if (!dismissible(el) && (el.getAttribute('aria-modal') === 'true' || nativeModal ||
                (!nativeDialog && positioned(el) && blocksCenter(el)))) add(el, 'dialog');
        });
    document.querySelectorAll('[class*="modal" i], [class*="dialog" i], ' +
        '[id*="modal" i], [id*="dialog" i]')
        .forEach(el => {
            const hint = `${el.getAttribute('class') || ''} ${el.id}`;
            if (el.matches('button, a, input, select, textarea, [role="button"], ' +
                '[role="link"], [role="menu"], [role="tooltip"]')) return;
            const rect = el.getBoundingClientRect();
            if (!dismissible(el) &&
                /(?:^|[\s_-])(?:modal|dialog)(?:$|[\s_-])/i.test(hint) && visible(el) &&
                rect.width >= 160 && rect.height >= 80 && positioned(el) && blocksCenter(el))
                add(el, 'overlay');
        });
    const usableNavigation = Array.from(document.querySelectorAll('a[href], nav button, ' +
        '[role="navigation"] button, [role="navigation"] [role="link"], ' +
        '[role="navigation"] [role="button"]'))
        .some(el => {
            if (!visible(el) || el.matches(':disabled, [aria-disabled="true"]')) return false;
            const name = explicitName(el) || labelText(el);
            if (!name || /^(?:sign|log)\s*(?:out|in)\b/i.test(name) ||
                /^(?:privacy|terms|help|support|contact)\b/i.test(name)) return false;
            if (el.localName !== 'a') return true;
            const href = (el.getAttribute('href') || '').trim();
            return !!href && href !== '#' && !/^(?:javascript:|mailto:|tel:)/i.test(href);
        });
    if (includeSetupForms && !usableNavigation) {
        document.querySelectorAll('form, [role="form"]').forEach(el => {
            if (!visible(el) || el.getAttribute('role') === 'search' ||
                el.closest('[role="search"]')) return;
            const fields = fieldElements(el);
            const incomplete = fields.some(field => {
                if (field.type === 'search' || /\bsearch\b/i.test(fieldName(field))) return false;
                return (field.required && field.validity?.valueMissing) ||
                    (field.getAttribute('aria-required') === 'true' &&
                        field.getAttribute('aria-invalid') === 'true');
            });
            if (incomplete) add(el, 'setup_form');
        });
    }
    // A modal wrapper and its dialog body describe one gate, not two prompts.
    return candidates.filter(item => !candidates.some(other => other !== item &&
        other.el.contains(item.el))).slice(0, MAX_BLOCKERS).map(({el, kind}) => ({
            kind, title: title(el, kind === 'setup_form' ?
                'Required setup input' : 'Dialog requires attention'),
            selector: path(el), fields: fieldElements(el).map(describeField)
        }));
}"""


async def detect_blockers(
    page: Page,
    selectors: Sequence[str] = (),
    *,
    include_setup_forms: bool = False,
) -> list[Blocker]:
    """Return visible gates without reading entered values or interacting with the UI."""
    result: list[dict[str, Any]] = await page.evaluate(
        _DETECTOR,
        {
            "configuredSelectors": list(selectors)[:20],
            "includeSetupForms": include_setup_forms,
        },
    )
    return [
        Blocker(
            kind=item["kind"],
            title=item["title"],
            selector=item["selector"],
            fields=tuple(
                InputField(
                    label=field["label"],
                    kind=field["kind"],
                    required=field["required"],
                    options=tuple(field["options"]),
                )
                for field in item["fields"]
            ),
        )
        for item in result
    ]

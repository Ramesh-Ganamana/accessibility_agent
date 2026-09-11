"""Conservative UI action planning. Test values never enter graph records."""

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from playwright.async_api import Page
from pydantic import SecretStr

from accessibility_agent.config.settings import Crawl
from accessibility_agent.crawler.discovery import locator_for_item
from accessibility_agent.crawler.fingerprint import digest
from accessibility_agent.crawler.navigation import classify_link
from accessibility_agent.models import Action, Element, Safety, State

ROLES = {
    "button",
    "link",
    "tab",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "combobox",
    "checkbox",
    "radio",
    "switch",
    "textbox",
    "slider",
    "spinbutton",
}


def is_control(item: dict[str, Any]) -> bool:
    return (
        item["tag"] in {"a", "button", "input", "textarea", "select", "summary"}
        or item["role"] in ROLES
        or item["attributes"].get("tabindex", "-1") not in {"-1", ""}
        or bool(item.get("clickable"))
        or bool(item.get("canvas"))
        or bool(item.get("draggable"))
    )


def identity(item: dict[str, Any]) -> tuple[str, ...]:
    return (
        item["tag"],
        item["role"],
        item["accessible_name"],
        item["href"],
        item["input_type"],
        str(item["submit"]),
        item.get("navigation_hint", ""),
        str(item.get("frame_path", [])),
        str(item.get("shadow_path", [])),
    )


@dataclass
class Step:
    action: Action
    expected_identity: tuple[str, ...] = field(repr=False)
    url: str = field(default="", repr=False)
    value: SecretStr | None = field(default=None, repr=False)
    skip_reason: str | None = None
    direct_navigation: bool = False


def safety(item: dict[str, Any], root: str, settings: Crawl) -> Safety:
    if item["href"]:
        return classify_link(
            item["href"], item["accessible_name"], root, settings.same_origin_only, item["download"]
        )
    # Check negative names before positive ARIA semantics or labels.
    if classify_link(root, item["accessible_name"], root, False) == Safety.DESTRUCTIVE:
        return Safety.DESTRUCTIVE
    if item["submit"] or item["download"]:
        return Safety.CAUTION
    if item["input_type"] in {"password", "file", "hidden"}:
        return Safety.UNKNOWN
    if item.get("navigation_hint"):
        return classify_link(
            item["navigation_hint"],
            item["accessible_name"],
            root,
            settings.same_origin_only,
            item["download"],
        )
    if (
        item["tag"] in {"summary", "select"}
        or item["role"]
        in {
            "tab",
            "menuitem",
            "menuitemcheckbox",
            "menuitemradio",
            "option",
            "combobox",
            "checkbox",
            "radio",
            "switch",
        }
        or item["input_type"] in {"checkbox", "radio"}
        or any(k in item["attributes"] for k in ("aria-expanded", "aria-haspopup", "popovertarget"))
        or re.match(
            r"^(open|show|view|close|expand|collapse|toggle|learn more|read more|see details)\b",
            item["accessible_name"].strip(),
            re.I,
        )
        or re.fullmatch(
            r"(notifications?(?:\s*\(.*\))?|(?:user|account|profile|navigation|main)\s+menu|"
            r"menu|search(?:\W.*)?|(?:switch to |toggle )?(?:dark|light)(?: mode| theme)?)",
            item["accessible_name"].strip(),
            re.I | re.S,
        )
    ):
        return Safety.SAFE
    # An explicit QA opt-in broadens labels, not the preceding safety guards.
    # Focusability alone is not evidence that an element is a click target.
    if settings.test_environment and (
        item["tag"] == "button"
        or (item["tag"] == "input" and item["input_type"] == "button")
        or item["role"] in {"button", "link"}
        or bool(item.get("clickable"))
    ):
        return Safety.SAFE
    return Safety.UNKNOWN


async def plan(
    page: Page,
    state: State,
    items: list[dict[str, Any]],
    root: str,
    settings: Crawl,
    *,
    keyboard_enabled: bool = True,
) -> list[Step]:
    elements = {
        (tuple(e.frame_path), tuple(e.shadow_path), e.selector): e
        for e in [*state.interactive_elements, *state.discovered_links]
    }
    result = []
    for item in items:
        if not is_control(item):
            continue
        element: Element | None = elements.get(
            (
                tuple(item.get("frame_path", [])),
                tuple(item.get("shadow_path", [])),
                item["selector"],
            )
        )
        if element is None:
            # Inventory retains hidden anchors, but record() excludes hidden
            # controls without hrefs. Real hidden links remain in the map.
            continue
        category = safety(item, root, settings)
        kind: Literal["navigate", "click", "select", "fill"] = (
            "navigate" if item["href"] else "click"
        )
        value = None
        reason = None
        option_indices: list[int | None] = [None]
        if item["disabled"] or item["inert"]:
            reason = "disabled_control"
        elif item["blocked_by_modal"]:
            reason = "background_modal_control"
        elif item["input_type"] in {"password", "file", "hidden"}:
            reason = "sensitive_control"
        elif item["attributes"].get("aria-selected") == "true" and item["role"] == "tab":
            reason = "already_selected"
        elif item.get("canvas") and item["role"] not in {"button", "link"}:
            reason = "canvas_needs_hit_target"
        elif item["tag"] == "select":
            kind = "select"
            option_indices = [
                o["index"]
                for o in item["options"]
                if not o["selected"] and not o["disabled"] and not o["empty"]
            ]
            if not option_indices:
                option_indices, reason = [None], "no_select_alternative"
        elif (
            item["tag"] in {"input", "textarea"}
            and item["input_type"] not in {"checkbox", "radio", "button", "submit", "reset"}
            and item["role"] != "combobox"
        ):
            kind = "fill"
            for selector, configured in settings.form_values.items():
                if await locator_for_item(page, item).evaluate("(e,s) => e.matches(s)", selector):
                    value, category = configured, Safety.SAFE
                    break
            if value is None:
                if item["input_type"] == "search" or re.search(
                    r"\bsearch\b", item["accessible_name"], re.I
                ):
                    value, category = SecretStr("accessibility test"), Safety.SAFE
                else:
                    reason = "missing_test_data"
        specs: list[tuple[str, str | None, int | None]] = [
            (kind, None, option_index) for option_index in option_indices
        ]
        if item.get("draggable") and not (
            item["tag"] in {"a", "button", "input", "textarea", "select", "summary"}
            or item["role"] in ROLES
            or item.get("clickable")
            or item.get("canvas")
        ):
            # Draggability alone does not make the element a click target.
            specs = []
        # Explicit keyboard handlers can expose another UI state. This is an
        # interaction path, not a keyboard accessibility assessment. Native
        # click activation alone does not justify an additional keypress.
        if (
            keyboard_enabled
            and not item["href"]
            and item.get("keyboard")
            and item.get("keyboard_handler")
            and (
                item.get("role")
                in {"button", "link", "tab", "menuitem", "switch", "checkbox", "radio"}
                or (
                    item["attributes"].get("tabindex", "-1") not in {"-1", ""}
                    and bool(item.get("clickable"))
                )
            )
        ):
            activation_key = "Space" if item["role"] in {"switch", "checkbox", "radio"} else "Enter"
            specs.append(("keypress", activation_key, None))
        if (
            not item["href"]
            and item.get("hoverable")
            and (
                item.get("role") in {"button", "tab", "menuitem", "combobox"}
                or item["attributes"].get("aria-haspopup")
            )
        ):
            specs.append(("hover", None, None))
        if item.get("draggable"):
            specs.append(("drag", None, None))

        for option_number, (action_kind, key, option_index) in enumerate(specs):
            action_identity = [state.state_id, element.element_id, action_kind, option_index]
            if key is not None:
                action_identity.append(key)
            action = Action(
                action_id=digest(action_identity)[:20],
                source_state=state.state_id,
                action_type=action_kind,  # type: ignore[arg-type]
                element=element,
                safety=category,
                key=key,
                option_index=option_index,
                replayable=category == Safety.SAFE,
            )
            skip_reason = reason
            if action_kind == "drag":
                # Discovery cannot infer a safe, meaningful drop destination.
                skip_reason = skip_reason or "drag_needs_target"
                action.replayable = False
            if action_kind == "select" and option_number >= settings.max_select_options:
                skip_reason = "select_option_budget"
            allowed = (
                category in {Safety.UNKNOWN, Safety.CAUTION, Safety.DESTRUCTIVE}
                and action.action_id in settings.allowed_action_ids
            )
            if skip_reason is None and category != Safety.SAFE and not allowed:
                skip_reason = "policy_" + category.value.lower()
            result.append(
                Step(
                    action,
                    identity(item),
                    item["href"],
                    value,
                    skip_reason,
                    direct_navigation=bool(
                        action_kind == "navigate"
                        and item["href"]
                        and category == Safety.SAFE
                        and not item.get("scripted_link")
                        and not item.get("frame_path")
                        and not item.get("shadow_path")
                    ),
                )
            )
    return result

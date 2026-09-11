"""Interaction expansion must retain policy and require meaningful targets."""

import asyncio

import pytest

from accessibility_agent.config.settings import Crawl, load_settings
from accessibility_agent.crawler.fingerprint import digest
from accessibility_agent.crawler.interactive import InteractiveCrawler, ReplayDiverged
from accessibility_agent.crawler.planner import plan
from accessibility_agent.models import Element, Safety, State
from accessibility_agent.utils.privacy import Redactor

ROOT = "https://example.test/"


def control(**changes):
    return {
        "selector": "#target",
        "tag": "div",
        "role": "button",
        "accessible_name": "Show details",
        "href": "",
        "download": False,
        "submit": False,
        "input_type": "",
        "attributes": {"tabindex": "0"},
        "disabled": False,
        "inert": False,
        "blocked_by_modal": False,
        "keyboard": True,
        **changes,
    }


def planned(items, settings=None, *, keyboard_enabled=True):
    elements = [
        Element(
            element_id=f"element-{index}",
            selector=item["selector"],
            tag=item["tag"],
            role=item["role"],
            accessible_name=item["accessible_name"],
            frame_path=item.get("frame_path", []),
            shadow_path=item.get("shadow_path", []),
        )
        for index, item in enumerate(items)
    ]
    state = State(
        state_id="source",
        url=ROOT,
        title="Fixture",
        dom_hash="fixture",
        interactive_elements=elements,
    )
    return asyncio.run(
        plan(
            None,
            state,
            items,
            ROOT,
            settings or Crawl(),
            keyboard_enabled=keyboard_enabled,
        )
    )


def test_existing_click_keeps_identifier_and_has_no_extra_keypress():
    steps = planned([control()])
    assert len(steps) == 1
    assert steps[0].action.action_type == "click"
    assert steps[0].action.action_id == digest(["source", "element-0", "click", None])[:20]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("role,key", [("button", "Enter"), ("checkbox", "Space")])
def test_explicit_keyboard_handler_respects_setting_and_widget_key(enabled, role, key):
    steps = planned([control(role=role, keyboard_handler=True)], keyboard_enabled=enabled)
    assert [step.action.action_type for step in steps] == (
        ["click", "keypress"] if enabled else ["click"]
    )
    if enabled:
        assert steps[-1].action.key == key
        assert steps[-1].skip_reason is None
        assert steps[-1].action.action_id != steps[0].action.action_id


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"accessible_name": "Delete account"}, "policy_destructive"),
        ({"submit": True}, "policy_caution"),
        ({"disabled": True}, "disabled_control"),
        ({"inert": True}, "disabled_control"),
        ({"blocked_by_modal": True}, "background_modal_control"),
        ({"input_type": "password"}, "sensitive_control"),
        ({"navigation_hint": "https://outside.test/"}, "policy_external"),
    ],
)
def test_added_interactions_retain_all_control_guards(changes, reason):
    item = control(keyboard_handler=True, hoverable=True, draggable=True, **changes)
    steps = planned([item], Crawl(test_environment=True))
    assert {step.action.action_type for step in steps} == {"click", "keypress", "hover", "drag"}
    assert all(step.skip_reason == reason for step in steps if step.action.action_type != "drag")
    drag = next(step for step in steps if step.action.action_type == "drag")
    assert drag.skip_reason is not None
    assert not drag.action.replayable


@pytest.mark.parametrize("test_environment", [False, True])
def test_canvas_requires_explicit_whole_element_semantics(test_environment):
    settings = Crawl(test_environment=test_environment)
    item = control(tag="canvas", role="", canvas=True, clickable=True)
    skipped = planned([item], settings)[0]
    assert skipped.skip_reason == "canvas_needs_hit_target"
    settings.allowed_action_ids = [skipped.action.action_id]
    assert planned([item], settings)[0].skip_reason == "canvas_needs_hit_target"
    semantic = planned([item | {"role": "button"}], settings)[0]
    assert semantic.skip_reason is None
    assert semantic.action.safety == Safety.SAFE


def test_drag_requires_destination_and_does_not_imply_click():
    item = control(role="", draggable=True, attributes={})
    steps = planned([item], Crawl(test_environment=True))
    assert len(steps) == 1
    assert steps[0].action.action_type == "drag"
    assert steps[0].skip_reason == "drag_needs_target"
    assert not steps[0].action.replayable
    settings = Crawl(test_environment=True, allowed_action_ids=[steps[0].action.action_id])
    assert planned([item], settings)[0].skip_reason == "drag_needs_target"


def test_scoped_links_remain_distinct_and_use_their_own_document():
    items = [
        control(tag="a", role="link", href=ROOT + "details", **scope)
        for scope in ({}, {"frame_path": ["iframe"]}, {"shadow_path": ["#host"]})
    ]
    steps = planned(items)
    assert len({step.action.action_id for step in steps}) == 3
    assert [step.direct_navigation for step in steps] == [True, False, False]
    assert len({step.expected_identity for step in steps}) == 3


def test_drag_cannot_execute_without_a_target():
    step = planned([control(draggable=True)])[-1]
    crawler = InteractiveCrawler(load_settings(environ={"A11Y_URL": ROOT}), Redactor([]))
    with pytest.raises(ReplayDiverged, match="explicit drop target"):
        asyncio.run(crawler.perform(None, step))


def test_keyboard_cannot_execute_when_disabled():
    step = planned([control(keyboard_handler=True)])[-1]
    settings = load_settings(environ={"A11Y_URL": ROOT})
    settings.keyboard.enabled = False
    crawler = InteractiveCrawler(settings, Redactor([]))
    with pytest.raises(ReplayDiverged, match="Keyboard interaction is disabled"):
        asyncio.run(crawler.perform(None, step))

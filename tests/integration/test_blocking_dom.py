"""Application gates are described from rendered DOM without disclosing input values."""

import asyncio
from dataclasses import asdict

import pytest

from accessibility_agent.browser.browser_manager import ChromiumSession
from accessibility_agent.config.settings import load_settings
from accessibility_agent.interaction.blockers import detect_blockers


def inspect(markup, selectors=(), *, include_setup_forms=False):
    async def run():
        session = ChromiumSession()
        settings = load_settings(environ={"A11Y_URL": "https://example.test/"})
        await session.open(settings)
        try:
            await session.page.set_content(markup)
            blockers = await detect_blockers(
                session.page, selectors, include_setup_forms=include_setup_forms
            )
            for blocker in blockers:
                assert await session.page.locator(blocker.selector).count() == 1
            return blockers
        finally:
            await session.close()

    return asyncio.run(run())


def test_semantic_dialogs_include_labels_choices_and_required_fields():
    blockers = inspect("""
        <section role="dialog" aria-modal="true" aria-labelledby="title">
          <h2 id="title">Choose workspace</h2>
          <label>Workspace <select required><option value="">Choose one</option>
            <option value="private-id">Operations</option><option>Finance</option></select></label>
          <label>Remember workspace <input type="checkbox"></label>
        </section>
        <section role="alertdialog" aria-modal="true" aria-label="Accept terms">
          <label><input type="checkbox" required> I agree</label>
        </section>
    """)
    assert len(blockers) == 2
    assert blockers[0].title == "Choose workspace"
    assert blockers[0].fields[0].label == "Workspace"
    assert blockers[0].fields[0].required
    assert blockers[0].fields[0].options == ("Choose one", "Operations", "Finance")
    assert blockers[1].fields[0].label == "I agree"
    assert "private-id" not in str(blockers)


def test_custom_centered_domain_tree_is_one_gate():
    blockers = inspect("""
        <nav><a href="/dashboard">Dashboard</a></nav>
        <div class="modal-backdrop" style="position:fixed;inset:0;background:#fff8">
          <div class="domain-dialog"
            style="position:absolute;left:25%;top:25%;width:50%;height:50%">
            <h2>Select a Domain</h2>
            <div role="tree" aria-label="Available domains">
              <div role="treeitem" aria-label="North America">North America
                <div role="group"><div role="treeitem">Operations</div></div>
              </div>
              <div role="treeitem">Europe</div>
            </div>
            <button>Continue</button>
          </div>
        </div>
    """)
    assert len(blockers) == 1
    assert blockers[0].title == "Select a Domain"
    assert blockers[0].fields[0].kind == "tree"
    assert blockers[0].fields[0].options == ("North America", "Operations", "Europe")


@pytest.mark.parametrize("hidden", ["hidden", 'style="display:none"', 'aria-hidden="true"'])
def test_hidden_dialogs_and_normal_navigation_dropdowns_are_not_gates(hidden):
    assert (
        inspect(f"""
        <div {hidden}><section role="dialog" aria-modal="true"><input></section></div>
        <nav><button aria-expanded="true">Account</button>
          <div role="menu"><a role="menuitem" href="/profile">Profile</a></div>
        </nav>
        <label>View <select><option>Table</option><option>Cards</option></select></label>
        <div role="listbox"><div role="option">Active</div></div>
    """)
        == []
    )


def test_required_setup_form_without_app_navigation_is_a_gate():
    blockers = inspect(
        """
            <h1>Welcome</h1><form><h2>Set up your account</h2>
              <label>Organization name <input required></label><button>Continue</button>
            </form><a href="/privacy">Privacy policy</a>
        """,
        include_setup_forms=True,
    )
    assert len(blockers) == 1
    assert blockers[0].kind == "setup_form"
    assert blockers[0].fields[0].label == "Organization name"


def test_required_form_alongside_app_navigation_and_search_are_not_gates():
    assert (
        inspect("""
        <nav><a href="/orders">Orders</a></nav>
        <form><label>Customer name <input required></label><button>Save</button></form>
    """)
        == []
    )
    assert (
        inspect("""
        <form><label>Search orders <input type="search" required></label>
          <button>Search</button></form>
    """)
        == []
    )


def test_completed_required_form_does_not_need_another_prompt():
    assert (
        inspect("""
        <form><label>Organization <input required value="private-organization"></label></form>
    """)
        == []
    )


def test_entered_values_and_script_content_are_never_returned():
    blockers = inspect("""
        <div role="dialog" aria-modal="true" aria-label="Complete details">
          <label>Username <input value="private-user"></label>
          <label>Password <input type="password" value="private-password"></label>
          <label>Notes <textarea>private-notes</textarea></label>
          <div contenteditable="true" aria-label="Description">private-editable</div>
          <script>window.token = 'private-token'</script>
          <label>Organization <select><option value="private-org-id">Acme</option></select></label>
        </div>
    """)
    summary = str([asdict(blocker) for blocker in blockers])
    assert "private-" not in summary
    assert [field.kind for field in blockers[0].fields] == [
        "text",
        "password",
        "textarea",
        "textbox",
        "select",
    ]


def test_custom_selector_supports_unusual_markup_and_invalid_selectors_are_ignored():
    blockers = inspect(
        """
        <nav><a href="/dashboard">Dashboard</a></nav>
        <section id="account-choice"><h2>Choose environment</h2>
          <label>Environment <input required></label>
        </section>
    """,
        ["[", "#account-choice"],
    )
    assert len(blockers) == 1
    assert blockers[0].title == "Choose environment"


def test_native_dialog_without_fields_still_blocks_and_closed_dialog_does_not():
    blockers = inspect("""
        <dialog open aria-modal="true"><h2>Welcome</h2><button>Continue</button></dialog>
        <dialog><h2>Closed</h2><input></dialog>
    """)
    assert len(blockers) == 1
    assert blockers[0].title == "Welcome"


def test_non_modal_native_dialog_does_not_pause_application():
    assert (
        inspect("""
        <dialog open><h2>Optional tips</h2><button>Dismiss</button></dialog>
        <main><h1>Dashboard</h1><a href="/orders">Orders</a></main>
    """)
        == []
    )


def test_dismissible_modal_is_left_for_normal_crawler_interaction():
    assert (
        inspect("""
        <section role="dialog" aria-modal="true" aria-label="Optional tour">
          <p>Product tips</p><button aria-label="Close tour">Close</button>
        </section><main><h1>Dashboard</h1></main>
    """)
        == []
    )


def test_centered_dialog_trigger_button_is_not_itself_a_dialog():
    assert (
        inspect("""
        <button class="dialog-trigger" style="position:fixed;left:40%;top:40%;width:20%;height:20%">
          Open details
        </button>
    """)
        == []
    )


def test_non_modal_side_panel_with_fields_does_not_pause_application():
    assert (
        inspect("""
        <nav><a href="/dashboard">Dashboard</a></nav>
        <aside role="dialog" aria-label="Optional filters"
          style="position:fixed;right:0;top:0;width:240px;height:100%">
          <label>Customer <input></label><button>Apply</button>
        </aside><main><h1>Dashboard</h1></main>
    """)
        == []
    )


def test_invalid_label_reference_to_editable_text_does_not_disclose_it():
    blockers = inspect("""
        <section role="dialog" aria-modal="true">
          <div id="editable" contenteditable="true">private-entered-notes</div>
          <input aria-labelledby="editable">
        </section>
    """)
    assert "private-entered-notes" not in str(blockers)


def test_combobox_owned_options_and_bounds():
    controls = "".join(f"<label>Field {index}<input></label>" for index in range(50))
    options = "".join(f'<div role="option">Choice {index}</div>' for index in range(50))
    blockers = inspect(f"""
        <section role="dialog" aria-modal="true" aria-label="Organization">
          <div role="combobox" aria-label="Organization" aria-controls="choices"><input></div>
          <div id="choices" role="listbox">{options}</div>{controls}
        </section>
    """)
    assert len(blockers[0].fields) == 30
    assert len(blockers[0].fields[0].options) == 30
    assert blockers[0].fields[0].kind == "combobox"

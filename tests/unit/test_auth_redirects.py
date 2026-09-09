from urllib.parse import urlencode

import pytest

from accessibility_agent.authentication.redirects import authorization_origin


def authorization_url(**overrides):
    query = {
        "client_id": "fixture-client",
        "response_type": "code",
        "redirect_uri": "https://app.example.test/auth/callback",
        **overrides,
    }
    return "https://identity.example.test/tenant/oauth2/v2.0/authorize?" + urlencode(query)


@pytest.mark.parametrize("response_type", ["code", "id_token", "id_token token"])
def test_standard_auth_redirect_returns_to_application(response_type):
    assert (
        authorization_origin(
            authorization_url(response_type=response_type), "https://app.example.test/"
        )
        == "https://identity.example.test"
    )


@pytest.mark.parametrize(
    "url",
    [
        authorization_url(redirect_uri="https://unrelated.example.test/callback"),
        authorization_url(redirect_uri="https://app.example.test.evil.test/callback"),
        authorization_url(redirect_uri="http://app.example.test/callback"),
        authorization_url(redirect_uri="javascript:alert(1)"),
        authorization_url(redirect_uri="https://user:secret@app.example.test/callback"),
        authorization_url(client_id=""),
        authorization_url(response_type="invalid"),
        authorization_url() + "&redirect_uri=https://unrelated.example.test/",
        authorization_url().replace("https://identity", "http://identity"),
        authorization_url().replace("/authorize?", "/delete?"),
        "https://identity.example.test/login",
    ],
)
def test_unrelated_or_invalid_redirect_is_not_trusted(url):
    assert authorization_origin(url, "https://app.example.test/") is None

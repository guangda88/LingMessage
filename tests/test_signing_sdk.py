"""signing_sdk tests."""

import pytest
from lingmessage.signing_sdk import (
    sign_payload,
    verify_payload,
    sign_request,
    verify_request,
    _get_key,
)


TEST_KEY = "test-secret-key-for-unit-tests"


class TestSignPayload:
    def test_basic_sign(self):
        sig = sign_payload("zhibridge", '{"id":123}', TEST_KEY)
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_different_senders_different_sig(self):
        sig1 = sign_payload("zhibridge", "same payload", TEST_KEY)
        sig2 = sign_payload("lingyang", "same payload", TEST_KEY)
        assert sig1 != sig2

    def test_different_payloads_different_sig(self):
        sig1 = sign_payload("zhibridge", '{"id":1}', TEST_KEY)
        sig2 = sign_payload("zhibridge", '{"id":2}', TEST_KEY)
        assert sig1 != sig2

    def test_deterministic(self):
        sig1 = sign_payload("zhibridge", "payload", TEST_KEY)
        sig2 = sign_payload("zhibridge", "payload", TEST_KEY)
        assert sig1 == sig2


class TestVerifyPayload:
    def test_valid_signature(self):
        sig = sign_payload("zhibridge", '{"id":123}', TEST_KEY)
        assert verify_payload("zhibridge", '{"id":123}', sig, TEST_KEY) is True

    def test_tampered_payload(self):
        sig = sign_payload("zhibridge", '{"id":123}', TEST_KEY)
        assert verify_payload("zhibridge", '{"id":999}', sig, TEST_KEY) is False

    def test_wrong_sender(self):
        sig = sign_payload("zhibridge", "payload", TEST_KEY)
        assert verify_payload("attacker", "payload", sig, TEST_KEY) is False

    def test_wrong_key(self):
        sig = sign_payload("zhibridge", "payload", TEST_KEY)
        assert verify_payload("zhibridge", "payload", sig, "wrong-key") is False

    def test_empty_signature(self):
        assert verify_payload("zhibridge", "payload", "", TEST_KEY) is False


class TestSignRequest:
    def test_post_sign(self):
        sig = sign_request("zhibridge", "POST", "/api/decisions/email", '{"id":1}', TEST_KEY)
        assert len(sig) == 64

    def test_method_case_insensitive(self):
        sig1 = sign_request("zhibridge", "post", "/api/test", "", TEST_KEY)
        sig2 = sign_request("zhibridge", "POST", "/api/test", "", TEST_KEY)
        assert sig1 == sig2

    def test_different_methods_different_sig(self):
        sig_get = sign_request("zhibridge", "GET", "/api/test", "", TEST_KEY)
        sig_post = sign_request("zhibridge", "POST", "/api/test", "", TEST_KEY)
        assert sig_get != sig_post

    def test_verify_request(self):
        sig = sign_request("zhibridge", "POST", "/api/email", '{"id":1}', TEST_KEY)
        assert verify_request("zhibridge", "POST", "/api/email", '{"id":1}', sig, TEST_KEY)

    def test_verify_request_tampered_path(self):
        sig = sign_request("zhibridge", "POST", "/api/email", '{"id":1}', TEST_KEY)
        assert verify_request("zhibridge", "POST", "/api/admin", '{"id":1}', sig, TEST_KEY) is False


class TestGetKey:
    def test_explicit_key(self):
        assert _get_key("explicit") == "explicit"

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("LINGMESSAGE_SIGNING_KEY", "from-env")
        assert _get_key() == "from-env"

    def test_no_key_raises(self, monkeypatch):
        monkeypatch.delenv("LINGMESSAGE_SIGNING_KEY", raising=False)
        with pytest.raises(ValueError, match="签名密钥未设置"):
            _get_key()

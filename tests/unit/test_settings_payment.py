"""Tests for Task 2: PaymentSettings, LambdaProyectSettings, ChatwootSettings.webhook_secret.

TDD RED: Written before implementation.
"""

from __future__ import annotations

import os


def test_payment_settings_cartera_allowlist_parses_csv() -> None:
    """cartera_phone_allowlist filters to E.164 entries only."""
    os.environ["CARTERA_PHONE_ALLOWLIST"] = "+573001,+573002, bad,"
    os.environ["LAMBDA_PROYECT_INTERNAL_TOKEN"] = "x"
    os.environ["CHATWOOT_WEBHOOK_SECRET"] = "y"

    from app.config.settings import PaymentSettings  # type: ignore[attr-defined]

    s = PaymentSettings()
    result = s.cartera_phone_allowlist
    assert result == frozenset({"+573001", "+573002"}), f"Got {result!r}"


def test_payment_settings_empty_allowlist() -> None:
    """Empty CARTERA_PHONE_ALLOWLIST returns empty frozenset."""
    os.environ["CARTERA_PHONE_ALLOWLIST"] = ""
    os.environ["LAMBDA_PROYECT_INTERNAL_TOKEN"] = "x"
    os.environ["CHATWOOT_WEBHOOK_SECRET"] = "y"

    from app.config.settings import PaymentSettings  # type: ignore[attr-defined]

    s = PaymentSettings()
    assert s.cartera_phone_allowlist == frozenset()


def test_payment_settings_volume_path_default() -> None:
    """volume_path defaults to /data/comprobantes."""
    os.environ.setdefault("CARTERA_PHONE_ALLOWLIST", "")
    os.environ["LAMBDA_PROYECT_INTERNAL_TOKEN"] = "x"
    os.environ["CHATWOOT_WEBHOOK_SECRET"] = "y"
    os.environ.pop("PAYMENT_VOLUME_PATH", None)

    from app.config.settings import PaymentSettings  # type: ignore[attr-defined]

    s = PaymentSettings()
    assert str(s.volume_path) == "/data/comprobantes"


def test_payment_settings_template_name_default() -> None:
    """template_no_answer_name defaults to voice_no_answer_followup."""
    os.environ.setdefault("CARTERA_PHONE_ALLOWLIST", "")
    os.environ["LAMBDA_PROYECT_INTERNAL_TOKEN"] = "x"
    os.environ["CHATWOOT_WEBHOOK_SECRET"] = "y"
    os.environ.pop("META_TEMPLATE_NO_ANSWER_NAME", None)

    from app.config.settings import PaymentSettings  # type: ignore[attr-defined]

    s = PaymentSettings()
    assert s.template_no_answer_name == "voice_no_answer_followup"


def test_lambda_proyect_settings_internal_token_required() -> None:
    """LAMBDA_PROYECT_INTERNAL_TOKEN is required (no default)."""
    import pydantic

    os.environ.pop("LAMBDA_PROYECT_INTERNAL_TOKEN", None)

    from app.config.settings import LambdaProyectSettings  # type: ignore[attr-defined]

    try:
        LambdaProyectSettings()
        raise AssertionError("Should have raised ValidationError")
    except pydantic.ValidationError:
        pass  # expected


def test_lambda_proyect_settings_internal_token_is_secret() -> None:
    """internal_token is a SecretStr (not plain str)."""
    from pydantic import SecretStr

    os.environ["LAMBDA_PROYECT_INTERNAL_TOKEN"] = "my-secret"

    from app.config.settings import LambdaProyectSettings  # type: ignore[attr-defined]

    s = LambdaProyectSettings()
    assert isinstance(s.internal_token, SecretStr)
    assert s.internal_token.get_secret_value() == "my-secret"


def test_chatwoot_settings_has_webhook_secret() -> None:
    """ChatwootSettings now has webhook_secret as SecretStr."""
    from pydantic import SecretStr

    os.environ["CHATWOOT_WEBHOOK_SECRET"] = "cw-secret-abc"

    from app.config.settings import ChatwootSettings

    s = ChatwootSettings()
    assert isinstance(s.webhook_secret, SecretStr)
    assert s.webhook_secret.get_secret_value() == "cw-secret-abc"


def test_settings_has_payment_and_lambda_fields() -> None:
    """Composite Settings has .payment and .lambda_proyect sub-settings."""
    os.environ["CARTERA_PHONE_ALLOWLIST"] = "+573001,+573002"
    os.environ["LAMBDA_PROYECT_INTERNAL_TOKEN"] = "tok"
    os.environ["CHATWOOT_WEBHOOK_SECRET"] = "cw"

    from app.config.settings import Settings  # noqa: F401

    s = Settings()
    assert hasattr(s, "payment"), "Settings missing .payment"
    assert hasattr(s, "lambda_proyect"), "Settings missing .lambda_proyect"
    assert s.payment.cartera_phone_allowlist == frozenset({"+573001", "+573002"})


def test_qastate_has_payment_fields() -> None:
    """QAState TypedDict has all 6 new payment-flow fields."""
    from app.features.qa.state import QAState

    hints = QAState.__annotations__
    expected = {
        "case_id",
        "attachment_count",
        "attachment_idx",
        "payment_status",
        "cartera_message_wamid",
        "payment_approved",
    }
    missing = expected - hints.keys()
    assert not missing, f"QAState missing payment fields: {missing}"


def test_qastate_node_literal_has_payment_nodes() -> None:
    """QAState.node Literal must include all 5 payment node names."""
    import typing

    from app.features.qa.state import QAState

    node_annotation = QAState.__annotations__["node"]
    # Unwrap NotRequired if needed
    args = typing.get_args(node_annotation)
    # The Literal can be nested inside NotRequired
    # Collect all literal values recursively
    literal_values: set[str] = set()

    def _collect(tp: object) -> None:
        for a in typing.get_args(tp):
            if isinstance(a, str):
                literal_values.add(a)
            else:
                _collect(a)

    _collect(node_annotation)
    # Also check direct args
    for a in args:
        if isinstance(a, str):
            literal_values.add(a)

    expected_payment_nodes = {
        "node_receive_comprobante",
        "node_forward_to_cartera",
        "node_awaiting_cartera",
        "node_confirming",
        "node_payment_escalate",
    }
    missing = expected_payment_nodes - literal_values
    assert not missing, f"QAState.node missing payment nodes: {missing}"

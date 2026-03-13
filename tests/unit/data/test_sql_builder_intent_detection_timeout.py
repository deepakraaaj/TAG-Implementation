from unittest.mock import patch

from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode


def test_intent_detection_timeout_uses_configured_setting():
    with patch("app.assistant.nodes.sql.sql_builder_node.settings.INTENT_DETECTION_TIMEOUT_SECONDS", 1.25):
        assert SQLBuilderNode._intent_detection_timeout_seconds() == 1.25


def test_intent_detection_timeout_has_safe_floor():
    with patch("app.assistant.nodes.sql.sql_builder_node.settings.INTENT_DETECTION_TIMEOUT_SECONDS", 0):
        assert SQLBuilderNode._intent_detection_timeout_seconds() == 2.0

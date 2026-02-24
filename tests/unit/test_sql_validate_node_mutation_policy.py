from app.assistant.nodes.sql_validate_node import SQLValidateNode


def test_mutation_policy_override_parses_truthy_values():
    node = SQLValidateNode()
    assert node._mutation_policy_override({"allow_mutations": True}) is True
    assert node._mutation_policy_override({"allow_mutations": "true"}) is True
    assert node._mutation_policy_override({"allow_mutations": "yes"}) is True


def test_mutation_policy_override_parses_falsy_values():
    node = SQLValidateNode()
    assert node._mutation_policy_override({"allow_mutations": False}) is False
    assert node._mutation_policy_override({"allow_mutations": "false"}) is False
    assert node._mutation_policy_override({"allow_mutations": "0"}) is False


def test_mutation_policy_override_returns_none_when_not_set():
    node = SQLValidateNode()
    assert node._mutation_policy_override({}) is None

from app.assistant.nodes.sql_validate_node import SQLValidateNode


def test_mutation_policy_override_parses_truthy_values():
    node = SQLValidateNode()
    assert node._parse_allow_mutations_flag({"allow_mutations": True}) is True
    assert node._parse_allow_mutations_flag({"allow_mutations": "true"}) is True
    assert node._parse_allow_mutations_flag({"allow_mutations": "yes"}) is True


def test_mutation_policy_override_parses_falsy_values():
    node = SQLValidateNode()
    assert node._parse_allow_mutations_flag({"allow_mutations": False}) is False
    assert node._parse_allow_mutations_flag({"allow_mutations": "false"}) is False
    assert node._parse_allow_mutations_flag({"allow_mutations": "0"}) is False


def test_mutation_policy_override_returns_none_when_not_set():
    node = SQLValidateNode()
    assert node._parse_allow_mutations_flag({}) is None


def test_mutation_requires_explicit_permission_and_allowed_role():
    node = SQLValidateNode()
    node.allowed_mutation_roles = {"admin"}
    node.require_explicit_mutation_permission = True

    assert node._mutation_policy_override({"allow_mutations": True, "user_role": "admin"}, is_mutation=True) is True
    assert node._mutation_policy_override({"allow_mutations": True, "user_role": "user"}, is_mutation=True) is False
    assert node._mutation_policy_override({"user_role": "admin"}, is_mutation=True) is False


def test_mutation_policy_accepts_role_from_role_key():
    node = SQLValidateNode()
    node.allowed_mutation_roles = {"manager"}
    node.require_explicit_mutation_permission = True

    assert node._mutation_policy_override({"allow_mutations": "true", "role": "manager"}, is_mutation=True) is True

"""Tests for domain generation semantic clarification features."""
import pytest


class TestParseEnumAnswer:
    """Test the _parse_enum_answer static method."""

    @staticmethod
    def _parse(raw: str):
        from tools.domain_onboarding.generator import DomainGenerationService
        return DomainGenerationService._parse_enum_answer(raw)

    def test_basic_parse(self):
        result = self._parse("0=Pending, 1=In Progress, 2=Completed")
        assert result == {0: "Pending", 1: "In Progress", 2: "Completed"}

    def test_empty_string(self):
        assert self._parse("") == {}

    def test_none_value(self):
        assert self._parse(None) == {}

    def test_no_equals(self):
        assert self._parse("Pending, In Progress, Completed") == {}

    def test_single_value(self):
        assert self._parse("0=Open") == {0: "Open"}

    def test_trailing_comma(self):
        result = self._parse("0=Pending, 1=Done,")
        assert result == {0: "Pending", 1: "Done"}

    def test_non_integer_key_skipped(self):
        result = self._parse("abc=Pending, 1=Done")
        assert result == {1: "Done"}

    def test_whitespace_handling(self):
        result = self._parse("  0 = Pending ,  1 = In Progress  ")
        assert result == {0: "Pending", 1: "In Progress"}


class TestGenerateEnumsPy:
    """Test the _generate_enums_py class method."""

    @staticmethod
    def _generate(enum_hints):
        from tools.domain_onboarding.generator import DomainGenerationService
        return DomainGenerationService._generate_enums_py(enum_hints)

    def test_empty_hints_returns_stub(self):
        result = self._generate({})
        assert "ENUM_MAPPINGS = {}" in result
        assert "ENUM_LABELS = {}" in result

    def test_none_hints_returns_stub(self):
        result = self._generate(None)
        assert "ENUM_MAPPINGS = {}" in result

    def test_generates_real_enums(self):
        result = self._generate({"status": "0=Pending, 1=In Progress, 2=Completed"})
        assert '"pending": 0,' in result
        assert '"in_progress": 1,' in result
        assert '"completed": 2,' in result
        assert '0: "Pending",' in result
        assert '1: "In Progress",' in result
        assert '2: "Completed",' in result

    def test_generates_from_dict_format(self):
        result = self._generate({"status": {0: "Open", 1: "Closed"}})
        assert '"open": 0,' in result
        assert '"closed": 1,' in result
        assert '0: "Open",' in result
        assert '1: "Closed",' in result

    def test_multiple_columns(self):
        result = self._generate({
            "status": "0=Open, 1=Closed",
            "priority": "0=Low, 1=Medium, 2=High",
        })
        assert '"status"' in result
        assert '"priority"' in result
        assert '"low": 0,' in result
        assert '"high": 2,' in result


class TestDetectEnumCandidateColumns:
    """Test the _detect_enum_candidate_columns class method."""

    @staticmethod
    def _detect(table):
        from tools.domain_onboarding.generator import DomainGenerationService
        return DomainGenerationService._detect_enum_candidate_columns(table)

    def test_finds_status_column(self):
        table = {
            "name": "task_transaction",
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "status", "type": "INTEGER"},
                {"name": "title", "type": "VARCHAR(200)"},
            ],
        }
        result = self._detect(table)
        assert len(result) == 1
        assert result[0]["name"] == "status"

    def test_finds_suffix_columns(self):
        table = {
            "name": "work_order",
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "facility_status", "type": "TINYINT"},
                {"name": "task_type", "type": "SMALLINT"},
            ],
        }
        result = self._detect(table)
        names = [c["name"] for c in result]
        assert "facility_status" in names
        assert "task_type" in names

    def test_ignores_varchar_columns(self):
        table = {
            "name": "task",
            "columns": [
                {"name": "status", "type": "VARCHAR(50)"},
            ],
        }
        result = self._detect(table)
        assert len(result) == 0

    def test_ignores_non_enum_int_columns(self):
        table = {
            "name": "task",
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "user_count", "type": "INTEGER"},
            ],
        }
        result = self._detect(table)
        assert len(result) == 0


class TestStatusBucketsFromEnum:
    """Test the _status_buckets_from_enum class method."""

    @staticmethod
    def _buckets(enum_hints, status_column):
        from tools.domain_onboarding.generator import DomainGenerationService
        return DomainGenerationService._status_buckets_from_enum(enum_hints, status_column)

    def test_generates_buckets(self):
        result = self._buckets(
            {"status": "0=Pending, 1=In Progress, 2=Completed"},
            "status",
        )
        assert len(result) == 3
        assert result[0]["key"] == "pending"
        assert result[0]["label"] == "Pending"
        assert "0" in result[0]["values"]
        assert "pending" in result[0]["values"]

    def test_empty_when_column_not_found(self):
        result = self._buckets({"status": "0=Open"}, "priority")
        assert result == []

    def test_empty_when_no_hints(self):
        result = self._buckets({}, "status")
        assert result == []

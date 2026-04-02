from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode
from app.domains.registry import DomainRegistry


def test_normalized_filters_do_not_infer_assignee_from_for_phrase():
    query = "today's tasks for Ele unit _G Floor_Warehouse facility"
    filters = SQLBuilderNode._normalized_user_filters({}, query)
    assert "assignee" not in filters


def test_normalized_filters_clear_user_filters_for_all_users_phrase():
    query = "today's tasks for Ele unit _G Floor_Warehouse facility for all user"
    filters = SQLBuilderNode._normalized_user_filters({"assignee": "Ele"}, query)
    assert "assignee" not in filters
    assert "assigned_user_id" not in filters


def test_requests_all_users_phrase_detection():
    assert SQLBuilderNode._requests_all_users("for all users")
    assert SQLBuilderNode._requests_all_users("show tasks for everyone")
    assert not SQLBuilderNode._requests_all_users("assigned to nirmala")


def test_task_autorun_context_all_users_facility_date():
    filters = {"scheduled_date": "today", "facility_name": "Ele unit _G Floor_Warehouse"}
    assert SQLBuilderNode._has_task_autorun_context(filters)


def test_task_autorun_context_assignee_and_date():
    filters = {"scheduled_date": "today", "assignee": "Nirmala S"}
    assert SQLBuilderNode._has_task_autorun_context(filters)


def test_requests_self_tasks_detection():
    assert SQLBuilderNode._requests_self_tasks("show my tasks today")
    assert SQLBuilderNode._requests_self_tasks("tasks for me")
    assert not SQLBuilderNode._requests_self_tasks("task status today")


def test_normalized_filters_extracts_assignee_from_tasks_for_name():
    filters = SQLBuilderNode._normalized_user_filters({}, "tasks for Nirmala today")
    assert filters.get("assignee") == "Nirmala"


def test_normalized_filters_extracts_assignee_with_explicit_iso_date_clause():
    filters = SQLBuilderNode._normalized_user_filters({}, "show pending tasks for Nirmala dated on 2026-01-30")
    assert filters.get("assignee") == "Nirmala"
    assert filters.get("scheduled_date") == "2026-01-30"


def test_normalized_filters_extracts_assignee_for_me():
    filters = SQLBuilderNode._normalized_user_filters({}, "tasks for me")
    assert filters.get("assignee") == "me"


def test_normalized_filters_do_not_infer_assignee_from_dont_contraction():
    filters = SQLBuilderNode._normalized_user_filters({}, "which user don't have task today")
    assert "assignee" not in filters
    assert filters.get("scheduled_date") == "today"


def test_today_your_tasks_option_parses_current_user_alias():
    filters = SQLBuilderNode._normalized_user_filters({}, "scheduled_date=today, assigned_to=current_user")
    assert filters.get("scheduled_date") == "today"
    assert filters.get("assigned_to") == "current_user"


def test_vts_normalized_filters_do_not_treat_mapping_alias_as_user_filter():
    with DomainRegistry.use_domain("vts"):
        filters = SQLBuilderNode._normalized_user_filters({}, "show user location mappings")
    assert "assignee" not in filters
    assert "user" not in filters


class _VehicleCatalog:
    @staticmethod
    def important_columns(_table):
        return {"vehicle_number", "company_id", "vehicle_number"}


class _VehicleBuilder:
    def __init__(self):
        self.catalog = _VehicleCatalog()


def test_vts_natural_language_filter_extracts_vehicle_number():
    node = SQLBuilderNode(sql_builder=_VehicleBuilder())
    with DomainRegistry.use_domain("vts"):
        filters = node._augment_explicit_filters_from_query(
            "vehicle",
            {},
            "Show vehicle details for vehicle number TN55AB1234",
        )
    assert filters.get("vehicle_number") == "TN55AB1234"


class _ContactInformationCatalog:
    @staticmethod
    def important_columns(_table):
        return {
            "application_code",
            "company_id",
            "country_state_id",
            "created_by",
            "customer_support_mobile_number",
            "date_created",
            "date_updated",
            "electronics_tech_support_mobile_number",
        }

    @staticmethod
    def table_meta(_table):
        return {
            "aliases": ["contact information", "contact informations"],
        }


class _ContactInformationBuilder:
    def __init__(self):
        self.catalog = _ContactInformationCatalog()


class _MappingCatalog:
    @staticmethod
    def important_columns(_table):
        return set()

    @staticmethod
    def table_names():
        return {"user", "location", "user_location_mapping"}

    @staticmethod
    def aliases(table):
        labels = {
            "user": ["user", "users"],
            "location": ["location", "locations"],
            "user_location_mapping": ["user location mapping", "user location mappings"],
        }
        return labels.get(table, [table])

    @staticmethod
    def table_meta(table):
        return {"aliases": _MappingCatalog.aliases(table)}

    @staticmethod
    def resolve_table_from_query(query):
        if "location" in str(query or "").lower():
            return "user_location_mapping"
        return ""


class _MappingBuilder:
    def __init__(self):
        self.catalog = _MappingCatalog()


def test_vts_contact_information_extracts_person_name_from_for_clause():
    node = SQLBuilderNode(sql_builder=_ContactInformationBuilder())
    with DomainRegistry.use_domain("vts"):
        filters = node._augment_explicit_filters_from_query(
            "contact_information",
            {},
            "What contact information is available for Meenakshi",
        )

    assert filters.get("assignee") == "Meenakshi"


def test_vts_what_query_counts_as_explicit_list_request_for_resolved_table():
    node = SQLBuilderNode()
    with DomainRegistry.use_domain("vts"):
        assert node._is_explicit_list_request(
            "What contact information is available for Meenakshi",
            resolved_table="contact_information",
        )


def test_vts_table_alias_counts_as_explicit_table_mention():
    node = SQLBuilderNode(sql_builder=_ContactInformationBuilder())
    with DomainRegistry.use_domain("vts"):
        assert node._query_mentions_explicit_table(
            "What contact information is available for Meenakshi",
            resolved_table="contact_information",
        )


def test_vts_mapping_queries_prefer_specific_mapping_table_over_user_intent():
    node = SQLBuilderNode(sql_builder=_MappingBuilder())
    with DomainRegistry.use_domain("vts"):
        preferred = node._preferred_table(
            "user",
            "user_location_mapping",
            "show user location mappings",
        )

    assert preferred == "user_location_mapping"


def test_vts_mapping_query_uses_catalog_resolution_before_intent_resolution():
    node = SQLBuilderNode(sql_builder=_MappingBuilder())
    with DomainRegistry.use_domain("vts"):
        resolved = node._resolved_table_from_query("show user location mappings")

    assert resolved == "user_location_mapping"


def test_vts_contact_information_filter_options_use_table_date_key_and_human_title():
    node = SQLBuilderNode(sql_builder=_ContactInformationBuilder())
    with DomainRegistry.use_domain("vts"):
        options = node._generate_dynamic_filter_options("contact_information")
        payload = node._filter_prompt_payload("contact_information", ["application_code", "created_by"])

    assert options[0]["value"] == "date_created=today"
    assert options[1]["value"] == "date_created=yesterday"
    assert payload["ui"]["title"] == "Add filters for contact information"

from app.services.reporting.export_service import ExportService


def test_generate_filename_sanitizes_report_name(tmp_path):
    service = ExportService(max_rows=10, temp_dir=tmp_path)

    filename = service.generate_filename("../Quarterly Sales/../../", "csv", 7)

    assert filename.startswith("quarterly_sales_7_")
    assert filename.endswith(".csv")
    assert "/" not in filename
    assert ".." not in filename


def test_generate_filename_is_unique_per_call(tmp_path):
    service = ExportService(max_rows=10, temp_dir=tmp_path)

    first = service.generate_filename("Quarterly Sales", "csv", 7)
    second = service.generate_filename("Quarterly Sales", "csv", 7)

    assert first != second

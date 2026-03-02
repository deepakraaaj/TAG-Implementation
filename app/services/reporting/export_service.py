"""Export service for CSV and Excel report downloads."""
from __future__ import annotations

from copy import copy
from datetime import datetime
import logging
import os
from pathlib import Path
import re
from typing import List, Dict, Any
import uuid
import pandas as pd

logger = logging.getLogger(__name__)


class ExportService:
    """
    Service for exporting report results to CSV and Excel.
    
    Features:
    - CSV export (lightweight)
    - Excel export with formatting
    - Automatic filename generation
    - Row limit enforcement
    """

    def __init__(self, max_rows: int, temp_dir: Path):
        self.max_rows = max(1, int(max_rows))
        self.temp_dir = Path(temp_dir).expanduser()
        self._ensure_temp_dir()

    @staticmethod
    def _safe_slug(value: str, default: str = "report") -> str:
        candidate = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip().lower())
        candidate = re.sub(r"_+", "_", candidate).strip("._-")
        return candidate or default

    @staticmethod
    def _safe_sheet_name(value: str) -> str:
        candidate = re.sub(r"[\[\]:*?/\\\\]+", " ", str(value or "").strip())
        candidate = re.sub(r"\s+", " ", candidate).strip().strip("'")
        return (candidate or "Report")[:31]

    def _ensure_temp_dir(self) -> None:
        """Create temp directory if it doesn't exist."""
        if self.temp_dir.exists() and not self.temp_dir.is_dir():
            raise NotADirectoryError(f"{self.temp_dir} is not a directory")
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.exception("Failed to create temp directory %s", self.temp_dir)
            raise

    def generate_filename(
        self,
        report_name: str,
        file_format: str,
        company_id: int,
    ) -> str:
        """
        Generate filename for export.
        
        Format: {report_name}_{company_id}_{timestamp}.{ext}
        """
        safe_name = self._safe_slug(report_name)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        suffix = uuid.uuid4().hex[:8]
        ext = "csv" if str(file_format).strip().lower() == "csv" else "xlsx"
        return f"{safe_name}_{company_id}_{timestamp}_{suffix}.{ext}"

    def export_to_csv(
        self,
        results: List[Dict[str, Any]],
        report_name: str,
        company_id: int
    ) -> str:
        """
        Export results to CSV file.
        
        Args:
            results: List of dictionaries (query results)
            report_name: Human-readable report name
            company_id: Company identifier
            
        Returns:
            Absolute path to generated CSV file
        """
        if not results:
            raise ValueError("No data to export")

        # Enforce row limit
        if len(results) > self.max_rows:
            logger.warning("Export truncated: %s rows > %s limit", len(results), self.max_rows)
            results = results[:self.max_rows]

        try:
            # Convert to DataFrame
            df = pd.DataFrame(results)
            
            # Generate filename
            filename = self.generate_filename(report_name, "csv", company_id)
            filepath = (self.temp_dir / filename).resolve()
            
            # Export to CSV
            df.to_csv(filepath, index=False, encoding="utf-8")

            logger.info("Exported %s rows to CSV %s", len(results), filepath)
            return str(filepath)

        except Exception:
            logger.exception("CSV export failed for report_name=%s company_id=%s", report_name, company_id)
            raise

    def export_to_excel(
        self,
        results: List[Dict[str, Any]],
        report_name: str,
        company_id: int
    ) -> str:
        """
        Export results to Excel file with formatting.
        
        Args:
            results: List of dictionaries (query results)
            report_name: Human-readable report name
            company_id: Company identifier
            
        Returns:
            Absolute path to generated Excel file
        """
        if not results:
            raise ValueError("No data to export")

        # Enforce row limit
        if len(results) > self.max_rows:
            logger.warning("Export truncated: %s rows > %s limit", len(results), self.max_rows)
            results = results[:self.max_rows]

        try:
            # Convert to DataFrame
            df = pd.DataFrame(results)
            
            # Generate filename
            filename = self.generate_filename(report_name, "excel", company_id)
            filepath = (self.temp_dir / filename).resolve()
            sheet_name = self._safe_sheet_name(report_name)

            # Export to Excel with formatting
            with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
                df.to_excel(
                    writer,
                    sheet_name=sheet_name,
                    index=False,
                )

                # Get worksheet for formatting
                worksheet = writer.sheets[sheet_name]

                # Auto-adjust column widths
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter

                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except Exception:
                            continue

                    adjusted_width = min(max_length + 2, 50)
                    worksheet.column_dimensions[column_letter].width = adjusted_width

                # Bold header row
                for cell in worksheet[1]:
                    header_font = copy(cell.font)
                    header_font.bold = True
                    cell.font = header_font

            logger.info("Exported %s rows to Excel %s", len(results), filepath)
            return str(filepath)

        except Exception:
            logger.exception("Excel export failed for report_name=%s company_id=%s", report_name, company_id)
            raise

    def cleanup_old_files(self, max_age_hours: int = 24) -> None:
        """
        Clean up export files older than max_age_hours.
        
        Args:
            max_age_hours: Maximum age of files to keep (default: 24 hours)
        """
        try:
            now = datetime.utcnow().timestamp()
            max_age_seconds = max(0, int(max_age_hours)) * 3600
            deleted_count = 0
            for file in self.temp_dir.glob("*"):
                if file.is_file():
                    try:
                        file_age = now - file.stat().st_mtime
                        if file_age > max_age_seconds:
                            file.unlink()
                            deleted_count += 1
                    except FileNotFoundError:
                        continue

            if deleted_count > 0:
                logger.info("Cleaned up %s old export files", deleted_count)
        except Exception:
            logger.exception("Export cleanup failed in %s", self.temp_dir)

    def get_file_size_mb(self, filepath: str) -> float:
        """Get file size in MB."""
        try:
            size_bytes = os.path.getsize(filepath)
            return round(size_bytes / 1024 / 1024, 2)
        except Exception:
            logger.exception("Failed to get export file size for %s", filepath)
            return 0.0

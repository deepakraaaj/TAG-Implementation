"""Export service for CSV and Excel report downloads."""
import logging
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
import os

from app.config import get_settings

settings = get_settings()
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

    def __init__(self):
        self.max_rows = settings.EXPORT_MAX_ROWS
        self.temp_dir = Path(settings.EXPORT_TEMP_DIR)
        self._ensure_temp_dir()

    def _ensure_temp_dir(self):
        """Create temp directory if it doesn't exist."""
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create temp directory: {e}")

    def generate_filename(
        self,
        report_name: str,
        format: str,
        company_id: int
    ) -> str:
        """
        Generate filename for export.
        
        Format: {report_name}_{company_id}_{timestamp}.{ext}
        """
        # Sanitize report name
        safe_name = report_name.lower().replace(" ", "_").replace("/", "_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = "csv" if format == "csv" else "xlsx"
        
        return f"{safe_name}_{company_id}_{timestamp}.{ext}"

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
            logger.warning(f"Export truncated: {len(results)} rows > {self.max_rows} limit")
            results = results[:self.max_rows]

        try:
            # Convert to DataFrame
            df = pd.DataFrame(results)
            
            # Generate filename
            filename = self.generate_filename(report_name, "csv", company_id)
            filepath = self.temp_dir / filename
            
            # Export to CSV
            df.to_csv(filepath, index=False, encoding='utf-8')
            
            logger.info(f"Exported {len(results)} rows to CSV: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"CSV export failed: {e}")
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
            logger.warning(f"Export truncated: {len(results)} rows > {self.max_rows} limit")
            results = results[:self.max_rows]

        try:
            # Convert to DataFrame
            df = pd.DataFrame(results)
            
            # Generate filename
            filename = self.generate_filename(report_name, "excel", company_id)
            filepath = self.temp_dir / filename
            
            # Export to Excel with formatting
            with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
                df.to_excel(
                    writer,
                    sheet_name=report_name[:31],  # Excel sheet name limit
                    index=False
                )
                
                # Get worksheet for formatting
                worksheet = writer.sheets[report_name[:31]]
                
                # Auto-adjust column widths
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    
                    adjusted_width = min(max_length + 2, 50)
                    worksheet.column_dimensions[column_letter].width = adjusted_width
                
                # Bold header row
                for cell in worksheet[1]:
                    cell.font = cell.font.copy(bold=True)
            
            logger.info(f"Exported {len(results)} rows to Excel: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Excel export failed: {e}")
            raise

    def cleanup_old_files(self, max_age_hours: int = 24):
        """
        Clean up export files older than max_age_hours.
        
        Args:
            max_age_hours: Maximum age of files to keep (default: 24 hours)
        """
        try:
            now = datetime.now().timestamp()
            max_age_seconds = max_age_hours * 3600
            
            deleted_count = 0
            for file in self.temp_dir.glob("*"):
                if file.is_file():
                    file_age = now - file.stat().st_mtime
                    if file_age > max_age_seconds:
                        file.unlink()
                        deleted_count += 1
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old export files")
                
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")

    def get_file_size_mb(self, filepath: str) -> float:
        """Get file size in MB."""
        try:
            size_bytes = os.path.getsize(filepath)
            return round(size_bytes / 1024 / 1024, 2)
        except Exception as e:
            logger.error(f"Failed to get file size: {e}")
            return 0.0

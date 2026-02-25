import logging
import re
from typing import Any, Callable, Dict
from sqlalchemy import text

from app.services.interfaces import SchemaGateway

logger = logging.getLogger(__name__)

class UserService:
    def __init__(
        self,
        schema_service: SchemaGateway,
        domain_provider: Callable[[], Any],
    ):
        self.schema_service = schema_service
        self.domain_provider = domain_provider

    @staticmethod
    def _safe_identifier(name: str, default: str) -> str:
        candidate = str(name or "").strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
            return candidate
        return default

    def get_user_info(self, user_id: str) -> Dict[str, str]:
        """
        Fetches user details (name) from the database using user_id.
        """
        try:
            # Check if user_id is valid (numeric)
            if not user_id or not str(user_id).isdigit():
                 return {}

            engine = self.schema_service.get_engine_for_url()
            lookup_cfg = self.domain_provider().get_user_lookup_config()
            table = self._safe_identifier(lookup_cfg.get("table"), "user")
            id_column = self._safe_identifier(lookup_cfg.get("id_column"), "id")
            first_name_column = self._safe_identifier(lookup_cfg.get("first_name_column"), "first_name")
            last_name_column = self._safe_identifier(lookup_cfg.get("last_name_column"), "last_name")
            fallback_name = str(lookup_cfg.get("fallback_name", "User")).strip() or "User"
            with engine.connect() as conn:
                stmt = text(
                    "SELECT "
                    f"`{first_name_column}` AS first_name, "
                    f"`{last_name_column}` AS last_name "
                    f"FROM `{table}` WHERE `{id_column}` = :uid LIMIT 1"
                )
                row = conn.execute(stmt, {"uid": int(user_id)}).mappings().first()
                
                if row:
                    first_name = str(row.get("first_name") or "").strip()
                    last_name = str(row.get("last_name") or "").strip()
                    
                    full_name = first_name or fallback_name
                    if last_name:
                        full_name += f" {last_name}"
                        
                    return {"user_name": full_name}
                    
        except Exception as e:
            logger.error(f"Error fetching user info for {user_id}: {e}")
            
        return {}

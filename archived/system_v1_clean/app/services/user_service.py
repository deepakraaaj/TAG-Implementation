import logging
from typing import Optional, Dict
from sqlalchemy import text
from app.services.schema_service import SchemaService

logger = logging.getLogger(__name__)

class UserService:
    def __init__(self):
        self.schema_service = SchemaService()

    def get_user_info(self, user_id: str) -> Dict[str, str]:
        """
        Fetches user details (name) from the database using user_id.
        """
        try:
            # Check if user_id is valid (numeric)
            if not user_id or not str(user_id).isdigit():
                 return {}

            engine = self.schema_service.get_engine_for_url()
            with engine.connect() as conn:
                # Attempt to fetch first_name and last_name
                # varying schemas might have different column names, but first_name is likely based on PersonResolver
                stmt = text("SELECT first_name, last_name FROM user WHERE id = :uid LIMIT 1")
                result = conn.execute(stmt, {"uid": int(user_id)})
                row = result.fetchone()
                
                if row:
                    first_name = row[0]
                    last_name = row[1]
                    
                    full_name = first_name or "User"
                    if last_name:
                        full_name += f" {last_name}"
                        
                    return {"user_name": full_name}
                    
        except Exception as e:
            logger.error(f"Error fetching user info for {user_id}: {e}")
            
        return {}

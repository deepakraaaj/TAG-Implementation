from sqlalchemy import create_engine, inspect, text
import logging
from typing import Any, Dict, List, Set
from ..config import get_settings

logger = logging.getLogger(__name__)

class SchemaService:
    def __init__(self, db_url: str = None):
        self.default_db_url = db_url or get_settings().DATABASE_URL
        self._engine_cache: Dict[str, Any] = {}
        self.schema_cache: Dict[str, List[str]] = {}
        
        # Initialize default engine
        self._get_or_create_engine(self.default_db_url)

    def _get_or_create_engine(self, db_url: str):
        """
        Get cached engine or create a new one for the given URL.
        Handles dialect adjustments (e.g. aiomysql -> mysqlconnector for sync inspection).
        """
        # Normalize URL for inspection if needed (sync driver)
        inspection_url = db_url
        if "aiomysql" in inspection_url:
            inspection_url = inspection_url.replace("mysql+aiomysql", "mysql+mysqlconnector")
            
        if inspection_url in self._engine_cache:
            return self._engine_cache[inspection_url]
            
        try:
            logger.info(f"Creating new DB engine for: {inspection_url.split('@')[-1]}") # Log safe part
            engine = create_engine(inspection_url, pool_recycle=3600)
            self._engine_cache[inspection_url] = engine
            return engine
        except Exception as e:
            logger.error(f"Failed to create engine for {db_url}: {e}")
            raise e

    @property
    def engine(self):
        """Backwards compatibility for default engine access."""
        return self._get_or_create_engine(self.default_db_url)
    
    @property
    def inspector(self):
        """Backwards compatibility for default inspector."""
        return inspect(self.engine)
        
    def get_engine_for_url(self, db_url: str = None):
        """Public accessor for dynamic engine."""
        url = db_url or self.default_db_url
        return self._get_or_create_engine(url)

    def get_schema_hints(self, db_url: str = None) -> str:
        """
        Fetches semantic hints from `ai_schema_note`.
        """
        try:
            engine = self.get_engine_for_url(db_url)
            with engine.connect() as conn:
                # We assume 'answer' contains the description or relevant context for 'table_name'
                # Or we can construct a context: "For questions like '{question}', use table '{table_name}'"
                result = conn.execute(text("SELECT table_name, question, answer FROM ai_schema_note WHERE table_name IS NOT NULL LIMIT 50"))
                rows = result.fetchall()
                
                if not rows:
                    return ""
                
                hints = ["Semantic Hints (Use these to find relevant tables):"]
                for row in rows:
                    hints.append(f"- To answer '{row.question}', check table '{row.table_name}' (Context: {row.answer})")
                
                return "\n".join(hints)
        except Exception as e:
            # logger.warning(f"Failed to fetch schema hints: {e}") 
            # Silent fail is okay as table might not exist in new DB
            return ""

    def get_schema(self, table_names: List[str] = None, db_url: str = None, concise: bool = False) -> str:
        """
        Returns a string representation of the schema.
        If concise=True, returns a compressed format for token optimization.
        """
        try:
            # Check cache first
            cache_key = f"{db_url}_{'concise' if concise else 'full'}_" + (",".join(sorted(table_names)) if table_names else "all")
            
            if not db_url and cache_key in self.schema_cache:
                return self.schema_cache[cache_key]

            engine = self.get_engine_for_url(db_url)
            with engine.connect() as conn:
                inspector = inspect(conn)
                if not table_names:
                    try:
                        table_names = inspector.get_table_names()
                    except Exception as e:
                        logger.error(f"Error fetching table names: {e}")
                        return ""

                schema_text = []
                for table in table_names:
                    try:
                        columns = inspector.get_columns(table)
                        if concise:
                            # Concise Format: table_name(col1:type, col2:type)
                            # Minimize types: VARCHAR -> STR, INTEGER -> INT
                            col_strings = []
                            pk_cols = []
                            try:
                                pk = inspector.get_pk_constraint(table)
                                pk_cols = pk.get('constrained_columns', [])
                            except:
                                pass

                            for col in columns:
                                type_str = str(col['type']).upper()
                                # Simplify types
                                if 'VARCHAR' in type_str or 'TEXT' in type_str:
                                    type_str = 'STR'
                                elif 'INT' in type_str:
                                    type_str = 'INT'
                                elif 'BOOL' in type_str or 'BIT' in type_str:
                                    type_str = 'BOOL'
                                elif 'DATETIME' in type_str:
                                    type_str = 'DATETIME'
                                
                                # Mark PK
                                if col['name'] in pk_cols:
                                    type_str += ",PK"
                                
                                col_strings.append(f"{col['name']}:{type_str}")
                                
                            schema_text.append(f"{table}({', '.join(col_strings)})")
                        else:
                            # Verbose Format
                            col_strings = [f"{col['name']} ({col['type']})" for col in columns]
                            try:
                               pk = inspector.get_pk_constraint(table)
                               pk_cols = pk.get('constrained_columns', [])
                               if pk_cols:
                                   col_strings = [c + " (PK)" if c.split(' ')[0] in pk_cols else c for c in col_strings]
                            except:
                                pass
                            schema_text.append(f"Table: {table}\nColumns: {', '.join(col_strings)}\n")
                            
                    except Exception as e:
                        logger.warning(f"Failed to inspect table {table}: {e}")
                
                final_schema = "\n".join(schema_text)
                
                if not db_url:
                    self.schema_cache[cache_key] = final_schema
                return final_schema

        except Exception as e:
             logger.error(f"Schema generation failed: {e}")
             return ""

    def get_table_columns(self, table_names: List[str], db_url: str = None) -> Dict[str, Set[str]]:
        """Return a mapping of table -> set(column_names) for the given tables."""
        columns_map: Dict[str, Set[str]] = {}
        if not table_names:
            return columns_map

        engine = self.get_engine_for_url(db_url)
        try:
            inspector = inspect(engine)
            for table in table_names:
                try:
                    cols = inspector.get_columns(table)
                    columns_map[table] = {col["name"] for col in cols if col.get("name")}
                except Exception as e:
                    logger.warning(f"Failed to inspect columns for table {table}: {e}")
            return columns_map
        except Exception as e:
            logger.error(f"Failed to inspect table columns: {e}")
            return columns_map

    def get_all_tables(self, db_url: str = None) -> List[str]:
        engine = self.get_engine_for_url(db_url)
        try:
            # Create a fresh inspector for the engine
            inspector = inspect(engine)
            return inspector.get_table_names()
        except:
             return []



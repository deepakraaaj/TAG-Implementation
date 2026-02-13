import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.query_refiner import QueryRefinerService

def test_sql_alias_fix():
    print("Testing SQL Alias Syntax Fix...")
    refiner = QueryRefinerService()
    
    # Malformed SQL with triple-qualification
    malformed_sql = """
    SELECT t1.task_id, t4.scheduled_facility_meta_details.total_task_count 
    FROM task_transaction AS t1 
    JOIN scheduled_facility_meta_details AS t4 ON t1.scheduled_facility_ref_no = t4.scheduled_ref_no
    WHERE t1.company_id = 56942686
    """
    
    # Apply heuristics
    fixed_sql = refiner.apply_ironclad_heuristics(malformed_sql, {}, 56942686)
    
    print(f"Original SQL snippet: ... {malformed_sql.strip().splitlines()[1].strip()} ...")
    print(f"Fixed SQL snippet:    ... {fixed_sql.strip().splitlines()[1].strip()} ...")
    
    # Assertions
    assert "t4.scheduled_facility_meta_details.total_task_count" not in fixed_sql
    assert "t4.total_task_count" in fixed_sql
    assert "t1.task_id" in fixed_sql
    
    print("SQL Alias Syntax fix test PASSED")

if __name__ == "__main__":
    test_sql_alias_fix()

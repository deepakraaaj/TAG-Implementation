from fastapi import APIRouter, BackgroundTasks
import logging
from app.services.vector import vector_service

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/debug/index")
async def index_documents(background_tasks: BackgroundTasks):
    """
    Trigger document indexing (Debug only).
    """
    
    sample_docs = [
        {
            "title": "How to Add a New User",
            "content": "To add a new user to the facility management system: 1. Navigate to the Users section. 2. Click 'Add User' button. 3. Fill in the user details including name, email, role, and department. 4. Assign appropriate permissions. 5. Click 'Save' to create the user account.",
            "metadata": {"category": "user_management", "type": "guide"}
        },
        {
            "title": "Maintenance Request Process",
            "content": "When submitting a maintenance request: 1. Go to the Maintenance section. 2. Click 'New Request'. 3. Select the facility and asset requiring maintenance. 4. Describe the issue in detail. 5. Set priority level (Low, Medium, High, Critical). 6. Attach photos if applicable. 7. Submit the request. The maintenance team will be notified automatically.",
            "metadata": {"category": "maintenance", "type": "process"}
        },
        {
            "title": "Safety Protocols for Chemical Storage",
            "content": "Chemical storage safety guidelines: 1. Store chemicals in designated areas only. 2. Keep incompatible chemicals separated. 3. Ensure proper ventilation in storage areas. 4. Label all containers clearly with contents and hazard warnings. 5. Maintain Material Safety Data Sheets (MSDS) for all chemicals. 6. Use appropriate PPE when handling chemicals. 7. Report any spills or leaks immediately to the safety officer.",
            "metadata": {"category": "safety", "type": "policy"}
        }
    ]
    
    async def run_indexing():
        logger.info("Starting background indexing...")
        for doc in sample_docs:
            try:
                await vector_service.index_document(
                    content=doc['content'],
                    metadata={**doc['metadata'], 'title': doc['title']}
                )
            except Exception as e:
                logger.error(f"Indexing failed for {doc['title']}: {e}")
        logger.info("Background indexing complete.")

    background_tasks.add_task(run_indexing)
    return {"status": "Indexing started", "count": len(sample_docs)}

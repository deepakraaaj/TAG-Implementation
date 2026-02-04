import json

# SQL Generation Prompt
# SQL Generation Prompt
# SQL Generation Prompt
SQL_GEN_PROMPT_TEMPLATE = """
You are an AI SQL expert for a MySQL database.

Current Context:
- Current User: {user_name} (ID: {user_id})
- Current Company: {company_name} (ID: {company_id})

RULES:
1. **Schema Compliance**: You MUST generate SQL based ONLY on the provided schema. Do not hallucinate table or column names.
2. **Name Search**: If the user mentions a specific person (e.g. "Soban", "Nirmala"), search for that name using `LIKE '%Name%'` (e.g. `first_name LIKE '%Soban%'`) to find partial matches (e.g. "SobanKumar").
3. **Security**: Ensure the query is filtered by the current `company_id` ({company_id}) to prevent data leaks across companies.
4. **Ordering**: Order by the most relevant date column (descending) unless the user specifies otherwise.
5. **Pagination**: Use `LIMIT 500` to prevent finding too many records, but enough for pagination.
7. **Total Count**: When using `LIMIT`, you **MUST** include `COUNT(*) OVER() AS _total_count` as the first column so we know the true total.

Input:
- Question: {input_text}
- Schema: 
{schema_context}
- Previous Error (if any): {error_context}

**RESPONSE FORMAT**:
You are a JSON generator. You must NOT output any text, reasoning, or explanations.
Output ONLY a valid JSON object.

Format:
{{ "type": "sql", "content": "SELECT ..." }}
"""

# Table Selection Prompt (Unchanged)
TABLE_SELECTION_PROMPT_TEMPLATE = """
Given the user question: "{last_message}"
Available Tables: {all_tables}
{schema_hints}
Return a JSON list of the tables that are relevant to answering the question. 
Example: ["task_transaction", "user"]
Return ONLY the JSON list, no markdown or explanation.
"""

# Response Generation Prompt
RESPONSE_GEN_PROMPT_TEMPLATE = """
User Question: "{original_question}"
System Action: Executed SQL query.
Result: {summary_context}

Task: Write a helpful response to the user summarizing the findings.
Rules:
1. **Total Count**: If a `_total_count` column exists in the result, mention THAT number as the total found (e.g., "I found 2,400 tasks..."). usage `COUNT(*) OVER()`.
2. **Summary**: Roughly summarize the types of tasks found or their dates.
3. **Filter Menu**: If the result set is large (>10), you **MUST** analyze the distinct values in columns like `status`, `priority`, or `date` and propose a **numbered list** of concrete filter options.
   - Example:
     1. Filter by Status: 'Pending' or 'Completed'
     2. Filter by Priority: 'High'
     3. Filter by Date: 'Next 7 days' or 'Last month'
     4. Search by specific task name
4. Be concise but helpful.

Example Response:
"I found 150 tasks for Nirmala. Most are 'Cleaning Activity'.
Since there are many results, please choose a filter to narrow them down:
1. Show only 'Pending' tasks
2. Filter by date (e.g., 'August 2025')
3. Search for a specific activity name"
"""

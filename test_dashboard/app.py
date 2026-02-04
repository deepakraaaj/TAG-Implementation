import streamlit as st
import json
import requests
import pandas as pd
import base64
import uuid

# --- Config ---
st.set_page_config(page_title="TAG Test Dashboard", layout="wide")

# Session State Init
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state["messages"] = []

# --- Sidebar: User Context ---
st.sidebar.title("Configuration")
user_id = st.sidebar.text_input("User ID", value="11784788")
company_id = st.sidebar.text_input("Company ID", value="56942686")
# Removed User Name input as backend handles it now.

if st.sidebar.button("New Session"):
    st.session_state["session_id"] = str(uuid.uuid4())
    st.session_state["messages"] = []
    st.divider()

st.sidebar.caption(f"Session ID: {st.session_state['session_id']}")

api_base_url = st.sidebar.text_input("API URL", value="http://localhost:8005")

# --- Helper Functions ---
def encode_context(uid, cid):
    data = {"user_id": uid, "company_id": cid, "user_role": "admin"}
    return base64.b64encode(json.dumps(data).encode()).decode()

def parse_ndjson(response):
    for line in response.iter_lines():
        if line:
            yield json.loads(line)

# --- Main Interface ---
st.title("🤖 TAG Backend Test")

# Render History
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        if msg.get("type") == "text":
            st.markdown(msg["content"])
        elif msg.get("type") == "error":
            st.error(msg["content"])
        elif msg.get("type") == "data":
            # Render SQL preview as dataframe
            if "rows" in msg:
                st.dataframe(pd.DataFrame(msg["rows"]))
            # Render metadata/metrics
            if "sql_query" in msg:
                 st.code(msg["sql_query"], language="sql")
        
        if msg.get("debug_payload"):
             debug_payload = msg["debug_payload"]
             
             # Metrics
             cols = st.columns(4)
             usage = debug_payload.get("token_usage", {})
             if usage:
                 cols[0].metric("Input", usage.get("prompt_tokens", 0))
                 cols[1].metric("Output", usage.get("completion_tokens", 0))
             
             toon_data = debug_payload.get("toon")
             if toon_data:
                 cols[2].metric("Toon Savings", toon_data.get("savings", "0%"))
                 cols[3].metric("Payload", f"{toon_data.get('toon_len', 0)} chars")

             with st.expander("🛠️ Raw API Payload (Saved)"):
                  st.json(debug_payload)

# Input
if prompt := st.chat_input("Ask a question..."):
    # Add User Message to history
    st.session_state["messages"].append({"role": "user", "type": "text", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call API via requests (Streaming)
    headers = {
        "Content-Type": "application/json",
        "x-user-context": encode_context(user_id, company_id)
    }
    payload = {
        "session_id": st.session_state["session_id"],
        "message": prompt, 
        "user_id": user_id
    }
    
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        full_text = ""
        sql_data = None
        
        try:
            with requests.post(f"{api_base_url}/chat", headers=headers, json=payload, stream=True) as r:
                if r.status_code != 200:
                    st.error(f"API Error: {r.status_code} - {r.text}")
                else:
                    for item in parse_ndjson(r):
                        if item["type"] == "token":
                            full_text += item["content"]
                            message_placeholder.markdown(full_text + "▌")
                            
                        elif item["type"] == "error":
                            st.error(item.get("message", "Unknown error"))
                            st.session_state["messages"].append({"role": "assistant", "type": "error", "content": item.get("message")})
                            
                        elif item["type"] == "result":
                            # Final result payload
                            message_placeholder.markdown(full_text)
                            
                            # Check for SQL data
                            if item.get("sql") and item["sql"].get("ran"):
                                sql_info = item["sql"]
                                st.caption(f"SQL Executed ({sql_info.get('row_count', 0)} rows)")
                                
                                if sql_info.get("rows_preview"):
                                    # Filter columns for display (No Ids)
                                    df = pd.DataFrame(sql_info["rows_preview"])
                                    
                                    # Helper to filter ids
                                    def is_meaningful(col_name):
                                        lower = col_name.lower()
                                        if lower == 'id': return False
                                        if lower.endswith('_id'): return False
                                        if 'uuid' in lower or 'guid' in lower: return False
                                        return True
                                        
                                    display_cols = [c for c in df.columns if is_meaningful(c)]
                                    if display_cols:
                                        df_display = df[display_cols]
                                    else:
                                        df_display = df # Fallback if everything is filtered
                                    
                                    # Info Bar
                                    total_count = sql_info.get("row_count", 0)
                                    shown_count = len(sql_info["rows_preview"])
                                    st.info(f"Showing {shown_count} of {total_count} records")
                                    
                                    st.dataframe(df_display)
                                    
                                    # Pagination Control
                                    if shown_count < total_count:
                                        if st.button("Load More", key=f"btn_{len(st.session_state['messages'])}"):
                                            # Send a follow-up message to get next page
                                            # We use the previous query context implicitly via chat history
                                            next_page_msg = f"Show the next 15 records for the previous query. (Offset: {shown_count})"
                                            st.session_state["messages"].append({"role": "user", "type": "text", "content": next_page_msg})
                                            st.rerun()

                                    # Save to history
                                    st.session_state["messages"].append({
                                        "role": "assistant", 
                                        "type": "data", 
                                        "rows": sql_info["rows_preview"],
                                        "sql_query": sql_info.get("query"),
                                        "total_count": total_count
                                    })
            
            # Show Raw Payload (Debug)
            with st.expander("🛠️ Raw API Payload"):
                 st.subheader("Request")
                 st.json(payload)
                 
                 st.subheader("Response (Combined)")
                 # Synthesize a complete response object for visibility
                 debug_response = item if 'item' in locals() else {}
                 if full_text:
                     debug_response["_generated_message"] = full_text
                 st.json(debug_response)
        
            # Finalize message text in history
            st.session_state["messages"].append({"role": "assistant", "type": "text", "content": full_text, "debug_payload": debug_response})
            
            # Show Metrics
            if debug_response:
                cols = st.columns(4)
                
                # Token Metrics
                usage = debug_response.get("token_usage", {})
                if usage:
                    cols[0].metric("Input Tokens", usage.get("prompt_tokens", 0))
                    cols[1].metric("Output Tokens", usage.get("completion_tokens", 0))
                
                # Toon Metrics
                toon_data = debug_response.get("toon")
                if toon_data:
                    cols[2].metric("Toon Savings", toon_data.get("savings", "0%"))
                    cols[3].metric("Payload Size", f"{toon_data.get('toon_len', 0)} chars")
            
        except Exception as e:
            st.error(f"Connection Failed: {e}")

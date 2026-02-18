import streamlit as st
import json
import requests
import pandas as pd
import base64
import uuid
from urllib.parse import quote_plus

# --- Config ---
st.set_page_config(page_title="TAG Test Dashboard", layout="wide", initial_sidebar_state="collapsed")

# Session State Init
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "pending_filter_field" not in st.session_state:
    st.session_state["pending_filter_field"] = ""
if "pending_filter_source" not in st.session_state:
    st.session_state["pending_filter_source"] = ""

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

api_base_url = st.sidebar.text_input("API URL", value="http://localhost:8006")

# --- Helper Functions ---
def encode_context(uid, cid):
    data = {"user_id": uid, "company_id": cid, "user_role": "admin"}
    return base64.b64encode(json.dumps(data).encode()).decode()

def parse_ndjson(response):
    for line in response.iter_lines():
        if line:
            yield json.loads(line)


def render_workflow_ui(workflow_payload, key_prefix="wf"):
    if not isinstance(workflow_payload, dict):
        return

    ui = workflow_payload.get("ui") or {}
    collected_data = workflow_payload.get("collected_data") or {}
    next_field = workflow_payload.get("next_field")

    title = ui.get("title")
    if title:
        st.markdown(f"**{title}**")

    if next_field:
        st.caption(f"Next field: `{next_field}`")

    collected_fields = collected_data.get("collected_fields") or {}
    if collected_fields:
        st.caption(f"Current filters: `{json.dumps(collected_fields, ensure_ascii=True)}`")

    options = ui.get("options") or []
    if options:
        for idx, option in enumerate(options):
            label = str(option.get("label") or option.get("value") or f"Option {idx + 1}")
            value = str(option.get("value") or "").strip()
            if st.button(label, key=f"{key_prefix}_opt_{idx}", use_container_width=True):
                if "=" in value:
                    field, field_value = value.split("=", 1)
                    field = field.strip()
                    field_value = field_value.strip()
                    if field and not field_value:
                        st.session_state["pending_filter_field"] = field
                        st.session_state["pending_filter_source"] = key_prefix
                    else:
                        st.session_state["queued_prompt"] = value or label
                else:
                    st.session_state["queued_prompt"] = value or label
                st.rerun()

    active_field = st.session_state.get("pending_filter_field", "")
    active_source = st.session_state.get("pending_filter_source", "")
    if active_field and active_source == key_prefix:
        st.markdown(f"**Set value for `{active_field}`**")

        if "date" in active_field.lower():
            picked_date = st.date_input("Pick date", key=f"{key_prefix}_date_value")
            col_apply, col_cancel = st.columns(2)
            if col_apply.button("Apply", key=f"{key_prefix}_date_apply", use_container_width=True):
                st.session_state["queued_prompt"] = f"{active_field}={picked_date.isoformat()}"
                st.session_state["pending_filter_field"] = ""
                st.session_state["pending_filter_source"] = ""
                st.rerun()
            if col_cancel.button("Cancel", key=f"{key_prefix}_date_cancel", use_container_width=True):
                st.session_state["pending_filter_field"] = ""
                st.session_state["pending_filter_source"] = ""
                st.rerun()
        elif active_field.lower() == "status":
            status_val = st.selectbox(
                "Choose status",
                ["Pending", "In Progress", "Completed", "Overdue"],
                key=f"{key_prefix}_status_value",
            )
            col_apply, col_cancel = st.columns(2)
            if col_apply.button("Apply", key=f"{key_prefix}_status_apply", use_container_width=True):
                st.session_state["queued_prompt"] = f"{active_field}={status_val}"
                st.session_state["pending_filter_field"] = ""
                st.session_state["pending_filter_source"] = ""
                st.rerun()
            if col_cancel.button("Cancel", key=f"{key_prefix}_status_cancel", use_container_width=True):
                st.session_state["pending_filter_field"] = ""
                st.session_state["pending_filter_source"] = ""
                st.rerun()
        elif active_field.lower() == "priority":
            priority_val = st.selectbox("Choose priority", ["High", "Medium", "Low"], key=f"{key_prefix}_priority_value")
            col_apply, col_cancel = st.columns(2)
            if col_apply.button("Apply", key=f"{key_prefix}_priority_apply", use_container_width=True):
                st.session_state["queued_prompt"] = f"{active_field}={priority_val}"
                st.session_state["pending_filter_field"] = ""
                st.session_state["pending_filter_source"] = ""
                st.rerun()
            if col_cancel.button("Cancel", key=f"{key_prefix}_priority_cancel", use_container_width=True):
                st.session_state["pending_filter_field"] = ""
                st.session_state["pending_filter_source"] = ""
                st.rerun()
        else:
            typed_value = st.text_input(
                f"Enter {active_field}",
                placeholder=f"{active_field} value",
                key=f"{key_prefix}_text_value",
            )
            col_apply, col_cancel = st.columns(2)
            if col_apply.button("Apply", key=f"{key_prefix}_text_apply", use_container_width=True):
                if str(typed_value).strip():
                    st.session_state["queued_prompt"] = f"{active_field}={typed_value.strip()}"
                    st.session_state["pending_filter_field"] = ""
                    st.session_state["pending_filter_source"] = ""
                    st.rerun()
            if col_cancel.button("Cancel", key=f"{key_prefix}_text_cancel", use_container_width=True):
                st.session_state["pending_filter_field"] = ""
                st.session_state["pending_filter_source"] = ""
                st.rerun()

    example = ui.get("example")
    if example:
        st.caption(f"Example: `{example}`")


def run_prompt(prompt, api_base_url, user_id, company_id):
    st.session_state["pending_filter_field"] = ""
    st.session_state["pending_filter_source"] = ""
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
                                        if lower == 'id':
                                            return False
                                        if lower.endswith('_id'):
                                            return False
                                        if 'uuid' in lower or 'guid' in lower:
                                            return False
                                        return True

                                    display_cols = [c for c in df.columns if is_meaningful(c)]
                                    if display_cols:
                                        df_display = df[display_cols]
                                    else:
                                        df_display = df  # Fallback if everything is filtered

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

                            workflow_payload = item.get("workflow")
                            if workflow_payload:
                                render_workflow_ui(workflow_payload, key_prefix=f"wf_live_{len(st.session_state['messages'])}")
                                st.session_state["messages"].append({
                                    "role": "assistant",
                                    "type": "workflow",
                                    "workflow": workflow_payload,
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

# --- Main Interface ---
st.markdown(
    """
<style>
#MainMenu, footer, header {visibility: hidden;}
[data-testid="stAppViewContainer"] {
  background: linear-gradient(180deg, #f2f3f6 0%, #e8edf1 48%, #f2f3f6 100%);
}
.top-actions {
  position: fixed;
  top: 26px;
  right: 22px;
  display: flex;
  align-items: center;
  gap: 10px;
  z-index: 9999;
}
.task-status-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 40px;
  padding: 0 16px;
  border-radius: 12px;
  text-decoration: none;
  color: #ffffff !important;
  background: #3f46ee;
  box-shadow: 0 8px 16px rgba(63, 70, 238, 0.28);
  font-weight: 500;
  font-size: 13px;
  font-family: "Segoe UI", sans-serif;
}
.top-profile-chip {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: #3f46ee;
  box-shadow: 0 6px 14px rgba(63, 70, 238, 0.26);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.left-lightning-chip {
  position: fixed;
  top: 84px;
  left: 16px;
  width: 28px;
  height: 28px;
  border-radius: 10px;
  background: #f4f5f8;
  border: 1px solid #d7dce2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  text-decoration: none;
  box-shadow: 0 2px 6px rgba(17, 24, 39, 0.08);
}
.left-lightning-chip span {
  color: #5662f6;
  font-size: 14px;
}
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    f"""
<div class="top-actions">
  <a class="task-status-chip" href="?suggestion={quote_plus("Task's status")}">Task's status</a>
  <div class="top-profile-chip" aria-label="Profile">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="8" r="3.25" stroke="#ffffff" stroke-width="1.6"></circle>
      <path d="M6.5 18.2C7.5 15.7 9.5 14.4 12 14.4C14.5 14.4 16.5 15.7 17.5 18.2" stroke="#ffffff" stroke-width="1.6" stroke-linecap="round"></path>
    </svg>
  </div>
</div>
<a class="left-lightning-chip" href="?suggestion={quote_plus('Show priority high tasks for last 30 days')}"><span>⚡</span></a>
""",
    unsafe_allow_html=True,
)

# Render History
for idx, msg in enumerate(st.session_state["messages"]):
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
        elif msg.get("type") == "workflow":
            render_workflow_ui(msg.get("workflow"), key_prefix=f"wf_hist_{idx}")
        
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

selected_suggestion = st.query_params.get("suggestion")
queued_prompt = st.session_state.pop("queued_prompt", None)
prompt = queued_prompt if queued_prompt else (selected_suggestion if selected_suggestion else st.chat_input("Ask a question..."))
if selected_suggestion:
    del st.query_params["suggestion"]

if prompt:
    run_prompt(prompt, api_base_url, user_id, company_id)

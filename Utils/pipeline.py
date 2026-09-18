import streamlit as st
import json
from datetime import datetime
from snowflake.snowpark.context import get_active_session


session = get_active_session()


def save_dq_pipeline_to_snowflake():
    DB_NAME = "VALIDATOR_DB"
    SCHEMA_NAME = "VALIDATOR_SCHEMA"
    TABLE_NAME = "DQ_PIPELINE_LIBRARY"
    FULL_PATH = f"{DB_NAME}.{SCHEMA_NAME}.{TABLE_NAME}"

    target_table = st.session_state.get("selected_table")
    if not target_table:
        st.error("Pehle ek table select karein!")
        return

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    rule_id = f"{target_table.upper()}_{timestamp_str}"
    
    rules_json = json.dumps(st.session_state["constraints"])

    insert_sql = f"""
    INSERT INTO {FULL_PATH} 
    (RULE_ID, TARGET_TABLE, RULES, CREATED_AT, UPDATED_AT)
    SELECT 
        '{rule_id}', 
        '{target_table}', 
        PARSE_JSON($${rules_json}$$), 
        CURRENT_TIMESTAMP(), 
        CURRENT_TIMESTAMP()
    """
    
    try:
        session.sql(insert_sql).collect()
        
        st.success(f" **Rule Saved Successfully!**")
        st.info(f"""
        **Details for Manual Check:**
        * **Rule ID:** `{rule_id}`
        * **Location:** `{FULL_PATH}`
        * **Table Name:** `{target_table}`
        """)
        
    except Exception as e:
        st.error(f" Snowflake Error: {e}")

import streamlit as st
import json
import re
from datetime import datetime, date, time, timedelta
from snowflake.snowpark.context import get_active_session


def show_schedule_page():
    session = get_active_session()

    st.markdown('<div class="page-title">Schedule Quality Check</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-subtitle">Configure automated data quality jobs</div>', unsafe_allow_html=True)

    came_from_constraints = st.session_state.get("schedule_from_constraints", False)
    editing_job_id = st.session_state.get("editing_job_id", None)
    editing_job_name = st.session_state.get("editing_job_name", None)

    if not came_from_constraints and not editing_job_id:
        st.markdown("---")
        st.markdown(
            '<p style="font-weight:600;font-size:0.95rem;color:#1a1a2e;margin-bottom:8px;">Manage Existing Jobs</p>',
            unsafe_allow_html=True
        )
        _show_manage_section(session)
        return

    if st.button("Cancel", use_container_width=False):
        st.session_state.pop("editing_job_id", None)
        st.session_state.pop("editing_job_name", None)
        st.session_state.pop("schedule_from_constraints", None)
        st.experimental_rerun()

    if editing_job_id:
        st.info(f"Editing: **{editing_job_name}** (ID: {editing_job_id})")
        save_mode = st.radio("Action:", ["Update Existing Job", "Save as New Job"], horizontal=True)
    else:
        save_mode = "Save as New Job"

    selected_constraints = st.session_state.get("constraints", {})
    selected_table = st.session_state.get("selected_table", "TABLE")
    selected_schema = st.session_state.get("selected_schema", "SCHEMA")
    selected_database = st.session_state.get("selected_database", "DATABASE")

    st.markdown('**Job Configuration**')

    default_job_name = f"JOB_{selected_table}_{datetime.now().strftime('%m%d_%H%M')}"

    c1, c2 = st.columns([3, 1])
    with c1:
        job_name = st.text_input("Job Name", value=editing_job_name if save_mode == "Update Existing Job" else default_job_name)
    with c2:
        is_active = st.checkbox("Active", value=True)

    st.caption(f"Target: **{selected_database}.{selected_schema}.{selected_table}**")

    scan_type = st.radio("Scan Type", options=["Full Scan", "Incremental Scan"], horizontal=True)
    if 'final_query_template' not in st.session_state:
        st.session_state['final_query_template'] = ""

    if scan_type == "Full Scan":
        st.session_state['final_query_template'] = 'SELECT * FROM "{db}"."{schema}"."{table}"'
    else:
        try:
            df_schema = session.sql(f'SELECT * FROM "{selected_database}"."{selected_schema}"."{selected_table}" LIMIT 1').to_pandas()
            all_columns = df_schema.columns.tolist()
            column_list = st.multiselect(label="Incremental Fields", options=all_columns, default=None)
            if column_list:
                wm_placeholder = "{WATER_MARK_VALUE}"
                conditions = [f'"{col}" > \'{wm_placeholder}\'' for col in column_list]
                where_clause = " OR ".join(conditions)
                st.session_state['final_query_template'] = 'SELECT * FROM "{db}"."{schema}"."{table}" WHERE ' + where_clause
            else:
                st.session_state['final_query_template'] = ""
                st.warning("Please select at least one column for incremental scan.")
        except Exception as e:
            st.error(f"Error: {e}")

    final_query = st.session_state['final_query_template'].replace("'", "''")
    Static_water_mark = '1900-01-01 09:00:00'

    if "time_list" not in st.session_state:
        st.session_state.time_list = [time(9, 15)]

    def add_time():
        st.session_state.time_list.append(time(9, 0))

    def remove_time(index):
        if len(st.session_state.time_list) > 1:
            st.session_state.time_list.pop(index)

    def is_valid_cron(cron):
        return bool(re.match(r'^(\S+\s+){4}\S+$', cron))

    day_map = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}

    st.markdown('**Execution Schedule**')

    s1, s2, s3, s4 = st.columns(4)
    with s1:
        schedule_type = st.selectbox("Frequency", ["Daily", "Weekly", "Monthly"])
    with s2:
        run_mode = st.selectbox("Run Mode", ["Specific Time", "Every X Hours", "Every X Minutes", "Custom CRON"])
    with s3:
        st.date_input("Start Date", value=st.session_state.get("start_date", date.today()), key="start_date")
    with s4:
        st.date_input("End Date", value=st.session_state.get("end_date", date.today() + timedelta(days=365)), key="end_date")

    if schedule_type == "Weekly":
        day_of_week = st.selectbox("Day of Week", ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])
    elif schedule_type == "Monthly":
        day_of_month = st.number_input("Day of Month", min_value=1, max_value=31, value=1)

    base_cron_list = []

    if run_mode == "Specific Time":
        for i in range(len(st.session_state.time_list)):
            t_val = st.session_state.time_list[i]
            tc1, tc2 = st.columns([3, 1])
            with tc1:
                new_t = st.time_input(f"Time Slot {i+1}", value=t_val, key=f"time_{i}")
                st.session_state.time_list[i] = new_t
            with tc2:
                st.button("Remove", key=f"remove_{i}", on_click=remove_time, args=(i,), use_container_width=True)
        st.button("Add Time Slot", on_click=add_time)

        for t_val in st.session_state.time_list:
            h, m = t_val.hour, t_val.minute
            if schedule_type == "Daily":
                base_cron_list.append(f"{m} {h} * * *")
            elif schedule_type == "Weekly":
                base_cron_list.append(f"{m} {h} * * {day_map[day_of_week]}")
            else:
                base_cron_list.append(f"{m} {h} {day_of_month} * *")

    elif run_mode == "Every X Hours":
        ih_c1, ih_c2 = st.columns(2)
        with ih_c1:
            interval_hours = st.number_input("Every X Hours", min_value=1, max_value=23, value=2)
        with ih_c2:
            start_time_val = st.time_input("Starting At", value=time(9, 0), key="start_time_hours")
        start_hour, start_minute = start_time_val.hour, start_time_val.minute
        if schedule_type == "Daily":
            base_cron_list.append(f"{start_minute} {start_hour}-23/{interval_hours} * * *")
        elif schedule_type == "Weekly":
            base_cron_list.append(f"{start_minute} {start_hour}-23/{interval_hours} * * {day_map[day_of_week]}")
        else:
            base_cron_list.append(f"{start_minute} {start_hour}-23/{interval_hours} {day_of_month} * *")

    elif run_mode == "Every X Minutes":
        im_c1, im_c2 = st.columns(2)
        with im_c1:
            interval_minutes_only = st.number_input("Every X Minutes", min_value=1, max_value=59, value=5)
        with im_c2:
            st.time_input("Reference Start", value=time(9, 0), key="start_time_mins")
        if interval_minutes_only < 5:
            st.warning("High frequency may increase compute costs.")
        base_cron_list.append(f"*/{interval_minutes_only} * * * *")

    elif run_mode == "Custom CRON":
        custom_cron = st.text_input("CRON Expression", "0 9 * * *")
        if is_valid_cron(custom_cron):
            base_cron_list = [custom_cron]
            st.success("Valid CRON expression")
        else:
            st.error("Invalid CRON format. Expected: min hour day month weekday")

    if base_cron_list:
        st.code(" ; ".join([f"USING CRON {c} UTC" for c in base_cron_list]), language="sql")

    warehouse = st.text_input("Warehouse", "COMPUTE_WH")
    schedule_btn = st.button("Confirm and Schedule", type="primary", use_container_width=True)

    if schedule_btn:
        try:
            start_d_str = st.session_state.start_date.strftime('%Y-%m-%d')
            end_d_str = st.session_state.end_date.strftime('%Y-%m-%d')
            rules_json = json.dumps(selected_constraints)
            active_val = "TRUE" if is_active else "FALSE"
            full_schedule_string = "; ".join(base_cron_list)

            if save_mode == "Update Existing Job":
                sql = f"""
                    UPDATE VALIDATOR_DB.VALIDATOR_SCHEMA.DQ_JOB_CONFIG
                    SET JOB_NAME = '{job_name}',
                        VALIDATION_RULES = PARSE_JSON($${rules_json}$$),
                        SCHEDULE_CRON = '{full_schedule_string}',
                        ACTIVE = {active_val}
                    WHERE JOB_ID = {editing_job_id}
                """
                session.sql(sql).collect()
                final_job_id = editing_job_id
            else:
                res = session.sql(
                    "SELECT COALESCE(MAX(job_id),0)+1 FROM VALIDATOR_DB.VALIDATOR_SCHEMA.DQ_JOB_CONFIG"
                ).collect()
                new_job_id = res[0][0]
                sql = f"""
                    INSERT INTO VALIDATOR_DB.VALIDATOR_SCHEMA.DQ_JOB_CONFIG
                    (JOB_ID, JOB_NAME, DB_NAME, SCHEMA_NAME, TABLE_NAME,
                     VALIDATION_RULES, SCHEDULE_CRON, CREATED_BY, CREATED_AT, ACTIVE,
                     RUN_QUERY, WATER_MARK_VALUE)
                    SELECT 
                        {new_job_id}, '{job_name}', '{selected_database}', '{selected_schema}', '{selected_table}',
                        PARSE_JSON($${rules_json}$$), '{full_schedule_string}',
                        CURRENT_USER(), CURRENT_TIMESTAMP(), {active_val},
                        '{final_query}', '{Static_water_mark}'
                """
                session.sql(sql).collect()
                final_job_id = new_job_id

            for idx, cron in enumerate(base_cron_list, start=1):
                task_sql = f"""
                    CREATE OR REPLACE TASK VALIDATOR_DB.VALIDATOR_SCHEMA.{job_name}_{idx}
                    WAREHOUSE = '{warehouse}'
                    SCHEDULE = 'USING CRON {cron} UTC'
                    AS
                    BEGIN
                        IF (CURRENT_DATE() >= '{start_d_str}' AND CURRENT_DATE() <= '{end_d_str}') THEN
                            CALL DQ_RUN_JOB('{final_job_id}');
                        END IF;
                    END;
                """
                session.sql(task_sql).collect()
                if is_active:
                    session.sql(f"ALTER TASK VALIDATOR_DB.VALIDATOR_SCHEMA.{job_name}_{idx} RESUME").collect()
                else:
                    session.sql(f"ALTER TASK VALIDATOR_DB.VALIDATOR_SCHEMA.{job_name}_{idx} SUSPEND").collect()

            st.session_state.pop("schedule_from_constraints", None)
            st.session_state.pop("editing_job_id", None)
            st.session_state.pop("editing_job_name", None)
            st.success(f"Job '{job_name}' scheduled successfully!")
        except Exception as e:
            st.error(f"Scheduling Error: {e}")

    st.markdown("---")
    st.markdown(
        '<p style="font-weight:600;font-size:0.95rem;color:#1a1a2e;margin-bottom:8px;">Manage Existing Jobs</p>',
        unsafe_allow_html=True
    )
    _show_manage_section(session)


def _show_manage_section(session):
    all_jobs_df = session.sql(
        "SELECT JOB_ID, JOB_NAME, DB_NAME, SCHEMA_NAME, TABLE_NAME, VALIDATION_RULES "
        "FROM VALIDATOR_DB.VALIDATOR_SCHEMA.DQ_JOB_CONFIG ORDER BY CREATED_AT DESC"
    ).to_pandas()

    if not all_jobs_df.empty:
        job_map = {f"ID {r['JOB_ID']} | {r['JOB_NAME']}": r for _, r in all_jobs_df.iterrows()}
        selected_job = st.selectbox("Select Job to Edit:", ["-- Select --"] + list(job_map.keys()))

        if selected_job != "-- Select --":
            job_data = job_map[selected_job]
            if st.button("Load Rules to Main Page", use_container_width=True, type="primary"):
                st.session_state.editing_job_id = job_data['JOB_ID']
                st.session_state.editing_job_name = job_data['JOB_NAME']
                st.session_state.selected_database = job_data['DB_NAME']
                st.session_state.selected_schema = job_data['SCHEMA_NAME']
                st.session_state.selected_table = job_data['TABLE_NAME']

                rules_raw = job_data['VALIDATION_RULES']
                if isinstance(rules_raw, str):
                    rules_raw = json.loads(rules_raw)
                if isinstance(rules_raw, dict):
                    normalized = {}
                    for col_name, col_rules in rules_raw.items():
                        if isinstance(col_rules, list):
                            normalized[col_name] = col_rules
                        else:
                            normalized[col_name] = []
                    st.session_state["constraints"] = normalized
                else:
                    st.session_state["constraints"] = {}

                st.session_state["dq_ready"] = True
                st.session_state["schedule_from_constraints"] = True
                st.session_state.active_tab = "main"
                st.experimental_rerun()
    else:
        st.caption("No jobs found.")

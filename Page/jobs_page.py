import streamlit as st
import pandas as pd
import numpy as np
import json
from datetime import date, timedelta
from snowflake.snowpark.context import get_active_session
import plotly.express as px
import plotly.graph_objects as go


def showjobdashboard():
    st.markdown('<div class="page-title">Job Management</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-subtitle">View and manage scheduled data quality jobs</div>', unsafe_allow_html=True)
    session = get_active_session()

    try:
        jobs_df = session.sql("""
            SELECT JOB_ID, JOB_NAME, DB_NAME, SCHEMA_NAME, TABLE_NAME, ACTIVE, 
                   SCHEDULE_CRON, VALIDATION_RULES
            FROM VALIDATOR_DB.VALIDATOR_SCHEMA.DQ_JOB_CONFIG 
            ORDER BY CREATED_AT DESC
        """).to_pandas()

        if jobs_df.empty:
            st.markdown(
                '<div style="text-align:center;padding:40px 20px;background:#f8f9fb;border-radius:12px;'
                'border:1px dashed #d1d5db;margin-top:1rem;">'
                '<p style="font-size:1rem;color:#6b7280;margin:0;">No jobs configured yet.</p>'
                '<p style="font-size:0.85rem;color:#9ca3af;margin-top:4px;">Create a job from the Scheduler tab.</p>'
                '</div>',
                unsafe_allow_html=True
            )
            return

        active_count = int(jobs_df['ACTIVE'].sum()) if 'ACTIVE' in jobs_df.columns else 0
        total_count = len(jobs_df)

        k1, k2, k3 = st.columns(3)
        k1.metric("Total Jobs", total_count)
        k2.metric("Active Jobs", active_count)
        k3.metric("Inactive Jobs", total_count - active_count)

        st.markdown(
            '<p style="font-weight:600;font-size:0.95rem;color:#1a1a2e;margin-top:1rem;margin-bottom:8px;">Job Configuration</p>',
            unsafe_allow_html=True
        )

        display_df = jobs_df[['JOB_ID', 'JOB_NAME', 'TABLE_NAME', 'ACTIVE', 'SCHEDULE_CRON']].copy()
        display_df.columns = ['ID', 'Job Name', 'Table', 'Active', 'Schedule']
        display_df['Active'] = display_df['Active'].astype(bool)
        st.dataframe(display_df, use_container_width=True)

        st.markdown(
            '<p style="font-weight:600;font-size:0.9rem;color:#1a1a2e;margin-top:1rem;margin-bottom:4px;">Toggle Job Status</p>',
            unsafe_allow_html=True
        )
        job_options = {f"{r['JOB_NAME']} (ID {r['JOB_ID']})": r for _, r in jobs_df.iterrows()}
        sel = st.selectbox("Select Job", ["-- Select --"] + list(job_options.keys()), key="toggle_job_sel")

        if sel != "-- Select --":
            row = job_options[sel]
            is_active = bool(row['ACTIVE'])
            status_text = "Active" if is_active else "Inactive"
            btn_label = "Disable Job" if is_active else "Enable Job"
            st.caption(f"Current status: **{status_text}**")
            if st.button(btn_label, use_container_width=True):
                new_val = "FALSE" if is_active else "TRUE"
                session.sql(f"UPDATE VALIDATOR_DB.VALIDATOR_SCHEMA.DQ_JOB_CONFIG SET ACTIVE = {new_val} WHERE JOB_ID = {row['JOB_ID']}").collect()
                st.experimental_rerun()

    except Exception as e:
        st.error(f"Dashboard Error: {e}")


def show_history_page():
    st.markdown(
        '<div style="margin-top:1.5rem;">'
        '<span style="font-size:1.1rem;font-weight:700;color:#1a1a2e;">Data Quality Trends</span>'
        '</div>',
        unsafe_allow_html=True
    )

    try:
        session = get_active_session()
    except:
        st.error("Snowflake session not found.")
        return

    jobs_df = session.sql("SELECT JOB_ID, JOB_NAME FROM VALIDATOR_DB.VALIDATOR_SCHEMA.DQ_JOB_CONFIG").to_pandas()
    if jobs_df.empty:
        st.caption("No jobs found.")
        return

    job_map = {f"{r.JOB_NAME} (ID {r.JOB_ID})": r.JOB_ID for r in jobs_df.itertuples()}

    st.markdown('**Filters**')
    f1, f2, f3, f4 = st.columns([2, 2, 2, 1])
    with f1:
        selected = st.selectbox("Job", list(job_map.keys()), key="hist_job")
        job_id = job_map[selected]
    with f2:
        date_range = st.date_input("Date Range", value=(date.today() - timedelta(days=7), date.today()), key="hist_dates")
    with f3:
        view_mode = st.selectbox("View Mode", ["Full View", "Incremental View"], key="hist_mode")
    with f4:
        if view_mode == "Full View":
            view_level = st.selectbox("Aggregation", ["Daily Mean", "Weekly Mean"], key="hist_agg")
        else:
            view_level = "Every Run (Delta)"
            st.caption("Delta mode")

    if len(date_range) == 2:
        start_dt, end_dt = date_range
    else:
        st.info("Please select a valid date range.")
        return

    query = f"""
        SELECT RUN_TIMESTAMP, TOTAL_ROWS_SCANNED AS TOTAL_ROWS, TOTAL_FAILED_ROWS AS FAILED_ROWS, RESULTS
        FROM VALIDATOR_DB.VALIDATOR_SCHEMA.DQ_RESULTS
        WHERE JOB_ID = '{job_id}' 
        AND CAST(RUN_TIMESTAMP AS DATE) BETWEEN '{start_dt}' AND '{end_dt}'
        ORDER BY RUN_TIMESTAMP ASC
    """
    all_runs_df = session.sql(query).to_pandas()

    if all_runs_df.empty:
        st.markdown(
            '<div style="text-align:center;padding:30px 20px;background:#f8f9fb;border-radius:12px;'
            'border:1px dashed #d1d5db;">'
            '<p style="font-size:0.95rem;color:#6b7280;margin:0;">No execution data found for this period.</p>'
            '</div>',
            unsafe_allow_html=True
        )
        return

    all_runs_df["RUN_TIMESTAMP"] = pd.to_datetime(all_runs_df["RUN_TIMESTAMP"])
    all_runs_df = all_runs_df.sort_values("RUN_TIMESTAMP")

    if view_mode == "Incremental View":
        all_runs_df["INC_TOTAL_ROWS"] = all_runs_df["TOTAL_ROWS"].diff().fillna(all_runs_df["TOTAL_ROWS"])
        all_runs_df["INC_FAILED_ROWS"] = all_runs_df["FAILED_ROWS"].diff().fillna(all_runs_df["FAILED_ROWS"])
        all_runs_df["INC_TOTAL_ROWS"] = all_runs_df["INC_TOTAL_ROWS"].clip(lower=0)
        all_runs_df["INC_FAILED_ROWS"] = all_runs_df["INC_FAILED_ROWS"].clip(lower=0)
        all_runs_df["PASS_PERCENT"] = np.where(
            all_runs_df["INC_TOTAL_ROWS"] > 0,
            ((all_runs_df["INC_TOTAL_ROWS"] - all_runs_df["INC_FAILED_ROWS"]) / all_runs_df["INC_TOTAL_ROWS"]) * 100,
            100.0
        ).round(2)
        trend_data = all_runs_df.copy()
        bar_col = "INC_TOTAL_ROWS"
        bar_label = "New Rows Scanned"
        y1_label = "New Rows Scanned"
        m2_label = "Incr. Rows"
        m3_label = "Incr. Failed"
        m2_val = all_runs_df["INC_TOTAL_ROWS"].sum()
        m3_val = all_runs_df["INC_FAILED_ROWS"].sum()
    else:
        all_runs_df["PASS_PERCENT"] = np.where(
            all_runs_df["TOTAL_ROWS"] > 0,
            ((all_runs_df["TOTAL_ROWS"] - all_runs_df["FAILED_ROWS"]) / all_runs_df["TOTAL_ROWS"]) * 100,
            0
        ).round(2)
        numeric_cols = ["TOTAL_ROWS", "FAILED_ROWS", "PASS_PERCENT"]
        if view_level == "Daily Mean":
            trend_data = all_runs_df.resample('D', on='RUN_TIMESTAMP')[numeric_cols].mean().reset_index()
        elif view_level == "Weekly Mean":
            trend_data = all_runs_df.resample('W', on='RUN_TIMESTAMP')[numeric_cols].mean().reset_index()
        else:
            trend_data = all_runs_df.copy()
        trend_data = trend_data.dropna(subset=["PASS_PERCENT"])
        bar_col = "TOTAL_ROWS"
        bar_label = "Rows Scanned"
        y1_label = "Volume (Rows)"
        m2_label = "Total Rows"
        m3_label = "Total Failed"
        m2_val = all_runs_df['TOTAL_ROWS'].sum()
        m3_val = all_runs_df['FAILED_ROWS'].sum()

    m1, m2, m3 = st.columns(3)
    latest_score = all_runs_df.iloc[-1]["PASS_PERCENT"]
    m1.metric("Latest Quality", f"{latest_score:.1f}%")
    m2.metric(m2_label, f"{int(m2_val):,}")
    m3.metric(m3_label, f"{int(m3_val):,}")

    chart_left, chart_right = st.columns(2)

    with chart_left:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=trend_data["RUN_TIMESTAMP"], y=trend_data[bar_col],
            name=bar_label, marker_color='rgba(15, 52, 96, 0.25)', yaxis="y1"
        ))
        fig.add_trace(go.Scatter(
            x=trend_data["RUN_TIMESTAMP"], y=trend_data["PASS_PERCENT"],
            name="Pass %", mode='lines+markers',
            line=dict(color='#c62828', width=2), marker=dict(size=6, color='#c62828'), yaxis="y2"
        ))
        fig.update_layout(
            xaxis=dict(title="", gridcolor="#f0f2f6"),
            yaxis=dict(title=y1_label, gridcolor="#f0f2f6"),
            yaxis2=dict(title="Pass %", overlaying="y", side="right", range=[0, 105], gridcolor="#f0f2f6"),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, sans-serif", size=11),
            margin=dict(t=30, b=20, l=20, r=20), height=350
        )
        st.plotly_chart(fig, use_container_width=True)

    with chart_right:
        st.markdown(
            '<p style="font-weight:600;font-size:0.9rem;color:#1a1a2e;margin-bottom:8px;">Failure Analysis</p>',
            unsafe_allow_html=True
        )
        detail_df = all_runs_df.sort_values("RUN_TIMESTAMP", ascending=False)
        run_list = {r.RUN_TIMESTAMP.strftime('%Y-%m-%d %H:%M:%S'): r.Index for r in detail_df.itertuples()}
        chosen_run_label = st.selectbox("Run:", options=list(run_list.keys()), key="hist_run_sel")

        current_row = all_runs_df.loc[run_list[chosen_run_label]]
        dq_json = json.loads(current_row["RESULTS"]) if isinstance(current_row["RESULTS"], str) else current_row["RESULTS"]
        column_rules = dq_json.get("column_rules", {})

        prev_column_rules = {}
        if view_mode == "Incremental View":
            sorted_all = all_runs_df.sort_values("RUN_TIMESTAMP")
            idx_list = sorted_all.index.tolist()
            curr_idx_pos = idx_list.index(run_list[chosen_run_label])
            if curr_idx_pos > 0:
                prev_row = all_runs_df.loc[idx_list[curr_idx_pos - 1]]
                prev_column_rules = json.loads(prev_row["RESULTS"]) if isinstance(prev_row["RESULTS"], str) else prev_row["RESULTS"]
                prev_column_rules = prev_column_rules.get("column_rules", {})

        fail_records = []
        for col, rules in column_rules.items():
            for i, r in enumerate(rules):
                curr_f = r.get("failed_count", 0)
                rule_t = r.get("rule_type", "Unknown")
                if view_mode == "Incremental View":
                    prev_f = 0
                    if col in prev_column_rules:
                        try:
                            prev_f = prev_column_rules[col][i].get("failed_count", 0)
                        except:
                            prev_f = 0
                    display_f = max(0, curr_f - prev_f)
                else:
                    display_f = curr_f
                if display_f > 0:
                    fail_records.append({"Column": col, "Rule": rule_t, "Failures": display_f})

        if fail_records:
            fail_df = pd.DataFrame(fail_records)
            fig2 = px.bar(
                fail_df, x='Column', y='Failures', color='Rule', barmode='group',
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            fig2.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(family="Inter, sans-serif", size=11),
                margin=dict(t=10, b=20, l=20, r=20), height=280,
                xaxis=dict(gridcolor="#f0f2f6"), yaxis=dict(gridcolor="#f0f2f6"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0)
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.success("No failures in this run!")

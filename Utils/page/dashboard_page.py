import streamlit as st
import pandas as pd
import json
from snowflake.snowpark.context import get_active_session
import plotly.express as px
import plotly.graph_objects as go
import streamlit.components.v1 as components

HEALTHY_COLOR = "#2E7D32"
ERROR_COLOR = "#D32F2F"
WARNING_COLOR = "#F57C00"


def get_accuracy_and_validation():
    session = get_active_session()

    st.markdown('<div class="page-title">Accuracy & Validation Dashboard</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-subtitle">Monitor data health across quarantined tables</div>', unsafe_allow_html=True)

    try:
        table_list_query = """
            SELECT TABLE_NAME 
            FROM VALIDATOR_DB.INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_SCHEMA = 'DQ_QUARANTINE' 
            ORDER BY TABLE_NAME
        """
        tables_df = session.sql(table_list_query).to_pandas()
        quarantine_options = tables_df['TABLE_NAME'].tolist()
    except Exception as e:
        st.error(f"Error fetching table list: {e}")
        return

    if not quarantine_options:
        st.warning("No tables found in DQ_QUARANTINE schema.")
        return

    selected_tables = st.multiselect(
        "Select Table(s) to Analyze",
        options=quarantine_options,
        default=[],
        help="Leave empty to include all tables"
    )

    if not selected_tables:
        selected_tables = quarantine_options
        st.caption("Showing metrics for all tables in DQ_QUARANTINE schema.")

    try:
        total_records = 0
        quarantine_records = 0

        for table in selected_tables:
            source_query = f"""
                SELECT COUNT(*) AS TOTAL_ROWS
                FROM VALIDATOR_DB.VALIDATOR_SCHEMA.{table}
            """
            df_source = session.sql(source_query).to_pandas()
            total_records += int(df_source["TOTAL_ROWS"].iloc[0])

            quarantine_query = f"""
                SELECT COUNT(*) AS Q_ROWS
                FROM VALIDATOR_DB.DQ_QUARANTINE.{table}
            """
            df_quarantine = session.sql(quarantine_query).to_pandas()
            quarantine_records += int(df_quarantine["Q_ROWS"].iloc[0])

        valid_records = total_records - quarantine_records
        health_pct = (valid_records / total_records) * 100 if total_records > 0 else 0

    except Exception as e:
        st.error(f"Data Loading Error: {e}")
        return

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Records", f"{total_records:,}")
    k2.metric("Quarantined", f"{quarantine_records:,}")
    k3.metric("Valid Records", f"{valid_records:,}")
    k4.metric("Health Score", f"{health_pct:.1f}%")

    if health_pct >= 95:
        status, health_color = "EXCELLENT", HEALTHY_COLOR
    elif health_pct >= 70:
        status, health_color = "GOOD", WARNING_COLOR
    else:
        status, health_color = "CRITICAL", ERROR_COLOR

    components.html(f"""
    <div style="font-family:'Inter',sans-serif; background:#ffffff; padding:16px 20px; border-radius:10px;
                border:1px solid #e8eaed; box-shadow:0 1px 4px rgba(0,0,0,0.04);">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <div>
                <span style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;font-weight:500;">
                    Data Health Status
                </span>
                <div style="font-weight:700; color:{health_color}; font-size:1rem; margin-top:2px;">
                    {status}
                </div>
            </div>
            <span style="font-size:1.8rem; font-weight:700; color:{health_color};">
                {health_pct:.1f}%
            </span>
        </div>
        <div style="height:6px; background:#f0f2f6; border-radius:6px; overflow:hidden;">
            <div style="width:{health_pct}%; height:100%; background:{health_color};
                        border-radius:6px; transition:width 0.8s ease;"></div>
        </div>
    </div>
    """, height=100)

    chart_left, chart_right = st.columns(2)

    with chart_left:
        st.markdown('<p style="font-weight:600;font-size:0.9rem;color:#1a1a2e;margin:12px 0 4px 0;">Records Distribution</p>', unsafe_allow_html=True)
        fig = go.Figure(data=[go.Pie(
            values=[valid_records, quarantine_records],
            labels=["Valid", "Quarantined"],
            hole=0.6,
            marker=dict(colors=[HEALTHY_COLOR, ERROR_COLOR]),
            textinfo="label+percent",
            textfont=dict(size=12, family="Inter, sans-serif")
        )])
        fig.update_layout(
            showlegend=False,
            margin=dict(t=10, b=10, l=10, r=10),
            height=260,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, sans-serif")
        )
        st.plotly_chart(fig, use_container_width=True)

    with chart_right:
        if len(selected_tables) == 1:
            st.markdown(
                '<p style="font-weight:600;font-size:0.9rem;color:#1a1a2e;margin:12px 0 4px 0;">Rule-Wise Failures</p>',
                unsafe_allow_html=True
            )

            tables_string = ",".join([f"'{t}'" for t in selected_tables])
            try:
                results_query = f"""
                    SELECT results
                    FROM VALIDATOR_DB.VALIDATOR_SCHEMA.DQ_RESULTS
                    WHERE TABLE_NAME IN ({tables_string})
                """
                df_results = session.sql(results_query).to_pandas()
                all_rules = []

                if not df_results.empty:
                    results_json = df_results["RESULTS"].iloc[0]
                    dq_json = json.loads(results_json) if isinstance(results_json, str) else results_json
                    if "column_rules" in dq_json:
                        for column_name, rules in dq_json["column_rules"].items():
                            for r in rules:
                                all_rules.append({
                                    "Column": column_name,
                                    "Rule Type": r.get("rule_type"),
                                    "Failed Rows": r.get("failed_count", 0)
                                })

                if all_rules:
                    rules_df = pd.DataFrame(all_rules)
                    summary_rules = (
                        rules_df.groupby(["Column", "Rule Type"])["Failed Rows"].sum().reset_index()
                    )
                    fig2 = px.bar(
                        summary_rules, x="Rule Type", y="Failed Rows", color="Column",
                        text="Failed Rows",
                        color_discrete_sequence=px.colors.qualitative.Set2
                    )
                    fig2.update_layout(
                        height=260,
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="Inter, sans-serif", size=11),
                        margin=dict(t=10, b=20, l=20, r=10),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0)
                    )
                    st.plotly_chart(fig2, use_container_width=True)
                else:
                    st.success("No rule failures in latest run.")
            except Exception as e:
                st.error(f"Rule breakdown error: {e}")
        else:
            st.markdown(
                '<p style="font-weight:600;font-size:0.9rem;color:#1a1a2e;margin:12px 0 4px 0;">Per-Table Breakdown</p>',
                unsafe_allow_html=True
            )
            st.info("Select a single table to see rule-wise failure breakdown.")

    if len(selected_tables) == 1 and 'all_rules' in dir() and all_rules:
        with st.expander("Detailed Rule Failures Table"):
            st.dataframe(summary_rules.sort_values("Failed Rows", ascending=False), use_container_width=True)

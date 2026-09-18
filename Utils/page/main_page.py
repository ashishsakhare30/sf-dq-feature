import streamlit as st
import pandas as pd
import numpy as np
import json
import re
import time
from datetime import datetime, date
from snowflake.snowpark.context import get_active_session
import plotly.express as px
import plotly.graph_objects as go

from utils.helpers import (
    _ensure_state,
    normalize_type_from_ai,
    normalize_type_for_compare,
    render_selectbox_with_disabled,
    prune_session_for_new_df,
    normalize_series_for_compare,
)
from utils.validators import (
    regex_match_series,
    detect_date_columns,
    validate_timeliness,
    run_validation,
    build_missing_mask,
)
from utils.pipeline import save_dq_pipeline_to_snowflake

session = get_active_session()

CONSTRAINT_BADGE_COLORS = {
    "Not Null": ("#e8f5e9", "#2e7d32"),
    "Uniqueness": ("#e3f2fd", "#1565c0"),
    "Regex Match": ("#fff3e0", "#e65100"),
    "Min Value": ("#fce4ec", "#c62828"),
    "Max Value": ("#fce4ec", "#c62828"),
    "Validation": ("#f3e5f5", "#6a1b9a"),
    "Phone Validation": ("#e0f2f1", "#00695c"),
    "Timeliness": ("#fff8e1", "#f57f17"),
    "Values Allowed (comma-separated)": ("#e8eaf6", "#283593"),
    "Values Not Allowed (comma-separated)": ("#fbe9e7", "#bf360c"),
}


def _constraint_badge_html(c):
    ctype = c["type"]
    bg, fg = CONSTRAINT_BADGE_COLORS.get(ctype, ("#f5f5f5", "#424242"))
    src_tag = '<span style="font-size:0.65rem;opacity:0.7;margin-left:3px;">AI</span>' if c.get("source", "") == "ai" else ""
    cs_tag = '<span style="font-size:0.65rem;opacity:0.7;margin-left:3px;">Aa</span>' if c.get("case_sensitive", False) else ""
    val = f' = {c["value"]}' if c.get("value") else ""
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f'padding:2px 8px;border-radius:12px;font-size:0.75rem;font-weight:600;'
        f'margin:2px 2px;">{ctype}{val}{src_tag}{cs_tag}</span>'
    )


def show_main_page():
    st.markdown(
        '<div style="background:#f8f9fb;border:1px solid #e8eaed;border-radius:10px;padding:20px;margin-bottom:1rem;">'
        '<p style="margin:0 0 4px 0;font-weight:600;font-size:0.95rem;color:#1a1a2e;">Select Data Source</p>'
        '<p style="margin:0;font-size:0.8rem;color:#6b7280;">Choose database, schema and table to validate</p>'
        '</div>',
        unsafe_allow_html=True
    )

    sel_col1, sel_col2, sel_col3 = st.columns(3)

    with sel_col1:
        dbs = [r[1] for r in session.sql("SHOW DATABASES").collect()]
        database = st.selectbox("Database", dbs, key="db_sel")

    with sel_col2:
        schemas = [r[1] for r in session.sql(f"SHOW SCHEMAS IN DATABASE {database}").collect()] if database else []
        schema = st.selectbox("Schema", schemas, key="schema_sel")

    with sel_col3:
        if "last_schema" not in st.session_state:
            st.session_state.last_schema = schema

        if schema != st.session_state.last_schema:
            st.session_state.tables_cached = None
            st.session_state.table_sel = None
            st.session_state.last_schema = schema

        if "tables_cached" not in st.session_state or st.session_state.tables_cached is None:
            st.session_state.tables_cached = (
                [r[1] for r in session.sql(
                    f"SHOW TABLES IN SCHEMA {database}.{schema}"
                ).collect()]
                if schema else []
            )

        tables = st.session_state.tables_cached

        if "table_sel" in st.session_state and st.session_state.table_sel not in (tables or []):
            del st.session_state["table_sel"]
        table = st.selectbox(
            "Table",
            tables,
            key="table_sel"
        )

    available_columns = []
    if database and schema and table:
        try:
            col_info = session.sql(f"SHOW COLUMNS IN TABLE {database}.{schema}.{table}").collect()
            if len(col_info) > 0:
                for r in col_info:
                    try:
                        col_name = r["column_name"]
                    except Exception:
                        col_name = list(r.values())[2] if len(list(r.values())) > 2 else None
                    if col_name:
                        available_columns.append(col_name)
        except Exception:
            available_columns = []

    opt_col1, opt_col2 = st.columns([3, 1])
    with opt_col1:
        selected_columns = st.multiselect(
            "Columns (optional - leave empty for all)",
            options=available_columns,
            default=[]
        )
    with opt_col2:
        sample_percent = st.slider(
            "AI Sample %",
            min_value=1,
            max_value=100,
            value=10,
            step=1,
            help="Percentage of table rows to use for stratified sampling during AI inference"
        )

    if st.button("Analyze & Detect Constraints", type="primary", use_container_width=True) and database and schema and table:

        start_time = time.time()
        with st.spinner("Loading table and auto-detecting datatypes & constraints..."):
            fqtn = f"{database}.{schema}.{table}"

            try:
                if selected_columns and len(selected_columns) > 0:
                    cols_str = ", ".join([f'"{c}"' for c in selected_columns])
                    df = session.sql(f"SELECT {cols_str} FROM {fqtn}").to_pandas()
                else:
                    df = session.table(fqtn).to_pandas()
            except Exception as e:
                st.error(f"Failed to load table: {e}")
                st.stop()

            st.session_state["df"] = df
            st.session_state["selected_table"] = table
            st.session_state["selected_schema"] = schema
            st.session_state["selected_database"] = database

            prune_session_for_new_df(df)


            try:
                desc_query = f"""
                    SELECT column_name, comment
                    FROM {database}.information_schema.columns
                    WHERE table_schema='{schema}' AND table_name='{table}'
                """
                col_meta = session.sql(desc_query).collect()
                col_descriptions = {row["COLUMN_NAME"]: (row.get("COMMENT") or "") for row in col_meta}
            except Exception:
                col_descriptions = {}

            sample_frac = max(0.01, min(1.0, float(sample_percent) / 100.0))
            seed = 123
            sample_size = max(1, int(len(df) * sample_frac))

            strat_col = None
            best_nunique = 0
            for c in df.columns:
                if pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_categorical_dtype(df[c]):
                    nu = df[c].nunique()
                    if 2 <= nu <= 100 and nu > best_nunique:
                        best_nunique = nu
                        strat_col = c

            if strat_col is not None:
                groups = df.groupby(strat_col, dropna=False)
                frames = []
                for _, grp in groups:
                    n_grp = max(1, int(round(len(grp) / len(df) * sample_size)))
                    n_grp = min(n_grp, len(grp))
                    frames.append(grp.sample(n=n_grp, random_state=seed))
                sample_df = pd.concat(frames).sample(frac=1, random_state=seed).reset_index(drop=True)
                if len(sample_df) > sample_size:
                    sample_df = sample_df.head(sample_size)
            else:
                sample_df = df.sample(n=min(sample_size, len(df)), random_state=seed).reset_index(drop=True)

            MAX_SAMPLE_VALUES = 20
            column_profiles = []
            for col in sample_df.columns:
                series = sample_df[col]
                full_series = df[col]
                total = len(series)
                null_count = int(series.isna().sum())
                non_null = series.dropna()
                unique_count = int(non_null.nunique())
                pandas_dtype = str(series.dtype)

                full_non_null = full_series.dropna()
                full_unique_count = int(full_non_null.nunique())
                full_total_non_null = len(full_non_null)
                full_unique_pct = round(full_unique_count / max(1, full_total_non_null) * 100, 1)
                has_repeats_in_full = full_unique_count < full_total_non_null

                diverse_vals = []
                if len(non_null) > 0:
                    uniques = non_null.unique()
                    pick_n = min(MAX_SAMPLE_VALUES, len(uniques))
                    chosen = pd.Series(uniques).sample(n=pick_n, random_state=seed)
                    diverse_vals = [str(v) for v in chosen.tolist()]

                profile = {
                    "column_name": col,
                    "pandas_dtype": pandas_dtype,
                    "total_rows": total,
                    "null_count": null_count,
                    "null_pct": round(null_count / total * 100, 1) if total > 0 else 0,
                    "unique_count": unique_count,
                    "unique_pct": round(unique_count / max(1, total - null_count) * 100, 1),
                    "full_data_unique_pct": full_unique_pct,
                    "full_data_has_repeats": has_repeats_in_full,
                    "sample_values": diverse_vals,
                }

                if pd.api.types.is_numeric_dtype(series):
                    profile["min"] = str(non_null.min()) if len(non_null) > 0 else None
                    profile["max"] = str(non_null.max()) if len(non_null) > 0 else None

                if unique_count <= 30 and len(non_null) > 0:
                    vc = non_null.value_counts(normalize=True).head(15)
                    profile["top_values"] = {str(k): round(v * 100, 1) for k, v in vc.items()}

                column_profiles.append(profile)

            COL_CHUNK_SIZE = 25
            col_chunks = [column_profiles[i:i + COL_CHUNK_SIZE] for i in range(0, len(column_profiles), COL_CHUNK_SIZE)]

            base_prompt = """You are a data quality and schema inference expert. Analyze column profiles and apply ALL relevant constraints aggressively. Most columns should have 2-3 constraints.

Data context: SAMPLE_PERCENT% stratified sample (SAMPLE_SIZE of TOTAL_ROWS rows).

Each profile has: column_name, pandas_dtype, total_rows, null_count, null_pct, unique_count, unique_pct, full_data_unique_pct (from ALL rows), full_data_has_repeats (true = duplicates exist in full data), sample_values, and optionally min/max/top_values.

For each column return a JSON object with: column_name, detected_datatype, description, applied_constraints (array), regex_pattern.

CONSTRAINT RULES - apply ALL that fit:

1. NOT NULL: Apply when null_pct < 5%. Most columns in well-maintained tables qualify.

2. UNIQUENESS: Apply when the column is an identifier (IDs, codes, serial numbers, SSNs, emails, invoice numbers, reference numbers) AND full_data_has_repeats is false AND full_data_unique_pct > 98%. Do NOT apply to names, addresses, descriptions, categories, statuses, dates, or amounts.

3. PHONE VALIDATION: Apply when the column represents phone/mobile/contact numbers. Look for column names containing phone/mobile/contact/msisdn/telephone OR sample values that look like phone numbers (digits with optional +, dashes, spaces, parentheses). When applied, do NOT also apply VALIDATION. Generate a country-agnostic phone regex.

4. VALIDATION: Apply broadly to any column with a recognizable value pattern:
   - Email addresses (contains @)
   - Gender/sex columns (M/F/Male/Female/Other)
   - Status/flag columns (Active/Inactive, Yes/No, True/False, Open/Closed)
   - Country/state codes (2-3 letter codes like US, IN, CA)
   - Formatted codes (ZIP codes, postal codes, PIN codes)
   - Blood groups (A+, B-, O+, AB+)
   - Categorical columns with a small fixed set of allowed values (check top_values)
   - Any column where values follow a clear pattern or belong to a known domain
   Do NOT apply to: phone columns (use PHONE VALIDATION), free-text descriptions, or arbitrary numeric columns.

5. TIMELINESS: Apply to date/datetime columns that represent events, creation times, update times, appointments, deadlines, DOB, admission/discharge dates, or any temporal event.

Other rules:
- detected_datatype: one of int, float, str, bool, date, datetime
- description: one short sentence
- regex_pattern: a practical generalized regex for the column values. For VALIDATION columns, make it match the expected pattern. For other columns, match the general format.
- Be noise-tolerant: ignore a small % of irregular/null values
- IMPORTANT: Apply multiple constraints per column. E.g., an email column should get NOT NULL + UNIQUENESS + VALIDATION. A phone column should get NOT NULL + PHONE VALIDATION. A status column should get NOT NULL + VALIDATION.

Return ONLY a valid JSON array. No markdown, no explanation, no code fences."""

            all_chunk_results = {}
            for chunk_idx, col_chunk in enumerate(col_chunks):
                profiles_json = json.dumps(col_chunk, default=str)
                filled_prompt = base_prompt.replace("SAMPLE_PERCENT", str(sample_percent)).replace("SAMPLE_SIZE", str(sample_size)).replace("TOTAL_ROWS", str(len(df)))
                prompt = filled_prompt + f"\n\nColumn profiles (batch {chunk_idx + 1}/{len(col_chunks)}):\n{profiles_json}"

                query = f"SELECT AI_COMPLETE('llama3.3-70b', $$ {prompt} $$) AS RAW_JSON"
                chunk_rows = session.sql(query).collect()
                chunk_raw = chunk_rows[0]["RAW_JSON"]
                chunk_match = re.search(r"\[.*\]", chunk_raw, re.S)
                if chunk_match:
                    chunk_json = chunk_match.group(0).strip()
                    chunk_json = chunk_json.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")
                    chunk_json = re.sub(r",\s*]", "]", chunk_json)
                    chunk_json = re.sub(r",\s*}", "}", chunk_json)
                    try:
                        parsed = json.loads(chunk_json)
                        for item in parsed:
                            cname = item.get("column_name", "")
                            if cname:
                                all_chunk_results[cname] = item
                    except json.JSONDecodeError:
                        pass

            raw_results = list(all_chunk_results.values())
            raw_text = json.dumps(raw_results)
            if raw_results:
                result_df = pd.json_normalize(raw_results)
                if "column_name" not in result_df.columns and "COLUMN_NAME" in result_df.columns:
                    result_df.rename(columns={"COLUMN_NAME": "column_name"}, inplace=True)
                st.session_state["result_df"] = result_df

                for col in df.columns:
                    if "column_name" not in result_df.columns:
                        st.session_state["constraints"][col] = []
                        continue
                    if col in result_df["column_name"].values:
                        pre_cons = result_df.loc[result_df["column_name"]==col,"applied_constraints"].values[0]
                        ai_regex = result_df.loc[result_df["column_name"]==col,"regex_pattern"].values[0] if "regex_pattern" in result_df.columns else ""
                        col_min = None
                        col_max = None
                        try:
                            if pd.api.types.is_integer_dtype(df[col]) or pd.api.types.is_float_dtype(df[col]):
                                col_min = df[col].min(skipna=True)
                                col_max = df[col].max(skipna=True)
                            if pd.api.types.is_datetime64_any_dtype(df[col]):
                                col_min = df[col].min(skipna=True)
                                col_max = df[col].max(skipna=True)
                        except Exception:
                            pass
                        st.session_state["ai_suggestions"].setdefault(col, {})
                        st.session_state["ai_suggestions"][col]["regex"] = ai_regex or ""
                        st.session_state["ai_suggestions"][col]["min"] = col_min
                        st.session_state["ai_suggestions"][col]["max"] = col_max

                        parsed = []
                        if isinstance(pre_cons, (list, tuple)):
                            for pc in pre_cons:
                                try:
                                    parsed.append({"type": normalize_type_from_ai(str(pc)), "value": "", "source":"ai", "case_sensitive": False})
                                except:
                                    parsed.append({"type": str(pc), "value":"", "source":"ai", "case_sensitive": False})
                        else:
                            try:
                                for pc in str(pre_cons).split(","):
                                    parsed.append({"type": normalize_type_from_ai(pc), "value":"", "source":"ai", "case_sensitive": False})
                            except:
                                parsed.append({"type": normalize_type_from_ai(str(pre_cons)), "value":"", "source":"ai", "case_sensitive": False})
                        st.session_state["constraints"][col] = parsed
                    else:
                        st.session_state["constraints"][col] = []

            st.session_state["col_descriptions"] = col_descriptions
        elapsed = round(time.time() - start_time, 2)
        st.success(f"Constraints detected and applied in {elapsed}s")
        st.session_state["dq_ready"] = True

    if st.session_state["dq_ready"] and st.session_state["df"] is not None:
        df = st.session_state["df"]
        if st.session_state.get("active_col") and st.session_state.get("active_col") not in df.columns:
            st.session_state["active_col"] = None
            st.session_state["edit_mode"] = None

        result_df = st.session_state.get("result_df", None)
        col_descriptions = st.session_state["col_descriptions"]

        st.markdown(
            '<div style="margin-top:1.5rem;margin-bottom:0.75rem;">'
            '<span style="font-size:1.1rem;font-weight:700;color:#1a1a2e;">Column Metadata & Constraints</span>'
            '</div>',
            unsafe_allow_html=True
        )

        header_html = (
            '<div style="display:grid;grid-template-columns:1.2fr 1.8fr 1fr 3.5fr 1fr;gap:12px;'
            'padding:10px 16px;background:#f0f2f6;border-radius:8px 8px 0 0;font-weight:600;'
            'font-size:0.8rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">'
            '<div>Column</div><div>Description</div><div>Datatype</div>'
            '<div>Constraints</div><div>Action</div></div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)

        python_dtypes = ["int", "float", "str", "bool", "date", "datetime"]
        dtype_map = {"int64":"int", "Int64":"int","float64":"float","object":"str","bool":"bool",
                     "datetime64[ns]":"datetime","datetime64[ns, UTC]":"datetime","datetime64":"datetime","date":"date"}

        for idx, col in enumerate(df.columns):
            bg = "#ffffff" if idx % 2 == 0 else "#fafbfc"
            st.markdown(
                f'<div style="border-left:1px solid #e8eaed;border-right:1px solid #e8eaed;'
                f'border-bottom:1px solid #e8eaed;background:{bg};"></div>',
                unsafe_allow_html=True
            )
            row_cols = st.columns([1.2, 1.8, 1, 3.5, 1])
            with row_cols[0]:
                st.markdown(f'<span style="font-weight:600;font-size:0.85rem;color:#1a1a2e;">{col}</span>', unsafe_allow_html=True)
            with row_cols[1]:
                desc_val = st.session_state["col_descriptions"].get(col, "—")
                if result_df is not None and "column_name" in result_df.columns and col in result_df["column_name"].values:
                    desc_val = result_df.loc[result_df["column_name"]==col, "description"].values[0]
                st.markdown(f'<span style="font-size:0.82rem;color:#555;">{desc_val}</span>', unsafe_allow_html=True)
            with row_cols[2]:
                dtype_val = dtype_map.get(str(df[col].dtype),"str")
                if result_df is not None and "column_name" in result_df.columns and col in result_df["column_name"].values:
                    dtype_val = result_df.loc[result_df["column_name"]==col,"detected_datatype"].values[0]
                    if dtype_val not in python_dtypes: dtype_val="str"
                selected_type = st.selectbox("", python_dtypes, index=python_dtypes.index(dtype_val), key=f"dtype_{col}", label_visibility="collapsed")
                try:
                    if selected_type=="int": df[col]=pd.to_numeric(df[col],errors="coerce").astype("Int64")
                    elif selected_type=="float": df[col]=pd.to_numeric(df[col],errors="coerce")
                    elif selected_type=="str": df[col]=df[col].astype(str)
                    elif selected_type=="bool": df[col]=df[col].astype(bool)
                    elif selected_type in ["datetime","date"]: df[col]=pd.to_datetime(df[col],errors="coerce")
                except: pass
                st.session_state["df"]=df
            with row_cols[3]:
                constraints = st.session_state["constraints"].get(col, [])
                if constraints:
                    badges = "".join([_constraint_badge_html(c) for c in constraints])
                    st.markdown(f'<div style="display:flex;flex-wrap:wrap;gap:3px;align-items:center;min-width:0;">{badges}</div>', unsafe_allow_html=True)
                else:
                    st.markdown('<span style="color:#9ca3af;font-size:0.8rem;">No constraints</span>', unsafe_allow_html=True)
            with row_cols[4]:
                if st.button("Manage", key=f"manage_{col}", use_container_width=True):
                    st.session_state["active_col"]=col
                    st.session_state["edit_mode"]=None
                    st.experimental_rerun()

        if st.session_state["active_col"]:
            active_col = st.session_state["active_col"]
            if active_col not in st.session_state["df"].columns:
                st.session_state["active_col"] = None
                st.session_state["edit_mode"] = None
            else:
                df = st.session_state["df"]
                existing = st.session_state["constraints"].get(active_col, [])
                ai_suggestions = st.session_state.get("ai_suggestions", {}).get(active_col, {})

                with st.sidebar:
                    st.markdown(
                        f'<div style="padding:12px 16px;background:linear-gradient(135deg,#1a1a2e,#0f3460);'
                        f'border-radius:10px;margin-bottom:1rem;">'
                        f'<p style="color:rgba(255,255,255,0.7);font-size:0.75rem;margin:0;">Managing constraints for</p>'
                        f'<p style="color:#fff;font-size:1rem;font-weight:700;margin:4px 0 0 0;">{active_col}</p>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                    if existing:
                        st.markdown('<p style="font-weight:600;font-size:0.85rem;color:#1a1a2e;margin-bottom:8px;">Current Constraints</p>', unsafe_allow_html=True)
                        for i,c in enumerate(existing):
                            st.markdown(_constraint_badge_html(c), unsafe_allow_html=True)
                            btn_cols = st.columns(2)
                            with btn_cols[0]:
                                if st.button("Edit", use_container_width=True, key=f"edit_{active_col}_{i}"):
                                    st.session_state["edit_mode"] = {
                                        "active": True,
                                        "col": active_col,
                                        "idx": i,
                                        "constraint": c.copy()
                                    }
                                    st.experimental_rerun()
                            with btn_cols[1]:
                                if st.button("Del", use_container_width=True, key=f"del_{active_col}_{i}"):
                                    st.session_state["constraints"][active_col].pop(i)
                                    st.session_state["edit_mode"]=None
                                    st.experimental_rerun()

                    st.markdown("---")
                    st.markdown('<p style="font-weight:600;font-size:0.85rem;color:#1a1a2e;">Add / Edit Constraint</p>', unsafe_allow_html=True)

                    edit_mode = st.session_state.get("edit_mode",None)
                    if edit_mode and edit_mode.get("col") != active_col:
                        st.session_state["edit_mode"] = None
                        edit_mode = None
                    if edit_mode:
                        default_type = edit_mode["constraint"]["type"]
                        default_value = edit_mode["constraint"].get("value", "")
                        default_case = edit_mode["constraint"].get("case_sensitive", False)
                    else:
                        default_type = "Not Null"
                        default_value = ""
                        default_case = False

                    constraint_types=[
                        "Not Null","Regex Match","Min Value","Max Value",
                        "Validation","Timeliness", "Phone Validation",
                        "Values Allowed (comma-separated)","Values Not Allowed (comma-separated)"
                    ]
                    col_is_datetime = pd.api.types.is_datetime64_any_dtype(df[active_col])
                    col_is_int = pd.api.types.is_integer_dtype(df[active_col]) or str(df[active_col].dtype).startswith("Int64")
                    col_is_float = pd.api.types.is_float_dtype(df[active_col])
                    col_is_str = pd.api.types.is_object_dtype(df[active_col]) or df[active_col].dtype == "string"

                    disabled_options = []
                    if col_is_datetime:
                        ctype_options = [t for t in constraint_types if "Values" not in t]
                    elif col_is_str:
                        ctype_options = constraint_types[:]
                        disabled_options = ["Min Value", "Max Value"]
                    else:
                        ctype_options = [t for t in constraint_types if t!="Timeliness"]

                    if default_type not in ctype_options:
                        default_type=ctype_options[0]

                    ctype, disabled_selected = render_selectbox_with_disabled(
                        "Constraint Type", ctype_options, disabled_options, default_type, f"ctype_sidebar_{active_col}"
                    )

                    cvalue=""
                    valid_input=True
                    validation_message = ""

                    if edit_mode and edit_mode.get("active"):
                        default_type = edit_mode["constraint"]["type"]
                        default_value = edit_mode["constraint"]["value"]
                        default_case = edit_mode["constraint"].get("case_sensitive", False)
                    else:
                        if not edit_mode:
                            default_value = ""
                            default_case = False

                    ai_regex_default = ai_suggestions.get("regex","") if ai_suggestions else ""
                    ai_min = ai_suggestions.get("min", None)
                    ai_max = ai_suggestions.get("max", None)

                    if ctype in ["Not Null", "Validation"]:
                        cvalue=""

                    elif ctype == "Phone Validation":
                        is_phone_col = any(k in active_col.lower() for k in ["phone", "mobile", "contact"])
                        if is_phone_col:
                            import phonenumbers
                            from phonenumbers import COUNTRY_CODE_TO_REGION_CODE
                            DEFAULT_REGION_OPTIONS = sorted({region for regions in COUNTRY_CODE_TO_REGION_CODE.values() for region in regions})

                            st.markdown('<p style="font-weight:600;font-size:0.85rem;">Phone Validation Settings</p>', unsafe_allow_html=True)
                            st.caption("Select a region for regional validation, or leave blank for global.")

                            suggested_region = None
                            if default_value and str(default_value).upper() in DEFAULT_REGION_OPTIONS:
                                suggested_region = str(default_value).upper()
                            elif f"default_phone_region_{active_col}" in st.session_state:
                                suggested_region = st.session_state[f"default_phone_region_{active_col}"]

                            region_options = [""] + DEFAULT_REGION_OPTIONS
                            selected_index = region_options.index(suggested_region) if suggested_region in region_options else 0

                            cvalue = st.selectbox(
                                "Default phone region (optional)",
                                region_options,
                                index=selected_index,
                                key=f"default_phone_region_select_{active_col}",
                                help="Leave blank if you do not want to specify a default region."
                            )

                            if cvalue == "" or cvalue is None:
                                st.session_state[f"default_phone_region_{active_col}"] = None
                                st.info("No region selected - validation will rely on explicit country codes.")
                            else:
                                st.session_state[f"default_phone_region_{active_col}"] = cvalue
                                st.success(f"Selected region: {cvalue}")
                        else:
                            st.sidebar.info("No validation settings available for this column.")
                            cvalue = None

                    elif ctype in ["Values Allowed (comma-separated)","Values Not Allowed (comma-separated)"]:
                        inp = st.text_input("Constraint Value (comma-separated)",value=default_value,key=f"cvalue_sidebar_{active_col}_{ctype.replace(' ', '_')}")
                        cvalue = inp
                        if col_is_int:
                            vals = [v.strip() for v in inp.split(",") if v.strip()!=""]
                            for v in vals:
                                try:
                                    int(v)
                                except:
                                    valid_input=False
                                    validation_message = "For integer columns, values must be integers."
                                    break

                    elif ctype == "Timeliness":
                        st.markdown('<p style="font-weight:600;font-size:0.85rem;">Timeliness Validation</p>', unsafe_allow_html=True)

                        detected_cols = detect_date_columns(df, detection_threshold=0.6)

                        if not detected_cols:
                            st.warning("No date-like columns detected.")
                            valid_input = False
                            cvalue = ""

                        auto_key = "timeliness_auto_order_full"
                        if auto_key not in st.session_state or set(st.session_state[auto_key]) != set(detected_cols):
                            _, _, auto_order = validate_timeliness(
                                df,
                                detected_cols,
                                pd.DataFrame(False, index=df.index, columns=df.columns),
                                [],
                                selected_timeliness_cols=None
                            )
                            st.session_state[auto_key] = auto_order or detected_cols

                        auto_order = st.session_state[auto_key]

                        selected_cols = st.multiselect(
                            "Select date columns to validate:",
                            options=detected_cols,
                            default=st.session_state.get("selected_timeliness_cols", detected_cols),
                            key=f"timeliness_select_{active_col}"
                        )

                        st.session_state["selected_timeliness_cols"] = selected_cols

                        if not selected_cols:
                            st.info("Select at least one column.")
                            valid_input = False
                            cvalue = ""

                        canonical_order = [c for c in auto_order if c in selected_cols]
                        for c in selected_cols:
                            if c not in canonical_order:
                                canonical_order.append(c)

                        rank_key = f"timeliness_ranks_{active_col}"
                        prev_key = f"{rank_key}_prev"

                        prev_cols = st.session_state.get(prev_key, [])
                        selection_changed = prev_cols != selected_cols

                        if selection_changed:
                            st.session_state[rank_key] = {
                                c: i + 1 for i, c in enumerate(canonical_order)
                            }

                        st.session_state[prev_key] = selected_cols.copy()
                        ranks = st.session_state[rank_key]

                        if len(selected_cols) >= 2:
                            st.markdown('<p style="font-size:0.82rem;font-weight:600;margin-top:8px;">Column Ordering</p>', unsafe_allow_html=True)

                            max_rank = len(selected_cols)
                            available = list(range(1, max_rank + 1))

                            for col in canonical_order:
                                cols_ui = st.columns([3, 1])
                                with cols_ui[0]:
                                    st.write(col)
                                with cols_ui[1]:
                                    current = ranks.get(col, canonical_order.index(col) + 1)
                                    if current not in available:
                                        current = canonical_order.index(col) + 1

                                    chosen = st.selectbox(
                                        "",
                                        available,
                                        index=available.index(current),
                                        key=f"timeliness_rank_{active_col}_{col}"
                                    )
                                    ranks[col] = int(chosen)

                            st.session_state[rank_key] = ranks

                            if len(set(ranks.values())) != len(selected_cols):
                                st.error("Order numbers must be unique (1,2,3...).")
                                valid_input = False


                            ordering_rule = {c: int(ranks[c]) for c in selected_cols}
                            st.session_state["timeliness_ordering"] = ordering_rule

                            ordered_cols = sorted(ordering_rule.items(), key=lambda x: x[1])
                            cvalue = json.dumps(ordering_rule)

                            valid_input = True

                        else:
                            st.info("Only one column selected - only invalid/future date checks will run.")
                            ordering_rule = None
                            valid_input = True
                            cvalue = f"{selected_cols[0]}:1"

                            st.session_state.pop("timeliness_ordering", None)



                    elif ctype in ["Min Value","Max Value"]:
                        if col_is_int or col_is_float:
                            suggested = ai_min if ctype=="Min Value" else ai_max
                            if suggested is None or (isinstance(suggested, float) and pd.isna(suggested)):
                                suggested = df[active_col].min(skipna=True) if ctype=="Min Value" else df[active_col].max(skipna=True)
                            if suggested is None or (isinstance(suggested, float) and pd.isna(suggested)):
                                suggested = 0
                            if col_is_int:
                                try:
                                    suggested_int = int(suggested)
                                except Exception:
                                    try:
                                        suggested_int = int(float(suggested))
                                    except Exception:
                                        suggested_int = 0
                                cvalue = st.number_input("Constraint Value", value=int(default_value) if default_value else suggested_int, step=1, format="%d", key=f"cvalue_sidebar_{active_col}_{ctype.replace(' ', '_')}")
                            else:
                                try:
                                    suggested_float = float(suggested)
                                except Exception:
                                    suggested_float = 0.0
                                cvalue = st.number_input("Constraint Value", value=float(default_value) if default_value else suggested_float, key=f"cvalue_sidebar_{active_col}_{ctype.replace(' ', '_')}")
                        elif col_is_datetime:
                            suggested = ai_min if ctype=="Min Value" else ai_max
                            if suggested is None or (isinstance(suggested, float) and pd.isna(suggested)):
                                suggested = df[active_col].min(skipna=True) if ctype=="Min Value" else df[active_col].max(skipna=True)
                            if isinstance(suggested, pd.Timestamp):
                                suggested_date = suggested.to_pydatetime().date()
                            elif isinstance(suggested, datetime):
                                suggested_date = suggested.date()
                            elif isinstance(suggested, date):
                                suggested_date = suggested
                            else:
                                suggested_date = date.today()
                            cvalue = st.date_input("Constraint Value (date)", value=pd.to_datetime(default_value).date() if default_value else suggested_date, key=f"cvalue_sidebar_{active_col}_{ctype.replace(' ', '_')}")
                        else:
                            cvalue = st.text_input("Constraint Value",value=default_value,key=f"cvalue_sidebar_{active_col}_{ctype.replace(' ', '_')}")

                    elif ctype=="Regex Match":
                        if edit_mode and edit_mode.get("active") and edit_mode["constraint"]["type"] == "Regex Match":
                            default_regex = default_value
                        else:
                            default_regex = ai_regex_default or ""
                        cvalue = st.text_input("Regex Pattern", value=default_regex, key=f"cvalue_sidebar_{active_col}_Regex_Match")
                        try:
                            re.compile(cvalue)
                        except:
                            valid_input=False
                            validation_message = "Invalid regular expression pattern."
                    else:
                        cvalue = st.text_input("Constraint Value",value=default_value,key=f"cvalue_sidebar_{active_col}_{ctype.replace(' ', '_')}")

                    case_sensitive_constraint_types = [
                        "Regex Match",
                        "Values Allowed (comma-separated)",
                        "Values Not Allowed (comma-separated)"
                    ]

                    if ctype in case_sensitive_constraint_types:
                        case_checkbox = st.checkbox(
                            "Case-sensitive comparison",
                            value=bool(default_case),
                            key=f"case_sensitive_checkbox_{active_col}_{ctype}"
                        )
                    else:
                        case_checkbox = False

                    chosen_key = normalize_type_for_compare(ctype)
                    ai_has_same = any((normalize_type_for_compare(c["type"])==chosen_key and c.get("source","")=="ai") for c in existing)
                    manual_has_same = any((normalize_type_for_compare(c["type"])==chosen_key and c.get("source","")!="ai") for c in existing)

                    duplicate_exists = any(
                        normalize_type_for_compare(c["type"]) == chosen_key
                        for c in existing
                    )

                    is_editing_same_constraint = (
                        edit_mode
                        and normalize_type_for_compare(edit_mode["constraint"]["type"]) == chosen_key
                    )

                    if duplicate_exists and not is_editing_same_constraint:
                        st.warning(
                            f"Constraint `{ctype}` already exists on this column. "
                            "Edit the existing one instead."
                        )
                        can_save = False
                    else:
                        can_save = True


                    if not valid_input:
                        st.error(validation_message)

                    col1,col2=st.columns(2)
                    with col1:
                        if st.button(
                            "Save",
                            key=f"apply_sidebar_{active_col}",
                            disabled=not can_save or not valid_input,
                            type="primary",
                            use_container_width=True
                        ):
                            if not can_save or not valid_input:
                                st.stop()

                            source = "manual"
                            new_value = str(cvalue) if cvalue is not None else ""

                            new_constraint = {
                                "type": ctype,
                                "value": new_value,
                                "source": source,
                                "case_sensitive": bool(case_checkbox)
                            }

                            if edit_mode and edit_mode.get("active"):
                                idx = edit_mode["idx"]
                                col_name = edit_mode["col"]

                                if col_name in st.session_state["constraints"] and idx < len(st.session_state["constraints"][col_name]):
                                    st.session_state["constraints"][col_name][idx] = new_constraint

                                st.session_state["edit_mode"] = None

                            else:
                                st.session_state["constraints"].setdefault(active_col, []).append(new_constraint)

                            try:
                                from phonenumbers import COUNTRY_CODE_TO_REGION_CODE
                                VALID_REGIONS = {r for regions in COUNTRY_CODE_TO_REGION_CODE.values() for r in regions}
                            except Exception:
                                VALID_REGIONS = set()

                            if ctype == "Phone Validation" and new_value.strip().upper() in VALID_REGIONS:
                                st.session_state["default_phone_region"] = new_value.strip().upper()

                            st.experimental_rerun()


                    with col2:
                        if st.button("Clear All", key=f"clear_sidebar_{active_col}", use_container_width=True):

                            has_timeliness = any(
                                normalize_type_for_compare(c["type"]) == "timeliness"
                                for c in st.session_state["constraints"].get(active_col, [])
                            )

                            if has_timeliness:
                                for col, cons in st.session_state["constraints"].items():
                                    st.session_state["constraints"][col] = [
                                        c for c in cons
                                        if normalize_type_for_compare(c["type"]) != "timeliness"
                                    ]
                                st.session_state["timeliness_ordering"] = None
                            else:
                                st.session_state["constraints"][active_col] = []

                            st.session_state["edit_mode"] = None
                            st.experimental_rerun()

        st.markdown("---")

        action_col1, action_col2, action_col3 = st.columns(3)
        with action_col1:
            if st.button("Save Rules", use_container_width=True, type="primary"):
                save_dq_pipeline_to_snowflake()
        with action_col2:
            if st.button("Schedule It", use_container_width=True):
                st.session_state["selected_constraints"] = st.session_state["constraints"]
                st.session_state["selected_table"] = table
                st.session_state["selected_schema"] = schema
                st.session_state["selected_database"] = database
                st.session_state["schedule_from_constraints"] = True
                st.session_state["active_tab"] = "schedule"
                st.experimental_rerun()

        if "dq_ready" not in st.session_state:
            st.session_state.dq_ready = False

        if "show_persist" not in st.session_state:
            st.session_state.show_persist = False

        if "persisted" not in st.session_state:
            st.session_state.persisted = False

        with action_col3:
            run_dq = st.button("Run Data Quality Check", use_container_width=True, type="primary")

        if run_dq:

            st.session_state.execution_context = {
                "database": st.session_state.db_sel,
                "schema": st.session_state.schema_sel,
                "table": st.session_state.table_sel,
                "timestamp": datetime.utcnow()
            }

            ctx = st.session_state.execution_context

            df = session.table(
                f'"{ctx["database"]}"."{ctx["schema"]}"."{ctx["table"]}"'
            ).to_pandas()


            dq_results=[]
            for col,cons in st.session_state["constraints"].items():
                if col not in df.columns:
                    dq_results.append({
                        "Column": col,
                        "Constraint": "SKIPPED",
                        "Value": "",
                        "Failed Rows": "COLUMN_MISSING"
                    })
                    continue

                for c in cons:
                    failed=None
                    t = normalize_type_for_compare(c["type"])
                    case_sensitive = bool(c.get("case_sensitive", False))

                    if t == normalize_type_for_compare("Not Null"):
                        failed=int(df[col].isnull().sum())

                    elif t == normalize_type_for_compare("Uniqueness"):
                        try:
                            s = df[col].astype(str).str.strip().str.lower()
                            vc = s.value_counts()
                            dup_mask = s.map(vc) > 1
                            failed = int(dup_mask.sum())
                        except Exception:
                            failed = "Skipped"

                    elif t == normalize_type_for_compare("Regex Match"):
                        try:
                            matched = regex_match_series(df[col], str(c["value"]), case_sensitive=case_sensitive)
                            failed = int((~matched).sum())
                        except Exception:
                            failed="REGEX_ERROR"

                    elif t == normalize_type_for_compare("Values Allowed (comma-separated)"):
                        allowed=[v.strip() for v in str(c["value"]).split(",") if v.strip()!=""]
                        if pd.api.types.is_integer_dtype(df[col]):
                            try:
                                allowed_parsed = [int(x) for x in allowed if x!=""]
                                failed_series = ~df[col].isin(allowed_parsed)
                                failed=int(failed_series.sum())
                            except Exception:
                                failed="PARSE_ERROR"
                        else:
                            normalized = normalize_series_for_compare(df[col], case_sensitive=case_sensitive)
                            if case_sensitive:
                                failed_series = ~normalized.isin([v for v in allowed])
                            else:
                                allowed_lower = [x.lower() for x in allowed]
                                failed_series = ~normalized.isin(allowed_lower)
                            failed=int(failed_series.sum())

                    elif t == normalize_type_for_compare("Values Not Allowed (comma-separated)"):
                        not_allowed=[v.strip() for v in str(c["value"]).split(",") if v.strip()!=""]
                        if pd.api.types.is_integer_dtype(df[col]):
                            try:
                                not_allowed_parsed = [int(x) for x in not_allowed if x!=""]
                                failed_series = df[col].isin(not_allowed_parsed)
                                failed=int(failed_series.sum())
                            except Exception:
                                failed="PARSE_ERROR"
                        else:
                            normalized = normalize_series_for_compare(df[col], case_sensitive=case_sensitive)
                            if case_sensitive:
                                failed_series = normalized.isin([v for v in not_allowed])
                            else:
                                na_lower = [x.lower() for x in not_allowed]
                                failed_series = normalized.isin(na_lower)
                            failed=int(failed_series.sum())

                    elif t == normalize_type_for_compare("Min Value"):
                        try:
                            if pd.api.types.is_datetime64_any_dtype(df[col]):
                                if isinstance(c["value"], (date, datetime)):
                                    threshold = pd.to_datetime(c["value"])
                                else:
                                    threshold = pd.to_datetime(str(c["value"]))
                                failed = int((df[col] < threshold).sum())
                            else:
                                failed = int((df[col] < float(c["value"])).sum())
                        except Exception:
                            failed = "ERR"

                    elif t == normalize_type_for_compare("Max Value"):
                        try:
                            if pd.api.types.is_datetime64_any_dtype(df[col]):
                                if isinstance(c["value"], (date, datetime)):
                                    threshold = pd.to_datetime(c["value"])
                                else:
                                    threshold = pd.to_datetime(str(c["value"]))
                                failed = int((df[col] > threshold).sum())
                            else:
                                failed = int((df[col] > float(c["value"])).sum())
                        except Exception:
                            failed = "ERR"
                    else:
                        failed = "SKIPPED"

                    dq_results.append({
                        "Column":col,
                        "Constraint":c["type"],
                        "Value":c["value"],
                        "Failed Rows":failed
                    })

            dq_df=pd.DataFrame(dq_results)

            df = st.session_state["df"]

            not_null_cols = set()
            for col, cons in st.session_state["constraints"].items():
                for c in cons:
                    if normalize_type_for_compare(c["type"]) == normalize_type_for_compare("Not Null"):
                        not_null_cols.add(col)

            missing_mask = build_missing_mask(df, not_null_columns=not_null_cols)

            completeness_counts = (missing_mask.sum()).sort_values(ascending=False)
            completeness_counts = completeness_counts[completeness_counts > 0]
            completeness_report = pd.DataFrame({"Missing/Empty Count": completeness_counts})

            constraints_records = []
            for col, cons in st.session_state["constraints"].items():
                for c in cons:
                    constraints_records.append({
                        "column_name": col,
                        "constraint_type": c.get("type", ""),
                        "value": c.get("value", ""),
                        "source": c.get("source", ""),
                        "case_sensitive": bool(c.get("case_sensitive", False))
                    })
            constraints_df = pd.DataFrame(constraints_records) if constraints_records else pd.DataFrame(columns=["column_name","constraint_type","value","source","case_sensitive"])

            validation_report, validation_mask, all_results_df = run_validation(df, constraints_df)

            anomaly_mask = validation_mask.reindex(index=df.index, columns=df.columns, fill_value=False) | missing_mask

            error_list = []
            invalid_results = all_results_df[all_results_df["IsValid"] == False] if not all_results_df.empty else pd.DataFrame(columns=all_results_df.columns)

            for row_idx in df.index:
                row_errors = {}
                for col in not_null_cols:
                    if col in df.columns:
                        try:
                            if missing_mask.loc[row_idx, col]:
                                row_errors[col] = "Missing / Empty value"
                        except Exception:
                            pass
                if not invalid_results.empty:
                    row_problem_details = invalid_results[invalid_results["RowIndex"] == row_idx]
                    for _, r in row_problem_details.iterrows():
                        colname = r["Column"]
                        msg = r["ErrorMessage"] if r["ErrorMessage"] else "Invalid value"
                        row_errors[colname] = msg

                for col in df.columns:
                    try:
                        if validation_mask.loc[row_idx, col] and col not in row_errors:
                            row_errors[col] = "Timeliness / validation rule violation"
                    except Exception:
                        pass

                error_list.append(row_errors)

            df_with_errors = df.copy()
            df_with_errors["Errors"] = error_list

            df_with_errors["__error_count__"] = df_with_errors["Errors"].apply(lambda d: len(d) if isinstance(d, dict) else 0)
            df_sorted = df_with_errors.sort_values("__error_count__", ascending=False).drop(columns=["__error_count__"])

            missing_mask_sorted = missing_mask.reindex(index=df_sorted.index)
            validation_mask_sorted = validation_mask.reindex(index=df_sorted.index)
            anomaly_mask_sorted = validation_mask.reindex(index=df_sorted.index, fill_value=False) | missing_mask_sorted

            rows_with_errors = anomaly_mask_sorted.any(axis=1)

            invalid_df = df_sorted.loc[rows_with_errors].copy()
            valid_df = df_sorted.loc[~rows_with_errors].copy()

            st.markdown("---")
            st.markdown(
                '<div style="margin-bottom:1rem;">'
                '<span style="font-size:1.1rem;font-weight:700;color:#1a1a2e;">Quality Check Results</span>'
                '</div>',
                unsafe_allow_html=True
            )

            m1, m2, m3 = st.columns(3)
            m1.metric("Total Rows", f"{len(df):,}")
            m2.metric("Columns", len(df.columns))
            m3.metric("Missing Values", f"{int(missing_mask.sum().sum()):,}")

            with st.expander("Completeness Report", expanded=True):
                st.dataframe(completeness_report, use_container_width=True)
                if not completeness_report.empty:
                    fig = px.bar(
                        completeness_report, y="Missing/Empty Count", x=completeness_report.index,
                        text="Missing/Empty Count",
                        color_discrete_sequence=["#0f3460"]
                    )
                    fig.update_layout(
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="Inter, sans-serif"),
                        margin=dict(t=20, b=20)
                    )
                    st.plotly_chart(fig, use_container_width=True)

            with st.expander("Validation Report", expanded=True):
                if validation_report.empty:
                    st.success("No validation issues found")
                else:
                    st.dataframe(validation_report, use_container_width=True)
                    try:
                        fig = px.bar(
                            validation_report, x="Column", y="Invalid Count", color="Description",
                            text="Invalid Count",
                            color_discrete_sequence=px.colors.qualitative.Set2
                        )
                    except Exception:
                        fig = px.bar(
                            validation_report, x="Column", y="Invalid Count", text="Invalid Count",
                            color_discrete_sequence=["#0f3460"]
                        )
                    fig.update_layout(
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="Inter, sans-serif"),
                        margin=dict(t=20, b=20)
                    )
                    st.plotly_chart(fig, use_container_width=True)

            total_cells = len(df) * len(df.columns)
            missing_total = missing_mask.sum().sum()
            invalid_total = validation_mask.sum().sum()
            try:
                constraint_fail_total = sum([r["Failed Rows"] for r in dq_results if isinstance(r["Failed Rows"], int)])
            except Exception:
                constraint_fail_total = 0
            bad = missing_total + invalid_total + constraint_fail_total
            valid_total = max(total_cells - bad, 0)
            dq_score = round((valid_total / total_cells) * 100, 2) if total_cells > 0 else 0

            st.markdown(
                '<div style="margin-top:1.5rem;margin-bottom:0.5rem;">'
                '<span style="font-size:1.1rem;font-weight:700;color:#1a1a2e;">Overall Data Health</span>'
                '</div>',
                unsafe_allow_html=True
            )
            accuracy = int(round(dq_score))

            def render_donut(value):
                if value >= 90:
                    colors = ["#2e7d32", "#e8f5e9"]
                elif value >= 70:
                    colors = ["#f57f17", "#fff8e1"]
                else:
                    colors = ["#c62828", "#fce4ec"]
                fig = go.Figure(data=[go.Pie(
                    values=[value, 100 - value],
                    hole=0.75,
                    textinfo="none",
                    sort=False,
                    direction="clockwise",
                    rotation=0,
                    marker=dict(colors=colors)
                )])
                fig.update_layout(
                    annotations=[dict(
                        text=f"<b style='font-size:28px'>{value}%</b><br><span style='color:#6b7280;font-size:13px'>Data Health</span>",
                        x=0.5, y=0.5,
                        font_size=22, showarrow=False
                    )],
                    showlegend=False,
                    margin=dict(t=10, b=10, l=10, r=10),
                    height=300,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                return fig

            donut_placeholder = st.empty()
            step = max(1, int(max(1, accuracy / 25)))
            for i in range(0, accuracy + 1, step):
                fig = render_donut(i)
                donut_placeholder.plotly_chart(fig, use_container_width=True)
                time.sleep(0.02)
            donut_placeholder.plotly_chart(render_donut(accuracy), use_container_width=True, key="final")


            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Valid Records", f"{len(valid_df):,}",
                          delta=f"{round(len(valid_df)/len(df)*100, 1)}% of total")
            with col2:
                st.metric("Invalid Records", f"{len(invalid_df):,}",
                          delta=f"{round(len(invalid_df)/len(df)*100, 1)}% of total")
            with col3:
                st.metric("Total Records", f"{len(df):,}")


            st.session_state["df_sorted"] = df_sorted
            st.session_state["valid_df"] = valid_df
            st.session_state["invalid_df"] = invalid_df

            st.session_state["dq_ready"] = True
            st.session_state.show_persist = True

            st.session_state.persisted = False

            st.success(
                f"DQ completed for {ctx['database']}.{ctx['schema']}.{ctx['table']}"
            )


        if st.session_state.show_persist and st.session_state.execution_context:

            ctx = st.session_state.execution_context

            with st.expander("Store Data Quality Results", expanded=True):
                st.markdown(
                    '<p style="font-size:0.85rem;color:#6b7280;margin-bottom:12px;">'
                    'Persist validated and quarantined records to Snowflake tables.</p>',
                    unsafe_allow_html=True
                )

                store_valid = st.checkbox("Store VALID records", value=True)
                st.caption("Invalid records are always stored.")

                default_valid_path = f"{ctx['database']}.DQ_VALID.{ctx['table']}"
                default_invalid_path = f"{ctx['database']}.DQ_QUARANTINE.{ctx['table']}"

                dest_col1, dest_col2 = st.columns(2)
                with dest_col1:
                    valid_path = st.text_input(
                        "Valid Destination",
                        default_valid_path
                    )
                with dest_col2:
                    invalid_path = st.text_input(
                        "Invalid Destination",
                        default_invalid_path
                    )

                persist_now = st.button("Store Results", type="primary", use_container_width=True)

            def parse_fqn(path: str):
                parts = [p.strip() for p in path.split(".") if p.strip()]

                if len(parts) != 3:
                    raise ValueError(
                        "Destination must be in format: DATABASE.SCHEMA.TABLE"
                    )

                return parts[0], parts[1], parts[2]

            def ensure_schema_and_table(session, database, schema, table, df):

                if df is None or df.empty:
                    return

                database = database.strip().upper()
                schema = schema.strip().upper()
                table = table.strip().upper()

                session.sql(
                    f'CREATE SCHEMA IF NOT EXISTS "{database}"."{schema}"'
                ).collect()

                exists = session.sql(f"""
                    SELECT 1
                    FROM "{database}".INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_SCHEMA = '{schema}'
                      AND TABLE_NAME = '{table}'
                    LIMIT 1
                """).collect()

                if not exists:
                    sp_df = session.create_dataframe(df.head(0))
                    sp_df.write \
                        .mode("overwrite") \
                        .save_as_table(f'"{database}"."{schema}"."{table}"')

            def normalize_for_snowflake(df: pd.DataFrame | None) -> pd.DataFrame | None:
                if df is None or df.empty:
                    return None

                df = df.copy()

                for col in df.columns:
                    if df[col].dtype == "object":
                        df[col] = df[col].apply(
                            lambda x: json.dumps(x)
                            if isinstance(x, (dict, list))
                            else str(x) if x is not None else None
                        )

                return df


            if st.session_state.show_persist and persist_now:

                if st.session_state.persisted:
                    st.info(
                        "Data already stored for this run. "
                        "Run quality checks again to store a new run."
                    )
                    st.stop()

                try:
                    valid_db, valid_schema, valid_table = parse_fqn(valid_path)
                    invalid_db, invalid_schema, invalid_table = parse_fqn(invalid_path)
                except ValueError as e:
                    st.error(str(e))
                    st.stop()

                valid_df = st.session_state.valid_df
                invalid_df = st.session_state.invalid_df

                st.session_state["valid_destination_path"] = valid_path
                st.session_state["invalid_destination_path"] = invalid_path

                st.session_state["valid_destination"] = {
                    "valid_database": valid_db,
                    "valid_schema": valid_schema,
                    "valid_table": valid_table
                }

                st.session_state["invalid_destination"] = {
                    "invalid_database": invalid_db,
                    "invalid_schema": invalid_schema,
                    "invalid_table": invalid_table
                }


                invalid_df_sf = normalize_for_snowflake(invalid_df)

                if invalid_df_sf is not None:
                    ensure_schema_and_table(
                        session,
                        invalid_db,
                        invalid_schema,
                        invalid_table,
                        invalid_df_sf
                    )

                    session.create_dataframe(invalid_df_sf) \
                        .write \
                        .mode("overwrite") \
                        .save_as_table(
                            f'"{invalid_db}"."{invalid_schema}"."{invalid_table}"'
                        )

                if store_valid:
                    valid_df_sf = normalize_for_snowflake(valid_df)

                    if valid_df_sf is not None:

                        valid_df_sf = valid_df_sf.drop(columns=["Errors"], errors="ignore")

                        ensure_schema_and_table(
                            session,
                            valid_db,
                            valid_schema,
                            valid_table,
                            valid_df_sf
                        )

                        session.create_dataframe(valid_df_sf) \
                            .write \
                            .mode("overwrite") \
                            .save_as_table(
                                f'"{valid_db}"."{valid_schema}"."{valid_table}"'
                            )

                st.session_state.persisted = True


                st.success("Data successfully persisted")

            valid_path = st.session_state.get("valid_destination_path")
            invalid_path = st.session_state.get("invalid_destination_path")

            valid_dest = st.session_state.get("valid_destination")
            invalid_dest = st.session_state.get("invalid_destination")

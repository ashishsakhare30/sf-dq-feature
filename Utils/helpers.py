import streamlit as st
import pandas as pd
import re


def _ensure_state():
    st.session_state.setdefault("dq_ready", False)
    st.session_state.setdefault("df", None)
    st.session_state.setdefault("col_descriptions", {})
    st.session_state.setdefault("constraints", {})  # dict of col -> list of {"type","value","source","case_sensitive"}
    st.session_state.setdefault("active_col", None)
    st.session_state.setdefault("edit_mode", None)
    st.session_state.setdefault("result_df", None)
    st.session_state.setdefault("refresh_flag", False)
    st.session_state.setdefault("ai_suggestions", {})  # store ai suggestions like regex and min/max
    st.session_state.setdefault("default_phone_region", "")  # default region if not set
    # global timeliness ordering (list of column names in desired order)
    st.session_state.setdefault("timeliness_ordering", None)


def normalize_type_from_ai(s: str):
    s = s.strip().upper()
    if s in ["NOT NULL", "NOT_NULL", "NOTNULL"]:
        return "Not Null"
    if s in ["UNIQUENESS", "UNIQUE", "UNIQUENESS_CHECK"]:
        return "Uniqueness"
    if s in ["REGEX", "REGEX MATCH", "REGEX_MATCH"]:
        return "Regex Match"
    if s in ["MIN", "MIN VALUE"]:
        return "Min Value"
    if s in ["MAX", "MAX VALUE"]:
        return "Max Value"
    if s in ["VALIDATION"]:
        return "Validation"
    if s in ["PHONE VALIDATION"]:
        return "Phone Validation"
    if s in ["TIMELINESS"]:
        return "Timeliness"
    if "VALUES ALLOWED" in s:
        return "Values Allowed (comma-separated)"
    if "VALUES NOT" in s:
        return "Values Not Allowed (comma-separated)"
    return s.title()


def normalize_type_for_compare(display_type: str):
    return re.sub(r'[^a-z0-9]', '', display_type).lower()


def render_selectbox_with_disabled(label, options, disabled_opts, default, key):
    labels = []
    for opt in options:
        if opt in disabled_opts:
            labels.append(f"{opt} (disabled)")
        else:
            labels.append(opt)
    if default in disabled_opts:
        default_label = f"{default} (disabled)"
    else:
        default_label = default
    if default_label not in labels:
        default_idx = 0
    else:
        default_idx = labels.index(default_label)
    selected_label = st.selectbox(label, labels, index=default_idx, key=key)
    disabled_selected = selected_label.endswith(" (disabled)")
    selected = selected_label.replace(" (disabled)", "")
    return selected, disabled_selected


def prune_session_for_new_df(df: pd.DataFrame):
    """
    Remove/trim session-state entries that refer to columns not present in the newly loaded df.
    Keeps constraints and ai_suggestions only for columns present in df.
    Also clears per-column phone-region keys and per-constraint UI keys that don't match.
    Ensures active_col/edit_mode are valid for the new df (clears them otherwise).
    """
    if df is None:
        st.session_state["constraints"] = {}
        st.session_state["ai_suggestions"] = {}
        st.session_state["col_descriptions"] = {}
        st.session_state["active_col"] = None
        st.session_state["edit_mode"] = None
        st.session_state["selected_timeliness_cols"] = None
        st.session_state["detected_timeliness_order"] = None
        return

    cols = set(df.columns.tolist())

    existing_constraints = st.session_state.get("constraints", {}) or {}
    pruned_constraints = {c: existing_constraints[c] for c in existing_constraints if c in cols}
    for c in cols:
        pruned_constraints.setdefault(c, [])
    st.session_state["constraints"] = pruned_constraints

    existing_ai = st.session_state.get("ai_suggestions", {}) or {}
    pruned_ai = {c: existing_ai[c] for c in existing_ai if c in cols}
    for c in cols:
        pruned_ai.setdefault(c, {})
    st.session_state["ai_suggestions"] = pruned_ai

    keys_to_remove = []
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and k.startswith("default_phone_region_"):
            suffix = k.replace("default_phone_region_", "")
            if suffix not in cols:
                keys_to_remove.append(k)
    for k in keys_to_remove:
        st.session_state.pop(k, None)

    keys_to_remove = []
    for k in list(st.session_state.keys()):
        if isinstance(k, str):
            for prefix in ("case_sensitive_checkbox_", "cvalue_sidebar_", "ctype_sidebar_", "timeliness_select_", "timeliness_rank_", "default_phone_region_select_"):
                if k.startswith(prefix):
                    rest = k[len(prefix):]
                    possible_col = rest.split("_")[0] if "_" in rest else rest
                    if possible_col not in cols:
                        keys_to_remove.append(k)
    for k in keys_to_remove:
        st.session_state.pop(k, None)

    sel_timeliness = st.session_state.get("selected_timeliness_cols", None)
    if sel_timeliness:
        st.session_state["selected_timeliness_cols"] = [c for c in sel_timeliness if c in cols]

    detected_order = st.session_state.get("detected_timeliness_order", None)
    if detected_order:
        st.session_state["detected_timeliness_order"] = [c for c in detected_order if c in cols]

    active = st.session_state.get("active_col", None)
    if active is None or active not in cols:
        st.session_state["active_col"] = None
        st.session_state["edit_mode"] = None
    else:
        em = st.session_state.get("edit_mode", None)
        if em and isinstance(em, dict):
            em_col = em.get("col")
            if em_col not in cols:
                st.session_state["edit_mode"] = None


def normalize_series_for_compare(series: pd.Series, case_sensitive: bool):
    """
    Return a Series of strings normalized for equality comparisons:
    - coerce to str
    - strip whitespace
    - if not case_sensitive -> lower()
    """
    s = series.fillna("").astype(str).str.strip()
    if not case_sensitive:
        s = s.str.lower()
    return s

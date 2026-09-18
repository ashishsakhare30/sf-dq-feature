import streamlit as st
import pandas as pd
import re

try:
    import phonenumbers
    from phonenumbers.phonenumberutil import NumberParseException
    from phonenumbers import PhoneNumberFormat
    PHONENUMBERS_AVAILABLE = True
except Exception:
    PHONENUMBERS_AVAILABLE = False

from utils.helpers import normalize_series_for_compare


def regex_match_series(series: pd.Series, pattern: str, case_sensitive: bool):
    """
    Perform regex FULLMATCH per-row, returning boolean Series.
    Avoids pandas-vectorized flag quirks by doing per-value checks.
    Normalizes each value via str(...) and .strip() before matching.
    """
    results = []
    if pattern is None or str(pattern).strip() == "":
        return pd.Series([False]*len(series), index=series.index)
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        compiled = re.compile(pattern, flags=flags)
    except re.error:
        return pd.Series([False]*len(series), index=series.index)
    for v in series:
        s = "" if pd.isnull(v) else str(v).strip()
        if s == "":
            results.append(False)
            continue
        try:
            if compiled.fullmatch(s):
                results.append(True)
            else:
                results.append(False)
        except Exception:
            results.append(False)
    return pd.Series(results, index=series.index)


def validate_name(df, col: str):
    results = []
    for idx, val in df[col].items():
        s = "" if pd.isnull(val) else str(val).strip()
        if not s:
            results.append((col, idx, val, False, "Name missing"))
        elif len(s) > 40:
            results.append((col, idx, val, False, "Too long"))
        else:
            results.append((col, idx, val, True, None))
    return pd.DataFrame(results, columns=["Column","RowIndex","Value","IsValid","ErrorMessage"])


def validate_gender(df, col: str):
    results = []
    for idx, val in df[col].items():
        sx = "" if pd.isnull(val) else str(val).strip().upper()
        if not sx:
            results.append((col, idx, val, False, "Missing"))
        elif sx not in {"M","F","OTHER","MALE","FEMALE"}:
            results.append((col, idx, val, False, f"Invalid gender '{val}'"))
        else:
            results.append((col, idx, val, True, None))
    return pd.DataFrame(results, columns=["Column","RowIndex","Value","IsValid","ErrorMessage"])


def validate_phone(df, col: str):
    """
    Validate phone numbers using the phonenumbers library.
    - When no region is set: validates all international numbers with country codes
    - When region is set (e.g., "US"): validates ONLY numbers from that region
    """
    results = []
    
    default_region = st.session_state.get(f"default_phone_region_{col}", None)
    
    if default_region is None or default_region == "" or (isinstance(default_region, str) and not default_region.strip()):
        default_region = None
        has_region = False
    else:
        default_region = str(default_region).strip()
        has_region = True
    
    allowed_pattern = re.compile(r"^\+?[0-9\-\s\(\)\.]+$")
    
    for idx, val in df[col].items():
        raw = val
        s = "" if pd.isnull(val) else str(val).strip()
        
        if s == "" or s.lower() in {"nan", "none", "null", "na", "-"}:
            results.append((col, idx, raw, False, "Missing"))
            continue
        
        if re.search(r"[A-Za-z]", s):
            results.append((col, idx, raw, False, "Invalid phone (contains letters)"))
            continue
        
        if s.count("+") > 1:
            results.append((col, idx, raw, False, "Invalid phone (multiple +)"))
            continue
        
        if not allowed_pattern.match(s):
            results.append((col, idx, raw, False, "Invalid phone (bad characters)"))
            continue
        
        if not has_region and not s.startswith("+"):
            results.append((col, idx, raw, False, "No region selected for non-international number"))
            continue
        
        if PHONENUMBERS_AVAILABLE:
            try:
                if s.startswith("+"):
                    pn = phonenumbers.parse(s, None)
                else:
                    if not has_region:
                        results.append((col, idx, raw, False, "Local number requires region selection"))
                        continue
                    pn = phonenumbers.parse(s, default_region)
                
                if has_region:
                    if phonenumbers.is_valid_number_for_region(pn, default_region):
                        formatted = phonenumbers.format_number(
                            pn, phonenumbers.PhoneNumberFormat.E164
                        )
                        results.append((col, idx, formatted, True, None))
                    else:
                        results.append((col, idx, raw, False, f"Not a valid {default_region} number"))
                else:
                    if phonenumbers.is_valid_number(pn):
                        formatted = phonenumbers.format_number(
                            pn, phonenumbers.PhoneNumberFormat.E164
                        )
                        results.append((col, idx, formatted, True, None))
                    else:
                        results.append((col, idx, raw, False, "Invalid phone number"))
                        
            except NumberParseException as e:
                results.append((col, idx, raw, False, f"Parse error: {str(e)}"))
            except Exception as e:
                results.append((col, idx, raw, False, f"Validation error: {str(e)}"))
        
        else:
            digits = re.sub(r"\D", "", s)
            if 7 <= len(digits) <= 15:
                results.append((col, idx, raw, True, None))
            else:
                results.append((col, idx, raw, False, "Invalid phone (fallback)"))
    
    return pd.DataFrame(results, columns=["Column", "RowIndex", "Value", "IsValid", "ErrorMessage"])


def validate_email(df, col: str):
    results = []
    for idx, val in df[col].items():
        s = "" if pd.isnull(val) else str(val).strip()
        if not s:
            results.append((col, idx, val, False, "Missing"))
        elif not re.fullmatch(r"^[\w\.-]+@[\w\.-]+\.\w+$", s):
            results.append((col, idx, val, False, "Invalid email"))
        else:
            results.append((col, idx, val, True, None))
    return pd.DataFrame(results, columns=["Column","RowIndex","Value","IsValid","ErrorMessage"])


def validate_postal(df, col: str):
    results = []
    for idx, val in df[col].items():
        s = "" if pd.isnull(val) else str(val).strip()
        if not re.fullmatch(r"\d{6}", s):
            results.append((col, idx, val, False, "Invalid postal"))
        else:
            results.append((col, idx, val, True, None))
    return pd.DataFrame(results, columns=["Column","RowIndex","Value","IsValid","ErrorMessage"])


def validate_amount(df, col: str):
    results = []
    for idx, val in df[col].items():
        s = "" if pd.isnull(val) else str(val).strip()
        if not s:
            results.append((col, idx, val, False, "Missing"))
            continue
        try:
            num = float(re.sub(r"[^\d\.\-]", "", s).replace(",", ""))
            if num <= 0:
                results.append((col, idx, val, False, "Not positive"))
            else:
                results.append((col, idx, val, True, None))
        except Exception:
            results.append((col, idx, val, False, "Invalid amount"))
    return pd.DataFrame(results, columns=["Column","RowIndex","Value","IsValid","ErrorMessage"])


def validate_age(df, col: str):
    results = []
    for idx, val in df[col].items():
        try:
            if pd.isnull(val) or str(val).strip()=="":
                results.append((col, idx, val, False, "Missing"))
                continue
            age = int(val)
            if age <= 0 or age > 120:
                results.append((col, idx, val, False, "Invalid age"))
            else:
                results.append((col, idx, val, True, None))
        except Exception:
            results.append((col, idx, val, False, "Invalid age"))
    return pd.DataFrame(results, columns=["Column","RowIndex","Value","IsValid","ErrorMessage"])


def choose_builtin_validator_by_column(col_name):
    col_lower = col_name.lower()
    if "email" in col_lower:
        return validate_email
    if "gender" in col_lower:
        return validate_gender
    if "name" in col_lower or "first" in col_lower or "last" in col_lower:
        return validate_name
    if "age" in col_lower:
        return validate_age
    if "postal" in col_lower or "pin" in col_lower or "zip" in col_lower:
        return validate_postal
    if "amount" in col_lower or "price" in col_lower or "cost" in col_lower:
        return validate_amount
    return None


def choose_phone_validator_by_column(col_name):
    col_lower = col_name.lower()
    if "phone" in col_lower or "contact" in col_lower or "mobile" in col_lower or "msisdn" in col_lower:
        return validate_phone


def detect_date_columns(df, detection_threshold=0.6):
    date_cols = []

    for col in df.columns:
        series = df[col].dropna().astype(str)

        if series.empty:
            continue

        digit_ratio = series.str.contains(r"\d").mean()
        if digit_ratio < 0.5:
            continue

        sep_ratio = series.str.contains(r"[-/.:]").mean()
        if sep_ratio < 0.3:
            continue

        parsed = pd.to_datetime(series, errors="coerce", infer_datetime_format=True)
        success_ratio = parsed.notna().mean()

        if success_ratio >= detection_threshold:
            date_cols.append(col)

    return date_cols


def validate_timeliness(
    df,
    date_cols,
    mask,
    summary_rows,
    detection_threshold=0.6,
    ordering_rule=None,
    selected_timeliness_cols=None
):
    today = pd.Timestamp.now().normalize()
    parsed_dates = {}

    if selected_timeliness_cols is not None:
        date_cols = [c for c in selected_timeliness_cols if c in df.columns]

        if len(date_cols) < 1:
            summary_rows.append({
                "Validation Type": "Timeliness",
                "Description": "No columns selected for timeliness validation."
            })
            return mask, summary_rows, []

    else:
        detected = []
        for col in df.columns:
            s = df[col].dropna().astype(str)
            if s.empty:
                continue
            if s.str.contains(r"\d").mean() < 0.5:
                continue
            if s.str.contains(r"[-/.:]").mean() < 0.3:
                continue

            parsed = pd.to_datetime(s, errors="coerce", infer_datetime_format=True)
            if parsed.notna().mean() >= detection_threshold:
                detected.append(col)

        date_cols = list(dict.fromkeys((date_cols or []) + detected))

    for col in date_cols:
        parsed = pd.to_datetime(df[col], errors="coerce", infer_datetime_format=True)
        parsed_dates[col] = parsed

        invalid = parsed.isna() & df[col].notna()
        future = parsed > today

        if invalid.any():
            mask.loc[invalid, col] = True
            summary_rows.append({
                "Column": col,
                "Invalid Count": int(invalid.sum()),
                "Description": "Invalid or unparsable date"
            })

        if future.any():
            mask.loc[future, col] = True
            summary_rows.append({
                "Column": col,
                "Invalid Count": int(future.sum()),
                "Description": "Future date detected"
            })

    if len(date_cols) < 2:
        return mask, summary_rows, date_cols

    if isinstance(ordering_rule, dict) and len(ordering_rule) >= 2:
        ordered = sorted(
            [(c, r) for c, r in ordering_rule.items() if c in date_cols],
            key=lambda x: x[1]
        )

        if len(ordered) >= 2:
            ordered_cols = [c for c, _ in ordered]

            for a, b in zip(ordered_cols, ordered_cols[1:]):
                d1, d2 = parsed_dates[a], parsed_dates[b]
                bad = (d1 >= d2) & d1.notna() & d2.notna()

                if bad.any():
                    mask.loc[bad, [a, b]] = True
                    summary_rows.append({
                        "Column": f"{a} / {b}",
                        "Invalid Count": int(bad.sum()),
                        "Description": f"Manual rule: {a} must be earlier than {b}"
                    })

            return mask, summary_rows, ordered_cols

    paired_orders = []

    for i in range(len(date_cols)):
        for j in range(i + 1, len(date_cols)):
            c1, c2 = date_cols[i], date_cols[j]

            d1, d2 = parsed_dates.get(c1), parsed_dates.get(c2)
            if d1 is None or d2 is None:
                continue

            if d1.notna().sum() < 5 or d2.notna().sum() < 5:
                continue

            m1, m2 = d1.median(), d2.median()
            if pd.isna(m1) or pd.isna(m2):
                continue

            earlier, later = (c1, c2) if m1 < m2 else (c2, c1)
            paired_orders.append((earlier, later))

            bad = (d1 >= d2) & d1.notna() & d2.notna()
            if bad.any():
                mask.loc[bad, [earlier, later]] = True
                summary_rows.append({
                    "Column": f"{earlier} / {later}",
                    "Invalid Count": int(bad.sum()),
                    "Description": f"{earlier} should be earlier than {later}"
                })

    graph = {c: set() for c in date_cols}
    for e, l in paired_orders:
        graph[e].add(l)

    visited, temp, topo = set(), set(), []
    cycle = False

    def dfs(node):
        nonlocal cycle
        if node in temp:
            cycle = True
            return
        if node in visited:
            return
        temp.add(node)
        for nxt in graph.get(node, []):
            dfs(nxt)
        temp.remove(node)
        visited.add(node)
        topo.append(node)

    for c in date_cols:
        if c not in visited:
            dfs(c)

    detected_order = topo[::-1] if not cycle and topo else date_cols

    return mask, summary_rows, detected_order


def validate_by_regex(df, col: str, pattern: str, case_sensitive: bool = False):
    """
    Validate values in df[col] using regex pattern with per-constraint case sensitivity.
    Returns DataFrame with columns: Column, RowIndex, Value, IsValid, ErrorMessage
    """
    results = []
    if pattern is None or str(pattern).strip() == "":
        for idx, val in df[col].items():
            results.append((col, idx, val, False, "No regex provided"))
        return pd.DataFrame(results, columns=["Column","RowIndex","Value","IsValid","ErrorMessage"])

    match_series = regex_match_series(df[col], pattern, case_sensitive=case_sensitive)

    for idx, is_ok in match_series.items():
        val = df[col].loc[idx]
        if pd.isnull(val) or str(val).strip() == "":
            results.append((col, idx, val, False, "Missing"))
        else:
            if is_ok:
                results.append((col, idx, str(val).strip(), True, None))
            else:
                results.append((col, idx, str(val).strip(), False, "Regex mismatch"))
    return pd.DataFrame(results, columns=["Column","RowIndex","Value","IsValid","ErrorMessage"])


def build_missing_mask(df, not_null_columns=None):
    """
    Build missing mask only for columns that have Not Null constraint.
    If not_null_columns is None, returns empty mask.
    """
    if not_null_columns is None or len(not_null_columns) == 0:
        return pd.DataFrame(False, index=df.index, columns=df.columns)
    
    mask = pd.DataFrame(False, index=df.index, columns=df.columns)
    
    for col in not_null_columns:
        if col in df.columns:
            empty_str = df[col].astype(str).apply(
                lambda x: str(x).strip() == "" or 
                         str(x).strip() == "-" or
                         str(x).strip().lower() == "none" or
                         str(x).strip().lower() == "null" or
                         str(x).strip().lower() == "na"
            )
            mask[col] = df[col].isna() | empty_str
    
    return mask


def run_validation(df: pd.DataFrame, constraints_df: pd.DataFrame):
    """
    Returns:
      - summary_df: DataFrame with columns ["Column","Invalid Count","Description"]
      - mask: DataFrame boolean mask marking invalid cells (index aligned to df, columns as df)
      - all_results_df: DataFrame of row-level validation results with columns:
          ["Column","RowIndex","Value","IsValid","ErrorMessage"]
    """

    mask = pd.DataFrame(False, index=df.index, columns=df.columns)
    summary_rows = []
    all_results = []

    not_null_columns = set()
    if constraints_df is not None and not constraints_df.empty:
        for _, row in constraints_df.iterrows():
            if row.get("constraint_type", "").strip().lower() == "not null":
                not_null_columns.add(row.get("column_name"))
    
    if constraints_df is None or constraints_df.empty:
        constraints_df = pd.DataFrame(columns=["column_name", "constraint_type", "value", "source", "case_sensitive"])
    timeliness_cols = []
    validated_columns = set()

    for _, row in constraints_df.iterrows():
        col = row.get("column_name")
        if col not in df.columns:
            summary_rows.append({
                "Column": col,
                "Invalid Count": 0,
                "Description": "Skipped: column not present in dataframe"
            })
            continue

        case_sensitive = bool(row.get("case_sensitive", False))

        ctype_raw = row.get("constraint_type", "")
        ctype = str(ctype_raw).strip().lower()
        cvalue = row.get("value", "")

        if ctype == "timeliness":
            timeliness_cols.append(col)
            summary_rows.append({
                "Column": col,
                "Invalid Count": 0,
                "Description": "Timeliness constraint requested (checked later)"
            })
            continue

        if ctype == "validation":
            validator = choose_builtin_validator_by_column(col)
            if validator is not None:
                col_results = validator(df, col)
                all_results.append(col_results)
                validated_columns.add(col)
                invalid_rows_idx = col_results.loc[~col_results["IsValid"], "RowIndex"].tolist()
                if invalid_rows_idx:
                    mask.loc[invalid_rows_idx, col] = True
                grouped = col_results.loc[~col_results["IsValid"]].groupby("ErrorMessage").size()
                if grouped.empty:
                    summary_rows.append({
                        "Column": col,
                        "Invalid Count": 0,
                        "Description": "Validation passed (builtin validator)"
                    })
                else:
                    for err, count in grouped.items():
                        summary_rows.append({
                            "Column": col,
                            "Invalid Count": int(count),
                            "Description": err
                        })
            else:
                ai_regex = st.session_state.get("ai_suggestions", {}).get(col, {}).get("regex", "")
                if ai_regex and str(ai_regex).strip():
                    col_results = validate_by_regex(df, col, ai_regex, case_sensitive=case_sensitive)
                    all_results.append(col_results)
                    validated_columns.add(col)
                    invalid_rows_idx = col_results.loc[~col_results["IsValid"], "RowIndex"].tolist()
                    if invalid_rows_idx:
                        mask.loc[invalid_rows_idx, col] = True
                    grouped = col_results.loc[~col_results["IsValid"]].groupby("ErrorMessage").size()
                    for err, count in grouped.items():
                        summary_rows.append({
                            "Column": col,
                            "Invalid Count": int(count),
                            "Description": err
                        })
                else:
                    summary_rows.append({
                        "Column": col,
                        "Invalid Count": 0,
                        "Description": "Validation requested but no regex available"
                    })
            continue

        failed_rows = pd.Series(False, index=df.index)
        if ctype == "not null":
            failed_rows = df[col].isna() | (df[col].astype(str).str.strip() == "")
            results = []
            for idx in df.index:
                val = df[col].loc[idx]
                is_valid = not failed_rows.loc[idx]
                if not is_valid:
                    results.append((col, idx, val, False, "Missing / Empty value"))
                else:
                    results.append((col, idx, val, True, None))
            
            col_results = pd.DataFrame(results, columns=["Column", "RowIndex", "Value", "IsValid", "ErrorMessage"])
            all_results.append(col_results)
            validated_columns.add(col)

        elif ctype == "regex match":
            try:
                matched = regex_match_series(df[col], str(cvalue), case_sensitive=case_sensitive)
                failed_rows = ~matched.fillna(False).astype(bool)
            except Exception:
                summary_rows.append({
                    "Column": col,
                    "Invalid Count": 0,
                    "Description": f"Invalid regex pattern: {cvalue}"
                })
                continue
                
        elif "values allowed" in ctype:
            allowed = [v.strip() for v in str(cvalue).split(",") if v.strip()]
            if pd.api.types.is_integer_dtype(df[col]):
                try:
                    allowed_parsed = [int(x) for x in allowed if x != ""]
                    failed_rows = ~df[col].isin(allowed_parsed)
                except Exception:
                    summary_rows.append({
                        "Column": col,
                        "Invalid Count": 0,
                        "Description": f"Invalid Allowed values (not integers for integer column): {cvalue}"
                    })
                    continue
            else:
                normalized = normalize_series_for_compare(df[col], case_sensitive=case_sensitive)
                if case_sensitive:
                    failed_rows = ~normalized.isin([v for v in allowed])
                else:
                    allowed_lower = [v.lower() for v in allowed]
                    failed_rows = ~normalized.isin(allowed_lower)

        elif "values not allowed" in ctype:
            not_allowed = [v.strip() for v in str(cvalue).split(",") if v.strip()]
            if pd.api.types.is_integer_dtype(df[col]):
                try:
                    not_allowed_parsed = [int(x) for x in not_allowed if x != ""]
                    failed_rows = df[col].isin(not_allowed_parsed)
                except Exception:
                    summary_rows.append({
                        "Column": col,
                        "Invalid Count": 0,
                        "Description": f"Invalid Not Allowed values (not integers for integer column): {cvalue}"
                    })
                    continue
            else:
                normalized = normalize_series_for_compare(df[col], case_sensitive=case_sensitive)
                if case_sensitive:
                    failed_rows = normalized.isin([v for v in not_allowed])
                else:
                    na_lower = [v.lower() for v in not_allowed]
                    failed_rows = normalized.isin(na_lower)

        elif ctype == "min value":
            try:
                if pd.api.types.is_datetime64_any_dtype(df[col]):
                    threshold = pd.to_datetime(cvalue)
                    failed_rows = df[col] < threshold
                else:
                    threshold = float(cvalue)
                    failed_rows = df[col].astype(float) < threshold
            except Exception:
                summary_rows.append({
                    "Column": col,
                    "Invalid Count": 0,
                    "Description": f"Invalid Min Value: {cvalue}"
                })
                continue
        elif ctype == "max value":
            try:
                if pd.api.types.is_datetime64_any_dtype(df[col]):
                    threshold = pd.to_datetime(cvalue)
                    failed_rows = df[col] > threshold
                else:
                    threshold = float(cvalue)
                    failed_rows = df[col].astype(float) > threshold
            except Exception:
                summary_rows.append({
                    "Column": col,
                    "Invalid Count": 0,
                    "Description": f"Invalid Max Value: {cvalue}"
                })
                continue

        elif ctype == "phone validation": 
            try:
                from phonenumbers import COUNTRY_CODE_TO_REGION_CODE
                VALID_REGIONS = {r for regions in COUNTRY_CODE_TO_REGION_CODE.values() for r in regions}
            except Exception:
                VALID_REGIONS = set()

            if isinstance(cvalue, str) and cvalue.strip().upper() in VALID_REGIONS:
                st.session_state["default_phone_region"] = cvalue.strip().upper()

            validator = choose_phone_validator_by_column(col) 
            if validator is not None:
                col_results = validator(df, col)
                all_results.append(col_results)
                invalid_rows_idx = col_results.loc[~col_results["IsValid"], "RowIndex"].tolist()
                if invalid_rows_idx:
                    mask.loc[invalid_rows_idx, col] = True
                grouped = col_results.loc[~col_results["IsValid"]].groupby("ErrorMessage").size()
                if grouped.empty:
                    summary_rows.append({
                        "Column": col,
                        "Invalid Count": 0,
                        "Description": "Validation passed (builtin validator)"
                    })
                else:
                    for err, count in grouped.items():
                        summary_rows.append({
                            "Column": col,
                            "Invalid Count": int(count),
                            "Description": err
                        })
            else:
                summary_rows.append({
                    "Column": col,
                    "Invalid Count": 0,
                    "Description": f"No builtin validator mapped for column '{col}'"
                })
            continue
        else:
            summary_rows.append({
                "Column": col,
                "Invalid Count": 0,
                "Description": f"Unknown constraint type: {ctype_raw}"
            })
            continue
            
        if ctype not in ["validation", "phone validation", "not null", "timeliness"]:
            failed_bool = failed_rows.fillna(False).astype(bool)
            if failed_bool.any():
                mask.loc[failed_bool, col] = True
            validated_columns.add(col)
            summary_rows.append({
                "Column": col,
                "Invalid Count": int(failed_bool.sum()),
                "Description": f"{ctype} violation"
            })

    selected_timeliness_cols = st.session_state.get("selected_timeliness_cols", None)
    
    if timeliness_cols and not selected_timeliness_cols:
        selected_timeliness_cols = timeliness_cols
        st.session_state["selected_timeliness_cols"] = timeliness_cols
    
    ordering_rule = st.session_state.get("timeliness_ordering", None)
    
    if selected_timeliness_cols and len(selected_timeliness_cols) > 0 and not ordering_rule:
        detected_cols = detect_date_columns(df, detection_threshold=0.6)
        if detected_cols:
            _, _, auto_order = validate_timeliness(
                df,
                detected_cols,
                pd.DataFrame(False, index=df.index, columns=df.columns),
                [],
                selected_timeliness_cols=None
            )
            ordering_rule = {col: idx + 1 for idx, col in enumerate(auto_order) if col in selected_timeliness_cols}
            st.session_state["timeliness_ordering"] = ordering_rule
    
    if selected_timeliness_cols and len(selected_timeliness_cols) > 0:
        date_cols = [
            c for c in df.columns if any(k in c.lower() for k in ["date", "time", "dt"])
        ]
        
        mask, summary_rows, detected_order = validate_timeliness(
            df, 
            date_cols,
            mask, 
            summary_rows, 
            ordering_rule=ordering_rule,
            selected_timeliness_cols=selected_timeliness_cols
        )
        
        st.session_state["detected_timeliness_order"] = detected_order
            
    all_results_df = pd.concat(all_results, ignore_index=True) if all_results else pd.DataFrame(
        columns=["Column", "RowIndex", "Value", "IsValid", "ErrorMessage"]
    )
    summary_df = pd.DataFrame(summary_rows, columns=["Column", "Invalid Count", "Description"])
    summary_df = summary_df[summary_df["Invalid Count"] > 0].reset_index(drop=True)
    return summary_df, mask, all_results_df

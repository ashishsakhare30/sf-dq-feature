import streamlit as st
import tempfile
from snowflake.snowpark.context import get_active_session

from utils.helpers import _ensure_state
from page.main_page import show_main_page
from page.schedule_page import show_schedule_page
from page.jobs_page import showjobdashboard, show_history_page
from page.dashboard_page import get_accuracy_and_validation

session = get_active_session()

st.set_page_config(page_title="Data Quality Suite", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* ── Global overrides ── */
.block-container {
    padding-top: 1rem !important;
    padding-bottom: 2rem !important;
    max-width: 1200px;
}

/* ── Header bar ── */
.dq-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1.5rem;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    border-radius: 12px;
    margin-bottom: 0.5rem;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
}
.dq-header-title {
    color: #ffffff;
    font-size: 1.35rem;
    font-weight: 700;
    letter-spacing: -0.3px;
}
.dq-header-subtitle {
    color: rgba(255,255,255,0.6);
    font-size: 0.8rem;
    font-weight: 400;
    margin-top: 2px;
}
.dq-header-badge {
    background: rgba(255,255,255,0.12);
    color: #e0e0e0;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 500;
    letter-spacing: 0.5px;
}

/* ── Navigation tabs ── */
.nav-container {
    display: flex;
    gap: 4px;
    padding: 4px;
    background: #f0f2f6;
    border-radius: 10px;
    margin-bottom: 1.5rem;
}
.nav-tab {
    flex: 1;
    text-align: center;
    padding: 10px 16px;
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 600;
    color: #555;
    cursor: pointer;
    transition: all 0.2s ease;
    text-decoration: none;
    border: none;
    background: transparent;
}
.nav-tab:hover {
    background: rgba(255,255,255,0.7);
    color: #333;
}
.nav-tab-active {
    background: #ffffff !important;
    color: #1a1a2e !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
.nav-icon {
    margin-right: 6px;
    font-size: 0.95rem;
}

/* ── Streamlit button styling for nav ── */
.nav-wrapper div[data-testid="stHorizontalBlock"] div.stButton > button {
    border: none !important;
    border-radius: 8px !important;
    padding: 10px 16px !important;
    background: transparent !important;
    font-size: 0.85rem !important;
    font-weight: 600 !important;
    color: #555 !important;
    transition: all 0.2s ease !important;
    box-shadow: none !important;
}
.nav-wrapper div[data-testid="stHorizontalBlock"] div.stButton > button:hover {
    background: rgba(255,255,255,0.7) !important;
    color: #333 !important;
}
.nav-wrapper div[data-testid="stHorizontalBlock"] div.stButton > button:focus {
    background: #ffffff !important;
    color: #1a1a2e !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08) !important;
    border: none !important;
}

/* ── Cards / containers ── */
div[data-testid="stExpander"] {
    border: 1px solid #e8eaed !important;
    border-radius: 10px !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04) !important;
    overflow: hidden;
}
div[data-testid="stExpander"] summary {
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    color: #1a1a2e !important;
}

/* ── Metric cards ── */
div[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #e8eaed;
    border-radius: 10px;
    padding: 16px 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
div[data-testid="stMetric"] label {
    color: #6b7280 !important;
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
    font-size: 1.6rem !important;
    font-weight: 700 !important;
    color: #1a1a2e !important;
}

/* ── Buttons ── */
.stButton > button {
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
    padding: 8px 20px !important;
    transition: all 0.2s ease !important;
    border: 1.5px solid #d1d5db !important;
    background: #ffffff !important;
    color: #1a1a2e !important;
}
.stButton > button:hover {
    background: #f0f2f6 !important;
    border-color: #0f3460 !important;
    color: #0f3460 !important;
}
button[kind="primary"], .stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #1a1a2e, #0f3460) !important;
    border: none !important;
    color: white !important;
}
button[kind="primary"]:hover {
    box-shadow: 0 4px 12px rgba(15,52,96,0.3) !important;
    transform: translateY(-1px);
}

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background: #fafbfc !important;
    border-right: 1px solid #e8eaed !important;
}
section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3 {
    color: #1a1a2e !important;
}

/* ── Dataframes ── */
div[data-testid="stDataFrame"] {
    border: 1px solid #e8eaed;
    border-radius: 8px;
    overflow: hidden;
}

/* ── Selectbox & inputs ── */
div[data-testid="stSelectbox"] > div > div,
div[data-testid="stTextInput"] > div > div > input,
div[data-testid="stNumberInput"] > div > div > input {
    border-radius: 8px !important;
    border-color: #d1d5db !important;
    font-size: 0.85rem !important;
}
div[data-testid="stSelectbox"] > div > div:focus-within,
div[data-testid="stTextInput"] > div > div > input:focus,
div[data-testid="stNumberInput"] > div > div > input:focus {
    border-color: #0f3460 !important;
    box-shadow: 0 0 0 2px rgba(15,52,96,0.15) !important;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 2px;
    background: #f0f2f6;
    border-radius: 8px;
    padding: 3px;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 6px;
    font-weight: 600;
    font-size: 0.82rem;
}
.stTabs [aria-selected="true"] {
    background: white !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}

/* ── Divider ── */
hr {
    border-color: #e8eaed !important;
    margin: 1rem 0 !important;
}

/* ── Page title area ── */
.page-title {
    font-size: 1.5rem;
    font-weight: 700;
    color: #1a1a2e;
    margin-bottom: 4px;
}
.page-subtitle {
    font-size: 0.88rem;
    color: #6b7280;
    margin-bottom: 1.5rem;
}

/* ── Active tab highlight for nav buttons ── */
.nav-active-btn > button {
    background: #ffffff !important;
    color: #1a1a2e !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08) !important;
}

/* ── Toast / status messages ── */
div[data-testid="stAlert"] {
    border-radius: 8px !important;
    font-size: 0.85rem !important;
}

/* ── Checkbox ── */
div[data-testid="stCheckbox"] label span {
    font-size: 0.85rem !important;
}

/* ── Plotly charts ── */
.js-plotly-plot .plotly .main-svg {
    border-radius: 8px;
}
</style>
""", unsafe_allow_html=True)

with tempfile.TemporaryDirectory() as tmp_dir:
    try:
        session.file.get('@"VALIDATOR_DB"."VALIDATOR_SCHEMA"."MYDATA"', tmp_dir)
        logo_path = f"{tmp_dir}/inferenz-logo.png"
    except Exception:
        logo_path = None

st.markdown("""
<div class="dq-header">
    <div>
        <div class="dq-header-title">Data Quality Suite</div>
        <div class="dq-header-subtitle">Automated data validation & monitoring for Snowflake</div>
    </div>
    <div class="dq-header-badge">POWERED BY SNOWFLAKE</div>
</div>
""", unsafe_allow_html=True)

if "current_page" not in st.session_state:
    st.session_state["current_page"] = "main"

_ensure_state()

if "dq_ready" not in st.session_state:
    st.session_state["dq_ready"] = False

if "df_sorted" not in st.session_state:
    st.session_state["df_sorted"] = None

if "valid_df" not in st.session_state:
    st.session_state["valid_df"] = None

if "invalid_df" not in st.session_state:
    st.session_state["invalid_df"] = None

if "execution_context" not in st.session_state:
    st.session_state.execution_context = None

if "table_sel" not in st.session_state:
    st.session_state.table_sel = None

if "tables_cached" not in st.session_state:
    st.session_state.tables_cached = None

if "active_tab" not in st.session_state:
    st.session_state.active_tab = "main"

NAV_ITEMS = [
    ("main", "Constraints", "🎯"),
    ("schedule", "Scheduler", "📅"),
    ("jobs", "Job Management", "⚙️"),
    ("accuracy", "Dashboard", "📊"),
]

st.markdown('<div class="nav-wrapper" style="background:#f0f2f6; border-radius:10px; padding:4px; margin-bottom:1.5rem;">', unsafe_allow_html=True)
nav_cols = st.columns(len(NAV_ITEMS))
for i, (tab_key, label, icon) in enumerate(NAV_ITEMS):
    with nav_cols[i]:
        is_active = st.session_state.active_tab == tab_key
        btn_label = f"{icon}  {label}"
        if is_active:
            st.markdown(
                f'<div style="text-align:center;background:#fff;border-radius:8px;padding:10px 16px;'
                f'font-weight:600;font-size:0.85rem;color:#1a1a2e;box-shadow:0 2px 8px rgba(0,0,0,0.08);">'
                f'{icon} {label}</div>',
                unsafe_allow_html=True
            )
        else:
            if st.button(btn_label, key=f"nav_{tab_key}", use_container_width=True):
                st.session_state.active_tab = tab_key
                st.experimental_rerun()
st.markdown('</div>', unsafe_allow_html=True)

if st.session_state.active_tab == "main":
    st.markdown('<div class="page-title">Data Quality Validator</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-subtitle">Run completeness & validation checks on your Snowflake tables</div>', unsafe_allow_html=True)
    show_main_page()

elif st.session_state.active_tab == "schedule":
    show_schedule_page()

elif st.session_state.active_tab == "jobs":
    showjobdashboard()
    st.markdown("---")
    show_history_page()

elif st.session_state.active_tab == "accuracy":
    get_accuracy_and_validation()

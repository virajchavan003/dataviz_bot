import io
import re
import zipfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="DataViz Bot", page_icon="📊", layout="wide")

st.title("📊 DataViz Bot")
st.caption("Upload • Clean • Visualize • Download")

SUPPORTED = {".csv", ".xlsx", ".xls"}

def safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "dataset"

def read_uploaded_file(uploaded):
    suffix = Path(uploaded.name).suffix.lower()
    raw = uploaded.getvalue()
    if suffix == ".csv":
        # Try common encodings; pandas detects the delimiter by default.
        for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin1"):
            try:
                return pd.read_csv(io.BytesIO(raw), encoding=encoding, sep=None, engine="python")
            except UnicodeDecodeError:
                continue
        return pd.read_csv(io.BytesIO(raw), sep=None, engine="python")
    if suffix in {".xlsx", ".xls"}:
        # For Excel workbooks, use the first worksheet.
        return pd.read_excel(io.BytesIO(raw), sheet_name=0)
    raise ValueError("Unsupported file type. Upload CSV or Excel.")

def clean_dataframe(df, trim_text=True, remove_duplicates=True, fill_missing=False):
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    # Remove completely empty rows and columns.
    out = out.dropna(axis=0, how="all").dropna(axis=1, how="all")
    # Trim leading/trailing whitespace in text columns.
    if trim_text:
        for col in out.select_dtypes(include=["object", "string"]).columns:
            out[col] = out[col].map(lambda v: v.strip() if isinstance(v, str) else v)
            out[col] = out[col].replace("", pd.NA)
    before = len(out)
    if remove_duplicates:
        out = out.drop_duplicates()
    duplicates_removed = before - len(out)
    filled_cells = 0
    if fill_missing:
        for col in out.columns:
            missing = int(out[col].isna().sum())
            if missing == 0:
                continue
            if pd.api.types.is_numeric_dtype(out[col]):
                median = out[col].median()
                if pd.notna(median):
                    out[col] = out[col].fillna(median)
                    filled_cells += missing
            else:
                mode = out[col].mode(dropna=True)
                if not mode.empty:
                    out[col] = out[col].fillna(mode.iloc[0])
                    filled_cells += missing
    return out, duplicates_removed, filled_cells

def aggregate_for_chart(df, x_col, y_col, aggregation):
    # A numeric Y column is required for sum/mean/min/max.
    if aggregation == "Count":
        return df.groupby(x_col, dropna=False).size().reset_index(name="Count"), "Count"
    if aggregation == "Rows":
        return df[[x_col, y_col]].copy(), y_col
    funcs = {
        "Sum": "sum",
        "Average": "mean",
        "Minimum": "min",
        "Maximum": "max",
    }
    result = df.groupby(x_col, dropna=False)[y_col].agg(funcs[aggregation]).reset_index()
    return result, y_col

# Sidebar settings
st.sidebar.header("⚙️ Settings")
st.sidebar.write("Upload your files and choose cleaning options.")

uploaded_files = st.sidebar.file_uploader(
    "Upload CSV / Excel files",
    type=["csv", "xlsx", "xls"],
    accept_multiple_files=True,
)

st.sidebar.subheader("Automatic cleaning")
trim_text = st.sidebar.checkbox("Trim extra spaces in text", value=True)
remove_duplicates = st.sidebar.checkbox("Remove duplicate rows", value=True)
fill_missing = st.sidebar.checkbox("Fill missing values (numeric median / text mode)", value=False)

if not uploaded_files:
    st.info("👈 Upload one or more CSV/Excel files from the sidebar to begin.")
    st.markdown("""
    **Example:** Upload a sales dataset with columns such as `Category`, `City`, `Sales`, `Profit`, and `Order Date`.

    Then choose:
    - **Sales by Category:** X = Category, Y = Sales, aggregation = Sum
    - **Profit by City:** X = City, Y = Profit, aggregation = Sum
    """)
    st.stop()

datasets = {}
errors = []
for uploaded in uploaded_files:
    try:
        original = read_uploaded_file(uploaded)
        cleaned, duplicates_removed, filled_cells = clean_dataframe(
            original, trim_text, remove_duplicates, fill_missing
        )
        key = f"{uploaded.name} ({len(cleaned):,} rows)"
        # Avoid overwriting if two files have the same name.
        unique_key = key
        counter = 2
        while unique_key in datasets:
            unique_key = f"{key} #{counter}"
            counter += 1
        datasets[unique_key] = {
            "original": original,
            "df": cleaned,
            "duplicates_removed": duplicates_removed,
            "filled_cells": filled_cells,
            "filename": uploaded.name,
        }
    except Exception as e:
        errors.append(f"{uploaded.name}: {e}")

for error in errors:
    st.error(error)

if not datasets:
    st.stop()

st.subheader("1. Your uploaded datasets")
dataset_names = list(datasets.keys())
selected_dataset = st.selectbox("Choose dataset to preview and chart", dataset_names)
item = datasets[selected_dataset]
df = item["df"]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Rows after cleaning", f"{len(df):,}")
c2.metric("Columns", f"{len(df.columns):,}")
c3.metric("Duplicate rows removed", f"{item['duplicates_removed']:,}")
c4.metric("Missing cells filled", f"{item['filled_cells']:,}")

with st.expander("Preview cleaned data", expanded=True):
    st.dataframe(df.head(100), use_container_width=True)
    st.caption("Preview shows up to 100 rows. Download the full cleaned dataset below.")

csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "⬇️ Download cleaned data (CSV)",
    data=csv_bytes,
    file_name=f"{safe_name(item['filename'])}_cleaned.csv",
    mime="text/csv",
)

with st.expander("See cleaning notes"):
    st.write(f"- Empty rows and empty columns were removed.")
    st.write(f"- Extra spaces in text values: {'trimmed' if trim_text else 'not changed'}.")
    st.write(f"- Duplicate rows: {'removed' if remove_duplicates else 'kept'}.")
    st.write(f"- Missing values: {'filled with numeric median / text mode where possible' if fill_missing else 'left unchanged'}.")
    st.warning("Automatic cleaning can change your data. Review the preview before using it in a report.")

st.divider()
st.subheader("2. Create charts")
if len(df.columns) < 1:
    st.warning("This dataset has no columns to chart.")
    st.stop()

column_names = [str(c) for c in df.columns]
numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
chart_types = ["Bar", "Line", "Scatter", "Pie", "Histogram", "Box"]
color_options = {
    "Blue": "#3B82F6",
    "Green": "#10B981",
    "Orange": "#F59E0B",
    "Purple": "#8B5CF6",
    "Red": "#EF4444",
    "Teal": "#14B8A6",
    "Pink": "#EC4899",
    "Slate": "#64748B",
}

if "chart_configs" not in st.session_state:
    st.session_state.chart_configs = [
        {"title": "Sales by Category", "type": "Bar", "x": column_names[0],
         "y": numeric_cols[0] if numeric_cols else column_names[0],
         "aggregation": "Sum", "color": "Blue"}
    ]

with st.form("chart_builder_form"):
    number_of_charts = st.number_input(
        "How many charts do you want on the dashboard?",
        min_value=1, max_value=8, value=min(3, len(st.session_state.chart_configs)), step=1
    )
    configs = []
    for i in range(int(number_of_charts)):
        default = st.session_state.chart_configs[i] if i < len(st.session_state.chart_configs) else {}
        st.markdown(f"#### Chart {i+1}")
        col1, col2, col3 = st.columns(3)
        title = col1.text_input("Chart title", value=default.get("title", f"Chart {i+1}"), key=f"title_{i}")
        chart_type = col2.selectbox(
            "Chart type", chart_types,
            index=chart_types.index(default.get("type", "Bar")) if default.get("type", "Bar") in chart_types else 0,
            key=f"type_{i}"
        )
        color = col3.selectbox(
            "Chart color", list(color_options.keys()),
            index=list(color_options.keys()).index(default.get("color", "Blue")) if default.get("color", "Blue") in color_options else 0,
            key=f"color_{i}"
        )
        col4, col5, col6 = st.columns(3)
        x_default = default.get("x", column_names[0])
        x_col = col4.selectbox("X-axis / category", column_names, index=column_names.index(x_default) if x_default in column_names else 0, key=f"x_{i}")
        y_choices = column_names
        y_default = default.get("y", numeric_cols[0] if numeric_cols else column_names[0])
        y_col = col5.selectbox("Y-axis / value", y_choices, index=y_choices.index(y_default) if y_default in y_choices else 0, key=f"y_{i}")
        aggregation_options = ["Sum", "Average", "Minimum", "Maximum", "Count", "Rows"]
        agg_default = default.get("aggregation", "Sum")
        aggregation = col6.selectbox("Calculation", aggregation_options, index=aggregation_options.index(agg_default) if agg_default in aggregation_options else 0, key=f"agg_{i}")
        configs.append({"title": title, "type": chart_type, "x": x_col, "y": y_col, "aggregation": aggregation, "color": color})
    submitted = st.form_submit_button("✨ Generate dashboard", type="primary", use_container_width=True)

if submitted:
    st.session_state.chart_configs = configs
    st.session_state.generated_charts = configs

generated = st.session_state.get("generated_charts")
if generated:
    st.subheader("3. Your dashboard")
    download_items = []
    chart_errors = []
    for i, config in enumerate(generated):
        x_col, y_col = config["x"], config["y"]
        chart_type = config["type"]
        color = color_options[config["color"]]
        try:
            chart_df, plotted_y = aggregate_for_chart(df, x_col, y_col, config["aggregation"])
            if config["aggregation"] not in ("Count", "Rows") and not pd.api.types.is_numeric_dtype(df[y_col]):
                st.warning(f"{config['title']}: '{y_col}' is not numeric. Choose a numeric Y column or use Count.")
                continue

            if chart_type == "Bar":
                fig = px.bar(chart_df, x=x_col, y=plotted_y, title=config["title"], color_discrete_sequence=[color])
            elif chart_type == "Line":
                fig = px.line(chart_df, x=x_col, y=plotted_y, title=config["title"], markers=True, color_discrete_sequence=[color])
            elif chart_type == "Scatter":
                if config["aggregation"] == "Count":
                    fig = px.scatter(chart_df, x=x_col, y=plotted_y, title=config["title"], color_discrete_sequence=[color])
                else:
                    fig = px.scatter(chart_df, x=x_col, y=plotted_y, title=config["title"], color_discrete_sequence=[color])
            elif chart_type == "Pie":
                pie_df = chart_df.copy()
                if config["aggregation"] == "Rows":
                    pie_df = df.groupby(x_col, dropna=False).size().reset_index(name="Count")
                    plotted_y = "Count"
                fig = px.pie(pie_df, names=x_col, values=plotted_y, title=config["title"], color_discrete_sequence=[color])
            elif chart_type == "Histogram":
                fig = px.histogram(df, x=y_col, title=config["title"], color_discrete_sequence=[color])
            else:  # Box
                if not pd.api.types.is_numeric_dtype(df[y_col]):
                    st.warning(f"{config['title']}: Box chart needs a numeric Y column.")
                    continue
                fig = px.box(df, x=x_col, y=y_col, title=config["title"], color_discrete_sequence=[color])
            fig.update_layout(template="plotly_white", height=420, margin=dict(l=20, r=20, t=60, b=30))
            st.plotly_chart(fig, use_container_width=True, key=f"render_chart_{i}")
            download_items.append((safe_name(config["title"] or f"chart_{i+1}"), fig))
        except Exception as e:
            chart_errors.append(f"{config['title']}: {e}")

    for error in chart_errors:
        st.error(error)

    if download_items:
        # Bundle interactive Plotly HTML charts into one ZIP download.
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, fig in download_items:
                zf.writestr(f"{name}.html", fig.to_html(include_plotlyjs="cdn", full_html=True))
        st.download_button(
            "⬇️ Download all charts (interactive HTML ZIP)",
            data=zip_buffer.getvalue(),
            file_name="dataviz_bot_charts.zip",
            mime="application/zip",
        )
        st.caption("The ZIP contains interactive HTML charts. Open them in a browser. The cleaned data can be downloaded separately above.")

st.divider()
st.caption("DataViz Bot • Local-first demo. Uploaded files are processed by this app session; avoid uploading confidential data to a public deployment.")

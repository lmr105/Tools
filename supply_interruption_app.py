import streamlit as st
import pandas as pd
import io
from datetime import datetime, timedelta
from xlsxwriter.utility import xl_col_to_name

# --------------------
# Helper Functions
# --------------------

def get_supply_interruptions(time_series, status_series):
    """
    Given a time_series (Pandas Series of datetime objects) and a boolean status_series 
    (True if in supply, False if out of supply), returns a list of outage events.
    Each event is a dict with:
      - 'lost_time': when supply was lost,
      - 'regained_time': when supply was restored,
      - 'duration': the outage duration as a timedelta.
    """
    interruptions = []
    in_interrupt = False
    start_time = None

    for i in range(len(status_series)):
        if not status_series.iloc[i] and not in_interrupt:
            in_interrupt = True
            start_time = time_series.iloc[i]
        elif status_series.iloc[i] and in_interrupt:
            end_time = time_series.iloc[i]
            duration = end_time - start_time
            interruptions.append({
                'lost_time': start_time,
                'regained_time': end_time,
                'duration': duration
            })
            in_interrupt = False

    if in_interrupt:
        end_time = time_series.iloc[-1]
        duration = end_time - start_time
        interruptions.append({
            'lost_time': start_time,
            'regained_time': end_time,
            'duration': duration
        })
    return interruptions

def format_timedelta(td):
    """Convert a timedelta to an HH:MM:SS string."""
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def highlight_row_with_index(row, raw_durations):
    """
    For styling the raw table: if the raw duration (from the hidden column)
    is 3 hours or more, highlight the row in yellow.
    """
    idx = row.name
    raw_dur = raw_durations.loc[idx]
    if pd.notnull(raw_dur) and isinstance(raw_dur, timedelta) and raw_dur.total_seconds() >= 3 * 3600:
        return ['background-color: yellow'] * len(row)
    else:
        return [''] * len(row)

def generate_excel_file(results_df):
    """
    Generate an Excel file (raw data) in memory with conditional formatting.
    Uses a hidden column (Raw Duration in seconds) to highlight rows with outages ≥ 3 hours.
    """
    df_excel = results_df.copy()
    df_excel['Raw Duration (seconds)'] = df_excel['Raw Duration'].apply(
        lambda x: x.total_seconds() if pd.notnull(x) else None
    )
    df_excel = df_excel.drop(columns=["Raw Duration"])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_excel.to_excel(writer, index=False, sheet_name='Results')
        workbook = writer.book
        worksheet = writer.sheets['Results']

        num_rows = df_excel.shape[0] + 1  # header + data rows
        num_cols = df_excel.shape[1]

        raw_col_index = df_excel.columns.get_loc("Raw Duration (seconds)")
        worksheet.set_column(raw_col_index, raw_col_index, None, None, {'hidden': True})

        highlight_format = workbook.add_format({'bg_color': '#FFFF00'})
        raw_col_letter = xl_col_to_name(raw_col_index)
        visible_range = f"A2:{xl_col_to_name(num_cols - 1)}{num_rows}"
        formula = f"=${raw_col_letter}2>=10800"  # 10800 seconds = 3 hours
        worksheet.conditional_format(visible_range, {
            'type': 'formula',
            'criteria': formula,
            'format': highlight_format
        })
    return output.getvalue()

def generate_processed_excel_file(processed_df):
    """
    Generate an Excel file (processed data) in memory.
    The processed data contains only the combined outage events that meet the rule.
    Columns: Property Height (m), Total Properties, Lost Supply, Regained Supply, Duration (formatted as HH:MM:SS), CML Impact.
    A total row is added summing the CML Impact as "Total Impact".
    """
    # Calculate CML Impact for each row.
    def calc_cml(row):
        # Outage duration in hours:
        hours = row['Duration'].total_seconds() / 3600
        return ((hours * row['Total Properties']) / 1473786) * 60

    processed_df['CML Impact'] = processed_df.apply(lambda row: calc_cml(row) if pd.notnull(row['Duration']) else 0, axis=1)
    # Format Duration as string.
    processed_df['Duration'] = processed_df['Duration'].apply(lambda x: format_timedelta(x) if pd.notnull(x) and isinstance(x, timedelta) else "")
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        processed_df.to_excel(writer, index=False, sheet_name='Processed Results')
        workbook = writer.book
        worksheet = writer.sheets['Processed Results']
        
        # Add a total row for CML Impact at the bottom.
        num_rows = processed_df.shape[0] + 1  # including header
        cml_col_index = processed_df.columns.get_loc("CML Impact")
        cml_col_letter = xl_col_to_name(cml_col_index)
        worksheet.write(num_rows, 0, "Total Impact")
        sum_range = f"{cml_col_letter}2:{cml_col_letter}{num_rows}"
        worksheet.write_formula(num_rows, cml_col_index, f"=SUM({sum_range})")
    return output.getvalue()

def process_outages(result_rows):
    """
    Process raw outage events (from result_rows) to combine events that are separated
    by a restoration period of less than one hour. For each property (by height),
    if the combined outage duration is 3 hours or more, include it in the output.
    Returns a list of dicts with keys: 
      Property Height (m), Total Properties, Lost Supply, Regained Supply, Duration (timedelta).
    """
    processed = []
    from collections import defaultdict
    groups = defaultdict(list)
    for row in result_rows:
        if row['Lost Supply'] != "In supply all times":
            groups[row['Property Height (m)']].append(row)
    for height, events in groups.items():
        events_sorted = sorted(events, key=lambda x: x['Lost Supply'])
        current_event = None
        for e in events_sorted:
            if current_event is None:
                current_event = {
                    "Lost Supply": e["Lost Supply"],
                    "Regained Supply": e["Regained Supply"],
                    "Cumulative Duration": e["Raw Duration"]
                }
            else:
                restoration_duration = e["Lost Supply"] - current_event["Regained Supply"]
                if restoration_duration < timedelta(hours=1):
                    current_event["Regained Supply"] = e["Regained Supply"]
                    current_event["Cumulative Duration"] += restoration_duration + e["Raw Duration"]
                else:
                    if current_event["Cumulative Duration"] >= timedelta(hours=3):
                        processed.append({
                            "Property Height (m)": height,
                            "Total Properties": events[0]["Total Properties"],
                            "Lost Supply": current_event["Lost Supply"],
                            "Regained Supply": current_event["Regained Supply"],
                            "Duration": current_event["Cumulative Duration"]
                        })
                    current_event = {
                        "Lost Supply": e["Lost Supply"],
                        "Regained Supply": e["Regained Supply"],
                        "Cumulative Duration": e["Raw Duration"]
                    }
        if current_event is not None and current_event["Cumulative Duration"] >= timedelta(hours=3):
            processed.append({
                "Property Height (m)": height,
                "Total Properties": events[0]["Total Properties"],
                "Lost Supply": current_event["Lost Supply"],
                "Regained Supply": current_event["Regained Supply"],
                "Duration": current_event["Cumulative Duration"]
            })
    processed_sorted = sorted(processed, key=lambda x: x["Property Height (m)"], reverse=True)
    return processed_sorted

def compute_quick_table(pressure_df, logger_height, additional_headloss, unique_heights, total_props):
    """
    Computes a quick supply status table.
    For each property height, determines if it is in supply at the last timestamp.
    If out of supply, calculates how long it has been out.
    Also computes CML Impact using the formula:
       ((Outage Duration in hours * Total Properties) / 1473786)*60.
    Returns a DataFrame with columns: 
      Property Height (m), Total Properties, Supply Status, Outage Duration, CML Impact.
    """
    modified_pressure = pressure_df['Pressure'] - additional_headloss
    effective_supply_head = logger_height + (modified_pressure - 3)
    last_time = pressure_df['Datetime'].iloc[-1]
    first_time = pressure_df['Datetime'].iloc[0]
    rows = []
    for h in unique_heights:
        total_prop = total_props.get(h, 1)
        if h <= logger_height:
            condition = modified_pressure > 0
        else:
            condition = effective_supply_head > h
        last_status = condition.iloc[-1]
        if last_status:
            supply_status = "In Supply"
            outage_duration = None
            outage_duration_str = ""
            cml_impact = 0
        else:
            true_indices = condition[condition].index
            if not true_indices.empty:
                last_in_supply = pressure_df['Datetime'].loc[true_indices[-1]]
                outage_duration = last_time - last_in_supply
            else:
                outage_duration = last_time - first_time
            supply_status = "Out of Supply"
            outage_duration_str = format_timedelta(outage_duration)
            outage_hours = outage_duration.total_seconds() / 3600
            cml_impact = ((outage_hours * total_prop) / 1473786) * 60
        rows.append({
            "Property Height (m)": h,
            "Total Properties": total_prop,
            "Supply Status": supply_status,
            "Outage Duration": outage_duration_str,
            "CML Impact": cml_impact
        })
    return pd.DataFrame(rows)

# --------------------
# Main UI & Processing (Review Mode)
# --------------------
st.set_page_config(
    page_title="Water Supply Interruption Calculator",
    page_icon="💧",
    layout="wide"
)

# Custom CSS for improved styling.
st.markdown(
    """
    <style>
    body { font-family: 'Arial', sans-serif; background-color: #f7f7f7; }
    .main-container { background-color: #ffffff; padding: 2rem; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin: 2rem auto; max-width: 1200px; }
    .stTextArea textarea { background-color: #e8f0fe; }
    </style>
    """,
    unsafe_allow_html=True
)

# Centered, resized logo.
st.markdown(
    "<div style='text-align: center;'><img src='https://via.placeholder.com/800x150.png?text=Water+Supply+Interruption+Calculator' width='400'></div>",
    unsafe_allow_html=True
)

with st.container():
    st.title("Review Mode - Supply Interruption Analysis")
    st.markdown("""
    **Instructions:**

    1. **Pressure Data:**
       - Open your CSV file in Excel.
       - Copy the entire column of timestamps and paste it into the **Pressure Timestamps** box.
       - Copy the entire column of pressure readings and paste it into the **Pressure Readings** box.
    2. **Property Heights:**
       - Copy the column of property heights and paste it into the **Property Heights** box.
    3. Enter the height of the pressure logger.
    4. Enter the simulated additional headloss (in meters) to deduct from the pressure readings.
    5. Click **Run Analysis** for full downloadable results or **Quick Table** for a quick on‑screen summary.
    """)

    col1, col2, col3 = st.columns(3)
    with col1:
        pressure_timestamps_text = st.text_area("Pressure Timestamps (one per line)", height=150)
    with col2:
        pressure_readings_text = st.text_area("Pressure Readings (one per line)", height=150)
    with col3:
        property_heights_text = st.text_area("Property Heights (one per line)", height=150)

    logger_height = st.number_input("Enter the height of the pressure logger (in meters):", min_value=0.0, value=100.0)
    additional_headloss = st.number_input("Simulate additional headloss (in meters):", min_value=0.0, value=0.0, step=0.1)

    # Full Analysis Button
    if st.button("Run Analysis"):
        if pressure_timestamps_text and pressure_readings_text and property_heights_text:
            timestamps_list = [line.strip() for line in pressure_timestamps_text.splitlines() if line.strip()]
            pressure_list = [line.strip() for line in pressure_readings_text.splitlines() if line.strip()]
            heights_list = [line.strip() for line in property_heights_text.splitlines() if line.strip()]

            try:
                pressure_df = pd.DataFrame({
                    'Datetime': [pd.to_datetime(ts, format="%d/%m/%Y %H:%M") for ts in timestamps_list],
                    'Pressure': [float(p) for p in pressure_list]
                })
            except Exception as e:
                st.error(f"Error parsing pressure data: {e}")
                st.stop()

            try:
                heights_df = pd.DataFrame({
                    'Property_Height': [float(h) for h in heights_list]
                })
            except Exception as e:
                st.error(f"Error parsing property heights: {e}")
                st.stop()

            pressure_df['Modified_Pressure'] = pressure_df['Pressure'] - additional_headloss
            pressure_df['Effective_Supply_Head'] = logger_height + (pressure_df['Modified_Pressure'] - 3)
            grouped = heights_df.groupby('Property_Height').size().reset_index(name='Total Properties')
            total_props = dict(zip(grouped['Property_Height'], grouped['Total Properties']))

            result_rows = []
            for _, group_row in grouped.iterrows():
                property_height = group_row['Property_Height']
                total_properties = group_row['Total Properties']
                if property_height <= logger_height:
                    supply_status = pressure_df['Modified_Pressure'] > 0
                else:
                    supply_status = pressure_df['Effective_Supply_Head'] > property_height
                interruptions = get_supply_interruptions(pressure_df['Datetime'], supply_status)
                if not interruptions:
                    result_rows.append({
                        'Property Height (m)': property_height,
                        'Total Properties': total_properties,
                        'Lost Supply': "In supply all times",
                        'Regained Supply': "",
                        'Duration': "",
                        'Restoration Duration': "",
                        'Raw Duration': None
                    })
                else:
                    for i, intr in enumerate(interruptions):
                        if intr['duration'] is None:
                            continue
                        # Store duration as timedelta for further processing.
                        duration_td = intr['duration']
                        formatted_duration = format_timedelta(duration_td)
                        if i > 0:
                            restoration_td = intr['lost_time'] - interruptions[i-1]['regained_time']
                            formatted_restoration = format_timedelta(restoration_td)
                        else:
                            formatted_restoration = ""
                        result_rows.append({
                            'Property Height (m)': property_height,
                            'Total Properties': total_properties,
                            'Lost Supply': intr['lost_time'],
                            'Regained Supply': intr['regained_time'],
                            'Duration': duration_td,
                            'Restoration Duration': formatted_restoration,
                            'Raw Duration': duration_td
                        })

            results_df = pd.DataFrame(result_rows)
            raw_durations = results_df['Raw Duration']
            results_df_display = results_df.drop(columns=["Raw Duration"])
            raw_excel = generate_excel_file(results_df)
            st.download_button(
                label="Download Raw Data as Excel (.xlsx)",
                data=raw_excel,
                file_name="raw_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            processed_events = process_outages(result_rows)
            if processed_events:
                processed_df = pd.DataFrame(processed_events)
                processed_df = processed_df.sort_values(by="Property Height (m)", ascending=False)
                processed_excel_data = generate_processed_excel_file(processed_df)
                st.download_button(
                    label="Download Processed Data as Excel (.xlsx)",
                    data=processed_excel_data,
                    file_name="processed_results.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.info("No processed outage events meet the criteria for being truly out of supply.")
        else:
            st.error("Please provide data in all text areas.")

    # Quick Table Functionality
    if st.button("Quick Table"):
        if pressure_timestamps_text and pressure_readings_text and property_heights_text:
            timestamps_list = [line.strip() for line in pressure_timestamps_text.splitlines() if line.strip()]
            pressure_list = [line.strip() for line in pressure_readings_text.splitlines() if line.strip()]
            heights_list = [line.strip() for line in property_heights_text.splitlines() if line.strip()]
            try:
                pressure_df = pd.DataFrame({
                    'Datetime': [pd.to_datetime(ts, format="%d/%m/%Y %H:%M") for ts in timestamps_list],
                    'Pressure': [float(p) for p in pressure_list]
                })
            except Exception as e:
                st.error(f"Error parsing pressure data: {e}")
                st.stop()
            try:
                heights_df = pd.DataFrame({
                    'Property_Height': [float(h) for h in heights_list]
                })
            except Exception as e:
                st.error(f"Error parsing property heights: {e}")
                st.stop()

            pressure_df['Modified_Pressure'] = pressure_df['Pressure'] - additional_headloss
            pressure_df['Effective_Supply_Head'] = logger_height + (pressure_df['Modified_Pressure'] - 3)
            grouped = heights_df.groupby('Property_Height').size().reset_index(name='Total Properties')
            total_props = dict(zip(grouped['Property_Height'], grouped['Total Properties']))
            unique_heights = sorted(heights_df['Property_Height'].unique())

            quick_df = compute_quick_table(pressure_df, logger_height, additional_headloss, unique_heights, total_props)
            st.markdown("### Quick Supply Status Table")
            st.dataframe(quick_df)
            total_impact = quick_df['CML Impact'].sum()
            st.markdown(f"**Total Impact: {total_impact:.2f}**")
        else:
            st.error("Please provide data in all text areas.")

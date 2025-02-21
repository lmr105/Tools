import streamlit as st
import pandas as pd
import io
from datetime import datetime, timedelta
from xlsxwriter.utility import xl_col_to_name

def get_supply_interruptions(time_series, status_series):
    """
    Given a time_series (Pandas Series of datetime objects) and a boolean status_series 
    (True if in supply, False if out of supply), this function returns a list of dictionaries,
    each containing:
      - 'lost_time': when the property lost supply,
      - 'regained_time': when the property regained supply, and
      - 'duration': the interruption duration.
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
    """
    Convert a timedelta object to a string in HH:MM:SS format.
    """
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def highlight_row_with_index(row, raw_durations):
    """
    Look up the raw duration for the current row (by index) from the provided Series.
    If the duration is 3 hours or more, return a list of CSS styles to highlight the row yellow.
    """
    idx = row.name
    raw_dur = raw_durations.loc[idx]
    if pd.notnull(raw_dur) and isinstance(raw_dur, timedelta) and raw_dur.total_seconds() >= 3 * 3600:
        return ['background-color: yellow'] * len(row)
    else:
        return [''] * len(row)

def generate_excel_file(results_df):
    """
    Generate an Excel file in memory with conditional formatting.
    The Excel file will include a hidden column with the raw duration in seconds,
    and rows will be highlighted if that value is 10800 seconds (3 hours) or more.
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
        formula = f"=${raw_col_letter}2>=10800"
        worksheet.conditional_format(visible_range, {
            'type': 'formula',
            'criteria': formula,
            'format': highlight_format
        })
    return output.getvalue()

def main():
    st.title("Water Supply Interruption Calculator")

    # --- Password Protection ---
    password = st.text_input("Enter password to access the app", type="password")
    if not password:
        st.info("Please enter the password to continue.")
        st.stop()
    elif password != "mysecretpassword":  # Replace with your chosen password
        st.error("Incorrect password")
        st.stop()
    # --- End Password Protection ---

    st.markdown("""
    **Instructions:**
    - Upload the **Pressure Data CSV** (with two columns: date/time and pressure in meters head).
    - Upload the **Property Heights CSV** (with a single column of property heights in meters).
    - Enter the height of the pressure logger (in meters).
    """)

    pressure_file = st.file_uploader("Upload Pressure Data CSV", type=["csv"])
    heights_file = st.file_uploader("Upload Property Heights CSV", type=["csv"])
    logger_height = st.number_input("Enter the height of the pressure logger (in meters):", min_value=0.0, value=100.0)

    if pressure_file is not None and heights_file is not None:
        try:
            pressure_df = pd.read_csv(pressure_file)
            date_col = pressure_df.columns[0]
            pressure_col = pressure_df.columns[1]
            pressure_df[date_col] = pd.to_datetime(pressure_df[date_col])
            pressure_df.sort_values(date_col, inplace=True)
        except Exception as e:
            st.error(f"Error processing pressure data: {e}")
            return

        try:
            heights_df = pd.read_csv(heights_file, header=None, dtype={0: float})
            heights_df.columns = ['Property_Height']
        except Exception as e:
            st.error(f"Error processing property heights: {e}")
            return

        pressure_df['Effective_Supply_Head'] = logger_height + (pressure_df[pressure_col] - 3)
        grouped = heights_df.groupby('Property_Height').size().reset_index(name='Count')

        result_rows = []
        for _, group_row in grouped.iterrows():
            property_height = group_row['Property_Height']
            count = group_row['Count']
            supply_status = pressure_df['Effective_Supply_Head'] > property_height
            interruptions = get_supply_interruptions(pressure_df[date_col], supply_status)

            if not interruptions:
                result_rows.append({
                    'Property Height (m)': property_height,
                    'Count': count,
                    'Lost Supply': "In supply all times",
                    'Regained Supply': "",
                    'Duration': "",
                    'Restoration Duration': "",
                    'Raw Duration': None  # For internal use
                })
            else:
                # For each interruption, compute outage duration.
                # Also, if there are successive outages, compute restoration duration.
                for i, intr in enumerate(interruptions):
                    formatted_duration = format_timedelta(intr['duration'])
                    if i > 0:
                        restoration_td = intr['lost_time'] - interruptions[i-1]['regained_time']
                        formatted_restoration = format_timedelta(restoration_td)
                    else:
                        formatted_restoration = ""
                    result_rows.append({
                        'Property Height (m)': property_height,
                        'Count': count,
                        'Lost Supply': intr['lost_time'],
                        'Regained Supply': intr['regained_time'],
                        'Duration': formatted_duration,
                        'Restoration Duration': formatted_restoration,
                        'Raw Duration': intr['duration']  # For internal use (for highlighting)
                    })

        results_df = pd.DataFrame(result_rows)
        raw_durations = results_df['Raw Duration']
        results_df_display = results_df.drop(columns=["Raw Duration"])
        styled_df = results_df_display.style.apply(lambda row: highlight_row_with_index(row, raw_durations), axis=1)
        st.markdown("### Supply Interruption Results Table:")
        html_table = styled_df.to_html()
        st.markdown(html_table, unsafe_allow_html=True)

        # --- Download Buttons ---
        st.download_button(
            label="Download Styled Table as HTML",
            data=html_table,
            file_name="styled_table.html",
            mime="text/html"
        )

        csv_data = results_df_display.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download Table as CSV",
            data=csv_data,
            file_name="results.csv",
            mime="text/csv"
        )

        excel_data = generate_excel_file(results_df)
        st.download_button(
            label="Download Table as Excel (.xlsx)",
            data=excel_data,
            file_name="results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

if __name__ == "__main__":
    main()

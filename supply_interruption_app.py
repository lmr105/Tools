import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

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
            # Transition: In supply -> Out of supply
            in_interrupt = True
            start_time = time_series.iloc[i]
        elif status_series.iloc[i] and in_interrupt:
            # Transition: Out of supply -> In supply
            end_time = time_series.iloc[i]
            duration = end_time - start_time
            interruptions.append({
                'lost_time': start_time, 
                'regained_time': end_time, 
                'duration': duration
            })
            in_interrupt = False

    # If still out of supply at the end of the series, record until the final time.
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

def main():
    st.title("Water Supply Interruption Calculator")

    # --- Password Protection ---
    password = st.text_input("Enter password to access the app", type="password")
    if not password:
        st.info("Please enter the password to continue.")
        st.stop()
    elif password != "doesdimdwrdafi":  # Replace with your chosen password
        st.error("Incorrect password")
        st.stop()
    # --- End Password Protection ---

    st.markdown("""
    **In

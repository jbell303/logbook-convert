import pandas as pd
from datetime import datetime, timedelta, timezone
from astral.sun import sun
from astral import LocationInfo
import pytz
import airportsdata
import argparse
import os
from functools import lru_cache

# Initialize the airports database
airports = airportsdata.load('IATA')

# Fallback dictionary for airports not found in the database
# Define airport metadata: (Name, Timezone, Latitude, Longitude)
fallback_airport_timezones = {
    "CAN": ("Guangzhou", "Asia/Shanghai", 23.3924, 113.2988),
    "BKK": ("Bangkok", "Asia/Bangkok", 13.6900, 100.7501),
    "PEN": ("Penang", "Asia/Kuala_Lumpur", 5.2976, 100.2760),
    "TPE": ("Taipei", "Asia/Taipei", 25.0777, 121.2330),
    "KIX": ("Osaka", "Asia/Tokyo", 34.4347, 135.2440),
}

# logbook.aero column mapping - maps original columns to logbook.aero standard columns
LOGBOOK_AERO_COLUMN_MAPPING = {
    'DEPT_DATE': 'Date',
    'ORG': 'Departure_Airfield',
    'DEST': 'Arrival_Airfield',
    'EQUIP': 'Aircraft_Type',
    'TAIL': 'Aircraft_Registration',
    'OUT': 'Departure_Time',
    'IN': 'Arrival_Time',
    'BLK_HRS': 'Total_Time',
    'Night Time': 'Night_Time',
    'Day Landings': 'Landing_Day',
    'Night Landings': 'Landing_Night',
    'Day Takeoffs': 'Takeoff_Day',
    'Night Takeoffs': 'Takeoff_Night',
    'XC': 'XC_Time',
    'Act Inst': 'IFR_Time',
    'Approaches': 'Instrument_Approach'
}

# Crew position time distribution
CREW_POSITION_DISTRIBUTION = {
    'captain': {
        'PIC': 1.0,    # 100% as PIC
        'SIC': 0.0,
        'Duration': 1.0
    },
    'first_officer': {
        'PIC': 0.0,
        'SIC': 1.0,    # 100% as SIC
        'Duration': 1.0
    },
    'relief_first_officer': {
        'PIC': 0.0,
        'SIC': 0.5,    # 50% as SIC
        'Duration': 0.5  # 50% of flight time
    },
    'relief_captain': {
        'PIC': 0.5,    # 50% as PIC
        'SIC': 0.0,
        'Duration': 0.5  # 50% of flight time
    }
}

def get_airport_data(code):
    """Get airport data for a given IATA code."""
    try:
        airport = airports.get(code)
        if airport and 'tz' in airport and 'lat' in airport and 'lon' in airport:
            return (
                airport.get('name', code),
                airport['tz'],
                float(airport['lat']),
                float(airport['lon'])
            )
    except (KeyError, ValueError):
        pass

    # Fall back to hard-coded values if airport not found
    if code in fallback_airport_timezones:
        return fallback_airport_timezones[code]

    # If we can't find the airport, return None
    return None

def parse_time(date_str, time_str):
    """
    Parse date and time strings into a datetime object.
    Returns a datetime with UTC timezone.
    Handles malformed time values gracefully.
    """
    try:
        # Check for malformed time strings
        if not time_str or time_str == '.' or not isinstance(time_str, str):
            time_str = "12:00"
            print(f"Warning: Malformed time value for date {date_str}, using {time_str} instead.")

        # Parse the date first using flexible parsing
        date_dt = parse_date_flexible(date_str)

        # Parse the time
        time_parts = str(time_str).split(':')
        hour = int(time_parts[0])
        minute = int(time_parts[1]) if len(time_parts) > 1 else 0

        dt = date_dt.replace(hour=hour, minute=minute)
        return dt.replace(tzinfo=pytz.utc)
    except (ValueError, IndexError) as e:
        print(f"Warning: Could not parse time '{time_str}' for date {date_str}: {e}")
        try:
            date_dt = parse_date_flexible(date_str)
            dt = date_dt.replace(hour=12, minute=0)
            return dt.replace(tzinfo=pytz.utc)
        except ValueError:
            print(f"Error: Could not parse date '{date_str}'. Using current datetime instead.")
            return datetime.now(timezone.utc)

@lru_cache(maxsize=128)
def get_timezone_diff(tz1, tz2):
    """
    Calculate the time difference in hours between two timezones.
    This function is cached to improve performance for repeated calls.
    """
    # Create a naive datetime for the pytz calculations
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Get timezone objects
    tz1_obj = pytz.timezone(tz1)
    tz2_obj = pytz.timezone(tz2)

    # Calculate offsets
    offset1 = tz1_obj.utcoffset(now).total_seconds() / 3600
    offset2 = tz2_obj.utcoffset(now).total_seconds() / 3600

    return abs(offset1 - offset2)

@lru_cache(maxsize=256)
def get_sunrise_sunset(airport_code, date):
    """
    Get sunrise and sunset times for an airport on a specific date.
    Returns UTC-timezone aware datetimes.
    """
    airport_data = get_airport_data(airport_code)
    if not airport_data:
        return None, None

    name, tzname, lat, lon = airport_data
    location = LocationInfo(name=name, region="", timezone=tzname, latitude=lat, longitude=lon)
    s = sun(location.observer, date=date.date(), tzinfo=pytz.timezone(tzname))
    return s['sunrise'].astimezone(pytz.utc), s['sunset'].astimezone(pytz.utc)

def estimate_night_time(row):
    """
    Estimate the amount of night flying time for a flight.
    """
    origin = row['ORG']
    destination = row['DEST']
    date = parse_date_flexible(row['DEPT_DATE'])
    off_time = parse_time(row['DEPT_DATE'], row['OFF'])
    on_time = parse_time(row['DEPT_DATE'], row['ON'])
    flt_time = safe_float_conversion(row['FLT_HRS'])

    origin_data = get_airport_data(origin)
    dest_data = get_airport_data(destination)

    if not origin_data or not dest_data:
        return 0.0

    tz_diff = get_timezone_diff(origin_data[1], dest_data[1])

    if tz_diff <= 4:
        # Simple rule, based on destination
        sunrise, sunset = get_sunrise_sunset(destination, date)
        if not sunrise or not sunset:
            return 0.0

        if off_time >= sunset or on_time <= sunrise:
            return round(flt_time, 1)
        elif (off_time < sunset < on_time) or (off_time < sunrise < on_time):
            return round(flt_time * 0.5, 1)
        else:
            return 0.0
    else:
        # Advanced method using time intervals
        time_increment = timedelta(minutes=10)
        total_night_minutes = 0
        current_time = off_time
        while current_time < on_time:
            progress_ratio = (current_time - off_time).total_seconds() / (on_time - off_time).total_seconds()
            lat = origin_data[2] + progress_ratio * (dest_data[2] - origin_data[2])
            lon = origin_data[3] + progress_ratio * (dest_data[3] - origin_data[3])
            tz = dest_data[1]
            try:
                location = LocationInfo(latitude=lat, longitude=lon)
                s = sun(location.observer, date=current_time.date(), tzinfo=pytz.timezone(tz))
                if current_time < s['sunrise'].astimezone(pytz.utc) or current_time > s['sunset'].astimezone(pytz.utc):
                    total_night_minutes += 10
            except Exception as e:
                print(f"Warning: Error calculating sun position at lat={lat}, lon={lon}: {e}")
                pass
            current_time += time_increment
        estimated_night_hours = round(total_night_minutes / 60.0, 1)
        return min(estimated_night_hours, flt_time)

def is_night_time(time_dt, airport_code):
    """
    Determine if a given time occurs during night at an airport.
    """
    airport_data = get_airport_data(airport_code)
    if not airport_data:
        return False

    name, tzname, lat, lon = airport_data
    location = LocationInfo(name=name, region="", timezone=tzname, latitude=lat, longitude=lon)

    s = sun(location.observer, date=time_dt.date(), tzinfo=pytz.timezone(tzname))
    sunrise = s['sunrise'].astimezone(pytz.utc)
    sunset = s['sunset'].astimezone(pytz.utc)

    # Civil twilight is approximately 30 minutes after sunset
    civil_twilight = sunset + timedelta(minutes=30)

    return time_dt >= civil_twilight or time_dt <= sunrise

def process_landings(row):
    """
    Process a flight row to determine if the landing was during day or night.
    Only counts a landing if the LANDING column indicates the crew member performed the landing.
    """
    performed_landing = False
    try:
        landing_val = row.get('LANDING', 0)
        if landing_val == 1 or landing_val == '1':
            performed_landing = True
    except:
        performed_landing = False

    if not performed_landing:
        return 0, 0

    try:
        destination = row['DEST']
        landing_time = parse_time(row['DEPT_DATE'], row['ON'])

        if is_night_time(landing_time, destination):
            return 0, 1  # 0 day landings, 1 night landing
        else:
            return 1, 0  # 1 day landing, 0 night landings
    except Exception as e:
        print(f"Warning: Error determining landing type: {e}. Defaulting to day landing.")
        return 1, 0

def process_takeoffs(row):
    """
    Process a flight row to determine if the takeoff was during day or night.
    Only counts a takeoff if the LANDING column indicates the crew member performed the landing,
    since if you did the landing, you also did the takeoff for that flight.
    """
    # Check if this crew member performed the landing (and therefore the takeoff)
    performed_landing = False
    try:
        landing_val = row.get('LANDING', 0)
        if landing_val == 1 or landing_val == '1':
            performed_landing = True
    except:
        performed_landing = False

    if not performed_landing:
        return 0, 0

    try:
        origin = row['ORG']
        takeoff_time = parse_time(row['DEPT_DATE'], row['OFF'])

        if is_night_time(takeoff_time, origin):
            return 0, 1  # 0 day takeoffs, 1 night takeoff
        else:
            return 1, 0  # 1 day takeoff, 0 night takeoffs
    except Exception as e:
        print(f"Warning: Error determining takeoff type: {e}. Defaulting to day takeoff.")
        return 1, 0

def count_approaches(row):
    """
    Count the number of instrument approaches for a flight.
    Returns an integer count.
    """
    if 'LANDING' in row and (row['LANDING'] == 1 or row['LANDING'] == '1'):
        return 1
    else:
        return 0

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Process flight data and format it according to logbook.aero standards.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        '--flights',
        type=str,
        default="DWNLD_3983442.csv",
        help='Input CSV file containing flight data'
    )

    default_output = f"Logbook_Aero_{datetime.now().strftime('%Y-%m-%d')}.csv"

    parser.add_argument(
        '--output',
        type=str,
        default=default_output,
        help='Output CSV file path'
    )

    parser.add_argument(
        '--position',
        type=str,
        choices=['captain', 'first_officer', 'relief_first_officer', 'relief_captain', 'auto'],
        default='captain',
        help='Crew position used for logging flight time'
    )

    parser.add_argument(
        '--oe-data',
        type=str,
        help='Optional CSV file with Operating Experience data'
    )

    parser.add_argument(
        '--pilot-name',
        type=str,
        default='SELF',
        help='Pilot name to use for PIC_Name field when acting as PIC'
    )

    args = parser.parse_args()

    if args.output == default_output and args.flights != "DWNLD_3983442.csv":
        input_base = os.path.splitext(os.path.basename(args.flights))[0]
        args.output = f"Logbook_Aero_{input_base}_{datetime.now().strftime('%Y-%m-%d')}.csv"
        print(f"Auto-generating output filename: {args.output}")

    return args

def load_oe_data(oe_file):
    """
    Load Operating Experience data from CSV file.
    Returns a dictionary mapping flight IDs to crew roles and times.
    """
    if not os.path.exists(oe_file):
        print(f"Warning: OE data file {oe_file} not found.")
        return {}

    try:
        oe_df = pd.read_csv(oe_file)

        if 'FLIGHT' not in oe_df.columns:
            print("Warning: OE data file must have FLIGHT column.")
            return {}

        oe_data = {}
        flight_count = 0

        for _, row in oe_df.iterrows():
            # Create unique key using flight number + origin + destination + date
            flight_num = str(row['FLIGHT']).strip().zfill(4)
            origin = str(row.get('ORG', '')).strip().upper()
            dest = str(row.get('DEST', '')).strip().upper()
            # Parse and normalize date from OE file (format: DDMMMYYYY like "02DEC2025")
            oe_date = str(row.get('FLT_DT', '')).strip().upper()
            try:
                oe_date_parsed = datetime.strptime(oe_date, "%d%b%Y")
                oe_date_normalized = oe_date_parsed.strftime("%Y-%m-%d")
            except ValueError:
                oe_date_normalized = oe_date
            flight_id = f"{flight_num}_{origin}_{dest}_{oe_date_normalized}"

            flight_data = {
                'role': 'captain',
                'pic_time': None,
                'sic_time': None
            }

            if 'SEAT' in oe_df.columns:
                seat_value = str(row.get('SEAT', '')).strip().upper()

                if seat_value in ['CAPT', 'CPT', 'CAPTAIN']:
                    flight_data['role'] = 'captain'
                    if 'PIC_OE' in oe_df.columns:
                        pic_time = safe_float_conversion(row.get('PIC_OE', 0))
                        flight_data['pic_time'] = pic_time if pic_time > 0 else None
                        flight_data['sic_time'] = 0.0

                elif seat_value in ['FO', 'F/O', 'FIRST OFFICER']:
                    flight_data['role'] = 'first_officer'
                    if 'SIC_OE' in oe_df.columns:
                        sic_time = safe_float_conversion(row.get('SIC_OE', 0))
                        flight_data['sic_time'] = sic_time if sic_time > 0 else None
                        flight_data['pic_time'] = 0.0

                elif seat_value in ['RFO', 'RF/O', 'R/FO', 'RELIEF FIRST OFFICER']:
                    flight_data['role'] = 'relief_first_officer'
                    flight_data['pic_time'] = 0.0
                    if 'SIC_RFO_OE' in oe_df.columns:
                        sic_time = safe_float_conversion(row.get('SIC_RFO_OE', 0))
                        flight_data['sic_time'] = sic_time if sic_time > 0 else None

                elif seat_value in ['RF2', 'RC', 'RELIEF CAPTAIN']:
                    flight_data['role'] = 'relief_captain'
                    flight_data['sic_time'] = 0.0
                    if 'PIC_RFO_OE' in oe_df.columns:
                        pic_time = safe_float_conversion(row.get('PIC_RFO_OE', 0))
                        flight_data['pic_time'] = pic_time if pic_time > 0 else None

            elif 'ROLE' in oe_df.columns:
                role_value = str(row.get('ROLE', '')).strip().upper()

                if role_value == 'PIC':
                    flight_data['role'] = 'captain'
                    flight_data['pic_time'] = safe_float_conversion(row.get('PIC_OE', 0))
                    flight_data['sic_time'] = 0.0

                elif role_value == 'SIC':
                    flight_data['role'] = 'first_officer'
                    flight_data['sic_time'] = safe_float_conversion(row.get('SIC_OE', 0))
                    flight_data['pic_time'] = 0.0

            if flight_data['pic_time'] is None and flight_data['sic_time'] is None:
                if 'PIC_OE' in oe_df.columns and flight_data['role'] == 'captain':
                    pic_time = safe_float_conversion(row.get('PIC_OE', 0))
                    if pic_time > 0:
                        flight_data['pic_time'] = pic_time
                        flight_data['sic_time'] = 0.0

                if 'SIC_OE' in oe_df.columns and flight_data['role'] == 'first_officer':
                    sic_time = safe_float_conversion(row.get('SIC_OE', 0))
                    if sic_time > 0:
                        flight_data['sic_time'] = sic_time
                        flight_data['pic_time'] = 0.0

            oe_data[flight_id] = flight_data
            flight_count += 1

            if flight_count <= 3:
                print(f"Example flight {flight_id}: Seat: {row.get('SEAT', 'unknown')}, Role: {flight_data['role']}")

        return oe_data
    except Exception as e:
        print(f"Error loading OE data: {e}")
        return {}

def determine_crew_position(row, default_position, oe_data):
    """
    Determine crew position based on OE data if available.
    """
    if not oe_data:
        return default_position

    # Create unique key using flight number + origin + destination + date
    flight_num = str(row.get('FLIGHT', '')).strip().zfill(4)
    origin = str(row.get('ORG', '')).strip().upper()
    dest = str(row.get('DEST', '')).strip().upper()
    # Normalize date from flight data (already converted to YYYY-MM-DD by format_date_logbook_aero)
    flight_date = str(row.get('DEPT_DATE', '')).strip()
    flight_id = f"{flight_num}_{origin}_{dest}_{flight_date}"

    if flight_id in oe_data:
        return oe_data[flight_id]['role']

    return default_position

def assign_crew_time(row, position, oe_data=None):
    """
    Assign flight time based on crew position.
    Returns dict with logbook.aero time categories.
    All times are capped to not exceed total time (block time).
    """
    total_time = safe_float_conversion(row['BLK_HRS'])

    distribution = CREW_POSITION_DISTRIBUTION[position]

    pic_time = total_time * distribution['PIC']
    sic_time = total_time * distribution['SIC']

    # Create unique key using flight number + origin + destination + date
    flight_num = str(row.get('FLIGHT', '')).strip().zfill(4)
    origin = str(row.get('ORG', '')).strip().upper()
    dest = str(row.get('DEST', '')).strip().upper()
    # Normalize date from flight data (already converted to YYYY-MM-DD by format_date_logbook_aero)
    flight_date = str(row.get('DEPT_DATE', '')).strip()
    flight_id = f"{flight_num}_{origin}_{dest}_{flight_date}"

    if oe_data and flight_id in oe_data:
        flight_data = oe_data[flight_id]
        if flight_data['pic_time'] is not None:
            pic_time = min(flight_data['pic_time'], total_time)
        if flight_data['sic_time'] is not None:
            sic_time = min(flight_data['sic_time'], total_time)

    # Cap all times to total_time
    pic_time = min(pic_time, total_time)
    sic_time = min(sic_time, total_time)

    return {
        'PIC_Time': pic_time,
        'CoPilot_Time': sic_time,
        'MultiPilot_Time': total_time,  # Multi-pilot time equals total time for multi-crew aircraft
        'XC_Time': total_time
    }

def safe_float_conversion(value):
    """
    Safely convert a value to float, returning 0.0 if conversion fails.
    """
    try:
        if value is None or value == '' or value == '.':
            return 0.0
        return float(value)
    except (ValueError, TypeError):
        return 0.0

def format_tail_number(tail_number):
    """
    Convert a numeric tail number to a full aircraft ID in the format of NXXXFE.
    """
    try:
        if str(tail_number).isdigit():
            return f"N{tail_number}FE"
        return str(tail_number)
    except:
        return str(tail_number)

def parse_date_flexible(date_str):
    """
    Parse a date string in various formats and return a datetime object.
    Supports: YYYY-MM-DD, MM/DD/YYYY, DD/MM/YYYY
    """
    formats = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unable to parse date: {date_str}")

def format_date_logbook_aero(date_str):
    """
    Convert date to YYYY-MM-DD format for logbook.aero.
    Accepts multiple input formats.
    """
    try:
        dt = parse_date_flexible(date_str)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return date_str

def format_time_hhmm(time_str):
    """
    Ensure time is in HH:mm format.
    """
    if not time_str or time_str == '.':
        return ''
    try:
        # Already in HH:mm format
        if ':' in str(time_str):
            parts = str(time_str).split(':')
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
        return str(time_str)
    except:
        return str(time_str)

def calculate_actual_instrument(row):
    """
    Calculate actual instrument time as 50% of night time.
    """
    night_time = safe_float_conversion(row['Night Time'])
    return night_time * 0.5

def get_pic_name(row, position, pilot_name):
    """
    Determine the PIC name based on crew position.
    If the pilot is acting as PIC, return SELF or their name.
    If acting as SIC, leave blank (the actual PIC would be entered separately).
    """
    if position in ['captain', 'relief_captain']:
        return pilot_name
    else:
        return ''

def get_sic_name(row, position, pilot_name):
    """
    Determine the SIC name based on crew position.
    If the pilot is acting as SIC, return SELF or their name.
    """
    if position in ['first_officer', 'relief_first_officer']:
        return pilot_name
    else:
        return ''

def main_web(args):
    """
    Web version of the main function that accepts pre-parsed args.
    """
    flights_csv = args.flights
    output_csv = args.output
    default_crew_position = args.position
    oe_file = args.oe_data
    pilot_name = getattr(args, 'pilot_name', 'SELF')

    oe_data = {}
    if oe_file:
        try:
            oe_data = load_oe_data(oe_file)
            if default_crew_position == 'auto' and not oe_data:
                default_crew_position = 'captain'
        except Exception as e:
            if default_crew_position == 'auto':
                default_crew_position = 'captain'

    if not os.path.exists(flights_csv):
        raise FileNotFoundError(f"Flight data file '{flights_csv}' not found.")

    df = pd.read_csv(flights_csv)
    rows_processed = len(df)

    # Format tail numbers
    df['TAIL'] = df['TAIL'].apply(format_tail_number)

    # Convert date format
    df['DEPT_DATE'] = df['DEPT_DATE'].apply(format_date_logbook_aero)

    # Format times
    df['OUT'] = df['OUT'].apply(format_time_hhmm)
    df['IN'] = df['IN'].apply(format_time_hhmm)

    # Calculate night time
    df['Night Time'] = df.apply(estimate_night_time, axis=1)

    # Calculate actual instrument time (50% of night time)
    df['Act Inst'] = df.apply(calculate_actual_instrument, axis=1)

    # Calculate day and night landings
    day_night_landings = df.apply(process_landings, axis=1, result_type='expand')
    df['Day Landings'] = day_night_landings[0]
    df['Night Landings'] = day_night_landings[1]

    # Calculate day and night takeoffs
    day_night_takeoffs = df.apply(process_takeoffs, axis=1, result_type='expand')
    df['Day Takeoffs'] = day_night_takeoffs[0]
    df['Night Takeoffs'] = day_night_takeoffs[1]

    # Count approaches
    df['Approaches'] = df.apply(count_approaches, axis=1)

    # Determine crew position for each flight if using auto mode
    if default_crew_position == 'auto':
        df['CrewPosition'] = df.apply(lambda row: determine_crew_position(row, 'captain', oe_data), axis=1)
        crew_times = df.apply(
            lambda row: assign_crew_time(row, row['CrewPosition'], oe_data),
            axis=1, result_type='expand'
        )
        df['PIC_Name'] = df.apply(lambda row: get_pic_name(row, row['CrewPosition'], pilot_name), axis=1)
        df['SIC_Name'] = df.apply(lambda row: get_sic_name(row, row['CrewPosition'], pilot_name), axis=1)
    else:
        crew_times = df.apply(lambda row: assign_crew_time(row, default_crew_position, oe_data), axis=1, result_type='expand')
        df['PIC_Name'] = df.apply(lambda row: get_pic_name(row, default_crew_position, pilot_name), axis=1)
        df['SIC_Name'] = df.apply(lambda row: get_sic_name(row, default_crew_position, pilot_name), axis=1)

    # Add crew position time columns
    df['PIC_Time'] = crew_times['PIC_Time']
    df['CoPilot_Time'] = crew_times['CoPilot_Time']
    df['MultiPilot_Time'] = crew_times['MultiPilot_Time']
    df['XC'] = crew_times['XC_Time']

    # Cap Night Time and IFR Time to not exceed Total Time (block hours)
    df['Night Time'] = df.apply(lambda row: min(safe_float_conversion(row['Night Time']), safe_float_conversion(row['BLK_HRS'])), axis=1)
    df['Act Inst'] = df.apply(lambda row: min(safe_float_conversion(row['Act Inst']), safe_float_conversion(row['BLK_HRS'])), axis=1)

    # Rename columns to logbook.aero format
    df = df.rename(columns=LOGBOOK_AERO_COLUMN_MAPPING)

    # Build route column (Departure-Arrival)
    df['Route'] = df['Departure_Airfield'] + '-' + df['Arrival_Airfield']

    # Reorganize columns in logbook.aero order
    column_order = [
        'Date',
        'Departure_Airfield',
        'Arrival_Airfield',
        'Route',
        'Departure_Time',
        'Arrival_Time',
        'Aircraft_Type',
        'Aircraft_Registration',
        'Total_Time',
        'MultiPilot_Time',
        'PIC_Name',
        'SIC_Name',
        'Takeoff_Day',
        'Takeoff_Night',
        'Landing_Day',
        'Landing_Night',
        'Night_Time',
        'IFR_Time',
        'PIC_Time',
        'CoPilot_Time',
        'XC_Time',
        'Instrument_Approach',
    ]

    # Only include columns that exist in the dataframe
    final_columns = [col for col in column_order if col in df.columns]

    df = df[final_columns]

    # Format float columns with 2 decimal places
    float_columns = ['Total_Time', 'MultiPilot_Time', 'Night_Time', 'IFR_Time',
                     'PIC_Time', 'CoPilot_Time', 'XC_Time']
    for col in float_columns:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: round(safe_float_conversion(x), 1))

    # Ensure integer columns are integers
    int_columns = ['Takeoff_Day', 'Takeoff_Night', 'Landing_Day', 'Landing_Night', 'Instrument_Approach']
    for col in int_columns:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: int(safe_float_conversion(x)))

    df.to_csv(output_csv, index=False)
    return rows_processed

def main():
    """
    Main function for processing flight logbook data.
    Formats data according to logbook.aero standards.
    """
    args = parse_args()
    flights_csv = args.flights
    output_csv = args.output
    default_crew_position = args.position
    oe_file = args.oe_data
    pilot_name = args.pilot_name

    # Load OE data if provided
    oe_data = {}
    if oe_file:
        print(f"Loading Operating Experience data from {oe_file}...")
        try:
            oe_data = load_oe_data(oe_file)
            if oe_data:
                print(f"Successfully loaded OE data for {len(oe_data)} flights.")
            else:
                print("No OE data loaded.")
                if default_crew_position == 'auto':
                    default_crew_position = 'captain'
        except Exception as e:
            print(f"Error loading OE data: {e}")
            if default_crew_position == 'auto':
                default_crew_position = 'captain'

    print(f"Processing flights from {flights_csv} as {default_crew_position.replace('_', ' ').title() if default_crew_position != 'auto' else 'Auto'}...")

    if not os.path.exists(flights_csv):
        print(f"Error: Flight data file '{flights_csv}' not found.")
        return

    try:
        df = pd.read_csv(flights_csv)
        rows_processed = len(df)

        # Format tail numbers
        df['TAIL'] = df['TAIL'].apply(format_tail_number)

        # Convert date format to YYYY-MM-DD
        df['DEPT_DATE'] = df['DEPT_DATE'].apply(format_date_logbook_aero)

        # Format times
        df['OUT'] = df['OUT'].apply(format_time_hhmm)
        df['IN'] = df['IN'].apply(format_time_hhmm)

        # Calculate night time
        df['Night Time'] = df.apply(estimate_night_time, axis=1)

        # Calculate actual instrument time (50% of night time)
        df['Act Inst'] = df.apply(calculate_actual_instrument, axis=1)

        # Calculate day and night landings
        day_night_landings = df.apply(process_landings, axis=1, result_type='expand')
        df['Day Landings'] = day_night_landings[0]
        df['Night Landings'] = day_night_landings[1]

        # Calculate day and night takeoffs
        day_night_takeoffs = df.apply(process_takeoffs, axis=1, result_type='expand')
        df['Day Takeoffs'] = day_night_takeoffs[0]
        df['Night Takeoffs'] = day_night_takeoffs[1]

        # Count approaches
        df['Approaches'] = df.apply(count_approaches, axis=1)

        # Determine crew position for each flight if using auto mode
        if default_crew_position == 'auto':
            df['CrewPosition'] = df.apply(lambda row: determine_crew_position(row, 'captain', oe_data), axis=1)
            crew_times = df.apply(
                lambda row: assign_crew_time(row, row['CrewPosition'], oe_data),
                axis=1, result_type='expand'
            )
            df['PIC_Name'] = df.apply(lambda row: get_pic_name(row, row['CrewPosition'], pilot_name), axis=1)
            df['SIC_Name'] = df.apply(lambda row: get_sic_name(row, row['CrewPosition'], pilot_name), axis=1)
        else:
            crew_times = df.apply(lambda row: assign_crew_time(row, default_crew_position, oe_data), axis=1, result_type='expand')
            df['PIC_Name'] = df.apply(lambda row: get_pic_name(row, default_crew_position, pilot_name), axis=1)
            df['SIC_Name'] = df.apply(lambda row: get_sic_name(row, default_crew_position, pilot_name), axis=1)

        # Add crew position time columns
        df['PIC_Time'] = crew_times['PIC_Time']
        df['CoPilot_Time'] = crew_times['CoPilot_Time']
        df['MultiPilot_Time'] = crew_times['MultiPilot_Time']
        df['XC'] = crew_times['XC_Time']

        # Cap Night Time and IFR Time to not exceed Total Time (block hours)
        df['Night Time'] = df.apply(lambda row: min(safe_float_conversion(row['Night Time']), safe_float_conversion(row['BLK_HRS'])), axis=1)
        df['Act Inst'] = df.apply(lambda row: min(safe_float_conversion(row['Act Inst']), safe_float_conversion(row['BLK_HRS'])), axis=1)

        # Rename columns to logbook.aero format
        df = df.rename(columns=LOGBOOK_AERO_COLUMN_MAPPING)

        # Build route column (Departure-Arrival)
        df['Route'] = df['Departure_Airfield'] + '-' + df['Arrival_Airfield']

        # Reorganize columns in logbook.aero order
        column_order = [
            'Date',
            'Departure_Airfield',
            'Arrival_Airfield',
            'Route',
            'Departure_Time',
            'Arrival_Time',
            'Aircraft_Type',
            'Aircraft_Registration',
            'Total_Time',
            'MultiPilot_Time',
            'PIC_Name',
            'SIC_Name',
            'Takeoff_Day',
            'Takeoff_Night',
            'Landing_Day',
            'Landing_Night',
            'Night_Time',
            'IFR_Time',
            'PIC_Time',
            'CoPilot_Time',
            'XC_Time',
            'Instrument_Approach',
        ]

        # Only include columns that exist in the dataframe
        final_columns = [col for col in column_order if col in df.columns]

        df = df[final_columns]

        # Format float columns with 2 decimal places
        float_columns = ['Total_Time', 'MultiPilot_Time', 'Night_Time', 'IFR_Time',
                         'PIC_Time', 'CoPilot_Time', 'XC_Time']
        for col in float_columns:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: round(safe_float_conversion(x), 1))

        # Ensure integer columns are integers
        int_columns = ['Takeoff_Day', 'Takeoff_Night', 'Landing_Day', 'Landing_Night', 'Instrument_Approach']
        for col in int_columns:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: int(safe_float_conversion(x)))

        df.to_csv(output_csv, index=False)
        print(f"Done! Processed {rows_processed} flights. Output written to {output_csv}")

    except Exception as e:
        print(f"Error processing flight data: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

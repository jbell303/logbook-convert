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

# FAA logbook column mapping - maps original columns to FAA standard columns
FAA_COLUMN_MAPPING = {
    'DEPT_DATE': 'Date',
    'ORG': 'Route From',
    'DEST': 'Route To',
    'EQUIP': 'Aircraft Type',
    'TAIL': 'Aircraft Ident.',
    'OUT': 'Out',
    'OFF': 'Off',
    'ON': 'On',
    'IN': 'In',
    'FLT_HRS': 'Duration',
    'BLK_HRS': 'Block',
    'Night Time': 'Night',
    'Day Landings': 'Day Landings',
    'Night Landings': 'Night Landings',
    'XC': 'Cross Country',
    'Act Inst': 'Actual Instrument',
    'Approaches': 'Approaches'
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
            # Default to noon for malformed times
            time_str = "12:00"
            print(f"Warning: Malformed time value '{time_str}' for date {date_str}, using {time_str} instead.")
        
        dt = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %H:%M")
        return dt.replace(tzinfo=pytz.utc)
    except ValueError as e:
        # Print warning and use default time
        print(f"Warning: Could not parse time '{time_str}' for date {date_str}: {e}")
        # Return noon on that date as fallback
        try:
            dt = datetime.strptime(f"{date_str} 12:00", "%m/%d/%Y %H:%M")
            return dt.replace(tzinfo=pytz.utc)
        except ValueError:
            # If even the date is invalid, use current date and time
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
    
    Args:
        airport_code: IATA airport code
        date: datetime object for the day to calculate
        
    Returns:
        Tuple of (sunrise, sunset) datetimes in UTC timezone, or (None, None) if data not available
        
    Note:
        This function is cached for performance.
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
    
    Night flying is determined based on sunrise/sunset times along the route.
    For flights crossing multiple time zones (>4 hours diff), a more detailed
    calculation is performed sampling multiple points along the route.
    
    Args:
        row: Pandas DataFrame row containing flight data
        
    Returns:
        Float representing night flying hours
    """
    origin = row['ORG']
    destination = row['DEST']
    date = datetime.strptime(row['DEPT_DATE'], "%m/%d/%Y")
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
            return round(flt_time, 2)
        elif (off_time < sunset < on_time) or (off_time < sunrise < on_time):
            return round(flt_time * 0.5, 2)
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
                # Log the error but continue processing
                print(f"Warning: Error calculating sun position at lat={lat}, lon={lon}: {e}")
                pass
            current_time += time_increment
        estimated_night_hours = round(total_night_minutes / 60.0, 2)
        return min(estimated_night_hours, flt_time)

def is_night_landing(landing_time, destination):
    """
    Determine if a landing occurs during night time.
    
    Args:
        landing_time: UTC datetime of the landing
        destination: IATA code of the destination airport
        
    Returns:
        True if it's a night landing, False otherwise
    """
    airport_data = get_airport_data(destination)
    if not airport_data:
        return False  # Default to day landing if airport data not available
    
    name, tzname, lat, lon = airport_data
    location = LocationInfo(name=name, region="", timezone=tzname, latitude=lat, longitude=lon)
    
    # Get sunrise/sunset for the landing date
    s = sun(location.observer, date=landing_time.date(), tzinfo=pytz.timezone(tzname))
    sunrise = s['sunrise'].astimezone(pytz.utc)
    sunset = s['sunset'].astimezone(pytz.utc)
    
    # Civil twilight is approximately 30 minutes after sunset
    civil_twilight = sunset + timedelta(minutes=30)
    
    # If landing time is after sunset/civil twilight or before sunrise, it's a night landing
    return landing_time >= civil_twilight or landing_time <= sunrise

def process_landings(row):
    """
    Process a flight row to determine if the landing was during day or night.
    Adds day_landings and night_landings columns.
    
    Only counts a landing if the LANDING column indicates the crew member performed the landing (value of 1).
    
    Args:
        row: Pandas DataFrame row containing flight data
        
    Returns:
        Tuple of (day_landings, night_landings) counts (0 or 1)
    """
    # First check if this crew member performed the landing
    performed_landing = False
    try:
        landing_val = row.get('LANDING', 0)
        if landing_val == 1 or landing_val == '1':
            performed_landing = True
    except:
        # If there's any error processing the landing value, assume no landing
        performed_landing = False
    
    # If the crew member didn't perform the landing, return 0 for both day and night landings
    if not performed_landing:
        return 0, 0
    
    # If they did perform the landing, determine if it was day or night
    try:
        destination = row['DEST']
        landing_time = parse_time(row['DEPT_DATE'], row['ON'])
        
        # Check if it's a night landing
        if is_night_landing(landing_time, destination):
            return 0, 1  # 0 day landings, 1 night landing
        else:
            return 1, 0  # 1 day landing, 0 night landings
    except Exception as e:
        # If there's any error processing, default to day landing
        print(f"Warning: Error determining landing type: {e}. Defaulting to day landing.")
        return 1, 0  # Default to day landing on error

def record_approaches(row):
    """
    Record approaches for a flight.
    If a landing was performed, log an approach in the format 1;XXX
    where XXX is the destination airport code.
    """
    # Check if this crew member performed the landing
    if 'LANDING' in row and (row['LANDING'] == 1 or row['LANDING'] == '1'):
        # If they performed the landing, record an approach
        destination = row['DEST']
        return f"1;{destination}"
    else:
        # If no landing was performed, leave the field empty
        return ""

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Process flight data and format it according to FAA logbook standards.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument(
        '--flights', 
        type=str, 
        default="DWNLD_3983442.csv",
        help='Input CSV file containing flight data with columns for flight times, origins, destinations, etc.'
    )
    
    # Generate a default output filename that includes the current date
    default_output = f"FAA_Logbook_{datetime.now().strftime('%Y-%m-%d')}.csv"
    
    parser.add_argument(
        '--output', 
        type=str, 
        default=default_output,
        help='Output CSV file path where the formatted FAA logbook data will be written'
    )
    
    parser.add_argument(
        '--position', 
        type=str, 
        choices=['captain', 'first_officer', 'relief_first_officer', 'relief_captain', 'auto'],
        default='captain', 
        help='Crew position used for logging flight time. Use "auto" with --oe-data to determine automatically based on OE data'
    )
    
    parser.add_argument(
        '--oe-data', 
        type=str, 
        help='Optional CSV file with Operating Experience data that contains crew position information and custom logging rules'
    )
    
    args = parser.parse_args()
    
    # If no custom output file is specified, generate one based on the input filename
    if args.output == default_output and args.flights != "DWNLD_3983442.csv":
        # Extract the base filename without extension
        input_base = os.path.splitext(os.path.basename(args.flights))[0]
        args.output = f"FAA_{input_base}_{datetime.now().strftime('%Y-%m-%d')}.csv"
        print(f"Auto-generating output filename: {args.output}")
    
    return args

def handle_specific_flights():
    """
    This function contains specific handling for flight numbers that 
    require special treatment when OE data is missing or incomplete.
    Returns a dictionary mapping flight IDs to additional data.
    """
    # No specific handling needed now that we understand the logic better
    return {}

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
        
        # Check for required columns
        if 'FLIGHT' not in oe_df.columns:
            print("Warning: OE data file must have FLIGHT column.")
            return {}
        
        # Create a dictionary mapping flight IDs to crew roles and time info
        oe_data = {}
        flight_count = 0
        
        for _, row in oe_df.iterrows():
            flight_id = str(row['FLIGHT']).strip().zfill(4)  # Ensure consistent formatting with leading zeros
            
            # Initialize flight data dictionary
            flight_data = {
                'role': 'captain',  # Default role
                'pic_time': None,   # Custom PIC time if available
                'sic_time': None    # Custom SIC time if available
            }
            
            # Determine role based on seat
            if 'SEAT' in oe_df.columns:
                seat_value = str(row.get('SEAT', '')).strip().upper()
                
                if seat_value in ['CAPT', 'CPT', 'CAPTAIN']:
                    # Captain seat
                    flight_data['role'] = 'captain'
                    if 'PIC_OE' in oe_df.columns:
                        pic_time = safe_float_conversion(row.get('PIC_OE', 0))
                        flight_data['pic_time'] = pic_time if pic_time > 0 else None
                        flight_data['sic_time'] = 0.0  # No SIC time for Captain
                
                elif seat_value in ['FO', 'F/O', 'FIRST OFFICER']:
                    # First Officer seat
                    flight_data['role'] = 'first_officer'
                    if 'SIC_OE' in oe_df.columns:
                        sic_time = safe_float_conversion(row.get('SIC_OE', 0))
                        flight_data['sic_time'] = sic_time if sic_time > 0 else None
                        flight_data['pic_time'] = 0.0  # No PIC time for First Officer
                
                elif seat_value in ['RFO', 'RF/O', 'R/FO', 'RELIEF FIRST OFFICER']:
                    # Relief First Officer - always SIC time only
                    flight_data['role'] = 'relief_first_officer'
                    flight_data['pic_time'] = 0.0  # No PIC time for RFO
                    
                    # Check for SIC_RFO_OE column
                    if 'SIC_RFO_OE' in oe_df.columns:
                        sic_time = safe_float_conversion(row.get('SIC_RFO_OE', 0))
                        flight_data['sic_time'] = sic_time if sic_time > 0 else None
                
                elif seat_value in ['RF2', 'RC', 'RELIEF CAPTAIN']:
                    # Relief Captain - always PIC time only
                    flight_data['role'] = 'relief_captain'  # Use specific relief_captain role
                    flight_data['sic_time'] = 0.0  # No SIC time for Relief Captain
                    
                    # Check for PIC_RFO_OE column
                    if 'PIC_RFO_OE' in oe_df.columns:
                        pic_time = safe_float_conversion(row.get('PIC_RFO_OE', 0))
                        flight_data['pic_time'] = pic_time if pic_time > 0 else None
            
            # If role assignment was based on something else or seat info not available
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
            
            # Fallback for regular PIC/SIC columns if no specific times are set
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
            
            # Only log the first few flights for debugging
            if flight_count <= 3:
                print(f"Example flight {flight_id}: Seat: {row.get('SEAT', 'unknown')}, Role: {flight_data['role']}")
        
        return oe_data
    except Exception as e:
        print(f"Error loading OE data: {e}")
        return {}

def determine_crew_position(row, default_position, oe_data):
    """
    Determine crew position based on OE data if available,
    otherwise use the default position.
    """
    if not oe_data:
        return default_position
    
    flight_id = str(row.get('FLIGHT', '')).strip().zfill(4)
    if flight_id in oe_data:
        return oe_data[flight_id]['role']
    
    return default_position

def assign_crew_time(row, position, oe_data=None):
    """
    Assign flight time based on crew position.
    Returns dict with FAA time categories.
    
    If oe_data is provided and contains custom PIC/SIC times for this flight,
    those times will be used instead of calculating from the block time.
    """
    # Use block time for PIC/SIC
    block_time = safe_float_conversion(row['BLK_HRS'])
    # Use flight time for duration
    flight_time = safe_float_conversion(row['FLT_HRS'])
    
    distribution = CREW_POSITION_DISTRIBUTION[position]
    
    # Default calculations based on distribution
    pic_time = block_time * distribution['PIC']
    sic_time = block_time * distribution['SIC']
    duration = flight_time * distribution['Duration']
    
    # Check if we have custom PIC/SIC times from OE data
    flight_id = str(row.get('FLIGHT', '')).strip().zfill(4)
    if oe_data and flight_id in oe_data:
        flight_data = oe_data[flight_id]
        if flight_data['pic_time'] is not None:
            pic_time = flight_data['pic_time']
        if flight_data['sic_time'] is not None:
            sic_time = flight_data['sic_time']
    
    return {
        'PIC': pic_time,
        'SIC': sic_time,
        'Duration': duration,
        'XC': block_time  # XC time equals block time
    }

def safe_float_conversion(value):
    """
    Safely convert a value to float, returning 0.0 if conversion fails.
    Handles None, empty strings, '.', and other invalid values.
    """
    try:
        # Check for invalid values
        if value is None or value == '' or value == '.':
            return 0.0
        return float(value)
    except (ValueError, TypeError):
        return 0.0

def format_tail_number(tail_number):
    """
    Convert a numeric tail number to a full aircraft ID in the format of NXXXFE.
    Example: 115 -> N115FE
    """
    try:
        # If the tail number is numeric, format it as N{number}FE
        if str(tail_number).isdigit():
            return f"N{tail_number}FE"
        # If it's already in the right format or not numeric, return as is
        return str(tail_number)
    except:
        # If any error occurs, return the original value
        return str(tail_number)

def calculate_actual_instrument(row):
    """
    Calculate actual instrument time as 50% of night time.
    This is an approximation.
    """
    night_time = safe_float_conversion(row['Night Time'])
    return night_time * 0.5

def main_web(args):
    """
    Web version of the main function that accepts pre-parsed args
    instead of using argparse.
    
    This version suppresses some print statements and is designed
    to be called from a web application.
    """
    flights_csv = args.flights
    output_csv = args.output
    default_crew_position = args.position
    oe_file = args.oe_data
    
    # Load OE data if provided
    oe_data = {}
    if oe_file:
        try:
            oe_data = load_oe_data(oe_file)
            if default_crew_position == 'auto' and not oe_data:
                default_crew_position = 'captain'  # Fallback to captain if auto requested but no OE data
        except Exception as e:
            if default_crew_position == 'auto':
                default_crew_position = 'captain'
    
    # Check if the input file exists
    if not os.path.exists(flights_csv):
        raise FileNotFoundError(f"Flight data file '{flights_csv}' not found.")
    
    # Process the data
    df = pd.read_csv(flights_csv)
    rows_processed = len(df)
    
    # Format tail numbers
    df['TAIL'] = df['TAIL'].apply(format_tail_number)
    
    # Calculate night time
    df['Night Time'] = df.apply(estimate_night_time, axis=1)
    
    # Calculate actual instrument time (50% of night time)
    df['Act Inst'] = df.apply(calculate_actual_instrument, axis=1)
    
    # Calculate day and night landings
    day_night_landings = df.apply(process_landings, axis=1, result_type='expand')
    df['Day Landings'] = day_night_landings[0]
    df['Night Landings'] = day_night_landings[1]
    
    # Record approaches
    df['Approaches'] = df.apply(record_approaches, axis=1)
    
    # Determine crew position for each flight if using auto mode
    if default_crew_position == 'auto':
        # Create a new column for the crew position
        df['CrewPosition'] = df.apply(lambda row: determine_crew_position(row, 'captain', oe_data), axis=1)
        
        # Apply crew time calculations based on determined position
        crew_times = df.apply(
            lambda row: assign_crew_time(row, row['CrewPosition'], oe_data), 
            axis=1, result_type='expand'
        )
    else:
        # Use the default crew position for all flights
        crew_times = df.apply(lambda row: assign_crew_time(row, default_crew_position, oe_data), axis=1, result_type='expand')
    
    # Add crew position time columns
    df['PIC'] = crew_times['PIC']
    df['SIC'] = crew_times['SIC']
    df['XC'] = crew_times['XC']
    
    # Rename columns to FAA format
    df = df.rename(columns=FAA_COLUMN_MAPPING)
    
    # Reorganize columns in a logical FAA logbook order
    column_order = [
        'Date', 'Aircraft Type', 'Aircraft Ident.', 
        'Route From', 'Route To', 
        'Out', 'Off', 'On', 'In', 
        'Duration', 'Block', 'PIC', 'SIC', 'Cross Country', 'Night', 'Actual Instrument',
        'Day Landings', 'Night Landings', 'Approaches'
    ]
    
    # Only include columns in column_order that exist in the dataframe
    final_columns = [col for col in column_order if col in df.columns]
    
    # Add any other columns not in our predefined order
    for col in df.columns:
        if col not in final_columns and col != 'CrewPosition':  # Skip temporary CrewPosition column
            final_columns.append(col)
    
    df = df[final_columns]
    
    # Format float columns with 1 decimal place
    for col in ['Duration', 'Block', 'PIC', 'SIC', 'Cross Country', 'Night', 'Actual Instrument']:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: round(safe_float_conversion(x), 1))
    
    df.to_csv(output_csv, index=False)
    return rows_processed

def main():
    """
    Main function for processing flight logbook data.
    
    This script processes airline flight data and formats it according to FAA logbook standards.
    It can automatically detect crew positions from Operating Experience data or use a specified
    position for all flights.
    
    The process involves:
    1. Loading flight and Operating Experience data
    2. Calculating night time, landings, and approaches
    3. Determining crew positions and appropriate time logging
    4. Reformatting to FAA logbook standards
    5. Writing the result to CSV
    
    Command line parameters control input file, output location, and crew position.
    """
    # Parse command line arguments
    args = parse_args()
    flights_csv = args.flights
    output_csv = args.output
    default_crew_position = args.position
    oe_file = args.oe_data
    
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
                    default_crew_position = 'captain'  # Fallback to captain if auto requested but no OE data
        except Exception as e:
            print(f"Error loading OE data: {e}")
            if default_crew_position == 'auto':
                default_crew_position = 'captain'
    
    print(f"Processing flights from {flights_csv} as {default_crew_position.replace('_', ' ').title() if default_crew_position != 'auto' else 'Auto'}...")
    
    # Check if the input file exists
    if not os.path.exists(flights_csv):
        print(f"Error: Flight data file '{flights_csv}' not found.")
        return
    
    try:
        # Rest of the processing
        df = pd.read_csv(flights_csv)
        rows_processed = len(df)
        
        # Format tail numbers
        df['TAIL'] = df['TAIL'].apply(format_tail_number)
        
        # Calculate night time
        df['Night Time'] = df.apply(estimate_night_time, axis=1)
        
        # Calculate actual instrument time (50% of night time)
        df['Act Inst'] = df.apply(calculate_actual_instrument, axis=1)
        
        # Calculate day and night landings
        day_night_landings = df.apply(process_landings, axis=1, result_type='expand')
        df['Day Landings'] = day_night_landings[0]
        df['Night Landings'] = day_night_landings[1]
        
        # Record approaches
        df['Approaches'] = df.apply(record_approaches, axis=1)
        
        # Determine crew position for each flight if using auto mode
        if default_crew_position == 'auto':
            # Create a new column for the crew position
            df['CrewPosition'] = df.apply(lambda row: determine_crew_position(row, 'captain', oe_data), axis=1)
            
            # Apply crew time calculations based on determined position
            crew_times = df.apply(
                lambda row: assign_crew_time(row, row['CrewPosition'], oe_data), 
                axis=1, result_type='expand'
            )
        else:
            # Use the default crew position for all flights
            crew_times = df.apply(lambda row: assign_crew_time(row, default_crew_position, oe_data), axis=1, result_type='expand')
        
        # Add crew position time columns
        df['PIC'] = crew_times['PIC']
        df['SIC'] = crew_times['SIC']
        df['XC'] = crew_times['XC']
        
        # Rename columns to FAA format
        df = df.rename(columns=FAA_COLUMN_MAPPING)
        
        # Reorganize columns in a logical FAA logbook order
        column_order = [
            'Date', 'Aircraft Type', 'Aircraft Ident.', 
            'Route From', 'Route To', 
            'Out', 'Off', 'On', 'In', 
            'Duration', 'Block', 'PIC', 'SIC', 'Cross Country', 'Night', 'Actual Instrument',
            'Day Landings', 'Night Landings', 'Approaches'
        ]
        
        # Only include columns in column_order that exist in the dataframe
        final_columns = [col for col in column_order if col in df.columns]
        
        # Add any other columns not in our predefined order
        for col in df.columns:
            if col not in final_columns and col != 'CrewPosition':  # Skip temporary CrewPosition column
                final_columns.append(col)
        
        df = df[final_columns]
        
        # Format float columns with 1 decimal place
        for col in ['Duration', 'Block', 'PIC', 'SIC', 'Cross Country', 'Night', 'Actual Instrument']:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: round(safe_float_conversion(x), 1))
        
        df.to_csv(output_csv, index=False)
        print(f"Done! Processed {rows_processed} flights. Output written to {output_csv}")
    
    except Exception as e:
        print(f"Error processing flight data: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
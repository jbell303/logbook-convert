from flask import Flask, request, render_template, send_file, flash
import os
import tempfile
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import polars as pl
from astral.sun import sun
from astral import LocationInfo
import pytz
import airportsdata
import argparse
from functools import lru_cache

app = Flask(__name__)
app.secret_key = 'logbook-formatter-secret-key'  # Required for flash messages
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Limit uploads to 16MB
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()  # Use system temp directory for uploads

ALLOWED_EXTENSIONS = {'csv'}

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

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
        # Check for malformed or missing date strings
        if date_str is None:
            print(f"Warning: Date string is None, using current date")
            date_str = datetime.now().strftime("%m/%d/%Y")
            
        # Check for malformed time strings
        if time_str is None or time_str == '' or time_str == '.' or not isinstance(time_str, str):
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
            return datetime.now(pytz.utc)

@lru_cache(maxsize=128)
def get_timezone_diff(tz1, tz2):
    """
    Calculate the time difference in hours between two timezones.
    This function is cached to improve performance for repeated calls.
    """
    # Create a naive datetime for the pytz calculations
    now = datetime.now(pytz.utc).replace(tzinfo=None)
    
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

def process_flight_data(flights_csv, output_csv, crew_position, oe_file=None):
    """Web-friendly version of the flight data processing function."""
    
    # Validate crew position
    if crew_position not in CREW_POSITION_DISTRIBUTION:
        # If auto is specified but we don't have OE data, default to captain
        if crew_position == 'auto':
            crew_position = 'captain'
        else:
            raise ValueError(f"Invalid crew position: {crew_position}")
    
    # Load flight data
    df = pl.read_csv(flights_csv)
    rows_processed = len(df)
    
    # Format tail numbers
    df = df.with_columns(pl.col("TAIL").map_elements(format_tail_number, return_dtype=pl.Utf8).alias("TAIL"))
    
    # Process each flight for night time, landings, etc.
    results = []
    
    for row in df.iter_rows(named=True):
        try:
            # Get flight details - with validation
            origin = row.get('ORG', '')
            destination = row.get('DEST', '')
            date_str = row.get('DEPT_DATE')
            
            # Skip rows with missing critical data
            if not origin or not destination or not date_str:
                print(f"Warning: Skipping row with missing critical data: ORG={origin}, DEST={destination}, DATE={date_str}")
                continue
                
            try:
                date = datetime.strptime(date_str, "%m/%d/%Y")
            except (ValueError, TypeError):
                print(f"Warning: Invalid date format '{date_str}', using current date")
                date = datetime.now()
            
            # Parse times with better error handling
            off_time = parse_time(date_str, row.get('OFF'))
            on_time = parse_time(date_str, row.get('ON'))
            
            # Calculate flight times
            flt_time = safe_float_conversion(row.get('FLT_HRS', 0))
            blk_time = safe_float_conversion(row.get('BLK_HRS', 0))
            
            # Calculate night time
            night_time = estimate_night_time(row, off_time, on_time, flt_time, origin, destination, date)
            
            # Calculate instrument time (50% of night time)
            act_inst = night_time * 0.5
            
            # Process landings
            day_landings, night_landings = process_landings(row, destination, off_time, on_time)
            
            # Record approaches
            approaches = ""
            if 'LANDING' in row and (row['LANDING'] == 1 or row['LANDING'] == '1'):
                approaches = f"1;{destination}"
            
            # Determine crew time based on position
            distribution = CREW_POSITION_DISTRIBUTION[crew_position]
            pic_time = blk_time * distribution['PIC']
            sic_time = blk_time * distribution['SIC']
            duration = flt_time * distribution['Duration']
            
            # Create result row
            result_row = dict(row)
            result_row['Night Time'] = night_time
            result_row['Act Inst'] = act_inst
            result_row['Day Landings'] = day_landings
            result_row['Night Landings'] = night_landings
            result_row['Approaches'] = approaches
            result_row['PIC'] = pic_time
            result_row['SIC'] = sic_time
            result_row['XC'] = blk_time
            
            results.append(result_row)
            
        except Exception as e:
            # If a row fails to process, log it and continue with the next row
            print(f"Warning: Error processing row: {e}")
            continue
    
    if not results:
        raise ValueError("No valid flight data could be processed")
    
    # Convert back to DataFrame
    result_df = pl.DataFrame(results)
    
    # Rename columns to FAA format
    for old_col, new_col in FAA_COLUMN_MAPPING.items():
        if old_col in result_df.columns:
            result_df = result_df.rename({old_col: new_col})
    
    # Format float columns with 1 decimal place
    float_cols = ['Duration', 'Block', 'PIC', 'SIC', 'Cross Country', 'Night', 'Actual Instrument']
    for col in float_cols:
        if col in result_df.columns:
            result_df = result_df.with_columns(pl.col(col).map_elements(
                lambda x: round(safe_float_conversion(x), 1), 
                return_dtype=pl.Float64
            ).alias(col))
    
    # Write to CSV
    result_df.write_csv(output_csv)
    
    return rows_processed

def estimate_night_time(row, off_time, on_time, flt_time, origin, destination, date):
    """Estimate night flying time."""
    origin_data = get_airport_data(origin)
    dest_data = get_airport_data(destination)
    
    if not origin_data or not dest_data:
        return 0.0
    
    tz_diff = get_timezone_diff(origin_data[1], dest_data[1])
    
    if tz_diff <= 4:
        # Simple rule based on destination
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
        # More complex calculation for long flights
        increment_minutes = 10
        total_night_minutes = 0
        segments = int((on_time - off_time).total_seconds() / (increment_minutes * 60))
        
        if segments <= 0:
            return 0.0
            
        for i in range(segments):
            progress = i / segments
            current_time = off_time + (on_time - off_time) * progress
            
            # Interpolate position
            lat = origin_data[2] + progress * (dest_data[2] - origin_data[2])
            lon = origin_data[3] + progress * (dest_data[3] - origin_data[3])
            
            try:
                location = LocationInfo(latitude=lat, longitude=lon)
                s = sun(location.observer, date=current_time.date(), tzinfo=pytz.timezone(dest_data[1]))
                if current_time < s['sunrise'].astimezone(pytz.utc) or current_time > s['sunset'].astimezone(pytz.utc):
                    total_night_minutes += increment_minutes
            except Exception as e:
                # Skip this segment if there's an error
                print(f"Warning: Error calculating sun position at lat={lat}, lon={lon}: {e}")
        
        night_hours = round(total_night_minutes / 60.0, 2)
        return min(night_hours, flt_time)

def is_night_landing(landing_time, destination):
    """Determine if landing occurred during night time."""
    airport_data = get_airport_data(destination)
    if not airport_data:
        return False
    
    name, tzname, lat, lon = airport_data
    location = LocationInfo(name=name, region="", timezone=tzname, latitude=lat, longitude=lon)
    
    s = sun(location.observer, date=landing_time.date(), tzinfo=pytz.timezone(tzname))
    sunrise = s['sunrise'].astimezone(pytz.utc)
    sunset = s['sunset'].astimezone(pytz.utc)
    
    # Civil twilight is 30 minutes after sunset
    civil_twilight = sunset + timedelta(minutes=30)
    
    return landing_time >= civil_twilight or landing_time <= sunrise

def process_landings(row, destination, off_time, on_time):
    """Process landings to determine if day or night."""
    # Check if landing was performed
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
        landing_time = on_time
        if is_night_landing(landing_time, destination):
            return 0, 1  # Night landing
        else:
            return 1, 0  # Day landing
    except Exception as e:
        print(f"Warning: Error determining landing type: {e}")
        return 1, 0  # Default to day landing

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Check if flight file is present
        if 'flights_file' not in request.files:
            flash('No flights file selected', 'error')
            return render_template('index.html')
        
        flights_file = request.files['flights_file']
        
        if flights_file.filename == '':
            flash('No flights file selected', 'error')
            return render_template('index.html')
            
        if not allowed_file(flights_file.filename):
            flash('Invalid file type. Please upload CSV files.', 'error')
            return render_template('index.html')
        
        # Get crew position
        crew_position = request.form.get('crew_position', 'captain')
        
        # Create temporary filenames
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        flights_filename = f"flights_{timestamp}.csv"
        oe_filename = None
        output_filename = f"FAA_Logbook_{timestamp}.csv"
        
        # Save uploaded flights file
        flights_path = os.path.join(app.config['UPLOAD_FOLDER'], flights_filename)
        flights_file.save(flights_path)
        
        # Check if OE data was uploaded
        oe_path = None
        if 'oe_file' in request.files and request.files['oe_file'].filename != '':
            oe_file = request.files['oe_file']
            if allowed_file(oe_file.filename):
                oe_filename = f"oe_{timestamp}.csv"
                oe_path = os.path.join(app.config['UPLOAD_FOLDER'], oe_filename)
                oe_file.save(oe_path)
            else:
                os.unlink(flights_path)
                flash('Invalid OE file type. Please upload CSV files.', 'error')
                return render_template('index.html')
        
        # Process the files
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
        
        try:
            # Call the processing function
            process_flight_data(flights_path, output_path, crew_position, oe_path)
            
            # Send the processed file to the user
            return send_file(output_path, as_attachment=True, 
                            download_name=f"FAA_Logbook_{datetime.now().strftime('%Y-%m-%d')}.csv")
            
        except Exception as e:
            flash(f"Error processing files: {str(e)}", 'error')
            return render_template('index.html')
        finally:
            # Clean up temporary files
            if os.path.exists(flights_path):
                os.unlink(flights_path)
            if oe_path and os.path.exists(oe_path):
                os.unlink(oe_path)
            if os.path.exists(output_path):
                os.unlink(output_path)
        
    return render_template('index.html')

if __name__ == '__main__':
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Run the Logbook Formatter web application')
    parser.add_argument('--port', type=int, default=5000, help='Port to run the web server on')
    parser.add_argument('--host', type=str, default='127.0.0.1', help='Host to run the web server on')
    args = parser.parse_args()
    
    # Run the app
    app.run(debug=True, host=args.host, port=args.port) 
# Logbook Formatter

A Python utility for processing and formatting flight logbook data according to FAA standards, available both as a command-line tool and a web application.

## Features

- Convert airline flight data to FAA standard format
- Calculate night flying time based on sunrise/sunset data
- Determine day and night landings
- Calculate PIC/SIC time based on crew position
- Support for relief crew positions
- Web interface for easy file uploads and downloads
- Compatible with Python 3.13+ using polars

## Requirements

- Python 3.x
- polars (modern DataFrame library)
- astral (for sunrise/sunset calculations)
- pytz (timezone support)
- airportsdata (airport database)
- flask (for web application)

Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Web Application (Recommended)

The easiest way to use the tool is through the web interface:

```bash
python app.py --port 5050
```

Then open your browser to http://127.0.0.1:5050 to access the interface.

The web app provides:
- File upload interface for flight data and OE data
- Dropdown to select crew position
- Immediate download of the processed FAA logbook file
- Error handling with user-friendly messages

### Command Line Interface

For automated or batch processing, you can use the command line:

```bash
python format.py --flights <flight_csv_file> --output <output_csv_file> --position <crew_position> --oe-data <oe_data_csv>
```

#### Arguments

- `--flights`: Input CSV file containing flight data (default: "DWNLD_3983442.csv")
- `--output`: Output CSV file path (default: "FAA_Logbook_YYYY-MM-DD.csv")
  - If not specified, output filename is auto-generated based on the input filename
  - Example: Input "2023_flights.csv" → Output "FAA_2023_flights_2024-06-12.csv"
- `--position`: Crew position for logging flight time (default: "captain")
  - Options: captain, first_officer, relief_first_officer, relief_captain, auto
  - Use "auto" with --oe-data to determine position from OE data
- `--oe-data`: Optional CSV file with Operating Experience data

#### Example

```bash
python format.py --flights 2023_flights.csv --position auto --oe-data 2023_OE.csv
```

## Calculations

### Flight Time Distribution

The script distributes flight time based on crew position:
- **Captain**: 100% PIC time
- **First Officer**: 100% SIC time
- **Relief First Officer**: 50% SIC time (50% of flight duration)
- **Relief Captain**: 50% PIC time (50% of flight duration)

### Night Time Calculation

Night time is calculated using two methods:
1. **Simple method** (timezone difference ≤ 4 hours):
   - 100% night if flight is entirely between sunset and sunrise
   - 50% night if flight crosses sunrise or sunset
   - 0% night if flight is entirely in daylight

2. **Advanced method** (timezone difference > 4 hours):
   - Samples positions every 10 minutes along the flight path
   - Determines if each sample is during night time
   - Calculates percentage of flight in darkness

### Landings

- Only counted if the LANDING column has a value of 1
- Night landings are determined based on sunset plus 30 minutes (civil twilight)

### Cross Country

All flight time is logged as cross country time.

### Actual Instrument

Calculated as 50% of night time.

## Data Format

### Input Flight Data
Required columns: DEPT_DATE, ORG, DEST, EQUIP, TAIL, OUT, OFF, ON, IN, FLT_HRS, BLK_HRS, FLIGHT, LANDING

### Operating Experience (OE) Data
Required columns: FLIGHT
Optional columns: SEAT, ROLE, PIC_OE, SIC_OE, PIC_RFO_OE, SIC_RFO_OE

### Output
Standard FAA logbook format with columns for Date, Aircraft Type, Aircraft Ident, Routes, Times, and different categories of flight time.

## Error Handling

The application includes robust error handling:

- **Missing data**: Skips rows with missing critical data
- **Malformed time values**: Replaces with reasonable defaults
- **Airport lookup failures**: Uses fallback timezone data
- **Date/time parsing errors**: Defaults to noon on the given date
- **Sun calculation errors**: Skips problematic calculations while continuing processing

## Assumptions

- Flight dates and times are in format MM/DD/YYYY HH:MM
- All flights are cross-country
- For malformed time values, noon (12:00) is used
- For unknown airports, fallback data is used when available
- For international flights, night calculations are sampled along the route

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. 
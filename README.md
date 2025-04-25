# Logbook Formatter

A Python utility for processing and formatting flight logbook data according to FAA standards.

## Requirements

- Python 3.x
- pandas
- astral
- pytz
- airportsdata

Install dependencies:
```bash
pip install pandas astral pytz airportsdata
```

## Usage

```bash
python format.py --flights <flight_csv_file> --output <output_csv_file> --position <crew_position> --oe-data <oe_data_csv>
```

### Arguments

- `--flights`: Input CSV file containing flight data (default: "DWNLD_3983442.csv")
- `--output`: Output CSV file path (default: "FAA_Logbook_YYYY-MM-DD.csv")
  - If not specified, output filename is auto-generated based on the input filename
  - Example: Input "2023_flights.csv" → Output "FAA_2023_flights_2024-06-12.csv"
- `--position`: Crew position for logging flight time (default: "captain")
  - Options: captain, first_officer, relief_first_officer, relief_captain, auto
  - Use "auto" with --oe-data to determine position from OE data
- `--oe-data`: Optional CSV file with Operating Experience data

### Example

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

## Assumptions

- Flight dates and times are in format MM/DD/YYYY HH:MM
- All flights are cross-country
- For malformed time values, noon (12:00) is used
- For unknown airports, fallback data is used when available
- For international flights, night calculations are sampled along the route

## Error Handling

The script includes robust error handling for common issues:

### Time Format Errors
- Malformed time values like '.' are replaced with 12:00
- Invalid date/time strings are reported with warnings

### Sun Position Calculation Warnings
- Solar calculations can fail in certain geographical areas:
  - Polar regions where the sun may not rise/set
  - Specific coordinates in remote areas
  - Mathematical edge cases along flight routes
- These warnings do not stop processing and minimally affect night time calculations

### Flight Data Processing
- Missing airport data is handled with fallbacks
- Invalid numerical values are safely converted or defaulted to 0.0
- Crew position is determined from OE data when available

## Data Format

### Input Flight Data
Required columns: DEPT_DATE, ORG, DEST, EQUIP, TAIL, OUT, OFF, ON, IN, FLT_HRS, BLK_HRS, FLIGHT, LANDING

### Operating Experience (OE) Data
Required columns: FLIGHT
Optional columns: SEAT, ROLE, PIC_OE, SIC_OE, PIC_RFO_OE, SIC_RFO_OE

### Output
Standard FAA logbook format with columns for Date, Aircraft Type, Aircraft Ident, Routes, Times, and different categories of flight time. 
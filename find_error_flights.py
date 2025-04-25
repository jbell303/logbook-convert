import pandas as pd
import os
from datetime import datetime

print("Looking for flights with coordinates that caused sun position errors...")

# Coordinates from error messages
error_coords = [
    (32.79347530516432, 47.783749201877924),  # "Unable to find a dusk time"
    (29.24589971830986, 72.50648535211269),   # "Unable to find a dawn time"
    (61.17408, -149.99814)                     # "Unable to find a sunrise time"
]

# Try to load 2023 flight data
try:
    flights_file = '2023_flights.csv'
    if os.path.exists(flights_file):
        flights = pd.read_csv(flights_file)
        print(f"Successfully loaded {len(flights)} flights from {flights_file}")
        
        # Load airports data (simplified version of what's in format.py)
        import airportsdata
        airports = airportsdata.load('IATA')
        
        # Function to get airport coordinates
        def get_airport_coords(code):
            try:
                airport = airports.get(code)
                if airport and 'lat' in airport and 'lon' in airport:
                    return float(airport['lat']), float(airport['lon'])
            except:
                pass
            return None
        
        # Check each flight origin/destination
        found_flights = []
        for _, flight in flights.iterrows():
            try:
                org_coords = get_airport_coords(flight['ORG'])
                dest_coords = get_airport_coords(flight['DEST'])
                
                if org_coords and dest_coords:
                    for error_lat, error_lon in error_coords:
                        # Check if this flight path might cross near these coordinates
                        # Very simplified check - just see if coordinates are in general vicinity
                        org_lat, org_lon = org_coords
                        dest_lat, dest_lon = dest_coords
                        
                        # Check if error coordinates might be on this route (simplified check)
                        lat_in_range = min(org_lat, dest_lat) - 10 <= error_lat <= max(org_lat, dest_lat) + 10
                        
                        # Handle longitude wrap-around
                        if min(org_lon, dest_lon) <= max(org_lon, dest_lon) + 180:
                            lon_in_range = min(org_lon, dest_lon) - 10 <= error_lon <= max(org_lon, dest_lon) + 10
                        else:
                            # Route crossing the ±180° longitude line
                            lon_in_range = error_lon <= min(org_lon, dest_lon) + 10 or error_lon >= max(org_lon, dest_lon) - 10
                        
                        if lat_in_range and lon_in_range:
                            date = flight.get('DEPT_DATE', 'Unknown')
                            route = f"{flight['ORG']}-{flight['DEST']}"
                            error_desc = "Route near coordinates that caused sun calculation error"
                            
                            # Identify which error this matches
                            if abs(error_lat - 32.79) < 1 and abs(error_lon - 47.78) < 1:
                                error_type = "Unable to find a dusk time"
                            elif abs(error_lat - 29.24) < 1 and abs(error_lon - 72.50) < 1:
                                error_type = "Unable to find a dawn time" 
                            elif abs(error_lat - 61.17) < 1 and abs(error_lon - -149.99) < 1:
                                error_type = "Unable to find a sunrise time"
                            else:
                                error_type = "Unknown error"
                                
                            found_flights.append({
                                'Date': date,
                                'Flight': flight.get('FLIGHT', 'Unknown'),
                                'Route': route,
                                'Error': error_type,
                                'Matching Coordinates': f"({error_lat:.2f}, {error_lon:.2f})"
                            })
            except Exception as e:
                print(f"Error processing flight: {e}")
        
        if found_flights:
            print("\nFlights potentially causing sun position errors:")
            for flight in found_flights:
                print(f"Date: {flight['Date']}, Flight: {flight['Flight']}, Route: {flight['Route']}")
                print(f"  Error: {flight['Error']}")
                print(f"  Coordinates: {flight['Matching Coordinates']}")
                print()
        else:
            print("\nNo flights found matching the error coordinates.")
    else:
        print(f"Error: {flights_file} not found. Please provide the correct filename.")
except Exception as e:
    print(f"Error running script: {e}") 
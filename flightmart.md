# Flightmart Flight Hours and Operating Experience Download

## Overview
This guide shows you how to download flight hours and Operating Experience data from Flightmart via PFC for use with the Logbook Formatter.

## Instructions

### Step 1: Login to PFC
- Login to PFC on a device with firewall access (e.g. iPad)
- Select **Reports** on the right side of the screen

<img src="screenshots/1_pfc.PNG" alt="PFC Screenshot" width="600"/>

### Step 2: Access Flight Data
- Select **Flight and Block Hours / Operating Experience** on the left side of the screen

<img src="screenshots/2_fm_home.PNG" alt="Flightmart main menu" width="600"/>

### Step 3: Choose Report Type
- Select **Flight and Block Hours** from the dropdown menu

<img src="screenshots/3_hours_select.PNG" alt="Dropdown menu selection" width="500"/>

### Step 4: Select Date Range
- Pick a range of dates 
- **Note:** Choose a span that will yield no more than the Flightmart display limit of 200 flights

<img src="screenshots/4_date_picker.PNG" alt="Date Picker" width="500"/>

### Step 5: Download Data
- Select **Download Data** from the upper-right corner of the screen

<img src="screenshots/5_summary_display.PNG" alt="Summary Display" width="600"/>

### Step 6: Configure Export Options
- Select all of the columns on the left side of the screen
- Set the **Number of Observations to Display** to **200**

<img src="screenshots/6_columns.PNG" alt="Columns" width="500"/>

### Step 7: Download as Spreadsheet
- Select **Download as Spreadsheet** in the top-left corner of the screen

<img src="screenshots/7_downloadable.PNG" alt="Download Spreadsheet" width="500"/>

### Step 8: Confirm Download
- Select **Download** from the modal dialog

<img src="screenshots/8_dowload_modal.PNG" alt="Modal" width="400"/>

### Step 9: Access Downloads
- Select **Downloads** from the toolbar at the top of the screen (down arrow)
- Select the downloaded file

<img src="screenshots/9_dropdown.PNG" alt="Safari toolbar" width="500"/>

### Step 10: Share File
- Select the **Share icon** at the top of the screen
- Share to another device using your preferred method

<img src="screenshots/10_download.png" alt="Share icon" width="400"/>

## Next Steps
After downloading your flight data, you can process it using the Logbook Formatter:
1. Run the web app: `python app.py`
2. Open http://127.0.0.1:5000 in your browser
3. Upload the downloaded CSV file
4. Select your crew position
5. Download your FAA-formatted logbook
# Python script to automate key tasks for the core of a weather, lightning and wildfire tracking mobile app
# requirements.txt
selenium>=4.34
beautifulsoup4>=4.13
webdriver-manager>=3.8
urllib3>=2.0
requests>=2.25
schedule>=1.2

# Import supporting software packages
import os
import sys
import shutil
import subprocess
import tempfile
import json
import requests
import urllib3
import schedule
import time
from datetime import datetime
from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from bs4 import BeautifulSoup

def update_pip():
    try:
        subprocess.run(["python", "-m", "pip", "install", "--upgrade", "pip"], check=True)
        print("Python has been updated to the latest version.")
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while updating pip: {e}")

# Install necessary Python libraries to support automated data extraction
def install_libraries():
    try:
        subprocess.run(["pip", "install", "selenium", "beautifulsoup4", "webdriver-manager", "urllib3"], check=True)
        print("Libraries have been installed.")
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while installing libraries: {e}")

# Fetch API data using GET request
def fetch_api_data():
    url = "https://api.github.com/users/psf/repos"
    http = urllib3.PoolManager()
    response = None # Initialize response
    try:
        response = http.request('GET', url)
        print("Fetch API Status Code:", response.status)
        if response.status == 200:
             # Parse the data and return it
        else:
             # Then make sure there's a return None or explicit return
            print(f"API request failed with status: {response.status}")
            return None
             # Ensure all paths return something consistent
    except urllib3.exceptions.MaxRetryError as e:
        print(f"Network error while fetching API data: {e}")
        return None
    except urllib3.exceptions.NewConnectionError as e:
        print(f"Connection error while fetching API data: {e}")
        return None
    finally:
        if response:
            response.release_conn() # Good practice to release connection

# Use json format to parse the data for more usable output
api_data = fetch_api_data()
print("Response Data:", json.dumps(api_data, indent=4))

  # Parse the data and return it
data = json.loads(response.data.decode('utf-8'))

# Post API data using POST request
def post_api_data():
    url = "https://httpbin.org/post"
    http = urllib3.PoolManager()
    response = None
    try:
        payload = {'Winter': 'snowing', 'Spring': 'raining', 'Summer': 'sunny', 'Fall': 'windy'}
        response = http.request('POST', url, fields=payload)
        print("Post API Status Code:", response.status)
        if response.status == 200:
            # Optionally process response
            response_data = json.loads(response.data.decode('utf-8'))
            print("Post API Response Data:")
            print(json.dumps(response_data, indent=4))
            return response_data # Return data if needed
        else:
             print(f"API post failed with status: {response.status}")
             return None
    except urllib3.exceptions.MaxRetryError as e:
         print(f"Network error while posting API data: {e}")
         return None
    except urllib3.exceptions.NewConnectionError as e:
         print(f"Connection error while posting API data: {e}")
         return None
    finally:
        if response:
            response.release_conn()

def derive_condition(hour_data):
# Derive a simple weather condition based on Stormglass API hourly data
    cloud_cover = hour_data.get('cloudCover', {}).get('sg', 0) # Default to 0 if not available
    precipitation = hour_data.get('precipitation', {}).get('sg', 0) # Default to 0 if not available
    air_temperature = hour_data.get('airTemperature', {}).get('sg', 0) # Default to 0 if not available
    if precipitation > 0.1: # Some threshold for rain/snow
        return "Rainy/Precipitating"
    elif cloud_cover > 75:
        return "Overcast"
    elif cloud_cover > 25:
        return "Partly Cloudy"
    elif air_temperature > 25: # Example threshold for warm
        return "Clear and Warm"
    else:
        return "Clear"

# Extract local weather data based on location
def get_weather_data(location_name):
    """
    Fetches current weather data for a location using the Stormglass API.

    Args:
        location_name (str): The name of the location (e.g., "Westcliffe, CO").

    Returns:
        dict: A dictionary containing weather data, or None if an error occurs.
    """
    print(f"Fetching coordinates for: {location_name}")
    geolocator = Nominatim(user_agent="thorshammer") # Be polite, identify your app
    location_geo = None
    try:
        location_geo = geolocator.geocode(location_name, timeout=10) # 10 second timeout
        if location_geo:
            lat = location_geo.latitude
            lon = location_geo.longitude
            print(f"Coordinates found: Lat={lat}, Lon={lon}")
        else:
            print(f"Error: Could not geocode location '{location_name}'.")
            return None
    except GeocoderTimedOut:
        print("Error: Geocoding service timed out.")
        return None
    except GeocoderServiceError as e:
        print(f"Error: Geocoding service error: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during geocoding: {e}")
        return None
   
    api_key = os.environ.get('STORMGLASS_API_KEY')
    if not api_key or api_key == 'YOUR_DEFAULT_KEY_HERE':
        print("Error: STORMGLASS_API_KEY environment variable not set.")
        return None

    # Define the weather parameters you want from the API
    # See Stormglass docs for available parameters: https://docs.stormglass.io/reference/weather-api#available-weather-parameters
    weather_params = [
        "airTemperature", "humidity", "pressure", "windSpeed", "windDirection",
        "precipitation", "cloudCover" # Cloud cover can help determine 'condition'
        # Add/remove params as needed based on your plan and requirements
    ]
    params_str = ",".join(weather_params)

    print(f"Querying Storm Glass API for weather at ({lat}, {lon})")
    url = "https://api.stormglass.io/v2/weather/point"
    headers = {'Authorization': api_key}
    params = {
        'lat': lat,
        'lng': lon,
        'params': params_str,
        # Optional: Add 'start' and 'end' if you need a specific time range
        # 'start': datetime.utcnow().timestamp(), # Example: current time
        # 'end': datetime.utcnow().timestamp(),   # Example: current time
    }
   
    try:
        response = requests.get(url, params=params, headers=headers, timeout=20) # 20 second timeout for API call
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        api_data = response.json()
        print("Storm Glass API response received.")
        # print(json.dumps(api_data, indent=2)) # Uncomment to see the full response for debugging

        # Process the response - Stormglass usually returns hourly data.
        # We'll take the data from the first hour returned as "current".
        if 'hours' in api_data and len(api_data['hours']) > 0:
            current_hour_data = api_data['hours'][0]

        # Map API data to your desired output format
        # Use .get() to avoid errors if a parameter is missing (e.g., due to API plan limits)
        weather_output = {
            'location': location_name, # Use the original name
            'latitude': lat,
            'longitude': lon,
            'timestamp': current_hour_data.get('time'),
            'temperature': f"{current_hour_data.get('airTemperature', {}).get('sg', 'N/A')}°C", # Assumes Celsius from API
                'humidity': f"{current_hour_data.get('humidity', {}).get('sg', 'N/A')}%",
                'barometer': f"{current_hour_data.get('pressure', {}).get('sg', 'N/A')} hPa", # units from API
                'wind': f"{current_hour_data.get('windSpeed', {}).get('sg', 'N/A')} m/s", # units from API
                # Consider adding wind direction: current_hour_data.get('windDirection', {}).get('sg', 'N/A')
                'precipitation': f"{current_hour_data.get('precipitation', {}).get('sg', 'N/A')} kg/m²/h", # units from API
                'cloud_cover': f"{current_hour_data.get('cloudCover', {}).get('sg', 'N/A')}%",
                # Derive a simple 'condition' - you might want more sophisticated logic
                'condition': derive_condition(current_hour_data),
            }
        print("Successfully processed weather data from API.")
        return weather_output
        print("Error: Unexpected API response format - 'hours' data missing or empty.")
        print("API Meta:", api_data.get('meta')) # Print metadata which might contain errors
        return None
    except requests.exceptions.Timeout:
        print("Error: Request to Storm Glass API timed out.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching weather data from Storm Glass: {e}")
        if hasattr(e, 'response') and e.response is not None:
             print(f"API Response Status: {e.response.status_code}")
             try:
                 # Try to parse error details if the response is JSON
                 error_details = e.response.json()
                 print(f"API Error Details: {json.dumps(error_details)}")
             except json.JSONDecodeError:
                 # Otherwise print raw text
                 print(f"API Response Body: {e.response.text}")
        return None
    except json.JSONDecodeError:
        print("Error: Could not decode JSON response from Storm Glass API.")
        if response: print(f"Raw Response: {response.text}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during API data processing: {e}")
        return None
    finally:
        driver.quit()

 # Extract lightning data using Storm Glass API      
def get_lightning_strikes(api_key, bbox):
    url = f"https://api.stormglass.io/v2/weather/points?lat{bbox}"
    headers = {'Authorization': api_key}
    response = requests.get(url, headers=headers)
    return response.json()

class StormglassClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.last_call = 0
        self.rate_limit = 43200  # 43200 sec delay

# Implement automatic delay between requests
    def get_weather(self, params):
        elapsed = time.time() - self.last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        
        response = requests.get(
            "https://api.stormglass.io/v2/weather/point",
            headers={"Authorization": self.api_key},
            params=params
        )
        
        self.last_call = time.time()
        return response

# Use tracking to avoid overages
class UsageTracker:
    def __init__(self):
        self.daily_count = 0
        self.last_reset = time.time()
    
    def increment(self):
        if time.time() - self.last_reset > 86400:  # 24h reset
            self.daily_count = 0
            self.last_reset = time.time()
        self.daily_count += 1
        
        if self.daily_count > 45:  # Leave 5 req buffer
            raise Exception("Approaching daily limit")

# Global list to store all weather data collected during script runtime
# Initialize it before any functions that will add to the file
all_collected_weather_data = []

# Update get_weather_data function to append to this list

# Collect extracted weather data
if __name__ == "__main__":
    update_pip()
    install_libraries()
    fetch_api_data()
    post_api_data()
    # Use proper location handling
    location = input("Enter location (city, state/country): ")
    weather = get_weather_data(location)
    print(json.dumps(weather, indent=2))

if all_collected_weather_data: # Only append if the data fetch was successful
        print(json.dumps(current_weather_report, indent=2))
        all_collected_weather_data.append(current_weather_report)
else:
    print(f"Failed to get weather data for {location}.")

# Perform a daily backup script for all weather data captured that day
# The save_daily_backup function will now receive the global list
def save_daily_backup(data_to_save): # Renamed 'data' to 'data_to_save' for clarity
    """
    Save the weather data to a file with today's date as the filename.

    Args:
        data_to_save (list): The list of weather data reports to be saved.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"weather_backup
    
    try:
        with open(filename, 'w') as file:
            json.dump(data, file, indent=4)
        print(f"Backup saved for {today} at {filename}.")
    except Exception as e:
        print(f"An error occurred while saving the backup: {e}")

# Schedule the backup task to run daily at 11:59 PM
schedule.every().day.at("23:59").do(save_daily_backup, data=weather_data)

print("Scheduled daily backup at 11:59 PM.")

# Keep the script running to execute scheduled tasks
while True:
    schedule.run_pending()
    time.sleep(1)

# Python script to automate key tasks for the core of a weather, lightning and wildfire tracking mobile app
# requirements.txt
selenium>=4.34
beautifulsoup4>=4.13
webdriver-manager>=3.8
urllib3>=2.0
requests>=2.25
schedule>=1.2
python-dotenv # Added for API key management

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
from dotenv import load_dotenv # Import load_dotenv

# Load environment variables from .env file
load_dotenv()

def update_pip():
    try:
        subprocess.run(["python", "-m", "pip", "install", "--upgrade", "pip"], check=True)
        print("Python has been updated to the latest version.")
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while updating pip: {e}")

# Install necessary Python libraries to support automated data extraction
def install_libraries():
    try:
        subprocess.run(["pip", "install", "selenium", "beautifulsoup4", "webdriver-manager", "urllib3", "python-dotenv"], check=True)
        print("Libraries have been installed.")
    except subprocess.CalledCalledProcessError as e:
        print(f"An error occurred while installing libraries: {e}")

# Fetch API data using GET request (General purpose, not specific to weather)
def fetch_api_data():
    url = "https://api.github.com/users/psf/repos"
    http = urllib3.PoolManager()
    response = None # Initialize response
    try:
        response = http.request('GET', url)
        print("Fetch API Status Code:", response.status)
        if response.status == 200:
            data = json.loads(response.data.decode('utf-8'))
            return data
        else:
            print(f"API request failed with status: {response.status}")
            return None
    except urllib3.exceptions.MaxRetryError as e:
        print(f"Network error while fetching API data: {e}")
        return None
    except urllib3.exceptions.NewConnectionError as e:
        print(f"Connection error while fetching API data: {e}")
        return None
    finally:
        if response:
            response.release_conn() # Good practice to release connection

# Post API data using POST request (General purpose, not specific to weather)
def post_api_data():
    url = "https://httpbin.org/post"
    http = urllib3.PoolManager()
    response = None
    try:
        payload = {'Winter': 'snowing', 'Spring': 'raining', 'Summer': 'sunny', 'Fall': 'windy'}
        response = http.request('POST', url, fields=payload)
        print("Post API Status Code:", response.status)
        if response.status == 200:
            response_data = json.loads(response.data.decode('utf-8'))
            print("Post API Response Data:")
            print(json.dumps(response_data, indent=4))
            return response_data
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

def derive_condition(weather_data):
    # Derive a simple weather condition based on Weatherbit Current Weather API data
    # Weatherbit provides a 'weather' object with 'description'
    description = weather_data.get('weather', {}).get('description', '').lower()
    temp_c = weather_data.get('temp') # Temperature in Celsius
    precip = weather_data.get('precip') # Precipitation rate in mm/hr
    clouds = weather_data.get('clouds') # Cloud coverage in %

    if 'rain' in description or 'drizzle' in description or precip > 0.1:
        return "Rainy/Precipitating"
    elif 'snow' in description:
        return "Snowy"
    elif 'cloud' in description or clouds > 75:
        return "Overcast"
    elif clouds > 25:
        return "Partly Cloudy"
    elif temp_c is not None and temp_c > 25:
        return "Clear and Warm"
    elif temp_c is not None and temp_c < 0:
        return "Clear and Cold"
    else:
        return "Clear"

# Extract local weather data based on location using Weatherbit Current Weather API
def get_weather_data(location_name):
    """
    Fetches current weather data for a location using the Weatherbit Current Weather API.

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
   
    weatherbit_api_key = os.environ.get('WEATHERBIT_API_KEY')
    if not weatherbit_api_key or weatherbit_api_key == 'YOUR_DEFAULT_KEY_HERE':
        print("Error: WEATHERBIT_API_KEY environment variable not set or is default.")
        print("Please set the WEATHERBIT_API_KEY environment variable with your actual key.")
        return None

    print(f"Querying Weatherbit Current Weather API for weather at ({lat}, {lon})")
    url = "https://api.weatherbit.io/v2.0/current"
    params = {
        'lat': lat,
        'lon': lon,
        'key': weatherbit_api_key,
        'units': 'M' # Metric units (Celsius, m/s, mm/hr)
    }
   
    try:
        response = requests.get(url, params=params, timeout=20) # 20 second timeout for API call
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        api_data = response.json()
        print("Weatherbit Current Weather API response received.")
        # print(json.dumps(api_data, indent=2)) # Uncomment to see the full response for debugging

        if 'data' in api_data and len(api_data['data']) > 0:
            current_weather_data = api_data['data'][0]

            weather_output = {
                'location': location_name,
                'latitude': lat,
                'longitude': lon,
                'timestamp': current_weather_data.get('datetime'),
                'temperature': f"{current_weather_data.get('temp', 'N/A')}°C",
                'humidity': f"{current_weather_data.get('rh', 'N/A')}%", # Relative humidity
                'barometer': f"{current_weather_data.get('pres', 'N/A')} mb", # Atmospheric pressure in millibars
                'wind_speed': f"{current_weather_data.get('wind_spd', 'N/A')} m/s", # Wind speed
                'wind_direction': f"{current_weather_data.get('wind_cdir_full', 'N/A')}", # Wind direction in full text
                'precipitation_rate': f"{current_weather_data.get('precip', 'N/A')} mm/hr", # Precipitation rate
                'cloud_cover': f"{current_weather_data.get('clouds', 'N/A')}%",
                'condition_code': current_weather_data.get('weather', {}).get('code'), # Weather condition code
                'condition_description': current_weather_data.get('weather', {}).get('description'), # Weather condition description
                'condition': derive_condition(current_weather_data), # Derived condition
            }
            print("Successfully processed weather data from API.")
            return weather_output
        else:
            print("Error: Unexpected API response format - 'data' missing or empty.")
            return None
    except requests.exceptions.Timeout:
        print("Error: Request to Weatherbit API timed out.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching weather data from Weatherbit: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"API Response Status: {e.response.status_code}")
            try:
                error_details = e.response.json()
                print(f"API Error Details: {json.dumps(error_details)}")
            except json.JSONDecodeError:
                print(f"API Response Body: {e.response.text}")
        return None
    except json.JSONDecodeError:
        print("Error: Could not decode JSON response from Weatherbit API.")
        if response: print(f"Raw Response: {response.text}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during API data processing: {e}")
        return None

# Extract lightning data using Weatherbit Current Lightning API (commented out as per request)
def get_lightning_strikes(api_key, lat, lon):
    """
    Fetches lightning strike data for a location using the Weatherbit Current Lightning API.
    NOTE: This API requires a paid Weatherbit tier.

    Args:
        api_key (str): Your Weatherbit API key.
        lat (float): Latitude of the location.
        lon (float): Longitude of the location.

    Returns:
        dict: A dictionary containing lightning data, or None if an error occurs.
    """
    # print(f"Querying Weatherbit Current Lightning API for lightning at ({lat}, {lon})")
    # url = "https://api.weatherbit.io/v2.0/lightning"
    # params = {
    #     'lat': lat,
    #     'lon': lon,
    #     'key': api_key
    # }
    # try:
    #     response = requests.get(url, params=params, timeout=20)
    #     response.raise_for_status()
    #     api_data = response.json()
    #     print("Weatherbit Current Lightning API response received.")
    #     # print(json.dumps(api_data, indent=2))
    #     return api_data
    # except requests.exceptions.Timeout:
    #     print("Error: Request to Weatherbit Lightning API timed out.")
    #     return None
    # except requests.exceptions.RequestException as e:
    #     print(f"Error fetching lightning data from Weatherbit: {e}")
    #     if hasattr(e, 'response') and e.response is not None:
    #         print(f"API Response Status: {e.response.status_code}")
    #         try:
    #             error_details = e.response.json()
    #             print(f"API Error Details: {json.dumps(error_details)}")
    #         except json.JSONDecodeError:
    #             print(f"API Response Body: {e.response.text}")
    #     return None
    # except json.JSONDecodeError:
    #     print("Error: Could not decode JSON response from Weatherbit Lightning API.")
    #     if response: print(f"Raw Response: {response.text}")
    #     return None
    # except Exception as e:
    #     print(f"An unexpected error occurred during lightning API data processing: {e}")
    return None # Always return None since it's commented out

# No longer needed as Weatherbit handles rate limiting and usage tracking differently
# class StormglassClient:
#     def __init__(self, api_key):
#         self.api_key = api_key
#         self.last_call = 0
#         self.rate_limit = 43200  # 43200 sec delay

#     def get_weather(self, params):
#         elapsed = time.time() - self.last_call
#         if elapsed < self.rate_limit:
#             time.sleep(self.rate_limit - elapsed)
        
#         response = requests.get(
#             "https://api.stormglass.io/v2/weather/point",
#             headers={"Authorization": self.api_key},
#             params=params
#         )
        
#         self.last_call = time.time()
#         return response

# class UsageTracker:
#     def __init__(self):
#         self.daily_count = 0
#         self.last_reset = time.time()
    
#     def increment(self):
#         if time.time() - self.last_reset > 86400:  # 24h reset
#             self.daily_count = 0
#             self.last_reset = time.time()
#         self.daily_count += 1
        
#         if self.daily_count > 45:  # Leave 5 req buffer
#             raise Exception("Approaching daily limit")

# Global list to store all weather data collected during script runtime
all_collected_weather_data = []

# Perform a daily backup script for all weather data captured that day
def save_daily_backup(data_to_save): # Renamed 'data' to 'data_to_save' for clarity
    """
    Save the weather data to a file with today's date as the filename.

    Args:
        data_to_save (list): The list of weather data reports to be saved.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"weather_backup_{today}.json" # Corrected filename format
    
    try:
        with open(filename, 'w') as file:
            json.dump(data_to_save, file, indent=4) # Use data_to_save
        print(f"Backup saved for {today} at {filename}.")
    except Exception as e:
        print(f"An error occurred while saving the backup: {e}")

# Collect extracted weather data
if __name__ == "__main__":
    update_pip()
    install_libraries()
    
    # These general API fetch/post functions are kept but not directly related to weatherbit
    api_data = fetch_api_data()
    print("Response Data (General API Fetch):", json.dumps(api_data, indent=4))
    post_api_data()

    # Use proper location handling
    location = input("Enter location (city, state/country): ")
    weather = get_weather_data(location)
    
    if weather: # Only append if the data fetch was successful
        print(json.dumps(weather, indent=2))
        all_collected_weather_data.append(weather)
        # Example of how you might call the lightning API if enabled
        # weatherbit_api_key = os.environ.get('WEATHERBIT_API_KEY')
        # if weatherbit_api_key:
        #     lightning_data = get_lightning_strikes(weatherbit_api_key, weather['latitude'], weather['longitude'])
        #     if lightning_data:
        #         print("Lightning Data:", json.dumps(lightning_data, indent=2))
        #     else:
        #         print(f"Failed to get lightning data for {location}.")
    else:
        print(f"Failed to get weather data for {location}.")

    # Schedule the backup task to run daily at 11:59 PM
    # Ensure all_collected_weather_data is passed correctly
    schedule.every().day.at("23:59").do(save_daily_backup, data_to_save=all_collected_weather_data)

    print("Scheduled daily backup at 11:59 PM.")

    # Keep the script running to execute scheduled tasks
    while True:
        schedule.run_pending()
        time.sleep(1)

"""
Weather tool for the hike planner agent.

Uses Open-Meteo's free Forecast API — no API key required. Note: daily
forecasts are only available up to 16 days out from today; requesting a
date range beyond that will return an error, not a silent empty result.
"""

import sys
from datetime import date, timedelta

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_FORECAST_DAYS = 16


def get_weather_forecast(lat: float, lon: float, start_date: str, end_date: str) -> dict:
    """Fetch a daily weather forecast for a date range.

    Args:
        lat: Latitude of the location.
        lon: Longitude of the location.
        start_date: Start date, format YYYY-MM-DD.
        end_date: End date, format YYYY-MM-DD.

    Returns:
        A dict with a list of daily forecasts: date, temp min/max (°C),
        precipitation probability (%), and max wind speed (km/h).
    """
    latest_allowed = date.today() + timedelta(days=MAX_FORECAST_DAYS)
    requested_end = date.fromisoformat(end_date)
    if requested_end > latest_allowed:
        raise ValueError(
            f"end_date {end_date} is beyond Open-Meteo's {MAX_FORECAST_DAYS}-day "
            f"forecast limit (latest available: {latest_allowed.isoformat()})"
        )

    response = requests.get(
        FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "daily": [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "windspeed_10m_max",
            ],
            "timezone": "auto",
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    daily = payload["daily"]

    forecasts = []
    for i, day in enumerate(daily["time"]):
        forecasts.append({
            "date": day,
            "temp_min": daily["temperature_2m_min"][i],
            "temp_max": daily["temperature_2m_max"][i],
            "precipitation_probability": daily["precipitation_probability_max"][i],
            "wind_speed_max": daily["windspeed_10m_max"][i],
        })

    return {"forecasts": forecasts}


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("Usage: python3 tools/weather.py <lat> <lon> <start_date YYYY-MM-DD> <end_date YYYY-MM-DD>")
        sys.exit(1)

    lat, lon = float(sys.argv[1]), float(sys.argv[2])
    start_date, end_date = sys.argv[3], sys.argv[4]

    result = get_weather_forecast(lat, lon, start_date, end_date)
    for day in result["forecasts"]:
        print(
            f"{day['date']}: {day['temp_min']}–{day['temp_max']}°C, "
            f"{day['precipitation_probability']}% precip, "
            f"wind {day['wind_speed_max']} km/h"
        )
import json
import os
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()

# Progress's SDK patches the anthropic client at import time, so instrument()
# must run before `import anthropic` anywhere in the process.
from progress.observability import Observability

Observability.instrument(
    app_name=os.getenv("OBSERVABILITY_APP_NAME"),
    api_key=os.getenv("OBSERVABILITY_API_KEY"),
)

import anthropic

from tools.routing import get_hiking_route
from tools.weather import get_weather_forecast

MODEL = "claude-sonnet-5"
MAX_TOKENS = 2048

TOOLS = [
    {
        "name": "get_hiking_route",
        "description": (
            "Geocode an ordered sequence of place names and return the "
            "foot-hiking route through them: resolved coordinates, distance "
            "(meters), duration (seconds), and an elevation profile (min/max "
            "elevation and total ascent/descent, all in meters)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "waypoints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "description": (
                        "Ordered place names to route through, starting point "
                        "first and destination last."
                    ),
                },
            },
            "required": ["waypoints"],
        },
    },
    {
        "name": "get_weather_forecast",
        "description": (
            "Fetch a daily weather forecast (temperature min/max, precipitation "
            "probability, max wind speed) for a location and date range. Only "
            "forecasts up to 16 days out are available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "description": "Latitude"},
                "lon": {"type": "number", "description": "Longitude"},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["lat", "lon", "start_date", "end_date"],
        },
    },
]

TOOL_FUNCTIONS = {
    "get_hiking_route": lambda waypoints: get_hiking_route(waypoints),
    "get_weather_forecast": lambda lat, lon, start_date, end_date: get_weather_forecast(
        lat, lon, start_date, end_date
    ),
}

SYSTEM_PROMPT = f"""You are a hike-planning assistant. Today's date is {date.today().isoformat()}.

When planning a hike:
1. Always call get_hiking_route first to establish the route. Pass
   waypoints as an ordered list of place names: the starting point first,
   any intermediate stops next in the order they're visited, and the
   destination last. A simple point-to-point hike (no intermediate stops)
   should still be passed as a 2-item list — e.g. [start, finish].
2. Then use the first entry in waypoint_coords (lat/lon) from that result
   to call get_weather_forecast for the trip's date range. Always check
   weather against the route's actual resolved location — never assume or
   guess coordinates for the forecast.
3. Once you have both the route and the forecast, give the user a concise
   final summary covering: total distance, estimated duration, elevation
   profile (min/max elevation, total ascent/descent), and the day-by-day
   weather forecast. Call out any weather conditions that could affect the
   hike (heavy rain, high wind, extreme temperatures).

If a tool call fails (e.g. an unknown place name or a date outside the
forecast window), explain the failure to the user clearly and, if possible,
suggest how they might rephrase their request.
"""


def execute_tool(name: str, tool_input: dict) -> str:
    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        return json.dumps({"error": f"Unknown tool: {name}"})

    try:
        result = func(**tool_input)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def run_agent(user_prompt: str) -> None:
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": user_prompt}]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            for block in response.content:
                if block.type == "text":
                    print(block.text)
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            print(f"[calling {block.name}({block.input})]")
            result_text = execute_tool(block.name, block.input)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                }
            )

        messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    prompt = (
        " ".join(sys.argv[1:])
        or "plan next weekend's hike from Bukovets hut via Aglikina Meadow finishing at Luda Kamchiya Spring"
    )
    run_agent(prompt)

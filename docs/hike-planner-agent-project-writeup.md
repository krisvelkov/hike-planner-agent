# Hike Planner Agent — Project Writeup

*A retrospective on designing, building, and instrumenting this agent — motivation, build process, obstacles, and outcome.*

---

## Why I Built This

I'm a Senior Product Manager targeting AI-native and enterprise AI platform roles. My background is strong on the platform/experimentation side — 0-to-1 discovery, robotics automation — but I hadn't built or instrumented an AI agent myself, hands-on, from scratch.

I don't write code myself. I direct Claude Code to implement each piece, read and reason about the code it produces well enough to spot bugs and design flaws, and debug by testing behavior — running commands, inspecting output, forming hypotheses — rather than editing syntax directly. Rather than treat that as a limitation, I used this project as the reason to learn agent architecture, tool-use design, third-party API integration, and observability instrumentation hands-on, through that directing-and-debugging process — not just reading about it.

I chose a hiking trip planner because it's genuinely useful to me. I hike regularly in Rila, Pirin, the Rhodopes, and Stara Planina, and a tool that combines real route/elevation data with weather forecasting for a specific trip is something I'd actually use, not a toy example. Using a domain I know well also turned out to be an asset during the build — my own knowledge of these mountains is what caught several data-quality bugs that would otherwise have shipped silently.

## What I Set Out to Build

**v1 scope, deliberately narrowed:**
1. Route definition — point A to point B, or a named trailhead, resolved via a routing API
2. Elevation profile — for assessing trip difficulty (min/max elevation, total ascent/descent)
3. Weather forecast — for the trip's date range

I explicitly descoped open-ended "somewhere in this area" route discovery to a v2 stretch goal — that requires trail-discovery queries against OpenStreetMap data, a meaningfully harder problem than routing between two named points, and I wanted a working core loop before taking that on.

**Budget constraint:** I set out to build this on a tight budget. I corrected two false assumptions early — that I'd need a paid Azure OpenAI resource (I didn't; the observability platform supports Anthropic and OpenAI directly) and that the observability platform required a $29/month plan (it has a genuine free tier sufficient for a portfolio project). Total spend on the whole project: roughly $5 in Anthropic API credits, $0 recurring.

**Tech stack:** Python (chosen over the vendor's official .NET starter kit — Python code is easier for me to read and reason about alongside Claude Code than C#, and it let me avoid the Azure dependency entirely), Anthropic's Claude API for the agent, GraphHopper for routing/elevation/geocoding, Open-Meteo for weather, Progress AI Observability (free tier) for tracing.

## Obstacles and How I Handled Them

### Environment setup
Early friction was mundane but real: an outdated system Python (below the 3.10 minimum required by my dependencies), a `pip`/`pip3` PATH confusion, and — the trickiest one — a virtual environment that silently kept resolving to the wrong Python after I renamed the project folder. Venvs bake an absolute path into their activation script at creation time; renaming the folder doesn't update it, and the failure mode (prompt shows `(venv)` correctly, but `which python3` still points to the system install) is genuinely non-obvious. I diagnosed it by checking `$PATH` directly rather than trusting the shell prompt, found the stale path, and rebuilt the venv cleanly from the renamed location.

### A live third-party outage, mid-build
Partway through building the routing tool, I discovered OpenRouteService (my original provider) was mid-migration to a new domain, with the old one scheduled to shut down in a week. After directing Claude Code to update the routing calls to the new URLs, I hit a persistent 403 error — "Access to this API has been disallowed" — on *both* the old and new hosts, despite a valid, unexpired key.

I didn't guess. I isolated variables systematically: confirmed the same error via raw `curl` (ruling out a Python/requests-library issue), checked my dashboard and found real quota consumption (1,992/2,000 remaining — proving the key was valid and reaching their API, not simply dead), and found a matching, unresolved report on the vendor's own community forum describing identical symptoms. That confirmed it was an account-side bug on their end, not my code. I filed a support ticket with the specific diagnostic evidence, and rather than block my build on a third party's unresolved issue, directed Claude Code to migrate the routing/elevation logic to GraphHopper's equivalent free-tier API — same shape of problem, different provider, working within the hour.

### A systemic geocoding accuracy problem, not a one-off bug
Testing the routing tool against real Bulgarian hut and peak names, I found the geocoder repeatedly mismatching results: "Malyovitsa Hut" resolved to Malyovitsa *peak*; "Ivan Vazov Hut" resolved to a different real hut, "Ivan Rilski"; "Musala Peak" resolved to a same-named peak in Pakistan; a Bulgarian-language query for Musala resolved to Vihren, an entirely different Bulgarian summit. This happened across *two independent geocoding providers* (Pelias/ORS and GraphHopper), and a location-bias parameter I tried as a fix helped with fuzzy ambiguity but didn't solve exact-name collisions.

Because I know these mountains personally, I could catch these errors by inspection rather than trusting the API's confidence — a case where domain expertise mattered more than the tool. Rather than keep tuning queries against an unreliable general-purpose geocoder, I made a deliberate engineering call: build a small curated lookup table (`KNOWN_LOCATIONS`) as the trusted source of truth for places I actually route between, with the live geocoding API as a best-effort fallback only for anything outside it. This is a legitimate production pattern (a bounded gazetteer for a narrow domain) rather than a workaround, and I made sure the reasoning was documented directly in the code and in the README.

### Debugging discipline
I adopted a strict habit of testing each tool standalone — via direct CLI calls with real coordinates — before ever wiring it into the agent loop. This caught several bugs that would otherwise have silently reached the LLM as ground truth: a no-op `pass` stub left in a test block, an unsaved file, a coordinate-order mismatch (`lat, lon` vs `lon, lat`) between a lookup table and an API call, and dead code left over from the ORS-to-GraphHopper migration that threw an opaque error only once a real user query happened to hit it.

### Extending scope mid-build
Once the core two-point routing worked, I recognized a real use case — a three-stop weekend route — that the original `start`/`finish` function signature couldn't support. I directed Claude Code to refactor `get_hiking_route` to accept an ordered list of waypoints instead and update the agent's tool schema and system prompt to match, then tested it standalone before touching the agent loop, keeping the same "verify each layer independently" discipline throughout.

### Observability instrumentation
Progress AI Observability's SDK requires `Observability.instrument()` to be called *before* importing the `anthropic` package, since it patches the client at import time — an easy thing to get wrong silently. I made sure Claude Code implemented this ordering requirement correctly in `main.py` and verified traces were actually landing in the dashboard (tool calls, latency, token usage) rather than assuming instrumentation was working just because the code ran without errors.

### Security and repo hygiene
I kept `.env` out of version control from the first commit (verified via `git ls-files`, not just assumed), grepped the full codebase for accidentally hardcoded keys before going public, and regenerated my GraphHopper API key as a precaution after it had appeared in plaintext during debugging sessions — even though that exposure was in chat history, not the repo itself. When GitHub rejected password authentication on my first public push (a policy change I hadn't hit before), I set up a Personal Access Token rather than working around it insecurely.

## Outcome

The agent works end-to-end. Given a natural-language prompt like *"plan next weekend's hike from Bukovets Hut via Aglikina Meadow finishing at Luda Kamchiya Spring,"* it correctly:
1. Resolves all waypoints in order and calculates the multi-stop route (distance, duration, elevation profile)
2. Checks the weather forecast against the route's actual starting coordinates for the correct date range
3. Produces a coherent trip summary with practical recommendations (e.g., flagging a hot-weather day and suggesting an early start or a two-day split for a 32km route with 1,350m of elevation gain)

Every run is fully traced in the Progress Observability dashboard — tool calls, latency, and token usage are all visible and inspectable.

**v1 scope is complete:** route definition (including multi-waypoint), elevation-based difficulty assessment, and weather forecasting, all instrumented, all built and debugged for roughly $5 total spend.

## Skills This Demonstrates

- **Agent architecture and tool-use design** — directing the design of tool schemas, sequencing logic (route before weather, using resolved coordinates to chain calls), and graceful error handling that routes failures back through the LLM rather than crashing, working through Claude Code
- **Reading and reasoning about code without writing it** — understanding what generated code does well enough to spot bugs, mismatched conventions, and design flaws, and to give Claude Code precise, targeted fix instructions rather than vague ones
- **Third-party API integration under real-world conditions** — including diagnosing and working around a live vendor outage without blocking progress
- **Data quality judgment** — catching a systemic, cross-provider accuracy problem through domain expertise, and making a deliberate, documented tradeoff rather than over-engineering a fix
- **Debugging methodology** — isolating variables (host vs. key vs. quota), testing components standalone before integration, and not trusting surface-level signals (a shell prompt, a lack of error output) without verification
- **Observability instrumentation** — understanding and correctly implementing SDK-level requirements like import ordering
- **Scope management** — deliberately narrowing v1, then making a considered call to extend it (multi-waypoint) only once the core loop was proven
- **Security-conscious engineering habits** — credential hygiene, verifying rather than assuming `.gitignore` worked, key rotation after incidental exposure

## What Stood Out in Retrospect

- The ORS outage diagnosis was the clearest example of systematic debugging under ambiguity — isolating whether the problem was my code, my key, or the vendor's service, rather than guessing, and not letting a third-party blocker stall the build.
- The geocoding accuracy finding was a reminder that domain expertise catches errors a purely technical review can miss — "the model sounds confident" isn't the same as "the model is right," and that gap matters most in exactly the kind of AI product work I want to do next.
- The scope decisions (the v1 boundary, the curated location table vs. pure API reliance) were the most transferable part of the process — pragmatic tradeoffs under real constraints, not just following a tutorial.

## Extending the Build: LLM-as-a-Judge Evaluations

Once the core agent was working end-to-end, I added a second layer to the observability story: automated quality evaluation, not just tracing. This tests a genuinely different skill — defining what "good output" means for an AI agent in measurable terms, not just building the agent itself.

**A provider dead-end, and the habit that avoided wasted time.** My original plan used an OpenRouter key to run Gemini as the judge model — but that key had since been deactivated. Rather than spend time obtaining a new OpenRouter key, I checked whether Progress AI Observability's evaluation feature supported Anthropic directly, since I already had a working, funded Anthropic key from the agent build itself. The platform's own marketing page confirmed evaluations work with "Azure, OpenAI, Anthropic and other major providers" — but given that same class of page had already misled me once earlier in this project (the `pip install progress-observability` command that didn't match what was actually on PyPI), I didn't take the claim at face value. I checked the actual dashboard's provider dropdown directly before committing to the approach, confirmed Anthropic was genuinely selectable, and used `claude-haiku-4-5-20251001` as the judge model — cheap enough to run repeatedly against test traces without denting the ~$5 total project budget.

**Designing the two eval tasks deliberately, not from a generic template.** Rather than use an out-of-the-box "relevance" template, I picked two evaluations targeted at where this specific agent's real risk lives:

1. **Trip Summary Groundedness** — does the agent's natural-language summary accurately reflect the raw numbers returned by the routing and weather tools, or does it drift, transpose, or invent figures? This is the highest-leverage check because the agent's entire value is trustworthy numbers for a real trip decision, and a user reading the prose summary has no way to independently verify it against the underlying data themselves.
2. **Weather Risk Flagging** — given the forecast data, does the summary proportionately call out real hazards (high wind, high precipitation probability, heat combined with significant elevation gain, cold exposure) against explicit thresholds? This targets the part of the output with actual physical-safety stakes, not just informational accuracy.

I deliberately did *not* build a third eval for tool-call sequencing (route-before-weather, correct coordinate chaining) — that's a deterministic property already verifiable by reading the trace directly, and using an LLM judge for something a simple assertion could check would be a wasteful use of both budget and design effort. Knowing when *not* to reach for LLM-as-a-judge was as much a part of this design work as building the two evals that mattered.

**Prompt engineering for the evaluator itself.** The platform's prompt template exposes `{{variable}}` placeholders, but I initially wrote judge instructions assuming access to the raw tool call data without confirming the template actually exposed it — the example the platform provided only showed `{{input}}`/`{{output}}`. I corrected course by explicitly checking what variables the UI's prompt field actually offered before finalizing the prompts, rather than assuming. I also caught and fixed a mismatch between the evaluator's qualitative instructions (framed around a grounded/ungrounded distinction) and an initial scoring rubric (framed around counting errors) — rewriting the score range to use the same conceptual framing as the evaluator prompt, so the judge isn't asked to reason in two different frameworks within one task.

**Results.** Running the Trip Summary Groundedness evaluator against real traces produced genuinely discriminating output: across six evaluated spans, scores included both 1s and 5s, not a flat default — meaning the judge is actually distinguishing grounded from ungrounded responses rather than rubber-stamping every run. One passing trace's explanation confirmed the evaluator checking specific figures (distance rounding, elevation totals, per-day forecast pairing) against the tool data individually, rather than giving a generic pass. The Weather Risk Flagging evaluator followed the same design and setup process.

This closes the loop on the original project goal: not just building an agent, but demonstrating the ability to define, instrument, and validate quality evaluation for one — the specific gap I set out to close at the start of this project.

## What's Next (v2)

- Open-area route discovery ("somewhere in Pirin, 3 days") via OpenStreetMap trail-relation queries — the capability explicitly descoped from v1
- A proper geocoding fallback beyond `KNOWN_LOCATIONS` for arbitrary places
- Possibly extending weather checks across all waypoints on multi-day routes, not just the starting point

---
*Repository: github.com/krisvelkov/hike-planner-agent*

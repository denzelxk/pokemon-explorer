# Pokemon Explorer 

Normalizes data from [PokeAPI](https://pokeapi.co) into a SQLite database and explores it with an interactive Streamlit dashboard.

## Files

- `pokeapi_schema.sql` — normalized SQLite schema (18 tables, 3NF, FK constraints)
- `etl_pokeapi.py` — ETL script that fetches from PokeAPI and loads the DB
- `dashboard.py` — Streamlit app for interactive analysis
- `Dockerfile`, `entrypoint.sh`, `requirements.txt`, `.dockerignore` — container setup

## Setup

### Option A: Docker (recommended)

```bash
docker build -t pokeapi-dashboard .
docker run -p 8501:8501 -v pokeapi-data:/app/data pokeapi-dashboard
```

Then open http://localhost:8501.

On first run, the container builds the database before starting the dashboard — a full pull (~1300+ Pokemon) can take several minutes. The `-v pokeapi-data:/app/data` volume persists it, so subsequent `docker run`s (even in a brand new container) skip straight to the dashboard instead of re-pulling. Mount the volume at `/app/data`, not `/app` — the latter would hide the application code that was copied into the image at build time.

Environment variables (pass with `-e`) control the build:

| Variable | Default | Purpose |
|---|---|---|
| `POKEAPI_DB` | `data/pokeapi.db` | Database path inside the container (keep it under `data/` if using the volume above) |
| `POKEAPI_LIMIT` | unset (full pull) | Limit records per resource, e.g. `151` for a fast Gen 1 test run |
| `POKEAPI_WORKERS` | `8` | Thread pool size for concurrent PokeAPI fetches |

```bash
# fast Gen 1-only test run
docker run -p 8501:8501 -e POKEAPI_LIMIT=151 -v pokeapi-data:/app/data pokeapi-dashboard
```

### Option B: Local Python (uv)

```bash
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
```

## Usage (local Python)

**1. Build the database**

```bash
.venv/bin/python etl_pokeapi.py --db pokeapi.db --limit 151   # Gen 1 only, fast test run
.venv/bin/python etl_pokeapi.py --db pokeapi.db                # full dataset (all ~1300+ Pokemon)
.venv/bin/python etl_pokeapi.py --db pokeapi.db --workers 16   # raise concurrency for a faster full pull
```

**2. Launch the dashboard**

```bash
.venv/bin/streamlit run dashboard.py -- --db=pokeapi.db
```

## Schema Overview

- **Lookups**: `generations`, `types`, `stats`, `abilities`, `damage_classes`, `egg_groups`, `colors`, `shapes`, `evolution_chains`
- **Core entities**: `pokemon_species` (Pokédex data, including `evolves_from_species_id`) → `pokemon` (per-form data, 1:many, including sprite URLs)
- **Junctions**: `pokemon_types`, `pokemon_abilities`, `pokemon_stats`, `pokemon_moves`, `species_egg_groups`
- **Type effectiveness**: `type_damage_relations` flattens damage multipliers into a queryable edge table

## Dashboard Features

Sidebar filters (generation, type, legendary status, name search, default-forms-only) narrow the Pokémon table used in the Overview, Base Stats, Moves, and Species Explorer tabs. Type Analysis, Team Builder, and Guess That Pokemon work over the full dataset regardless of the sidebar, since they're built around comparisons (types, a custom team, a random target) that a filtered subset would only get in the way of.

- **Overview** — filtered Pokémon table, base experience distribution, average-stat-total and legendary/mythical trends by generation
- **Base Stats** — stat comparison bar chart, stat correlation heatmap, stat-total distribution by type or generation
- **Type Analysis** — Pokémon count by type, type distribution treemap by generation, single-type damage lookup, dual-type defensive calculator
- **Moves** — filterable move table, per-Pokémon level-up learnset timeline, movepool type coverage
- **Species Explorer** — single-Pokémon detail view with artwork, stat radar chart, breeding compatibility, evolution chain diagram, and an ability browser/rarity chart
- **Team Builder** — pick up to 6 Pokémon and see a heatmap of incoming damage multipliers by attacking type, with shared-weakness callouts
- **Guess That Pokemon** — a silhouette guessing game

## Notes

- ETL is idempotent — safe to rerun; upserts by PokeAPI resource ID. Schema changes to an existing `.db` file are applied automatically (missing columns are added on startup).
- Increase `--workers` / `POKEAPI_WORKERS` for faster full pulls; be mindful of PokeAPI fair-use limits.
- The Guess-game and Species Explorer artwork rely on sprite URLs fetched live from GitHub-hosted PokeAPI sprite assets — an internet connection is needed when *viewing* those, not just when building the database.

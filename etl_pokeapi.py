"""
ETL script: Extracts data from PokeAPI (pokeapi.co) and loads it into a
normalized SQLite database. Schema lives in pokeapi_schema.sql, executed
directly by build_database() (single source of truth for the DDL).

Usage:
    pip install requests
    python etl_pokeapi.py --db pokeapi.db --limit 151

Arguments:
    --db       Output SQLite file path (default: pokeapi.db)
    --limit    Limit the number of records pulled per resource type. Useful
               for testing (e.g. --limit 151 restricts to Gen 1 species/
               pokemon/evolution-chains/moves/abilities). Omit for a full pull
               of the entire PokeAPI dataset (~1000+ pokemon, ~900+ moves).
    --workers  Thread pool size for concurrent per-resource fetches (default 8)

Notes:
- Uses the public PokeAPI v2 REST endpoints (https://pokeapi.co/api/v2/).
- A bounded thread pool keeps concurrent requests reasonable; raise --workers
  cautiously and consider PokeAPI's fair-use guidance for large pulls.
- Idempotent: re-running is safe. All inserts use INSERT OR REPLACE / INSERT
  OR IGNORE keyed on the PokeAPI numeric resource id, so repeated runs update
  existing rows rather than duplicating them.
- Load order matters: lookup tables (generations, types, stats, etc.) are
  loaded first so that pokemon/species/moves inserted afterwards can satisfy
  their foreign key constraints.
"""

import argparse
import concurrent.futures
import sqlite3
import sys
import time
from pathlib import Path

import requests

BASE_URL = "https://pokeapi.co/api/v2"
SESSION = requests.Session()
SCHEMA_PATH = Path(__file__).parent / "pokeapi_schema.sql"


def get_json(url, retries=3, backoff=1.5):
    """GET a URL and return parsed JSON, retrying transient failures."""
    last_exc = None
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_exc}")


def id_from_url(url):
    """PokeAPI resource URLs end in /{id}/ - extract the trailing integer."""
    if not url:
        return None
    return int(url.rstrip("/").split("/")[-1])


def fetch_list_ids(resource, limit=None):
    """Fetch all resource ids for a given endpoint (e.g. 'pokemon', 'type')."""
    url = f"{BASE_URL}/{resource}?limit=1"
    first = get_json(url)
    total = first["count"]
    if limit is not None:
        total = min(total, limit)
    url = f"{BASE_URL}/{resource}?limit={total}"
    data = get_json(url)
    return [id_from_url(r["url"]) for r in data["results"]]


def ensure_lookup(cur, table, id_, name):
    """Insert a simple id/name lookup row if it does not already exist."""
    if id_ is None or name is None:
        return
    cur.execute(f"INSERT OR IGNORE INTO {table} (id, name) VALUES (?, ?)", (id_, name))


def ensure_column(cur, table, column, coltype):
    """Add a column to an existing table if pokeapi_schema.sql gained one since
    this db file was created (CREATE TABLE IF NOT EXISTS is a no-op on tables
    that already exist, so schema additions need this to reach old db files)."""
    existing = [row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def load_generations(cur):
    ids = fetch_list_ids("generation")
    for gid in ids:
        data = get_json(f"{BASE_URL}/generation/{gid}/")
        ensure_lookup(cur, "generations", data["id"], data["name"])


def load_types(cur):
    ids = fetch_list_ids("type")
    type_details = {}
    for tid in ids:
        data = get_json(f"{BASE_URL}/type/{tid}/")
        ensure_lookup(cur, "types", data["id"], data["name"])
        type_details[data["id"]] = data
    for tid, data in type_details.items():
        relations = data.get("damage_relations", {})
        factor_map = {
            "double_damage_to": 200,
            "half_damage_to": 50,
            "no_damage_to": 0,
        }
        for rel_key, factor in factor_map.items():
            for target in relations.get(rel_key, []):
                target_id = id_from_url(target["url"])
                cur.execute(
                    """INSERT OR REPLACE INTO type_damage_relations
                       (attacking_type_id, defending_type_id, damage_factor)
                       VALUES (?, ?, ?)""",
                    (tid, target_id, factor),
                )


def load_damage_classes(cur):
    ids = fetch_list_ids("move-damage-class")
    for did in ids:
        data = get_json(f"{BASE_URL}/move-damage-class/{did}/")
        ensure_lookup(cur, "damage_classes", data["id"], data["name"])


def load_stats(cur):
    ids = fetch_list_ids("stat")
    for sid in ids:
        data = get_json(f"{BASE_URL}/stat/{sid}/")
        cur.execute(
            "INSERT OR REPLACE INTO stats (id, name, is_battle_only) VALUES (?, ?, ?)",
            (data["id"], data["name"], int(data.get("is_battle_only", False))),
        )


def load_egg_groups(cur):
    ids = fetch_list_ids("egg-group")
    for eid in ids:
        data = get_json(f"{BASE_URL}/egg-group/{eid}/")
        ensure_lookup(cur, "egg_groups", data["id"], data["name"])


def load_colors(cur):
    ids = fetch_list_ids("pokemon-color")
    for cid in ids:
        data = get_json(f"{BASE_URL}/pokemon-color/{cid}/")
        ensure_lookup(cur, "colors", data["id"], data["name"])


def load_shapes(cur):
    ids = fetch_list_ids("pokemon-shape")
    for sid in ids:
        data = get_json(f"{BASE_URL}/pokemon-shape/{sid}/")
        ensure_lookup(cur, "shapes", data["id"], data["name"])


def load_abilities(cur, limit=None, workers=8):
    ids = fetch_list_ids("ability", limit=limit)

    def fetch_one(aid):
        return get_json(f"{BASE_URL}/ability/{aid}/")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for data in ex.map(fetch_one, ids):
            gen_id = id_from_url(data.get("generation", {}).get("url"))
            short_effect = None
            for entry in data.get("effect_entries", []):
                if entry.get("language", {}).get("name") == "en":
                    short_effect = entry.get("short_effect")
                    break
            cur.execute(
                """INSERT OR REPLACE INTO abilities
                   (id, name, generation_id, is_main_series, short_effect)
                   VALUES (?, ?, ?, ?, ?)""",
                (data["id"], data["name"], gen_id,
                 int(data.get("is_main_series", True)), short_effect),
            )


def load_evolution_chains(cur, limit=None):
    ids = fetch_list_ids("evolution-chain", limit=limit)
    for eid in ids:
        data = get_json(f"{BASE_URL}/evolution-chain/{eid}/")
        baby_item = data.get("baby_trigger_item")
        baby_item_name = baby_item["name"] if baby_item else None
        cur.execute(
            "INSERT OR REPLACE INTO evolution_chains (id, baby_trigger_item) VALUES (?, ?)",
            (data["id"], baby_item_name),
        )


def load_species(cur, limit=None, workers=8):
    ids = fetch_list_ids("pokemon-species", limit=limit)

    def fetch_one(sid):
        return get_json(f"{BASE_URL}/pokemon-species/{sid}/")

    # evolves_from_species_id is deferred to a second pass (see below): species
    # arrive in whatever order the thread pool completes fetches, so the
    # earlier stage a given species evolves from may not be inserted yet,
    # which would trip the self-referencing foreign key.
    evolves_from = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for data in ex.map(fetch_one, ids):
            gen_id = id_from_url(data.get("generation", {}).get("url"))
            evo_chain_id = id_from_url(data.get("evolution_chain", {}).get("url")) \
                if data.get("evolution_chain") else None
            color_id = id_from_url(data.get("color", {}).get("url"))
            shape = data.get("shape")
            shape_id = id_from_url(shape.get("url")) if shape else None
            growth_rate = data.get("growth_rate", {}).get("name")
            habitat = data.get("habitat", {}).get("name") if data.get("habitat") else None
            evolves_from_species = data.get("evolves_from_species")
            if evolves_from_species:
                evolves_from[data["id"]] = id_from_url(evolves_from_species["url"])

            cur.execute(
                """INSERT OR REPLACE INTO pokemon_species
                   (id, name, generation_id, evolution_chain_id, capture_rate,
                    base_happiness, is_baby, is_legendary, is_mythical,
                    hatch_counter, color_id, shape_id, growth_rate, habitat)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data["id"], data["name"], gen_id, evo_chain_id,
                 data.get("capture_rate"), data.get("base_happiness"),
                 int(data.get("is_baby", False)), int(data.get("is_legendary", False)),
                 int(data.get("is_mythical", False)), data.get("hatch_counter"),
                 color_id, shape_id, growth_rate, habitat),
            )

            for eg in data.get("egg_groups", []):
                eg_id = id_from_url(eg["url"])
                ensure_lookup(cur, "egg_groups", eg_id, eg["name"])
                cur.execute(
                    """INSERT OR IGNORE INTO species_egg_groups
                       (species_id, egg_group_id) VALUES (?, ?)""",
                    (data["id"], eg_id),
                )

    for species_id, parent_id in evolves_from.items():
        try:
            cur.execute(
                "UPDATE pokemon_species SET evolves_from_species_id = ? WHERE id = ?",
                (parent_id, species_id),
            )
        except sqlite3.IntegrityError:
            # Parent species falls outside a --limit-restricted pull; leave unset.
            pass


def load_moves(cur, limit=None, workers=8):
    ids = fetch_list_ids("move", limit=limit)

    def fetch_one(mid):
        return get_json(f"{BASE_URL}/move/{mid}/")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for data in ex.map(fetch_one, ids):
            gen_id = id_from_url(data.get("generation", {}).get("url"))
            type_id = id_from_url(data.get("type", {}).get("url"))
            dmg_class_id = id_from_url(data.get("damage_class", {}).get("url")) \
                if data.get("damage_class") else None
            cur.execute(
                """INSERT OR REPLACE INTO moves
                   (id, name, generation_id, type_id, damage_class_id,
                    power, pp, accuracy, priority, target)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data["id"], data["name"], gen_id, type_id, dmg_class_id,
                 data.get("power"), data.get("pp"), data.get("accuracy"),
                 data.get("priority"), data.get("target", {}).get("name")),
            )


def load_pokemon(cur, limit=None, workers=8):
    ids = fetch_list_ids("pokemon", limit=limit)

    def fetch_one(pid):
        return get_json(f"{BASE_URL}/pokemon/{pid}/")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for data in ex.map(fetch_one, ids):
            species_id = id_from_url(data.get("species", {}).get("url"))
            sprites = data.get("sprites") or {}
            sprite_front_default = sprites.get("front_default")
            sprite_official_artwork = (
                sprites.get("other", {}).get("official-artwork", {}).get("front_default")
            )
            cur.execute(
                """INSERT OR REPLACE INTO pokemon
                   (id, name, species_id, height, weight, base_experience,
                    pokemon_order, is_default, sprite_front_default, sprite_official_artwork)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data["id"], data["name"], species_id, data.get("height"),
                 data.get("weight"), data.get("base_experience"),
                 data.get("order"), int(data.get("is_default", True)),
                 sprite_front_default, sprite_official_artwork),
            )

            for t in data.get("types", []):
                type_id = id_from_url(t["type"]["url"])
                ensure_lookup(cur, "types", type_id, t["type"]["name"])
                cur.execute(
                    """INSERT OR REPLACE INTO pokemon_types
                       (pokemon_id, type_id, slot) VALUES (?, ?, ?)""",
                    (data["id"], type_id, t["slot"]),
                )

            for a in data.get("abilities", []):
                ability_id = id_from_url(a["ability"]["url"])
                ensure_lookup(cur, "abilities", ability_id, a["ability"]["name"])
                cur.execute(
                    """INSERT OR REPLACE INTO pokemon_abilities
                       (pokemon_id, ability_id, slot, is_hidden) VALUES (?, ?, ?, ?)""",
                    (data["id"], ability_id, a["slot"], int(a.get("is_hidden", False))),
                )

            for s in data.get("stats", []):
                stat_id = id_from_url(s["stat"]["url"])
                ensure_lookup(cur, "stats", stat_id, s["stat"]["name"])
                cur.execute(
                    """INSERT OR REPLACE INTO pokemon_stats
                       (pokemon_id, stat_id, base_stat, effort) VALUES (?, ?, ?, ?)""",
                    (data["id"], stat_id, s["base_stat"], s["effort"]),
                )

            for m in data.get("moves", []):
                move_id = id_from_url(m["move"]["url"])
                ensure_lookup(cur, "moves", move_id, m["move"]["name"])
                for detail in m.get("version_group_details", []):
                    cur.execute(
                        """INSERT OR IGNORE INTO pokemon_moves
                           (pokemon_id, move_id, version_group, learn_method,
                            level_learned_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (data["id"], move_id,
                         detail["version_group"]["name"],
                         detail["move_learn_method"]["name"],
                         detail.get("level_learned_at", 0)),
                    )


def build_database(db_path: str, limit: int, workers: int):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()
    cur.executescript(SCHEMA_PATH.read_text())
    ensure_column(cur, "pokemon_species", "evolves_from_species_id", "INTEGER")
    ensure_column(cur, "pokemon", "sprite_front_default", "TEXT")
    ensure_column(cur, "pokemon", "sprite_official_artwork", "TEXT")
    conn.commit()

    steps = [
        ("generations", load_generations, {}),
        ("damage classes", load_damage_classes, {}),
        ("stats", load_stats, {}),
        ("egg groups", load_egg_groups, {}),
        ("colors", load_colors, {}),
        ("shapes", load_shapes, {}),
        ("types + damage relations", load_types, {}),
        ("abilities", load_abilities, {"limit": limit, "workers": workers}),
        ("evolution chains", load_evolution_chains, {"limit": limit}),
        ("species", load_species, {"limit": limit, "workers": workers}),
        ("moves", load_moves, {"limit": limit, "workers": workers}),
        ("pokemon", load_pokemon, {"limit": limit, "workers": workers}),
    ]

    for label, fn, kwargs in steps:
        print(f"Loading {label} ...", file=sys.stderr)
        fn(cur, **kwargs) if kwargs else fn(cur)
        conn.commit()
        print("  done.", file=sys.stderr)

    conn.close()
    print(f"ETL complete. Database written to {db_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Load PokeAPI data into a normalized SQLite DB")
    parser.add_argument("--db", default="pokeapi.db", help="Output SQLite file path")
    parser.add_argument("--limit", type=int, default=None,
                         help="Limit number of records per resource (e.g. --limit 151 for "
                              "the original Gen 1 Pokemon). Omit for a full data pull.")
    parser.add_argument("--workers", type=int, default=8, help="Thread pool size for concurrent fetches")
    args = parser.parse_args()

    out_dir = Path(args.db).parent
    if str(out_dir) != ".":
        out_dir.mkdir(parents=True, exist_ok=True)

    build_database(args.db, args.limit, args.workers)


if __name__ == "__main__":
    main()

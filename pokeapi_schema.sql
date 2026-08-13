-- Normalized SQLite schema for PokeAPI (pokeapi.co) data
-- Design notes:
--   * Every entity that appears in PokeAPI as its own resource (type, ability,
--     stat, generation, egg-group, color, shape, damage-class, evolution-chain,
--     move, species, pokemon) gets its own table keyed on the PokeAPI numeric id
--     extracted from its resource URL. This avoids duplicating name strings.
--   * Many-to-many relationships (pokemon<->type, pokemon<->ability,
--     pokemon<->move, species<->egg-group) are modeled as junction tables with
--     composite primary keys, matching 3NF practice.
--   * pokemon_stats and pokemon_moves capture per-relationship attributes
--     (base_stat/effort, and learn_method/level/version_group respectively)
--     that cannot live on either parent table.
--   * type_damage_relations flattens PokeAPI's damage_relations object
--     (double/half/no damage to/from) into a single weighted edge table,
--     letting you compute full attacking/defending multipliers with SQL joins
--     instead of parsing nested JSON at query time.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS generations (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS types (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

-- Edge table: attacking_type_id's effectiveness against defending_type_id.
-- damage_factor: 200 = super effective, 50 = not very effective, 0 = no effect.
-- Absence of a row implies the default factor of 100 (normal effectiveness).
CREATE TABLE IF NOT EXISTS type_damage_relations (
    attacking_type_id INTEGER NOT NULL,
    defending_type_id INTEGER NOT NULL,
    damage_factor INTEGER NOT NULL,
    PRIMARY KEY (attacking_type_id, defending_type_id),
    FOREIGN KEY (attacking_type_id) REFERENCES types(id),
    FOREIGN KEY (defending_type_id) REFERENCES types(id)
);

CREATE TABLE IF NOT EXISTS damage_classes (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS stats (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    is_battle_only INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS abilities (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    generation_id INTEGER,
    is_main_series INTEGER NOT NULL DEFAULT 1,
    short_effect TEXT,
    FOREIGN KEY (generation_id) REFERENCES generations(id)
);

CREATE TABLE IF NOT EXISTS egg_groups (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS colors (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS shapes (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_chains (
    id INTEGER PRIMARY KEY,
    baby_trigger_item TEXT
);

CREATE TABLE IF NOT EXISTS pokemon_species (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    generation_id INTEGER,
    evolution_chain_id INTEGER,
    evolves_from_species_id INTEGER,
    capture_rate INTEGER,
    base_happiness INTEGER,
    is_baby INTEGER NOT NULL DEFAULT 0,
    is_legendary INTEGER NOT NULL DEFAULT 0,
    is_mythical INTEGER NOT NULL DEFAULT 0,
    hatch_counter INTEGER,
    color_id INTEGER,
    shape_id INTEGER,
    growth_rate TEXT,
    habitat TEXT,
    FOREIGN KEY (generation_id) REFERENCES generations(id),
    FOREIGN KEY (evolution_chain_id) REFERENCES evolution_chains(id),
    FOREIGN KEY (evolves_from_species_id) REFERENCES pokemon_species(id),
    FOREIGN KEY (color_id) REFERENCES colors(id),
    FOREIGN KEY (shape_id) REFERENCES shapes(id)
);

CREATE TABLE IF NOT EXISTS species_egg_groups (
    species_id INTEGER NOT NULL,
    egg_group_id INTEGER NOT NULL,
    PRIMARY KEY (species_id, egg_group_id),
    FOREIGN KEY (species_id) REFERENCES pokemon_species(id),
    FOREIGN KEY (egg_group_id) REFERENCES egg_groups(id)
);

-- One row per Pokemon *form* (e.g. "pikachu"), distinct from species, which
-- represents the Pokedex entry a form belongs to (species:form is 1:many).
CREATE TABLE IF NOT EXISTS pokemon (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    species_id INTEGER,
    height INTEGER,
    weight INTEGER,
    base_experience INTEGER,
    pokemon_order INTEGER,
    is_default INTEGER NOT NULL DEFAULT 1,
    sprite_front_default TEXT,
    sprite_official_artwork TEXT,
    FOREIGN KEY (species_id) REFERENCES pokemon_species(id)
);

CREATE TABLE IF NOT EXISTS pokemon_types (
    pokemon_id INTEGER NOT NULL,
    type_id INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    PRIMARY KEY (pokemon_id, type_id),
    FOREIGN KEY (pokemon_id) REFERENCES pokemon(id),
    FOREIGN KEY (type_id) REFERENCES types(id)
);

CREATE TABLE IF NOT EXISTS pokemon_abilities (
    pokemon_id INTEGER NOT NULL,
    ability_id INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    is_hidden INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (pokemon_id, ability_id, slot),
    FOREIGN KEY (pokemon_id) REFERENCES pokemon(id),
    FOREIGN KEY (ability_id) REFERENCES abilities(id)
);

CREATE TABLE IF NOT EXISTS pokemon_stats (
    pokemon_id INTEGER NOT NULL,
    stat_id INTEGER NOT NULL,
    base_stat INTEGER NOT NULL,
    effort INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (pokemon_id, stat_id),
    FOREIGN KEY (pokemon_id) REFERENCES pokemon(id),
    FOREIGN KEY (stat_id) REFERENCES stats(id)
);

CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    generation_id INTEGER,
    type_id INTEGER,
    damage_class_id INTEGER,
    power INTEGER,
    pp INTEGER,
    accuracy INTEGER,
    priority INTEGER,
    target TEXT,
    FOREIGN KEY (generation_id) REFERENCES generations(id),
    FOREIGN KEY (type_id) REFERENCES types(id),
    FOREIGN KEY (damage_class_id) REFERENCES damage_classes(id)
);

-- A Pokemon can learn the same move via different methods/version groups,
-- hence the composite key rather than a single (pokemon_id, move_id) pair.
CREATE TABLE IF NOT EXISTS pokemon_moves (
    pokemon_id INTEGER NOT NULL,
    move_id INTEGER NOT NULL,
    version_group TEXT NOT NULL,
    learn_method TEXT NOT NULL,
    level_learned_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (pokemon_id, move_id, version_group, learn_method),
    FOREIGN KEY (pokemon_id) REFERENCES pokemon(id),
    FOREIGN KEY (move_id) REFERENCES moves(id)
);

CREATE INDEX IF NOT EXISTS idx_pokemon_species ON pokemon(species_id);
CREATE INDEX IF NOT EXISTS idx_pokemon_types_type ON pokemon_types(type_id);
CREATE INDEX IF NOT EXISTS idx_pokemon_abilities_ability ON pokemon_abilities(ability_id);
CREATE INDEX IF NOT EXISTS idx_pokemon_stats_stat ON pokemon_stats(stat_id);
CREATE INDEX IF NOT EXISTS idx_moves_type ON moves(type_id);
CREATE INDEX IF NOT EXISTS idx_pokemon_moves_move ON pokemon_moves(move_id);

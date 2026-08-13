"""
Interactive Streamlit dashboard for exploring the normalized PokeAPI
SQLite database produced by etl_pokeapi.py.

Setup:
    pip install streamlit pandas plotly

Run with:
    python etl_pokeapi.py --db pokeapi.db --limit 151   # build the DB first
    streamlit run dashboard.py -- --db=pokeapi.db

(or simply `streamlit run dashboard.py` if pokeapi.db is in the same folder)
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DB_PATH = "pokeapi.db"
for arg in sys.argv:
    if arg.startswith("--db="):
        DB_PATH = arg.split("=", 1)[1]

st.set_page_config(page_title="Pokemon Explorer", layout="wide", page_icon="\U0001F52E")


@st.cache_resource
def get_connection(db_path: str):
    if not Path(db_path).exists():
        st.error(
            f"Database file '{db_path}' not found. Run `python etl_pokeapi.py --db {db_path}` first."
        )
        st.stop()
    return sqlite3.connect(db_path, check_same_thread=False)


@st.cache_data
def run_query(_conn, sql: str, params: tuple = ()):
    return pd.read_sql_query(sql, _conn, params=params)


def type_multiplier(relations_df, attacking_type_id, defending_type_ids):
    multiplier = 1.0
    for def_id in defending_type_ids:
        match = relations_df[
            (relations_df["attacking_type_id"] == attacking_type_id)
            & (relations_df["defending_type_id"] == def_id)
        ]
        factor = match["damage_factor"].iloc[0] if len(match) else 100
        multiplier *= factor / 100
    return multiplier


conn = get_connection(DB_PATH)

st.title("Pokemon Explorer")
st.caption("Interactive analysis of normalized Pokemon data loaded from pokeapi.co")

types_df = run_query(conn, "SELECT id, name FROM types ORDER BY name")
gens_df = run_query(conn, "SELECT id, name FROM generations ORDER BY id")
all_pokemon_df = run_query(conn, "SELECT id, name FROM pokemon ORDER BY name")
type_relations_df = run_query(
    conn, "SELECT attacking_type_id, defending_type_id, damage_factor FROM type_damage_relations"
)
type_id_by_name = dict(zip(types_df["name"], types_df["id"]))

with st.sidebar:
    st.header("Filters")

    gen_options = ["All"] + gens_df["name"].tolist()
    selected_gen = st.selectbox("Generation", gen_options)

    type_options = ["All"] + types_df["name"].tolist()
    selected_type = st.selectbox("Type", type_options)

    legendary_filter = st.radio(
        "Species class", ["All", "Legendary/Mythical only", "Regular only"], index=0
    )

    search_name = st.text_input("Search by name (partial match)")

    default_forms_only = st.checkbox(
        "Default forms only",
        value=True,
        help="Exclude alternate forms (mega evolutions, regional forms, gmax, etc.)",
    )

base_query = """
SELECT
    p.id, p.name, p.height, p.weight, p.base_experience,
    ps.is_legendary, ps.is_mythical, g.name AS generation,
    GROUP_CONCAT(DISTINCT t.name) AS types_list
FROM pokemon p
LEFT JOIN pokemon_species ps ON p.species_id = ps.id
LEFT JOIN generations g ON ps.generation_id = g.id
LEFT JOIN pokemon_types pt ON pt.pokemon_id = p.id
LEFT JOIN types t ON t.id = pt.type_id
"""

conditions = []
params = []

if selected_gen != "All":
    conditions.append("g.name = ?")
    params.append(selected_gen)

if selected_type != "All":
    conditions.append(
        "p.id IN (SELECT pokemon_id FROM pokemon_types pt2 "
        "JOIN types t2 ON t2.id = pt2.type_id WHERE t2.name = ?)"
    )
    params.append(selected_type)

if legendary_filter == "Legendary/Mythical only":
    conditions.append("(ps.is_legendary = 1 OR ps.is_mythical = 1)")
elif legendary_filter == "Regular only":
    conditions.append("(ps.is_legendary = 0 AND ps.is_mythical = 0)")

if search_name:
    conditions.append("p.name LIKE ?")
    params.append(f"%{search_name.lower()}%")

if default_forms_only:
    conditions.append("p.is_default = 1")

if conditions:
    base_query += " WHERE " + " AND ".join(conditions)

base_query += " GROUP BY p.id ORDER BY p.id"

pokemon_df = run_query(conn, base_query, tuple(params))

col1, col2, col3, col4 = st.columns(4)
col1.metric("Pokemon matched", len(pokemon_df))
col2.metric("Avg height (dm)", round(pokemon_df["height"].mean(), 1) if len(pokemon_df) else 0)
col3.metric("Avg weight (hg)", round(pokemon_df["weight"].mean(), 1) if len(pokemon_df) else 0)
col4.metric(
    "Legendary/Mythical",
    int(((pokemon_df["is_legendary"] == 1) | (pokemon_df["is_mythical"] == 1)).sum())
    if len(pokemon_df) else 0,
)

tab_overview, tab_stats, tab_types, tab_moves, tab_explorer, tab_team, tab_guess = st.tabs(
    ["Overview", "Base Stats", "Type Analysis", "Moves", "Species Explorer",
     "Team Builder", "Guess That Pokemon"]
)

with tab_overview:
    st.subheader("Filtered Pokemon")
    st.dataframe(
        pokemon_df[["id", "name", "generation", "types_list", "height", "weight", "base_experience"]],
        use_container_width=True,
        hide_index=True,
    )

    if len(pokemon_df):
        fig = px.histogram(
            pokemon_df, x="base_experience", nbins=30,
            title="Base Experience Distribution",
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("Trends Across Generations")

    trend_query = """
        SELECT g.id AS gen_id, g.name AS generation, p.id AS pokemon_id, ps.base_stat
        FROM pokemon p
        JOIN pokemon_species sp ON sp.id = p.species_id
        JOIN generations g ON g.id = sp.generation_id
        JOIN pokemon_stats ps ON ps.pokemon_id = p.id
        WHERE p.is_default = 1
    """
    trend_df = run_query(conn, trend_query)
    if len(trend_df):
        totals = trend_df.groupby(["gen_id", "generation", "pokemon_id"])["base_stat"].sum().reset_index()
        gen_avg = totals.groupby(["gen_id", "generation"])["base_stat"].mean().reset_index()
        gen_avg = gen_avg.sort_values("gen_id")
        fig_trend = px.line(
            gen_avg, x="generation", y="base_stat", markers=True,
            category_orders={"generation": gen_avg["generation"].tolist()},
            title="Average Base Stat Total by Generation",
            labels={"base_stat": "Avg base stat total"},
        )
        st.plotly_chart(fig_trend, use_container_width=True)

    legend_query = """
        SELECT g.id AS gen_id, g.name AS generation,
               SUM(sp.is_legendary) AS legendary_count,
               SUM(sp.is_mythical) AS mythical_count
        FROM pokemon_species sp
        JOIN generations g ON g.id = sp.generation_id
        GROUP BY g.id, g.name
        ORDER BY g.id
    """
    legend_df = run_query(conn, legend_query)
    if len(legend_df):
        legend_melted = legend_df.melt(
            id_vars=["gen_id", "generation"], value_vars=["legendary_count", "mythical_count"],
            var_name="class", value_name="count",
        )
        fig_legend = px.bar(
            legend_melted, x="generation", y="count", color="class", barmode="group",
            category_orders={"generation": legend_df["generation"].tolist()},
            title="Legendary & Mythical Species by Generation",
        )
        st.plotly_chart(fig_legend, use_container_width=True)

with tab_stats:
    st.subheader("Base Stats Comparison")

    if len(pokemon_df):
        ids_tuple = tuple(pokemon_df["id"].tolist())
        placeholders = ",".join("?" * len(ids_tuple))
        stats_query = f"""
            SELECT p.name, s.name AS stat_name, ps.base_stat
            FROM pokemon_stats ps
            JOIN pokemon p ON p.id = ps.pokemon_id
            JOIN stats s ON s.id = ps.stat_id
            WHERE p.id IN ({placeholders})
        """
        stats_df = run_query(conn, stats_query, ids_tuple)

        if len(stats_df):
            pivot = stats_df.pivot_table(
                index="name", columns="stat_name", values="base_stat", aggfunc="first"
            ).reset_index()

            default_selection = pivot["name"].tolist()[:10]
            chosen = st.multiselect(
                "Choose Pokemon to compare", pivot["name"].tolist(), default=default_selection
            )

            if chosen:
                melted = stats_df[stats_df["name"].isin(chosen)]
                fig2 = px.bar(
                    melted, x="stat_name", y="base_stat", color="name",
                    barmode="group", title="Base Stats by Pokemon",
                )
                st.plotly_chart(fig2, use_container_width=True)

            st.dataframe(pivot, use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("Stat Correlations")
            corr = pivot.drop(columns=["name"]).corr()
            fig_corr = px.imshow(
                corr, text_auto=".2f", color_continuous_scale="RdBu_r", zmin=-1, zmax=1,
                title="Correlation Between Base Stats (filtered Pokemon)",
            )
            st.plotly_chart(fig_corr, use_container_width=True)

            st.divider()
            st.subheader("Base Stat Total Distribution")
            totals_df = stats_df.groupby("name")["base_stat"].sum().reset_index(name="stat_total")
            totals_df = totals_df.merge(pokemon_df[["name", "generation", "types_list"]], on="name", how="left")
            group_by = st.radio("Group by", ["Type", "Generation"], horizontal=True, key="dist_group_by")
            if group_by == "Type":
                exploded = totals_df.assign(type=totals_df["types_list"].fillna("").str.split(","))
                exploded = exploded.explode("type")
                exploded = exploded[exploded["type"] != ""]
                fig_dist = px.box(exploded, x="type", y="stat_total", title="Base Stat Total by Type")
            else:
                fig_dist = px.box(
                    totals_df, x="generation", y="stat_total",
                    category_orders={"generation": gens_df["name"].tolist()},
                    title="Base Stat Total by Generation",
                )
            st.plotly_chart(fig_dist, use_container_width=True)
    else:
        st.info("No Pokemon match the current filters.")

with tab_types:
    st.subheader("Type Effectiveness & Distribution")

    type_count_query = """
        SELECT t.name AS type_name, COUNT(*) AS pokemon_count
        FROM pokemon_types pt
        JOIN types t ON t.id = pt.type_id
        GROUP BY t.name
        ORDER BY pokemon_count DESC
    """
    type_counts = run_query(conn, type_count_query)
    fig3 = px.bar(type_counts, x="type_name", y="pokemon_count", title="Pokemon Count by Type")
    st.plotly_chart(fig3, use_container_width=True)

    st.markdown("**Damage relations lookup**")
    st.caption("Types with no explicit relation deal normal (100) damage.")
    dmg_type = st.selectbox("Attacking type", types_df["name"].tolist(), key="dmg_type_select")
    dmg_query = """
        SELECT dt.name AS defending_type,
               COALESCE(td.damage_factor, 100) AS damage_factor
        FROM types dt
        LEFT JOIN types at ON at.name = ?
        LEFT JOIN type_damage_relations td
            ON td.attacking_type_id = at.id AND td.defending_type_id = dt.id
        ORDER BY damage_factor DESC, dt.name
    """
    dmg_df = run_query(conn, dmg_query, (dmg_type,))
    st.dataframe(
        dmg_df[["defending_type", "damage_factor"]], use_container_width=True, hide_index=True
    )

    st.divider()
    st.subheader("Type Distribution by Generation")
    treemap_query = """
        SELECT g.name AS generation, t.name AS type_name, COUNT(*) AS pokemon_count
        FROM pokemon p
        JOIN pokemon_species sp ON sp.id = p.species_id
        JOIN generations g ON g.id = sp.generation_id
        JOIN pokemon_types pt ON pt.pokemon_id = p.id
        JOIN types t ON t.id = pt.type_id
        WHERE p.is_default = 1
        GROUP BY g.name, t.name
    """
    treemap_df = run_query(conn, treemap_query)
    if len(treemap_df):
        fig_tree = px.treemap(
            treemap_df, path=["generation", "type_name"], values="pokemon_count",
            title="Pokemon Count by Generation and Type",
        )
        st.plotly_chart(fig_tree, use_container_width=True)

    st.divider()
    st.subheader("Dual-Type Defensive Calculator")
    st.caption("Pick one or two defending types to see combined incoming damage from every attacking type.")
    dc1, dc2 = st.columns(2)
    def_type_1 = dc1.selectbox("Defending type 1", types_df["name"].tolist(), key="def_type_1")
    def_type_2 = dc2.selectbox(
        "Defending type 2 (optional)", ["None"] + types_df["name"].tolist(), key="def_type_2"
    )

    defending_ids = [type_id_by_name[def_type_1]]
    if def_type_2 != "None":
        defending_ids.append(type_id_by_name[def_type_2])

    calc_rows = [
        {"attacking_type": atk["name"], "multiplier": type_multiplier(type_relations_df, atk["id"], defending_ids)}
        for _, atk in types_df.iterrows()
    ]
    calc_df = pd.DataFrame(calc_rows).sort_values("multiplier", ascending=False)
    calc_df["label"] = calc_df["multiplier"].apply(lambda m: "Immune (0x)" if m == 0 else f"{m:g}x")

    matchup_title = def_type_1 if def_type_2 == "None" else f"{def_type_1}/{def_type_2}"
    fig_calc = px.bar(
        calc_df, x="attacking_type", y="multiplier", color="label",
        title=f"Incoming Damage vs {matchup_title}",
    )
    st.plotly_chart(fig_calc, use_container_width=True)

with tab_moves:
    st.subheader("Move Explorer")

    move_type_options = ["All"] + types_df["name"].tolist()
    selected_move_type = st.selectbox("Filter moves by type", move_type_options, key="move_type_select")

    move_query = """
        SELECT m.name, t.name AS type, dc.name AS damage_class,
               m.power, m.pp, m.accuracy, m.priority
        FROM moves m
        LEFT JOIN types t ON t.id = m.type_id
        LEFT JOIN damage_classes dc ON dc.id = m.damage_class_id
    """
    move_params = []
    if selected_move_type != "All":
        move_query += " WHERE t.name = ?"
        move_params.append(selected_move_type)
    move_query += " ORDER BY m.power DESC"

    moves_df = run_query(conn, move_query, tuple(move_params))
    st.dataframe(moves_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Learnset Explorer")
    if len(pokemon_df):
        learnset_pokemon = st.selectbox(
            "View learnset for", pokemon_df["name"].tolist(), key="learnset_pokemon"
        )
        learnset_pid = int(pokemon_df.loc[pokemon_df["name"] == learnset_pokemon, "id"].iloc[0])

        version_groups_df = run_query(
            conn,
            "SELECT DISTINCT version_group FROM pokemon_moves WHERE pokemon_id = ? ORDER BY version_group",
            (learnset_pid,),
        )
        if len(version_groups_df):
            version_group = st.selectbox(
                "Version group", version_groups_df["version_group"].tolist(), key="learnset_version_group"
            )
            levelup_query = """
                SELECT m.name AS move_name, t.name AS move_type, m.power, pm.level_learned_at
                FROM pokemon_moves pm
                JOIN moves m ON m.id = pm.move_id
                LEFT JOIN types t ON t.id = m.type_id
                WHERE pm.pokemon_id = ? AND pm.version_group = ? AND pm.learn_method = 'level-up'
                ORDER BY pm.level_learned_at
            """
            levelup_df = run_query(conn, levelup_query, (learnset_pid, version_group))
            if len(levelup_df):
                fig_learn = px.scatter(
                    levelup_df, x="level_learned_at", y="move_name", color="move_type",
                    size=levelup_df["power"].fillna(0) + 10,
                    title=f"{learnset_pokemon.title()} Level-Up Learnset ({version_group})",
                    labels={"level_learned_at": "Level"},
                )
                st.plotly_chart(fig_learn, use_container_width=True)
            else:
                st.info("No level-up moves recorded for this version group.")
        else:
            st.info("No movepool data for this Pokemon.")

        st.divider()
        st.subheader("Movepool Type Coverage")
        coverage_query = """
            SELECT t.name AS move_type, COUNT(DISTINCT m.id) AS move_count
            FROM pokemon_moves pm
            JOIN moves m ON m.id = pm.move_id
            JOIN types t ON t.id = m.type_id
            WHERE pm.pokemon_id = ?
            GROUP BY t.name
            ORDER BY move_count DESC
        """
        coverage_df = run_query(conn, coverage_query, (learnset_pid,))
        if len(coverage_df):
            fig_cov = px.bar(
                coverage_df, x="move_type", y="move_count",
                title=f"{learnset_pokemon.title()} Movepool Type Coverage (all methods/versions)",
            )
            st.plotly_chart(fig_cov, use_container_width=True)
    else:
        st.info("No Pokemon match the current filters.")

with tab_explorer:
    st.subheader("Single Pokemon Detail")

    if len(pokemon_df):
        chosen_name = st.selectbox("Choose a Pokemon", pokemon_df["name"].tolist())
        detail_row = pokemon_df[pokemon_df["name"] == chosen_name].iloc[0]

        st.markdown(f"### {chosen_name.title()} (#{int(detail_row['id'])})")

        sprite_row = run_query(
            conn,
            "SELECT sprite_front_default, sprite_official_artwork FROM pokemon WHERE id = ?",
            (int(detail_row["id"]),),
        )
        if len(sprite_row):
            sprite_url = sprite_row.iloc[0]["sprite_official_artwork"] or sprite_row.iloc[0]["sprite_front_default"]
            if sprite_url:
                st.image(sprite_url, width=200)

        d1, d2, d3 = st.columns(3)
        d1.metric("Height (dm)", detail_row["height"])
        d2.metric("Weight (hg)", detail_row["weight"])
        d3.metric("Base XP", detail_row["base_experience"])
        st.write(f"**Types:** {detail_row['types_list']}")
        st.write(f"**Generation:** {detail_row['generation']}")

        abilities_query = """
            SELECT a.name, pa.is_hidden
            FROM pokemon_abilities pa
            JOIN abilities a ON a.id = pa.ability_id
            WHERE pa.pokemon_id = ?
        """
        abilities_df = run_query(conn, abilities_query, (int(detail_row["id"]),))
        st.write("**Abilities:**",
                  ", ".join(abilities_df["name"].tolist()) if len(abilities_df) else "N/A")

        stat_query = """
            SELECT s.name AS stat, ps.base_stat
            FROM pokemon_stats ps
            JOIN stats s ON s.id = ps.stat_id
            WHERE ps.pokemon_id = ?
        """
        single_stats_df = run_query(conn, stat_query, (int(detail_row["id"]),))
        if len(single_stats_df):
            fig4 = px.bar_polar(
                single_stats_df, r="base_stat", theta="stat",
                title=f"{chosen_name.title()} Base Stat Radar",
            )
            st.plotly_chart(fig4, use_container_width=True)

        with st.expander("Breeding compatibility"):
            egg_groups_df = run_query(
                conn,
                """
                SELECT eg.id, eg.name
                FROM species_egg_groups seg
                JOIN egg_groups eg ON eg.id = seg.egg_group_id
                WHERE seg.species_id = (SELECT species_id FROM pokemon WHERE id = ?)
                    AND eg.name != 'no-eggs'
                """,
                (int(detail_row["id"]),),
            )
            if len(egg_groups_df):
                st.write("**Egg groups:**", ", ".join(egg_groups_df["name"].tolist()))
                eg_ids = tuple(egg_groups_df["id"].tolist())
                placeholders = ",".join("?" * len(eg_ids))
                partners_query = f"""
                    SELECT DISTINCT sp.name
                    FROM species_egg_groups seg
                    JOIN pokemon_species sp ON sp.id = seg.species_id
                    WHERE seg.egg_group_id IN ({placeholders})
                        AND sp.id != (SELECT species_id FROM pokemon WHERE id = ?)
                    ORDER BY sp.name
                """
                partners_df = run_query(conn, partners_query, eg_ids + (int(detail_row["id"]),))
                st.write(f"**{len(partners_df)} compatible breeding partner species:**")
                st.dataframe(partners_df, use_container_width=True, hide_index=True)
            else:
                st.info("No egg group data (or this species cannot breed).")

        with st.expander("Evolution chain"):
            chain_df = run_query(
                conn,
                """
                SELECT sp.id, sp.name, sp.evolves_from_species_id
                FROM pokemon_species sp
                WHERE sp.evolution_chain_id = (
                    SELECT evolution_chain_id FROM pokemon_species
                    WHERE id = (SELECT species_id FROM pokemon WHERE id = ?)
                )
                """,
                (int(detail_row["id"]),),
            )
            if len(chain_df) <= 1:
                st.info("This species does not evolve.")
            else:
                children_of = {}
                for _, row in chain_df.iterrows():
                    parent = row["evolves_from_species_id"]
                    if pd.notna(parent):
                        children_of.setdefault(int(parent), []).append(int(row["id"]))

                all_ids = {int(i) for i in chain_df["id"]}
                child_ids = {cid for kids in children_of.values() for cid in kids}
                roots = [sid for sid in all_ids if sid not in child_ids]

                if not roots:
                    st.info(
                        "Evolution links unavailable for this chain "
                        "(species may fall outside a --limit-restricted pull)."
                    )
                else:
                    depth = {}

                    def assign_depth(node_id, d):
                        depth[node_id] = d
                        for child in children_of.get(node_id, []):
                            assign_depth(child, d + 1)

                    for r in roots:
                        assign_depth(r, 0)

                    y_pos = {}
                    leaf_counter = [0]

                    def assign_y(node_id):
                        kids = children_of.get(node_id, [])
                        if not kids:
                            y_pos[node_id] = leaf_counter[0]
                            leaf_counter[0] += 1
                            return y_pos[node_id]
                        child_ys = [assign_y(k) for k in kids]
                        y_pos[node_id] = sum(child_ys) / len(child_ys)
                        return y_pos[node_id]

                    for r in roots:
                        assign_y(r)

                    id_to_name = dict(zip(chain_df["id"].astype(int), chain_df["name"]))
                    node_ids = list(depth.keys())

                    edge_x, edge_y = [], []
                    for parent_id, kids in children_of.items():
                        for kid in kids:
                            edge_x += [depth[parent_id], depth[kid], None]
                            edge_y += [y_pos[parent_id], y_pos[kid], None]

                    fig_evo = go.Figure()
                    fig_evo.add_trace(go.Scatter(
                        x=edge_x, y=edge_y, mode="lines",
                        line={"width": 2, "color": "lightgray"}, hoverinfo="skip",
                    ))
                    fig_evo.add_trace(go.Scatter(
                        x=[depth[i] for i in node_ids],
                        y=[y_pos[i] for i in node_ids],
                        mode="markers+text",
                        text=[id_to_name[i].title() for i in node_ids],
                        textposition="middle right",
                        marker={"size": 22, "color": "#3B82F6"},
                        hoverinfo="text",
                    ))
                    fig_evo.update_layout(
                        title=f"Evolution Chain: {chosen_name.title()}",
                        showlegend=False,
                        xaxis={"visible": False, "showgrid": False, "range": [-0.3, max(depth.values()) + 1.3]},
                        yaxis={"visible": False, "showgrid": False},
                        margin={"l": 20, "r": 100, "t": 40, "b": 20},
                    )
                    st.plotly_chart(fig_evo, use_container_width=True)
    else:
        st.info("No Pokemon match the current filters.")

    st.divider()
    st.subheader("Ability Browser")
    all_abilities_df = run_query(conn, "SELECT id, name FROM abilities ORDER BY name")
    if len(all_abilities_df):
        browse_ability = st.selectbox(
            "Browse Pokemon with ability", all_abilities_df["name"].tolist(), key="ability_browse_select"
        )
        ability_pokemon_query = """
            SELECT p.name AS pokemon, pa.is_hidden
            FROM pokemon_abilities pa
            JOIN pokemon p ON p.id = pa.pokemon_id
            JOIN abilities a ON a.id = pa.ability_id
            WHERE a.name = ?
            ORDER BY p.name
        """
        ability_pokemon_df = run_query(conn, ability_pokemon_query, (browse_ability,))
        st.write(f"**{len(ability_pokemon_df)} Pokemon have {browse_ability.title()}:**")
        st.dataframe(ability_pokemon_df, use_container_width=True, hide_index=True)

        st.markdown("**Ability rarity (top 15 by Pokemon count)**")
        rarity_query = """
            SELECT a.name AS ability, COUNT(*) AS pokemon_count
            FROM pokemon_abilities pa
            JOIN abilities a ON a.id = pa.ability_id
            GROUP BY a.name
            ORDER BY pokemon_count DESC
            LIMIT 15
        """
        rarity_df = run_query(conn, rarity_query)
        fig_rarity = px.bar(rarity_df, x="ability", y="pokemon_count", title="Most Common Abilities")
        st.plotly_chart(fig_rarity, use_container_width=True)

with tab_team:
    st.subheader("Team Weakness Coverage")
    st.caption("Pick up to 6 Pokemon to see a heatmap of incoming damage multipliers by attacking type.")

    team_choice = st.multiselect(
        "Team (max 6)", all_pokemon_df["name"].tolist(), max_selections=6, key="team_builder_select"
    )

    if team_choice:
        team_members = all_pokemon_df[all_pokemon_df["name"].isin(team_choice)]
        placeholders = ",".join("?" * len(team_members))
        team_types_query = f"""
            SELECT pt.pokemon_id, t.id AS type_id
            FROM pokemon_types pt
            JOIN types t ON t.id = pt.type_id
            WHERE pt.pokemon_id IN ({placeholders})
        """
        team_types_df = run_query(conn, team_types_query, tuple(team_members["id"].tolist()))

        matrix_rows = []
        for _, member in team_members.iterrows():
            member_type_ids = team_types_df.loc[
                team_types_df["pokemon_id"] == member["id"], "type_id"
            ].tolist()
            row = {"pokemon": member["name"]}
            for _, atk in types_df.iterrows():
                row[atk["name"]] = type_multiplier(type_relations_df, atk["id"], member_type_ids)
            matrix_rows.append(row)

        matrix_df = pd.DataFrame(matrix_rows).set_index("pokemon")
        fig_team = px.imshow(
            matrix_df, text_auto=".2g", color_continuous_scale="RdYlGn_r", aspect="auto",
            title="Incoming Damage Multiplier by Attacking Type",
            labels={"x": "Attacking type", "y": "Pokemon", "color": "Multiplier"},
        )
        st.plotly_chart(fig_team, use_container_width=True)

        weak_counts = (matrix_df >= 2).sum(axis=0)
        threshold = min(3, len(team_choice))
        shared_weaknesses = weak_counts[weak_counts >= threshold].sort_values(ascending=False)
        if len(shared_weaknesses):
            st.warning(
                "Shared weaknesses: " + ", ".join(
                    f"{t} ({n}/{len(team_choice)})" for t, n in shared_weaknesses.items()
                )
            )
        else:
            st.success(f"No type hits {threshold}+ of your team super-effectively.")
    else:
        st.info("Select at least one Pokemon to build a team.")

with tab_guess:
    st.subheader("Guess That Pokemon!")

    guessable_df = run_query(
        conn,
        "SELECT id, name, sprite_front_default FROM pokemon "
        "WHERE sprite_front_default IS NOT NULL AND is_default = 1",
    )

    if not len(guessable_df):
        st.info("No sprite data available yet - re-run the ETL after the sprite-column update.")
    else:
        if "guess_target_id" not in st.session_state or not len(
            guessable_df[guessable_df["id"] == st.session_state.guess_target_id]
        ):
            st.session_state.guess_target_id = int(guessable_df.sample(1)["id"].iloc[0])
            st.session_state.guess_revealed = False
            st.session_state.guess_was_correct = False

        target_row = guessable_df[guessable_df["id"] == st.session_state.guess_target_id].iloc[0]

        img_col, guess_col = st.columns([1, 2])

        with img_col:
            img_style = "width:220px;" if st.session_state.guess_revealed else "width:220px; filter:brightness(0);"
            st.markdown(
                f'<img src="{target_row["sprite_front_default"]}" style="{img_style}">',
                unsafe_allow_html=True,
            )

        with guess_col:
            guess = st.selectbox(
                "Who's that Pokemon?", [""] + sorted(guessable_df["name"].tolist()), key="guess_input"
            )

            gcol1, gcol2, gcol3 = st.columns(3)
            if gcol1.button("Check"):
                if guess.lower() == target_row["name"].lower():
                    st.session_state.guess_revealed = True
                    st.session_state.guess_was_correct = True
                    st.rerun()
                else:
                    st.error("Not quite, try again.")
            if gcol2.button("Reveal"):
                st.session_state.guess_revealed = True
                st.rerun()
            if gcol3.button("Next Pokemon"):
                st.session_state.guess_target_id = int(guessable_df.sample(1)["id"].iloc[0])
                st.session_state.guess_revealed = False
                st.session_state.guess_was_correct = False
                st.rerun()

            if st.session_state.guess_revealed:
                if st.session_state.guess_was_correct:
                    st.success(f"Correct! It's {target_row['name'].title()}!")
                else:
                    st.write(f"**It's {target_row['name'].title()}!**")

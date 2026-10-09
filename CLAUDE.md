# mg-prop-hunt-maps

The maps mg-prop-hunt plays, as JSON documents. Read `../../CLAUDE.md` and `../mg-prop-hunt/CLAUDE.md` first: the format is defined and validated by mg-prop-hunt's `game/ph_map_doc.gd`, and `maps/README.md` is the mapper's copy of it.

**`maps/` is what the game reads**, through a dot-bootstrap link (`mg-prop-hunt/.gitignore` names it: `# bootstrap-link: mg-prop-hunt-maps/maps`). The game reads every `.json` there plus the practice house built into it; a refused document is listed by `ph_maps` with the reason, and every other map still plays.

**`tools/build_maps.py` writes every document, and the JSON is committed.** Edit the builder, run it, commit both. `--check` (CI) fails when a committed file differs from what the builder writes. The builder exists because a building is hundreds of boxes that have to meet: a wall with a door in it is five boxes whose edges are the door's, and a classroom is thirty desks and chairs in rows.

**Prop sizes are a snapshot** (`tools/prop_sizes.json`), copied from mg-prop-hunt's `props/catalogue.json` by `--sync-sizes`, so the builder (and CI) runs without the game checked out. The builder needs a prop's size to stand it against a wall, on a table, or to keep a spawn clear of it; a prop it does not know stops the build.

## Things the builder decides for you

- **Spawns are settled** (`Map._settle_spawns`): a spawn is written where a room's plan says players start, before the room is furnished, and a sofa put down later lands on it. mg-prop-hunt's `headless_maps` found six that way; the builder now moves each blocked one to the nearest clear, floored spot in rings of half a metre and prints what it moved. A spawn with nothing clear within four metres stops the build.
- **Roof eaves are per room** (`room(..., eave=)`): a roof reaches 0.3 m past its room to cover the walls and overhang the outside, but a room beside a taller one (the school's hallway and bathroom beside the gym) wants `WALL / 2`, or its roof shows as a dark band inside the taller room. Found by rendering the gym.
- **A sunken thing is cut out of the ground** (`floor_around`): the house's pool and the woods' pond sit below a lawn that would otherwise be drawn over them. Found by rendering: the first house had a pool of grass.
- **A tall room is lined on the inside** (the school's gym): brick above a painted wall read as a missing wall.

## Lighting

Outdoor maps are lit by the sun and the ambient colour in the document's `environment`. The first renders were washed out (sun 1.15, ambient 0.6 on the school); 0.95 and 0.42 read as daylight. The woods' fog was 0.008 and turned the whole map orange from above; it is 0.0025. Render after changing either: `mg-prop-hunt/tools/shot.sh --view=overview --ph-map-ids=<id>`.

## Publishing

**This repository is a pack of its own, mounted on the server alone.** mg-prop-hunt's `game.yml` names it under `server_dependencies`; a server mounts it and the game adds that mount's `maps/` to its catalogue (`PhModule._add_delivered_maps`). It is never in a client's content sync: a client is sent the one document being played. `.github/workflows/release.yml` runs `build_maps.py --check` and then dot-ci's release with `pack: true` (and `server-compat: false`, because there is no code in it to check). dot-server-deploy's `content/prophunt_maps/` and `examples/prophunt_client` (22 checks) prove the delivered pair works over a real socket.

**Not yet on GitHub** (2026-10-09): the remote is set, the repository is the owner's to create, and nothing is pushed or published.

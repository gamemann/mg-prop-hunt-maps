The maps for [**mg-prop-hunt**](https://github.com/gamemann/mg-prop-hunt): a whole primary school with its grounds, a family house with a garden and a pool, an office floor, and a clearing in the woods at golden hour. Four maps to start with, plus the practice house built into the game.

Every map is a **JSON document**, not a scene. A server reads the documents in its map directory and sends the one it is playing to every client when the map changes, so players need no map files at all and a new map is one file dropped into a directory. [`maps/README.md`](maps/README.md) is the format.

## Using it

[dot-bootstrap](https://github.com/modcommunity/dot-bootstrap) clones this repository next to mg-prop-hunt and links it into the game as `maps/`, so `./game.sh` in mg-prop-hunt plays these maps with nothing else to do. On a server they are delivered as a pack of their own (`prophunt_maps`), which mg-prop-hunt's `./game.sh online` and `./game.sh server` build for you.

## The maps

| Map | What it is |
| --- | --- |
| `ph_school` (Maple Grove School) | Four classrooms, a library, a cafeteria, a gym, bathrooms, the offices and a hallway of lockers, then the playground, the field, the car park and a ring of trees outside |
| `ph_house` (Willow Lane) | A living room, kitchen, two bedrooms, a bathroom and a garage, with a garden, a deck and a pool behind it and the street in front |
| `ph_office` (Brightline Offices) | Open-plan desks, glass meeting rooms, a break room and a store room full of boxes, on one floor with glass all round |
| `ph_woods` (Pinecrest Woods) | A log cabin in a clearing, a woodpile, a pumpkin patch, a pond and a lot of trees and rocks to be |

## How they are made

`tools/build_maps.py` writes every document in `maps/`. A wall with its doors and windows, a room with its floor, ceiling and lights, and a furnished classroom are one call each, and every prop is placed by where it stands.

```bash
python3 tools/build_maps.py                # rewrite maps/*.json
python3 tools/build_maps.py --check        # exit 1 if a file differs from what the builder writes
python3 tools/build_maps.py --sync-sizes   # copy prop sizes from ../mg-prop-hunt after it measures new models
```

The JSON is still what the game reads and is committed; CI runs `--check`, so a hand edit to a built document is caught instead of being lost on the next build. Hand-written documents the builder does not produce are fine too: anything in `maps/` with a `.json` extension is loaded.

The builder moves any spawn that a prop or a wall was put on top of to the nearest clear spot and says so. Every map is then checked by mg-prop-hunt's `examples/headless_maps.tscn`: it fits on the wire, every prop is one the game knows, every spawn has a floor and room for a person, no prop is sunk into a wall, and every prop stands on something.

## License

MIT. See [LICENSE](LICENSE). The furniture and nature models the maps name are Kenney's (CC0) and live in mg-prop-hunt.

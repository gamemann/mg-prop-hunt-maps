#!/usr/bin/env python3
"""Writes every map in maps/ from the plans below.

A map is a JSON document (see mg-prop-hunt's game/ph_map_doc.gd and maps/README.md), and
nothing stops a mapper writing one by hand. These are generated instead because a building is
hundreds of boxes that have to meet: a wall with a door in it is five boxes whose edges are
the door's, a room's floor, ceiling and light belong to the room, and a classroom is thirty
desks and chairs in rows. Here a wall is one call with its holes, a room is one call, and a
classroom is one call that furnishes it.

    tools/build_maps.py                 # rewrite every map
    tools/build_maps.py --check         # fail if a file on disk is not what this would write
    tools/build_maps.py --sync-sizes    # copy prop sizes from ../mg-prop-hunt (when it is there)

Conventions, which every function below keeps:

- Metres, degrees. y is up and every floor's top is at y 0 unless a room says otherwise.
- A box's `at` is its centre. A prop's `at` is the middle of its footprint ON the floor.
- A prop's FRONT faces +Z at yaw 0 (Kenney's furniture does: a stove's door, a desk's drawers),
  so a sofa against a north wall (the wall at the smaller z) faces into the room at yaw 0, one
  against a south wall at yaw 180, against a west wall at 90 and an east wall at -90.
- Doors are 1.4 m wide and 2.3 m tall, so a disguised prop's hull (at most 1.1 m across) fits.
- Stairs are not used: a small prop's step is 0.2 m.
"""

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIZES_FILE = os.path.join(HERE, "prop_sizes.json")
GAME_CATALOGUE = os.path.join(ROOT, "..", "mg-prop-hunt", "props", "catalogue.json")

WALL = 0.2          # an interior wall
OUTER = 0.3         # an exterior wall, brick outside and paint inside
DOOR_W = 1.4
DOOR_H = 2.3
SILL = 0.9          # a window's bottom
HEAD = 2.2          # a window's top


def r(v):
    return round(v, 3)


def v3(x, y, z):
    return [r(x), r(y), r(z)]


def load_sizes():
    with open(SIZES_FILE) as f:
        return json.load(f)["props"]


SIZES = load_sizes() if os.path.exists(SIZES_FILE) else {}


def size_of(pid):
    s = SIZES.get(pid)
    if s is None:
        raise SystemExit("unknown prop %s (run --sync-sizes?)" % pid)
    return s["size"]


def footprint(pid, yaw):
    """A prop's width (x) and depth (z) once turned by yaw (multiples of 90 only)."""
    sx, sy, sz = size_of(pid)
    if int(round(yaw)) % 180 == 90 or int(round(yaw)) % 180 == -90:
        return sz, sx
    return sx, sz


class Map:
    def __init__(self, mid, name, blurb, environment, round_seconds=0.0, hide_seconds=0.0, kill_y=-20.0):
        self.doc = {
            "format": 1, "kind": "prophunt_map", "id": mid, "name": name, "author": "mg-prop-hunt",
            "blurb": blurb, "environment": environment, "kill_y": kill_y,
        }
        if round_seconds:
            self.doc["round_seconds"] = round_seconds
        if hide_seconds:
            self.doc["hide_seconds"] = hide_seconds
        self.boxes = []
        self.props = []
        self.lights = []
        self.spawns = {"props": [], "hunters": []}

    # --- Boxes -------------------------------------------------------------

    def box(self, at, size, mat, tint=None, yaw=0.0, solid=True, visible=True):
        b = {"at": v3(*at), "size": v3(*size), "mat": mat}
        if tint is not None:
            b["tint"] = [r(c) for c in tint]
        if yaw:
            b["yaw"] = r(yaw)
        if not solid:
            b["solid"] = False
        if not visible:
            b["visible"] = False
        self.boxes.append(b)
        return b

    def span(self, x0, x1, y0, y1, z0, z1, mat, tint=None, solid=True):
        """A box from its corners."""
        if x1 - x0 <= 0.001 or y1 - y0 <= 0.001 or z1 - z0 <= 0.001:
            return None
        return self.box(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2), (x1 - x0, y1 - y0, z1 - z0), mat, tint, solid=solid)

    def floor(self, x0, z0, x1, z1, mat, tint=None, y=0.0, thick=0.2):
        return self.span(x0, x1, y - thick, y, z0, z1, mat, tint)

    def floor_around(self, x0, z0, x1, z1, hole, mat, tint=None, y=0.0, thick=0.2):
        """A floor with a rectangle (hx0, hz0, hx1, hz1) left out of it, as four boxes: a pool
        or a pond sunk into ground that would otherwise cover it."""
        hx0, hz0, hx1, hz1 = hole
        for a, b, c, d in ((x0, z0, x1, hz0), (x0, hz1, x1, z1), (x0, hz0, hx0, hz1), (hx1, hz0, x1, hz1)):
            self.floor(a, b, c, d, mat, tint, y, thick)

    def ceiling(self, x0, z0, x1, z1, height, mat="ceiling", tint=None, roof="roof", roof_tint=None, eave=0.3):
        self.span(x0, x1, height, height + 0.1, z0, z1, mat, tint)
        if roof:
            self.span(x0 - eave, x1 + eave, height + 0.1, height + 0.35, z0 - eave, z1 + eave, roof, roof_tint)

    def overlay(self, x0, z0, x1, z1, mat, tint=None, y=0.0):
        """Paint on a floor: a line, a path. Drawn a hair above it and solid to nothing."""
        return self.span(x0, x1, y + 0.002, y + 0.012, z0, z1, mat, tint, solid=False)

    def wall(self, a, b, along, mat, tint=None, height=3.2, y0=0.0, thick=WALL, holes=(), glass=True,
             inner=None, inner_tint=None):
        """A wall along x (along='x', at z=b[1]) or z (along='z', at x=b[0]) from a to b.

        a and b are (x, z). holes are (from, to, bottom, top) measured along the wall from a.
        A hole with bottom > 0 is a window and gets a pane of glass. `inner` is the material
        of a second, thinner layer on the room side (+z for an x wall, +x for a z wall); an
        exterior wall is brick outside and paint inside.
        """
        if along == "x":
            start, end = min(a[0], b[0]), max(a[0], b[0])
            fixed = a[1]
        else:
            start, end = min(a[1], b[1]), max(a[1], b[1])
            fixed = a[0]

        length = end - start
        cuts = sorted(holes, key=lambda h: h[0])
        layers = [(mat, tint, thick, 0.0)]
        if inner:
            layers = [(mat, tint, thick - 0.06, -0.03), (inner, inner_tint, 0.06, (thick - 0.06) / 2.0)]

        for lmat, ltint, lthick, shift in layers:
            pos = 0.0
            for h0, h1, hb, ht in cuts:
                self._wall_piece(along, start + pos, start + h0, fixed + shift, y0, y0 + height, lthick, lmat, ltint)
                if hb > 0.0:
                    self._wall_piece(along, start + h0, start + h1, fixed + shift, y0, y0 + hb, lthick, lmat, ltint)
                if ht < height:
                    self._wall_piece(along, start + h0, start + h1, fixed + shift, y0 + ht, y0 + height, lthick, lmat, ltint)
                pos = h1
            self._wall_piece(along, start + pos, start + length, fixed + shift, y0, y0 + height, lthick, lmat, ltint)

        if glass:
            for h0, h1, hb, ht in cuts:
                if hb > 0.0:
                    self._wall_piece(along, start + h0, start + h1, fixed, y0 + hb, y0 + ht, 0.04, "glass", None)

    def _wall_piece(self, along, p0, p1, fixed, y0, y1, thick, mat, tint):
        if p1 - p0 <= 0.001 or y1 - y0 <= 0.001:
            return
        if along == "x":
            self.span(p0, p1, y0, y1, fixed - thick / 2, fixed + thick / 2, mat, tint)
        else:
            self.span(fixed - thick / 2, fixed + thick / 2, y0, y1, p0, p1, mat, tint)

    # --- Props, lights, spawns ----------------------------------------------

    def prop(self, pid, x, z, yaw=0.0, y=0.0, pitch=0.0, roll=0.0):
        size_of(pid)
        p = {"id": pid, "at": v3(x, y, z)}
        if yaw:
            p["yaw"] = r(yaw)
        if pitch:
            p["pitch"] = r(pitch)
        if roll:
            p["roll"] = r(roll)
        self.props.append(p)
        return p

    def on(self, pid, under, dx=0.0, dz=0.0, yaw=0.0):
        """A prop standing on top of another one, `under` a prop dict this built."""
        top = under["at"][1] + size_of(under["id"])[1]
        return self.prop(pid, under["at"][0] + dx, under["at"][2] + dz, yaw, y=top)

    def against(self, pid, side, wall_at, along, gap=0.04, y=0.0):
        """A prop with its back to a wall. side is where the wall is: 'n' (smaller z), 's', 'w'
        (smaller x), 'e'. wall_at is the wall's inner face; along is the other coordinate."""
        yaw = {"n": 0.0, "s": 180.0, "w": 90.0, "e": -90.0}[side]
        w, d = footprint(pid, yaw)
        if side == "n":
            return self.prop(pid, along, wall_at + d / 2 + gap, yaw, y)
        if side == "s":
            return self.prop(pid, along, wall_at - d / 2 - gap, yaw, y)
        if side == "w":
            return self.prop(pid, wall_at + w / 2 + gap, along, yaw, y)
        return self.prop(pid, wall_at - w / 2 - gap, along, yaw, y)

    def light(self, x, y, z, rng=9.0, energy=1.0, colour=(1.0, 0.95, 0.85)):
        self.lights.append({"at": v3(x, y, z), "range": r(rng), "energy": r(energy), "colour": [r(c) for c in colour]})

    def spawn(self, side, x, z, yaw=0.0, y=0.05):
        self.spawns[side].append({"at": v3(x, y, z), "yaw": r(yaw)})

    def lamp_panel(self, x, z, height, w=1.2, d=0.4):
        """A ceiling light fitting: drawn, not solid, with the light under it."""
        self.span(x - w / 2, x + w / 2, height - 0.06, height, z - d / 2, z + d / 2, "flat", (2.6, 2.6, 2.4), solid=False)

    # --- Rooms ----------------------------------------------------------------

    def room(self, x0, z0, x1, z1, floor_mat, floor_tint=None, height=3.2, ceiling="ceiling", roof="roof",
             lights=1, energy=1.1, roof_tint=None, eave=0.3):
        """A floor, a ceiling over it with a roof above, and its lamps. Walls are separate.

        `eave` is how far the roof reaches past the room: over the walls, and out past the
        building. A room beside a taller one wants less, or its roof shows inside the other."""
        self.floor(x0, z0, x1, z1, floor_mat, floor_tint)
        if ceiling:
            self.ceiling(x0, z0, x1, z1, height, ceiling, None, roof, roof_tint, eave)
        cols = max(1, int(round((x1 - x0) / 8.0)))
        rows = max(1, int(round((z1 - z0) / 8.0)))
        if lights:
            for i in range(cols):
                for j in range(rows):
                    lx = x0 + (i + 0.5) * (x1 - x0) / cols
                    lz = z0 + (j + 0.5) * (z1 - z0) / rows
                    self.lamp_panel(lx, lz, height)
                    self.light(lx, height - 0.5, lz, rng=max(x1 - x0, z1 - z0) / max(cols, rows) * 1.25 + 3.0, energy=energy)

    # --- Spawns ----------------------------------------------------------------

    PERSON = 0.45       # a person's radius and a little, for "is this spawn clear"
    PERSON_H = 1.9

    def _blocked(self, x, y, z):
        """What a person standing at (x, y, z) would be inside, or None. Props by their turned
        footprint, solid boxes by their extent; both as axis-aligned rectangles, which is
        generous for a turned one and so errs towards moving a spawn that was fine."""
        lo, hi = y + 0.1, y + self.PERSON_H
        for p in self.props:
            sx, sy, sz = size_of(p["id"])
            yaw = math.radians(p.get("yaw", 0.0))
            w = abs(sx * math.cos(yaw)) + abs(sz * math.sin(yaw))
            d = abs(sx * math.sin(yaw)) + abs(sz * math.cos(yaw))
            py = p["at"][1]
            if py + sy < lo or py > hi:
                continue
            if abs(x - p["at"][0]) < w / 2 + self.PERSON and abs(z - p["at"][2]) < d / 2 + self.PERSON:
                return p["id"]
        for b in self.boxes:
            if b.get("solid", True) is False:
                continue
            (bx, by, bz), (sx, sy, sz) = b["at"], b["size"]
            if by + sy / 2 < lo or by - sy / 2 > hi:
                continue
            if b.get("yaw"):
                yaw = math.radians(b["yaw"])
                sx, sz = abs(sx * math.cos(yaw)) + abs(sz * math.sin(yaw)), abs(sx * math.sin(yaw)) + abs(sz * math.cos(yaw))
            if abs(x - bx) < sx / 2 + self.PERSON and abs(z - bz) < sz / 2 + self.PERSON:
                return "a %s box" % b["mat"]
        return None

    def _floored(self, x, y, z):
        for b in self.boxes:
            if b.get("solid", True) is False:
                continue
            (bx, by, bz), (sx, sy, sz) = b["at"], b["size"]
            if abs(by + sy / 2 - y) < 0.12 and abs(x - bx) < sx / 2 - 0.1 and abs(z - bz) < sz / 2 - 0.1:
                return True
        return False

    def _settle_spawns(self):
        """Moves every spawn that a prop or a wall stands on to the nearest clear spot.

        A spawn is written where a room's plan says the players start, before the room is
        furnished, and a sofa put down later lands on it: mg-prop-hunt's headless_maps found
        six (a living room's corner sofa, an office desk, a hallway bench). Moving them here
        rather than by hand keeps a re-furnished room right without anybody remembering to."""
        for side, spots in self.spawns.items():
            for i, s in enumerate(spots):
                x, y, z = s["at"]
                what = self._blocked(x, y, z)
                if what is None:
                    continue
                found = None
                for ring in range(1, 9):
                    step = ring * 0.5
                    for k in range(8 * ring):
                        a = 2.0 * math.pi * k / (8 * ring)
                        cx, cz = x + step * math.cos(a), z + step * math.sin(a)
                        if self._blocked(cx, y, cz) is None and self._floored(cx, y, cz):
                            found = (cx, cz)
                            break
                    if found:
                        break
                if found is None:
                    raise SystemExit("%s: %s spawn %d at %s is inside %s and nothing near it is clear" % (self.doc["id"], side, i, s["at"], what))
                s["at"] = v3(found[0], y, found[1])
                print("  %s: %s spawn %d moved off %s to %s" % (self.doc["id"], side, i, what, s["at"]), file=sys.stderr)

    def _face_spawns(self):
        """Turns every spawn towards the middle of the map's furniture.

        The game's yaw 0 looks along -Z and its formula is yaw = atan2(-dx, -dz); the
        hand-written yaws assumed the other way round, and a browser render found a hunter
        released on Willow Lane's street looking at empty sky. Every map but the school had
        most of its spawns facing away. Computed rather than written per spawn, so a moved
        spawn or a re-furnished room cannot point the wrong way again."""
        if not self.props:
            return
        cx = sum(p["at"][0] for p in self.props) / len(self.props)
        cz = sum(p["at"][2] for p in self.props) / len(self.props)
        for spots in self.spawns.values():
            for s in spots:
                dx, dz = cx - s["at"][0], cz - s["at"][2]
                if abs(dx) + abs(dz) > 0.01:
                    s["yaw"] = r(math.degrees(math.atan2(-dx, -dz)))

    def build(self):
        self._settle_spawns()
        self._face_spawns()
        self.doc["boxes"] = self.boxes
        self.doc["props"] = self.props
        self.doc["lights"] = self.lights
        self.doc["spawns"] = self.spawns
        return self.doc


# --- Furnishing ------------------------------------------------------------------

def classroom(m, x0, z0, x1, z1, board_side="n", tint=(0.92, 0.95, 1.0)):
    """Rows of desks facing a board, a teacher's desk, bookcases at the back."""
    # The board on the front wall, as a box on it.
    if board_side == "n":
        m.span((x0 + x1) / 2 - 2.6, (x0 + x1) / 2 + 2.6, 0.9, 2.2, z0, z0 + 0.06, "chalkboard")
        teacher = m.against("furniture_desk", "n", z0 + 1.3, (x0 + x1) / 2 + 2.0)
        m.prop("furniture_chair_desk", (x0 + x1) / 2 + 2.0, z0 + 0.7, 180.0)
        m.on("furniture_computer_screen", teacher, dx=-0.3, yaw=0.0)
        m.on("furniture_books", teacher, dx=0.5)
        cols = 4
        rows = 4
        width = (x1 - x0) - 3.0
        for i in range(cols):
            for j in range(rows):
                cx = x0 + 1.5 + (i + 0.5) * width / cols
                cz = z0 + 3.6 + j * 2.0
                if cz > z1 - 2.6:
                    continue
                desk = m.prop("furniture_side_table", cx, cz, 180.0)
                m.prop("furniture_chair", cx, cz + 0.8, 180.0)
                if (i + j) % 3 == 0:
                    m.on("furniture_books", desk, dx=-0.3)
                elif (i * 7 + j) % 5 == 0:
                    m.on("furniture_laptop", desk, dx=0.2)
        # The back: bookcases, a plant, a bin, boxes.
        m.against("furniture_bookcase_open", "s", z1, x0 + 1.2)
        m.against("furniture_bookcase_closed_doors", "s", z1, x0 + 2.3)
        m.against("furniture_bookcase_open_low", "s", z1, x1 - 2.0)
        m.against("furniture_potted_plant", "s", z1, x1 - 0.6)
        m.prop("furniture_trashcan", x0 + 0.5, z0 + 0.6)
        m.prop("furniture_cardboard_box_closed", x1 - 0.5, z0 + 0.6, 15.0)
        m.prop("furniture_coat_rack_standing", x0 + 0.5, z1 - 3.5)
        m.prop("furniture_speaker_small", x1 - 0.4, z0 + 1.6, -90.0)


def library(m, x0, z0, x1, z1):
    """Stacks of shelves, tables between them, a reading corner."""
    for row in range(3):
        z = z0 + 2.2 + row * 2.6
        for i in range(4):
            x = x0 + 1.4 + i * 1.0
            m.prop("furniture_bookcase_open" if (i + row) % 2 else "furniture_bookcase_closed", x, z, 0.0)
            m.prop("furniture_bookcase_open", x, z + 0.62, 180.0)
    for t in range(3):
        tz = z0 + 2.5 + t * 3.0
        table = m.prop("furniture_table", x1 - 4.5, tz)
        m.prop("furniture_chair", x1 - 5.1, tz - 0.8, 0.0)
        m.prop("furniture_chair", x1 - 3.9, tz - 0.8, 0.0)
        m.prop("furniture_chair", x1 - 5.1, tz + 0.8, 180.0)
        m.prop("furniture_chair", x1 - 3.9, tz + 0.8, 180.0)
        m.on("furniture_books", table, dx=-0.4)
        m.on("furniture_lamp_square_table", table, dx=0.6)
    m.prop("furniture_rug_round", x1 - 1.6, z1 - 2.0)
    m.prop("furniture_lounge_chair", x1 - 1.0, z1 - 1.0, -135.0)
    m.prop("furniture_lounge_chair_relax", x1 - 2.6, z1 - 1.0, 180.0)
    m.prop("furniture_lamp_round_floor", x1 - 0.4, z1 - 2.6)
    m.prop("furniture_potted_plant", x0 + 0.5, z1 - 0.5)
    m.prop("furniture_potted_plant", x1 - 0.5, z0 + 0.5)
    desk = m.against("furniture_desk_corner", "s", z1, x0 + 1.6)
    m.prop("furniture_chair_desk", x0 + 2.0, z1 - 1.7, 0.0)
    m.on("furniture_computer_screen", desk, dx=-0.2, dz=0.3, yaw=180.0)


def cafeteria(m, x0, z0, x1, z1, kitchen_x):
    """Long tables with stools, and a kitchen behind a counter."""
    for i in range(3):
        for j in range(3):
            tx = x0 + 3.0 + i * 4.6
            tz = z0 + 2.6 + j * 3.4
            if tx > kitchen_x - 2.5:
                continue
            m.prop("furniture_table_cross", tx, tz, 90.0)
            for k in (-0.6, 0.6):
                m.prop("furniture_stool_bar_square", tx - 0.95, tz + k)
                m.prop("furniture_stool_bar_square", tx + 0.95, tz + k)
    # The counter between the hall and the kitchen, with a gap to walk through.
    for k in range(5):
        z = z0 + 1.5 + k * 1.0
        m.prop("furniture_kitchen_bar", kitchen_x, z, 90.0)
    stove = m.against("furniture_kitchen_stove", "e", x1, z0 + 1.2)
    m.against("furniture_kitchen_stove_electric", "e", x1, z0 + 2.23)
    sink = m.against("furniture_kitchen_sink", "e", x1, z0 + 3.26)
    cab = m.against("furniture_kitchen_cabinet", "e", x1, z0 + 4.29)
    m.against("furniture_kitchen_fridge_large", "e", x1, z0 + 5.6)
    m.against("furniture_kitchen_fridge_large", "e", x1, z0 + 6.85)
    m.on("furniture_kitchen_microwave", cab)
    m.on("furniture_toaster", sink, dz=0.3)
    m.on("furniture_kitchen_coffee_machine", stove, dz=0.3)
    m.prop("furniture_cardboard_box_closed", kitchen_x + 2.5, z1 - 0.6)
    m.prop("furniture_cardboard_box_open", kitchen_x + 3.2, z1 - 0.6, 20.0)
    m.prop("furniture_cardboard_box_closed", kitchen_x + 2.5, z1 - 0.6, 5.0).update({"at": v3(kitchen_x + 2.5, 0.65, z1 - 0.6)})
    m.prop("furniture_trashcan", x0 + 0.5, z1 - 0.5)
    m.prop("furniture_trashcan", kitchen_x - 0.9, z0 + 0.6)


def bathroom(m, x0, z0, x1, z1, stalls=2):
    """Stalls with toilets along the back, sinks along the side."""
    stall_w = 1.5
    for i in range(stalls):
        sx = x0 + 0.2 + i * stall_w
        m.prop("furniture_toilet", sx + stall_w / 2, z1 - 0.6, 180.0)
        if i > 0:
            m.span(sx - 0.03, sx + 0.03, 0.15, 2.0, z1 - 1.7, z1, "metal", (0.75, 0.82, 0.88))
    m.span(x0 + 0.2 + stalls * stall_w - 0.03, x0 + 0.2 + stalls * stall_w + 0.03, 0.15, 2.0, z1 - 1.7, z1, "metal", (0.75, 0.82, 0.88))
    for i in range(2):
        m.against("furniture_bathroom_sink_square", "e", x1, z0 + 1.0 + i * 1.2)
    m.prop("furniture_trashcan", x1 - 0.4, z0 + 3.4)
    m.against("furniture_bathroom_cabinet", "w", x0, z0 + 0.8)


def lockers(m, x0, x1, z, side, height=2.0):
    """A row of lockers against a corridor wall: part of the wall, not something to hide as."""
    depth = 0.45
    if side == "n":
        m.span(x0, x1, 0.0, height, z, z + depth, "locker")
    else:
        m.span(x0, x1, 0.0, height, z - depth, z, "locker")


def tree_ring(m, x0, z0, x1, z1, step, kinds, seed):
    """Trees along the edge of an area, a little irregular."""
    i = 0
    for x in frange(x0, x1, step):
        for z in (z0, z1):
            kind = kinds[(i * 7 + seed) % len(kinds)]
            m.prop(kind, x + ((i * 37) % 5 - 2) * 0.3, z + ((i * 53) % 5 - 2) * 0.3, (i * 47) % 360)
            i += 1
    for z in frange(z0 + step, z1 - step + 0.01, step):
        for x in (x0, x1):
            kind = kinds[(i * 7 + seed) % len(kinds)]
            m.prop(kind, x + ((i * 37) % 5 - 2) * 0.3, z + ((i * 53) % 5 - 2) * 0.3, (i * 47) % 360)
            i += 1


def frange(a, b, step):
    out = []
    v = a
    while v <= b + 1e-6:
        out.append(v)
        v += step
    return out


def fence(m, x0, z0, x1, z1, height=1.6, mat="wood", tint=(0.62, 0.48, 0.34), gaps=()):
    """A fence round an area, as thin walls with gates (gaps: (side, from, to))."""
    sides = {
        "n": ((x0, z0), (x1, z0), "x"),
        "s": ((x0, z1), (x1, z1), "x"),
        "w": ((x0, z0), (x0, z1), "z"),
        "e": ((x1, z0), (x1, z1), "z"),
    }
    for side, (a, b, along) in sides.items():
        holes = [(g0, g1, 0.0, height) for gs, g0, g1 in gaps if gs == side]
        m.wall(a, b, along, mat, tint, height=height, thick=0.12, holes=holes, glass=False)


# --- The maps ----------------------------------------------------------------------

def school():
    m = Map(
        "ph_school", "Maple Grove School",
        "A whole primary school and its grounds: classrooms, a library, the cafeteria, a gym, the playground and the field.",
        {
            "sky_top": [0.30, 0.52, 0.85], "sky_horizon": [0.74, 0.84, 0.94], "ground": [0.30, 0.36, 0.25],
            "sun_pitch": -50.0, "sun_yaw": 35.0, "sun_energy": 0.95, "sun_colour": [1.0, 0.96, 0.88],
            "ambient": [0.66, 0.70, 0.76], "ambient_energy": 0.42,
        },
        round_seconds=360.0, hide_seconds=40.0,
    )
    H = 3.4
    BRICK = (0.86, 0.62, 0.52)
    # The building is x -40..40, z -16..16; the hallway runs east-west on z -2..2.
    X0, X1, Z0, Z1 = -40.0, 40.0, -16.0, 16.0

    # --- The grounds ---
    m.floor(-75.0, -60.0, 75.0, 55.0, "grass", y=-0.02, thick=0.3)
    # The road, the parking lot and the walks.
    m.overlay(-75.0, -58.0, 75.0, -50.0, "asphalt", y=-0.02)
    for x in frange(-70.0, 70.0, 8.0):
        m.overlay(x, -54.1, x + 4.0, -53.9, "line", (1.0, 0.9, 0.4), y=-0.01)
    m.overlay(8.0, -48.0, 54.0, -22.0, "asphalt", y=-0.02)
    for x in frange(10.0, 52.0, 3.0):
        m.overlay(x - 0.06, -47.0, x + 0.06, -41.0, "line", y=-0.01)
        m.overlay(x - 0.06, -29.0, x + 0.06, -23.0, "line", y=-0.01)
    m.overlay(-3.0, -50.0, 3.0, Z0, "sidewalk", y=-0.02)
    m.overlay(-42.0, -24.0, 42.0, -19.0, "sidewalk", y=-0.02)
    m.overlay(-46.0, -24.0, -41.0, 26.0, "sidewalk", y=-0.02)
    m.overlay(41.0, -24.0, 46.0, 26.0, "sidewalk", y=-0.02)
    # The playground (rubber) and the field (grass with lines).
    m.overlay(-40.0, 20.0, -6.0, 46.0, "rubber", y=-0.02)
    m.overlay(2.0, 20.0, 60.0, 50.0, "grass", (0.85, 1.05, 0.8), y=-0.015)
    for x in (2.2, 59.8):
        m.overlay(x - 0.1, 20.2, x + 0.1, 49.8, "line", y=-0.01)
    for z in (20.2, 49.8):
        m.overlay(2.2, z - 0.1, 59.8, z + 0.1, "line", y=-0.01)
    m.overlay(30.9, 20.2, 31.1, 49.8, "line", y=-0.01)
    # A flagpole by the front walk.
    m.span(-5.2, -4.95, 0.0, 9.0, -27.2, -26.95, "metal")
    m.span(-5.05, -3.4, 7.6, 8.6, -27.1, -27.08, "fabric", (0.2, 0.35, 0.8), solid=False)
    # Goalposts on the field.
    for gx in (3.2, 58.8):
        m.span(gx - 0.08, gx + 0.08, 0.0, 2.4, 31.5, 31.66, "metal", (1.4, 1.4, 1.4))
        m.span(gx - 0.08, gx + 0.08, 0.0, 2.4, 38.34, 38.5, "metal", (1.4, 1.4, 1.4))
        m.span(gx - 0.08, gx + 0.08, 2.32, 2.48, 31.5, 38.5, "metal", (1.4, 1.4, 1.4))
    # Play equipment: a climbing frame and slides as boxes.
    for k in range(3):
        bx = -32.0 + k * 9.0
        m.span(bx - 1.5, bx + 1.5, 1.4, 1.6, 30.0, 33.0, "wood", (0.85, 0.5, 0.3))
        for cx, cz in ((-1.4, 0.1), (1.3, 0.1), (-1.4, 2.8), (1.3, 2.8)):
            m.span(bx + cx, bx + cx + 0.12, 0.0, 2.6, 30.0 + cz, 30.0 + cz + 0.12, "metal", (0.9, 0.3, 0.25))
        m.box((bx + 2.6, 0.75, 31.5), (2.8, 0.12, 1.0), "metal", (0.3, 0.6, 0.95))
    # A sandpit.
    m.overlay(-20.0, 38.0, -12.0, 44.0, "sand", y=-0.01)

    # Trees round the grounds, bushes along the building, rocks and flowers.
    tree_ring(m, -70.0, -40.0, 70.0, 52.0, 9.0,
              ["nature_tree_oak", "nature_tree_default", "nature_tree_pine_round_a", "nature_tree_fat", "nature_tree_detailed"], 3)
    for x in frange(-38.0, -8.0, 4.0):
        m.prop("nature_plant_bush", x, Z0 - 1.6, (x * 13) % 360)
    for x in frange(8.0, 38.0, 4.0):
        m.prop("nature_plant_bush_detailed", x, Z0 - 1.8, (x * 17) % 360)
    for i, (x, z) in enumerate(((-12.0, -30.0), (-18.0, -34.0), (14.0, -18.5), (-28.0, -30.0), (22.0, 18.0))):
        m.prop(["nature_tree_oak", "nature_tree_default_fall", "nature_tree_small", "nature_tree_cone", "nature_tree_plateau"][i], x, z, i * 60.0)
    for x, z, kind in ((-7.0, -22.0, "nature_flower_red_a"), (7.0, -22.0, "nature_flower_yellow_a"), (-8.0, -23.0, "nature_flower_purple_a"),
                       (8.5, -23.0, "nature_flower_red_a"), (-44.0, 30.0, "nature_rock_small_a"), (-2.0, 40.0, "nature_rock_large_a"),
                       (63.0, 10.0, "nature_rock_tall_a"), (-60.0, -10.0, "nature_stump_round"), (-62.0, 8.0, "nature_log"),
                       (55.0, -10.0, "nature_stone_small_a"), (-48.0, -32.0, "nature_grass_large"), (48.0, 12.0, "nature_grass_large")):
        m.prop(kind, x, z, (x * 7 + z * 3) % 360)
    # Benches, picnic tables and bins outside.
    for x, z, yaw in ((-6.0, -21.0, 0.0), (6.0, -21.0, 0.0), (-44.0, 5.0, 90.0), (44.0, 5.0, -90.0), (-1.0, 24.0, 180.0)):
        m.prop("furniture_bench", x, z, yaw)
    for x, z in ((-38.0, 24.0), (-30.0, 24.0), (-22.0, 24.0), (-14.0, 24.0)):
        m.prop("furniture_table_cross_cloth" if x == -30.0 else "furniture_table_cross", x, z)
    for x, z in ((-8.0, -20.0), (9.0, -20.0), (-41.5, 24.0), (41.5, 24.0), (0.0, 46.0)):
        m.prop("furniture_trashcan", x, z)
    m.prop("nature_sign", -6.5, -40.0, 30.0)
    m.prop("nature_pot_large", -4.0, Z0 - 1.5)
    m.prop("nature_pot_large", 4.0, Z0 - 1.5)

    # --- The building ---
    # Rooms north of the hallway (z 2..16): classroom A, classroom B, library, cafeteria.
    m.room(-40.0, 2.0, -24.0, 16.0, "tile_check", height=H)
    m.room(-24.0, 2.0, -8.0, 16.0, "tile_check", (1.0, 0.95, 0.85), height=H)
    m.room(-8.0, 2.0, 12.0, 16.0, "carpet", (0.55, 0.35, 0.3), height=H)
    m.room(12.0, 2.0, 40.0, 16.0, "tile", height=H, energy=1.2)
    # The hallway.
    m.room(-40.0, -2.0, 40.0, 2.0, "tile", (0.95, 0.95, 0.9), height=H, energy=0.9, eave=WALL / 2)
    # South of the hallway (z -16..-2): gym, bathrooms, nurse, principal, lobby, lounge, classrooms C and D.
    m.room(-40.0, -16.0, -22.0, -2.0, "gym", height=6.0, energy=1.4)
    m.room(-22.0, -16.0, -16.0, -2.0, "tile_small", height=H, eave=WALL / 2)
    m.room(-16.0, -16.0, -10.0, -2.0, "tile_small", (0.95, 0.9, 1.0), height=H)
    m.room(-10.0, -16.0, -4.0, -2.0, "carpet", (0.8, 0.85, 0.7), height=H)
    m.room(-4.0, -16.0, 4.0, -2.0, "parquet", height=H)
    m.room(4.0, -16.0, 12.0, -2.0, "carpet", (0.9, 0.6, 0.5), height=H)
    m.room(12.0, -16.0, 26.0, -2.0, "tile_check", (0.9, 1.0, 0.95), height=H)
    m.room(26.0, -16.0, 40.0, -2.0, "tile_check", (1.0, 0.92, 0.85), height=H)

    # Exterior walls, with windows, the front doors in the lobby and the doors at each end.
    def windows(start, end, every=4.0, width=2.0, skip=()):
        out = []
        for c in frange(start + every / 2, end - every / 2, every):
            if any(abs(c - s) < 2.0 for s in skip):
                continue
            out.append((c - width / 2 - start, c + width / 2 - start, SILL, HEAD))
        return out

    m.wall((X0, Z0), (X1, Z0), "x", "brick", BRICK, height=H, thick=OUTER, inner="paint",
           holes=windows(X0, X1, skip=(-31.0, -26.0, 0.0)) + [(40.0 - 1.4, 40.0 + 1.4, 0.0, 2.6)])
    m.wall((X0, Z1), (X1, Z1), "x", "brick", BRICK, height=H, thick=OUTER, inner=None,
           holes=windows(X0, X1, skip=(26.0, 34.0)) + [(26.0 - 0.7 + 40.0, 26.0 + 0.7 + 40.0, 0.0, DOOR_H), (34.0 - 0.7 + 40.0, 34.0 + 0.7 + 40.0, 0.0, DOOR_H)])
    m.wall((X0, Z0), (X0, Z1), "z", "brick", BRICK, height=H, thick=OUTER,
           holes=[(16.0 - 1.2, 16.0 + 1.2, 0.0, 2.6)])
    m.wall((X1, Z0), (X1, Z1), "z", "brick", BRICK, height=H, thick=OUTER,
           holes=[(16.0 - 1.2, 16.0 + 1.2, 0.0, 2.6)])
    # The gym is taller: its walls go on up, and its own roof.
    m.span(X0 - OUTER / 2, X0 + OUTER / 2, H, 6.0, Z0, -2.0, "brick", BRICK)
    m.span(-22.0 - WALL / 2, -22.0 + WALL / 2, H, 6.0, Z0, -2.0, "brick", BRICK)
    m.span(X0, -22.0, H, 6.0, Z0 - OUTER / 2, Z0 + OUTER / 2, "brick", BRICK)
    m.span(X0, -22.0, H, 6.0, -2.0 - WALL / 2, -2.0 + WALL / 2, "brick", BRICK)
    # Painted on the inside, all the way up: brick above a painted wall reads as a missing wall.
    PAINT = (0.95, 0.95, 0.92)
    m.span(X0 + OUTER / 2, X0 + OUTER / 2 + 0.04, 0.0, 6.0, Z0 + OUTER / 2, -2.0 - WALL / 2, "paint", PAINT, solid=False)
    m.span(-22.0 - WALL / 2 - 0.04, -22.0 - WALL / 2, H, 6.0, Z0 + OUTER / 2, -2.0 - WALL / 2, "paint", PAINT, solid=False)
    m.span(X0 + OUTER / 2, -22.0 - WALL / 2, H, 6.0, Z0 + OUTER / 2, Z0 + OUTER / 2 + 0.04, "paint", PAINT, solid=False)
    m.span(X0 + OUTER / 2, -22.0 - WALL / 2, H, 6.0, -2.0 - WALL / 2 - 0.04, -2.0 - WALL / 2, "paint", PAINT, solid=False)

    # The hallway's walls, with a door into every room.
    north_doors = [(-32.0, "A"), (-16.0, "B"), (-4.0, "L"), (6.0, "L2"), (20.0, "C1"), (32.0, "C2")]
    m.wall((X0, 2.0), (X1, 2.0), "x", "paint", (0.95, 0.93, 0.85), height=H,
           holes=[(x - DOOR_W / 2 - X0, x + DOOR_W / 2 - X0, 0.0, DOOR_H) for x, _ in north_doors]
           + [(14.0 - X0, 18.0 - X0, 1.0, 2.2)])
    south_doors = [(-31.0, 2.4), (-19.0, DOOR_W), (-13.0, DOOR_W), (-7.0, DOOR_W), (0.0, 3.0), (8.0, DOOR_W), (19.0, DOOR_W), (33.0, DOOR_W)]
    m.wall((X0, -2.0), (X1, -2.0), "x", "paint", (0.95, 0.93, 0.85), height=H,
           holes=[(x - w / 2 - X0, x + w / 2 - X0, 0.0, DOOR_H if w < 2.0 else 2.8) for x, w in south_doors])
    # Walls between the rooms.
    for x in (-24.0, -8.0, 12.0):
        m.wall((x, 2.0), (x, Z1), "z", "paint", (0.95, 0.95, 0.92), height=H)
    for x in (-22.0, -16.0, -10.0, -4.0, 4.0, 12.0, 26.0):
        holes = []
        if x == -4.0 or x == 4.0:
            holes = [(4.0, 6.0, 1.0, 2.2)]
        m.wall((x, Z0), (x, -2.0), "z", "paint", (0.95, 0.95, 0.92), height=H, holes=holes)
    # The front doors into the lobby, on the south wall.
    lobby_door = (0.0 - 1.4 - X0, 0.0 + 1.4 - X0, 0.0, 2.6)
    # (The south exterior wall above already has a window run; the doors are cut here.)
    m.boxes = [b for b in m.boxes if not (abs(b["at"][2] - Z0) < 0.3 and -1.4 < b["at"][0] < 1.4 and b["at"][1] < 2.6 and b["mat"] in ("brick", "paint", "glass"))]
    m.span(-1.4, 1.4, 2.6, H, Z0 - OUTER / 2, Z0 + OUTER / 2, "brick", BRICK)
    # A canopy over the front doors.
    m.span(-3.5, 3.5, 3.0, 3.2, Z0 - 3.5, Z0, "concrete")
    m.span(-3.4, -3.2, 0.0, 3.0, Z0 - 3.4, Z0 - 3.2, "metal")
    m.span(3.2, 3.4, 0.0, 3.0, Z0 - 3.4, Z0 - 3.2, "metal")
    _ = lobby_door

    # --- The hallway: lockers, benches, bins ---
    for x0, x1 in ((-39.0, -33.5), (-30.5, -17.5), (-14.5, -5.5), (-2.5, 4.5), (7.5, 13.5), (18.5, 19.2), (21.5, 30.5), (33.5, 39.0)):
        lockers(m, x0, x1, 2.0 - WALL / 2, "s")
    m.prop("furniture_bench_cushion", -11.0, -1.2, 180.0)
    m.prop("furniture_bench_cushion", 15.0, -1.2, 180.0)
    m.prop("furniture_trashcan", -21.0, -1.5)
    m.prop("furniture_trashcan", 24.0, -1.5)
    m.prop("furniture_potted_plant", -38.8, -1.4)
    m.prop("furniture_potted_plant", 38.8, -1.4)
    m.prop("furniture_cardboard_box_closed", 37.0, 1.1, 30.0)
    m.prop("furniture_bathroom_sink", -26.0, -1.6, 0.0)

    # --- Rooms ---
    classroom(m, -40.0 + 0.1, 2.0 + 0.1, -24.0 - 0.1, 16.0 - 0.3, "n")
    classroom(m, -24.0 + 0.1, 2.0 + 0.1, -8.0 - 0.1, 16.0 - 0.3, "n")
    library(m, -8.0 + 0.1, 2.0 + 0.1, 12.0 - 0.1, 16.0 - 0.3)
    cafeteria(m, 12.0 + 0.1, 2.0 + 0.1, 40.0 - 0.3, 16.0 - 0.3, kitchen_x=33.0)

    # The gym: benches along the walls, mats, crates, speakers, hoops.
    for z in (-14.6, -3.4):
        for x in (-36.0, -31.0, -26.0):
            m.prop("furniture_bench", x, z, 0.0 if z < -9 else 180.0)
    for x, z in ((-34.0, -9.0), (-28.0, -9.0)):
        m.prop("furniture_rug_square", x, z)
    m.prop("furniture_cardboard_box_closed", -38.9, -15.0, 10.0)
    m.prop("furniture_cardboard_box_closed", -38.9, -14.3, 30.0)
    m.prop("furniture_cardboard_box_open", -38.2, -15.1, 5.0)
    m.prop("furniture_speaker", -22.6, -15.2, -45.0)
    m.prop("furniture_speaker", -22.6, -2.8, -135.0)
    for z in (-15.75, -2.25):
        m.span(-31.6, -30.4, 3.0, 3.9, z - 0.04, z + 0.04, "flat", (1.6, 1.6, 1.6))
        m.span(-31.25, -30.75, 2.95, 3.0, z - (0.5 if z > -9 else -0.5) - 0.25, z - (0.5 if z > -9 else -0.5) + 0.25, "metal", (1.6, 0.6, 0.3), solid=False)
    m.overlay(-39.6, -9.06, -22.4, -8.94, "line", (1.0, 0.9, 0.3))

    # Bathrooms.
    bathroom(m, -22.0 + 0.1, -16.0 + 0.3, -16.0 - 0.1, -2.0 - 0.1, stalls=2)
    bathroom(m, -16.0 + 0.1, -16.0 + 0.3, -10.0 - 0.1, -2.0 - 0.1, stalls=2)

    # The nurse's room.
    m.against("furniture_bed_single", "w", -10.0 + 0.1, -12.0)
    m.against("furniture_cabinet_bed_drawer", "w", -10.0 + 0.1, -10.2)
    m.against("furniture_bookcase_closed_doors", "e", -4.0 - 0.1, -12.0)
    m.prop("furniture_chair_rounded", -6.0, -6.0, 180.0)
    m.prop("furniture_trashcan", -9.5, -3.0)
    m.prop("furniture_potted_plant", -4.5, -15.4)

    # The lobby: a reception desk, benches, a trophy case, plants, a rug.
    reception = m.prop("furniture_desk", 2.4, -6.0, -90.0)
    m.prop("furniture_chair_desk", 3.3, -6.0, 90.0)
    m.on("furniture_computer_screen", reception, dz=0.3, yaw=-90.0)
    m.prop("furniture_bench_cushion", -3.0, -11.0, 90.0)
    m.prop("furniture_bench_cushion", -3.0, -13.0, 90.0)
    m.against("furniture_bookcase_closed_doors", "e", 4.0 - 0.1, -12.0)
    m.against("furniture_potted_plant", "s", -2.0 - 0.1, -3.4)
    m.prop("furniture_potted_plant", -3.3, -15.2)
    m.prop("furniture_potted_plant", 3.3, -15.2)
    m.prop("furniture_rug_rectangle", 0.0, -10.0, 90.0)

    # The principal's office.
    pdesk = m.prop("furniture_desk_corner", 9.0, -13.5)
    m.prop("furniture_chair_desk", 9.6, -14.7, 0.0)
    m.on("furniture_computer_screen", pdesk, dx=0.2, dz=0.2, yaw=180.0)
    m.on("furniture_lamp_square_table", pdesk, dx=-0.8)
    m.against("furniture_bookcase_closed", "e", 12.0 - 0.1, -6.0)
    m.against("furniture_bookcase_closed_wide", "w", 4.0 + 0.1, -8.0)
    m.prop("furniture_lounge_chair", 6.5, -5.0, 135.0)
    m.prop("furniture_potted_plant", 11.4, -15.4)
    m.prop("furniture_cardboard_box_closed", 5.0, -15.2, 10.0)

    # Classroom C becomes the teachers' lounge, and D a science room.
    m.against("furniture_lounge_sofa_long", "s", -2.0 - 0.1, 18.0)
    m.prop("furniture_table_coffee", 18.0, -5.0)
    m.against("furniture_television_modern", "n", -16.0 + 0.3, 18.0)
    m.against("furniture_kitchen_cabinet", "e", 26.0 - 0.1, -14.5)
    m.against("furniture_kitchen_sink", "e", 26.0 - 0.1, -13.47)
    cofmach = m.against("furniture_kitchen_cabinet_drawer", "e", 26.0 - 0.1, -12.44)
    m.on("furniture_kitchen_coffee_machine", cofmach)
    m.against("furniture_kitchen_fridge", "e", 26.0 - 0.1, -11.2)
    lt = m.prop("furniture_table_round", 15.0, -10.0)
    m.prop("furniture_chair_cushion", 14.0, -10.0, 90.0)
    m.prop("furniture_chair_cushion", 16.0, -10.0, -90.0)
    m.prop("furniture_chair_cushion", 15.0, -11.1, 0.0)
    m.on("furniture_radio", lt)
    m.prop("furniture_lamp_round_floor", 12.5, -15.4)
    m.prop("furniture_potted_plant", 25.4, -2.8)
    classroom(m, 26.0 + 0.1, -16.0 + 0.3, 40.0 - 0.3, -2.0 - 0.1, "n")

    # --- Spawns: the props in the hallway, the hunters on the front walk. ---
    for i in range(12):
        m.spawn("props", -30.0 + i * 5.0, 0.0 if i % 2 == 0 else -0.8, 90.0 if i < 6 else -90.0)
    for i in range(8):
        m.spawn("hunters", -3.0 + (i % 4) * 2.0, -30.0 - (i // 4) * 2.0, 180.0)
    return m.build()


def house():
    m = Map(
        "ph_house", "Willow Lane",
        "A family house with a garage, a garden and a pool, on a quiet street.",
        {
            "sky_top": [0.26, 0.45, 0.80], "sky_horizon": [0.86, 0.78, 0.66], "ground": [0.30, 0.34, 0.24],
            "sun_pitch": -32.0, "sun_yaw": -60.0, "sun_energy": 1.0, "sun_colour": [1.0, 0.86, 0.68],
            "ambient": [0.62, 0.62, 0.70], "ambient_energy": 0.45,
        },
        round_seconds=300.0, hide_seconds=30.0,
    )
    H = 3.0
    # The house is x -12..12, z -10..6; the garage x 12..20; the garden north to z 26; the street south.
    m.floor_around(-40.0, -30.0, 40.0, 32.0, (-6.0, 12.0, 10.0, 22.0), "grass", y=-0.02, thick=0.3)
    m.overlay(-40.0, -30.0, 40.0, -22.0, "asphalt", y=-0.02)
    for x in frange(-38.0, 38.0, 6.0):
        m.overlay(x, -26.1, x + 3.0, -25.9, "line", (1.0, 0.95, 0.9), y=-0.01)
    m.overlay(-40.0, -22.0, 40.0, -19.5, "sidewalk", y=-0.02)
    m.overlay(13.0, -19.5, 19.0, -10.0, "concrete", y=-0.015)
    m.overlay(-1.0, -19.5, 1.0, -10.0, "sidewalk", y=-0.015)

    rooms = [
        (-12.0, -10.0, -2.0, -2.0, "wood_floor", None),      # living room
        (-2.0, -10.0, 4.0, -2.0, "tile", (1.0, 0.97, 0.9)),   # hall and dining
        (4.0, -10.0, 12.0, -2.0, "tile_check", (1.0, 0.95, 0.85)),  # kitchen
        (-12.0, -2.0, -4.0, 6.0, "carpet", (0.6, 0.62, 0.75)),  # main bedroom
        (-4.0, -2.0, 2.0, 6.0, "tile_small", None),           # bathroom
        (2.0, -2.0, 12.0, 6.0, "carpet", (0.65, 0.8, 0.6)),     # kids' bedroom
    ]
    for x0, z0, x1, z1, mat, tint in rooms:
        m.room(x0, z0, x1, z1, mat, tint, height=H, roof="roof", roof_tint=(0.7, 0.45, 0.4))
    m.room(12.0, -10.0, 20.0, 6.0, "concrete", height=H, roof="roof", roof_tint=(0.7, 0.45, 0.4), energy=0.9)

    WHITE = (0.98, 0.96, 0.9)
    SIDING = (0.85, 0.88, 0.95)

    def win(start, centres, width=1.8):
        return [(c - width / 2 - start, c + width / 2 - start, SILL, HEAD) for c in centres]

    # Outside walls: the front with the door and windows, the back with the patio door.
    m.wall((-12.0, -10.0), (12.0, -10.0), "x", "plaster", SIDING, height=H, thick=OUTER, inner="paint", inner_tint=WHITE,
           holes=win(-12.0, (-9.0, -5.0, 8.0)) + [(-0.7 + 12.0, 0.7 + 12.0, 0.0, DOOR_H)])
    m.wall((-12.0, 6.0), (12.0, 6.0), "x", "plaster", SIDING, height=H, thick=OUTER,
           holes=win(-12.0, (-8.0, 7.0)) + [(-2.8 + 12.0, -1.0 + 12.0, 0.0, DOOR_H)])
    m.wall((-12.0, -10.0), (-12.0, 6.0), "z", "plaster", SIDING, height=H, thick=OUTER, holes=win(-10.0, (-6.0, 2.0)))
    m.wall((20.0, -10.0), (20.0, 6.0), "z", "plaster", SIDING, height=H, thick=OUTER, holes=win(-10.0, (2.0,)))
    m.wall((12.0, 6.0), (20.0, 6.0), "x", "plaster", SIDING, height=H, thick=OUTER, holes=[(5.0, 6.4, 0.0, DOOR_H)])
    # The garage door, open.
    m.wall((12.0, -10.0), (20.0, -10.0), "x", "plaster", SIDING, height=H, thick=OUTER, holes=[(1.0, 7.0, 0.0, 2.6)])
    # Inside walls, with doors.
    m.wall((12.0, -10.0), (12.0, 6.0), "z", "paint", WHITE, height=H, holes=[(4.0, 5.4, 0.0, DOOR_H)])
    m.wall((-12.0, -2.0), (12.0, -2.0), "x", "paint", WHITE, height=H,
           holes=[(-8.0 + 12.0 - 0.7, -8.0 + 12.0 + 0.7, 0.0, DOOR_H), (-1.0 + 12.0 - 0.7, -1.0 + 12.0 + 0.7, 0.0, DOOR_H),
                  (6.0 + 12.0 - 0.7, 6.0 + 12.0 + 0.7, 0.0, DOOR_H)])
    m.wall((-2.0, -10.0), (-2.0, -2.0), "z", "paint", WHITE, height=H, holes=[(2.0, 5.0, 0.0, 2.6)])
    m.wall((4.0, -10.0), (4.0, -2.0), "z", "paint", WHITE, height=H, holes=[(3.0, 5.6, 0.0, 2.6)])
    m.wall((-4.0, -2.0), (-4.0, 6.0), "z", "paint", WHITE, height=H)
    m.wall((2.0, -2.0), (2.0, 6.0), "z", "paint", WHITE, height=H)

    # Living room.
    m.against("furniture_lounge_sofa_corner", "w", -12.0 + 0.15, -7.4)
    m.prop("furniture_table_coffee", -9.0, -6.0, 90.0)
    m.prop("furniture_rug_rectangle", -8.6, -6.0, 90.0)
    tv = m.against("furniture_cabinet_television", "e", -2.0 - 0.1, -6.0)
    m.on("furniture_television_modern", tv, yaw=-90.0)
    m.against("furniture_lounge_chair", "n", -10.0 + 0.15, -9.0)
    m.against("furniture_bookcase_open", "s", -2.0 - 0.1, -9.2).update({"yaw": 180.0})
    m.prop("furniture_lamp_round_floor", -11.4, -2.6)
    m.prop("furniture_potted_plant", -3.0, -2.6)
    m.prop("furniture_speaker", -2.6, -9.5)
    m.prop("furniture_pillow_blue", -11.0, -5.0, 90.0)
    m.prop("furniture_radio", -6.0, -9.6)
    # Hall and dining.
    dt = m.prop("furniture_table_cloth", 1.0, -6.5, 90.0)
    for dz in (-0.6, 0.6):
        m.prop("furniture_chair_cushion", 0.0, -6.5 + dz, 90.0)
        m.prop("furniture_chair_cushion", 2.0, -6.5 + dz, -90.0)
    m.on("furniture_plant_small1", dt)
    m.against("furniture_side_table_drawers", "n", -10.0 + 0.15, 1.0)
    m.prop("furniture_coat_rack_standing", -1.4, -9.4)
    m.prop("furniture_rug_doormat", 0.0, -9.6)
    # Kitchen.
    for i, pid in enumerate(("furniture_kitchen_cabinet", "furniture_kitchen_stove", "furniture_kitchen_cabinet_drawer",
                             "furniture_kitchen_sink", "furniture_kitchen_cabinet")):
        p = m.against(pid, "s", -2.0 - 0.1, 5.0 + i * 1.0)
        if i == 0:
            m.on("furniture_kitchen_microwave", p)
        if i == 2:
            m.on("furniture_toaster", p, dx=0.2)
        if i == 4:
            m.on("furniture_kitchen_coffee_machine", p)
    m.against("furniture_kitchen_fridge_large", "e", 12.0 - 0.1, -3.2)
    island = m.prop("furniture_kitchen_bar", 7.5, -6.5)
    m.prop("furniture_kitchen_bar", 8.5, -6.5)
    m.on("furniture_kitchen_blender", island)
    m.prop("furniture_stool_bar", 7.5, -7.4)
    m.prop("furniture_stool_bar", 8.5, -7.4)
    m.prop("furniture_trashcan", 11.4, -9.5)
    # Main bedroom.
    bed = m.against("furniture_bed_double", "w", -12.0 + 0.15, 2.0)
    m.against("furniture_cabinet_bed_drawer_table", "w", -12.0 + 0.15, 4.5)
    m.against("furniture_cabinet_bed_drawer_table", "w", -12.0 + 0.15, -0.5)
    m.prop("furniture_lamp_round_table", -11.6, 4.5, y=0.6)
    m.against("furniture_bookcase_closed_wide", "s", 6.0 - 0.15, -8.0)
    m.prop("furniture_rug_rounded", -7.5, 2.0)
    m.prop("furniture_pillow", -11.3, 2.6, 90.0, y=0.86)
    m.against("furniture_desk", "e", -4.0 - 0.1, 0.5)
    m.prop("furniture_chair_modern_cushion", -5.6, 0.5, 90.0)
    _ = bed
    # Bathroom.
    m.against("furniture_bathtub", "s", 6.0 - 0.15, -1.0)
    m.against("furniture_toilet", "e", 2.0 - 0.1, 2.0)
    m.against("furniture_bathroom_sink", "w", -4.0 + 0.1, 3.5)
    m.against("furniture_bathroom_cabinet_drawer", "w", -4.0 + 0.1, 0.5)
    m.prop("furniture_washer", 1.3, -1.3, -90.0)
    # Kids' bedroom.
    m.against("furniture_bed_bunk", "e", 12.0 - 0.1, 3.5)
    m.prop("furniture_bear", 8.0, 1.5, -150.0)
    m.against("furniture_desk", "s", 6.0 - 0.15, 4.5)
    m.prop("furniture_chair_rounded", 4.5, 4.4, 180.0)
    m.prop("furniture_rug_round", 6.0, 1.0)
    m.against("furniture_bookcase_open_low", "w", 2.0 + 0.1, 0.0)
    m.prop("furniture_cardboard_box_open", 3.0, -1.2, 30.0)
    m.prop("furniture_speaker_small", 11.5, -1.5, -90.0)
    m.prop("furniture_pillow_blue_long", 9.5, 0.5, 40.0)
    # Garage: boxes, shelves, the washing, a workbench.
    m.against("furniture_washer_dryer_stacked", "e", 20.0 - 0.15, 4.8)
    m.against("furniture_bookcase_open", "e", 20.0 - 0.15, 2.8)
    m.against("furniture_bookcase_open", "e", 20.0 - 0.15, 1.7)
    for i, (x, z, yaw) in enumerate(((13.0, 5.2, 10.0), (13.7, 5.3, -15.0), (13.3, 4.5, 40.0), (14.5, 5.3, 5.0))):
        m.prop("furniture_cardboard_box_closed" if i % 2 == 0 else "furniture_cardboard_box_open", x, z, yaw)
    m.prop("furniture_cardboard_box_closed", 13.0, 5.2, 25.0, y=0.65)
    m.against("furniture_side_table_drawers", "w", 12.0 + 0.1, -3.0)
    m.prop("furniture_trashcan", 19.3, -9.3)
    m.prop("furniture_trashcan", 18.6, -9.3)

    # The garden: a deck, a pool, loungers, a fence, trees and beds.
    m.floor(-6.0, 6.0, 10.0, 12.0, "wood", (0.78, 0.62, 0.45), y=0.15, thick=0.3)
    m.floor(-6.0, 12.0, 10.0, 22.0, "tile", (0.85, 0.9, 0.95), y=0.02, thick=0.2)
    # The pool: a hollow lined with tile, water over it not solid.
    m.span(-4.0, 8.0, -1.6, -1.4, 14.0, 20.0, "tile_small", (0.6, 0.85, 1.0))
    for x0, x1, z0, z1 in ((-4.2, -4.0, 14.0, 20.0), (8.0, 8.2, 14.0, 20.0), (-4.2, 8.2, 13.8, 14.0), (-4.2, 8.2, 20.0, 20.2)):
        m.span(x0, x1, -1.6, 0.02, z0, z1, "tile_small", (0.6, 0.85, 1.0))
    m.boxes = [b for b in m.boxes if not (b["mat"] == "tile" and b["at"][2] == 17.0 and b["size"][2] == 10.0)]
    for x0, x1, z0, z1 in ((-6.0, 10.0, 12.0, 14.0), (-6.0, 10.0, 20.0, 22.0), (-6.0, -4.0, 14.0, 20.0), (8.0, 10.0, 14.0, 20.0)):
        m.floor(x0, z0, x1, z1, "tile", (0.85, 0.9, 0.95), y=0.02)
    m.span(-4.0, 8.0, -0.25, -0.15, 14.0, 20.0, "water", solid=False)
    for x in (-3.0, -1.0, 7.0):
        m.prop("furniture_lounge_chair_relax", x, 12.9, 180.0, y=0.02)
    pt = m.prop("furniture_table_round", 2.5, 9.0, y=0.15)
    m.prop("furniture_chair", 1.5, 9.0, 90.0, y=0.15)
    m.prop("furniture_chair", 3.5, 9.0, -90.0, y=0.15)
    m.on("furniture_plant_small2", pt)
    m.prop("nature_pot_large", -5.0, 7.0, y=0.15)
    m.prop("nature_pot_small", 9.0, 7.0, y=0.15)
    fence(m, -20.0, 6.3, 20.0, 30.0, height=1.8, gaps=(("w", 4.0, 6.0),))
    for x, z, kind in ((-16.0, 26.0, "nature_tree_oak"), (15.0, 26.0, "nature_tree_default"), (-16.0, 12.0, "nature_tree_small"),
                       (16.0, 14.0, "nature_tree_pine_round_a"), (0.0, 27.0, "nature_tree_fat")):
        m.prop(kind, x, z, (x * 11) % 360)
    for x in frange(-14.0, 14.0, 3.5):
        m.prop("nature_plant_bush" if int(x) % 2 == 0 else "nature_plant_bush_large", x, 28.5, x * 30.0)
    for x, z, kind in ((-18.0, 18.0, "nature_flower_red_a"), (-18.0, 20.0, "nature_flower_yellow_a"), (18.0, 20.0, "nature_flower_purple_a"),
                       (-12.0, 16.0, "nature_crop_pumpkin"), (13.0, 22.0, "nature_rock_small_a"), (12.0, 10.0, "nature_stump_old")):
        m.prop(kind, x, z, (x * 5 + z) % 360)
    m.prop("furniture_bench", -15.0, 22.0, 90.0)
    # The front: a hedge, trees, a mailbox, flower beds.
    for x in frange(-11.0, -2.0, 1.4):
        m.prop("nature_plant_bush_small", x, -11.2)
    for x in frange(2.5, 11.0, 1.4):
        m.prop("nature_plant_bush_small", x, -11.2)
    m.prop("nature_tree_detailed", -8.0, -16.0)
    m.prop("nature_tree_default_fall", 8.0, -16.5)
    m.prop("nature_sign", 2.0, -18.8, 0.0)
    m.prop("furniture_trashcan", 21.0, -18.0)
    m.prop("furniture_trashcan", 22.0, -18.0)
    # Neighbours' fences, so the map has edges.
    m.wall((-30.0, -19.0), (-30.0, 30.0), "z", "wood", (0.55, 0.42, 0.3), height=2.0, thick=0.15, glass=False)
    m.wall((30.0, -19.0), (30.0, 30.0), "z", "wood", (0.55, 0.42, 0.3), height=2.0, thick=0.15, glass=False)
    m.wall((-30.0, 30.0), (30.0, 30.0), "x", "wood", (0.55, 0.42, 0.3), height=2.0, thick=0.15, glass=False)
    for x in (-26.0, -22.0, 24.0, 27.0):
        m.prop("nature_tree_tall", x, -12.0 + (x % 5) * 3.0)

    for i in range(8):
        m.spawn("props", -10.0 + (i % 4) * 2.4, -8.0 + (i // 4) * 2.0, 0.0)
    for i in range(6):
        m.spawn("hunters", -6.0 + i * 2.4, -24.5, 0.0)
    return m.build()


def office():
    m = Map(
        "ph_office", "Brightline Offices",
        "An office floor: open-plan desks, glass meeting rooms, a break room and a store room full of boxes.",
        {
            "sky_top": [0.20, 0.30, 0.50], "sky_horizon": [0.55, 0.62, 0.72], "ground": [0.25, 0.25, 0.27],
            "sun_pitch": -25.0, "sun_yaw": 120.0, "sun_energy": 0.7, "sun_colour": [1.0, 0.82, 0.62],
            "ambient": [0.55, 0.58, 0.66], "ambient_energy": 0.7,
        },
        round_seconds=300.0, hide_seconds=30.0,
    )
    H = 3.2
    X0, X1, Z0, Z1 = -24.0, 24.0, -16.0, 16.0
    m.floor(-50.0, -45.0, 50.0, 45.0, "concrete", (0.8, 0.8, 0.82), y=-0.02, thick=0.3)
    m.overlay(-50.0, -45.0, 50.0, -24.0, "asphalt", y=-0.02)
    m.overlay(-3.0, -24.0, 3.0, Z0, "sidewalk", y=-0.015)
    m.room(X0, Z0, X1, Z1, "carpet", (0.55, 0.58, 0.62), height=H, energy=1.0)
    # Glass all round the outside: an office tower's floor.
    m.wall((X0, Z0), (X1, Z0), "x", "concrete", None, height=H, thick=OUTER,
           holes=[(i * 4.0 + 0.3, i * 4.0 + 3.7, 0.6, 2.8) for i in range(12) if i not in (5, 6)] + [(21.6, 26.4, 0.0, 2.8)])
    m.wall((X0, Z1), (X1, Z1), "x", "concrete", None, height=H, thick=OUTER,
           holes=[(i * 4.0 + 0.3, i * 4.0 + 3.7, 0.6, 2.8) for i in range(12)])
    m.wall((X0, Z0), (X0, Z1), "z", "concrete", None, height=H, thick=OUTER,
           holes=[(i * 4.0 + 0.3, i * 4.0 + 3.7, 0.6, 2.8) for i in range(8)])
    m.wall((X1, Z0), (X1, Z1), "z", "concrete", None, height=H, thick=OUTER,
           holes=[(i * 4.0 + 0.3, i * 4.0 + 3.7, 0.6, 2.8) for i in range(8)])
    # The front doors' gap needs glass doors' frame left open: nothing here is a door leaf.
    GREY = (0.9, 0.92, 0.95)
    # Reception (x -6..6, z -16..-8).
    rd = m.prop("furniture_desk", -1.0, -11.0, 180.0)
    m.prop("furniture_desk", 0.7, -11.0, 180.0)
    m.prop("furniture_chair_desk", -0.2, -10.0, 0.0)
    m.on("furniture_computer_screen", rd, yaw=180.0)
    for x in (-5.0, -3.5):
        m.prop("furniture_lounge_design_chair", x, -14.6, 0.0)
    m.prop("furniture_table_coffee_glass", -4.2, -13.0)
    m.prop("furniture_lounge_design_sofa", 4.0, -14.6, 0.0)
    m.prop("furniture_potted_plant", -5.6, -9.0)
    m.prop("furniture_potted_plant", 5.6, -9.0)
    m.prop("furniture_rug_rectangle", 0.0, -13.0)
    m.span(-6.0, 6.0, 2.2, 2.6, -8.1, -7.9, "wood", (0.6, 0.45, 0.3))
    # Meeting rooms, glass walled, along the north (z 8..16): two of them.
    for x0, x1 in ((-24.0, -12.0), (-12.0, 0.0)):
        m.wall((x0, 8.0), (x1, 8.0), "x", "glass", None, height=H, thick=0.06, glass=False,
               holes=[((x1 - x0) / 2 - 0.7, (x1 - x0) / 2 + 0.7, 0.0, DOOR_H)])
        m.wall((x1, 8.0), (x1, 16.0), "z", "paint", GREY, height=H)
        for k in range(3):
            t = m.prop("furniture_table", x0 + 3.0 + k * 2.0, 12.0, 90.0)
            m.prop("furniture_chair_modern_frame_cushion", x0 + 3.0 + k * 2.0 - 0.7, 12.0, 90.0)
            m.prop("furniture_chair_modern_frame_cushion", x0 + 3.0 + k * 2.0 + 0.7, 12.0, -90.0)
            if k == 1:
                m.on("furniture_laptop", t, yaw=90.0)
        m.against("furniture_television_modern", "s", 16.0 - 0.2, (x0 + x1) / 2)
        m.prop("furniture_potted_plant", x0 + 0.6, 15.3)
    # The break room (x 0..12, z 8..16).
    m.wall((0.0, 8.0), (12.0, 8.0), "x", "paint", GREY, height=H, holes=[(5.3, 6.7, 0.0, DOOR_H)])
    m.wall((12.0, 8.0), (12.0, 16.0), "z", "paint", GREY, height=H)
    for i, pid in enumerate(("furniture_kitchen_cabinet", "furniture_kitchen_sink", "furniture_kitchen_cabinet_drawer",
                             "furniture_kitchen_cabinet")):
        p = m.against(pid, "s", 16.0 - 0.2, 1.0 + i * 1.0)
        if i == 2:
            m.on("furniture_kitchen_coffee_machine", p)
        if i == 3:
            m.on("furniture_kitchen_microwave", p)
    m.against("furniture_kitchen_fridge", "s", 16.0 - 0.2, 5.2)
    m.against("furniture_kitchen_fridge_small", "s", 16.0 - 0.2, 6.3)
    bt = m.prop("furniture_table_round", 8.0, 12.0)
    m.prop("furniture_chair", 7.0, 12.0, 90.0)
    m.prop("furniture_chair", 9.0, 12.0, -90.0)
    m.prop("furniture_chair", 8.0, 11.0, 0.0)
    m.on("furniture_toaster", bt)
    m.prop("furniture_trashcan", 11.4, 8.6)
    m.prop("furniture_lounge_sofa", 2.5, 9.2, 0.0)
    # The manager's office (x 12..24, z 8..16).
    m.wall((12.0, 8.0), (24.0, 8.0), "x", "glass", None, height=H, thick=0.06, glass=False, holes=[(1.0, 2.4, 0.0, DOOR_H)])
    md = m.prop("furniture_desk_corner", 21.0, 13.5, 180.0)
    m.prop("furniture_chair_desk", 21.5, 14.5, 180.0)
    m.on("furniture_computer_screen", md, dx=0.3, yaw=0.0)
    m.against("furniture_bookcase_closed_wide", "e", 24.0 - 0.2, 10.5)
    m.prop("furniture_lounge_chair", 15.0, 10.0, 45.0)
    m.prop("furniture_lounge_chair", 16.6, 10.0, -45.0)
    m.prop("furniture_potted_plant", 13.0, 15.3)
    # The store room (x 12..24, z -16..-6) full of boxes and shelves.
    m.wall((12.0, -6.0), (24.0, -6.0), "x", "paint", GREY, height=H, holes=[(1.0, 2.4, 0.0, DOOR_H)])
    m.wall((12.0, -16.0), (12.0, -6.0), "z", "paint", GREY, height=H)
    for row in range(3):
        for i in range(4):
            m.prop("furniture_bookcase_open", 15.0 + i * 1.0, -12.5 + row * 2.4, 0.0 if row % 2 else 180.0)
    for i in range(10):
        x = 13.0 + (i * 1.7) % 10.0
        z = -15.3 + (i // 6) * 0.7
        b = m.prop("furniture_cardboard_box_closed" if i % 3 else "furniture_cardboard_box_open", x, z, (i * 23) % 40)
        if i % 4 == 0:
            m.on("furniture_cardboard_box_closed", b, yaw=15.0)
    m.prop("furniture_kitchen_fridge_small", 23.2, -7.0, -90.0)
    # The server corner (x -24..-12, z -16..-6): racks as metal boxes, speakers for blinking boxes.
    m.wall((-24.0, -6.0), (-12.0, -6.0), "x", "paint", GREY, height=H, holes=[(9.0, 10.4, 0.0, DOOR_H)])
    m.wall((-12.0, -16.0), (-12.0, -6.0), "z", "paint", GREY, height=H)
    for k in range(4):
        m.span(-22.5 + k * 2.4, -21.3 + k * 2.4, 0.0, 2.2, -14.5, -13.5, "metal", (0.35, 0.37, 0.4))
    for k in range(5):
        m.prop("furniture_speaker", -22.0 + k * 1.8, -9.0, 180.0)
    m.prop("furniture_cardboard_box_closed", -13.0, -7.0, 10.0)
    m.prop("furniture_trashcan", -13.0, -15.3)
    # The open-plan floor: desks in pods with low partitions.
    for px in (-18.0, -8.0, 8.0, 18.0):
        if px > 6.0:
            zs = (-1.0, 4.0)
        else:
            zs = (-3.0, 3.0)
        for pz in zs:
            m.span(px - 3.4, px + 3.4, 0.0, 1.25, pz - 0.04, pz + 0.04, "fabric", (0.45, 0.55, 0.65))
            for side in (-1, 1):
                for k in (-1, 1):
                    dx = px + k * 1.7
                    dz = pz + side * 0.55
                    desk = m.prop("furniture_desk", dx, dz, 0.0 if side > 0 else 180.0)
                    m.prop("furniture_chair_desk", dx, pz + side * 1.5, 180.0 if side > 0 else 0.0)
                    m.on("furniture_computer_screen", desk, dz=-0.15 * side, yaw=0.0 if side > 0 else 180.0)
                    m.on("furniture_computer_keyboard", desk, dz=0.2 * side, yaw=0.0 if side > 0 else 180.0)
    for x, z in ((-22.8, 6.5), (-2.0, -6.5), (10.8, -6.5), (22.8, 6.5), (-10.0, 7.0)):
        m.prop("furniture_potted_plant", x, z)
    for x, z in ((-12.5, 6.8), (2.0, 6.8), (22.6, -4.0)):
        m.prop("furniture_trashcan", x, z)
    m.prop("furniture_coat_rack_standing", -5.6, -15.0)
    # Outside: planters and trees along the front, a bench.
    for x in (-20.0, -12.0, 12.0, 20.0):
        m.prop("nature_tree_small", x, -20.0, x * 9)
        m.prop("nature_pot_large", x + 3.0, -19.0)
    m.prop("furniture_bench", -6.0, -20.0, 0.0)
    m.prop("furniture_bench", 6.0, -20.0, 0.0)

    for i in range(10):
        m.spawn("props", -14.0 + i * 3.0, 6.0 if i % 2 == 0 else 5.0, 180.0)
    for i in range(6):
        m.spawn("hunters", -5.0 + i * 2.0, -28.0, 0.0)
    return m.build()


def woods():
    m = Map(
        "ph_woods", "Pinecrest Woods",
        "A clearing in the woods at golden hour: a log cabin, a woodpile, a pumpkin patch and a lot of places to be a rock.",
        {
            "sky_top": [0.32, 0.42, 0.68], "sky_horizon": [0.98, 0.72, 0.48], "ground": [0.25, 0.28, 0.18],
            "sun_pitch": -18.0, "sun_yaw": -100.0, "sun_energy": 1.1, "sun_colour": [1.0, 0.74, 0.48],
            "ambient": [0.56, 0.54, 0.62], "ambient_energy": 0.6, "fog": 0.0025,
        },
        round_seconds=300.0, hide_seconds=35.0,
    )
    S = 45.0
    m.floor_around(-S - 10.0, -S - 10.0, S + 10.0, S + 10.0, (-30.0, 16.0, -18.0, 28.0), "grass", (0.85, 0.95, 0.75), y=-0.02, thick=0.3)
    # Dirt paths and a clearing.
    m.overlay(-2.0, -S, 2.0, -6.0, "dirt", y=-0.015)
    m.overlay(-14.0, -6.0, 14.0, 10.0, "dirt", (0.95, 0.9, 0.85), y=-0.015)
    m.overlay(14.0, 0.0, S, 3.0, "dirt", y=-0.015)
    # A pond.
    m.span(-30.0, -18.0, -1.2, -1.0, 16.0, 28.0, "dirt", (0.5, 0.45, 0.4))
    m.span(-30.0, -18.0, -0.25, -0.15, 16.0, 28.0, "water", solid=False)

    for x0, x1, z0, z1 in ((-30.2, -30.0, 16.0, 28.0), (-18.0, -17.8, 16.0, 28.0), (-30.0, -18.0, 15.8, 16.0), (-30.0, -18.0, 28.0, 28.2)):
        m.span(x0, x1, -1.2, -0.02, z0, z1, "dirt", (0.5, 0.45, 0.4))
    m.prop("nature_lily_large", -26.0, 20.0, 30.0, y=-0.2)
    m.prop("nature_lily_large", -21.0, 24.0, 100.0, y=-0.2)
    # The cabin: log walls, a stone chimney, a porch.
    C0, C1, D0, D1 = -8.0, 8.0, 10.0, 22.0
    LOG = (0.62, 0.45, 0.32)
    m.room(C0, D0, C1, D1, "wood_floor", (0.85, 0.75, 0.65), height=3.0, ceiling="wood", roof="roof", roof_tint=(0.45, 0.32, 0.25), energy=0.9)
    m.wall((C0, D0), (C1, D0), "x", "wood", LOG, height=3.0, thick=0.35,
           holes=[(7.3, 8.7, 0.0, DOOR_H), (2.0, 4.0, SILL, HEAD), (12.0, 14.0, SILL, HEAD)])
    m.wall((C0, D1), (C1, D1), "x", "wood", LOG, height=3.0, thick=0.35, holes=[(6.0, 8.0, SILL, HEAD)])
    m.wall((C0, D0), (C0, D1), "z", "wood", LOG, height=3.0, thick=0.35, holes=[(5.0, 7.0, SILL, HEAD)])
    m.wall((C1, D0), (C1, D1), "z", "wood", LOG, height=3.0, thick=0.35, holes=[(5.0, 6.4, 0.0, DOOR_H)])
    m.span(-1.0, 1.0, 0.0, 4.6, D1, D1 + 1.2, "stone")
    m.floor(C0, D0 - 3.0, C1, D0, "wood", (0.7, 0.55, 0.4), y=0.2, thick=0.3)
    for x in (C0 + 0.2, C1 - 0.2):
        m.span(x - 0.12, x + 0.12, 0.2, 3.0, D0 - 2.95, D0 - 2.75, "wood", LOG)
    m.span(C0 - 0.2, C1 + 0.2, 2.9, 3.1, D0 - 3.1, D0, "roof", (0.45, 0.32, 0.25))
    # Inside the cabin.
    m.against("furniture_bed_single", "w", C0 + 0.2, 19.0)
    m.against("furniture_cabinet_bed", "w", C0 + 0.2, 17.0)
    m.against("furniture_kitchen_stove", "s", D1 - 0.2, 3.5)
    kc = m.against("furniture_kitchen_cabinet", "s", D1 - 0.2, 4.5)
    m.on("furniture_kitchen_coffee_machine", kc)
    tbl = m.prop("furniture_table_cloth", 3.0, 15.0)
    m.prop("furniture_chair", 2.4, 14.2, 0.0)
    m.prop("furniture_chair", 3.6, 15.8, 180.0)
    m.on("furniture_radio", tbl)
    m.against("furniture_bookcase_open", "e", C1 - 0.2, 19.5)
    m.prop("furniture_lounge_chair", -2.0, 13.0, 160.0)
    m.prop("furniture_rug_round", -2.0, 15.0)
    m.prop("furniture_bear", -7.0, 21.0, 120.0, y=0.0)
    m.prop("furniture_lamp_round_floor", -7.4, 11.0)
    m.prop("furniture_trashcan", 7.4, 11.0)
    m.prop("furniture_cardboard_box_closed", 5.0, 21.2, 10.0)
    # The porch.
    m.prop("furniture_lounge_chair_relax", -5.0, 8.0, 180.0, y=0.2)
    m.prop("furniture_bench", 4.0, 7.6, 180.0, y=0.2)
    m.prop("nature_pot_small", 7.0, 7.6, y=0.2)
    # The clearing: a campfire with logs round it, a woodpile, a pumpkin patch, a sign.
    m.prop("nature_campfire_logs", 0.0, -1.0)
    for a in range(4):
        ang = a * math.pi / 2 + 0.4
        m.prop("nature_log", math.cos(ang) * 3.0, -1.0 + math.sin(ang) * 3.0, math.degrees(-ang))
    for i in range(3):
        m.prop("nature_log_stack", 12.0, 8.0 + i * 1.6, 90.0)
    m.prop("nature_log_large", 10.0, 3.0, 20.0)
    for i in range(9):
        m.prop("nature_crop_pumpkin" if i % 3 else "nature_crop_melon", 16.0 + (i % 3) * 1.8, -10.0 + (i // 3) * 1.8, i * 40.0)
    fence(m, 14.5, -11.5, 22.0, -4.0, height=1.0, gaps=(("w", 3.0, 4.6),))
    m.prop("nature_sign", 1.0, -8.0, 160.0)
    m.prop("furniture_table_cross", -9.0, -3.0, 90.0)
    m.prop("furniture_bench", -10.4, -3.0, 90.0)
    m.prop("furniture_bench", -7.6, -3.0, -90.0)
    # The woods: trees in rings that thin toward the clearing, rocks, bushes, stumps, mushrooms.
    kinds = ["nature_tree_pine_round_a", "nature_tree_pine_tall_a", "nature_tree_tall", "nature_tree_cone",
             "nature_tree_default", "nature_tree_oak_fall", "nature_tree_simple"]
    i = 0
    for ring in range(4):
        rad = 18.0 + ring * 7.5
        count = 10 + ring * 6
        for k in range(count):
            a = 2 * math.pi * k / count + ring * 0.37
            x = math.cos(a) * rad + ((k * 31) % 7 - 3) * 0.6
            z = math.sin(a) * rad + ((k * 17) % 7 - 3) * 0.6
            if -30.5 < x < -17.5 and 15.5 < z < 28.5:
                continue
            if abs(x) < 3.5 and z < -10:
                continue
            if C0 - 3 < x < C1 + 3 and D0 - 4 < z < D1 + 3:
                continue
            m.prop(kinds[i % len(kinds)], x, z, (i * 53) % 360)
            i += 1
            if k % 3 == 0 and not (-31.5 < x + 2.2 < -16.5 and 14.5 < z - 1.4 < 29.5):
                small = ["nature_plant_bush", "nature_rock_small_a", "nature_stump_round", "nature_plant_bush_detailed",
                         "nature_mushroom_red_tall", "nature_grass_large", "nature_rock_large_a", "nature_stump_old",
                         "nature_plant_bush_large", "nature_flower_yellow_a"][(i * 3 + ring) % 10]
                m.prop(small, x + 2.2, z - 1.4, (i * 71) % 360)
    for x, z in ((-12.0, 4.0), (9.0, -6.0), (-15.0, -8.0), (6.0, 26.0), (-12.0, 25.0), (20.0, 12.0)):
        m.prop("nature_rock_tall_a", x, z, (x * 13) % 360)
    m.prop("nature_tree_pine_round_a", 22.0, -14.0)
    # The edge of the world: a ring of rock, so nobody walks off.
    for x0, x1, z0, z1 in ((-S, S, -S - 1, -S), (-S, S, S, S + 1), (-S - 1, -S, -S, S), (S, S + 1, -S, S)):
        m.span(x0, x1, 0.0, 4.0, z0, z1, "stone", (0.55, 0.55, 0.5))

    for i in range(10):
        a = i * 2 * math.pi / 10
        m.spawn("props", math.cos(a) * 6.0, -1.0 + math.sin(a) * 4.0, math.degrees(-a))
    for i in range(6):
        m.spawn("hunters", -5.0 + i * 2.0, -40.0, 0.0)
    return m.build()


MAPS = {
    "ph_school": school,
    "ph_house": house,
    "ph_office": office,
    "ph_woods": woods,
}


def write_all(check):
    stale = []
    for mid, make in MAPS.items():
        doc = make()
        text = json.dumps(doc, indent=1, sort_keys=False) + "\n"
        path = os.path.join(ROOT, "maps", mid + ".json")
        if check:
            have = open(path).read() if os.path.exists(path) else ""
            if have != text:
                stale.append(mid)
            continue
        with open(path, "w") as f:
            f.write(text)
        print("%-12s %5d boxes %5d props %3d lights  %s" % (mid, len(doc["boxes"]), len(doc["props"]), len(doc["lights"]), doc["name"]))
    if check:
        if stale:
            print("stale: %s (run tools/build_maps.py)" % ", ".join(stale))
            return 1
        print("every map is what the builder writes")
    return 0


def sync_sizes():
    with open(GAME_CATALOGUE) as f:
        cat = json.load(f)
    sizes = {p["id"]: {"size": p["size"], "disguise": p["disguise"]} for p in cat["props"]}
    with open(SIZES_FILE, "w") as f:
        json.dump({"comment": "Prop sizes from mg-prop-hunt's props/catalogue.json, copied by tools/build_maps.py --sync-sizes. The builder stacks props on each other with these; the game measures the models, this is a copy.", "props": sizes}, f, indent=1, sort_keys=True)
        f.write("\n")
    print("copied %d prop sizes" % len(sizes))


if __name__ == "__main__":
    if "--sync-sizes" in sys.argv:
        sync_sizes()
        sys.exit(0)
    sys.exit(write_all("--check" in sys.argv))

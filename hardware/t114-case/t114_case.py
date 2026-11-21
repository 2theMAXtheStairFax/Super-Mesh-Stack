"""Heltec T114 case that sits on top of an Argon Fan HAT (Pi 3B) on 8 mm standoffs.

Parametric CadQuery model. Run with CadQuery installed:
    python t114_case.py
Writes the printable parts to step/ and stl/, and view-only assemblies (case + component
stand-ins, not for slicing) to reference/.

Frame of reference (all mm):
  x, y = Raspberry Pi board coordinates (Pi mounting holes at 3.5/61.5, 3.5/52.5;
         USB/Ethernet end at +x, 40-pin GPIO header along the +y edge).
  z = 0 is the top surface of the Argon Fan HAT PCB.

Layout (lid seen from above):
  - T114 runs lengthways along the USB/Ethernet end, modelled from ref/heltec_t114_hull.step
    (Modeled-Components). The top of its bracket/screen sits flush against the underside of
    the lid; the hull's viewing keep-out is cut through the lid as the screen window. It rests
    on four corner posts at the PCB underside, placed where the hull's underside is empty.
    USB-C leaves through the -y (HDMI-side) wall.
    The U.FL end sits against the inside of the +y wall; the U.FL is a right-angle plug, so
    the pigtail goes straight out through the hull's U.FL cable keep-out (a window in that
    wall), loops round outside and
    comes back in through the GPIO notch and the window in the notch wall.
  - Lid fixings are spread round the edges: M3 into wall bosses on the SD-card end and the
    HDMI side, M3 beside the GPIO notch, the joystick's corner bolt continued into a base
    column, and two M2 straight down into the thickened USB/Ethernet-end wall.
  - Qwiic Navigation Switch and 0.96" TFT side by side along the HDMI side.
  - Foxeer M10Q-180 GPS and the RP-SMA bulkhead (pointing up) behind them.
  - The GPIO header comes up through a notch in the +y side.
  - The Argon power button is pressed with a flush button in the lid at the SD-card end.
    It is the top of a printed crank: a rod down from the lid, an arm under the joystick,
    and a second rod down through the floor onto the Argon button. The lid hole and the
    floor hole guide it; the button's own spring lifts it back.

Parts:
  base     - floor + walls; screws to the standoffs through four spacers.
  lid      - joystick, TFT, GPS and RP-SMA mount to it; window for the T114 screen.
  spacer   - 4x tubes from the standoff tops to the case floor (or use M3 standoffs).
  joy_cap  - push-on cap for the 5-way switch's square nub.
  btn_crank - offset linkage from the flush lid button to the Argon button.
  rpsma_riser - raises the RP-SMA bulkhead 7 mm above the lid; screwed up from under the lid
              with 2x M2 button heads (separate so the lid still prints top-down).

Values marked MEASURE are still estimates.
"""
from pathlib import Path

import cadquery as cq

OUT = Path(__file__).parent
STEP_DIR = OUT / "step"
STL_DIR = OUT / "stl"
REF_DIR = OUT / "reference"   # assemblies for viewing in CAD only - do not load into a slicer

# ---------------------------------------------------------------- Pi / Argon HAT
PI_W, PI_H = 85.0, 56.0
PI_HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]
STANDOFF_TOP = 8.0          # 8 mm standoffs, above the HAT PCB
FAN_TOP = 6.5               # measured: HAT PCB top -> top of fan
FAN_XY = (18.0, 58.0, 1.7, 41.7)            # fan footprint (x0, x1, y0, y1), from product photo
FAN_CLEARANCE = 5.0
ARGON_BUTTON = (6.0, 12.0, 19.0, 25.0)      # measured: 6 mm from SD-card edge, 19 mm from HDMI-side edge
ARGON_BUTTON_H = 5.25       # measured: HAT PCB -> top of the button
GPIO_HDR = (7.1, 57.9, 50.0, 55.0, 8.5)     # header housing footprint + pin-top height

# ---------------------------------------------------------------- case shell
FLOOR_Z = FAN_TOP + FAN_CLEARANCE   # underside of case floor
FLOOR_T = 2.5
OUTLINE_GROW = 0.4          # case outline is the Pi board grown by this all round (thicker walls, no slivers)
WALL = 2.0 + OUTLINE_GROW
X0, X1 = -OUTLINE_GROW, PI_W + OUTLINE_GROW
Y0, Y1 = -OUTLINE_GROW, PI_H + OUTLINE_GROW
CORNER_R = 3.0 + OUTLINE_GROW
LID_Z = 32.0                # top of walls / underside of lid
LID_T = 2.0
M3_CLEAR = 3.4              # M3 clearance
M3_PILOT = 2.5              # M3 thread-forming into plastic
M3_CSK = 6.4                # M3 countersunk head (6.0) + clearance
M2_CLEAR = 2.2              # M2 clearance
M2_BUTTON = (4.0, 1.4)      # M2 button head (3.5 dia x 1.3) counterbore: dia, depth -> sits flush
PI_SCREW_CLEAR = 2.9        # M2.5 clearance - Pi/standoff screws are M2.5
HEAD_BORE = 5.4             # M2.5 socket/pan head; drilled straight down for the screwdriver
HEAD_SINK = 1.5             # head sunk into the floor
FLOOR_TOP = FLOOR_Z + FLOOR_T
INNER_WALL = 1.2            # walls around the GPIO notch
IX0, IX1, IY0, IY1 = X0 + WALL, X1 - WALL, Y0 + WALL, Y1 - WALL   # inside faces of the walls

# GPIO pass-through: a notch through floor, walls and lid, open to the +y edge
NOTCH = (6.75, 58.25, 48.5)                 # x0, x1, y0  (runs to the +y edge)

# lid screws (base columns with pilot holes)
# (x, y, screw, column dia) - column None = pilot drilled straight into a wall
LID_SCREWS = [
    (3.3, 43.0, "M3", 7.0),     # SD-card end wall, GPIO-side half
    (43.0, 2.75, "M3", 5.5),    # HDMI-side wall, between the joystick end and the T114
    (32.5, 44.5, "M3", 7.0),    # beside the GPIO notch
    (83.45, 12.0, "M2", None),  # USB/Ethernet-end wall (3.9 mm thick), centred in it
    (83.45, 46.0, "M2", None),
]
M2_PILOT = 1.6              # M2 thread-forming into plastic
X_WALL = 3.5 + OUTLINE_GROW  # USB/Ethernet-end wall thickness, so M2 lid screws go straight into it

# ---------------------------------------------------------------- Argon button: flush lid button + crank
BTN_XY = ((ARGON_BUTTON[0] + ARGON_BUTTON[1]) / 2, (ARGON_BUTTON[2] + ARGON_BUTTON[3]) / 2)
LID_BTN_XY = (5.3, 33.3)    # flush button in the lid, between the joystick and the GPS
CRANK_ROD = 5.6             # both rods
CRANK_HOLE = 6.2            # guide holes in the lid and the floor
CRANK_ARM = (16.5, 19.0)    # arm z range: above the floor, below the GPS and joystick
CRANK_ARM_W = 4.0
CRANK_FLAT = 0.4            # flat on one side so the crank prints lying down

# ---------------------------------------------------------------- Heltec T114, from the hull model
# Hull frame: x -23..0 across, y 0 = USB-C end .. 50.7 = U.FL end; z 0..1.65 = PCB,
# underside parts down to -3.5, 6.25 = top of the screen/bracket; 6.25..11.25 is the viewing
# keep-out (not hardware), which may run out through the lid. y 50.7..55.7 above the PCB is
# the U.FL cable keep-out, straight off the end.
# Placed as modelled (no rotation): USB-C end faces -y, U.FL end at the +y outer face.
HULL_STEP = OUT / "ref" / "heltec_t114_hull.step"
HULL_W, HULL_L = 23.0, 50.7
HULL_PCB = (0.0, 1.65)
HULL_BRACKET_TOP = 6.25
HULL_SCREEN = (-20.25, -2.75, 8.2, 39.2)    # viewing keep-out footprint (x0, x1, y0, y1), hull frame
T114_X0 = X1 - X_WALL - 0.05 - HULL_W       # placement: case = (T114_X1 + hx, T114_Y0 + hy, HULL_Z0 + hz)
T114_X1 = T114_X0 + HULL_W
T114_Y1 = IY1                               # U.FL end against the inside of the +y wall
T114_Y0 = T114_Y1 - HULL_L                  # USB-C end (receptacle + plug run out the -y wall)
HULL_Z0 = LID_Z - HULL_BRACKET_TOP          # screen/bracket top flush against the lid underside
T114_PCB_Z = HULL_Z0 + HULL_PCB[0]          # PCB underside
T114_TOP_Z = HULL_Z0 + HULL_PCB[1]          # PCB top
T114_BRACKET_Z = HULL_Z0 + HULL_BRACKET_TOP
T114_CX = (T114_X0 + T114_X1) / 2
# U.FL: <1 mm from the far end, 3.5 mm from the -x long edge, in the hull's low end between the rails
UFL_BOX = (T114_X1 - 6.5, T114_X1 - 3.5, T114_Y1 - 3.8, T114_Y1 - 0.8)   # 3.5 mm in from the +x edge
# T114 support posts (hull frame x0, x1, y0, y1): corners where the hull's underside is empty
T114_POSTS = [(-23.0, -21.2, 7.8, 10.0), (-1.6, 0.0, 7.8, 10.0),
              (-23.0, -20.4, 43.3, 44.5), (-3.6, 0.0, 47.8, 50.6)]
HULL_BOTTOM = -3.5          # lowest point under the board
NOTCH_WINDOW = (36.1, 47.0, 24.0, 30.0)    # wire window in the notch wall: x0, x1, z0, z1
UFL_CABLE_Z = (T114_TOP_Z + 0.5, T114_TOP_Z + 1.9)   # 1.13 mm pigtail as it leaves the plug


def hull_to_case(wp):
    return wp.translate((T114_X1, T114_Y0, HULL_Z0))


def hull_xy(hx, hy):
    return T114_X1 + hx, T114_Y0 + hy


def screen_window(grow=0.0):
    x0, y0 = hull_xy(HULL_SCREEN[0], HULL_SCREEN[2])
    x1, y1 = hull_xy(HULL_SCREEN[1], HULL_SCREEN[3])
    return x0 - grow, x1 + grow, y0 - grow, y1 + grow


_hull_cache = {}


def t114_hull():
    if "h" not in _hull_cache:
        _hull_cache["h"] = hull_to_case(cq.importers.importStep(str(HULL_STEP)))
    return _hull_cache["h"]


# ---------------------------------------------------------------- SparkFun Qwiic Navigation Switch (PRT-27576)
JOY_SIZE = 25.4
JOY_X0, JOY_Y0 = IX0 + 0.7, IY0 + 0.7     # room for the M3 nuts against the walls
JOY_HOLE_INSET = 2.54
JOY_BODY_H = 2.0            # measured: switch body above the board
JOY_NUB_H = 3.0             # measured: nub above the body
JOY_BOSS_H = JOY_BODY_H + 0.2
JOY_STEM_HOLE = 7.6         # cap neck (5.6) + 1 mm tilt clearance all round
JOY_LED = (5.08, 18.1)      # STAT LED hole, from board lower-left
NUB_SQ = 1.9                # measured: square nub, 1.9 x 1.9
NUB_FIT = 0.05              # added to the bore; raise if the cap will not go on, lower if loose
CAP_BORE_DEPTH = 1.6        # how far the nub goes into the cap; neck ends 1.4 mm above the switch body,
                            # clear of the ring round the nub

# ---------------------------------------------------------------- 0.96" 80x160 ST7735 module
TFT_W, TFT_H = 30.0, 24.0
TFT_X0, TFT_Y0 = JOY_X0 + JOY_SIZE + 0.2, IY0 + 4.5   # pin header edge faces +y; bolt recess clear of the joystick countersink
TFT_X1, TFT_Y1 = TFT_X0 + TFT_W, TFT_Y0 + TFT_H
TFT_GLASS = (28.4, 13.5, 1.88)               # measured
TFT_GLASS_POCKET = 1.0
TFT_PCB_T = 1.6
TFT_WIN = (22.4, 11.4)      # active area is 21.7 x 10.8, centred on the module
TFT_PCB_Z = LID_Z + TFT_GLASS_POCKET - TFT_GLASS[2] - TFT_PCB_T   # PCB underside
TFT_PIN_SPAN = (4.85, 25.15)                 # 8-pin header along the +y edge, from TFT_X0
TFT_PIN_ROW = 2.0           # MEASURE: J1 pin row, distance from the +y (header) edge
TFT_HOLE_INSET = 2.25       # MEASURE: corner mounting holes, centre from both edges
TFT_HOLE_D = 2.2            # MEASURE: module hole size (pegs are 0.3 under this)
TFT_STANDOFF = TFT_GLASS[2] - TFT_GLASS_POCKET  # lid underside -> module PCB top

# ---------------------------------------------------------------- Foxeer M10Q-180 GPS
GPS = (18.0, 18.0, 8.0)     # w, d, h (retailer spec)
GPS_X0, GPS_Y0 = LID_BTN_XY[0] + CRANK_HOLE / 2 + 0.4, JOY_Y0 + JOY_SIZE + 0.5

# ---------------------------------------------------------------- RP-SMA bulkhead (antenna points up)
RPSMA_XY = (44.3, 41.5)
RPSMA_HOLE = 6.6            # 1/4-36 UNS thread is 6.35
RPSMA_FLAT = 5.8            # MEASURE: D-cut thread, round side to flat (hole gets +0.2)
RISER_RAISE = 7.0           # bulkhead panel raised this much above where it was in the lid
RISER_OD, RISER_ID = 18.0, 10.0
RISER_PLATE = 3.0           # panel the bulkhead clamps through (same as the old lid + boss)
RISER_SCREWS = [(-4.95, -4.95), (4.95, -4.95)]   # M2 screw offsets (r = 7, lower diagonals), up through the lid
RPSMA_BODY = (9.4, 11.0)    # MEASURE: hex/crimp body under the panel: dia, length


def box(x0, x1, y0, y1, z0, z1):
    return (cq.Workplane("XY")
            .box(x1 - x0, y1 - y0, z1 - z0, centered=False)
            .translate((x0, y0, z0)))


def cyl(x, y, z0, z1, d):
    return (cq.Workplane("XY").workplane(offset=z0)
            .center(x, y).circle(d / 2).extrude(z1 - z0))


def rounded_slab(z0, z1, inset=0.0, r=CORNER_R):
    return (cq.Workplane("XY").workplane(offset=z0)
            .center((X0 + X1) / 2, (Y0 + Y1) / 2)
            .rect(X1 - X0 - 2 * inset, Y1 - Y0 - 2 * inset)
            .extrude(z1 - z0)
            .edges("|Z").fillet(max(r - inset, 0.5)))


def countersink(x, y, head_d, hole_d):
    """90 deg countersink cut into the lid's top face."""
    depth = (head_d - hole_d) / 2
    top = LID_Z + LID_T
    return (cq.Workplane("XY").workplane(offset=top - depth).center(x, y)
            .circle(hole_d / 2).workplane(offset=depth + 0.01).circle(head_d / 2 + 0.01).loft())


def tft_holes():
    """(x, y, kind) for the TFT's corner holes: bolts on the open edge, pegs on the J1 edge
    (a nut there would hit the header)."""
    i = TFT_HOLE_INSET
    return [(TFT_X0 + i, TFT_Y0 + i, "bolt"), (TFT_X1 - i, TFT_Y0 + i, "bolt"),
            (TFT_X0 + i, TFT_Y1 - i, "peg"), (TFT_X1 - i, TFT_Y1 - i, "peg")]


def notch(z0, z1, grow=0.0):
    return box(NOTCH[0] - grow, NOTCH[1] + grow, NOTCH[2] - grow, Y1 + 1, z0, z1)


def chamfered_window(cx, cy, w, h):
    """Through-window in the lid with a 0.8 mm chamfer on the top face."""
    cut = box(cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2, LID_Z - 5, LID_Z + LID_T + 1)
    return cut.union(
        cq.Workplane("XY").workplane(offset=LID_Z + LID_T - 0.8)
        .center(cx, cy).rect(w, h)
        .workplane(offset=0.801).rect(w + 1.6, h + 1.6).loft())


# ================================================================= BASE
def make_base():
    floor = rounded_slab(FLOOR_Z, FLOOR_TOP)
    shell = rounded_slab(FLOOR_Z, LID_Z).cut(rounded_slab(FLOOR_TOP, LID_Z + 1, inset=WALL))
    base = floor.union(shell)

    # GPIO notch: walls around it, then cut it through everything
    base = base.union(notch(FLOOR_Z, LID_Z, grow=INNER_WALL).intersect(rounded_slab(FLOOR_Z, LID_Z)))
    base = base.cut(notch(FLOOR_Z - 1, LID_Z + 1))

    # thick USB/Ethernet-end wall for the M2 lid screws
    base = base.union(box(X1 - X_WALL, IX1, IY0, IY1, FLOOR_Z, LID_Z))

    # lid screw columns (clipped to the case outline) and pilot holes
    outline = rounded_slab(FLOOR_Z, LID_Z)
    for x, y, size, col in LID_SCREWS:
        if col:
            base = base.union(cyl(x, y, FLOOR_Z, LID_Z, col).intersect(outline))
        base = base.cut(cyl(x, y, LID_Z - 8, LID_Z + 1, M3_PILOT if size == "M3" else M2_PILOT))

    # column under the joystick's +x/-y corner hole: that M3 bolt carries on into it, so it
    # holds the lid down at that corner too
    jx, jy = JOY_X0 + JOY_SIZE - JOY_HOLE_INSET, JOY_Y0 + JOY_HOLE_INSET
    joy_bottom = LID_Z - JOY_BOSS_H - 1.6
    base = base.union(cyl(jx, jy, FLOOR_Z, joy_bottom, 6.0).intersect(outline))
    base = base.cut(cyl(jx, jy, joy_bottom - 8, joy_bottom + 1, M3_PILOT))

    # T114 support: four posts up to the PCB underside at the corners the hull leaves empty
    # (the hull is subtracted below, so they cannot touch anything hanging under the board).
    # Lip along the -x side locates it.
    ring_top = HULL_Z0 + HULL_BOTTOM - 0.3     # stops short of the lowest part under the board
    base = base.union(box(T114_X0, T114_X1, T114_Y0, T114_Y1, FLOOR_TOP, ring_top)
                      .cut(box(T114_X0 + 2.5, T114_X1 - 2.5, T114_Y0 + 2.5, T114_Y1 - 2.5, FLOOR_TOP - 1, ring_top + 1)))
    for hx0, hx1, hy0, hy1 in T114_POSTS:
        (x0, y0), (x1, y1) = hull_xy(hx0, hy0), hull_xy(hx1, hy1)
        base = base.union(box(x0, x1, y0, y1, FLOOR_TOP, T114_PCB_Z))
    base = base.union(box(T114_X0 - 1.4, T114_X0 - 0.2, T114_Y0 + 12.0, T114_Y1 - 8.0, FLOOR_TOP, T114_TOP_Z + 1.0))

    # Pi screws: clearance hole, head sunk into the floor, straight bore up for the screwdriver
    for hx, hy in PI_HOLES:
        base = base.cut(cyl(hx, hy, FLOOR_Z - 1, FLOOR_TOP + 1, PI_SCREW_CLEAR))
        base = base.cut(cyl(hx, hy, FLOOR_TOP - HEAD_SINK, LID_Z + 1, HEAD_BORE))

    # Argon button crank: lower rod guided by a hole in the floor
    base = base.cut(cyl(*BTN_XY, FLOOR_Z - 1, FLOOR_TOP + 1, CRANK_HOLE))

    # (the hull's U.FL cable keep-out cuts the pigtail window through the +y wall below)

    # the notch's +x wall would be a 0.2 mm sliver against the T114 - open the notch into the
    # T114 bay there instead of leaving something unprintable
    base = base.cut(box(NOTCH[1] - 0.01, T114_X0 + 0.01, NOTCH[2] - INNER_WALL - 0.01, Y1 + 1,
                        FLOOR_TOP, LID_Z + 1))

    # Pi-screw bores that would leave crescents too thin to print: open them into the
    # neighbouring space instead (outer skin of the case wall is kept)
    r = HEAD_BORE / 2
    (ax_, ay_), (bx_, by_), (cx_, cy_) = PI_HOLES[2], PI_HOLES[3], PI_HOLES[1]
    base = base.cut(box(ax_, NOTCH[0] + 0.01, ay_ - r, ay_ + r, FLOOR_TOP, LID_Z + 1))     # SD end of notch
    base = base.cut(box(NOTCH[1] - 0.01, bx_, by_ - r, by_ + r, FLOOR_TOP, LID_Z + 1))     # T114 end of notch
    base = base.cut(box(T114_X0 - 0.5, cx_ + r, IY0, cy_ + r, FLOOR_TOP,                   # T114 ring corner
                        HULL_Z0 + HULL_BOTTOM))

    # the T114 itself (receptacle + USB-C plug run out through the +y wall)
    base = base.cut(t114_hull())

    # window from the case into the GPIO notch for the TFT/Qwiic wires and the U.FL pigtail
    wx0, wx1, wz0, wz1 = NOTCH_WINDOW
    base = base.cut(box(wx0, wx1, NOTCH[2] - INNER_WALL - 1, NOTCH[2] + 1, wz0, wz1))
    return base


# ================================================================= LID
def make_lid():
    lid = rounded_slab(LID_Z, LID_Z + LID_T)
    lid = lid.cut(notch(LID_Z - 1, LID_Z + LID_T + 1))
    under = LID_Z
    gap = 0.3

    for x, y, size, _ in LID_SCREWS:
        lid = lid.cut(cyl(x, y, LID_Z - 1, LID_Z + LID_T + 1, M3_CLEAR if size == "M3" else M2_CLEAR))

    # flush Argon button (top of the crank) with a small lead-in chamfer
    lid = lid.cut(cyl(*LID_BTN_XY, LID_Z - 1, LID_Z + LID_T + 1, CRANK_HOLE))
    lid = lid.cut(cq.Workplane("XY").workplane(offset=LID_Z + LID_T - 0.5).center(*LID_BTN_XY)
                  .circle(CRANK_HOLE / 2).workplane(offset=0.501).circle(CRANK_HOLE / 2 + 0.5).loft())

    # RP-SMA riser seat: hole for the bulkhead body/cable, two M2 clearance holes
    rx, ry = RPSMA_XY
    lid = lid.cut(cyl(rx, ry, under - 1, LID_Z + LID_T + 1, RISER_ID))
    for dx, dy in RISER_SCREWS:
        lid = lid.cut(cyl(rx + dx, ry + dy, under - 1, LID_Z + LID_T + 1, M2_CLEAR))

    # T114: the lid bears directly on the screen/bracket top; the viewing keep-out is the
    # window (chamfered on top). The U.FL end is left alone under the flat lid.
    wx0, wx1, wy0, wy1 = screen_window(0.2)
    lid = lid.cut(chamfered_window((wx0 + wx1) / 2, (wy0 + wy1) / 2, wx1 - wx0, wy1 - wy0))

    # Foxeer M10Q-180: patch against the lid; locator along its +x side (wall, notch and joystick do the rest)
    gw, gd, _ = GPS
    gx1 = GPS_X0 + gw
    lid = lid.union(box(gx1 + gap, gx1 + gap + 1.2, GPS_Y0 + 2, GPS_Y0 + gd - 2, under - 4, under))

    # Qwiic Navigation Switch: stem hole, STAT LED hole, four bosses; M3 countersunk bolts
    # go down through the lid, bosses and board, nuts underneath
    jcx, jcy = JOY_X0 + JOY_SIZE / 2, JOY_Y0 + JOY_SIZE / 2
    lid = lid.cut(cyl(jcx, jcy, LID_Z - 1, LID_Z + LID_T + 1, JOY_STEM_HOLE))
    lid = lid.cut(cyl(JOY_X0 + JOY_LED[0], JOY_Y0 + JOY_LED[1], LID_Z - 1, LID_Z + LID_T + 1, 3.0))
    for dx in (JOY_HOLE_INSET, JOY_SIZE - JOY_HOLE_INSET):
        for dy in (JOY_HOLE_INSET, JOY_SIZE - JOY_HOLE_INSET):
            bx, by = JOY_X0 + dx, JOY_Y0 + dy
            lid = lid.union(cyl(bx, by, under - JOY_BOSS_H, under, 5.0))
            lid = lid.cut(cyl(bx, by, under - JOY_BOSS_H - 1, LID_Z + LID_T + 1, M3_CLEAR))
            lid = lid.cut(countersink(bx, by, M3_CSK, M3_CLEAR))

    # 0.96" TFT: glass pocket + window; bosses down to the module PCB; two M2 flush
    # button-head bolts (nuts under the PCB) on the open edge, two locating pegs on the J1 edge;
    # relief over J1's solder joints, which stand up to the glass
    tcx, tcy = TFT_X0 + TFT_W / 2, TFT_Y0 + TFT_H / 2
    gw, gh, _ = TFT_GLASS
    lid = lid.cut(box(tcx - gw / 2 - 0.2, tcx + gw / 2 + 0.2, tcy - gh / 2 - 0.2, tcy + gh / 2 + 0.2,
                      under - 1, under + TFT_GLASS_POCKET))
    lid = lid.cut(chamfered_window(tcx, tcy, *TFT_WIN))
    for hx, hy, kind in tft_holes():
        lid = lid.union(cyl(hx, hy, under - TFT_STANDOFF, under, 4.4 if kind == "bolt" else 3.6))
        if kind == "bolt":
            lid = lid.cut(cyl(hx, hy, under - 5, LID_Z + LID_T + 1, M2_CLEAR))
            lid = lid.cut(cyl(hx, hy, LID_Z + LID_T - M2_BUTTON[1], LID_Z + LID_T + 1, M2_BUTTON[0]))
        else:
            lid = lid.union(cyl(hx, hy, under - TFT_STANDOFF - TFT_PCB_T, under, TFT_HOLE_D - 0.3))
    pin_y = TFT_Y1 - TFT_PIN_ROW
    lid = lid.cut(box(TFT_X0 + TFT_PIN_SPAN[0] - 1.0, TFT_X0 + TFT_PIN_SPAN[1] + 1.0,
                      pin_y - 1.6, pin_y + 1.6, under - 1, under + 1.2))

    # trim anything below the lid that would land on the base's outer walls
    wall_ring = rounded_slab(LID_Z - 25, LID_Z).cut(rounded_slab(LID_Z - 26, LID_Z + 1, inset=WALL + gap))
    return lid.cut(wall_ring)


# ================================================================= SMALL PARTS
SPACER_H = FLOOR_Z - STANDOFF_TOP


def make_spacers():
    return [cyl(hx, hy, STANDOFF_TOP, FLOOR_Z, 7.0)
            .cut(cyl(hx, hy, STANDOFF_TOP - 1, FLOOR_Z + 1, PI_SCREW_CLEAR))
            for hx, hy in PI_HOLES]


def make_joy_cap():
    """Cap that pushes onto the square nub. Neck passes down through the lid hole,
    head sits 0.5 mm above the lid. Built in the assembled position."""
    jcx, jcy = JOY_X0 + JOY_SIZE / 2, JOY_Y0 + JOY_SIZE / 2
    body_top = LID_Z - JOY_BOSS_H + JOY_BODY_H
    nub_top = body_top + JOY_NUB_H
    bore, bore_depth = NUB_SQ + NUB_FIT, CAP_BORE_DEPTH
    neck_z0 = nub_top - bore_depth           # nub bottoms out in the bore
    head_z0 = LID_Z + LID_T + 0.5
    neck = cyl(jcx, jcy, neck_z0, head_z0, 5.6)
    head = (cyl(jcx, jcy, head_z0, head_z0 + 3.0, 11.0)
            .faces(">Z").edges().fillet(1.5)
            .faces("<Z").edges().chamfer(1.0))
    return neck.union(head).cut(box(jcx - bore / 2, jcx + bore / 2, jcy - bore / 2, jcy + bore / 2,
                                    neck_z0 - 1, nub_top))


def crank_local():
    """Crank in a local frame: lower rod at the origin, upper rod at (L, 0)."""
    (bx, by), (tx, ty) = BTN_XY, LID_BTN_XY
    length = ((tx - bx) ** 2 + (ty - by) ** 2) ** 0.5
    r = CRANK_ROD / 2
    lower = cyl(0, 0, ARGON_BUTTON_H, CRANK_ARM[1], CRANK_ROD)
    upper = cyl(length, 0, CRANK_ARM[0], LID_Z + LID_T, CRANK_ROD)        # flush with the lid top
    arm = (cq.Workplane("XY").workplane(offset=CRANK_ARM[0])
           .center(length / 2, 0).slot2D(length + CRANK_ARM_W, CRANK_ARM_W).extrude(CRANK_ARM[1] - CRANK_ARM[0]))
    part = lower.union(upper).union(arm)
    # print flat: shave the -y side of everything
    return part.cut(box(-20, length + 20, -20, -(r - CRANK_FLAT), 0, 60))


def make_rpsma_riser():
    """Tube on the lid top carrying the RP-SMA panel 7 mm higher; D-shaped hole in its top
    plate. Built in the assembled position."""
    rx, ry = RPSMA_XY
    z0 = LID_Z + LID_T
    z1 = z0 + RISER_RAISE
    riser = (cyl(rx, ry, z0, z1, RISER_OD)
             .intersect(box(rx - RISER_OD, rx + RISER_OD, ry - RISER_OD, NOTCH[2] - 0.1, z0, z1))   # flat at the notch
             .cut(cyl(rx, ry, z0 - 1, z1 - RISER_PLATE, RISER_ID)))
    riser = riser.faces(">Z").edges().chamfer(0.5)
    d_hole = cyl(rx, ry, z0, z1 + 1, RPSMA_HOLE).intersect(
        box(rx - RPSMA_HOLE / 2, rx - RPSMA_HOLE / 2 + RPSMA_FLAT + 0.2 + (RPSMA_HOLE - 6.35) / 2,
            ry - RPSMA_HOLE, ry + RPSMA_HOLE, z0, z1 + 1))                       # flat on the +x side
    riser = riser.cut(d_hole)
    for dx, dy in RISER_SCREWS:
        riser = riser.cut(cyl(rx + dx, ry + dy, z0 - 1, z0 + 5.0, M2_PILOT))
    return riser


def make_btn_crank():
    """Crank in the assembled position, resting on the Argon button."""
    (bx, by), (tx, ty) = BTN_XY, LID_BTN_XY
    import math
    angle = math.degrees(math.atan2(ty - by, tx - bx))
    return crank_local().rotate((0, 0, 0), (0, 0, 1), angle).translate((bx, by, 0))


# ================================================================= reference ghosts (not exported for print)
def make_ghosts():
    g = {}
    g["fan_hat_pcb"] = box(0, 65, 0, 56, -1.6, 0)
    g["fan"] = box(FAN_XY[0], FAN_XY[1], FAN_XY[2], FAN_XY[3], 0, FAN_TOP)
    bx0, bx1, by0, by1 = ARGON_BUTTON
    g["argon_button"] = box(bx0, bx1, by0, by1, 0, ARGON_BUTTON_H)
    hx0, hx1, hy0, hy1, hz = GPIO_HDR
    g["gpio_header"] = box(hx0, hx1, hy0, hy1, 0, hz)
    g["gpio_jumper_access"] = box(hx0, hx1, hy0, hy1, hz, 70)     # straight up must be clear
    g["standoffs"] = [cyl(x, y, 0, STANDOFF_TOP, 5.0) for x, y in PI_HOLES]
    g["t114"] = t114_hull()
    # U.FL plug + pigtail, all at plug height: straight off the end of the board and out of
    # the case, round outside, back in through the GPIO notch and its wire slot
    g["ufl_plug"] = box(UFL_BOX[0], UFL_BOX[1], UFL_BOX[2], UFL_BOX[3], T114_TOP_Z, T114_TOP_Z + 2.5)
    loop_y = Y1 + 8.0
    g["ufl_cable_path"] = [
        box(UFL_BOX[0], UFL_BOX[1], UFL_BOX[2], loop_y, *UFL_CABLE_Z),            # out the +y face
        box(36.4, UFL_BOX[1], Y1 + 2.0, loop_y, *UFL_CABLE_Z),                    # round outside
        box(36.4, 38.9, NOTCH[2] - INNER_WALL - 3.3, loop_y, *UFL_CABLE_Z),       # in via notch + window
    ]
    rx, ry = RPSMA_XY
    panel_under = LID_Z + LID_T + RISER_RAISE - RISER_PLATE
    g["rpsma_body"] = cyl(rx, ry, panel_under - RPSMA_BODY[1], panel_under, RPSMA_BODY[0])
    g["rpsma_nut_wrench"] = cyl(rx, ry, LID_Z + LID_T + RISER_RAISE + 0.01, LID_Z + LID_T + RISER_RAISE + 8, 16.0)
    g["riser_screw_heads"] = [cyl(rx + dx, ry + dy, LID_Z - 1.3, LID_Z, 3.6) for dx, dy in RISER_SCREWS]
    gw, gd, gh = GPS
    g["gps"] = box(GPS_X0, GPS_X0 + gw, GPS_Y0, GPS_Y0 + gd, LID_Z - gh, LID_Z)
    g["joystick"] = box(JOY_X0, JOY_X0 + JOY_SIZE, JOY_Y0, JOY_Y0 + JOY_SIZE,
                        LID_Z - JOY_BOSS_H - 1.6, LID_Z - JOY_BOSS_H)
    jcx, jcy = JOY_X0 + JOY_SIZE / 2, JOY_Y0 + JOY_SIZE / 2
    g["joystick_switch"] = box(jcx - 4.5, jcx + 4.5, jcy - 4.5, jcy + 4.5,
                               LID_Z - JOY_BOSS_H, LID_Z - JOY_BOSS_H + JOY_BODY_H)
    g["qwiic_plug"] = box(JOY_X0 + JOY_SIZE, JOY_X0 + JOY_SIZE + 6, jcy - 3, jcy + 3,
                          LID_Z - JOY_BOSS_H - 1.6 - 3.5, LID_Z - JOY_BOSS_H - 1.6)
    tcx, tcy = TFT_X0 + TFT_W / 2, TFT_Y0 + TFT_H / 2
    gw, gh, gt = TFT_GLASS
    pcb_top = TFT_PCB_Z + TFT_PCB_T
    tft = box(TFT_X0, TFT_X1, TFT_Y0, TFT_Y1, TFT_PCB_Z, pcb_top).union(
        box(tcx - gw / 2, tcx + gw / 2, tcy - gh / 2, tcy + gh / 2, pcb_top, pcb_top + gt))
    for hx, hy, _ in tft_holes():
        tft = tft.cut(cyl(hx, hy, TFT_PCB_Z - 1, pcb_top + 1, TFT_HOLE_D))
    g["tft"] = tft
    pin_y = TFT_Y1 - TFT_PIN_ROW
    g["tft_j1_solder"] = box(TFT_X0 + TFT_PIN_SPAN[0] - 0.8, TFT_X0 + TFT_PIN_SPAN[1] + 0.8,
                             pin_y - 0.8, pin_y + 0.8, pcb_top, pcb_top + gt)       # up to glass level
    g["nuts"] = ([cyl(hx, hy, TFT_PCB_Z - 1.6, TFT_PCB_Z, 4.6) for hx, hy, k in tft_holes() if k == "bolt"]
                 + [cyl(JOY_X0 + dx, JOY_Y0 + dy, LID_Z - JOY_BOSS_H - 1.6 - 2.4, LID_Z - JOY_BOSS_H - 1.6, 6.4)
                    for dx in (JOY_HOLE_INSET, JOY_SIZE - JOY_HOLE_INSET)
                    for dy in (JOY_HOLE_INSET, JOY_SIZE - JOY_HOLE_INSET)
                    if not (dx > JOY_SIZE / 2 and dy < JOY_SIZE / 2)])
    g["tft_header"] = box(TFT_X0 + TFT_PIN_SPAN[0] - 1.3, TFT_X0 + TFT_PIN_SPAN[1] + 1.3, TFT_Y1 - 2.5, TFT_Y1 + 6,
                          TFT_PCB_Z - 2.6, TFT_PCB_Z)
    return g


TOUCHING = {("btn_crank", "argon_button")}   # pairs that are meant to touch


def check_clearances(parts, ghosts):
    """Report anything the case intersects that it must not touch."""
    def vol(a, b):
        try:
            return a.intersect(b).val().Volume()
        except Exception:
            return 0.0
    problems = []
    for name, items in ghosts.items():
        if name in ("fan_hat_pcb", "standoffs"):
            continue
        for part_name, part in parts.items():
            if (part_name, name) in TOUCHING:
                continue
            for it in (items if isinstance(items, list) else [items]):
                v = vol(part, it)
                if v > 0.01:
                    problems.append(f"{part_name} hits {name}: {v:.2f} mm^3")
    names = list(parts)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            v = vol(parts[a], parts[b])
            if v > 0.01:
                problems.append(f"{a} hits {b}: {v:.2f} mm^3")
    return problems


if __name__ == "__main__":
    STEP_DIR.mkdir(exist_ok=True)
    REF_DIR.mkdir(exist_ok=True)
    STL_DIR.mkdir(exist_ok=True)
    base = make_base()
    lid = make_lid()
    spacers = make_spacers()
    cap = make_joy_cap()
    crank = make_btn_crank()
    riser = make_rpsma_riser()
    ghosts = make_ghosts()

    problems = check_clearances({"base": base, "lid": lid, "joy_cap": cap, "btn_crank": crank,
                                 "rpsma_riser": riser}, ghosts)
    for p in problems:
        print("CLEARANCE:", p)
    print("clearance check:", "FAIL" if problems else "ok")

    asm = cq.Assembly(name="t114_case")
    asm.add(base, name="base", color=cq.Color(0.25, 0.25, 0.28))
    asm.add(lid, name="lid", color=cq.Color(0.85, 0.45, 0.1))
    asm.add(cap, name="joy_cap", color=cq.Color(0.9, 0.9, 0.9))
    asm.add(crank, name="btn_crank", color=cq.Color(0.9, 0.9, 0.9))
    asm.add(riser, name="rpsma_riser", color=cq.Color(0.85, 0.45, 0.1))
    for i, s in enumerate(spacers):
        asm.add(s, name=f"spacer_{i + 1}", color=cq.Color(0.6, 0.6, 0.6))
    asm.export(str(REF_DIR / "t114_case_assembly.step"))

    # individual parts in print orientation
    def export(wp, stem):
        cq.exporters.export(wp, str(STEP_DIR / f"{stem}.step"))
        cq.exporters.export(wp, str(STL_DIR / f"{stem}.stl"))

    def upside_down(wp):
        return wp.rotate((0, 0, 0), (1, 0, 0), 180).translate((0, 0, wp.val().BoundingBox().zmax))

    export(base.translate((0, 0, -FLOOR_Z)), "t114_case_base")
    export(upside_down(lid), "t114_case_lid")
    export(spacers[0].translate((-PI_HOLES[0][0], -PI_HOLES[0][1], -STANDOFF_TOP)), "t114_case_spacer_x4")
    export(upside_down(cap), "t114_case_joy_cap")
    cl = crank_local().rotate((0, 0, 0), (1, 0, 0), 90)               # flat face down
    export(cl.translate((0, 0, -cl.val().BoundingBox().zmin)), "t114_case_btn_crank")
    export(upside_down(riser), "t114_case_rpsma_riser")                  # top plate on the bed

    # reference context (Argon HAT, T114, modules) for checking fit in CAD
    ref = cq.Assembly(name="t114_case_with_reference")
    ref.add(asm, name="case")
    for k, v in ghosts.items():
        if k in ("gpio_jumper_access", "rpsma_nut_wrench"):
            continue
        for i, it in enumerate(v if isinstance(v, list) else [v]):
            ref.add(it, name=f"ref_{k}_{i}", color=cq.Color(0.2, 0.6, 0.9, 0.5))
    ref.export(str(REF_DIR / "t114_case_with_reference.step"))

    # same, lid off: base, spacers, crank and every component in its installed position
    # (joystick, TFT, GPS and RP-SMA shown where they hang from the lid)
    open_ref = cq.Assembly(name="t114_case_reference_lid_off")
    open_ref.add(base, name="base", color=cq.Color(0.25, 0.25, 0.28))
    open_ref.add(crank, name="btn_crank", color=cq.Color(0.9, 0.9, 0.9))
    for i, s in enumerate(spacers):
        open_ref.add(s, name=f"spacer_{i + 1}", color=cq.Color(0.6, 0.6, 0.6))
    for k, v in ghosts.items():
        if k in ("gpio_jumper_access", "rpsma_nut_wrench"):
            continue
        for i, it in enumerate(v if isinstance(v, list) else [v]):
            open_ref.add(it, name=f"ref_{k}_{i}", color=cq.Color(0.2, 0.6, 0.9, 0.5))
    open_ref.export(str(REF_DIR / "t114_case_reference_lid_off.step"))

    print(f"spacer length {SPACER_H:.1f} mm; case floor at {FLOOR_Z:.1f} mm above HAT PCB; "
          f"lid top at {LID_Z + LID_T:.1f} mm; T114 posts {T114_PCB_Z - FLOOR_TOP:.1f} mm; "
          f"U.FL end: {LID_Z - T114_TOP_Z:.1f} mm from PCB to lid")

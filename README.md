# Cura Tapered Shrink Adjustment

A post-processing script for UltiMaker Cura. It scales each layer's X/Y by an amount that depends on the layer's height,
to cancel the taper a part picks up when the heated bed keeps the bottom layers warm while the upper layers cool.
The top of the part comes out smaller than the bottom. This script prints the upper layers a little bigger, or the
bottom a little smaller, so the part comes out straight.

Tested with Cura 5.14 and PLA on a Creality Ender-3 Pro with a glass bed.

## Why parts taper

- While the part prints, its bottom layers sit near bed temperature and its upper layers cool toward room temperature.
- Cooler plastic is smaller, and the shape sets that way, so the top ends up smaller than the bottom.
- The difference in diameter is roughly **D × α × (bottom temperature − top temperature)**.
  PLA's α is about 68 × 10⁻⁶ per °C.
- Example: a 70.5 mm part on a 122 °F (50 °C) bed, with the upper layers near 86 °F (30 °C):
  70.5 × 0.000068 × 20 ≈ **0.10 mm** smaller at the top.

Cura's own settings can't correct this:

| Cura setting | What it does | Why it doesn't fix the taper |
|---|---|---|
| Horizontal Scaling Factor Shrinkage Compensation | Scales every layer by the same amount | Fixing the top makes the bottom oversize |
| Initial Layer Horizontal Expansion | Offsets the first layer only | Handles elephant foot, not the taper above it |
| Horizontal Expansion | Offsets every layer by the same amount | Same at every height |

A lower bed temperature makes the taper smaller to begin with. Use the lowest bed temperature that still holds the part.

## Install

1. Download [`TaperCompensation.py`](TaperCompensation.py).
2. In Cura, open **Help → Show Configuration Folder** and put the file in the `scripts` folder there.
   Create the folder if it isn't there. On Windows it's `%APPDATA%\cura\<version>\scripts\`.
3. Restart Cura. Cura loads scripts only when it starts.

## Use

1. **Extensions → Post Processing → Modify G-Code → Add a script → Taper Compensation (XY scale by height).**
2. Type your table in **Height:scale pairs**.
3. Slice and save as usual.

Cura keeps post-processing scripts turned on for that printer across slices and restarts.
Remove the script, or set the table back to all 100, for parts that don't need it.
The default table, `0:100, 16:100`, changes nothing.

### The table

- **Format:** `height:percent` pairs, separated by commas, for example `0:100, 2:100, 16:100.2`.
  - Height is mm above the bed (the nozzle Z).
  - Percent is the X/Y scale at that height.
- **Between and beyond the points:** straight lines between the points. Below the first point and above the last, the end value holds.
- **What 100 means:** no change. The percentage is applied on top of Cura's own scaling, shrinkage compensation and
  horizontal expansion, so measure a print made with your normal settings.
- **Range:** 90 to 110%.
- **Also accepted:** spaces, `;` and `=`, so `0 = 100; 16 = 100.2%` works too.
- **If the table can't be read:** nothing in the G-code changes. A `;TAPER_COMP ERROR` line explains why, and the
  printer's screen shows `TaperComp table error` when the print starts.

## Measuring your printer

1. Print the part with your normal settings and the script off.
2. Measure the outside diameter just above the first millimetre (to stay clear of elephant foot) and near the top.
   If the taper might not be a straight line, also measure in the middle.
3. For each height: **percent = 100 × target ÷ measured.**
   - **Target = the design size:** every height comes out on size.
   - **Target = your bottom measurement:** the bottom stays as it prints, and the rest matches it.
4. As a cross-check, measure a bore the same way. Shrinkage taper changes holes by the same percentage, and the script
   corrects both.

## Examples

### 1. On size from top to bottom

A ring 70.5 mm across and 16 mm tall, in PLA on a 122 °F (50 °C) bed. Without the script it measured 70.54 at the
bottom and 70.47 at the top.

| Height | Measured | Percent |
|---|---|---|
| 0 mm | 70.54 | 100 × 70.5 ÷ 70.54 = 99.943 |
| 16 mm | 70.47 | 100 × 70.5 ÷ 70.47 = 100.043 |

```
0:99.943, 16:100.043
```

The bottom prints 0.057% smaller and the top 0.043% bigger. Both come out at 70.50, and so does the middle
(about 70.505 before, 99.993% at 8 mm).

### 2. Match the bottom

The same ring, keeping the bottom as it prints and bringing the top out to match it:
100 × 70.54 ÷ 70.47 = 100.099.

```
0:100, 16:100.099
```

### 3. A taper that levels off

The bed's effect fades with height, so on a taller part the size can stop changing partway up.
A 50 mm wide part, 30 mm tall, measured 50.10 at 1 mm, 50.02 at 6 mm and 50.02 at 30 mm.
Matching the bottom gives 100 × 50.10 ÷ 50.02 = 100.16 from 6 mm up:

```
0:100, 1:100, 6:100.16
```

Below 1 mm the layers print as sliced, so Cura's Initial Layer Horizontal Expansion still handles elephant foot there.
The 100.16% holds above 6 mm, so the 30 mm point isn't needed.

## What the script changes

- **Positions:** it moves the X and Y of every move (G0, G1, and G2/G3 with their I/J) in each layer, about each
  object's centre. The centre is the middle of that object's printed extent.
  - **One object on the plate:** everything in the layer scales with it, including skirt, supports and travel.
  - **Several objects:** only each object's own moves (its `;MESH:` sections) scale. Travel between objects and
    supports stay put.
- **Left alone:** extrusion (E), Z, speeds, temperatures, and the start and end G-code. At 0.1–0.5% the line width
  changes by about 0.001 mm.
- **Comments added, so you can check the result in the G-code.** This is from the ring in example 1:

```
;TAPER_COMP table 0:99.943, 16:100.043
;TAPER_COMP centre housing shell.stl X117.499 Y109.998
;POSTPROCESSED
;  [TaperCompensation]
...
;LAYER:0
;TAPER_COMP Z0.2 XY 99.944%
...
;LAYER:40
;TAPER_COMP Z8.2 XY 99.994%
```

## Limits

- **Rafts:** heights are nozzle Z. With a raft, subtract the raft's thickness to get the height on the part.
- **Relative positioning:** only absolute X/Y moves (G90) are scaled. Moves in relative mode (G91) pass through unchanged.
- **Object names:** objects are told apart by Cura's `;MESH:` names. Two objects with the same name would share one centre.
- **Header values:** the `;MINX`/`;MAXX` header lines aren't updated.

## Testing

Tested outside Cura on Cura 5.14 G-code of the 70.5 mm ring, 80 layers, with a deliberately large table
(`0:100, 2:100, 16:100.5`):

- The outer wall grew exactly as the table says at every layer, to within 0.001 mm (the G-code's 3-decimal rounding).
- The centre didn't move.
- Every line other than X and Y numbers stayed the same, apart from one comment per layer.
- A table of all 100 left the file byte-for-byte unchanged.
- A two-object file scaled each object about its own centre.
- An unreadable table changed nothing and flagged the error.

Then it was run inside Cura 5.14 with the example 1 table on the same ring.

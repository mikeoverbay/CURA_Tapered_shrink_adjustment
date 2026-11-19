# TaperCompensation.py - a Cura post-processing script (Extensions > Post Processing > Modify G-Code).
#
# Scales each layer's X/Y about the object's centre by a percentage that depends on the layer's height.
# Use it to cancel the taper PLA picks up when the bed keeps the bottom layers warm while the upper
# layers cool: the top comes out smaller than the bottom, so the upper layers get printed a little bigger.
#
# The table is height:percent pairs, height in mm above the bed (the nozzle Z), for example
#     0:100, 2:100, 10:100.2, 16:100.2
# - Straight lines between the points; below the first point and above the last, the end value holds.
# - 100 means no change. The percentage is on top of whatever Cura already did (model scale,
#   shrinkage compensation, horizontal expansion), so measure a print made with your normal settings.
#
# Centre: each object scales about the middle of its own printed extent.
# - One object on the plate: everything in each layer (skirt, supports, travel) scales with it.
# - Several objects: only each object's own moves scale; travel between objects and supports stay put.
# Extrusion (E) is left alone. At 0.2-0.5 % the line width changes by about 0.001-0.002 mm.
# Each layer gets a ";TAPER_COMP" comment with the scale used, so the result can be checked in the G-code.

import html
import re

from ..Script import Script

try:
    from UM.Logger import Logger
    from UM.Message import Message
except ImportError:  # running outside Cura (tests)
    Logger = None
    Message = None

_MOVE = re.compile(r"^\s*G0?[0-3](?![0-9])")       # G0..G3 (also G00..G03)
_POS_MODE = re.compile(r"^\s*G9([01])(?![0-9])")    # G90 absolute / G91 relative
_E_MODE = re.compile(r"^\s*M8([23])(?![0-9])")      # M82 absolute E / M83 relative E
_SET_POS = re.compile(r"^\s*G92(?![0-9])")
_WORD = re.compile(r"([A-Z])(-?\d*\.?\d+)")
_XYIJ = re.compile(r"([XYIJ])(-?\d*\.?\d+)")


def _parse_table(text):
    """'0:100, 2:100, 16:100.2' -> [(0.0, 100.0), (2.0, 100.0), (16.0, 100.2)]"""
    text = re.sub(r"\s*([:=])\s*", r"\1", text.strip())
    pairs = []
    for item in re.split(r"[,;\s]+", text):
        if not item:
            continue
        m = re.fullmatch(r"(\d*\.?\d+)[:=](\d*\.?\d+)%?", item)
        if not m:
            raise ValueError("can't read '{}'. Write height:percent, e.g. 10:100.2".format(item))
        pairs.append((float(m.group(1)), float(m.group(2))))
    if not pairs:
        raise ValueError("the table is empty. Write height:percent pairs, e.g. 0:100, 16:100.2")
    pairs.sort()
    for (z0, _), (z1, _) in zip(pairs, pairs[1:]):
        if z1 == z0:
            raise ValueError("height {:g} is listed twice".format(z0))
    for z, p in pairs:
        if not 90.0 <= p <= 110.0:
            raise ValueError("{:g}% at {:g} mm is outside 90-110%".format(p, z))
    return pairs


def _percent_at(table, z):
    if z <= table[0][0]:
        return table[0][1]
    for (z0, p0), (z1, p1) in zip(table, table[1:]):
        if z <= z1:
            return p0 + (p1 - p0) * (z - z0) / (z1 - z0)
    return table[-1][1]


def _fmt(v):
    s = "{:.3f}".format(v).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


class _Tracker:
    """Follows the printer's position and extrusion through the G-code (unscaled values)."""

    def __init__(self):
        self.abs_xyz = True
        self.abs_e = True
        self.x = None
        self.y = None
        self.z = 0.0
        self.e = 0.0

    def feed(self, code):
        """Follow one line (comment already stripped). For a G0-G3 move returns
        (start_x, start_y, end_x, end_y, extruding); otherwise None."""
        if not _MOVE.match(code):
            m = _POS_MODE.match(code)
            if m:
                self.abs_xyz = self.abs_e = m.group(1) == "0"   # Marlin: G90/G91 set E too
                return None
            m = _E_MODE.match(code)
            if m:
                self.abs_e = m.group(1) == "2"
                return None
            if _SET_POS.match(code):
                w = dict(_WORD.findall(code[code.upper().index("G92") + 3:]))
                if "E" in w:
                    self.e = float(w["E"])
                if "Z" in w:
                    self.z = float(w["Z"])
                if "X" in w:
                    self.x = float(w["X"])
                if "Y" in w:
                    self.y = float(w["Y"])
            return None

        w = dict(_WORD.findall(code))
        x0, y0 = self.x, self.y
        for axis in ("X", "Y", "Z"):
            if axis in w:
                v = float(w[axis])
                cur = getattr(self, axis.lower())
                if not self.abs_xyz and cur is not None:
                    v += cur
                setattr(self, axis.lower(), v)
        extruding = False
        if "E" in w:
            ev = float(w["E"])
            if self.abs_e:
                extruding = ev > self.e + 1e-6
                self.e = ev
            else:
                extruding = ev > 1e-6
        extruding = extruding and ("X" in w or "Y" in w)
        return x0, y0, self.x, self.y, extruding


def _popup(lines, error):
    """A message in Cura's window that stays until it's closed (no-op outside Cura).
    Cura shows message text as rich text, so lines are escaped and joined with <br>."""
    if Message is None:
        return
    text = "<br>".join(html.escape(ln, quote=False) for ln in lines)
    Message(text=text, title="Taper Compensation", lifetime=0,
            message_type=Message.MessageType.ERROR if error else Message.MessageType.WARNING).show()


def _layer_lines(chunk):
    """Split a layer chunk; yields (index, line, in_layer). Lines after ;TIME_ELAPSED are not layer moves."""
    in_layer = True
    for j, line in enumerate(chunk.split("\n")):
        yield j, line, in_layer
        if line.startswith(";TIME_ELAPSED:"):
            in_layer = False


class TaperCompensation(Script):

    def getSettingDataString(self):
        return """{
            "name": "Taper Compensation (XY scale by height)",
            "key": "TaperCompensation",
            "metadata": {},
            "version": 2,
            "settings":
            {
                "scale_table":
                {
                    "label": "Height:scale pairs",
                    "description": "Height above the bed in mm, a colon, then the XY scale in percent at that height. Separate pairs with commas. Straight lines between the points; the end values hold below the first and above the last. 100 = no change, and the scale is on top of Cura's own scaling. Example: 0:100, 2:100, 10:100.2, 16:100.2",
                    "type": "str",
                    "default_value": "0:100, 16:100"
                }
            }
        }"""

    def execute(self, data):
        text = self.getSettingValueByKey("scale_table") or ""
        try:
            table = _parse_table(text)
        except ValueError as err:
            msg = "Taper Compensation not applied: " + str(err)
            if Logger is not None:
                Logger.log("e", msg)
            _popup(["This G-code has NO taper compensation.",
                    "Table: " + (text.strip() or "(empty)"),
                    "Problem: " + str(err),
                    "Fix it in Extensions > Post Processing > Modify G-Code, then save again."], error=True)
            data[0] += ";TAPER_COMP ERROR " + msg + "\n"
            for i, chunk in enumerate(data):
                if ";LAYER:" in chunk:
                    data[i] = chunk.replace(";LAYER:", "M117 TaperComp table error\n;LAYER:", 1)
                    break
            return data

        table_txt = ", ".join("{:g}:{:g}".format(z, p) for z, p in table)
        if all(p == 100.0 for _, p in table):
            data[0] += ";TAPER_COMP table {} is all 100%, nothing changed\n".format(table_txt)
            return data

        centres = self._object_centres(data)
        if not centres:
            _popup(["This G-code has NO taper compensation: no objects were found in it "
                    "(no ;MESH: sections), so there was nothing to scale."], error=False)
            data[0] += ";TAPER_COMP no objects found (no ;MESH: sections), nothing changed\n"
            return data
        single = next(iter(centres.values())) if len(centres) == 1 else None

        data[0] += ";TAPER_COMP table {}\n".format(table_txt)
        for name, (cx, cy) in centres.items():
            data[0] += ";TAPER_COMP centre {} X{} Y{}\n".format(name, _fmt(cx), _fmt(cy))

        trk = _Tracker()
        for i, chunk in enumerate(data):
            if ";LAYER:" not in chunk:
                for line in chunk.split("\n"):
                    trk.feed(line.split(";", 1)[0])
                continue

            lines = chunk.split("\n")
            mesh = None
            layer_at = None
            first_z = None
            for j, line, in_layer in _layer_lines(chunk):
                if line.startswith(";LAYER:"):
                    layer_at = j
                    mesh = None
                    continue
                if line.startswith(";MESH:"):
                    mesh = line[6:].strip()
                    continue
                code = line.split(";", 1)[0]
                mv = trk.feed(code)
                if mv is None or not in_layer or not trk.abs_xyz:
                    continue
                if first_z is None and mv[4]:
                    first_z = trk.z
                centre = single if single is not None else centres.get(mesh)
                if centre is None:
                    continue
                f = _percent_at(table, trk.z) / 100.0
                if f == 1.0:
                    continue
                cx, cy = centre

                def scaled(m, f=f, cx=cx, cy=cy):
                    axis, v = m.group(1), float(m.group(2))
                    if axis == "X":
                        v = cx + (v - cx) * f
                    elif axis == "Y":
                        v = cy + (v - cy) * f
                    else:  # arc centre offsets I/J
                        v *= f
                    return axis + _fmt(v)

                new_code = _XYIJ.sub(scaled, code)
                if new_code != code:
                    lines[j] = new_code + line[len(code):]

            if layer_at is not None and first_z is not None:
                lines.insert(layer_at + 1, ";TAPER_COMP Z{} XY {:.3f}%".format(
                    _fmt(first_z), _percent_at(table, first_z)))
            data[i] = "\n".join(lines)
        return data

    @staticmethod
    def _object_centres(data):
        """Middle of each object's printed extent (extruding moves inside its ;MESH: sections)."""
        ext = {}
        trk = _Tracker()
        for chunk in data:
            is_layer = ";LAYER:" in chunk
            mesh = None
            for _, line, in_layer in _layer_lines(chunk):
                if line.startswith(";LAYER:"):
                    mesh = None
                    continue
                if line.startswith(";MESH:"):
                    mesh = line[6:].strip()
                    continue
                mv = trk.feed(line.split(";", 1)[0])
                if (mv is None or not mv[4] or not is_layer or not in_layer
                        or not mesh or mesh == "NONMESH" or not trk.abs_xyz):
                    continue
                x0, y0, x1, y1, _ = mv
                box = ext.setdefault(mesh, [x1, y1, x1, y1])
                for x, y in ((x0, y0), (x1, y1)):
                    if x is None or y is None:
                        continue
                    box[0] = min(box[0], x)
                    box[1] = min(box[1], y)
                    box[2] = max(box[2], x)
                    box[3] = max(box[3], y)
        return {name: ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0) for name, b in ext.items()}

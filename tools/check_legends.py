#!/usr/bin/env python3
"""Check keymap_legend.c against a ZMK keymap, key by key.

    python3 tools/check_legends.py path/to/your.keymap

The legend table is maintained by hand — ZMK keeps no printable label for a
binding at runtime — and nothing at build time notices when it drifts away from
the keymap. A missing layer is the worst case: the table is indexed by layer, so
one absent row silently shifts every layer above it and the last one draws as an
empty grid. This script is that missing check. It exits non-zero on any problem,
so it can guard a commit.

What it verifies:

  - one row per layer, in the keymap's own order, and the right number of keys
    per row for the configured grid;
  - NULL exactly where the keymap says &none;
  - a &trans entry spelled out with the legend of the layer it falls through to;
  - hold-taps labelled with their tap side;
  - a thumb cluster left blank when it is the cluster whose thumb holds that
    layer on. Which cluster that is gets worked out from the keymap: the
    positions bound to &mo/&lt/&tog/&sl for a layer, the combos that hold it,
    and, for a conditional layer, the union of the layers that trigger it;
  - the label itself, wherever this script knows what a binding should read.

Anything it does not recognise is reported as unverified rather than wrong, so
adapting the table for another keyboard does not turn the output into noise.
The block below is where those conventions live; extend it as your table grows.

SPDX-License-Identifier: MIT
"""

import argparse
import pathlib
import re
import sys

# --------------------------------------------------------------------------
# Conventions of the table shipped in this repository. Edit to match yours.
# --------------------------------------------------------------------------

# Keycodes whose legend is a plain ASCII character or a short string.
KEYCODE_LEGEND = {
    "SEMI": '";"', "COMMA": '","', "DOT": '"."', "FSLH": '"/"', "SQT": "\"'\"",
    "GRAVE": '"`"', "BSLH": '"\\\\"', "MINUS": '"-"', "EQUAL": '"="',
    "LBKT": '"["', "RBKT": '"]"',
    "EXCL": '"!"', "AT": '"@"', "HASH": '"#"', "DLLR": '"$"', "PRCNT": '"%"',
    "CARET": '"^"', "AMPS": '"&"', "ASTRK": '"*"', "LPAR": '"("', "RPAR": '")"',
}
KEYCODE_LEGEND.update({c: '"%s"' % c.lower() for c in "QWERTYUIOPASDFGHJKLZXCVBNM"})
KEYCODE_LEGEND.update({"N%s" % d: '"%s"' % d for d in "1234567890"})
KEYCODE_LEGEND.update({"F%d" % i: '"F%d"' % i for i in range(1, 25)})

# Keycodes drawn as an icon, by the K_* alias defined in keymap_legend.c.
KEYCODE_LEGEND.update({
    "ENTER": "K_ENTER", "RET": "K_ENTER", "BSPC": "K_BSPC", "DEL": "K_DEL",
    "SPACE": "K_SPACE", "TAB": "K_TAB", "ESC": "K_ESC", "CAPS": "K_CAPS",
    "UP": "K_UP", "DOWN": "K_DOWN", "LEFT": "K_LEFT", "RIGHT": "K_RIGHT",
    "HOME": "K_HOME", "END": "K_END", "PG_UP": "K_PG_UP", "PG_DN": "K_PG_DN",
    "LSHIFT": "K_SHIFT", "RSHIFT": "K_SHIFT", "LSHFT": "K_SHIFT", "RSHFT": "K_SHIFT",
    "LCTRL": "K_CTRL", "RCTRL": "K_CTRL", "LALT": "K_OPT", "RALT": "K_OPT",
    "LGUI": "K_CMD", "RGUI": "K_CMD",
    "C_PREV": "K_PREV", "C_PP": "K_PLAY", "C_NEXT": "K_NEXT", "C_MUTE": "K_MUTE",
    "C_VOL_DN": "K_VOL_DN", "C_VOL_UP": "K_VOL_UP",
    "C_BRI_DN": "K_BRI_DN", "C_BRI_UP": "K_BRI_UP",
})

# Behaviours that are not &kp, matched on the whole binding.
BEHAVIOUR_LEGEND = {
    "&bootloader": '"BLD"',
    "&sys_reset": '"RST"',
    "&bt BT_CLR": "K_BT_CLR",
    "&out OUT_TOG": "K_OUT",
    "&mkp LCLK": '"M1"',
    "&mkp MCLK": '"M3"',
    "&mkp RCLK": '"M2"',
}

# A layer-holding key reads as the layer's name in capitals: &mo Nav -> "NAV".
def layer_legend(name):
    return '"%s"' % name.upper()


# Host profiles are numbered from one on the screen (dongle_ui.c draws
# profile + 1), so &bt BT_SEL 0 reads "B1".
def bt_sel_legend(index):
    return '"B%d"' % (index + 1)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

LAYER_BEHAVIOURS = ("&mo", "&lt", "&tog", "&sl", "&to")


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def split_bindings(body):
    """A bindings property into one string per binding, arguments included."""
    return ["&" + part.strip() for part in body.split("&")[1:] if part.strip()]


def node_body(src, name):
    """The text inside `name { ... }`, brace-matched. Regex cannot: a layer's
    own closing brace would end the match at the first layer."""
    opening = re.search(r"\b%s\s*\{" % re.escape(name), src)
    if not opening:
        return None
    depth, position = 1, opening.end()
    while position < len(src) and depth:
        depth += {"{": 1, "}": -1}.get(src[position], 0)
        position += 1
    return src[opening.end():position - 1]


def parse_keymap(path):
    """-> (layer names in order, bindings per layer, activations per layer)."""
    src = strip_comments(pathlib.Path(path).read_text())

    defines = {name: int(value) for name, value in
               re.findall(r"#define\s+(\w+)\s+(\d+)\b", src)}

    keymap = node_body(src, "keymap")
    if keymap is None:
        sys.exit("%s: no keymap node found" % path)

    names, bindings = [], []
    for node, body in re.findall(r"(\w+)\s*\{(.*?)\};", keymap, re.S):
        prop = re.search(r"bindings\s*=\s*<(.*?)>\s*;", body, re.S)
        if not prop:
            continue
        shown = re.search(r'display-name\s*=\s*"([^"]*)"', body)
        names.append(shown.group(1) if shown else node)
        bindings.append(split_bindings(prop.group(1)))

    index_of = {}
    for position, name in enumerate(names):
        index_of[name] = position
    for name, value in defines.items():
        if value < len(names):
            index_of.setdefault(name, value)

    def resolve(token):
        if token.isdigit():
            return int(token)
        return index_of.get(token)

    # Positions that switch each layer on, so we know which thumb is occupied.
    activations = [set() for _ in names]
    for layer in bindings:
        for position, binding in enumerate(layer):
            parts = binding.split()
            if parts[0] in LAYER_BEHAVIOURS and len(parts) > 1:
                target = resolve(parts[1])
                if target is not None:
                    activations[target].add(position)

    combos = node_body(src, "combos") or ""
    for _, combo in re.findall(r"(\w+)\s*\{(.*?)\};", combos, re.S):
        positions = re.search(r"key-positions\s*=\s*<(.*?)>", combo, re.S)
        binds = re.search(r"bindings\s*=\s*<(.*?)>", combo, re.S)
        if not positions or not binds:
            continue
        for binding in split_bindings(binds.group(1)):
            parts = binding.split()
            if parts[0] in LAYER_BEHAVIOURS and len(parts) > 1:
                target = resolve(parts[1])
                if target is not None:
                    activations[target] |= {int(p) for p in positions.group(1).split()}

    # A conditional layer is held by whatever holds the layers that trigger it.
    conditionals = node_body(src, "conditional_layers") or ""
    for _, rule in re.findall(r"(\w+)\s*\{(.*?)\};", conditionals, re.S):
        triggers = re.search(r"if-layers\s*=\s*<(.*?)>", rule, re.S)
        result = re.search(r"then-layer\s*=\s*<(.*?)>", rule, re.S)
        if not triggers or not result:
            continue
        target = resolve(result.group(1).strip())
        if target is None:
            continue
        for token in triggers.group(1).split():
            source = resolve(token)
            if source is not None:
                activations[target] |= activations[source]

    return names, bindings, activations


def parse_legends(path):
    """-> (rows of legend tokens, keys per row, thumbs per cluster)."""
    src = pathlib.Path(path).read_text()

    def value(name):
        found = re.search(r"#define %s (\d+)" % name, src)
        return int(found.group(1)) if found else None

    columns, rows, thumbs = (value("LEGEND_TABLE_COLUMNS"),
                             value("LEGEND_TABLE_ROWS"),
                             value("LEGEND_TABLE_THUMBS"))
    if None in (columns, rows, thumbs):
        sys.exit("%s: LEGEND_TABLE_COLUMNS/ROWS/THUMBS not found" % path)

    macros = dict(re.findall(r"#define (THUMBS_[A-Z_]+) (.+)", src))
    table = src.split("static const char *const legends[]", 1)[1]
    table = table.split("#define LAYER_COUNT", 1)[0]
    table = strip_comments(table)

    token = re.compile(r'"(?:[^"\\]|\\.)*"|NULL|THUMBS_[A-Z_]+|K_[A-Z_0-9]+')
    layers = []
    for block in re.findall(r"\{(.*?)\},", table, re.S):
        entries = []
        for item in token.findall(block):
            entries += token.findall(macros[item]) if item in macros else [item]
        layers.append(entries)

    return layers, columns * rows * 2 + thumbs * 2, thumbs


# --------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------

def expected(binding, base_expected, names):
    """The legend a binding should carry, or None when unknown."""
    if binding in BEHAVIOUR_LEGEND:
        return BEHAVIOUR_LEGEND[binding]

    parts = binding.split()
    behaviour, args = parts[0], parts[1:]

    if behaviour == "&none":
        return "NULL"
    if behaviour == "&trans":
        return base_expected
    if behaviour == "&kp" and args:
        return KEYCODE_LEGEND.get(args[0])
    if behaviour in ("&hml", "&hmr", "&mt", "&lt") and len(args) > 1:
        # tap side: the second argument, except &lt, whose tap side it also is
        return KEYCODE_LEGEND.get(args[1])
    if behaviour in LAYER_BEHAVIOURS and args:
        if args[0].isdigit() and int(args[0]) < len(names):
            return layer_legend(names[int(args[0])])
        return layer_legend(args[0])
    if behaviour == "&bt" and len(args) > 1 and args[0] == "BT_SEL":
        return bt_sel_legend(int(args[1]))
    return None


def main():
    here = pathlib.Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Check keymap_legend.c against a keymap.")
    parser.add_argument("keymap", help="path to the .keymap the table describes")
    parser.add_argument("--legends", default=here / "boards/shields/dongle_tft/keymap_legend.c",
                        help="path to keymap_legend.c")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="list the bindings whose legend was not verified")
    args = parser.parse_args()

    names, bindings, activations = parse_keymap(args.keymap)
    table, keys, thumbs = parse_legends(args.legends)

    problems, unverified = [], []

    if len(table) != len(names):
        problems.append("the table has %d layers, the keymap has %d (%s)"
                        % (len(table), len(names), ", ".join(names)))

    left = range(keys - 2 * thumbs, keys - thumbs)
    right = range(keys - thumbs, keys)

    for index, name in enumerate(names):
        if index >= len(table):
            continue
        row, layer = table[index], bindings[index]
        if len(row) != keys or len(layer) != keys:
            problems.append("%s: %d legends against %d bindings, expected %d"
                            % (name, len(row), len(layer), keys))
            continue

        busy = set()
        for cluster in (left, right):
            if activations[index] & set(cluster):
                busy |= set(cluster)

        for position in range(keys):
            got = row[position]
            if position in busy:
                if got != "NULL":
                    problems.append("%s pos %d: the thumb holding this layer occupies the "
                                    "cluster, so the legend should be blank, not %s"
                                    % (name, position, got))
                continue
            want = expected(layer[position],
                            expected(bindings[0][position], "NULL", names), names)
            if want is None:
                unverified.append("%s pos %d: %s" % (name, position, layer[position]))
            elif want != got:
                problems.append("%s pos %d: %s should read %s, table has %s"
                                % (name, position, layer[position], want, got))

    for line in problems:
        print(line)
    if args.verbose:
        for line in unverified:
            print("unverified: %s" % line)

    print("%d layers x %d keys checked against %s; %d problems, %d legends unverified%s"
          % (len(table), keys, args.keymap, len(problems), len(unverified),
             "" if args.verbose else " (-v to list them)"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

# Syntax

A schematic is a plain-text list of parts and connections. Order does not
matter, and anything you don't declare is inferred from the connections.
Lines starting with `#` or `//` are comments (comments always take a whole
line).

- [Connections](#connections)
- [Net names](#net-names)
- [Parts](#parts)
- [Pins](#pins)
- [Standard parts](#standard-parts)
- [Image parts](#image-parts)
- [YAML](#yaml)

## Connections

```text
MCU.GND -> Sensor.GND, Display.GND
```

`Part.Pin` on the left connects to every `Part.Pin` on the right. The arrow
has no direction for the layout; `->`, `<-`, `<->`, `--` and `=>` all mean
the same.

A pin can carry a label in parentheses. The label is shown next to the pin
name, and later references can use either:

```text
MCU.GPIO21(SDA) -> Sensor.SDA
MCU.SDA -> Display.SDA          # same pin as GPIO21
```

Part names with spaces and pin names with dots go in quotes:
`"Sensor 1".VCC`, `Board."3.3V"`.

Connections that share a pin belong to the same net, so these two lines
describe a single net with three pins:

```text
MCU.GND -> Sensor.GND
Sensor.GND -> Display.GND
```

## Net names

Name a net after a colon. Named nets are labelled in the drawing:

```text
MCU.GPIO21(SDA) -> Sensor.SDA, Display.SDA : I2C_SDA
```

`[net=I2C_SDA]` at the end of the line does the same. A net can also be
listed as a whole:

```text
net I2C_SDA: MCU.GPIO21, Sensor.SDA, Display.SDA
```

Without a name, common signal pins (`GND`, `3V3`, `5V`, `VCC`, `VIN`, `SDA`,
`SCL`, `MISO`, `MOSI`, `SCK`, `CS`, `RST`, `TX`, `RX`) name the net after the
source pin; other nets get an internal name and no label.

Names never change the layout. They only pick a colour: ground nets
(`GND`, `VSS`, `AGND`, ...) are grey, supplies (`VCC`, `3V3`, `5V`, `VBAT`,
...) red, everything else a stable colour per name.

## Parts

Parts used in connections exist automatically. Declare a part to give it a
type (shown under its name), fix its pin order or make it a standard symbol:

```text
MCU: ESP32-WROOM-32
  pins: 3V3, GND, GPIO21(SDA), GPIO22(SCL)
```

The indented lines belong to the part above them. A declaration without a
type is fine too (`MCU:`).

## Pins

`pins:` lists pins in order. The layout decides on which side of the box
each pin goes, so that wires take the shortest clean path.

To fix the sides yourself, use `left:`, `right:`, `top:` and `bottom:`:

```text
MCU: ESP32 DevKit
  left: 3V3, EN, GPIO36, GND
  right: GND, GPIO23, GPIO22, 5V
```

Pins with the same name (a board with several `GND` pins) are separate
pins; a connection to `MCU.GND` goes to the first of them.

## Standard parts

Resistors, capacitors, inductors, diodes, LEDs, fuses, switches and current
shunts are drawn as compact symbols instead of boxes. The kind is recognised from the type:

```text
R1: resistor 10k
C1: 100nF Capacitor
D1: LED red
F1: Inline Fuse
SW1: Slide Switch (SPDT)
  pins: 1, 2, 3
RS1: 7.5mOhm Shunt
  pins: IN, OUT, SENSE+, SENSE-
```

or set explicitly, with an optional value:

```text
R1:
  kind: resistor
  value: 10k
  pins: 1, 2

R2 [kind=resistor, value=4.7k]:
R3: 4.7k [resistor]
```

Recognised kinds (and short forms): `resistor` (`res`, `r`), `capacitor`
(`cap`, `c`), `inductor` (`ind`, `l`), `diode` (`d`), `led`, `fuse`,
`switch` (`sw`), `shunt`. A switch with three pins is drawn as a changeover switch.
A shunt with four pins is drawn as a resistor with Kelvin sense taps: pins
named `SENSE…`, `S+` / `S-` (otherwise the last two pins) are the taps, and
`+` / `-` or a declared `left:` / `right:` side puts each tap at the end of
the matching current terminal.
For diodes and LEDs, pins named `A` / `anode` / `+` / `1` and `K` /
`cathode` / `-` / `2` set the direction of the symbol.

Types containing words like *sensor*, *module*, *board* or *controller*
are never taken for a standard part, so `Temp Sensor` stays a box.
`kind: box` forces a box.

## Image parts

A part can be drawn with a picture of the real board, with pins on a 20 px
grid. Define the part once with `part`, then use it like a type:

```text
part DevKit:
  image: parts/esp32_devkit.svg
  size: 9x18
  image_rect: 0, 0, 180, 360
  pins:
    3V3: 0, 2
    GND: 0, 16
    D21: 8, 12
    D22(SCL): 8, 15

MCU: DevKit
MCU.D21 -> Sensor.SDA
```

`size` is the footprint in grid cells (width x height), each pin is placed
at a grid column and row, and the optional `image_rect` positions the
picture inside the footprint in pixels (x, y, width, height).

Image paths are relative to the file that contains the schematic (for
Markdown, the `.md` file). The image is embedded into the SVG, so the
output is a single self-contained file. `schematic serve` has a part builder
that places the pins by clicking on the picture.

The layout may turn image parts by 90° if that makes the drawing clearly
cleaner. `rotatable: false` (on the part or on one instance) keeps it
upright. A pin coordinate can take a side as third value
(`GND: 0, 16, left`) when it is not on the outline.

## YAML

The same model in YAML, for generated input. It needs the optional
dependency: `pip install "schematic-txt[yaml]"`.

```yaml
components:
  MCU:
    type: ESP32
    pins: [3V3, GND, IO21, IO22]
  Sensor:
    type: BME280
    pins: [VIN, GND, SDA, SCL]
  R1:
    kind: resistor
    value: 10k
connections:
  - MCU.GND -> Sensor.GND
  - MCU.3V3 -> Sensor.VIN
  - MCU.IO21 -> Sensor.SDA : I2C_SDA
  - from: MCU.IO22
    to: [Sensor.SCL]
    net: I2C_SCL
```

A file is read as YAML when its first line starts with `components:`,
`connections:` or `nets:`.

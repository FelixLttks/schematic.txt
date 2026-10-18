# Schematic.txt - Text-to-Schematic 2D Engine

Ein modulares, textbasiertes CAD/Schaltplan-Werkzeug zur Beschreibung von elektronischen Schaltungen und automatischer Erzeugung von echten 2D-Schaltplänen.

---

## Highlights

- **Textbasiertes Format (Git-tauglich)**: Reiner Text (DSL oder YAML). Einfach zu versionieren, per Diff nachvollziehbar und konfliktarm zu mergen.
- **Verbindungen beschreiben, nicht zeichnen**: Einfache Schreibweise wie `ESP32.GND -> Sensor.GND, OLED.GND`. Pins und Komponenten werden bei Bedarf automatisch inferiert.
- **Lesbarkeit vor Kompaktheit**: Das Layout wird für Menschen optimiert – wenige Kreuzungen und Knicke, zusammengehörige Leitungen (Busse, Differenzpaare, SPI/UART-Bündel) laufen parallel, Versorgungs-Rails außen, klare Gruppen. Verbindungstreue ist eine harte Bedingung und wird bei jedem Lauf geprüft.
- **Konnektivitätsgetriebenes 2D-Placement**: Kein manuelles Positionieren nötig. Die Anordnung folgt der Topologie (Hubs, Ketten, Busse, Cluster); Pull-ups, Filter- und Abblockkondensatoren hängen konventionsgerecht an ihrem Pin. Mehrere Platzierungskandidaten werden tatsächlich geroutet und bewertet – der lesbarste gewinnt.
- **Echte 2D-Verdrahtung (kein WireViz 1D-Kabelbaum)**: Orthogonales Manhattan-Routing mit Bus-Trunks, Steiner-Bäumen, Rip-up-and-Reroute, automatischen Junction-Dots (`●`) und Netznamen-Labels.
- **Qualitätsbericht**: Jede Zeichnung wird vermessen (Kreuzungen, Knicke, Abstände, Bündel-Kohärenz, Verletzungen, Lesbarkeits-Score) – siehe `--report`.
- **Minimalistisches PoC Web-Interface**: 50/50 Split-Screen (links Editor mit Zeilennummern, rechts 2D-SVG-Vorschau mit Pan & Zoom sowie Live-Fehleranzeige). Kein Header-Bloat, keine Farbverläufe, purer Fokus.
- **Modulare Clean-Architecture**: Vollständige Trennung von Core-Logik (`schematic/`) und Web-App (`web/`).

---

## Schnellstart

### 1. Web-App starten
```bash
python3 app.py
```
Öffne anschließend [http://127.0.0.1:5000](http://127.0.0.1:5000) im Browser.

### 2. Als CLI-Tool nutzen (z. B. für Git CI/CD)
```bash
# Schaltplan-Datei direkt nach SVG kompilieren:
python3 app.py examples/esp32_sensors_bus.txt -o schaltplan.svg

# Mit Qualitätsbericht, hellem Theme und gewählter Layout-Qualität:
python3 app.py examples/example_large.txt -o large.svg --light --report --effort thorough
python3 app.py examples/example_large.txt --json-report qualitaet.json
```

`--effort` wählt zwischen `fast` (schnelle Vorschau), `balanced` (Standard) und `thorough`.

### 3. Tests ausführen
```bash
python3 -m unittest discover tests
```

### 4. Layout-Engine vergleichen
```bash
# Alle Beispiele und Layout-Testfälle mit einer früheren Engine-Version vergleichen:
python3 tools/layout_report.py --baseline main --out build/layout-report
```

Wie die Layout-Engine funktioniert (Problemformulierung, Algorithmen, Gewichte, Tuning, Grenzen):
[`docs/layout-engine.md`](docs/layout-engine.md). Vorher/Nachher-Vergleich: [`docs/comparison/index.md`](docs/comparison/index.md).

---

## Syntax-Beispiele

### Minimaler 4-Zeiler (Pins werden automatisch inferiert)
```text
ESP32.GND -> Sensor.GND, Display.GND
ESP32.3V3 -> Sensor.VCC, Display.VCC
ESP32.GPIO21(SDA) -> Sensor.SDA, Display.SDA : I2C_SDA
ESP32.GPIO22(SCL) -> Sensor.SCL, Display.SCL : I2C_SCL
```

### Vollständiges Beispiel mit Komponenten-Deklaration & Bus
```text
ESP32: ESP32-WROOM-32
  pins: 3V3, GND, GPIO21(SDA), GPIO22(SCL), GPIO4(INT)

BME280: Temp & Humidity Sensor
  pins: VIN, GND, SCL, SDA

OLED: 0.96" SSD1306
  pins: VDD, GND, SCK, SDA

MPU6050: 6-Axis IMU
  pins: VCC, GND, SCL, SDA, INT

# Power Rail (3.3V)
ESP32.3V3 -> BME280.VIN, OLED.VDD, MPU6050.VCC : 3V3

# Ground Rail
ESP32.GND -> BME280.GND, OLED.GND, MPU6050.GND : GND

# I2C Bus (SDA & SCL)
ESP32.GPIO21 -> BME280.SDA, OLED.SDA, MPU6050.SDA : I2C_SDA
ESP32.GPIO22 -> BME280.SCL, OLED.SCK, MPU6050.SCL : I2C_SCL

# Dedicated Interrupt line
ESP32.GPIO4 -> MPU6050.INT : SENSOR_INT
```

### Standardbauteile (Diskrete Symbole statt großer IC-Cards)
Kompakte passive und diskrete Standard-Elemente wie Widerstände, Kondensatoren, Schalter, Sicherungen, Dioden und LEDs werden automatisch als schlanke 2D-Schaltsymbole gerendert (60x40px bzw. 40x40px) statt als große IC-Karten:

```text
# 1. Inline mit Typ und Wert:
SpeedPullUp: resistor 2kOhm
  pins: 1, 2

FilterCap: capacitor 100nF
  pins: 1, 2

PowerSwitch: switch SPDT
  pins: 1, 2, 3

Fuse: fuse Inline
  pins: IN, OUT

# 2. Mit expliziten Eigenschaften (kind / symbol & value):
SpeedPullUp: 2kOhm
  kind: resistor      # oder symbol: resistor / standard: resistor
  pins: 1, 2

FilterCap:
  kind: capacitor
  value: 100nF
  pins: 1, 2

# 3. Automatische Erkennung aus natürlicher Bezeichnung (wie in example_large.txt):
SpeedPullUp: 2kOhm Resistor
  pins: 1, 2

SpeedCap: 100nF Capacitor
  pins: 1, 2

PowerSwitch: Slide Switch (SPDT)
  pins: 1, 2, 3

Fuse: Inline Fuse
  pins: IN, OUT
```

### Eigene Grafik-Bauteile (Fritzing-Stil)
Bauteile lassen sich mit einem Bild und rastergenauen Pin-Koordinaten definieren. Das Layout darf sie standardmäßig um 90° drehen, um eine bessere Platzierung zu prüfen; nur mit `rotatable: false` wird das für ein Bauteil explizit abgeschaltet:

```text
part INA228:
  image: parts/image-Photoroom.png
  size: 11x9
  image_rect: 8, 17, 204, 164
  # rotatable: false        # optional: Rotation für dieses Bauteil verbieten
  pins:
    VIN: 2, 8
    GND: 3, 8
    SDA: 5, 8
    SCL: 4, 8

INA228: INA228
```

Die Rotation wirkt auch auf normale Bauteile (nicht nur auf Bilder) und wird pro Bauteil nur dann übernommen, wenn die geroutete Zeichnung dadurch messbar lesbarer wird.

### YAML-Format (alternativ)
```yaml
components:
  MCU:
    type: ESP32
    pins: [3V3, GND, IO21, IO22]
  Sensor:
    type: BME280
    pins: [VIN, GND, SDA, SCL]
connections:
  - MCU.GND -> Sensor.GND
  - MCU.3V3 -> Sensor.VIN
  - MCU.IO21 -> Sensor.SDA : I2C_SDA
  - MCU.IO22 -> Sensor.SCL : I2C_SCL
```

---

## Projektstruktur

```
schematic.txt/
├── app.py                      # Einstiegspunkt (Webserver & CLI)
├── schematic/                  # Core-Logik (Pure Python, unabhängig vom Webserver)
│   ├── __init__.py             # Zentrale API: parse_schematic, layout_schematic, generate_schematic_svg
│   ├── models.py               # Datenmodelle (Component, Pin, Net, WireSegment, NetLabel, ...)
│   ├── parser.py               # Parser für DSL & YAML mit präzisen Zeilen-Fehlermeldungen
│   ├── serialize.py            # Geometrie <-> JSON (Vergleich verschiedener Engine-Versionen)
│   ├── styles.py               # Netzfarben nach semantischer Klasse
│   ├── layout/
│   │   ├── engine.py           # Ablauf: normalisieren, analysieren, Kandidaten platzieren/routen, auswählen
│   │   ├── config.py           # Alle Gewichte & Parameter, Effort-Stufen
│   │   ├── normalize.py        # Netze, die einen Pin teilen, zusammenführen
│   │   ├── semantics.py        # Masse/Versorgung/Signal, Rails, Busse, Differenzpaare, Bauteil-Rollen
│   │   ├── placer.py           # Placement-Kern (Satelliten, Pin-Konfiguration, Legalisierung)
│   │   ├── seeds.py            # Startplatzierungen: geschichtete Spalten / Stress-Majorisierung
│   │   ├── search.py           # Lokale Suche auf dem geschätzten Routing-Ziel
│   │   ├── estimate.py         # Schnelle Routing-Schätzung für das Placement
│   │   ├── pin_allocator.py    # Bauteilgröße & Pin-Koordinaten
│   │   ├── router.py           # Orthogonaler Grid-Router (A*, Steiner-Bäume, Rip-up & Reroute)
│   │   ├── labels.py           # Platzierung der Netznamen
│   │   └── metrics.py          # Qualitätsmetriken & Lesbarkeits-Score
│   └── renderer/
│       └── svg.py              # SVG-Generierung (IC-Boxen, Symbole, Leitungen, Labels)
├── web/                        # Web-Oberfläche (Minimalistischer PoC)
│   ├── server.py               # Python Built-in HTTP Server (Zero Dependencies)
│   └── static/                 # index.html, style.css, app.js
├── tools/
│   └── layout_report.py        # Vorher/Nachher-Bericht mit Metriken und Bildern
├── docs/
│   ├── layout-engine.md        # Architektur, Algorithmen, Gewichte, Tuning, Grenzen
│   └── comparison/             # Vorher/Nachher-Zeichnungen und Metriken
├── examples/                   # Beispiel-Schaltpläne
└── tests/                      # Unit-, Qualitäts- und Determinismus-Tests
    ├── layout_cases/           # 13 Layout-Testfälle (Kette, Bus, Differenzpaar, Cluster, ...)
    └── layout_quality_baseline.json
```

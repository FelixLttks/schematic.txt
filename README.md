# Schematic.txt - Text-to-Schematic 2D Engine

Ein modulares, textbasiertes CAD/Schaltplan-Werkzeug zur Beschreibung von elektronischen Schaltungen und automatischer Erzeugung von echten 2D-Schaltplänen.

---

## Highlights

- **Textbasiertes Format (Git-tauglich)**: Reiner Text (DSL oder YAML). Einfach zu versionieren, per Diff nachvollziehbar und konfliktarm zu mergen.
- **Verbindungen beschreiben, nicht zeichnen**: Einfache Schreibweise wie `ESP32.GND -> Sensor.GND, OLED.GND`. Pins und Komponenten werden bei Bedarf automatisch inferiert.
- **Automatisches 2D-Placement**: Kein manuelles Positionieren von X/Y-Koordinaten nötig. Erkennt Hubs (z. B. Microcontroller wie ESP32) und Peripheriegeräte (Sensoren, Displays, Module) und ordnet sie im 2D-Raum an.
- **Echte 2D-Verdrahtung (kein WireViz 1D-Kabelbaum)**: Orthogonales Manhattan-Routing mit Bus-Trunks (z. B. I2C SDA/SCL, SPI, Power-Rails) und automatischen Abzweig-Junction-Dots (`●`).
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
```

### 3. Tests ausführen
```bash
python3 -m unittest discover tests
```

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
│   ├── __init__.py             # Zentrale API: parse_schematic, generate_schematic_svg
│   ├── models.py               # Datenmodelle (Component, Pin, Net, WireSegment, etc.)
│   ├── parser.py               # Parser für DSL & YAML mit präzisen Zeilen-Fehlermeldungen
│   ├── layout/
│   │   ├── __init__.py
│   │   ├── pin_allocator.py    # Pin-Zuweisung & Bauteil-Dimensionierung
│   │   ├── placer.py           # 2D-Placement (Topologie, Kanal-Abstände, Kraftmodell)
│   │   └── router.py           # Orthogonales Manhattan-Routing & Bus-Junctions
│   └── renderer/
│       ├── __init__.py
│       └── svg.py              # SVG-Generierung (Vektorgrafik, IC-Symbole, Leitungen)
├── web/                        # Web-Oberfläche (Minimalistischer PoC)
│   ├── __init__.py
│   ├── server.py               # Python Built-in HTTP Server (Zero Dependencies)
│   └── static/
│       ├── index.html          # 50/50 Split-Screen (Editor / Preview)
│       ├── style.css           # Minimalistisches Styling ohne Header/Gradients
│       └── app.js              # Live-Debounce-Compiler, Pan & Zoom, Error-Handling
├── examples/                   # Beispiel-Schaltpläne
│   ├── esp32_sensors_bus.txt
│   ├── esp32_sd_spi.txt
│   └── simple_quickstart.txt
└── tests/                      # Unit-Tests
    ├── test_parser.py
    ├── test_layout.py
    └── test_renderer.py
```

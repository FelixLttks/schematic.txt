# Layout-Engine v3: Ziel, Analyse, Recherche, Entwurf

Dieses Dokument beschreibt, warum die Layout-Engine neu geschrieben wurde,
nach welchem Modell sie arbeitet und wie man sie weiterentwickelt.

---

## 1. Das Ziel: Übersichtlichkeit

Das Werkzeug dokumentiert Verdrahtung. Ein Mensch soll jede Leitung von Pin
zu Pin verfolgen und die Funktionsgruppen erkennen können. Platzsparend muss
die Zeichnung nicht sein.

„Übersichtlich“ heißt konkret: Das Auge kann **einer Leitung folgen**, ohne
an einer Stelle unsicher zu werden, und es erkennt **Gruppen** auf einen
Blick. Daraus folgen prüfbare Eigenschaften:

**Eine Leitung**

| | Eigenschaft | Warum | Umsetzung |
|---|---|---|---|
| L0 | **Eindeutig und vollständig** | eine mehrdeutige Zeichnung ist falsch | harte Regeln: jeder Pin verbunden, keine Leitung durch Bauteile oder fremde Anschlüsse, keine geteilten Leitungsstücke, fremde Netze berühren sich nur als gerade, rechtwinklige Kreuzung |
| L1 | **Wenige Ecken, Zweipunkt-Leitungen gerade** | das Auge folgt Geraden (Gestalt: gute Fortsetzung); ein Knick, der durch 2 Raster Verschiebung verschwände, wirkt wie ein Fehler | Knick-Kosten; Ausrichtungszüge (einzeln und für ganze gerade verbundene Ketten) |
| L2 | **Keine Kreuzungen**, wenn nötig rechtwinklig | stärkster Lesefehler-Faktor (Purchase 1997; Ware et al. 2002) | Kreuzungs-Kosten; Router kreuzt nur rechtwinklig |
| L3 | **Kurz und direkt** | eine Leitung quer über die Zeichnung ist schwer zu verfolgen und kreuzt mehr | Längen-Kosten (nicht vernachlässigbar, s. u.) |
| L4 | **Pins zeigen zu ihren Partnern** | sonst Umwege um das eigene Bauteil | Pin-Seiten freier Pins sind Optimierungsvariablen |

**Mehrere Leitungen**

| | Eigenschaft | Umsetzung |
|---|---|---|
| B1 | **Bündel parallel**: Leitungen zwischen denselben Bauteilen (SPI, UART, I²C, Motoranschluss) laufen direkt nebeneinander, gleiche Reihenfolge, knicken gemeinsam | Bündel = Signalnetze, die *genau dieselben Bauteile* verbinden (rein strukturell). Router: Strafe pro Schritt ohne Partner daneben, keine Abstandsstrafe zwischen Partnern, Partner werden nacheinander geroutet. Baryzentrische Pin-Reihenfolge verhindert Verdrillen |
| B2 | **Busse und Versorgung als Stamm mit geraden Abzweigen** | Schätzung: Single-Trunk-Steiner-Modell; Router: Steiner-Baum |
| B3 | **Fremde Leitungen halten Abstand** | Kosten pro Schritt im 20-px-Abstand zu einer fremden Leitung (Schätzung und Router) |
| B4 | **Pins werden nicht eingekesselt** | der Punkt vor einem Pin gehört dessen Netz (hart), der zweite ist teuer |

**Bauteile und Gesamtbild**

| | Eigenschaft | Umsetzung |
|---|---|---|
| P1 | **Zusammengehöriges beieinander** | Leitungslänge, analytische Startlösung (Partner ziehen sich an) |
| P2 | **Lesbare Beschriftung** | Textzonen sind Teil der Bauteilfläche, Router meidet sie; Pin-Namen von Bildbauteilen laufen entlang der eigenen Leitung |
| P3 | **Fotos aufrecht** | Bildbauteile nur ±90°, mit Strafe |
| P4 | **Ruhiges, kompaktes Gesamtbild** | Mindestabstand (hart), Umfangsterm |

Daraus folgt der wichtigste Entwurfsgrundsatz: **Es gibt genau ein
Kostenmodell** (`config.Weights`). Dieselben Zahlen bewerten eine Platzierung
(über die Routing-Schätzung), steuern den Router (A*-Schrittkosten) und
bewerten das Ergebnis. Wenn alle drei Stufen dasselbe unter „besser“
verstehen, kann eine Verbesserung an einer Stelle keine Verschlechterung an
einer anderen erzeugen, die das Modell nicht sieht.

Standardgewichte: Kreuzung 12, Ecke 4, Länge 0,4 pro Rasterschritt (20 px),
Abzweig 0,5, Schritt neben fremder Leitung 0,5, Bündelschritt ohne Partner
0,4. Eine Ecke kostet also so viel wie 200 px Umweg, eine Kreuzung so viel wie
drei Ecken, eine Leitung quer über 100 Raster so viel wie vier Ecken. Die
Länge war zuerst mit 0,2 angesetzt; im direkten Bildvergleich waren die
Zeichnungen damit zu breit und hatten zu viele lange Leitungen quer durchs
Bild. Netze mit vielen Anschlüssen (ab 5 Pins: Versorgung, Masse, große Busse)
zählen Ecken und Länge halb – sie müssen überall hin, die Signale sollen
sauber bleiben. Diese Einstufung folgt allein aus der Anzahl der Anschlüsse.

**Namen haben keinen Einfluss.** Netz-, Pin- und Bauteilnamen werden nur zur
Textbreite (Platzbedarf) und zur Farbe (`styles.py`: Masse grau/schwarz,
Versorgung rot) verwendet. Ein Test benennt alle Namen um und verlangt
identische Geometrie (`tests/test_engine.py::TestNamesHaveNoInfluence`).

---

## 2. Analyse der alten Engine (bis Commit `fd67b2c`)

Die alte Engine (~4 500 Zeilen in `placer.py`, `seeds.py`, `search.py`,
`estimate.py`, `router.py`, `semantics.py`, `pin_allocator.py`, …) war ein
Hybrid aus zwei Startplatzierungen (geschichtete Spalten, Stress-Majorisierung),
lokaler Suche auf einer Routing-Schätzung, Grid-A*-Router und Auswahl unter
wenigen Kandidaten nach einem Lesbarkeits-Score. Für das
Lights-Beispiel: **68 Kreuzungen, 80 Knicke, Score 27,8, 16 s**.

### Warum jede Korrektur neue Fehler erzeugte

1. **Drei verschiedene Kostenmodelle.** Die Platzierungsschätzung
   (`PlacementWeights`: Knick 5, Kreuzung 9, Länge 0,12), der Router
   (`RoutingWeights`: Knick 10, Kreuzung 8, Schritt 1) und die Auswahl
   (`ScoreWeights`: exponentieller Score pro Verbindung) bewerteten
   dieselbe Zeichnung unterschiedlich. Was die Suche für besser hielt, war
   für den Router oft schlechter und für den Score wieder anders. Jede
   Gewichtskorrektur verschob das Gleichgewicht zwischen den Modellen.
2. **Regeln statt Optimierung.** Viele Entscheidungen waren fest kodierte
   Heuristiken mit gegenseitigen Abhängigkeiten: Satelliten für
   Pull-ups/Filter (oben/unten nach Konvention), „gestapelte Teile legen
   Versorgungspins nach außen“, „Busgeräte zeigen zur Quelle“, „Pin mit
   Partner direkt darüber → äußere Seite“, Orientierungs- und
   Drehvarianten als nachgelagerte Einzelversuche. Jede Regel war für einen
   Fall richtig und für einen anderen falsch; die Optimierung konnte sie
   nicht überstimmen, weil sie keine Variablen waren.
3. **Schätzung und Router uneinig über das Erlaubte.** Der Router sperrt
   (zu Recht) den Rasterpunkt vor fremden Pins; die Schätzung legte Jogs
   genau dorthin. Platzierungen, die laut Schätzung gut waren, waren in
   Wirklichkeit eng oder unroutbar.
4. **Grobe Ausrichtung.** Lokale Suche mit Verschiebungen 20/60/140 px und
   anschließende Legalisierung, die Teile wieder verschob – exakte
   Ausrichtung (Leitung gerade statt 2 Raster versetzt) war Zufall. Genau das
   sind die Fälle „LightGateResistor 2 höher“ und „PowerSwitch 2 tiefer“.
5. **Wenige echte Kandidaten.** Drei Startvarianten plus ein paar Dreh- und
   Orientierungsproben; die Wahl fiel per Proxy-Score, den das eigene
   Dokument als unzuverlässig beschrieb („the score can prefer a drawing that
   looks less orderly“).
6. **Versteckte Namensabhängigkeit.** Iterationen über nach Namen sortierte
   Mengen beeinflussten Gleichstände.

Das Muster „ich finde einen offensichtlichen Fehler, fixe ihn, ein neuer
entsteht“ ist die typische Folge von 1 + 2: Es gibt keine einzige Instanz,
die zwei Lösungen konsistent vergleicht.

---

## 3. Recherche

### Ähnliche Projekte

| Projekt | Ansatz | Bezug |
|---|---|---|
| **WireViz** (jetzt *filare*) | YAML → Graphviz, Kabelbaum/Steckerbelegung, BOM | dokumentiert einzelne Kabel, ausdrücklich nicht die Gesamtverdrahtung eines Systems; 1D, keine 2D-Platzierung |
| **netlistsvg** | Yosys-Netzliste → ELK *layered* | Schichten-Layout mit Port-Bedingungen; gut für Datenfluss, schwach bei bidirektionalen Hubs |
| **d3-hwschematic** | elkjs, geschichtet + orthogonales Routing | dasselbe Prinzip für Hardware-Schaltbilder |
| **Weave** (arXiv 2026) | Netzliste → LTspice via elkjs *layered*, Raster-Snapping, Rücklese-Prüfung der Konnektivität | Konnektivität als harte, nachgeprüfte Bedingung (wie hier `metrics.py`); scheitert v. a. an dichten Mehrpin-Modulen |
| **tscircuit autolayout** | Vorlagen-/Muster-Matching + Packing, Zeilen/Spalten-Layouts | regelbasiert, Beta |
| **SKiDL / KiCad-Export** | Netzliste aus Python, Schaltplan ohne gutes Auto-Layout | — |
| **HOLA** (Kieffer, Dwyer, Marriott, Wybrow 2016) | Nutzerstudie zu menschlichem Zeichnen, Stress + Bäume außen + Ausrichtung | Beleg: Menschen bevorzugen Raster-Ausrichtung, gerade Linien, Kompaktheit nachrangig |
| **libavoid / Adaptagrams** (Wybrow et al. 2009) | Orthogonales Connector-Routing, danach „Nudging“ | Router-Ideen |
| Patente zu Schaltplangeneratoren | Vorlagen für Analog-Topologien; Kritik: klassische Generatoren optimieren Leitungslänge statt Lesbarkeit | bestätigt die Zielsetzung |

### Mathematische Modelle

| Modell | Eignung |
|---|---|
| **Sugiyama / geschichtet mit Port-Bedingungen** (Sugiyama 1981; Schulze et al. 2014) | sehr ordentlich bei gerichteten Flüssen; zwingt aber alles in Spalten und braucht Kantenrichtungen; ein ESP32 mit Pins auf beiden Seiten ist ein Zentrum, kein Fluss |
| **Topology-Shape-Metrics, Knickminimierung per Min-Cost-Flow** (Tamassia 1987) | optimal nur bei fester planarer Einbettung; Bauteile mit vielen festen Ports passen schlecht |
| **ILP / MIP** (Nöllenburg & Wolff 2011 für U-Bahn-Pläne; Mutzel & Weiskircher) | exakt, harte/weiche Bedingungen sauber; braucht einen Solver (z. B. OR-Tools CP-SAT) und skaliert bei Kreuzungsvariablen schlecht |
| **Quadratische (analytische) Platzierung mit Pin-Offsets** (VLSI-Placement) | löst die *globale* Anordnung in Millisekunden; Pin-Richtungen lassen sich als „Ziel vor dem Pin“ einbauen |
| **Simulated Annealing auf beliebiger Zielfunktion** (Kirkpatrick 1983; Davidson & Harel 1996; Stott et al. 2011 für U-Bahn-Pläne) | optimiert genau die gewünschte Zielfunktion, deterministisch mit festem Seed; braucht eine schnelle, *ehrliche* Schätzung |
| **Rechtwinklige Steiner-Bäume, Single-Trunk-Heuristik** (Hanan 1966; FLUTE) | realistisches Modell für Busse und Versorgung |
| **A* mit Richtungszustand, Rip-up & Reroute / PathFinder** (Hart et al. 1968; McMurchie & Ebeling 1995) | Standard für Grid-Routing mit Knickkosten und harten Regeln |

**Gewählt:** VLSI-artige Zweistufen-Platzierung (analytisch global +
Annealing lokal) auf einer ehrlichen, mit dem Router übereinstimmenden
Schätzung; A*-Router mit denselben Gewichten; mehrere unabhängige Läufe
parallel, die besten werden echt geroutet. Begründung: Die Zielfunktion ist
frei wählbar (keine Spaltenpflicht), jede Entscheidung (Position, Drehung,
Pin-Seite) ist eine Variable statt einer Regel, und es braucht keine neuen
Abhängigkeiten (reines Python, wie bisher).

**Ausprobiert und verworfen: exakte Feinausrichtung mit CP-SAT.** Modell:
Positionen ganzzahlig, Anordnung bleibt (pro Bauteilpaar eine der aktuell
gültigen Trennungen), Boolesche Variable „Verbindung gerade“, gerade
Leitungen dürfen kein Bauteil schneiden, Ziel: eingesparte Ecken minus
Verschiebung. Gelöst in 0,1–0,2 s. Ergebnis: Im Lights-Beispiel wurde eine
Verbindung mehr gerade, aber die Ausrichtung legte fremde Leitungen auf
dieselben Linien (Konflikte 2 → 23); mit Längenterm zog es die Zeichnung
zusammen und erzeugte noch mehr Konflikte. Ein Ausrichtungsmodell, das die
übrigen Leitungen nicht kennt, verbessert die Übersicht nicht – die lokale
Suche richtet bereits mit der vollständigen Schätzung aus (auch ganze
gerade verbundene Ketten). Deshalb keine neue Abhängigkeit.

---

## 4. Architektur

```
text ─parse─▶ Schematic
  engine.layout_schematic
   1 netlist.py     Netze mit gemeinsamem Pin zusammenführen, Pins auflösen,
                    Netzgewicht aus Anschlusszahl
   2 placement.py   je Teilschaltung und Lauf:
                      analytischer Start → Polieren → Simulated Annealing
                      → Polieren   (Läufe parallel in Prozessen)
   3 engine.py      beste Läufe (nach Schätzung) echt routen (router.py),
                    bei offenen Pins Korridore aufweiten und neu routen,
                    beste Zeichnung nach echten Kosten
   4 labels.py      Netznamen
   5 metrics.py     unabhängige Prüfung + Kennzahlen
render (renderer/svg.py)
```

| Modul | Aufgabe |
|---|---|
| `config.py` | **das** Kostenmodell und die Aufwandsstufen |
| `shapes.py` | Bauteilgeometrie in Rastereinheiten: Varianten (Drehungen), Pins, Textzonen |
| `netlist.py` | Normalisierung, Netzgewichte, Bündel, Teilschaltungen |
| `estimate.py` | Routing-Schätzung: Leitungsformen, Stamm-Modell, Kreuzungen/Konflikte |
| `placement.py` | Platzierungszustand mit inkrementellen Kosten, Start, Annealing, Polieren |
| `router.py` | Grid-A*, harte Regeln, Steiner-Bäume, Rip-up & Reroute, Recovery |
| `engine.py` | Ablauf, Kandidatenwahl, Korridor-Aufweitung, Ausgabe |
| `labels.py`, `metrics.py` | Netznamen; Prüfung und Bewertung |

### 4.1 Bauteile (`shapes.py`)

Alles in ganzzahligen Rastereinheiten. Varianten: Zweipol-Symbole 4
Drehungen, Umschalter 2 Spiegelungen, Bildbauteile 0/±90° (nie 180°: Fotos auf dem Kopf sind schwer lesbar; nur 0°
bei `rotatable: false`), Boxen 1 Variante. Erklärte Pin-Seiten sind
verbindlich. Jedes Bauteil hat einen **Footprint** aus Körper, Anschlüssen
und Textzonen (Pin-Beschriftungen von Bildbauteilen laufen *entlang* der
eigenen Leitung außerhalb des Anschlusses; Name/Wert der Symbole).

### 4.2 Platzierung (`placement.py`)

**Variablen:** Position, Variante, und **die Seite jedes freien Box-Pins**.
Die Reihenfolge der Pins auf einer Seite folgt der Höhe ihrer Partner
(Baryzentrum – verhindert Selbstkreuzungen eines Bündels); die Seite selbst
ist eine Optimierungsvariable (Annealing-Züge: Pin umklappen, Bündel
umklappen, Box neu ausrichten).

**Harte Bedingung:** zwischen Footprints mindestens `clearance` freie
Rasterlinien (Standard 2: genug für die Anschlussfelder beider Seiten).

**Zielfunktion:** Summe der geschätzten Netzkosten + Kreuzungen, Konflikte
und Nebeneinander-Laufen fremder Netze + Umfang + Drehstrafe für
Bildbauteile. Bündelpartner dürfen (und sollen) nebeneinander laufen; dass
sie dieselbe Linie teilen, kostet nur ein Viertel eines Konflikts (der
Router legt sie auf Nachbarspuren).

**Schätzung (`estimate.py`):**

* Jede Pin-zu-Pin-Verbindung bekommt die billigste Form, die beide
  Pin-Richtungen respektiert (gerade, L, Z/U, 3 oder 4 Knicke), mit freien
  Koordinaten aus Mittelpunkten und den Kanten der Bauteile im Weg.
* Hindernisse sind Bauteilkörper **und** die Rasterpunkte fremder
  Anschlüsse und der Punkte direkt davor – dieselben Punkte, die der Router
  sperrt. Damit sind Schätzung und Router sich einig, was erlaubt ist.
* Netze mit ≥ 3 Anschlüssen: Minimum aus Spannbaum (MST) und
  **Single-Trunk-Steiner-Baum** (ein gerader Stamm, Abzweige gerade, als L
  oder als U um das eigene Bauteil). Ohne das Stamm-Modell bestraft die
  Schätzung genau die übersichtliche Bus-Anordnung.
* Zwischen zwei Netzen zählen echte Kreuzungen und **Konflikte**: gleiche
  Linie oder ein Leitungsende auf einer fremden Leitung. Der Router muss
  dort kreuzen oder ausweichen; Konflikte kosten deshalb wie eine Kreuzung.

Die Kosten werden bei jedem Zug **inkrementell** aktualisiert (nur Netze in
der Nähe der bewegten Teile). Ein Test vergleicht nach jedem Zug mit einer
vollständigen Neuberechnung – Abweichung wäre ein Fehler, nicht Rauschen.

**Ablauf je Lauf:**

1. *Start analytisch* – quadratische Leitungslänge mit Pin-Richtungen: jede
   Verbindung zieht den Partner-Anschluss auf einen Punkt vor dem eigenen
   Pin (in beide Richtungen). Gauss-Seidel-Iterationen; alle 10 Iterationen
   beste Variante je Teil und vorläufige Seiten freier Pins. Dann
   Legalisierung (nächster freier Platz in Verbindungsreihenfolge). Ein
   konstruktiver Start (Teil für Teil an den billigsten Platz) war im
   Messvergleich deutlich schlechter und wurde entfernt.
2. *Polieren* (steilster Abstieg: jede Verbindung gerade ziehen, ±1 Raster,
   Variante, Pin-Seite).
3. *Simulated Annealing* über strukturierte Züge: verschieben, Verbindung
   ausrichten (auch „vor den Pin setzen“ und eine ganze gerade verbundene
   Kette gemeinsam), neben einen Nachbarn setzen, tauschen, drehen, eine
   ganze Seite der Zeichnung verschieben, Pin-Seiten (einzeln, als Bündel,
   eine Box zusammen mit ihren Partner-Boxen – sonst bleiben beide Enden
   eines Bündels falsch herum, weil jede Hälfte allein schlechter wäre).
   Starttemperatur aus der mittleren Verschlechterung, geometrische Abkühlung,
   der beste Zustand wird behalten.
4. *Polieren* – jede Verbindung, die durch Ausrichten gerade werden kann,
   wird es.

### 4.3 Routing (`router.py`)

A* auf dem 20-px-Raster über Zustände (Knoten, Richtung), Kosten aus
`Weights`. Harte Regeln: kein Körper, kein fremder Anschluss, kein Punkt vor
einem fremden Pin; Pins werden gerade verlassen; keine geteilten
Leitungsstücke; fremde Netze teilen einen Punkt nur als gerade, rechtwinklige
Kreuzung; keine Schleifen, keine Vierfach-Knoten. Mehrpunkt-Netze wachsen als
Steiner-Baum (nächster Anschluss an den nächsten gültigen Baumknoten).
Bündel werden nacheinander geroutet; ein Bündelmitglied zahlt für jeden
Schritt ohne gerouteten Partner direkt daneben, fremde Leitungen im
20-px-Abstand kosten, das zweite Feld vor einem fremden Pin kostet (damit
Pins nicht eingekesselt werden). Nicht verbindbare Anschlüsse:
Konflikt-Recovery (Weg mit hohen Strafen statt Verboten suchen, genau die
blockierenden Netze aufreißen, neu routen) – nach den Rip-up-Durchläufen
immer, solange ein Pin offen ist.

Bleibt ein Pin offen, **weitet die Engine Korridore auf**: an einer freien
Schnittlinie vor dem Pin werden Rasterlinien eingefügt (alles jenseits der
Linie rückt weiter). Das erhält jede Ausrichtung exakt und kann keine
Überlappung erzeugen. Letzte Stufe: gleichmäßiges Spreizen aller Lücken.

### 4.4 Auswahl

`restarts` Läufe mit verschiedenen Seeds, parallel in Prozessen (`fork`;
Ergebnisse in fester Reihenfolge, daher identisch zur sequenziellen
Ausführung); die `routed_candidates` mit der niedrigsten Schätzung werden
echt geroutet und mit den Kennzahlen des Berichts bewertet; die günstigste
Zeichnung gewinnt. Deterministisch: feste Seeds, Eingabereihenfolge statt
Hash- oder Namensreihenfolge.

Messung (10 aussagekräftige Fälle × 4 Seeds, Summe der Mittelwerte, gleiche
Referenzgewichte): Suchaufwand ist der Haupthebel. 150 → 1000 Züge je
Bauteil: −18 %; 1000 → 2500: nur noch −4 %. Die Streuung zwischen Seeds
ist groß (±20 %), deshalb mehrere Läufe und der beste gewinnt.

| Stufe | Läufe | Züge je Bauteil | echt geroutet | Lights (19 Teile) |
|---|---:|---:|---:|---:|
| `fast` | 2 | 300 | 1 | ~20 s |
| `balanced` | 6 | 1000 | 3 | ~2 min |
| `thorough` | 14 | 2500 | 4 | ~10 min |

---

## 5. Qualitätsprüfung (`metrics.py`)

Unabhängig vom Router aus der ausgegebenen Geometrie: Leitungen werden in
Rasterkanten zerlegt. Verstöße: offene Anschlüsse, geteilte Kanten,
mehrdeutige Berührungen, Leitung durch Bauteil, Kontakt mit fremdem
Anschluss, Überlappung von Bauteilen, schräge Leitungen. Kennzahlen:
Kreuzungen, Knicke, Länge, Abzweige, gerade Zweipunkt-Verbindungen,
Bündel-Zusammenhalt (Anteil der Bündelleitung direkt neben einem Partner),
Schritte neben fremden Leitungen, Kosten (dieselben Gewichte) und ein
Lesbarkeits-Score 0–100 (nur für Menschen).

---

## 6. Ergebnisse

Alle 20 Eingaben (Beispiele + Testfälle), alte Engine (`custom_components`,
Commit `fd67b2c`) gegen neue Engine (`balanced`), **beide mit denselben
Metriken bewertet** (`tools/layout_report.py --baseline custom_components
--effort balanced`):

| Fall | Kosten alt → neu | Kreuzungen | Knicke | Zeit (s) |
|---|---:|---:|---:|---:|
| example_lights | 1358 → 761 | 68 → 28 | 80 → 67 | 19 → 100 |
| example_large | 765 → 717 | 38 → 31 | 46 → 56 | 10 → 67 |
| esp32_sd_spi | 209 → 81 | 8 → 0 | 14 → 13 | 1 → 6 |
| esp32_sensors_bus | 277 → 206 | 12 → 7 | 20 → 19 | 2 → 8 |
| 05_i2c_bus | 294 → 208 | 16 → 9 | 24 → 29 | 5 → 16 |
| 02_branching | 39 → 27 | 0 → 0 | 5 → 4 | 0,3 → 3 |
| 06_differential_pairs | 81 → 63 | 2 → 0 | 8 → 11 | 0,2 → 6 |
| 12_labels_annotations | 78 → 61 | 2 → 0 | 11 → 15 | 0,5 → 8 |
| 03_dense_network | 224 → 383 | 8 → 11 | 23 → 43 | 2 → 23 |
| 13_functional_regions | 265 → 294 | 12 → 11 | 21 → 24 | 3 → 29 |
| 07_crossing_prone | 192 → 228 | 6 → 9 | 20 → 21 | 0,6 → 6 |
| **Summe aller 20** | | **252 → 143** | 395 → 425 | 64 → 401 |

* Kreuzungen insgesamt −43 %, im Lights-Beispiel −59 %; Kosten besser in
  15 von 20 Fällen; keine Verstöße (wie vorher).
* **Schlechter** in 5 Fällen, deutlich nur in `03_dense_network`: Dort
  findet die Suche eine nach den *eigenen* Metriken bessere Anordnung (die
  der alten Engine) nicht. Das ist ein Suchproblem, kein Modellproblem; mit
  `thorough` lag derselbe Fall bei 297.
* **Laufzeit** ist 5–10× höher. Das ist der Preis dafür, dass jede
  Entscheidung gegen die echte Zielfunktion geprüft wird.
* **Streuung**: einzelne Läufe desselben Falls unterscheiden sich um
  ±15–20 % in den Kosten (z. B. example_large 579 / 614 / 717 in drei
  Messungen mit kleinen Codeänderungen dazwischen). Mehr Läufe senken das.

---

## 7. Tests

| Datei | Prüft |
|---|---|
| `test_estimate.py` | Leitungsformen, Hindernisse, Pin-Vorfelder, Faltungen, Kreuzungen/Konflikte, Stamm-Modell |
| `test_placement.py` | inkrementelle Kosten = Neuberechnung (für alle Zugtypen), Abstands-Invariante, Pin-Seiten als Variable |
| `test_router.py` | harte Regeln an Handbeispielen, ehrliche Fehlermeldung, Baumstruktur |
| `test_engine.py` | alle Beispiele und Testfälle ohne Verstoß, Raster, Labels frei, SVG; gerade Kette; Bündel; Namen ohne Einfluss; gleiche SVG über Prozesse mit anderem Hash-Seed |
| `test_parser.py`, `test_renderer.py`, `test_custom_parts.py` | Parser, Renderer, Bildbauteile |

`python3 -m unittest discover tests`

---

## 8. Grenzen und nächste Schritte

* **Laufzeit.** Reines Python. `fast` braucht für 19 Bauteile etwa 10–20 s,
  `balanced` 1–2 min. Die Web-Vorschau nutzt standardmäßig `fast`.
* **Suchrauschen.** Annealing ist stochastisch (deterministisch je Seed);
  einzelne Läufe streuen. Mehr Läufe (`thorough`) glätten das.
* **Feinausrichtung exakt lösen.** Für eine feste grobe Anordnung ist die
  Frage „welche Verbindungen können gleichzeitig gerade sein“ ein kleines
  ganzzahliges Problem; mit OR-Tools CP-SAT wäre es exakt lösbar
  (neue Abhängigkeit, daher noch nicht umgesetzt).
* **Versorgung als Symbole.** Masse/Versorgung werden als Leitungen
  gezeichnet (Verdrahtungsdokumentation). Ein optionaler Modus mit
  Versorgungssymbolen statt Leitungen würde große Zeichnungen stark
  entlasten – das müsste eine bewusste Entscheidung des Nutzers sein (z. B.
  pro Netz in der Eingabe), nicht eine Namensregel.
* **Rip-up** bewertet jedes Netz einzeln; eine verhandelnde Variante
  (PathFinder mit Historienkosten) wäre robuster bei sehr dichten Korridoren.

---

## Literatur

* Benkert, Nöllenburg, Uno, Wolff. *Minimizing Intra-Edge Crossings in Wiring Diagrams and Public Transportation Maps.* GD 2006.
* Davidson, Harel. *Drawing Graphs Nicely Using Simulated Annealing.* ACM TOG 15(4), 1996.
* Hanan. *On Steiner's Problem with Rectilinear Distance.* SIAM J. Appl. Math. 14, 1966.
* Hart, Nilsson, Raphael. *A Formal Basis for the Heuristic Determination of Minimum Cost Paths.* IEEE SSC 4(2), 1968.
* Kieffer, Dwyer, Marriott, Wybrow. *HOLA: Human-like Orthogonal Network Layout.* IEEE TVCG 22(1), 2016. <https://ialab.it.monash.edu/~dwyer/papers/hola2015.pdf>
* Kirkpatrick, Gelatt, Vecchi. *Optimization by Simulated Annealing.* Science 220, 1983.
* McMurchie, Ebeling. *PathFinder: A Negotiation-Based Performance-Driven Router for FPGAs.* FPGA 1995.
* Mutzel, Weiskircher. *Bend Minimization in Orthogonal Drawings Using Integer Programming.* <https://www.ac.tuwien.ac.at/files/pub/Mutzel02b.pdf>
* Nöllenburg, Wolff. *Drawing and Labeling High-Quality Metro Maps by Mixed-Integer Programming.* IEEE TVCG 17(5), 2011.
* Purchase. *Which Aesthetic Has the Greatest Effect on Human Understanding?* GD 1997.
* Schulze, Spönemann, von Hanxleden. *Drawing Layered Graphs with Port Constraints.* JVLC 2014. <https://rtsys.informatik.uni-kiel.de/~biblio/downloads/papers/jvlc13.pdf>
* Stott, Rodgers, Martinez-Ovando, Walker. *Automatic Metro Map Layout Using Multicriteria Optimization.* IEEE TVCG 2011.
* Sugiyama, Tagawa, Toda. *Methods for Visual Understanding of Hierarchical System Structures.* IEEE SMC 11(2), 1981.
* Tamassia. *On Embedding a Graph in the Grid with the Minimum Number of Bends.* SIAM J. Comput. 16(3), 1987.
* Ware, Purchase, Colpoys, McGill. *Cognitive Measurements of Graph Aesthetics.* Information Visualization 1(2), 2002.
* Wybrow, Marriott, Stuckey. *Orthogonal Connector Routing.* GD 2009.
* *Weave: Verified Netlist-to-Schematic Conversion via Layered Graph Layout.* arXiv:2607.03835. <https://arxiv.org/abs/2607.03835>
* WireViz / filare. <https://pypi.org/project/wireviz>
* netlistsvg. <https://github.com/nturley/netlistsvg>
* d3-hwschematic. <https://www.skypack.dev/view/d3-hwschematic>
* tscircuit autolayout. <https://github.com/tscircuit/autolayout>
* Eclipse ELK Layered. <https://eclipse.dev/elk/blog/posts/2025/25-08-21-layered.html>

# Fortschritt Layout-Engine v3

## Erledigt

- Alte Engine analysiert und vollständig entfernt (bleibt in der Git-Historie, Commit `fd67b2c`).
- Neue Engine in `schematic/layout/` von Grund auf neu gebaut:
  - **ein** Kostenmodell (`config.Weights`) für Placement-Schätzung, Router und Endbewertung;
  - Routing-Schätzung stimmt mit dem Router überein (Pin-Vorfelder als Hindernisse, Konflikte, Stamm-Modell für Busse/Versorgung);
  - Placement: analytischer Start + Simulated Annealing + Polieren; Position, Drehung und Seiten freier Pins sind Optimierungsvariablen; ganze gerade verbundene Ketten werden gemeinsam verschoben;
  - Bündel (Netze zwischen denselben Bauteilen) laufen parallel; Nähe zu fremden Leitungen kostet;
  - Router: A* mit harten Eindeutigkeitsregeln, Steiner-Bäume, Rip-up & Reroute, Reparatur offener Pins, Korridor-Aufweitung;
  - unabhängige Läufe parallel (fork), deterministisch;
  - Bildbauteile nur ±90° gedreht, Pin-Beschriftungen entlang der eigenen Leitung.
- Namen haben nachweislich keinen Einfluss auf die Geometrie (Test benennt alles um).
- Gewichte per Mehr-Seed-Messung und Bildvergleich festgelegt (u. a. Länge 0,4, Nähe 0,5).
- CP-SAT-Feinausrichtung ausprobiert und verworfen (keine neue Abhängigkeit), Begründung in `docs/layout-engine.md`.
- Tests: 50/50 grün (`python3 -m unittest discover tests`).
- Vergleich alt → neu (balanced, gleiche Metriken): Kosten besser in 18 von 20 Fällen, Kreuzungen 252 → 138, Leitungen deutlich kürzer. Bericht: `build/layout-report/index.md`.
- Doku `docs/layout-engine.md` (Ziel, Analyse, Recherche, Modell) und README aktualisiert; Web-Vorschau standardmäßig `fast`.

## Offen / nächste Schritte

1. **Rückschritte untersuchen:** `03_dense_network` (Kreuzungen 8 → 13, Ecken 23 → 41) und `esp32_custom_part` (Ecken 11 → 23; Bündel/Versorgung verlaufen umständlich).
2. Ergebnisse in `docs/layout-engine.md` Abschnitt 6 auf den aktuellen Vergleich aktualisieren (Zahlen dort stammen noch vom Lauf mit Länge 0,2).
3. Laufzeit: balanced ~1–2 min für 19 Bauteile; weitere Beschleunigung der Schätzung bringt direkt mehr Qualität.
4. Versorgungsnetze (GND/3V3) erzeugen weiterhin viel Leitungsaufkommen; optionaler Symbol-Modus nur als ausdrückliche Eingabe-Option (Entscheidung des Nutzers).
5. Optional: freie Box-Pins auch oben/unten zulassen (Renderer-Kopfzeile müsste angepasst werden).

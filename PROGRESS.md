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
- Rückschritte untersucht (2026-10-20): Ursache war die Auswahl, nicht das Modell. Die Schätzung sagt die geroutete Qualität nur grob voraus (dense: Schätzung 338 → geroutet 454, Schätzung 415 → 350), Routing kostet ~1/10 eines Platzierungslaufs. Jetzt werden **alle** Läufe geroutet; `balanced` = 14 × 500 Züge statt 6 × 1000 (gleiche Laufzeit), `thorough` = 28 × 1500. Summe über 19 Fälle: Kosten 4191 → 4043, Kreuzungen 112 → 102, Knicke 328 → 296.
- Vergleich alt → neu aktualisiert: Kosten besser in **allen 20** Fällen (7852 → 5143), Kreuzungen 252 → 132. `03_dense_network` 343 → 336 (vorher 454), `esp32_custom_part` 218 → 192 (vorher 226). Doku Abschnitt 4.4 und 6 aktualisiert.
- Nachoptimierung am fertigen Bild (`engine._refine`, 2026-10-20): Züge des Annealings auf dem Gewinner, je Runde die vielversprechendsten echt geroutet (schnelles Routing ohne Aufweitung/Reparatur), nur Verbesserungen der Berichtskosten bleiben. Budgets je Stufe in `config.py` (`refine_rounds/batch/patience`). Wirkung (19 Fälle): fast 4962 → 4538, balanced 4043 → 3808, Kreuzungen 102 → 90. `thorough` (28 × 1500 + 60 Runden) nochmals deutlich besser: dense 240, functional_regions 216, large 599, lights 880.
- Vergleich alt → neu (balanced): Kosten besser in allen 20 Fällen, 7852 → 4823, Kreuzungen 252 → 116. Doku 4.4, 4.5, 6, 8 und README aktualisiert.

## Offen / nächste Schritte

1. Schätzung genauer machen (verbessert die Annealing-Suche selbst): Mehrpunkt-Netze (Stamm-Modell vs. echter Steiner-Baum, z. B. GND in dense: geschätzt 77 Schritte, geroutet 145) und Bündel, deren Pin-Reihenfolge an beiden Enden nicht passt (I2C_SCL in dense: geschätzt ~1, geroutet 7 Kreuzungen). Diagnose: Schätzung je Netz neben `Router.net_cost` legen.
2. Pin-Reihenfolge an Boxen: Baryzentrum der Partner-Höhen ist für Mehrpunkt-Netze ungünstig (esp32_custom_part: OLED.VDD oben, 3V3 muss den I²C-Bus kreuzen). Reihenfolge als echte Optimierungsvariable (Tausch-Zug) wäre eine Option.
3. Laufzeit: balanced ~2 min, thorough ~9 min für 19 Bauteile. Schnellere Schätzung und inkrementelles Routing (nur betroffene Netze neu) bringen direkt mehr Qualität pro Zeit.
6. Offene Entscheidung: Soll der Export in der Web-Oberfläche automatisch mit `thorough` neu layouten (Minuten bei großen Schaltungen)? Derzeit wird die angezeigte Vorschau exportiert.
4. Versorgungsnetze (GND/3V3) erzeugen weiterhin viel Leitungsaufkommen; optionaler Symbol-Modus nur als ausdrückliche Eingabe-Option (Entscheidung des Nutzers).
5. Optional: freie Box-Pins auch oben/unten zulassen (Renderer-Kopfzeile müsste angepasst werden).

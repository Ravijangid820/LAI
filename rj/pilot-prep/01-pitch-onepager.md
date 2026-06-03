# LAI — Pilot pitch (one-pager)

**Status:** DRAFT for boss/rj review · **Updated:** 2026-06-03

---

## Was wir bauen / What we build

**LAI** ist eine KI-gestützte Plattform für deutsche Windenergie-Verträge
und Due-Diligence-Prüfungen, gebaut für deutsche Rechtsanwälte und
DD-Boutiquen.

* **Retrieval-grounded:** Antworten gestützt auf einen 35,7 Millionen-
  Stellen deutschen Rechtskorpus (BImSchG, BauGB, EEG, 29 wind-relevante
  Bundesgesetze in `corpus_*` Tabellen), jede Aussage mit klickbarem
  `[C-n]`-Quellenhandle.
* **Document-grounded:** Ihre hochgeladenen Mandantsunterlagen werden
  per-Mandat als `[M-n]`-Handles isoliert; nie verwechselt mit dem
  öffentlichen Korpus.
* **Multi-modus:** Vertragsanalyse (PPA, EPC, Pacht, Netzanschluss),
  juristische Recherche, DDiQ-Berichtsgenerator für strukturierte
  Befunde.
* **EU-konform:** EU-origin, on-premise-Option verfügbar, EU AI Act
  Art. 12 Audit-Log eingebaut.

## Was wir empirisch belegen können / What we can prove empirically

Wir messen, was wir behaupten — und veröffentlichen die Lücken.

| Was wir gemessen haben | Was wir gefunden haben |
|---|---|
| End-to-end Retrieval (n=200 echte BImSchG-Fragen) | Recall@30 = 0.49 — die ehrliche Obergrenze unseres Index |
| Sechs Tuning-Experimente an vier Schichten | Eine Verbesserung (BM25 v5, +14 % schneller); fünf dokumentierte Negativresultate |
| Drei Produktionsfehler aus dem 06-01 Audit | Code-Layer-Fix für alle drei, 1029 Unit-Tests + Live-Probes (4/4 PASS) |
| Citation-Validator | Funktioniert für fabrizierte Handles (live verifiziert); off-topic-mit-echten-Zitaten ist außerhalb seines Designs und wird auf Routing-Ebene gefangen |
| EU AI Act Art. 12 / 13 / 14 / 15 | Code-Stellen + Commits pro Artikel zugeordnet; 9 offene Lücken ehrlich benannt |

**Warum das für einen Piloten zählt:** wir sagen Ihnen, was wir
können, BEVOR Sie es kaufen. Wenn unser Modell die Antwort auf eine
Ihrer Fragen nicht findet, sehen Sie das in der Metrik — nicht erst
nach drei Monaten Frust.

## Was Sie als Pilotkunde bekommen / What you get as pilot

* **4–8 Wochen kostenfreier Vollzugang** zur Produktion-LAI-Instanz.
* **Ihr Mandat, isoliert** — Ihre Dokumente bleiben in einer
  per-Organisation segregierten Matter-Ansicht; kein Training-Reuse
  ohne Ihre schriftliche Zustimmung.
* **Direkter Zugang zur Engineering-Seite** — wöchentlicher 30-Minuten
  Check-in mit rj; gemeldete Fehler werden in den ersten 24 h trianguliert
  und im Sprint-Fenster (typisch 3–7 Tage) behoben.
* **EU AI Act Art. 12 Audit-Log inklusive** — jede Anmeldung, jede
  Abfrage, jeder Bericht-Export wird append-only protokolliert; CSV/JSON
  Export für Ihren DPO verfügbar.
* **On-premise Option verfügbar** — wenn Ihre IT-Sicherheit das
  verlangt, deployen wir in Ihrer Infrastruktur (zusätzlicher Setup-
  Aufwand, technische Spezifikation auf Anfrage).

## Was wir von Ihnen brauchen / What we ask in return

* **1 reales Mandat** — vorzugsweise eine Windpark-DD oder ein
  laufender Vertragsstreit, der ohne uns 40+ Stunden Anwalts-Zeit
  kosten würde.
* **2–3 Anwälte** bereit, das System auf realer Arbeit zu testen und
  pro Woche 30-60 Minuten strukturiertes Feedback zu geben (was hat
  funktioniert, was war falsch, was wäre wertvoll gewesen).
* **Ein Endgespräch** nach 6–8 Wochen — Diskussion, ob (a) Sie weiter
  mit uns arbeiten möchten, (b) wir Sie als Referenzkunde benennen
  dürfen, (c) wie ein kommerzielles Angebot aussehen würde.

**Keine Verpflichtung über das Pilotende hinaus.** Wenn das Produkt
für Ihre Praxis nicht passt, gehen wir auseinander — wir haben
Engineering-Signal, Sie haben 4–8 Wochen kostenfreie Werkzeuge.

## Warum jetzt / Why now

* Phase 1 (Anti-Stillschweigen, Telemetrie, Smoke-Tests) ✅ geliefert
* Phase 2 (Audit-Log, Pilot-Ready) ✅ geliefert
* Phase 3 (BImSchG-spezialisierte Fine-Tune) **steht und wartet auf
  Sie** — wir trainieren das Modell auf reale Mandatsfragen IHRER
  Praxis, nicht auf synthetische Beispiele aus unserem Sandbox.

Der Pilot ist nicht „testet ein experimentelles Produkt mit uns" —
es ist „wir bauen die nächste Iteration MIT Ihnen, mit Ihrem
Use-Case als Referenzpunkt."

## Honest gaps (so it can't blow up the second meeting)

* Kein veröffentlichtes Model-Card; in Arbeit.
* Kein formelles Konformitätsbewertungs-Verfahren (EU AI Act); kommt
  vor kommerzieller Auslieferung.
* Lawyer-blinded A/B Evaluation ist gebaut (`/eval` Route, 50 BImSchG-
  Fragen) aber noch nicht mit echten Anwälten gefahren — der Pilot
  IST die Gelegenheit dafür.
* Geographie-Wissen für deutsche Windgemeinden (Treuenbrietzen,
  Heidenau, etc.) ist eine bekannte Lücke — wird mit Pilot-Daten
  geschlossen.

## Wer wir sind

LAI-Team:
* **rj (Backend / Retrieval / Ops)** — verantwortlich für Produktion,
  Audit-Pipeline, Statute-Feed
* **harsh (Phase 3 / Model-Recipe / Strategy)** — verantwortlich für
  Fine-Tuning-Plan, Modellbewertung, EU AI Act Mapping
* **boss (Sales / Kunde)** — verantwortlich für die Pilot-Konversation

Sitz: Blockland AE. EU-Datenresidenz garantiert.

---

## Kontakt

**[boss-name] · [boss-email] · [boss-phone]**

Anhang verfügbar: EU AI Act Compliance Map (`harsh/EU_AI_ACT.md`),
Audit-Pipeline-Spezifikation, on-premise Deployment-Skizze.

---

*Erstellt 2026-06-03 von der LAI-Engineering-Seite. Jede in diesem
Dokument behauptete Zahl ist mit Commit-Hash und Mess-Methode
nachvollziehbar im Repository belegt.*

# Outreach email — template (DRAFT)

**Status:** DRAFT for boss/rj review. German version is the primary;
English fallback below it.

**Tonality:** the doc reads as a peer talking to a peer — same field,
same constraints, no hyperbole. The opening is calibrated to
catch the prospect's attention without sounding like generic AI-
product cold-mail.

---

## German version (primary)

**Betreff:** Pilot-Anfrage: KI-Unterstützung für Windenergie-DD, mit
ehrlichem Engineering-Stand

Sehr geehrte/r {Anrede + Nachname},

ich schreibe aus dem LAI-Team. Wir bauen seit fünf Monaten eine
KI-Plattform für deutsche Windenergie-Verträge und
Due-Diligence-Prüfungen — und sind jetzt am Punkt, an dem
Engineering-Iterationen ohne realen Anwender keine Phasenfortschritt
mehr bringen. Daher die Anfrage: **wäre {Firmenname} an einem 4-8
wöchigen kostenfreien Piloten interessiert?**

Was wir konkret anbieten:

* **Voller Zugang** zu LAI für eine reale Windpark-DD oder einen
  laufenden Vertragsstreit Ihrer Kanzlei.
* **Datentrennung pro Mandat** — Ihre Dokumente bleiben in einer
  organisations-segregierten Sicht, kein Training-Reuse ohne Ihre
  schriftliche Zustimmung.
* **EU AI Act Art. 12 Audit-Log** integriert — jede Anmeldung, jede
  Abfrage, jeder Bericht-Export wird append-only protokolliert; CSV-
  Export für Ihren DPO verfügbar.
* **Wöchentlicher 30-Minuten Check-in** mit unserem Engineering;
  gemeldete Fehler binnen 24 h trianguliert.

Was wir von Ihnen brauchen:

* **2–3 Anwälte**, die das System auf realer Arbeit testen und ~30-60
  Minuten pro Woche strukturiertes Feedback geben.
* **Ein Endgespräch** nach 6–8 Wochen — keine Verpflichtung darüber
  hinaus.

Warum ich Sie konkret anschreibe: {Firmenname} hat — soweit
öffentlich nachvollziehbar — {konkrete Verbindung: "die Repowering-
Akquise von Windpark X in 2024 begleitet" / "in der Hude-Hatten-
Streitigkeit auf der Käuferseite vertreten" / "regelmäßig BImSchG-
Verfahren auf Erneuerbaren-Seite führt"}. Genau der Mandatsmix, für
den unser Werkzeug gebaut ist.

Drei Sätze zum Engineering-Stand, damit Sie nicht erst in einem
Kennenlernen erfahren, was funktioniert und was nicht:

1. **Unser Retrieval-Layer ist empirisch vermessen** — Recall@30 =
   0.49 über 200 reale BImSchG-Testfragen; wir wissen die obere
   Schranke unseres Index und stehen dazu (Negativresultate
   inklusive).
2. **Drei Produktionsfehler aus dem Mai sind gefixt + verifiziert
   live** — Routing-Probleme bei Meta-Fragen ("Was kann ich hier
   tun?"), Sprach-Drift bei deutschen Anfragen, und Vertrags-
   Modus-Übergrundung. Code-Layer-Fixes mit 1029 Unit-Tests + Live-
   End-to-End-Probes.
3. **Wir haben offene Lücken** — kein veröffentlichtes Model-Card,
   keine formelle EU-Konformitätsbewertung, kein operativer
   Kill-Switch. Wir bauen das in Phase 3 (LoRA Fine-Tune), und der
   Pilot ist der natürliche Lieferant für die Trainingsdaten.

Wenn das für {Firmenname} interessant klingt, schlage ich ein 30-
minütiges erstes Gespräch in den nächsten 1-2 Wochen vor. Anhängend
finden Sie unsere EU AI Act Coverage Map als Diskussionsgrundlage.

Mit besten Grüßen,
**{boss-name}**
{boss-title}
LAI

---

📎 Anhang: `EU_AI_ACT.md` (LAI-Konformitätsmapping zu Art. 12-15)
📎 Anhang (optional): `boss-status-2026-06-03.md` (kompakter
Engineering-Status)
📎 Anhang (optional): `01-pitch-onepager.md` (Pilot-Konditionen +
Honest-gaps-Sektion)

---

## English fallback (if German contact prefers English)

**Subject:** Pilot inquiry — AI for German wind-energy DD, with an
honest engineering snapshot

Dear {salutation + last name},

I'm writing from the LAI team. We've spent the last five months
building an AI platform for German wind-energy contracts and due
diligence, and we've reached the point where engineering iterations
without a real user stop producing meaningful phase progress. Hence
this email: **would {firm name} be interested in a 4-8 week free
pilot?**

What we'd concretely offer:

* **Full access** to LAI for one of your firm's real wind-park DDs
  or active contract disputes.
* **Per-matter data segregation** — your documents stay in an
  organisation-isolated matter view; no training reuse without your
  written consent.
* **EU AI Act Art. 12 audit log** integrated — every login, query,
  and report export is append-only logged; CSV export available for
  your DPO.
* **Weekly 30-minute check-in** with our engineering side; reported
  bugs triangulated within 24 hours.

What we'd ask from you:

* **2–3 lawyers** willing to test the system on real work and provide
  ~30-60 minutes per week of structured feedback.
* **A closing conversation** after 6–8 weeks — no obligations beyond.

Why I'm reaching out to {firm name} specifically: as far as I can
tell from public sources, you {specific connection — "led the
repowering acquisition of Wind Park X in 2024" / "represented the
buyer side in the Hude-Hatten dispute" / "regularly run BImSchG
proceedings on the renewables side"}. Exactly the matter mix our tool
is built for.

Three sentences on the engineering state, so you don't have to wait
for a first conversation to learn what works and what doesn't:

1. **Our retrieval layer is empirically measured** — Recall@30 = 0.49
   over 200 real BImSchG test queries; we know our index ceiling and
   own it (including the negative tuning results).
2. **Three production failures from May are fixed + verified live** —
   meta-question routing ("what can I do here?"), German language
   drift, and contract-mode over-grounding. Code-layer fixes with
   1029 unit tests + live end-to-end probes.
3. **We have open gaps** — no published model card, no formal EU
   conformity assessment, no operator kill-switch yet. We're building
   them in Phase 3 (LoRA fine-tune), and the pilot is the natural
   source of training data.

If that sounds interesting for {firm name}, I propose a 30-minute
first call in the next 1-2 weeks. Attached is our EU AI Act coverage
map as a discussion starting point.

Best regards,
**{boss-name}**
{boss-title}
LAI

---

## Filling-in checklist (per-firm)

Before sending:

- [ ] {Anrede + Nachname} — formal salutation
- [ ] {Firmenname} — firm name in both places
- [ ] {konkrete Verbindung} — ONE specific, recent project/case at
      that firm related to wind / DD / BImSchG. Public sources only;
      "your firm's renewables practice" is too generic.
- [ ] {boss-name + boss-title} — signature
- [ ] Re-read the third paragraph ("Engineering-Stand") to make sure
      no overclaim has crept in
- [ ] Attach the EU AI Act doc
- [ ] Send from boss's email, not rj's

## What this email deliberately does NOT do

* No "AI-powered automation" buzzwords
* No "transform your practice" hyperbole
* No undefined claims ("highest accuracy", "industry-leading")
* No pressure tactics or deadlines
* No "exclusive offer" — anyone we send this to is a potential pilot

The credibility move is *being honest about a measured ceiling*. A
firm whose partners read engineering literature will recognise that
shape and respond. A firm that wants generic-AI-vendor copy isn't
our pilot match anyway.

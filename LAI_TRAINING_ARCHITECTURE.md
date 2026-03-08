# LAI Training Architecture: Brain (Conductor) + Specialized Modules
# LAI Training-Architektur: Brain (Dirigent) + Spezialisierte Module

**Version:** 1.0
**Date / Datum:** 2025-11-28
**Status:** Documentation / Dokumentation

---

## Table of Contents / Inhaltsverzeichnis

1. [Executive Summary / Zusammenfassung](#1-executive-summary--zusammenfassung)
2. [System Architecture Overview / Systemarchitektur-Übersicht](#2-system-architecture-overview--systemarchitektur-übersicht)
3. [Training Data Sources / Trainingsdaten-Quellen](#3-training-data-sources--trainingsdaten-quellen)
4. [The LAI Brain (Conductor) / Das LAI Brain (Dirigent)](#4-the-lai-brain-conductor--das-lai-brain-dirigent)
5. [Specialized Expert Modules / Spezialisierte Experten-Module](#5-specialized-expert-modules--spezialisierte-experten-module)
6. [Training Pipeline / Training-Pipeline](#6-training-pipeline--training-pipeline)
7. [Data Flow Diagram with Arrow Explanations / Datenfluss-Diagramm mit Pfeil-Erklärungen](#7-data-flow-diagram-with-arrow-explanations--datenfluss-diagramm-mit-pfeil-erklärungen)
8. [Inference Flow / Inferenz-Ablauf](#8-inference-flow--inferenz-ablauf)
9. [Technical Implementation / Technische Implementierung](#9-technical-implementation--technische-implementierung)
10. [Expected Results / Erwartete Ergebnisse](#10-expected-results--erwartete-ergebnisse)

---

## 1. Executive Summary / Zusammenfassung

### English

The LAI (Legal AI) platform uses a **hierarchical AI architecture** consisting of:

- **LAI Brain (Conductor)**: A central orchestration model based on Llama 3 70B with LoRA fine-tuning that coordinates all analysis tasks
- **6 Specialized Expert Modules**: Domain-specific models for Contract, Economic, Grid, Land, Legal, and BImSchG analysis
- **RAG System**: Retrieval-Augmented Generation using 40GB of legal literature stored in pgvector

The system learns from **1TB of real data**:
- 40GB Literature (Laws, Guidelines, Court Decisions)
- 500GB Data Rooms (Real Wind Energy Projects)
- 500GB DD Reports (Expert Evaluations = Ground Truth)

### Deutsch

Die LAI (Legal AI) Plattform nutzt eine **hierarchische KI-Architektur** bestehend aus:

- **LAI Brain (Dirigent)**: Ein zentrales Orchestrierungs-Modell basierend auf Llama 3 70B mit LoRA Fine-tuning, das alle Analyseaufgaben koordiniert
- **6 Spezialisierte Experten-Module**: Domänenspezifische Modelle für Vertrags-, Wirtschafts-, Netz-, Grundstücks-, Rechts- und BImSchG-Analyse
- **RAG System**: Retrieval-Augmented Generation mit 40GB juristischer Literatur in pgvector

Das System lernt aus **1TB echter Daten**:
- 40GB Literatur (Gesetze, Leitfäden, Urteile)
- 500GB Datenräume (Echte Windenergie-Projekte)
- 500GB DD-Berichte (Experten-Bewertungen = Ground Truth)

---

## 2. System Architecture Overview / Systemarchitektur-Übersicht

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                         │
│                              COMPLETE SYSTEM ARCHITECTURE                               │
│                              VOLLSTÄNDIGE SYSTEMARCHITEKTUR                             │
│                                                                                         │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                         │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│   │                         TRAINING DATA LAYER                                      │   │
│   │                         TRAININGSDATEN-SCHICHT                                   │   │
│   │                                                                                 │   │
│   │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                │   │
│   │   │  📚 40GB        │  │  📁 500GB       │  │  📋 500GB       │                │   │
│   │   │  LITERATURE     │  │  DATA ROOMS     │  │  DD REPORTS     │                │   │
│   │   │  LITERATUR      │  │  DATENRÄUME     │  │  DD-BERICHTE    │                │   │
│   │   └────────┬────────┘  └────────┬────────┘  └────────┬────────┘                │   │
│   │            │                    │                    │                          │   │
│   │            └────────────────────┼────────────────────┘                          │   │
│   │                                 │                                               │   │
│   └─────────────────────────────────┼───────────────────────────────────────────────┘   │
│                                     │                                                   │
│                                     ▼                                                   │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│   │                         PROCESSING LAYER                                         │   │
│   │                         VERARBEITUNGS-SCHICHT                                    │   │
│   │                                                                                 │   │
│   │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                │   │
│   │   │  🔢 VECTOR DB   │  │  📄 STRUCTURED  │  │  📊 TRAINING    │                │   │
│   │   │  (pgvector)     │  │  DOCUMENTS      │  │  SET (JSONL)    │                │   │
│   │   │  Embeddings     │  │  Dokumente      │  │  50k-200k       │                │   │
│   │   └────────┬────────┘  └────────┬────────┘  └────────┬────────┘                │   │
│   │            │                    │                    │                          │   │
│   │            └────────────────────┼────────────────────┘                          │   │
│   │                                 │                                               │   │
│   └─────────────────────────────────┼───────────────────────────────────────────────┘   │
│                                     │                                                   │
│                                     ▼                                                   │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│   │                         AI MODEL LAYER                                           │   │
│   │                         KI-MODELL-SCHICHT                                        │   │
│   │                                                                                 │   │
│   │                    ┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓                                 │   │
│   │                    ┃     🧠 LAI BRAIN        ┃                                 │   │
│   │                    ┃     (CONDUCTOR/DIRIGENT)┃                                 │   │
│   │                    ┃                         ┃                                 │   │
│   │                    ┃     Llama 3 70B + LoRA  ┃                                 │   │
│   │                    ┗━━━━━━━━━━━┳━━━━━━━━━━━━━┛                                 │   │
│   │                                │                                                │   │
│   │         ┌──────────┬───────────┼───────────┬───────────┐                       │   │
│   │         │          │           │           │           │                       │   │
│   │         ▼          ▼           ▼           ▼           ▼                       │   │
│   │   ┌──────────┐┌──────────┐┌──────────┐┌──────────┐┌──────────┐┌──────────┐    │   │
│   │   │📜CONTRACT││💰ECONOMIC││⚡GRID    ││🏛️LAND    ││⚖️LEGAL   ││🌿BImSchG │    │   │
│   │   │  EXPERT  ││  EXPERT  ││  EXPERT  ││  EXPERT  ││  EXPERT  ││  EXPERT  │    │   │
│   │   │  15%     ││  25%     ││  15%     ││  20%     ││  15%     ││  20%     │    │   │
│   │   └──────────┘└──────────┘└──────────┘└──────────┘└──────────┘└──────────┘    │   │
│   │                                                                                 │   │
│   └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                                   │
│                                     ▼                                                   │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│   │                         OUTPUT LAYER                                             │   │
│   │                         AUSGABE-SCHICHT                                          │   │
│   │                                                                                 │   │
│   │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                │   │
│   │   │  📊 RISK SCORE  │  │  🚦 TRAFFIC     │  │  📋 REPORTS     │                │   │
│   │   │  RISIKO-SCORE   │  │  LIGHT SYSTEM   │  │  BERICHTE       │                │   │
│   │   │  0-100          │  │  AMPELSYSTEM    │  │  JSON/XLSX/HTML │                │   │
│   │   └─────────────────┘  └─────────────────┘  └─────────────────┘                │   │
│   │                                                                                 │   │
│   └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Training Data Sources / Trainingsdaten-Quellen

### 3.1 Literature Library / Literatur-Bibliothek (40GB)

| Category / Kategorie | Content / Inhalt | Purpose / Zweck |
|---------------------|------------------|-----------------|
| **Laws / Gesetze** | EEG, BImSchG, BNatSchG, BauGB | Legal requirements / Rechtliche Anforderungen |
| **Guidelines / Leitfäden** | BWE, VDMA, TA Lärm, TA Luft | Best practices / Beste Praktiken |
| **Court Decisions / Urteile** | BVerwG, OVG, VG | Legal precedents / Rechtsprechung |
| **Studies / Studien** | Market studies, Technical reports | Market context / Marktkontext |

**Usage / Verwendung:** RAG System for factual knowledge
**Nutzung:** RAG System für Faktenwissen

### 3.2 Virtual Data Rooms / Datenräume (500GB)

| Project / Projekt | Content / Inhalt |
|-------------------|------------------|
| WP Altmark | Full project documentation |
| WP Beppener Bruch | Contracts, Permits, Reports |
| WP Butterberg | Technical specifications |
| WP Hudehatten | Grid connection documents |
| WP Lamstedt | Economic assessments |
| WP Sebbenhausen | Land security documents |
| WP Tostedt | BImSchG applications |
| WP Zodel | Complete DD package |
| WP 33:34 | Reference project |

**Usage / Verwendung:** Pattern recognition, Document understanding
**Nutzung:** Mustererkennung, Dokumentenverständnis

### 3.3 Due Diligence Reports / DD-Berichte (500GB)

**This is the Ground Truth! / Das ist die Ground Truth!**

| Content / Inhalt | Value / Wert |
|------------------|--------------|
| Expert evaluations / Experten-Bewertungen | Scores, Ratings |
| Professional findings / Professionelle Erkenntnisse | Risk assessments |
| Recommendations / Empfehlungen | Action items |

**Usage / Verwendung:** Training labels (Input → Expert Output)
**Nutzung:** Training-Labels (Input → Experten-Output)

---

## 4. The LAI Brain (Conductor) / Das LAI Brain (Dirigent)

### 4.1 Role and Responsibilities / Rolle und Verantwortlichkeiten

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│                         🧠 LAI BRAIN / DIRIGENT                                │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                                                                         │   │
│   │   RESPONSIBILITIES / VERANTWORTLICHKEITEN:                             │   │
│   │                                                                         │   │
│   │   1. ORCHESTRATION / ORCHESTRIERUNG                                    │   │
│   │      • Receives user requests / Empfängt Nutzeranfragen               │   │
│   │      • Determines required analyses / Bestimmt erforderliche Analysen │   │
│   │      • Coordinates module execution / Koordiniert Modul-Ausführung    │   │
│   │                                                                         │   │
│   │   2. TASK ROUTING / AUFGABEN-ROUTING                                   │   │
│   │      • Identifies document types / Erkennt Dokumenttypen              │   │
│   │      • Routes to specialist modules / Leitet an Spezialisten weiter   │   │
│   │      • Manages parallel execution / Verwaltet parallele Ausführung    │   │
│   │                                                                         │   │
│   │   3. CONTEXT SYNTHESIS / KONTEXT-SYNTHESE                              │   │
│   │      • Aggregates module results / Aggregiert Modul-Ergebnisse        │   │
│   │      • Cross-validates findings / Kreuz-validiert Erkenntnisse        │   │
│   │      • Identifies conflicts / Erkennt Konflikte                       │   │
│   │                                                                         │   │
│   │   4. FINAL SCORING / FINALE BEWERTUNG                                  │   │
│   │      • Applies weighted scoring / Wendet gewichtete Bewertung an      │   │
│   │      • Determines traffic light status / Bestimmt Ampelstatus         │   │
│   │      • Generates recommendations / Generiert Empfehlungen             │   │
│   │                                                                         │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   TECHNICAL SPECIFICATION / TECHNISCHE SPEZIFIKATION:                          │
│                                                                                 │
│   • Base Model: Meta Llama 3 70B                                               │
│   • Fine-tuning: LoRA (Low-Rank Adaptation)                                    │
│   • LoRA Rank: 64                                                              │
│   • LoRA Alpha: 128                                                            │
│   • Training Data: 50k-200k examples from DD reports                           │
│   • Hardware: 4x NVIDIA A100 80GB                                              │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Brain Decision Logic / Brain Entscheidungslogik

```python
# Pseudo-code for Brain orchestration
# Pseudo-Code für Brain-Orchestrierung

class LAIBrain:
    """
    Central orchestration model
    Zentrales Orchestrierungs-Modell
    """

    def analyze_project(self, documents: List[Document]) -> RiskAssessment:
        """
        Main entry point for project analysis
        Haupteinstiegspunkt für Projektanalyse
        """

        # Step 1: Identify document types
        # Schritt 1: Dokumenttypen identifizieren
        document_mapping = self.classify_documents(documents)

        # Step 2: Route to specialists (parallel)
        # Schritt 2: An Spezialisten weiterleiten (parallel)
        module_tasks = {
            'contract': self.contract_expert.analyze(document_mapping['contracts']),
            'economic': self.economic_expert.analyze(document_mapping['financials']),
            'grid': self.grid_expert.analyze(document_mapping['grid_docs']),
            'land': self.land_expert.analyze(document_mapping['land_docs']),
            'legal': self.legal_expert.analyze(documents),  # All docs
            'bimschg': self.bimschg_expert.analyze(document_mapping['permits'])
        }

        # Step 3: Collect results
        # Schritt 3: Ergebnisse sammeln
        results = await asyncio.gather(*module_tasks.values())

        # Step 4: Synthesize and score
        # Schritt 4: Synthetisieren und bewerten
        final_assessment = self.synthesize_results(results)

        return final_assessment
```

---

## 5. Specialized Expert Modules / Spezialisierte Experten-Module

### 5.1 Module Overview / Modul-Übersicht

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│                    6 SPECIALIZED EXPERT MODULES                                │
│                    6 SPEZIALISIERTE EXPERTEN-MODULE                            │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  📜 CONTRACT EXPERT / VERTRAGS-EXPERTE                    Weight: 15%  │   │
│   │                                                                         │   │
│   │  Trained on / Trainiert auf:                                           │   │
│   │  • Lease agreements / Pachtverträge                                    │   │
│   │  • Clause analysis / Klauselanalyse                                    │   │
│   │  • Termination risks / Kündigungsrisiken                               │   │
│   │  • Liability provisions / Haftungsregelungen                           │   │
│   │                                                                         │   │
│   │  RAG Context / RAG-Kontext:                                            │   │
│   │  • BWE Leitfaden Pachtverträge                                         │   │
│   │  • BGB Pachtrecht                                                      │   │
│   │  • Market benchmarks / Markt-Benchmarks                                │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  💰 ECONOMIC EXPERT / WIRTSCHAFTS-EXPERTE                 Weight: 25%  │   │
│   │                                                                         │   │
│   │  Trained on / Trainiert auf:                                           │   │
│   │  • NPV/IRR calculations / NPV/IRR-Berechnungen                         │   │
│   │  • EEG subsidy analysis / EEG-Vergütungsanalyse                        │   │
│   │  • LCOE calculations / LCOE-Berechnungen                               │   │
│   │  • Amortization periods / Amortisationszeiten                          │   │
│   │                                                                         │   │
│   │  RAG Context / RAG-Kontext:                                            │   │
│   │  • EEG 2023 full text                                                  │   │
│   │  • IRR benchmarks                                                      │   │
│   │  • Market studies / Marktstudien                                       │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  ⚡ GRID EXPERT / NETZ-EXPERTE                             Weight: 15%  │   │
│   │                                                                         │   │
│   │  Trained on / Trainiert auf:                                           │   │
│   │  • Grid connection agreements / Netzanschlussverträge                  │   │
│   │  • Capacity analysis / Kapazitätsanalyse                               │   │
│   │  • Cable route assessment / Kabeltrassen-Bewertung                     │   │
│   │  • Connection costs / Anschlusskosten                                  │   │
│   │                                                                         │   │
│   │  RAG Context / RAG-Kontext:                                            │   │
│   │  • NAV (Niederspannungsanschlussverordnung)                            │   │
│   │  • Grid operator requirements                                          │   │
│   │  • Technical standards / Technische Normen                             │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  🏛️ LAND EXPERT / GRUNDSTÜCKS-EXPERTE                     Weight: 20%  │   │
│   │                                                                         │   │
│   │  Trained on / Trainiert auf:                                           │   │
│   │  • Plot verification / Flurstück-Prüfung                               │   │
│   │  • Land registry / Grundbuch                                           │   │
│   │  • Property rights / Eigentumsrechte                                   │   │
│   │  • Encumbrances / Belastungen                                          │   │
│   │                                                                         │   │
│   │  RAG Context / RAG-Kontext:                                            │   │
│   │  • ALKIS cadastral data                                                │   │
│   │  • Property law / Grundstücksrecht                                     │   │
│   │  • Right-of-way regulations                                            │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  ⚖️ LEGAL EXPERT / RECHTS-EXPERTE                         Weight: 15%  │   │
│   │                                                                         │   │
│   │  Trained on / Trainiert auf:                                           │   │
│   │  • EEG compliance / EEG-Compliance                                     │   │
│   │  • BNatSchG requirements / BNatSchG-Anforderungen                      │   │
│   │  • Court precedents / Rechtsprechung                                   │   │
│   │  • Regulatory changes / Regulatorische Änderungen                      │   │
│   │                                                                         │   │
│   │  RAG Context / RAG-Kontext:                                            │   │
│   │  • All relevant laws / Alle relevanten Gesetze                         │   │
│   │  • Court decisions / Gerichtsentscheidungen                            │   │
│   │  • Legal commentary / Rechtskommentare                                 │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  🌿 BImSchG EXPERT / BImSchG-EXPERTE                       Weight: 20%  │   │
│   │                                                                         │   │
│   │  Trained on / Trainiert auf:                                           │   │
│   │  • Permit validation / Genehmigungsprüfung                             │   │
│   │  • Noise regulations / Lärmschutz                                      │   │
│   │  • Species protection / Artenschutz                                    │   │
│   │  • Environmental conditions / Umweltauflagen                           │   │
│   │                                                                         │   │
│   │  RAG Context / RAG-Kontext:                                            │   │
│   │  • BImSchG full text                                                   │   │
│   │  • TA Lärm, TA Luft                                                    │   │
│   │  • Environmental impact guidelines                                     │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Module Weight Distribution / Modul-Gewichtung

```
                     MODULE WEIGHTS / MODUL-GEWICHTE
                     ════════════════════════════════

    ┌─────────────────────────────────────────────────────────────┐
    │                                                             │
    │   💰 Economic (25%)    ████████████████████████████████████ │
    │                                                             │
    │   🏛️ Land (20%)        ████████████████████████████        │
    │                                                             │
    │   🌿 BImSchG (20%)     ████████████████████████████        │
    │                                                             │
    │   📜 Contract (15%)    ████████████████████                │
    │                                                             │
    │   ⚡ Grid (15%)        ████████████████████                │
    │                                                             │
    │   ⚖️ Legal (15%)       ████████████████████                │
    │                                                             │
    └─────────────────────────────────────────────────────────────┘

    Note / Hinweis: Total exceeds 100% - normalized during scoring
                    Summe > 100% - wird bei Scoring normalisiert
```

---

## 6. Training Pipeline / Training-Pipeline

### 6.1 Three-Phase Training Strategy / Drei-Phasen-Training-Strategie

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│                    TRAINING PIPELINE / TRAINING-PIPELINE                        │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   PHASE 1: RAG SETUP (Weeks 1-3 / Wochen 1-3)                                  │
│   ════════════════════════════════════════════                                  │
│                                                                                 │
│   40 GB Literature / Literatur                                                 │
│        │                                                                        │
│        ▼                                                                        │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    │
│   │   PDF/DOCX  │───▶│   Docling   │───▶│   Chunks    │───▶│  Embeddings │    │
│   │   Reader    │    │   (97.9%)   │    │   (512 tok) │    │  (text-emb) │    │
│   └─────────────┘    └─────────────┘    └─────────────┘    └──────┬──────┘    │
│                                                                   │            │
│                                                                   ▼            │
│                                                          ┌─────────────┐       │
│                                                          │  pgvector   │       │
│                                                          │  Storage    │       │
│                                                          └─────────────┘       │
│                                                                                 │
│   Cost / Kosten: ~$100                                                         │
│   Duration / Dauer: 6-12 hours                                                 │
│   Result / Ergebnis: Semantic search over all laws/guidelines                  │
│                      Semantische Suche über alle Gesetze/Leitfäden             │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   PHASE 2: DATA PREPARATION (Weeks 4-8 / Wochen 4-8)                           │
│   ════════════════════════════════════════════════════                          │
│                                                                                 │
│   500 GB Data Rooms          500 GB DD Reports                                 │
│   500 GB Datenräume          500 GB DD-Berichte                                │
│        │                          │                                             │
│        ▼                          ▼                                             │
│   ┌─────────────┐          ┌─────────────┐                                     │
│   │  Document   │          │   Expert    │                                     │
│   │  Extraction │          │   Ratings   │                                     │
│   └──────┬──────┘          └──────┬──────┘                                     │
│          │                        │                                             │
│          │    ┌─────────────┐     │                                             │
│          └───▶│   MATCHING  │◀────┘                                             │
│               │             │                                                   │
│               │  Project X  │                                                   │
│               │  Document + │                                                   │
│               │  DD Score   │                                                   │
│               └──────┬──────┘                                                   │
│                      │                                                          │
│                      ▼                                                          │
│               ┌─────────────┐                                                   │
│               │  Training   │                                                   │
│               │  Examples   │                                                   │
│               │  (JSONL)    │                                                   │
│               └─────────────┘                                                   │
│                                                                                 │
│   Result / Ergebnis: 50k-200k training examples                                │
│                      50k-200k Trainingsbeispiele                               │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   PHASE 3: FINE-TUNING (Weeks 9-12 / Wochen 9-12)                              │
│   ════════════════════════════════════════════════                              │
│                                                                                 │
│   Training Data (JSONL)                                                        │
│        │                                                                        │
│        ▼                                                                        │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                                                                         │   │
│   │   ┌─────────────┐                                                       │   │
│   │   │ Base Model  │     Meta Llama 3 70B                                 │   │
│   │   │ (70B params)│                                                       │   │
│   │   └──────┬──────┘                                                       │   │
│   │          │                                                              │   │
│   │          │  LoRA Fine-tuning Parameters:                               │   │
│   │          │  • Rank: 64                                                 │   │
│   │          │  • Alpha: 128                                               │   │
│   │          │  • Epochs: 3                                                │   │
│   │          │  • Learning Rate: 2e-5                                      │   │
│   │          │  • Batch Size: 2 (gradient accumulation: 8)                 │   │
│   │          │                                                              │   │
│   │          ▼                                                              │   │
│   │   ┌─────────────┐                                                       │   │
│   │   │ LAI Expert  │     Specialized Wind Energy Model                    │   │
│   │   │ Model       │     Spezialisiertes Windenergie-Modell               │   │
│   │   └─────────────┘                                                       │   │
│   │                                                                         │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   Hardware: 4x NVIDIA A100 80GB (or 8x A6000 48GB)                             │
│   Duration / Dauer: 3-7 days / Tage                                            │
│   Cost / Kosten: $5k-10k (cloud rental / Cloud-Miete)                          │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Training Data Format / Trainingsdaten-Format

```jsonl
{"messages":[
  {"role":"system","content":"Du bist Experte für Windenergie Due Diligence mit 20 Jahren Erfahrung. / You are an expert in wind energy due diligence with 20 years of experience."},
  {"role":"user","content":"Bewerte diesen Pachtvertrag:\n\nLaufzeit: 15 Jahre\nPacht: 22.000 EUR/Jahr\nRegion: Niedersachsen\n..."},
  {"role":"assistant","content":"BEWERTUNG:\nScore: 45/100 (POOR)\n\nLAUFZEIT: Kritisch - 15 Jahre ist deutlich unter Standard (20-25 Jahre).\n\nPACHT: Unterdurchschnittlich - 37% unter Median für Niedersachsen (35.000 EUR).\n\nEMPFEHLUNG: Nachverhandlung erforderlich..."}
]}
```

---

## 7. Data Flow Diagram with Arrow Explanations / Datenfluss-Diagramm mit Pfeil-Erklärungen

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                         │
│              COMPLETE DATA FLOW / VOLLSTÄNDIGER DATENFLUSS                             │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘

   ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
   │  📚 40 GB        │     │  📁 500 GB       │     │  📋 500 GB       │
   │  LITERATURE      │     │  DATA ROOMS      │     │  DD REPORTS      │
   └────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘
            │                        │                        │
            │ ①                      │ ②                      │ ③
            │                        │                        │
            ▼                        ▼                        ▼
   ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
   │  🔢 VECTOR DB    │     │  📄 STRUCTURED   │     │  📊 TRAINING     │
   │  (Embeddings)    │     │  DOCUMENTS       │     │  SET (JSONL)     │
   └────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘
            │                        │                        │
            └────────────────────────┼────────────────────────┘
                                     │
                                     │ ④
                                     ▼
                        ┏━━━━━━━━━━━━━━━━━━━━━━━━━┓
                        ┃     🧠 LAI BRAIN       ┃
                        ┃     (CONDUCTOR)        ┃
                        ┗━━━━━━━━━━━┳━━━━━━━━━━━━┛
                                    │
                                    │ ⑤
                 ┌──────────────────┼──────────────────┐
                 │                  │                  │
                 ▼                  ▼                  ▼
         ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
         │📜 CONTRACT  │    │💰 ECONOMIC  │    │⚡ GRID      │
         │   EXPERT    │    │   EXPERT    │    │   EXPERT    │
         └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
                │                  │                  │
                │ ⑥                │ ⑥                │ ⑥
                │                  │                  │
                └──────────────────┼──────────────────┘
                                   │
                                   │ ⑦
                                   ▼
                        ┏━━━━━━━━━━━━━━━━━━━━━━━━━┓
                        ┃     🧠 LAI BRAIN       ┃
                        ┃     (SYNTHESIS)        ┃
                        ┗━━━━━━━━━━━┳━━━━━━━━━━━━┛
                                    │
                                    │ ⑧
                                    ▼
                        ┌─────────────────────────┐
                        │   📊 FINAL OUTPUT      │
                        │   🚦 TRAFFIC LIGHT     │
                        │   📋 RECOMMENDATIONS   │
                        └─────────────────────────┘
```

### Arrow Explanations / Pfeil-Erklärungen

| # | Arrow / Pfeil | From → To / Von → Nach | Description (EN) | Beschreibung (DE) |
|---|---------------|------------------------|------------------|-------------------|
| **①** | Chunking & Embedding | Literature → Vector DB | 40GB of laws/guidelines are split into 512-token chunks and stored as embeddings in pgvector. Enables semantic search. | 40GB Gesetze/Leitfäden werden in 512-Token-Chunks aufgeteilt und als Embeddings in pgvector gespeichert. Ermöglicht semantische Suche. |
| **②** | Document Extraction | Data Rooms → Structured Docs | 500GB of real project documents are extracted with Docling (97.9% accuracy), categorized, and tagged with metadata. | 500GB echter Projektdokumente werden mit Docling (97.9% Genauigkeit) extrahiert, kategorisiert und mit Metadaten versehen. |
| **③** | Training Example Extraction | DD Reports → Training Set | From 500GB expert DD reports, input-output pairs are generated: "Document X → Expert Evaluation Y". This is the Ground Truth! | Aus 500GB Experten-DD-Berichten werden Input-Output-Paare generiert: "Dokument X → Experten-Bewertung Y". Das ist die Ground Truth! |
| **④** | Training Input | All Sources → LAI Brain | Combined data flows into training: RAG context (facts) + Fine-tuning data (pattern recognition). | Kombinierte Daten fließen ins Training: RAG-Kontext (Fakten) + Fine-tuning-Daten (Mustererkennung). |
| **⑤** | Delegation to Specialists | Brain → Modules | The conductor recognizes the task type and routes to the appropriate expert module. E.g., "Lease contract" → Contract Expert. | Der Dirigent erkennt den Aufgabentyp und leitet an das richtige Experten-Modul weiter. Z.B. "Pachtvertrag" → Contract Expert. |
| **⑥** | Module Result | Module → Brain | Each module returns its partial score + findings + recommendations back to the conductor. | Jedes Modul liefert seinen Teil-Score + Findings + Empfehlungen zurück an den Dirigenten. |
| **⑦** | Aggregated Results | All Modules → Brain | All 6 module results are collected for final synthesis. | Alle 6 Modul-Ergebnisse werden für die finale Synthese gesammelt. |
| **⑧** | Final Output | Brain → Result | The conductor weights, validates, and creates the final score + traffic light + recommendations. | Der Dirigent gewichtet, validiert und erstellt den finalen Score + Ampel + Empfehlungen. |

---

## 8. Inference Flow / Inferenz-Ablauf

### 8.1 Step-by-Step Processing / Schrittweise Verarbeitung

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                         │
│                         INFERENCE FLOW / INFERENZ-ABLAUF                               │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘

STEP 1 / SCHRITT 1: User Request / Nutzeranfrage
═══════════════════════════════════════════════════

   👤 User: "Analyze Windpark Musterstadt"
            "Analysiere Windpark Musterstadt"
        │
        │ Documents: Lease, Permit, Grid Connection, ...
        │ Dokumente: Pachtvertrag, Genehmigung, Netzanschluss, ...
        │
        ▼

STEP 2 / SCHRITT 2: Brain Orchestration / Brain-Orchestrierung
═══════════════════════════════════════════════════════════════

   ┌─────────────────────────────────────────────────────────────────┐
   │                                                                 │
   │   🧠 LAI BRAIN (Conductor / Dirigent)                          │
   │                                                                 │
   │   Analysis: "I recognize: Lease, Permit, Grid Connection"      │
   │   Analyse: "Ich erkenne: Pachtvertrag, Genehmigung, Netz"      │
   │                                                                 │
   │   Routing Decision / Routing-Entscheidung:                     │
   │   ├── Lease Contract    → Contract Expert                      │
   │   ├── BImSchG Permit    → BImSchG Expert                       │
   │   ├── Grid Connection   → Grid Expert                          │
   │   ├── Land Registry     → Land Expert                          │
   │   ├── Business Plan     → Economic Expert                      │
   │   └── All Documents     → Legal Expert                         │
   │                                                                 │
   └──────────────────────────┬──────────────────────────────────────┘
                              │
                              │ Parallel Delegation
                              │ Parallele Delegation
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼

STEP 3 / SCHRITT 3: Expert Analysis (Parallel) / Experten-Analyse (Parallel)
════════════════════════════════════════════════════════════════════════════

┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐
│ 📜 CONTRACT EXPERT │  │ 🌿 BImSchG EXPERT  │  │ ⚡ GRID EXPERT     │
│                    │  │                    │  │                    │
│ Input:             │  │ Input:             │  │ Input:             │
│ Lease.pdf          │  │ Permit.pdf         │  │ GridConnection.pdf │
│                    │  │                    │  │                    │
│ + RAG Context:     │  │ + RAG Context:     │  │ + RAG Context:     │
│ BWE Guidelines     │  │ TA Lärm, BNatSchG  │  │ EEG § 8-9, NAV     │
│                    │  │                    │  │                    │
│ ────────────────── │  │ ────────────────── │  │ ────────────────── │
│                    │  │                    │  │                    │
│ Output:            │  │ Output:            │  │ Output:            │
│ Score: 78/100      │  │ Score: 85/100      │  │ Score: 34/100      │
│ Findings: [...]    │  │ Findings: [...]    │  │ Findings: [...]    │
│ Risk: MEDIUM       │  │ Risk: LOW          │  │ Risk: HIGH         │
│                    │  │                    │  │                    │
└─────────┬──────────┘  └─────────┬──────────┘  └─────────┬──────────┘
          │                       │                       │
          └───────────────────────┼───────────────────────┘
                                  │
                                  ▼

STEP 4 / SCHRITT 4: Brain Synthesis / Brain-Synthese
═════════════════════════════════════════════════════

   ┌─────────────────────────────────────────────────────────────────┐
   │                                                                 │
   │   🧠 LAI BRAIN (Synthesis / Synthese)                          │
   │                                                                 │
   │   Module Results / Modul-Ergebnisse:                           │
   │   ├── Contract:  78/100 × 15% = 11.7                           │
   │   ├── BImSchG:   85/100 × 20% = 17.0                           │
   │   ├── Grid:      34/100 × 15% =  5.1                           │
   │   ├── Land:      90/100 × 20% = 18.0                           │
   │   ├── Economic: 100/100 × 25% = 25.0                           │
   │   └── Legal:     74/100 × 15% = 11.1                           │
   │                                ────────                         │
   │   Weighted Score:              87.9/100                         │
   │                                                                 │
   │   Cross-Validation:                                            │
   │   └── Grid 34/100 + Contract 78/100 → Grid agreement missing   │
   │       in contract! (Confirmed)                                 │
   │       Netzanschlussvereinbarung fehlt im Vertrag! (Bestätigt)  │
   │                                                                 │
   │   Override: Grid is CRITICAL → Status becomes YELLOW           │
   │   Override: Grid ist KRITISCH → Status wird YELLOW             │
   │                                                                 │
   └──────────────────────────┬──────────────────────────────────────┘
                              │
                              ▼

STEP 5 / SCHRITT 5: Final Output / Finale Ausgabe
═════════════════════════════════════════════════

   ┌─────────────────────────────────────────────────────────────────┐
   │                                                                 │
   │   📊 FINAL RESULT / FINALES ERGEBNIS                           │
   │                                                                 │
   │   Overall Score / Gesamt-Score:    75.6/100                    │
   │   Overall Status / Gesamt-Status:  🟡 YELLOW                   │
   │                                                                 │
   │   Critical Issues / Kritische Probleme:                        │
   │   1. ⚡ Grid Connection - Score 34/100 - 4 Findings            │
   │   2. 📜 Contract - Problematic termination clause              │
   │                     Problematische Kündigungsklausel           │
   │                                                                 │
   │   Priority Recommendations / Priorisierte Empfehlungen:        │
   │   1. [HIGH] Finalize grid connection agreement                 │
   │             Netzanschlussvertrag finalisieren                  │
   │   2. [HIGH] Request capacity verification from grid operator   │
   │             Kapazitätsprüfung beim Netzbetreiber               │
   │   3. [MEDIUM] Renegotiate termination clause                   │
   │               Kündigungsklausel nachverhandeln                 │
   │                                                                 │
   │   Verdict / Urteil: "Proceed with conditions"                  │
   │                     "Mit Auflagen fortfahren"                  │
   │                                                                 │
   └─────────────────────────────────────────────────────────────────┘
```

---

## 9. Technical Implementation / Technische Implementierung

### 9.1 Directory Structure / Verzeichnisstruktur

```
lai/
├── src/
│   ├── lai/
│   │   ├── brain/
│   │   │   ├── conductor.py          # Main orchestration / Haupt-Orchestrierung
│   │   │   ├── router.py             # Task routing / Aufgaben-Routing
│   │   │   └── synthesizer.py        # Result synthesis / Ergebnis-Synthese
│   │   │
│   │   ├── modules/
│   │   │   ├── contract_analysis.py  # Contract Expert (15%)
│   │   │   ├── economic_assessment.py # Economic Expert (25%)
│   │   │   ├── grid_connection.py    # Grid Expert (15%)
│   │   │   ├── land_security.py      # Land Expert (20%)
│   │   │   ├── legal_compliance.py   # Legal Expert (15%)
│   │   │   └── bimschg_compliance.py # BImSchG Expert (20%)
│   │   │
│   │   ├── rag/
│   │   │   ├── retriever.py          # RAG retrieval / RAG-Abruf
│   │   │   ├── embeddings.py         # Embedding generation
│   │   │   └── knowledge_base.py     # Knowledge base management
│   │   │
│   │   └── core/
│   │       ├── risk_engine.py        # Risk aggregation / Risiko-Aggregation
│   │       └── report_generator.py   # Report generation / Bericht-Erstellung
│   │
├── data/
│   ├── knowledge_base/               # 40GB Literature / Literatur
│   │   ├── laws/                     # EEG, BImSchG, etc.
│   │   ├── guidelines/               # BWE, VDMA, etc.
│   │   └── case_law/                 # Court decisions / Urteile
│   │
│   ├── training/                     # Training data / Trainingsdaten
│   │   ├── train.jsonl               # 80% training
│   │   ├── validation.jsonl          # 10% validation
│   │   └── test.jsonl                # 10% test
│   │
│   └── benchmarks/                   # Market benchmarks
│       └── market_data.csv
│
├── models/
│   └── lai-llama3-70b-windenergie/   # Fine-tuned model / Feinjustiertes Modell
│
└── scripts/
    ├── build_rag_index.py            # RAG indexing
    ├── prepare_training_data.py      # Data preparation
    └── train_model.py                # Model training
```

### 9.2 Key Code Components / Wichtige Code-Komponenten

```python
# brain/conductor.py - Main Orchestration
# brain/conductor.py - Haupt-Orchestrierung

class LAIConductor:
    """
    Central orchestration for LAI analysis
    Zentrale Orchestrierung für LAI-Analyse
    """

    def __init__(self):
        self.modules = {
            'contract': ContractExpert(weight=0.15),
            'economic': EconomicExpert(weight=0.25),
            'grid': GridExpert(weight=0.15),
            'land': LandExpert(weight=0.20),
            'legal': LegalExpert(weight=0.15),
            'bimschg': BImSchGExpert(weight=0.20)
        }
        self.rag = RAGRetriever()
        self.synthesizer = ResultSynthesizer()

    async def analyze(self, project: Project) -> RiskAssessment:
        """
        Full project analysis
        Vollständige Projektanalyse
        """
        # 1. Classify documents / Dokumente klassifizieren
        doc_map = await self.classify_documents(project.documents)

        # 2. Run modules in parallel / Module parallel ausführen
        tasks = []
        for name, module in self.modules.items():
            relevant_docs = doc_map.get(name, [])
            rag_context = await self.rag.get_context(name, relevant_docs)
            tasks.append(module.analyze(relevant_docs, rag_context))

        results = await asyncio.gather(*tasks)

        # 3. Synthesize results / Ergebnisse synthetisieren
        final = await self.synthesizer.synthesize(results)

        return final
```

---

## 10. Expected Results / Erwartete Ergebnisse

### 10.1 Performance Improvement / Leistungsverbesserung

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│                    EXPECTED IMPROVEMENT / ERWARTETE VERBESSERUNG               │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   BASELINE (Current - GPT-4 without training)                                  │
│   AUSGANGSLAGE (Aktuell - GPT-4 ohne Training)                                 │
│                                                                                 │
│   Contract Evaluation Accuracy:  ~60%                                          │
│   Score Deviation:               ±25 points / Punkte                           │
│   Critical Issues Detected:      ~70%                                          │
│   False Positives:               ~30%                                          │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   WITH RAG (Phase 1) / MIT RAG (Phase 1)                                       │
│                                                                                 │
│   Contract Evaluation Accuracy:  ~75% (+15%)                                   │
│   Score Deviation:               ±18 points / Punkte                           │
│   Critical Issues Detected:      ~85% (+15%)                                   │
│   False Positives:               ~20% (-10%)                                   │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   WITH FINE-TUNING (Phase 3) / MIT FINE-TUNING (Phase 3)                       │
│                                                                                 │
│   Contract Evaluation Accuracy:  ~90% (+30%)                                   │
│   Score Deviation:               ±10 points / Punkte                           │
│   Critical Issues Detected:      ~95% (+25%)                                   │
│   False Positives:               ~10% (-20%)                                   │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   WITH RAG + FINE-TUNING (Optimal) / MIT RAG + FINE-TUNING (Optimal)           │
│                                                                                 │
│   Contract Evaluation Accuracy:  ~95% (+35%)  ████████████████████████████████ │
│   Score Deviation:               ±8 points                                     │
│   Critical Issues Detected:      ~98% (+28%)                                   │
│   False Positives:               ~5% (-25%)                                    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 10.2 Cost-Benefit Analysis / Kosten-Nutzen-Analyse

| Option | Setup Cost / Einrichtung | Monthly Cost / Monatlich | ROI Breakeven |
|--------|-------------------------|--------------------------|---------------|
| **RAG Only** | ~$100 | +$50-100 | Immediate / Sofort |
| **OpenAI Fine-tuning** | $2k-5k | $1k-2k | ~50 projects / Projekte |
| **Llama On-Premise** | $20k-40k | $500-1k | ~100 projects / Projekte |
| **Hybrid (Optimal)** | $25k-50k | $1k-1.5k | ~80 projects / Projekte |

### 10.3 Summary / Zusammenfassung

**English:**
The LAI Training Architecture enables transformation from a basic LLM application to an expert-level wind energy due diligence system. The three-phase approach (RAG → Fine-tuning → Continuous Learning) provides:
- Immediate improvement through RAG (+30% accuracy)
- Significant leap through fine-tuning (+35% total improvement)
- Expert-level evaluations matching human DD professionals
- Full data sovereignty with on-premise option

**Deutsch:**
Die LAI Training-Architektur ermöglicht die Transformation von einer einfachen LLM-Anwendung zu einem Experten-Level Windenergie Due-Diligence-System. Der dreiphasige Ansatz (RAG → Fine-tuning → Kontinuierliches Lernen) bietet:
- Sofortige Verbesserung durch RAG (+30% Genauigkeit)
- Signifikanter Sprung durch Fine-tuning (+35% Gesamtverbesserung)
- Experten-Level Bewertungen auf Niveau von DD-Profis
- Volle Datensouveränität mit On-Premise-Option

---

## Document Information / Dokumentinformation

| Field / Feld | Value / Wert |
|--------------|--------------|
| Created / Erstellt | 2025-11-28 |
| Author / Autor | LAI Development Team |
| Version | 1.0 |
| Status | Active / Aktiv |
| Language / Sprache | English / German (Bilingual) |

---

*End of Document / Ende des Dokuments*

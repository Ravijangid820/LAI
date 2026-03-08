
# Phase 2 – Multi‑Domain Legal Analysis System (MoE Architecture)
## **RAG Used Only for User Documents – Expert Models Provide All Legal Reasoning**
### **Detailed Architecture & Execution Plan (Long, Full Version)**

---

# 1. Vision

We are building a scalable, professional legal‑analysis system that mirrors real legal practice:  
**specialists**, not one “general-purpose model.”

The system evolves into a **Mixture‑of‑Experts (MoE)** architecture consisting of multiple **small, domain‑specific legal expert models**, each responsible for a particular field of law.

Domain examples:
- Contract / Energy / Wind-Farm Law *(Domain 1 – current focus)*
- Traffic Law
- Criminal Law  
- Fraud / Financial Crime  
- Additional domains (Land, Grid, Regulatory, etc.)

This design supports:
- High accuracy  
- Explainability  
- Strong separation of knowledge  
- Easy scaling to new legal domains  

**Core principle:**  
👉 **RAG is used ONLY for the user’s uploaded documents** (contracts, evidence, case files).  
👉 All legal reasoning and expertise lives inside **domain-specific expert models**.

---

# 2. Architectural Principle
## **RAG = Facts. Expert Models = Legal Knowledge.**

### RAG retrieves only:
- User-uploaded contracts
- Annexes, amendments, case docs
- Evidence files, PDFs, statements
- Any project-specific files

### RAG does NOT retrieve:
- Statutes  
- Case law  
- General legal knowledge  
- Internal guidelines  
- Internet-scale documents  

### Benefits
- Zero cross-client contamination  
- High security  
- Predictable retrieval  
- Clean modular design  
- Expert models behave consistently across domains  

---

# 3. Phase 2 Strategy: **Start Narrow → Then Expand**

This is the key scaling strategy of the entire system.

### Step A — Start with ONE domain (Contract/Energy Law)
- We already have data  
- Business impact is immediate  
- Results can be demonstrated early

### Step B — Build full end-to-end pipeline
Data → Labels → Training → Expert model → Integration → UI → Pilot

### Step C — Generalize pipeline to support new domains
Traffic → Crime → Fraud → Corporate → etc.

### Step D — Add multiple expert models
Each a small LoRA-tuned domain specialist.

### Step E — Add Router/Gating (MoE)
Automatic domain selection once several expert models exist.

---

# 4. High-Level Architecture Diagram

```
User Upload → User-only RAG → Router → Domain Expert Model →
Structured Legal Output → UI
```

Components:
- **MinIO** for raw PDF/doc storage  
- **Preprocessing pipeline** for segmentation, cleaning  
- **Vector DB** (pgvector/Qdrant) indexing *only user documents*  
- **Multiple domain models** (LoRA adapters)  
- **Router** (manual selection now → automatic later)  
- **Unified schema** across all domains  
- **UI** for chat + structured issue display  

---

# 5. Universal Output Schema

Every legal domain uses the same JSON structure:

```
{
  "domain": "",
  "overall_risk_score": 0-100,
  "risk_category": "GREEN | AMBER | RED",
  "issues": [
    {
      "title": "",
      "severity": "Low | Medium | High",
      "category": "",
      "explanation": "",
      "reference_clauses": []
    }
  ],
  "extracted_fields": {},
  "summary": ""
}
```

Why?
- UI stays unchanged when adding new legal domains  
- Backend integration stays simple  
- Models remain interchangeable  

---

# 6. Detailed Step-by-Step Execution Plan (Full Pipeline)

---

## **STEP 1 — Formalize Domain 1: Contract / Energy / Wind Farm Law**

Define:
- Analysis tasks  
- Common issue categories  
- Severity rules  
- Scoring logic  
- Key-field extraction list (term, rent, indexation, liability limits, etc.)  
- Jurisdiction scope (DE/EU/EN, etc.)

Deliverables:
- Domain definition document  
- Example annotated outputs  
- Evaluation criteria  

---

## **STEP 2 — Data Collection, Cleaning & Segmentation**

Pipeline:
1. Store PDFs in MinIO  
2. Extract clean text  
3. Detect German/English  
4. Split into clauses  
5. Create metadata (clause type, section number)

Outputs:
- Structured text  
- Clause-level dataset  
- RAG index (User-only)

---

## **STEP 3 — Label Creation for Domain 1**

Sources:
- Internal DD reports  
- Expert-written reviews  
- Issue lists  
- Risk ratings  

Create:
- Issues (severity + explanation + clause reference)  
- Risk scores  
- Summaries  
- Extracted key fields  

Store as JSONL samples for fine-tuning.

Deliverables:
- `train.jsonl`  
- `valid.jsonl`  
- `test.jsonl`  
- Quality validation samples  

---

## **STEP 4 — Automated Dataset Generation Pipeline**

Reusable across all future domains.

Features:
- Ingest documents from MinIO  
- RAG retrieves supporting context (from user docs)  
- Map annotations into unified schema  
- Export final JSONL training samples  

This is the key engineering investment that allows fast scaling later.

---

## **STEP 5 — Fine-Tune Contract Law Expert Model**

Model:
- Base LLM (Llama-family or similar)  
- LoRA adapters for domain specialization  

Training tasks:
- Clause-level reasoning  
- Issue classification  
- Severity scoring  
- Field extraction  
- Risk analysis  
- Short explanation generation  

Deliverables:
- `legal-expert-contract-v1`  
- Model card  
- Validation metrics  
- Output consistency tests  

---

## **STEP 6 — Backend & UI Integration**

Flow:
1. User uploads contract  
2. RAG returns relevant contract clauses  
3. Model receives:
   - extracted text  
   - retrieved context  
4. Model outputs structured JSON  
5. UI renders:
   - Score  
   - Traffic light  
   - Issues (severity + explanation)  
   - Clause references  
   - Fields  
   - Summary  

Deliverables:
- Complete functioning Contract Law expert  
- Pilot-ready system  

---

# 7. Expansion to Additional Domains

Once Domain 1 pipeline works, repeat for each new domain:

---

## **Domain 2 — Traffic Law**
Tasks:
- Violation classification  
- Penalty calculation  
- Evidence interpretation  

Model:
- `legal-expert-traffic-v1`

---

## **Domain 3 — Criminal Law**
Tasks:
- Offense classification  
- Severity analysis  
- Evidence correlation  
- Recommendation generation  

Model:
- `legal-expert-crime-v1`

---

## **Domain 4 — Fraud / Financial Crime**
Tasks:
- Misrepresentation analysis  
- Fraud pattern detection  
- Document inconsistencies  
- AML-like red flags  

Model:
- `legal-expert-fraud-v1`

---

## **Expansion Framework**
Every new model requires:
1. Domain specification  
2. Data collection  
3. Label generation  
4. Dataset pipeline reuse  
5. Fine-tuning  
6. Evaluation  
7. UI integration  
8. Versioning  

This keeps the architecture clean and predictable.

---

# 8. Router / Gating (MoE) Development

## Phase 1 — Manual domain selection (simple)
User chooses domain in UI.

## Phase 2 — Automatic domain classifier
Router predicts:
- best domain(s) for analysis  
- confidence scores  

## Phase 3 — Multi-expert aggregation
For overlapping cases:
- e.g., Contract + Fraud  
- e.g., Traffic + Criminal  

System merges the results intelligently.

---

# 9. Risks, Mitigations & Professional Considerations

### Risk 1 — Model hallucinations about law  
Mitigation:  
- Train only on curated legal texts + expert annotations  
- Do not index public laws in RAG  
- Add optional citation database later

### Risk 2 — Low-quality, inconsistent labels  
Mitigation:
- Domain expert review loop  
- Validation sets  
- JSON schema enforcement  

### Risk 3 — Router misclassification  
Mitigation:
- Confidence thresholds  
- Fallback to manual user selection  

### Risk 4 — Jurisdiction variance  
Mitigation:
- Versioning per jurisdiction (DE, EU, EN)  
- Clear metadata  

---

# 10. Deliverables for Management

At completion of Phase 2:

- Fully functioning Contract/Energy Law Expert Model  
- RAG system limited strictly to user-provided documents  
- Automated dataset pipeline  
- Unified schema across all legal domains  
- Clear expansion pathway for Traffic, Crime, Fraud  
- Early MoE architecture foundation  
- Preparation for router development  

---

# 11. Executive Summary

In Phase 2 we transform the system from a generic RAG pipeline into a **scalable legal analysis platform** using domain-specialized models.  
We **start with Contract Law**, deliver a working expert model, and build all pipelines so that adding Traffic, Crime, Fraud, and other domains becomes fast and predictable.  
RAG is used exclusively for **user documents**, ensuring privacy, accuracy, and clean legal reasoning.


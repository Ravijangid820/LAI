# Analyzer Gold Set

Drop a `<slug>.pdf` plus a `<slug>.gold.json` here. The eval harness
(`scripts/eval_analyzer.py`) consumes any pair it finds.

## Gold-JSON schema

```json
{
  "contract_type": "Pachtvertrag",
  "parties": ["Verpächter GmbH", "Wind Operator KG"],
  "required_clauses": [
    "Vertragsdauer",
    "Pacht/Vergütung",
    "Rückbauverpflichtung",
    "Wegerecht/Zufahrt"
  ],
  "parcels": [
    {
      "gemarkung": "Schweringen",
      "flur": "2",
      "flurstueck": "47/3",
      "groesse_m2": 12500
    }
  ],
  "top_issues": [
    {"keyword": "haftungsbeschränkung"},
    {"keyword": "verlängerungsoption fehlt"}
  ],
  "table_discrepancies": [
    {"table": "Pachtzins-Staffel", "severity": "medium"}
  ]
}
```

All fields are optional except they need to be filled if the
corresponding metric should score. Metrics on missing fields are reported
as `—` in the output table.

## Suggested initial gold set

Pulled from `/data/projects/lai/VDRs/` — see top of `CONTRACT_ANALYZER_V2.md`.

| Slug | Source PDF |
|---|---|
| `wp_altmark_uw_nutzung` | `VDRs/WP Altmark/UW-Nutzungsvertrag/UW-Nutzungsvertrag Windpark Altmark.pdf` |
| `wp_butterberg_pachtvertrag` | `VDRs/WP Butterberg/03. Grundstückssicherung/Pachtvertrag Bu03 mit PDB und flurkarte.pdf` |
| `wp_lamstedt_wartung` | `VDRs/WP Lamstedt/LA KG_Enercon_Wartungsvertrag S-00206-V03_2019-10-14_signed.pdf` |
| `wp_schweringen_dv` | `VDRs/WP 33:34/1. EVU/WP 33 - Direktvermarktungsvertrag Vattenfall WP Schweringen.pdf` |
| `quadra_ppa` | `Libary/QUADRA_PPA_Gazeas.pdf` |

Copy the PDF into this directory under the slug name; the eval harness
finds the pair automatically.

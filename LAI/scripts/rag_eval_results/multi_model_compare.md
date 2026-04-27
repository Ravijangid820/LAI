# Multi-Model RAG Comparison (n=5, top_k=3)

Pre-computed retrieval contexts (hybrid+rerank); each model generates from the same prompts.

## Models tested

- ✅ **qwen25-ft** (`/data/projects/lai/models/qwen25-7b-legal-merged`) — total gen time 13.8s, avg 2.8s/query
- ✅ **qwen25-base** (`Qwen/Qwen2.5-7B-Instruct`) — total gen time 18.9s, avg 3.8s/query
- ❌ **qwen35** (`Qwen/Qwen3.5-27B`) — The checkpoint you are trying to load has model type `qwen3_5` but Transformers does not recognize this architecture. This could be because of an issue with the checkpoint, or because your version of Transformers is out of date.

You can update Transformers with the command `pip install --upgrade transformers`. If this does not work, and the checkpoint is very new, then there may not be a release version that supports this model yet. In this case, you can get the most up-to-date code by installing Transformers from source with the command `pip install git+https://github.com/huggingface/transformers.git`
- ❌ **qwen36** (`Qwen/Qwen3.6-27B`) — The checkpoint you are trying to load has model type `qwen3_5` but Transformers does not recognize this architecture. This could be because of an issue with the checkpoint, or because your version of Transformers is out of date.

You can update Transformers with the command `pip install --upgrade transformers`. If this does not work, and the checkpoint is very new, then there may not be a release version that supports this model yet. In this case, you can get the most up-to-date code by installing Transformers from source with the command `pip install git+https://github.com/huggingface/transformers.git`
- ❌ **gemma4** (`google/gemma-4-E4B-it`) — 'list' object has no attribute 'keys'

## [1/5] ✓ gold parent 287

**Q:** Welche technischen Parameter sind im Vorhaben- und Erschließungsplan der Gemeinde Hatten für Windenergieanlagen (WEA) festgesetzt worden?

**REF (gold answer):** Im Vorhaben- und Erschließungsplan der Gemeinde Hatten aus dem Jahr 2001 wurden die folgenden technischen Parameter für Windenergieanlagen festgesetzt: max. Nabenhöhen von 98 m, eine max. Nennleistung von 1,8 MW und ein max. Rotordurchmesser von 70 m (siehe Rechtstext, letzter Absatz vor dem letzten Satz).

**qwen25-ft:**

Im Vorhaben- und Erschließungsplan der Gemeinde Hatten, der aus dem Jahr 2001 stammt, wurden für Windenergieanlagen (WEA) die folgenden technischen Parameter festgesetzt: max. Nabenhöhe von 98 m, max. Nennleistung von 1,8 MW und max. Rotordurchmesser von 70 m. Diese Werte wurden als Referenzanlage für die Ermittlung von Abständen zu Siedlungsgebieten verwendet, um harte und weiche Ausschlussflächen zu definieren. Die Gemeinde hat in ihrem FNP aus 2012 diese Werte als Referenzanlage zugrunde gelegt, um die harten sowie die weichen Ausschlussflächen zu Siedlungsbereichen zu bestimmen.

---

**qwen25-base:**

Im Vorhaben- und Erschließungsplan der Gemeinde Hatten, der seit 2001 gültig ist, wurden für Windenergieanlagen (WEA) die folgenden technischen Parameter festgelegt:

- Maximaler Nabenhöhe von 98 m
- Maximaler Nennleistung von 1,8 MW
- Maximaler Rotordurchmesser von 70 m

Diese Werte wurden im Zusammenhang mit den im Betrieb befindlichen WEA vom Typ ENERCON E-70 festgelegt [Quelle 1].

---

## [2/5] ✓ gold parent 294

**Q:** Welche Steuerberatungsgesellschaft ist für die NeXtWind Windpark Beteiligung III GmbH & Co. KG zuständig und welche Kontaktdaten sind hierfür angegeben?

**REF (gold answer):** Die zuständige Steuerberatungsgesellschaft für die NeXtWind Windpark Beteiligung III GmbH & Co. KG ist die Steuerberatungsgesellschaft mbH Dantax mit den folgenden Kontaktdaten: Sigrun Scherff, Telefon +49 461 700 35-0, Fax +49 461 700 35-99, E-Mail scherff@dantax.dk, Web: www.dantax.dk.

**qwen25-ft:**

Die Steuerberatungsgesellschaft, die für die NeXtWind Windpark Beteiligung III GmbH & Co. KG zuständig ist, ist die Steuerberatungsgesellschaft mbH. Die Kontaktdaten sind wie folgt angegeben: Marburger Straße 3, DE - 10789 Berlin, Telefon +49 461 700 35-0, Fax +49 461 700 35-99, E-Mail scherff@dantax.dk, Website www.dantax.dk. Diese Informationen sind in den beiden Rechtstexten [Quelle 1] und [Quelle 2] explizit aufgeführt.

---

**qwen25-base:**

Die Steuerberatungsgesellschaft, die für die NeXtWind Windpark Beteiligung III GmbH & Co. KG zuständig ist, lautet Steuerberatungsgesellschaft mbH. Die angegebenen Kontaktdaten sind:

- Lfd. Bearbeiter: Sigrun Scherff
- Telefon: +49 461 700 35-0
- Fax: +49 461 700 35-99
- E-Mail: scherff@dantax.dk
- Website: www.dantax.dk

Diese Informationen sind in beiden Rechtstexten [Quelle 1] und [Quelle 2] identisch aufgeführt.

---

## [3/5] ✗ gold parent 329

**Q:** Welches Verantwortungsspektrum haben die Prüfer im Zusammenhang mit der Lektüre und Bewertung der Leitungsaussage?

**REF (gold answer):** Die Prüfer sind verantwortlich, die Leitungsaussage zu lesen und zu überprüfen, ob sie wesentlich inkonsistent mit dem Jahresabschluss oder dem Wissen ist, das bei der erweiterten Prüfung des Jahresabschlusses erlangt wurde, oder ob sie wesentliche Fehlinformationen enthält (vgl. 'I tilknytning til vores udvidede gennemgang af årsregnskabet er det vores ansvar at læse ledelsesberetningen og i den forbindelse overveje, om ledelsesberetningen er væsentligt inkonsistent med årsregnskabet eller vores viden opnået ved den udvidede gennemgang eller på anden måde synes at indeholde væsentlig fejlinfo…

**qwen25-ft:**

Die Prüfer sind verantwortlich für die Lektüre der Leitungsaussage und die Überprüfung, ob diese mit dem Jahresabschluss und ihrer Kenntnis aus der Prüfung des Jahresabschlusses in Einklang steht oder ob sie wesentliche Fehler enthält. Sie müssen auch prüfen, ob die Leitungsaussage die erforderlichen Informationen gemäß der Jahresabschlussgesetzgebung enthält. Die Prüfer haben jedoch keine Verpflichtung, die Leitungsaussage zu überprüfen, wenn sie bereits festgestellt haben, dass der Jahresabschluss fehlerfrei ist. Dies wird in den Udtalelsern vom 5. Februar 2019, 31. Januar 2019 und 12. Februar 2018 explizit festgelegt.

---

**qwen25-base:**

Die Prüfer haben das folgende Verantwortungsspektrum im Zusammenhang mit der Lektüre und Bewertung der Leitungsaussage:

1. Die Prüfer sind verpflichtet, die Leitungsaussage zu lesen.
2. Sie müssen überprüfen, ob die Leitungsaussage wesentlich inkonsistent ist mit dem Jahresabschluss oder den von ihnen während der Prüfung gewonnenen Kenntnissen.
3. Sie müssen prüfen, ob die Leitungsaussage wesentliche Fehlinformationen enthält.
4. Es ist ihre Aufgabe, zu überprüfen, ob die Leitungsaussage die erforderlichen Informationen enthält, wie es die Jahresabschlussgesetzgebung vorsieht.

Zusammengefasst lautet die Aussage [Quelle 1] - [Quelle 3]: "Es ist das Verantwortungsbewusstsein der Prüfer, die Leitungsaussage zu lesen und zu überprüfen, ob sie mit dem Jahresabschluss und den in der Prüfung gewonnenen Kenntnissen übereinstimmt, ob sie Fehlinformationen enthält und ob sie die notwendigen Informationen enthält, die nach der Jahresabschlussgesetzgebung vorgeschrieben sind."

---

## [4/5] ✗ gold parent 346

**Q:** Welche Kriterien müssen erfüllt sein, damit ein Aktiv in die Bilanz aufgenommen wird?

**REF (gold answer):** Ein Aktiv wird in die Bilanz aufgenommen, wenn es aufgrund einer früheren Ereignis wahrscheinlich ist, dass zukünftige wirtschaftliche Vorteile dem Unternehmen zufallen werden, und wenn der Wert des Aktivs zuverlässig gemessen werden kann (siehe Text: 'Aktiver indregnes i balancen, når det som følge af en tidligere begivenhed er sandsynligt, at fremtidige økonomiske fordele vil tilflyd virksomheden, og aktivets værdi kan måles pålideligt.')

**qwen25-ft:**

Gemäß [Quelle 1] und [Quelle 2] müssen zwei Hauptkriterien erfüllt sein, damit ein Aktiv in die Bilanz aufgenommen wird: Erstens muss es sich um eine Tätigkeit handeln, die als Folge einer früheren Ereignis erfolgt ist, und zweitens muss es wahrscheinlich sein, dass zukünftige wirtschaftliche Vorteile für die Firma eintreten werden, und die Wertschätzung des Aktivs kann zuverlässig erfolgen. Gemäß [Quelle 3] ergänzt sich dies durch die Notwendigkeit, dass die Wertschätzung des Aktivs zuverlässig erfolgen kann.

---

**qwen25-base:**

Um ein Aktiv in die Bilanz aufzunehmen, müssen nach den genannten Rechtstexten folgende Kriterien erfüllt sein:

1. Es muss eine frühere Ereignisfolge bestehen, die es wahrscheinlich macht, dass zukünftige wirtschaftliche Vorteile für die Geschäftsführung entstehen werden ([Quelle 1], [Quelle 2], [Quelle 3]).

2. Der Wert des Aktivs muss palaibel gemessen werden können ([Quelle 1], [Quelle 2], [Quelle 3]).

3. Bei der ersten Aufnahme müssen Aktiva und Verbindlichkeiten am Kostpreis gemessen werden ([Quelle 1], [Quelle 2], [Quelle 3]).

4. Bei der Aufnahme und Messung muss berücksichtigt werden, dass zukünftige Risiken und Verluste, die vor dem Jahresabschluss auftreten, die bestehenden Verhältnisse auf dem Bilanztag beeinflussen können ([Quelle 1], [Quelle 2], [Quelle 3]).

Diese Kriterien sind unabhängig von den leicht abweichenden Formulierungen in den verschiedenen Quellen identisch.

---

## [5/5] ✓ gold parent 348

**Q:** Welche Dokumente sind erforderlich, um die aktuelle rechtliche Struktur und den Gesellschafterstand der SPV und der Infrastrukturgesellschaften vollständig zu dokumentieren?

**REF (gold answer):** Um die aktuelle rechtliche Struktur und den Gesellschafterstand der SPV und der Infrastrukturgesellschaften vollständig zu dokumentieren, sind die folgenden Dokumente erforderlich:
- Die Satzung (in beglaubigter Form) in der derzeit geltenden Fassung und aktuelle Eintragungen aus dem Handelsregister für die SPV oder ältere Eintragungen, falls die SPV unter einer anderen Nummer oder bei einem anderen Handelsregister eingetragen war, sowie anhängige Anträge.
- Alle Gesellschafterbeschlüsse.
- Kopien der Jahresabschlüsse der SPV (und der Infrastrukturgesellschaften) für die gesamte Dauer des Wind…

**qwen25-ft:**

Um die aktuelle rechtliche Struktur und den Gesellschafterstand der SPV und der Infrastrukturgesellschaften vollständig zu dokumentieren, sind verschiedene Dokumente erforderlich. Gemäß [Quelle 1] müssen die Artikel der Gesellschaft (certified form) in der aktuellen Fassung und recent excerpts aus dem Handelsregister vorgelegt werden. Zudem sind alle Gesellschafterbeschlüsse und etwaige Übertragungsverträge über Shares oder Assets, insbesondere Projektkaufverträge, Anteilskaufverträge, Anzeige Betreiberwechsel, Übertragung von Anteilen/Assets der Infrastrukturgesellschaft usw., zu beifügen. Diese Informationen sind in [Quelle 3] unter den Eintrags 3 Tostedt und 4 Tostedt detailliert aufgeführt.

---

**qwen25-base:**

Um die aktuelle rechtliche Struktur und den Gesellschafterstand der SPV (Special Purpose Vehicle) und der Infrastrukturgesellschaften vollständig zu dokumentieren, sind folgende Dokumente nach [Quelle 1] erforderlich:

1. **Übersicht aller beteiligten Unternehmen**: Dies umfasst SPVs, Infrastrukturgesellschaften, Substation Companies und andere Unternehmensgruppen [Quelle 1].

2. **Dokumente zur Einrichtung der SPV**:
   - Die Artikel der Gesellschaft (certified form) in der geltenden Fassung [Quelle 1].
   - Jüngste Auszüge aus dem Handelsregister der SPV oder frühere Auszüge, wenn die SPV unter einem anderen Nummernkennzeichen registriert war oder bei einem anderen Handelsregister eingetragen wurde [Quelle 1].
   - Offene Anmeldungen [Quelle 1].

3. **Gesellschafterbeschlüsse**:
   - Alle Beschlüsse der Gesellschafter [Quelle 1].

4. **Jahresberichte**:
   - Kopien der Jahresberichte der SPV und der Infrastrukturgesellschaften für die gesamte Dauer des Windparks, zumindest für die letzten drei Jahre [Quelle 1].

5. **Details zu Aktien- und Aktionärssituationen**:
   - Eine vollständige Kette der Eigentumsrechte, einschließlich der Gründungsakte oder Memorandum der Gesellschaft, der Gesellschaftsordnungen aller betroffenen Unternehmensgruppen, stiller Gesellschaften sowie der Gewährung von Gewinnrechten [Quelle 1].

6. **Verträge und andere relev

---


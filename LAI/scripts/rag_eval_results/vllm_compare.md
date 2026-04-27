# vLLM-Container Multi-Model Comparison (n=5)

Each model is loaded into a fresh vllm/vllm-openai container, queried, then torn down.
Reasoning models (Qwen3.x) emit `<think>...</think>` traces; we strip those for the answer column but preserve them in the report.

## Models

- ✅ **gemma4** (`google/gemma-4-E4B-it`) — total 10.0s, avg 2.0s/query

## [1/5] ✓ gold 287

**Q:** Welche technischen Parameter sind im Vorhaben- und Erschließungsplan der Gemeinde Hatten für Windenergieanlagen (WEA) festgesetzt worden?

**REF:** Im Vorhaben- und Erschließungsplan der Gemeinde Hatten aus dem Jahr 2001 wurden die folgenden technischen Parameter für Windenergieanlagen festgesetzt: max. Nabenhöhen von 98 m, eine max. Nennleistung von 1,8 MW und ein max. Rotordurchmesser von 70 m (siehe Rechtstext, letzter Absatz vor dem letzten Satz).

### gemma4

Gemäß dem Vorhaben- und Erschließungsplan der Gemeinde Hatten aus dem Jahr 2001 wurden folgende technische Parameter für Windenergieanlagen (WEA) festgesetzt:

*   Maximale Nabenhöhen von 98 m [Quelle 1].
*   Eine maximale Nennleistung von 1,8 MW [Quelle 1].
*   Ein maximaler Rotordurchmesser von 70 m [Quelle 1].

## [2/5] ✓ gold 294

**Q:** Welche Steuerberatungsgesellschaft ist für die NeXtWind Windpark Beteiligung III GmbH & Co. KG zuständig und welche Kontaktdaten sind hierfür angegeben?

**REF:** Die zuständige Steuerberatungsgesellschaft für die NeXtWind Windpark Beteiligung III GmbH & Co. KG ist die Steuerberatungsgesellschaft mbH Dantax mit den folgenden Kontaktdaten: Sigrun Scherff, Telefon +49 461 700 35-0, Fax +49 461 700 35-99, E-Mail scherff@dantax.dk, Web: www.dantax.dk.

### gemma4

Die für die NeXtWind Windpark Beteiligung III GmbH & Co. KG zuständige Steuerberatungsgesellschaft ist Dantax [Quelle 1, Quelle 2].

Die angegebenen Kontaktdaten sind:
*   Adresse: 24955 Flensburg-Harrislee · Am Oxer 7 [Quelle 1, Quelle 2]
*   Lfd. Bearbeiter: Sigrun Scherff [Quelle 1, Quelle 2]
*   Telefon: +49 461 700 35-0 [Quelle 1, Quelle 2]
*   Fax: +49 461 700 35-99 [Quelle 1, Quelle 2]
*   E-Mail: scherff@dantax.dk [Quelle 1, Quelle 2]
*   Webseite: www.dantax.dk [Quelle 1, Quelle 2]

## [3/5] ✗ gold 329

**Q:** Welches Verantwortungsspektrum haben die Prüfer im Zusammenhang mit der Lektüre und Bewertung der Leitungsaussage?

**REF:** Die Prüfer sind verantwortlich, die Leitungsaussage zu lesen und zu überprüfen, ob sie wesentlich inkonsistent mit dem Jahresabschluss oder dem Wissen ist, das bei der erweiterten Prüfung des Jahresabschlusses erlangt wurde, oder ob sie wesentliche Fehlinformationen enthält (vgl. 'I tilknytning til vores udvidede gennemgang af årsregnskabet er det vores ansvar at læse ledelsesberetningen og i den forbindelse overveje, om ledelsesberetningen er væsentligt inkonsistent med årsregnskabet eller vores viden opnået ved den udvidede gennemgang eller på anden måde synes at indeholde væsentlig fejlinfo…

### gemma4

Die Prüfer haben folgende Verantwortlichkeiten im Zusammenhang mit der Lektüre und Bewertung der Leitungsaussage:

*   Es ist ihre Verantwortung, die Leitungsaussage im Rahmen der Prüfung der Jahresrechnung zu lesen [Quelle 1, Quelle 2, Quelle 3].
*   In diesem Zusammenhang müssen sie prüfen, ob die Leitungsaussage wesentlich inkonsistent mit der Jahresrechnung oder ihrem durch die Prüfung gewonnenen Wissen ist oder ob sie auf andere Weise wesentliche Fehlinformationen enthält [Quelle 1, Quelle 2, Quelle 3].
*   Ihre Verantwortung besteht darüber hinaus darin, zu prüfen, ob die Leitungsaussage die gemäß dem Jahresrechnungsgesetz geforderten Informationen enthält [Quelle 1, Quelle 2, Quelle 3].

Es wird auch festgehalten, dass die Schlussfolgerung der Prüfer zur Jahresrechnung die Leitungsaussage nicht umfasst und sie keinerlei Sicherheitserklärung bezüglich der Leitungsaussage abgeben [Quelle 1, Quelle 2, Quelle 3].

## [4/5] ✗ gold 346

**Q:** Welche Kriterien müssen erfüllt sein, damit ein Aktiv in die Bilanz aufgenommen wird?

**REF:** Ein Aktiv wird in die Bilanz aufgenommen, wenn es aufgrund einer früheren Ereignis wahrscheinlich ist, dass zukünftige wirtschaftliche Vorteile dem Unternehmen zufallen werden, und wenn der Wert des Aktivs zuverlässig gemessen werden kann (siehe Text: 'Aktiver indregnes i balancen, når det som følge af en tidligere begivenhed er sandsynligt, at fremtidige økonomiske fordele vil tilflyd virksomheden, og aktivets værdi kan måles pålideligt.')

### gemma4

Damit ein Aktiv in die Bilanz aufgenommen wird, müssen folgende Kriterien erfüllt sein:

*   Es muss sich um eine Folge eines früheren Ereignisses handeln [Quelle 1, Quelle 2, Quelle 3].
*   Es muss wahrscheinlich sein, dass zukünftige wirtschaftliche Vorteile dem Unternehmen zufließen werden [Quelle 1, Quelle 2, Quelle 3].
*   Der Wert des Aktivs muss zuverlässig messbar sein [Quelle 1, Quelle 2, Quelle 3].

## [5/5] ✓ gold 348

**Q:** Welche Dokumente sind erforderlich, um die aktuelle rechtliche Struktur und den Gesellschafterstand der SPV und der Infrastrukturgesellschaften vollständig zu dokumentieren?

**REF:** Um die aktuelle rechtliche Struktur und den Gesellschafterstand der SPV und der Infrastrukturgesellschaften vollständig zu dokumentieren, sind die folgenden Dokumente erforderlich:
- Die Satzung (in beglaubigter Form) in der derzeit geltenden Fassung und aktuelle Eintragungen aus dem Handelsregister für die SPV oder ältere Eintragungen, falls die SPV unter einer anderen Nummer oder bei einem anderen Handelsregister eingetragen war, sowie anhängige Anträge.
- Alle Gesellschafterbeschlüsse.
- Kopien der Jahresabschlüsse der SPV (und der Infrastrukturgesellschaften) für die gesamte Dauer des Wind…

### gemma4

Um die aktuelle rechtliche Struktur und den Gesellschafterstand der SPV und der Infrastrukturgesellschaften vollständig zu dokumentieren, sind folgende Dokumente erforderlich:

*   Eine Übersicht aller in das Projekt involvierten Unternehmen (SPVs, Infrastrukturgesellschaften, Umspannwerksgesellschaften, andere Tochtergesellschaften etc.) und deren Organisationsschema [Quelle 1].
*   Dokumente zur Gründung der SPV [Quelle 1].
*   Die Satzung (in zertifizierter Form) in ihrer aktuell gültigen Fassung sowie aktuelle Auszüge aus dem Handelsregister der SPV oder Auszüge aus einem früheren Datum, falls die SPV unter einer anderen Nummer registriert war oder bei einem anderen Handelsregister eingetragen wurde, sowie anhängige Einreichungen [Quelle 1].
*   Alle Gesellschafterbeschlüsse [Quelle 1].
*   Vollständige Details zu allen Beteiligungen oder Anteilen, die die SPV (und Infrastrukturgesellschaften) hält, einschließlich der aktuellen Liste der Gesellschafter, die über eine vollständige Kette der Titeldokumente (d.h. Gründungsurkunde oder Satzung) und Satzungen aller jeweiligen Tochtergesellschaften, stiller Partnerschaften sowie der Gewinnzuteilung eingetragen sind [Quelle 1].
*   Details, einschließlich Kopien aller Vereinbarungen, anderer relevanter Dokumente oder anderer Regelungen, die Optionen, Rechte aufgrund eines Kontrollereignisses, Vorkaufsrechte und alle anderen Rechte zur Erwerbung von Beteiligungen an der SPV (und Infrastrukturgesellschaften) geschaffen haben (oder schaffen können) [Quelle 1].


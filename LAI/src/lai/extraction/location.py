"""LLM-based location extraction from German legal documents.

Uses Qwen2.5-72B via vLLM to extract geographic data:
- Wind park names and locations
- Addresses, municipalities, districts
- Flurstück (parcel) IDs, Gemarkung (cadastral districts)
- GPS coordinates when mentioned
"""

import json
import re
from typing import Any

import httpx

from lai.core.config import get_settings
from lai.core.logging import get_logger
from lai.extraction.models import ExtractedLocation, ExtractionResult, LocationType

logger = get_logger("lai.extraction.location")

SYSTEM_PROMPT = """Du bist ein Spezialist für die Extraktion von KONKRETEN, KARTIERBAREN Standortdaten aus deutschen Rechtstexten im Bereich Windenergie.

WICHTIG: Extrahiere NUR Standorte, die man auf einer Karte anzeigen kann. Jeder Standort MUSS mindestens eines davon haben:
- Eine konkrete Adresse (Straße + Hausnummer + PLZ + Ort)
- GPS-Koordinaten (Breitengrad/Längengrad)
- Ein Flurstück mit Gemarkung (z.B. "Flurstück 29, Flur 1, Gemarkung Zodel")

NICHT extrahieren:
- Reine Städte-/Gemeindenamen ohne Adresse (z.B. nur "Bremen" oder "Hude")
- Bundesländer oder Landkreise allein
- Kanzleiadressen, Notaradressen oder andere Büroadressen die nichts mit dem Projekt zu tun haben
- Vage Ortsangaben wie "in der Nähe von..." ohne konkrete Daten

Was extrahiert werden soll:
1. **Windpark-Standorte** mit konkreter Lage (Flurstücke, Koordinaten oder Adresse)
2. **Flurstücke/Grundstücke**: Flurstück-Nr. + Flur + Gemarkung (immer zusammen)
3. **WEA-Standorte**: Einzelne Windenergieanlagen mit konkreten Koordinaten oder Flurstücken
4. **Umspannwerke/Netzanschlüsse**: Nur mit konkretem Standort
5. **Konkrete Adressen**: Nur projektbezogene Adressen (z.B. Betreiberadresse am Windpark)

Für jeden Standort: Konstruiere ein Feld "geocode_address" — eine einzelne Zeichenkette die man direkt an eine Geocoding-API senden kann.
Beispiele:
- "Flurstück 29, Flur 1, Gemarkung Zodel, Gemeinde Neißeaue, Sachsen"
- "Windpark Hude-Hatten, Gemeinde Hude, Landkreis Oldenburg, Niedersachsen"
- "Musterstraße 12, 26919 Brake, Niedersachsen"
- Wenn Koordinaten vorhanden: nicht nötig, da Koordinaten direkt kartierbar sind

Antworte NUR mit einem JSON-Array. Jedes Objekt:
{
  "location_name": "Name/Bezeichnung",
  "location_type": "wind_park|wind_turbine|substation|grid_connection|parcel|address|other",
  "geocode_address": "Komplette, geocodierbare Adresszeile oder null wenn Koordinaten vorhanden",
  "address": "Straße + Hausnummer + PLZ + Ort falls vorhanden, sonst null",
  "latitude": Breitengrad als Zahl oder null,
  "longitude": Längengrad als Zahl oder null,
  "flurstuck": "Flurstück-Nr. oder null",
  "flur": "Flur-Nr. oder null",
  "gemarkung": "Gemarkung oder null",
  "gemeinde": "Gemeinde oder null",
  "landkreis": "Landkreis oder null",
  "bundesland": "Bundesland oder null",
  "raw_excerpt": "Kurzer Textausschnitt (max 200 Zeichen) der diese Standortinfo enthält",
  "confidence": 0.0 bis 1.0
}

Wenn keine kartierbaren Standorte gefunden werden, antworte mit: []
Keine Erklärung, nur das JSON-Array."""


def _parse_location_response(content: str) -> list[dict[str, Any]]:
    """Parse LLM response into location dicts, handling common JSON issues."""
    content = content.strip()

    # Try direct parse first
    try:
        result = json.loads(content)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Extract first JSON array from response (LLM sometimes adds explanation)
    match = re.search(r'\[.*\]', content, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse location extraction response")
    return []


def _dict_to_location(d: dict[str, Any], text: str) -> ExtractedLocation | None:
    """Convert a raw dict from LLM response to an ExtractedLocation."""
    from lai.extraction.models import Coordinates

    name = d.get("location_name", "").strip()
    if not name:
        return None

    # Parse location type
    raw_type = d.get("location_type", "other").strip().lower()
    try:
        loc_type = LocationType(raw_type)
    except ValueError:
        loc_type = LocationType.OTHER

    # Parse coordinates
    coords = None
    lat = d.get("latitude")
    lon = d.get("longitude")
    if lat is not None and lon is not None:
        try:
            coords = Coordinates(latitude=float(lat), longitude=float(lon))
        except (ValueError, TypeError):
            pass

    # Build excerpt — use LLM's excerpt or find location name in original text
    excerpt = d.get("raw_excerpt", "")
    if not excerpt and name:
        idx = text.find(name)
        if idx >= 0:
            start = max(0, idx - 50)
            end = min(len(text), idx + len(name) + 50)
            excerpt = text[start:end]

    return ExtractedLocation(
        location_name=name,
        location_type=loc_type,
        geocode_address=d.get("geocode_address"),
        address=d.get("address"),
        coordinates=coords,
        flurstuck=d.get("flurstuck"),
        flur=d.get("flur"),
        gemarkung=d.get("gemarkung"),
        gemeinde=d.get("gemeinde"),
        landkreis=d.get("landkreis"),
        bundesland=d.get("bundesland"),
        raw_excerpt=excerpt[:500] if excerpt else "",
        confidence=float(d.get("confidence", 0.5)),
    )


async def extract_locations(
    text: str,
    segment_id: int,
    llm_url: str | None = None,
    llm_model: str | None = None,
) -> ExtractionResult:
    """Extract locations from a text segment using the LLM.

    Args:
        text: Document text to extract locations from.
        segment_id: Database ID of the segment.
        llm_url: Override for vLLM endpoint URL.
        llm_model: Override for model name.

    Returns:
        ExtractionResult with extracted locations.
    """
    settings = get_settings().pipeline
    url = llm_url or settings.synth_llm_url
    model = llm_model or settings.synth_llm_model

    # Truncate very long texts to avoid exceeding context window
    max_chars = 12000  # ~4000 tokens, leaves room for system prompt + output
    truncated = text[:max_chars] if len(text) > max_chars else text

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": truncated},
        ],
        "temperature": 0.1,  # Low temp for extraction accuracy
        "max_tokens": 2048,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        raw_locations = _parse_location_response(content)

        locations = []
        for loc_dict in raw_locations:
            loc = _dict_to_location(loc_dict, text)
            if loc:
                locations.append(loc)

        logger.info(
            "Extracted %d locations from segment %d",
            len(locations), segment_id,
        )

        return ExtractionResult(
            segment_id=segment_id,
            locations=locations,
            model_name=model,
        )

    except httpx.HTTPStatusError as e:
        logger.error("LLM HTTP error for segment %d: %s", segment_id, e)
        return ExtractionResult(
            segment_id=segment_id,
            model_name=model,
            error=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        )
    except Exception as e:
        logger.error("Location extraction failed for segment %d: %s", segment_id, e)
        return ExtractionResult(
            segment_id=segment_id,
            model_name=model,
            error=str(e),
        )


async def extract_locations_batch(
    segments: list[dict],
    llm_url: str | None = None,
    llm_model: str | None = None,
    max_concurrent: int = 8,
) -> list[ExtractionResult]:
    """Extract locations from multiple segments concurrently.

    Args:
        segments: List of dicts with 'id' and 'text' keys.
        llm_url: Override for vLLM endpoint URL.
        llm_model: Override for model name.
        max_concurrent: Max concurrent LLM requests.

    Returns:
        List of ExtractionResults.
    """
    import asyncio

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _extract_one(seg: dict) -> ExtractionResult:
        async with semaphore:
            return await extract_locations(
                text=seg["text"],
                segment_id=seg["id"],
                llm_url=llm_url,
                llm_model=llm_model,
            )

    tasks = [_extract_one(seg) for seg in segments]
    return await asyncio.gather(*tasks)

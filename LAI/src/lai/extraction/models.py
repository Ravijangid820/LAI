"""Pydantic models for extracted location data."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class LocationType(str, Enum):
    """Types of locations found in wind energy legal documents."""

    WIND_PARK = "wind_park"
    WIND_TURBINE = "wind_turbine"
    SUBSTATION = "substation"
    GRID_CONNECTION = "grid_connection"
    PARCEL = "parcel"
    ADDRESS = "address"
    OTHER = "other"


class Coordinates(BaseModel):
    """Geographic coordinates."""

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class ExtractedLocation(BaseModel):
    """A single location extracted from a document."""

    location_name: str = Field(..., description="Name of the location (e.g. 'Windpark Nordergründe')")
    location_type: LocationType = Field(default=LocationType.OTHER)
    geocode_address: str | None = Field(default=None, description="Complete geocodable address string for mapping APIs")
    address: str | None = Field(default=None, description="Full street address if mentioned")
    coordinates: Coordinates | None = Field(default=None, description="Lat/lon if mentioned in text")
    flurstuck: str | None = Field(default=None, description="German parcel ID (Flurstück-Nr.)")
    flur: str | None = Field(default=None, description="Flur number")
    gemarkung: str | None = Field(default=None, description="Cadastral district (Gemarkung)")
    gemeinde: str | None = Field(default=None, description="Municipality (Gemeinde)")
    landkreis: str | None = Field(default=None, description="County (Landkreis)")
    bundesland: str | None = Field(default=None, description="Federal state (Bundesland)")
    raw_excerpt: str = Field(..., description="Source text excerpt containing the location reference")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    """Result of location extraction for a single document/segment."""

    segment_id: int
    locations: list[ExtractedLocation] = Field(default_factory=list)
    model_name: str = ""
    extracted_at: datetime = Field(default_factory=datetime.now)
    error: str | None = None

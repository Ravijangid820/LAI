"""Guards that keep the cadastral pipeline from grinding for hours.

A single mis-geocoded turbine (the applicant's HQ address resolving to a
different town) once inflated the convex-hull project area to 1321 km², which
turned the 300 m ALKIS sampling grid into 35,627 points — each hammering a
down (HTTP 530) WFS server with retries. These guards bound all three:

* :func:`filter_outlier_points` drops far-cluster geocode outliers,
* the grid is capped at ``_MAX_ALKIS_GRID_POINTS`` regardless of area,
* the query loop fails fast after ``_MAX_ALKIS_CONSECUTIVE_FAILURES``.

The first is a pure function exercised directly here; the latter two are
asserted as constants so a regression that removes the cap is caught.
"""

from __future__ import annotations

import cadastral_pipeline as cp


class TestHaversine:
    def test_zero_distance(self) -> None:
        assert cp._haversine_km(53.6, 9.1, 53.6, 9.1) == 0.0

    def test_known_distance_is_reasonable(self) -> None:
        # Lamstedt (~53.64, 9.10) to the mis-geocode near Reußenköge
        # (~54.62, 8.90) is ~100+ km — well past the spread limit.
        d = cp._haversine_km(53.638, 9.098, 54.62, 8.90)
        assert 90 < d < 130


class TestFilterOutlierPoints:
    def _cluster(self) -> list[tuple[float, float]]:
        return [
            (53.638, 9.098),
            (53.637, 9.099),
            (53.636, 9.100),
            (53.639, 9.097),
            (53.640, 9.096),
            (53.635, 9.101),
            (53.641, 9.095),
        ]

    def test_drops_far_outlier(self) -> None:
        outlier = (54.62, 8.90)  # ~110 km north
        kept = cp.filter_outlier_points(self._cluster() + [outlier])
        assert outlier not in kept
        assert len(kept) == 7

    def test_keeps_a_tight_cluster_intact(self) -> None:
        cluster = self._cluster()
        assert len(cp.filter_outlier_points(cluster)) == len(cluster)

    def test_two_or_fewer_points_unchanged(self) -> None:
        # No cluster to judge against — never drop the only data we have.
        pts = [(53.6, 9.1), (54.6, 8.9)]
        assert cp.filter_outlier_points(pts) == pts

    def test_never_returns_empty(self) -> None:
        # Even pathological input keeps at least the original points.
        pts = [(0.0, 0.0), (53.6, 9.1), (54.6, 8.9)]
        assert cp.filter_outlier_points(pts)


class TestGuardConstants:
    def test_grid_cap_is_bounded(self) -> None:
        assert 0 < cp._MAX_ALKIS_GRID_POINTS <= 1000

    def test_failfast_threshold_is_small(self) -> None:
        assert 1 <= cp._MAX_ALKIS_CONSECUTIVE_FAILURES <= 20

    def test_spread_limit_is_park_scale(self) -> None:
        # A real wind park spans a few km; the limit must be generous enough
        # to keep a real park whole but tight enough to catch a wrong-town pin.
        assert 5 <= cp._MAX_WEA_SPREAD_KM <= 50

    def test_total_failure_budget_is_bounded(self) -> None:
        assert cp._MAX_ALKIS_TOTAL_FAILURES >= cp._MAX_ALKIS_CONSECUTIVE_FAILURES
        assert cp._MAX_ALKIS_TOTAL_FAILURES <= 100


class TestAlkisCircuitBreaker:
    """When the WFS is unreachable (the wrapper now propagates the failure
    instead of swallowing it as []), the loop must abort after the breaker
    threshold — NOT grind every grid point. This is the bug that left reports
    'running' for hours."""

    @staticmethod
    def _park_area() -> cp.ProjectArea:
        # ~2 km box → a 300 m grid yields far more points than the breaker
        # threshold, so a non-tripping loop would call alkis_query many times.
        lat, lng = 53.636, 9.098
        d = 0.02
        poly = [(lat - d, lng - d), (lat - d, lng + d), (lat + d, lng + d), (lat + d, lng - d)]
        return cp.ProjectArea(
            name="t",
            polygon=poly,
            centroid_lat=lat,
            centroid_lng=lng,
            area_km2=16.0,
            source="test",
        )

    def test_breaker_trips_and_does_not_grind_all_points(self) -> None:
        calls = {"n": 0}

        def failing(lat: float, lng: float, bundesland: str, radius: int):
            calls["n"] += 1
            raise RuntimeError("HTTP 530")  # WFS unreachable (propagated)

        pipe = cp.CadastralPipeline(alkis_query_fn=failing)
        parcels = pipe._step2_collect_parcels(self._park_area(), "niedersachsen", [])

        # Aborted after the breaker threshold instead of hitting every point.
        assert calls["n"] <= cp._MAX_ALKIS_CONSECUTIVE_FAILURES
        assert parcels == []

    def test_no_query_when_no_bundesland(self) -> None:
        calls = {"n": 0}

        def failing(*_a, **_k):
            calls["n"] += 1
            raise RuntimeError("should not be called")

        pipe = cp.CadastralPipeline(alkis_query_fn=failing)
        parcels = pipe._step2_collect_parcels(self._park_area(), "", [])
        assert calls["n"] == 0 and parcels == []

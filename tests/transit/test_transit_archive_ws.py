import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.transit.archive_ws import MetroWSArchiveService, MetroWSArchiveConfig


def test_metro_ws_archive_service_initialization(tmp_path):
    """Test that the Metro WS archive service initializes correctly."""
    config = MetroWSArchiveConfig(
        agency_key="lametro-rail",
        system_name="LA Metro Rail",
        root_dir=tmp_path,
        api_agency_id="1",
        ws_base_url="wss://api.metro.net/ws",
        collect_window_seconds=30.0,
        static_refresh_seconds=21600,
        static_url="https://example.test/gtfs.zip",
        static_filename="gtfs.zip",
        vehicle_positions_filename="vehicle_positions.json",
        trip_updates_filename="trip_updates.json",
        alerts_filename="alerts.json",
    )

    service = MetroWSArchiveService(config)

    assert service.cfg == config
    assert isinstance(service._stop_event, type(__import__("threading").Event()))
    assert service._session is not None
    assert service._vp_accum is not None
    assert service._tu_accum is not None
    assert service._threads == []


def test_metro_messages_to_gtfs_rt_envelope():
    """Test conversion of Metro WS messages to GTFS-RT envelope."""
    from scripts.transit.archive_ws import _metro_messages_to_gtfs_rt_envelope

    messages = [
        {
            "id": "vehicle1",
            "vehicle": {
                "trip": {"route_id": "Red Line", "trip_id": "trip1"},
                "vehicle": {"id": "v1", "label": "1001"},
                "position": {
                    "latitude": 42.36,
                    "longitude": -71.06,
                    "bearing": 90,
                    "speed": 10,
                },
                "timestamp": 1710000000,
            },
        }
    ]

    envelope = _metro_messages_to_gtfs_rt_envelope(
        messages, "vehicle_positions", 1710000000000
    )

    assert "header" in envelope
    assert "entity" in envelope
    assert len(envelope["entity"]) == 1
    assert envelope["entity"][0]["id"] == "vehicle1"
    assert envelope["header"]["gtfs_realtime_version"] == "2.0"
    assert envelope["header"]["timestamp"] == 1710000000  # Converted to seconds
    assert envelope["header"]["feed_label"] == "lametro-ws"


def test_fetch_canceled_service_success():
    """Test fetching canceled service when successful."""
    from scripts.transit.archive_ws import _fetch_canceled_service
    import requests

    # Mock successful response
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"line": "Red Line", "summary": "Service suspended"}
    ]
    mock_response.raise_for_status.return_value = None

    with patch("scripts.transit.archive_ws.requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session

        result = _fetch_canceled_service(mock_session)

        assert result is not None
        assert "header" in result
        assert "entity" in result
        assert len(result["entity"]) == 1
        assert result["entity"][0]["id"] == "canceled-0"
        assert result["entity"][0]["alert"]["effect"] == "NO_SERVICE"


def test_fetch_canceled_service_failure():
    """Test fetching canceled service when it fails."""
    from scripts.transit.archive_ws import _fetch_canceled_service

    # Mock failed response
    mock_session = MagicMock()
    mock_session.get.side_effect = Exception("Network error")

    result = _fetch_canceled_service(mock_session)

    assert result is None


if __name__ == "__main__":
    # Simple test runner for manual execution
    import tempfile
    import os

    print("Running archive_ws tests...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Test initialization
        test_metro_ws_archive_service_initialization(tmp_path)
        print("✓ Initialization test passed")

        # Test GTFS-RT conversion
        test_metro_messages_to_gtfs_rt_envelope()
        print("✓ GTFS-RT conversion test passed")

        # Test canceled service success
        test_fetch_canceled_service_success()
        print("✓ Canceled service success test passed")

        # Test canceled service failure
        test_fetch_canceled_service_failure()
        print("✓ Canceled service failure test passed")

    print("All tests passed!")

"""Tests for asset license validation."""

import pytest
from unittest.mock import MagicMock, patch


class TestAssetLicenseValidation:
    """Tests for asset license validation logic."""

    def test_pexels_assets_are_verified(self):
        from storyfactory.services.asset_collector import validate_asset_license

        asset = MagicMock()
        asset.provider = "pexels"
        asset.policy_status = "verified"

        assert validate_asset_license(asset) is True

    def test_pixabay_assets_are_verified(self):
        from storyfactory.services.asset_collector import validate_asset_license

        asset = MagicMock()
        asset.provider = "pixabay"
        asset.policy_status = "pending"

        assert validate_asset_license(asset) is True

    def test_local_assets_are_verified(self):
        from storyfactory.services.asset_collector import validate_asset_license

        asset = MagicMock()
        asset.provider = "local"
        asset.policy_status = "pending"

        assert validate_asset_license(asset) is True

    def test_rejected_assets_fail(self):
        from storyfactory.services.asset_collector import validate_asset_license

        asset = MagicMock()
        asset.provider = "pexels"
        asset.policy_status = "rejected"

        assert validate_asset_license(asset) is False

    @patch("storyfactory.services.asset_collector.get_settings")
    def test_unknown_provider_fails_with_failsafe(self, mock_settings):
        from storyfactory.services.asset_collector import validate_asset_license

        settings = MagicMock()
        settings.fail_safe_on_license_unknown = True
        mock_settings.return_value = settings

        asset = MagicMock()
        asset.id = "test"
        asset.provider = "unknown_source"
        asset.policy_status = "unknown"

        assert validate_asset_license(asset) is False


class TestBlockedQueryTerms:
    """Tests for blocked search query term validation."""

    def test_blocked_terms_detected(self):
        from storyfactory.services.asset_collector import BLOCKED_TERMS

        test_queries = ["minecraft gameplay", "fortnite highlight", "mario run"]
        for query in test_queries:
            matches = [t for t in BLOCKED_TERMS if t in query.lower()]
            assert len(matches) > 0, f"Should block query: {query}"

    def test_approved_queries_pass(self):
        from storyfactory.services.asset_collector import BLOCKED_TERMS, CATEGORY_SEARCH_QUERIES, FALLBACK_QUERIES

        all_queries = []
        for queries in CATEGORY_SEARCH_QUERIES.values():
            all_queries.extend(queries)
        all_queries.extend(FALLBACK_QUERIES)

        for query in all_queries:
            matches = [t for t in BLOCKED_TERMS if t in query.lower()]
            assert len(matches) == 0, f"Approved query should not be blocked: {query}"


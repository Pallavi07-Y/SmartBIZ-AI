import pytest

from app.business.nearby import (
    LocationLookupError,
    geocode_place,
    save_shop_coordinates,
    save_shop_location,
    search_nearby_shops,
    validate_coordinates,
)
from app.business.register_business import BusinessManager
from app.database import Business, Product, get_session


def test_nearby_search_sorts_real_shops_and_includes_active_products(isolated_database):
    manager = BusinessManager()
    nearer = manager.register_business(
        "Near Shop",
        "Electronics",
        address="Madanapalle",
        business_id="near-shop",
    )
    farther = manager.register_business(
        "Far Shop",
        "Books",
        address="Madanapalle",
        business_id="far-shop",
    )
    unlocated = manager.register_business(
        "Unlocated Shop",
        "Groceries",
        address="Madanapalle",
        business_id="unlocated-shop",
    )
    save_shop_coordinates(nearer["business_id"], "13.6288", "79.4192")
    save_shop_coordinates(farther["business_id"], "13.7", "79.5")

    session = get_session()
    try:
        session.add_all([
            Product(
                business_id=nearer["business_id"],
                product_name="Phone Alpha",
                price=25000,
                stock_quantity=4,
                is_active=True,
            ),
            Product(
                business_id=nearer["business_id"],
                product_name="Inactive Phone",
                price=10000,
                stock_quantity=1,
                is_active=False,
            ),
        ])
        session.commit()
    finally:
        session.close()

    result = search_nearby_shops(13.6289, 79.4193)

    assert [shop["business_id"] for shop in result["nearby_shops"]] == [
        "near-shop",
        "far-shop",
    ]
    assert result["nearby_shops"][0]["distance_km"] < result["nearby_shops"][1]["distance_km"]
    assert result["nearby_shops"][0]["products"] == [{
        "product_name": "Phone Alpha",
        "price": 25000,
        "stock_quantity": 4,
    }]
    assert result["missing_coordinates"] == 1
    assert result["missing_coordinate_shops"][0]["business_id"] == unlocated["business_id"]


def test_outside_radius_shops_are_separate_from_missing_coordinates(isolated_database):
    manager = BusinessManager()
    manager.register_business(
        "Nearby Shop",
        "Groceries",
        address="Madanapalle, India",
        business_id="nearby-shop",
        latitude=13.63,
        longitude=79.42,
    )
    manager.register_business(
        "Distant Shop",
        "Books",
        address="Madanapalle, India",
        business_id="distant-shop",
        latitude=17.38,
        longitude=78.48,
    )
    manager.register_business(
        "Unlocated Shop",
        "Clothing",
        address="Madanapalle, India",
        business_id="unlocated-shop",
    )

    result = search_nearby_shops(
        13.63,
        79.42,
        location_query="Madanapalle",
        radius_km=50,
    )

    assert [shop["business_id"] for shop in result["nearby_shops"]] == ["nearby-shop"]
    assert [shop["business_id"] for shop in result["outside_radius_shops"]] == ["distant-shop"]
    assert [shop["business_id"] for shop in result["missing_coordinate_shops"]] == ["unlocated-shop"]
    assert result["outside_radius_shops"][0]["distance_km"] > 50
    assert result["missing_coordinate_shops"][0]["distance_km"] is None


def test_manual_location_search_includes_registered_shops_without_products(isolated_database):
    shop = BusinessManager().register_business(
        "Local Groceries",
        "Grocery",
        address="Madanapalle, Andhra Pradesh",
        business_id="local-groceries",
    )
    result = search_nearby_shops(location_query="madanapalle")

    assert [item["business_id"] for item in result["shops"]] == [shop["business_id"]]
    assert result["missing_coordinate_shops"][0]["distance_km"] is None
    assert result["missing_coordinate_shops"][0]["products"] == []


def test_registration_saves_address_and_optional_coordinates(isolated_database):
    shop = BusinessManager().register_business(
        "Registered Shop",
        "Groceries",
        address="Madanapalle, Andhra Pradesh",
        latitude="13.6288",
        longitude="79.4192",
        business_id="registered-shop",
    )

    assert shop["address"] == "Madanapalle, Andhra Pradesh"
    assert shop["latitude"] == pytest.approx(13.6288)
    assert shop["longitude"] == pytest.approx(79.4192)


def test_registration_rejects_only_one_coordinate(isolated_database):
    with pytest.raises(ValueError, match="Enter both shop coordinates"):
        BusinessManager().register_business(
            "Incomplete Shop",
            "Groceries",
            address="Madanapalle",
            latitude="13.6288",
            business_id="incomplete-shop",
        )


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(91, 0), (0, 181), (float("nan"), 0), (0, float("inf")), ("north", "east")],
)
def test_coordinate_validation_rejects_invalid_values(latitude, longitude):
    with pytest.raises(ValueError):
        validate_coordinates(latitude, longitude)


def test_shop_coordinates_can_be_cleared_and_invalid_partial_values_are_rejected(isolated_database):
    shop = BusinessManager().register_business(
        "Coordinate Shop",
        "General",
        business_id="coordinate-shop",
    )
    save_shop_coordinates(shop["business_id"], 12.5, 78.5)

    with pytest.raises(ValueError, match="Enter both coordinates"):
        save_shop_coordinates(shop["business_id"], 12.5, "")

    save_shop_coordinates(shop["business_id"], "", "")
    session = get_session()
    try:
        stored_shop = session.get(Business, shop["business_id"])
        assert stored_shop.latitude is None
        assert stored_shop.longitude is None
    finally:
        session.close()


def test_shopkeeper_can_edit_address_and_coordinates(isolated_database):
    shop = BusinessManager().register_business(
        "Editable Shop",
        "General",
        address="Old address",
        business_id="editable-shop",
    )

    save_shop_location(shop["business_id"], "New address, Madanapalle", 13.6288, 79.4192)

    session = get_session()
    try:
        stored_shop = session.get(Business, shop["business_id"])
        assert stored_shop.address == "New address, Madanapalle"
        assert stored_shop.latitude == pytest.approx(13.6288)
        assert stored_shop.longitude == pytest.approx(79.4192)
    finally:
        session.close()


def test_geocode_place_returns_verified_coordinates(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{
                "lat": "13.6288",
                "lon": "79.4192",
                "address": {"town": "Madanapalle"},
            }]

    monkeypatch.setattr("app.business.nearby.requests.get", lambda *args, **kwargs: Response())

    assert geocode_place("Madanapalle") == {
        "latitude": 13.6288,
        "longitude": 79.4192,
        "label": "Madanapalle",
    }


def test_geocode_place_reports_no_match(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    monkeypatch.setattr("app.business.nearby.requests.get", lambda *args, **kwargs: Response())

    with pytest.raises(LocationLookupError, match="found no location"):
        geocode_place("Unknown place")


def test_nominatim_requests_are_spaced_to_respect_public_service_limit(monkeypatch):
    import app.business.nearby as nearby

    clock = iter((10.0, 10.5, 11.0, 11.5))
    delays = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    monkeypatch.setattr(nearby, "_last_nominatim_request", 0.0)
    monkeypatch.setattr(nearby.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(nearby.time, "sleep", delays.append)
    monkeypatch.setattr(nearby.requests, "get", lambda *args, **kwargs: Response())

    nearby._nominatim_get("search", params={"q": "first"})
    nearby._nominatim_get("search", params={"q": "second"})

    assert delays == pytest.approx([nearby.NOMINATIM_MIN_INTERVAL_SECONDS - 0.5])

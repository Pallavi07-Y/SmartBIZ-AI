import threading
import time

import requests

from app.database import Business, Product, get_session
from app.database.business_db import BusinessDB
from app.utils.location import distance, validate_coordinates


NOMINATIM_URL = "https://nominatim.openstreetmap.org"
NOMINATIM_HEADERS = {"User-Agent": "SmartBiz-AI-local-shop-discovery/1.0"}
SEARCH_RADIUS_KM = 50
NOMINATIM_MIN_INTERVAL_SECONDS = 1.1
_nominatim_lock = threading.Lock()
_last_nominatim_request = 0.0


class LocationLookupError(RuntimeError):
    pass


def _nominatim_get(endpoint, *, params):
    global _last_nominatim_request
    with _nominatim_lock:
        elapsed = time.monotonic() - _last_nominatim_request
        if elapsed < NOMINATIM_MIN_INTERVAL_SECONDS:
            time.sleep(NOMINATIM_MIN_INTERVAL_SECONDS - elapsed)
        _last_nominatim_request = time.monotonic()
        response = requests.get(
            f"{NOMINATIM_URL}/{endpoint}",
            params=params,
            headers=NOMINATIM_HEADERS,
            timeout=8,
        )
    response.raise_for_status()
    return response.json()


def _location_label(result):
    address = result.get("address") or {}
    for key in ("city", "town", "village", "municipality", "suburb", "county", "state"):
        if address.get(key):
            return address[key]
    display_name = result.get("display_name")
    if display_name:
        return display_name.split(",", 1)[0].strip()
    return None


def geocode_place(place):
    query = (place or "").strip()
    if not query:
        raise ValueError("Enter a town, city, or postal code.")

    try:
        results = _nominatim_get(
            "search",
            params={
                "q": f"{query}, India",
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 1,
                "countrycodes": "in",
            },
        )
    except (requests.RequestException, ValueError) as exc:
        raise LocationLookupError("OpenStreetMap could not look up that location.") from exc

    if not isinstance(results, list):
        raise LocationLookupError("OpenStreetMap returned an invalid location response.")
    if not results:
        raise LocationLookupError(f"OpenStreetMap found no location for {query!r}.")

    result = results[0]
    if not isinstance(result, dict):
        raise LocationLookupError("OpenStreetMap returned an invalid location result.")
    try:
        latitude, longitude = validate_coordinates(result["lat"], result["lon"])
    except (KeyError, ValueError) as exc:
        raise LocationLookupError("OpenStreetMap returned invalid coordinates.") from exc

    return {
        "latitude": latitude,
        "longitude": longitude,
        "label": _location_label(result) or query,
    }


def reverse_geocode(latitude, longitude):
    latitude, longitude = validate_coordinates(latitude, longitude)
    try:
        result = _nominatim_get(
            "reverse",
            params={
                "lat": latitude,
                "lon": longitude,
                "format": "jsonv2",
                "addressdetails": 1,
            },
        )
    except (requests.RequestException, ValueError) as exc:
        raise LocationLookupError("OpenStreetMap could not identify the town for that location.") from exc

    if not isinstance(result, dict):
        raise LocationLookupError("OpenStreetMap returned an invalid location result.")
    label = _location_label(result)
    if not label:
        raise LocationLookupError("OpenStreetMap did not return a town name for that location.")
    return label


def search_nearby_shops(latitude=None, longitude=None, *, location_query=None, radius_km=SEARCH_RADIUS_KM):
    if (latitude is None) != (longitude is None):
        raise ValueError("Both latitude and longitude are required to calculate distances.")
    if latitude is not None:
        latitude, longitude = validate_coordinates(latitude, longitude)
    if radius_km <= 0:
        raise ValueError("Search radius must be greater than zero.")

    query = (location_query or "").strip().casefold()
    session = get_session()
    try:
        businesses = session.query(Business).order_by(Business.name.asc()).all()
        nearby = []
        outside_radius = []
        missing_coordinates = []
        for business in businesses:
            has_coordinates = business.latitude is not None and business.longitude is not None
            address_text = " ".join(
                str(value or "")
                for value in (business.name, business.category, business.address)
            ).casefold()
            address_matches = bool(query and query in address_text)
            shop = {
                "business_id": business.business_id,
                "name": business.name,
                "category": business.category,
                "address": business.address,
                "latitude": business.latitude,
                "longitude": business.longitude,
                "location_precision": business.location_precision,
                "distance_km": None,
                "location_status": "missing_coordinates",
            }
            if not has_coordinates:
                if query and address_matches:
                    missing_coordinates.append(shop)
                elif not query and latitude is not None:
                    missing_coordinates.append(shop)
                continue

            if latitude is not None:
                shop["distance_km"] = distance(
                    latitude,
                    longitude,
                    business.latitude,
                    business.longitude,
                )
                if shop["distance_km"] <= radius_km:
                    shop["location_status"] = "nearby"
                    nearby.append(shop)
                elif address_matches:
                    shop["location_status"] = "outside_radius"
                    outside_radius.append(shop)
                elif not query:
                    shop["location_status"] = "outside_radius"
                    outside_radius.append(shop)
            elif query and address_matches:
                shop["location_status"] = "town_match"
                nearby.append(shop)

        sort_by_distance = lambda shop: (
            shop["distance_km"] is None,
            shop["distance_km"] if shop["distance_km"] is not None else float("inf"),
            shop["name"].casefold(),
        )
        nearby.sort(key=sort_by_distance)
        outside_radius.sort(key=sort_by_distance)
        missing_coordinates.sort(key=lambda shop: shop["name"].casefold())
        matches = nearby + outside_radius + missing_coordinates
        business_ids = [shop["business_id"] for shop in matches]
        products_by_business = {business_id: [] for business_id in business_ids}
        if business_ids:
            products = (
                session.query(Product)
                .filter(
                    Product.business_id.in_(business_ids),
                    Product.is_active.is_(True),
                )
                .order_by(Product.product_name.asc())
                .all()
            )
            for product in products:
                items = products_by_business[product.business_id]
                if len(items) < 4:
                    items.append({
                        "product_name": product.product_name,
                        "price": product.price,
                        "stock_quantity": max(0, product.stock_quantity or 0),
                    })

        for shop in matches:
            shop["products"] = products_by_business[shop["business_id"]]

        missing_coordinate_count = sum(
            business.latitude is None or business.longitude is None
            for business in businesses
        )
        return {
            "shops": matches,
            "nearby_shops": nearby,
            "outside_radius_shops": outside_radius,
            "missing_coordinate_shops": missing_coordinates,
            "missing_coordinates": missing_coordinate_count,
            "radius_km": radius_km,
        }
    finally:
        session.close()


def save_shop_coordinates(business_id, latitude, longitude):
    latitude, longitude = _optional_coordinates(latitude, longitude)

    session = get_session()
    try:
        business = session.get(Business, business_id)
        if business is None:
            raise ValueError("Shop not found.")
        business.latitude = latitude
        business.longitude = longitude
        business.location_precision = "shop" if latitude is not None else None
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _optional_coordinates(latitude, longitude):
    latitude_missing = latitude is None or str(latitude).strip() == ""
    longitude_missing = longitude is None or str(longitude).strip() == ""
    if latitude_missing and longitude_missing:
        return None, None
    if latitude_missing or longitude_missing:
        raise ValueError("Enter both coordinates, or leave both blank to remove the shop location.")
    return validate_coordinates(latitude, longitude)


def save_shop_location(business_id, address, latitude, longitude):
    latitude, longitude = _optional_coordinates(latitude, longitude)
    address = (address or "").strip()
    if not address:
        raise ValueError("Enter the shop address.")

    session = get_session()
    try:
        business = session.get(Business, business_id)
        if business is None:
            raise ValueError("Shop not found.")
        business.address = address
        business.latitude = latitude
        business.longitude = longitude
        business.location_precision = "shop" if latitude is not None else None
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class NearbyBusinesses:

    def __init__(self):

        self.db = BusinessDB()

    def search(self, latitude, longitude):
        latitude, longitude = validate_coordinates(latitude, longitude)
        shops = self.db.get_all()
        result = []
        for shop in shops:
            shop_latitude = shop.get("latitude")
            shop_longitude = shop.get("longitude")
            if shop_latitude is None or shop_longitude is None:
                continue
            d = distance(
                latitude,
                longitude,
                shop_latitude,
                shop_longitude,
            )
            shop["distance"] = round(d, 2)
            result.append(shop)
        result.sort(key=lambda shop: (shop["distance"], shop["name"].casefold()))
        return result[:5]
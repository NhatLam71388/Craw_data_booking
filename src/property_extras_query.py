"""GraphQL query/bien mau cho 2 loi goi bo sung cua trang chi tiet khach san
Booking.com (khong nam trong SSR cache chinh, phai goi rieng qua /dml/graphql):

  1. PropertySurroundingsBlockDesktop -> nearby_attractions/nearby_essentials.
     Gui full query text (khong dung Automatic Persisted Query).
  2. Facilities -> amenities/amenity_groups day du (facilities + facilityGroups).
     Dung Automatic Persisted Query (APQ): chi can gui sha256Hash, KHONG can
     gui query text day du - server da cache san query theo hash (dung chung
     cho moi client, khong rieng session).

Trich xuat tu request that (Playwright network capture) khi mo trang khach san
that. Neu Booking.com doi schema, chup lai request moi va thay the.
"""

import json

SURROUNDINGS_QUERY = r"""query PropertySurroundingsBlockDesktop($input: PropertySurroundingsInput!, $airportsInput: SurroundingsQueryConfig!, $landmarkInput: SurroundingsQueryConfig!, $nearbyLandmarkInput: SurroundingsQueryConfig!, $diningInput: SurroundingsQueryConfig!, $beachInput: SurroundingsQueryConfig!, $transportInput: SurroundingsQueryConfig!, $skiInput: SurroundingsQueryConfig!, $naturalBeautyInput: SurroundingsQueryConfig!, $includeSkiSurroundings: Boolean!, $bestCommuteInput: BestCommuteInput!, $endorsementGroupsInput: PropertyEndorsementGroupsInput!, $includeEndorsements: Boolean!) {
  propertyEndorsementGroups(input: $endorsementGroupsInput) @include(if: $includeEndorsements) {
    result {
      items {
        groupId
        groupType
        groupLabel
        __typename
      }
      __typename
    }
    __typename
  }
  propertySurroundings(input: $input) {
    airports(input: $airportsInput) {
      ...landmarkFields
      __typename
    }
    beaches(input: $beachInput) {
      ...geoFields
      __typename
    }
    dining {
      restaurants(input: $diningInput) {
        ...diningFields
        __typename
      }
      cafeBars(input: $diningInput) {
        ...diningFields
        __typename
      }
      __typename
    }
    landmarks {
      nearby(input: $nearbyLandmarkInput) {
        ...landmarkFields
        __typename
      }
      top(input: $landmarkInput) {
        ...landmarkFields
        __typename
      }
      __typename
    }
    publicTransport {
      bus(input: $transportInput) {
        ...geoFields
        __typename
      }
      metro(input: $transportInput) {
        ...geoFields
        __typename
      }
      train(input: $transportInput) {
        ...geoFields
        __typename
      }
      __typename
    }
    skiSurroundings @include(if: $includeSkiSurroundings) {
      lifts(input: $skiInput) {
        ...geoFields
        __typename
      }
      __typename
    }
    naturalBeauty {
      mountain(input: $naturalBeautyInput) {
        ...geoFields
        __typename
      }
      lake(input: $naturalBeautyInput) {
        ...geoFields
        __typename
      }
      forest(input: $naturalBeautyInput) {
        ...geoFields
        __typename
      }
      peak(input: $naturalBeautyInput) {
        ...geoFields
        __typename
      }
      waterfall(input: $naturalBeautyInput) {
        ...geoFields
        __typename
      }
      __typename
    }
    legacyAirportsWouldBeShown
    trackedExperiments
    __typename
  }
}

fragment landmarkFields on SurroundingGeoObject {
  id
  legacyId
  name
  geoClassName
  distance
  distanceLocalized
  commute {
    best(input: $bestCommuteInput) {
      commuteType
      commuteDetails {
        distanceMeters
        distanceLocalized
        timeMillis
        timeLocalized
        __typename
      }
      __typename
    }
    __typename
  }
  realDistance {
    commuteTime {
      byCarLocalized
      byFootLocalized
      __typename
    }
    commuteDistance {
      byCarLocalized
      byFootLocalized
      byFootMeters
      byCarMeters
      __typename
    }
    isIsolated
    __typename
  }
  latitude
  longitude
  __typename
}

fragment geoFields on SurroundingGeoObject {
  id
  name
  geoClassName
  distance
  distanceLocalized
  commute {
    best(input: $bestCommuteInput) {
      commuteType
      commuteDetails {
        distanceMeters
        distanceLocalized
        timeMillis
        timeLocalized
        __typename
      }
      __typename
    }
    __typename
  }
  realDistance {
    commuteTime {
      byCarLocalized
      byFootLocalized
      __typename
    }
    commuteDistance {
      byCarLocalized
      byFootLocalized
      byFootMeters
      byCarMeters
      __typename
    }
    isIsolated
    __typename
  }
  latitude
  longitude
  __typename
}

fragment diningFields on SurroundingDiningGeoObject {
  id
  name
  geoClassName
  cuisineType
  distance
  distanceLocalized
  commute {
    best(input: $bestCommuteInput) {
      commuteType
      commuteDetails {
        distanceMeters
        distanceLocalized
        timeMillis
        timeLocalized
        __typename
      }
      __typename
    }
    __typename
  }
  realDistance {
    commuteTime {
      byCarLocalized
      byFootLocalized
      __typename
    }
    commuteDistance {
      byCarLocalized
      byFootLocalized
      byFootMeters
      byCarMeters
      __typename
    }
    isIsolated
    __typename
  }
  latitude
  longitude
  __typename
}
"""

# Bien mau cho surroundings; input.hotelId/hotelUfi va endorsementGroupsInput.hotelId
# se duoc ghi de khi chay.
_SURROUNDINGS_VARIABLES_JSON = r"""{
    "input": {
        "hotelId": 11104587,
        "hotelUfi": -3730078
    },
    "airportsInput": {
        "limit": 3,
        "maxDistanceKm": 100
    },
    "beachInput": {
        "limit": 5,
        "maxDistanceKm": 10
    },
    "diningInput": {
        "limit": 3,
        "maxDistanceKm": 50
    },
    "landmarkInput": {
        "limit": 10,
        "maxDistanceKm": 20
    },
    "nearbyLandmarkInput": {
        "limit": 0,
        "maxDistanceKm": 0
    },
    "skiInput": {
        "limit": 3,
        "maxDistanceKm": 50
    },
    "transportInput": {
        "limit": 2,
        "maxDistanceKm": 20
    },
    "naturalBeautyInput": {
        "limit": 1,
        "maxDistanceKm": 15
    },
    "bestCommuteInput": {
        "evaluationCriteria": [
            "ShortestDistance"
        ]
    },
    "includeSkiSurroundings": false,
    "endorsementGroupsInput": {
        "hotelId": 11104587,
        "limit": 3
    },
    "includeEndorsements": false
}"""

def default_surroundings_variables() -> dict:
    """Tra ve 1 ban sao moi cua bien mau surroundings."""
    return json.loads(_SURROUNDINGS_VARIABLES_JSON)


FACILITIES_SHA256_HASH = "41d0fb51e4060b083ced8635b4430cc1be434341fa208d204c4585634e3bfd3a"

# Bien mau cho facilities; input.pageNameDetails se duoc ghi de khi chay.
_FACILITIES_VARIABLES_JSON = r"""{
    "isPropertyFacilitiesBlockOn": true,
    "shouldGetRelevantForYourTrip": true,
    "shouldGetRestaurantAttributesDesktop": false,
    "relevantForYourTripInput": [
        {
            "criterion": "relevantForYourTrip",
            "criterionParams": {
                "limit": 10
            }
        }
    ],
    "facilitiesExcludeGroups": [
        37,
        38,
        39,
        40,
        41
    ],
    "input": {
        "pageNameDetails": {
            "countryCode": "vn",
            "pagename": "hilton-saigon"
        },
        "searchConfig": {
            "nbRooms": 1,
            "nbAdults": 2,
            "nbChildren": 0,
            "childrenAges": []
        },
        "selectedFilters": ""
    }
}"""

def default_facilities_variables() -> dict:
    """Tra ve 1 ban sao moi cua bien mau facilities."""
    return json.loads(_FACILITIES_VARIABLES_JSON)

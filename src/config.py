"""Constantes y rutas del proyecto. Unica fuente de verdad.

Nunca se calculan rutas absolutas a mano: todo cuelga de REPO_ROOT.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_PATH = REPO_ROOT / "data" / "hotel_reservations.csv"
MODELS_DIR = REPO_ROOT / "models"
REPORTS_DIR = REPO_ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"

SEED = 42
TEST_SIZE = 0.2

TARGET = "booking_status"
POSITIVE_LABEL = "Canceled"  # la clase que nos interesa predecir
ID_COLUMN = "Booking_ID"

# --- Descubierto en la fase 2 (EDA). Ver reports/eda.md ---

# Se descartan como features: identificador y una columna que no generaliza a 2019.
DROP_FEATURES = [ID_COLUMN, "arrival_year"]

# Rango valido observado por columna: (minimo, maximo, unidad).
# Alimenta el contrato de datos y los Field(ge=, le=) de la API.
NUMERIC_RANGES = {
    "no_of_adults": (0, 4, "personas"),
    "no_of_children": (0, 10, "personas"),
    "no_of_weekend_nights": (0, 7, "noches"),
    "no_of_week_nights": (0, 17, "noches"),
    "required_car_parking_space": (0, 1, "bandera"),
    "lead_time": (0, 443, "dias"),
    "arrival_month": (1, 12, "mes"),
    "arrival_date": (1, 31, "dia del mes"),
    "repeated_guest": (0, 1, "bandera"),
    "no_of_previous_cancellations": (0, 13, "reservas"),
    "no_of_previous_bookings_not_canceled": (0, 58, "reservas"),
    "avg_price_per_room": (0.0, 540.0, "euros"),
    "no_of_special_requests": (0, 5, "peticiones"),
}

CATEGORICAL_LEVELS = {
    "type_of_meal_plan": ["Meal Plan 1", "Meal Plan 2", "Meal Plan 3", "Not Selected"],
    "room_type_reserved": [f"Room_Type {i}" for i in range(1, 8)],
    "market_segment_type": ["Online", "Offline", "Corporate", "Complementary", "Aviation"],
}

# Fecha de corte del split temporal: entrenar con el pasado, validar con el futuro.
# La deriva de 14,75% (2017) a 36,71% (2018) hace que el split aleatorio mienta.
# El umbral se elige en esta ventana de validacion, NUNCA en el test: es una
# decision que depende de la distribucion.
VAL_DATE = "2018-07-01"
SPLIT_DATE = "2018-09-01"

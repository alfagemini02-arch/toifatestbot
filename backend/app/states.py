from aiogram.fsm.state import State, StatesGroup


class RegistrationState(StatesGroup):
    waiting_for_name = State()
    waiting_for_contact = State()


class CheckState(StatesGroup):
    waiting_for_origin_country = State()
    waiting_for_destination_country = State()
    waiting_for_vehicle_country = State()


class FeeCalcState(StatesGroup):
    waiting_for_vehicle_type = State()
    waiting_for_vehicle_country = State()
    waiting_for_direction = State()
    waiting_for_origin_country = State()
    waiting_for_destination_country = State()
    waiting_for_weight_category = State()
    waiting_for_stay_duration = State()
    waiting_for_declaration = State()
    waiting_for_customs_value = State()
    waiting_for_transit_declaration = State()
    waiting_for_tinted = State()
    waiting_for_osago = State()
    waiting_for_osago_period = State()
    waiting_for_heavy = State()
    waiting_for_humanitarian = State()
    waiting_for_animal = State()
    waiting_for_temp_overstay = State()
    waiting_for_temp_overstay_days = State()
    waiting_for_delivery_overdue = State()
    waiting_for_delivery_overdue_days = State()

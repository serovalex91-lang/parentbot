from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    waiting_role = State()
    waiting_birthdate = State()


class UploadPDF(StatesGroup):
    waiting_age_range = State()


class EditProfile(StatesGroup):
    waiting_value = State()


class SetDate(StatesGroup):
    waiting_birthdate = State()


class OnboardingPrompt(StatesGroup):
    waiting_fill_answer = State()    # Ожидание ответа на onboarding-вопрос
    waiting_review_edit = State()    # Ожидание нового значения при ревизии


class AdminPanel(StatesGroup):
    waiting_add_id = State()
    waiting_remove_id = State()
    waiting_broadcast_text = State()
    waiting_update_text = State()  # текст рассылки об обновлении

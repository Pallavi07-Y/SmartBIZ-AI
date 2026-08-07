import os
import re
import uuid


def file_exists(path):

    return os.path.exists(path)



def create_folder(path):

    if not os.path.exists(path):

        os.makedirs(path)


def generate_order_id():

    return f"ORD-{uuid.uuid4().hex[:8].upper()}"


def validate_phone_number(phone):

    if not phone:

        return False


    normalized = re.sub(r"[^0-9]", "", phone)

    return len(normalized) >= 10 and len(normalized) <= 13
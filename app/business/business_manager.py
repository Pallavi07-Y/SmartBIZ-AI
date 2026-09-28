from app.business.register_business import BusinessManager
from app.utils.auth import hash_password
from app.business.upload_documents import DocumentUploader
from app.business.search_business import BusinessSearch


class BusinessController:

    def __init__(self):

        self.register = BusinessManager()

        self.uploader = DocumentUploader()

        self.search = BusinessSearch()


    def create_business(
        self,
        name,
        category,
        address=None,
        owner_phone=None,
        username=None,
        password=None,
    ):

        return self.register.register_business(
            name,
            category,
            address=address,
            owner_phone=owner_phone,
            username=username,
            password_hash=hash_password(password) if password else None,
        )


    def add_document(
        self,
        business_id,
        file_path
    ):

        return self.uploader.upload(
            business_id,
            file_path
        )


    def find_business(
        self,
        business_id
    ):

        return self.search.search_by_id(
            business_id
        )


    def find_business_name(
        self,
        name
    ):

        return self.search.search_by_name(
            name
        )
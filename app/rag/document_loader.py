import base64
import io
from pathlib import Path

from langchain_core.documents import Document
from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    CSVLoader,
    Docx2txtLoader
)
from openai import OpenAI
from PIL import Image, ImageOps

from app.config import settings

class DocumentLoader:

    @staticmethod
    def _load_image(path):
        with Image.open(path) as source:
            if source.format not in {"JPEG", "PNG"}:
                raise ValueError("Only JPG and PNG images are supported.")
            if source.width * source.height > 25_000_000:
                raise ValueError("Image dimensions are too large to process.")
            image = ImageOps.exif_transpose(source)
            image.thumbnail((2048, 2048))
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            elif image.mode == "L":
                image = image.convert("RGB")

            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85, optimize=True)

        image_data = base64.b64encode(buffer.getvalue()).decode("ascii")
        client = OpenAI(api_key=settings.API_KEY, base_url=settings.BASE_URL)
        response = client.chat.completions.create(
            model=settings.VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Transcribe all readable text in this shop document faithfully. "
                                "Preserve meaningful headings, lists, prices, and policy wording. "
                                "Do not infer or add information. If no readable text is present, "
                                "return an empty response."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}",
                            },
                        },
                    ],
                }
            ],
            temperature=0,
            max_tokens=2048,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("No readable text was found in the image.")
        return [Document(page_content=text, metadata={"source": str(path)})]

    def load_document(self, file_path):

        path = Path(file_path)

        if not path.exists():

            raise FileNotFoundError(
                "File not found"
            )


        extension = path.suffix.lower()


        if extension == ".pdf":

            loader = PyPDFLoader(
                str(path)
            )


        elif extension == ".txt":

            loader = TextLoader(
                str(path),
                encoding="utf-8"
            )


        elif extension == ".csv":

            loader = CSVLoader(
                str(path)
            )


        elif extension == ".docx":

            loader = Docx2txtLoader(
                str(path)
            )

        elif extension in {".jpg", ".jpeg", ".png"}:
            return self._load_image(path)


        else:

            raise ValueError(
                "Unsupported file format"
            )


        return loader.load()



    def load_folder(self, folder_path):

        documents = []

        folder = Path(folder_path)


        for file in folder.iterdir():

            if file.suffix.lower() in [
                ".pdf",
                ".txt",
                ".csv",
                ".docx",
                ".jpg",
                ".jpeg",
                ".png",
            ]:

                docs = self.load_document(
                    file
                )

                documents.extend(
                    docs
                )


        return documents
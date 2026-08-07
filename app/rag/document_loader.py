from pathlib import Path

from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    CSVLoader,
    Docx2txtLoader
)


class DocumentLoader:


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
                ".docx"
            ]:

                docs = self.load_document(
                    file
                )

                documents.extend(
                    docs
                )


        return documents
from app.rag.document_loader import DocumentLoader
from app.rag.chunking import DocumentChunker
from app.rag.embedding import EmbeddingModel


def test_rag():

    loader = DocumentLoader()

    documents = loader.load_folder(
        "data/documents/ABC_Mobiles/"
    )


    chunker = DocumentChunker()

    chunks = chunker.split_documents(
        documents
    )


    embedding = EmbeddingModel()

    vectors = embedding.embed_documents(
        chunks
    )


    print("Documents:", len(documents))
    print("Chunks:", len(chunks))
    print("Vectors:", len(vectors))


if __name__ == "__main__":
    test_rag()
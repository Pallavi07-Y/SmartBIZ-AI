from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import app.rag.document_loader as document_loader_module
from app.rag.document_loader import DocumentLoader


class FakeOpenAI:
    calls = []

    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.client_options = kwargs

    def create(self, **kwargs):
        self.calls.append((self.client_options, kwargs))
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Shop hours: 9 AM to 6 PM.")
                )
            ]
        )


@pytest.mark.parametrize("extension, image_format", [(".jpg", "JPEG"), (".png", "PNG")])
def test_image_document_loader_extracts_text_with_configured_vision_model(
    tmp_path,
    monkeypatch,
    extension,
    image_format,
):
    source = tmp_path / f"shop-hours{extension}"
    Image.new("RGB", (32, 32), color="white").save(source, format=image_format)
    FakeOpenAI.calls = []
    monkeypatch.setattr(document_loader_module, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("API_KEY", "test-api-key")

    documents = DocumentLoader().load_document(source)

    assert len(documents) == 1
    assert documents[0].page_content == "Shop hours: 9 AM to 6 PM."
    assert Path(documents[0].metadata["source"]) == source
    client_options, request = FakeOpenAI.calls[0]
    assert client_options["api_key"] == "test-api-key"
    assert request["model"] == document_loader_module.settings.VISION_MODEL
    image_url = request["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")


def test_image_document_loader_rejects_images_without_extracted_text(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "blank.png"
    Image.new("RGB", (16, 16), color="white").save(source)
    monkeypatch.setattr(
        document_loader_module,
        "OpenAI",
        lambda **_: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **__: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
                    )
                )
            )
        ),
    )
    monkeypatch.setenv("API_KEY", "test-api-key")

    with pytest.raises(ValueError, match="No readable text"):
        DocumentLoader().load_document(source)


def test_image_document_loader_rejects_non_jpeg_and_png_files(tmp_path):
    source = tmp_path / "animated.jpg"
    Image.new("RGB", (16, 16), color="white").save(source, format="GIF")

    with pytest.raises(ValueError, match="Only JPG and PNG"):
        DocumentLoader().load_document(source)

import pytest

from app.core.drafts import DraftMedia, PostDraft


def test_draft_round_trips_through_a_dictionary() -> None:
    draft = PostDraft(
        caption="Текст публикации",
        media=(
            DraftMedia("media/a.jpg", "photo", "a.jpg", 2048),
            DraftMedia("media/b.mp4", "video", "b.mp4", 4096),
        ),
    )

    restored = PostDraft.from_dict(draft.to_dict())

    assert restored == draft
    assert restored.has_content


def test_empty_draft_has_no_content() -> None:
    assert not PostDraft().has_content
    assert not PostDraft(caption="   ").has_content
    assert PostDraft(media=(DraftMedia("media/a.jpg", "photo"),)).has_content


def test_missing_payload_reads_back_as_an_empty_draft() -> None:
    assert PostDraft.from_dict(None) == PostDraft()
    assert PostDraft.from_dict({}) == PostDraft()


def test_unknown_media_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported media type"):
        DraftMedia.from_dict({"file_path": "media/a.pdf", "media_type": "document"})

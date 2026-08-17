import pytest

from app.bot.models import DraftMedia, PostDraft, toggle_index


def test_draft_accumulates_content_and_round_trips() -> None:
    draft = (
        PostDraft()
        .append_caption("Первая часть")
        .append_caption("Вторая часть")
        .append_media(DraftMedia("photo-id", "photo"))
    )

    restored = PostDraft.from_dict(draft.to_dict())

    assert restored == draft
    assert restored.caption == "Первая часть\n\nВторая часть"
    assert restored.has_content


def test_draft_rejects_more_than_ten_media_files() -> None:
    draft = PostDraft()
    for index in range(10):
        draft = draft.append_media(DraftMedia(str(index), "photo"))

    with pytest.raises(ValueError, match="at most 10"):
        draft.append_media(DraftMedia("extra", "video"))


def test_target_toggle_is_reversible() -> None:
    selected = toggle_index(set(), 1, total=3)
    assert selected == {1}
    assert toggle_index(selected, 1, total=3) == set()


@pytest.mark.parametrize("index", [-1, 3])
def test_target_toggle_rejects_unknown_index(index: int) -> None:
    with pytest.raises(IndexError):
        toggle_index(set(), index, total=3)

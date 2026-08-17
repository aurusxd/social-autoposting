from app.bot.models import DraftMedia, PostDraft
from app.bot.views import draft_text, review_text
from app.core.config import PublishTarget


def test_draft_view_escapes_user_text() -> None:
    draft = PostDraft("<script>", (DraftMedia("id", "photo"),))

    rendered = draft_text(draft)

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "фото: 1" in rendered


def test_review_view_contains_only_selected_targets() -> None:
    targets = (
        PublishTarget("telegram", "1", "channel", "Первый"),
        PublishTarget("whatsapp", "2", "group", "Второй"),
    )

    rendered = review_text(PostDraft("Текст"), targets, {1})

    assert "Второй" in rendered
    assert "Первый" not in rendered

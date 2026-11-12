"""The guard that keeps real values out of `.env.example` (run in CI against the real file)."""

from scripts.check_env_example import ALLOWED_VALUES, check_env_text, is_secret_name


def test_empty_secrets_comments_and_allowed_defaults_pass():
    content = "# a comment\n\nFINNHUB_API_KEY=\nPOSTGRES_PASSWORD=\nAPI_PORT=8100\nOLLAMA_MODEL=x\n"
    assert check_env_text(content) == []


def test_a_filled_secret_fails_and_its_value_is_not_reported():
    problems = check_env_text("API_PORT=8100\nFINNHUB_API_KEY=not-a-real-key-123\n")
    assert problems == ["line 2: FINNHUB_API_KEY is a secret and must be empty"]
    assert "not-a-real-key-123" not in " ".join(problems)


def test_every_secret_word_is_recognised():
    for name in ("X_API_KEY", "N8N_TRIGGER_SECRET", "BOT_TOKEN", "POSTGRES_PASSWORD", "SENTRY_DSN"):
        assert is_secret_name(name)
        assert check_env_text(f"{name}=filled\n") != []
    assert not is_secret_name("API_PORT")


def test_a_new_variable_with_a_value_fails_until_it_is_allowed():
    problems = check_env_text("NEW_SETTING=42\n")
    assert len(problems) == 1 and "NEW_SETTING" in problems[0] and "42" not in problems[0]
    assert check_env_text("NEW_SETTING=\n") == []


def test_a_malformed_line_fails():
    assert check_env_text("just some text\n") == ["line 1: not a NAME=value line"]


def test_no_secret_name_is_allowed_a_value():
    assert not [name for name in ALLOWED_VALUES if is_secret_name(name)]
